"""Serialized lifecycle and failure tests; all Bluetooth access is mocked."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.exceptions import HomeAssistantError
from lumalou import crypto

from custom_components.lumalou.const import (
    ALLOWED_OPCODES,
    CONF_PRODUCT_CODE,
    GLOBAL_STATE_FIELDS,
    SUPPORTED_PRODUCT_CODE,
)
from custom_components.lumalou.coordinator import LumalouCoordinator, SafeLumalouClient
from custom_components.lumalou.models import (
    LumalouRuntimeData,
    ProfileRecord,
    ProfileValidationError,
    RevisionConflictError,
)
from custom_components.lumalou.storage import ProfileStorageError, ProfileStore


@pytest.fixture
def rig():
    """Exercise public coordinator APIs against a deterministic fake transport."""
    journal = []
    device = BLEDevice("synthetic-device", "Test Lumalou", {})
    store = SimpleNamespace(async_load=AsyncMock(return_value=ProfileRecord()))

    async def save(record):
        journal.append(("save", deepcopy(record)))

    store.async_save = AsyncMock(side_effect=save)
    background_tasks = []

    def create_background_task(hass, coro, name, *, eager_start=True):
        task = asyncio.create_task(coro)
        background_tasks.append(task)
        return task

    entry = SimpleNamespace(
        data={
            "address": device.address,
            CONF_PRODUCT_CODE: SUPPORTED_PRODUCT_CODE,
        },
        title="Test Lumalou",
        entry_id="synthetic",
        async_create_background_task=Mock(side_effect=create_background_task),
    )
    coordinator = LumalouCoordinator(SimpleNamespace(), entry, store)
    state = dict.fromkeys(GLOBAL_STATE_FIELDS, 0)
    state.update(currentVolume=2, lightBrightness=3, lightColor=4)
    clients = []

    def create_client(ble_device, on_state):
        assert ble_device is device
        client = SimpleNamespace(connected=False, on_state=on_state, mode="fresh")

        async def connect():
            journal.append(("connect", ble_device))
            client.connected = True

        async def disconnect():
            client.connected = False
            journal.append(("disconnect", ble_device))

        async def send(payload):
            journal.append(("send", payload))

        async def request_state(**kwargs):
            assert kwargs["timeout"] > 0
            journal.append(("request", None))
            if client.mode == "fresh":
                on_state(deepcopy(state))
            if client.mode == "error":
                raise OSError("Synthetic BLE disconnect")
            return deepcopy(state)

        client.connect = AsyncMock(side_effect=connect)
        client.disconnect = AsyncMock(side_effect=disconnect)
        client.send = AsyncMock(side_effect=send)
        client.request_state = AsyncMock(side_effect=request_state)
        clients.append(client)
        return client

    with (
        patch(
            "custom_components.lumalou.coordinator.SafeLumalouClient",
            side_effect=create_client,
        ) as client_factory,
        patch(
            "custom_components.lumalou.coordinator.bluetooth.async_ble_device_from_address",
            return_value=device,
        ) as discovery,
        patch(
            "custom_components.lumalou.coordinator.bluetooth.async_address_present",
            return_value=False,
        ) as address_present,
        patch(
            "custom_components.lumalou.coordinator.bluetooth.async_register_callback",
            return_value=Mock(),
        ) as register_callback,
        patch(
            "custom_components.lumalou.coordinator.bluetooth.async_track_unavailable",
            return_value=Mock(),
        ) as track_unavailable,
    ):
        yield SimpleNamespace(
            coordinator=coordinator,
            store=store,
            device=device,
            clients=clients,
            discovery=discovery,
            client_factory=client_factory,
            journal=journal,
            state=state,
            background_tasks=background_tasks,
            address_present=address_present,
            register_callback=register_callback,
            track_unavailable=track_unavailable,
        )


async def test_offline_setup_preserves_pending_profile_without_ble(rig):
    rig.discovery.return_value = None
    saved = ProfileRecord(
        revision=3, desired_profile={"volume": 7}, pending=True, sync_status="pending"
    )
    rig.store.async_load.return_value = saved
    listener = Mock()
    remove = rig.coordinator.async_add_listener(listener)
    await rig.coordinator.async_setup()
    assert rig.coordinator.profile_record == saved
    assert rig.coordinator.observed_state is None
    assert not rig.coordinator.available
    assert rig.coordinator.sw_version is None
    rig.discovery.assert_not_called()
    rig.client_factory.assert_not_called()
    rig.store.async_save.assert_not_awaited()
    listener.assert_called_once()
    remove()
    rig.coordinator._notify()
    listener.assert_called_once()


@pytest.mark.parametrize(
    "operation",
    [
        lambda coordinator: coordinator.async_set_light(True),
        lambda coordinator: coordinator.async_set_light(False),
        lambda coordinator: coordinator.async_set_volume(3),
        lambda coordinator: coordinator.async_set_light_duration(1),
        lambda coordinator: coordinator.async_play(2),
        lambda coordinator: coordinator.async_stop_audio(),
        lambda coordinator: coordinator.async_sync_clock(),
    ],
)
async def test_legacy_entry_blocks_every_device_mutation_before_side_effects(
    rig, operation
):
    """An address-only entry may read but cannot mutate a candidate device."""
    coordinator = rig.coordinator
    coordinator.product_code = None

    with pytest.raises(HomeAssistantError, match="Confirm product code GLD09"):
        await operation(coordinator)

    rig.store.async_save.assert_not_awaited()
    rig.client_factory.assert_not_called()
    assert coordinator.profile_record == ProfileRecord()

    await coordinator.async_request_refresh()
    assert coordinator.available
    assert rig.clients[0].request_state.await_count == 1


async def test_tampered_product_code_cannot_bypass_low_level_write_gate(rig):
    """Private command dispatch remains guarded against future missed callers."""
    coordinator = rig.coordinator
    coordinator.product_code = "gld09"

    with pytest.raises(HomeAssistantError, match="Confirm product code GLD09"):
        await coordinator._send_commands([bytes([0x37, 3])])

    rig.client_factory.assert_not_called()


async def test_queued_device_mutation_checks_product_code_under_lock(rig):
    """Authorization is evaluated when a queued operation actually starts."""
    coordinator = rig.coordinator
    async with coordinator._lock:
        operation = asyncio.create_task(coordinator.async_set_volume(3))
        await asyncio.sleep(0)
        assert not operation.done()
        coordinator.product_code = None

    with pytest.raises(HomeAssistantError, match="Confirm product code GLD09"):
        await operation

    rig.store.async_save.assert_not_awaited()
    rig.client_factory.assert_not_called()


async def test_unreadable_profile_blocks_writes_not_discovery(rig):
    rig.store.async_load.side_effect = ProfileStorageError("Synthetic corruption")
    await rig.coordinator.async_setup()
    assert rig.coordinator.profile_record.last_error == "storage_load"
    with pytest.raises(HomeAssistantError, match="requires recovery"):
        await rig.coordinator.async_set_volume(3)
    rig.client_factory.assert_not_called()
    rig.store.async_save.assert_not_awaited()


async def test_corrupt_profile_cannot_export_empty_backup_or_overwrite_file(
    rig, tmp_path
):
    """A setup fallback is diagnostic state, never a valid backup document."""
    path = tmp_path / "lumalou.synthetic.profile"
    corrupt_document = '{"version":1,"data":'
    path.write_text(corrupt_document)

    async def executor(function, *args):
        return function(*args)

    backend = SimpleNamespace(
        path=str(path), key="lumalou.synthetic.profile", async_save=AsyncMock()
    )
    with patch("custom_components.lumalou.storage.Store", return_value=backend):
        store = ProfileStore(
            SimpleNamespace(async_add_executor_job=executor), "synthetic"
        )
    coordinator = LumalouCoordinator(rig.coordinator.hass, rig.coordinator.entry, store)
    await coordinator.async_setup()
    assert coordinator.profile_record.last_error == "storage_load"

    with pytest.raises(HomeAssistantError, match="requires recovery before exporting"):
        await coordinator.async_export_profile()

    assert path.read_text() == corrupt_document
    backend.async_save.assert_not_awaited()
    rig.client_factory.assert_not_called()


async def test_queued_export_rechecks_storage_health_under_lock(rig):
    coordinator = rig.coordinator
    async with coordinator._lock:
        export = asyncio.create_task(coordinator.async_export_profile())
        await asyncio.sleep(0)
        assert not export.done()
        coordinator._storage_healthy = False

    with pytest.raises(HomeAssistantError, match="requires recovery before exporting"):
        await export
    rig.store.async_save.assert_not_awaited()


async def test_fresh_callback_is_observation_not_desired_profile(rig):
    await rig.coordinator.async_request_refresh()
    coordinator = rig.coordinator
    assert coordinator.available
    assert coordinator.data == rig.state
    assert coordinator.profile_record.desired_profile == {}
    assert coordinator.profile_record.verified_revision is None
    copy = coordinator.observed_state
    copy["currentVolume"] = 9
    assert coordinator.data["currentVolume"] == 2
    rig.discovery.assert_called_once_with(
        coordinator.hass, rig.device.address, connectable=True
    )
    rig.clients[0].send.assert_not_awaited()
    await coordinator.async_request_refresh()
    assert len(rig.clients) == 1
    assert rig.clients[0].request_state.await_count == 2


async def test_unsolicited_valid_state_updates_entities_not_saved_intent(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    listener = Mock()
    coordinator.async_add_listener(listener)
    changed = {**rig.state, "currentVolume": 8}

    rig.clients[0].on_state(changed)

    assert coordinator.observed_state == changed
    assert coordinator.available
    assert coordinator.profile_record.desired_profile == {}
    listener.assert_called_once_with()
    rig.store.async_save.assert_not_awaited()


async def test_stale_cache_does_not_confirm_refresh_and_old_callback_is_ignored(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    old_client = rig.clients[0]
    old_client.mode = "stale"
    with pytest.raises(HomeAssistantError, match="refresh failed"):
        await coordinator.async_request_refresh()
    assert coordinator.data is None
    assert not coordinator.available
    old_client.disconnect.assert_awaited_once()
    received = coordinator._received
    old_client.on_state(rig.state)
    assert coordinator._received == received
    await coordinator.async_request_refresh()
    assert len(rig.clients) == 2
    assert coordinator.available
    # Reconnection reads only; no saved or transient action is replayed.
    assert not [item for item in rig.journal if item[0] == "send"]


@pytest.mark.parametrize("mode", ["generation", "empty", "different"])
async def test_freshness_requires_matching_session_callback_and_result(rig, mode):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    client = rig.clients[0]

    async def ambiguous_response(**kwargs):
        client.on_state(rig.state)
        if mode == "generation":
            coordinator._generation += 1
        elif mode == "empty":
            coordinator._callback_state = None
        else:
            return {**rig.state, "currentVolume": 1}
        return rig.state

    client.request_state.side_effect = ambiguous_response
    with pytest.raises(HomeAssistantError):
        await coordinator.async_request_refresh()
    assert not coordinator.available


@pytest.mark.parametrize("invalid", [None, {}, {"unknown": 1}])
def test_wrong_callback_schema_ignored(rig, invalid):
    coordinator = rig.coordinator
    coordinator._receive(coordinator._generation, invalid)
    assert coordinator._received == 0


@pytest.mark.parametrize("value", [True, "1", -1, 256, 19])
def test_invalid_decoded_callback_values_ignored(rig, value):
    rig.state["currentSong"] = value
    rig.coordinator._receive(rig.coordinator._generation, rig.state)
    assert rig.coordinator._received == 0


async def test_save_precedes_apply_and_cannot_claim_verified(rig):
    await rig.coordinator.async_set_volume(7)
    record = rig.coordinator.profile_record
    assert rig.journal[0][0] == "save"
    assert rig.journal[0][1].desired_profile == {"volume": 7}
    assert ("send", bytes([0x37, 7])) in rig.journal
    assert record.revision == 1
    assert record.pending is True
    assert record.sync_status == "partial"
    assert record.verified_revision is None
    assert rig.coordinator.data["currentVolume"] == 2  # no optimistic state
    detached = record.desired_profile
    detached["volume"] = 1
    assert rig.coordinator.profile_record.desired_profile == {"volume": 7}
    assert LumalouRuntimeData(rig.coordinator).profile_record.revision == 1


async def test_concurrent_edits_are_serialized_without_mixing_revisions(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    client = rig.clients[0]
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    original = client.send.side_effect

    async def delayed_send(payload):
        if payload == bytes([0x37, 3]):
            send_started.set()
            await release_send.wait()
        await original(payload)

    client.send.side_effect = delayed_send
    first = asyncio.create_task(coordinator.async_set_volume(3))
    await send_started.wait()
    second = asyncio.create_task(coordinator.async_set_volume(4))
    await asyncio.sleep(0)
    assert coordinator.profile_record.revision == 1
    assert coordinator.profile_record.desired_profile == {"volume": 3}
    release_send.set()
    await asyncio.gather(first, second)
    assert coordinator.profile_record.revision == 2
    assert coordinator.profile_record.previous == {
        "revision": 1,
        "profile": {"volume": 3},
    }
    assert coordinator.profile_record.desired_profile == {"volume": 4}
    assert [item[1] for item in rig.journal if item[0] == "send"] == [
        bytes([0x37, 3]),
        bytes([0x37, 4]),
    ]


async def test_offline_edit_is_saved_pending_and_write_failure_keeps_old_revision(rig):
    coordinator = rig.coordinator
    rig.discovery.return_value = None
    await coordinator.async_set_volume(5)
    assert coordinator.profile_record.pending
    assert coordinator.profile_record.desired_profile == {"volume": 5}
    assert coordinator.profile_record.last_error == "ble_apply"
    rig.store.async_save.side_effect = ProfileStorageError("Synthetic disk full")
    with pytest.raises(ProfileStorageError):
        await coordinator.async_set_volume(6)
    assert coordinator.profile_record.revision == 1
    assert coordinator.profile_record.desired_profile == {"volume": 5}
    rig.client_factory.assert_not_called()


async def test_connection_failure_cleans_up_and_leaves_pending(rig):
    original = rig.client_factory.side_effect

    def fail_connect(*args):
        client = original(*args)
        client.connect.side_effect = OSError("Synthetic connect error")
        return client

    rig.client_factory.side_effect = fail_connect
    await rig.coordinator.async_set_volume(4)
    assert rig.coordinator.profile_record.pending
    assert rig.coordinator._client is None
    rig.clients[0].disconnect.assert_awaited_once()


async def test_disconnect_failure_does_not_mask_refresh_error(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    client = rig.clients[0]
    client.mode = "error"
    client.disconnect.side_effect = OSError("Synthetic teardown failure")

    with pytest.raises(HomeAssistantError, match="refresh failed"):
        await coordinator.async_request_refresh()

    assert coordinator._client is None
    assert not coordinator.available
    assert coordinator.data is None


async def test_cancelled_durable_save_is_published_before_cancellation(rig):
    """Cancellation-safe Store completion and RAM revision stay consistent."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def cancellation_safe_save(record):
        commit = asyncio.create_task(release.wait())
        started.set()
        try:
            await asyncio.shield(commit)
        except asyncio.CancelledError:
            await commit
            raise

    rig.store.async_save.side_effect = cancellation_safe_save
    task = asyncio.create_task(rig.coordinator.async_set_volume(3))
    await started.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.coordinator.profile_record.revision == 1
    assert rig.coordinator.profile_record.desired_profile == {"volume": 3}
    rig.discovery.assert_not_called()


async def test_light_settings_saved_but_off_never_erases_them(rig):
    coordinator = rig.coordinator
    await coordinator.async_set_light(True, brightness=5, color=9)
    assert coordinator.profile_record.desired_profile == {"brightness": 5, "color": 9}
    assert ("send", bytes([0x3C, 9])) in rig.journal
    assert ("send", bytes([0x3A, 5])) in rig.journal
    revision = coordinator.profile_record.revision
    await coordinator.async_set_light(False)
    assert coordinator.profile_record.revision == revision
    assert coordinator.profile_record.desired_profile["brightness"] == 5
    assert ("send", bytes([0x3E])) in rig.journal
    await coordinator.async_set_light(True)
    assert coordinator.profile_record.revision == revision
    assert rig.clients[0].send.call_args.args[0] == bytes([0x3A, 5])


async def test_light_duration_is_persistent_and_default_on_is_not(rig):
    coordinator = rig.coordinator
    await coordinator.async_set_light(True)
    assert coordinator.profile_record.revision == 0
    assert ("send", bytes([0x3A, 1])) in rig.journal
    await coordinator.async_set_light_duration(5)
    assert coordinator.profile_record.desired_profile == {"light_duration": 5}
    assert ("send", bytes([0x6C, 5])) in rig.journal


async def test_transient_play_stop_off_never_persist_or_retry(rig):
    coordinator = rig.coordinator
    await coordinator.async_play(7)
    await coordinator.async_stop_audio()
    assert ("send", bytes([0x3F, 7])) in rig.journal
    assert ("send", bytes([0x38])) in rig.journal
    rig.store.async_save.assert_not_awaited()
    rig.clients[0].send.side_effect = OSError("Ambiguous write")
    with pytest.raises(HomeAssistantError, match="will not be replayed"):
        await coordinator.async_play(1)
    assert rig.clients[0].send.await_count == 3
    await coordinator.async_request_refresh()
    rig.clients[1].send.assert_not_awaited()
    assert coordinator.profile_record.revision == 0


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("async_set_light", (1,)),
        ("async_set_light", (True, 0)),
        ("async_set_light", (True, None, 10)),
        ("async_set_volume", (True,)),
        ("async_set_volume", (10,)),
        ("async_set_light_duration", (6,)),
        ("async_play", (8,)),
        ("async_play", (True,)),
        ("async_set_maintenance", (1,)),
    ],
)
async def test_method_validation_before_storage_or_ble(rig, method, arguments):
    with pytest.raises(ProfileValidationError):
        await getattr(rig.coordinator, method)(*arguments)
    rig.store.async_save.assert_not_awaited()
    rig.discovery.assert_not_called()


async def test_maintenance_releases_ble_and_allows_only_saved_edits(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    await coordinator.async_set_maintenance(True)
    assert coordinator.profile_record.maintenance
    assert not coordinator.available
    rig.clients[0].disconnect.assert_awaited_once()
    await coordinator.async_set_volume(8)
    assert coordinator.profile_record.desired_profile == {"volume": 8}
    assert coordinator.profile_record.pending
    with pytest.raises(HomeAssistantError):
        await coordinator.async_play(1)
    assert len(rig.clients) == 1
    await coordinator.async_set_maintenance(False)
    assert not coordinator.profile_record.maintenance
    assert len(rig.clients) == 1  # no automatic reconnect or restore
    rig.clients[0].send.assert_not_awaited()


async def test_maintenance_survives_new_coordinator_without_reconnecting(rig):
    await rig.coordinator.async_set_maintenance(True)
    record = rig.coordinator.profile_record
    rig.store.async_load.return_value = record
    reopened = LumalouCoordinator(
        rig.coordinator.hass, rig.coordinator.entry, rig.store
    )
    await reopened.async_setup()
    assert reopened.profile_record.maintenance
    assert reopened.profile_record == record
    rig.client_factory.assert_not_called()


async def test_leaving_maintenance_recovers_present_device_without_replaying(rig):
    coordinator = rig.coordinator
    await coordinator.async_set_maintenance(True)
    await coordinator.async_set_volume(8)
    pending = coordinator.profile_record
    rig.address_present.return_value = True
    coordinator.async_start()
    assert coordinator.present
    assert not rig.background_tasks

    await coordinator.async_set_maintenance(False)
    assert len(rig.background_tasks) == 1
    await rig.background_tasks[0]

    assert coordinator.available
    assert coordinator.profile_record.revision == pending.revision
    assert coordinator.profile_record.desired_profile == pending.desired_profile
    assert coordinator.profile_record.pending
    rig.clients[0].request_state.assert_awaited_once()
    rig.clients[0].send.assert_not_awaited()
    await coordinator.async_shutdown()


async def test_leaving_maintenance_coalesces_existing_cooldown_recovery(rig):
    coordinator = rig.coordinator
    coordinator.async_start()
    coordinator._next_recovery_at = asyncio.get_running_loop().time() + 3600
    coordinator._async_handle_advertisement(Mock(), Mock())
    await asyncio.sleep(0)
    original_recovery = coordinator._recovery_task
    assert original_recovery is not None

    await coordinator.async_set_maintenance(True)
    await coordinator.async_set_maintenance(False)

    assert coordinator._recovery_task is original_recovery
    assert len(rig.background_tasks) == 1
    rig.client_factory.assert_not_called()
    await coordinator.async_shutdown()
    assert original_recovery.cancelled()


async def test_entries_do_not_share_saved_intent(rig):
    other_store = SimpleNamespace(
        async_load=AsyncMock(
            return_value=ProfileRecord(revision=10, desired_profile={"volume": 9})
        ),
        async_save=AsyncMock(),
    )
    other_entry = SimpleNamespace(
        data={"address": "synthetic-other"}, title="Other", entry_id="other"
    )
    other = LumalouCoordinator(rig.coordinator.hass, other_entry, other_store)
    await other.async_setup()
    rig.discovery.return_value = None
    await rig.coordinator.async_set_volume(2)
    assert other.profile_record.revision == 10
    assert other.profile_record.desired_profile == {"volume": 9}
    other_store.async_save.assert_not_awaited()


async def test_clock_uses_supplied_ha_timezone_and_never_persists(rig):
    now = datetime(
        2026, 9, 20, 0, 1, 2, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")
    )
    with patch("custom_components.lumalou.coordinator.dt_util.now", return_value=now):
        await rig.coordinator.async_sync_clock()
    # Sunday=0, local midnight is valid; no stored calendar timestamp.
    assert ("send", bytes([0x30, 0, 1, 2, 0])) in rig.journal
    rig.store.async_save.assert_not_awaited()


async def test_clock_rejects_obviously_invalid_host_time(rig):
    with (
        patch(
            "custom_components.lumalou.coordinator.dt_util.now",
            return_value=datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC")),
        ),
        pytest.raises(HomeAssistantError, match="not trustworthy"),
    ):
        await rig.coordinator.async_sync_clock()
    rig.discovery.assert_not_called()


async def test_import_export_confirmation_conflicts_and_no_restore(rig):
    coordinator = rig.coordinator
    payload = {
        "schema_version": 1,
        "scope": "supported_subset",
        "profile": {"playlist": [18, 2, 2], "volume": 1},
    }
    with pytest.raises(ProfileValidationError):
        await coordinator.async_import_profile(payload, 0)
    with pytest.raises(RevisionConflictError):
        await coordinator.async_import_profile(payload, 2, confirmed=True)
    await coordinator.async_import_profile(payload, 0, confirmed=True)
    assert coordinator.profile_record.pending
    assert coordinator.profile_record.revision == 1
    assert coordinator.profile_record.previous == {"revision": 0, "profile": {}}
    assert await coordinator.async_export_profile() == {
        "current_revision": 1,
        "profile": payload,
    }
    payload["profile"]["playlist"].clear()
    assert coordinator.profile_record.desired_profile["playlist"] == [18, 2, 2]
    with pytest.raises(RevisionConflictError):
        await coordinator._save_edit({"volume": 4}, expected_revision=0)
    with pytest.raises(ProfileValidationError):
        await coordinator._save_edit({"volume": 4}, expected_revision=True)
    await coordinator._save_edit({"volume": 4}, expected_revision=1)
    assert coordinator.profile_record.previous["revision"] == 1
    with pytest.raises(HomeAssistantError, match="upstream full-profile"):
        await coordinator.async_restore_profile()
    rig.discovery.assert_not_called()


async def test_export_revision_and_profile_are_one_serialized_snapshot(rig):
    coordinator = rig.coordinator
    apply_started = asyncio.Event()
    release_apply = asyncio.Event()

    async def blocked_apply(_payloads):
        apply_started.set()
        await release_apply.wait()

    with patch.object(coordinator, "_apply_edit", side_effect=blocked_apply):
        edit = asyncio.create_task(coordinator.async_set_volume(6))
        await apply_started.wait()
        export = asyncio.create_task(coordinator.async_export_profile())
        await asyncio.sleep(0)
        assert not export.done()
        release_apply.set()
        await edit

    assert await export == {
        "current_revision": 1,
        "profile": {
            "schema_version": 1,
            "scope": "supported_subset",
            "profile": {"volume": 6},
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"schema_version": 2, "scope": "supported_subset", "profile": {}},
        {"schema_version": True, "scope": "supported_subset", "profile": {}},
        {"schema_version": 1, "scope": "full", "profile": {}},
        {"schema_version": 1, "scope": "supported_subset", "profile": {"raw": 1}},
    ],
)
async def test_import_rejects_unsupported_schemas_without_writes(rig, payload):
    with pytest.raises(ProfileValidationError):
        await rig.coordinator.async_import_profile(payload, 0, confirmed=True)
    rig.store.async_save.assert_not_awaited()


async def test_advertisements_coalesce_one_background_refresh(rig):
    coordinator = rig.coordinator
    coordinator.async_start()
    advertisement = rig.register_callback.call_args.args[1]
    assert rig.register_callback.call_args.args[3].value == "passive"
    assert rig.register_callback.call_args.kwargs["replay"].name == "DISABLED"
    assert rig.register_callback.call_args.args[2] == {
        "address": rig.device.address,
        "connectable": True,
    }
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_refresh():
        started.set()
        await release.wait()

    with patch.object(
        coordinator, "async_request_refresh", side_effect=blocked_refresh
    ) as refresh:
        advertisement(Mock(), Mock())
        advertisement(Mock(), Mock())
        advertisement(Mock(), Mock())
        await started.wait()
        assert coordinator.present
        assert not coordinator.available
        assert len(rig.background_tasks) == 1
        refresh.assert_awaited_once_with()
        release.set()
        await rig.background_tasks[0]

    await coordinator.async_shutdown()


def test_partial_callback_registration_is_rolled_back(rig):
    coordinator = rig.coordinator
    unsubscribe = rig.register_callback.return_value
    rig.track_unavailable.side_effect = RuntimeError("Synthetic registration failure")

    with pytest.raises(RuntimeError, match="registration failure"):
        coordinator.async_start()

    unsubscribe.assert_called_once_with()
    assert not coordinator._callbacks_started
    assert not coordinator._unsubscribers


async def test_unavailable_immediately_invalidates_and_closes_old_session(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    old_client = rig.clients[0]
    received = coordinator._received
    rig.address_present.return_value = True
    coordinator.async_start()
    unavailable = rig.track_unavailable.call_args.args[1]

    unavailable(Mock())

    assert not coordinator.present
    assert not coordinator.available
    assert coordinator.data is None
    assert coordinator._client is None
    old_client.on_state(rig.state)
    assert coordinator._received == received
    await asyncio.gather(*list(coordinator._background_tasks))
    old_client.disconnect.assert_awaited_once_with()
    await coordinator.async_shutdown()


async def test_unavailable_cleanup_cannot_close_reconnected_session(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    old_client = rig.clients[0]
    rig.address_present.return_value = True
    coordinator.async_start()
    unavailable = rig.track_unavailable.call_args.args[1]
    advertisement = rig.register_callback.call_args.args[1]
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def blocked_disconnect():
        close_started.set()
        await release_close.wait()
        old_client.connected = False

    old_client.disconnect.side_effect = blocked_disconnect
    unavailable(Mock())
    await close_started.wait()
    advertisement(Mock(), Mock())
    release_close.set()
    await asyncio.gather(*list(coordinator._background_tasks))

    assert len(rig.clients) == 2
    assert coordinator._client is rig.clients[1]
    assert coordinator.available
    old_client.disconnect.assert_awaited_once_with()
    await coordinator.async_shutdown()
    rig.clients[1].disconnect.assert_awaited_once_with()


async def test_shutdown_unregisters_and_cancels_delayed_recovery(rig):
    coordinator = rig.coordinator
    coordinator.async_start()
    advertisement = rig.register_callback.call_args.args[1]
    coordinator._next_recovery_at = asyncio.get_running_loop().time() + 3600
    advertisement(Mock(), Mock())
    await asyncio.sleep(0)
    recovery = coordinator._recovery_task
    assert recovery is not None

    await coordinator.async_shutdown()

    assert recovery.cancelled()
    assert not coordinator._background_tasks
    rig.register_callback.return_value.assert_called_once_with()
    rig.track_unavailable.return_value.assert_called_once_with()


async def test_shutdown_cancels_active_request_and_unregisters_listeners(rig):
    coordinator = rig.coordinator
    await coordinator.async_request_refresh()
    client = rig.clients[0]
    started = asyncio.Event()

    async def hang(**kwargs):
        started.set()
        await asyncio.Event().wait()

    client.request_state.side_effect = hang
    listener = Mock()
    coordinator.async_add_listener(listener)
    task = asyncio.create_task(coordinator.async_request_refresh())
    await started.wait()
    await coordinator.async_shutdown()
    assert task.cancelled()
    assert not coordinator._tasks
    assert not coordinator._listeners
    assert coordinator._client is None
    assert not coordinator.available
    received = coordinator._received
    client.on_state(rig.state)
    assert coordinator._received == received
    with pytest.raises(HomeAssistantError, match="unloaded"):
        await coordinator.async_request_refresh()


async def test_queued_operation_rechecks_unloaded_after_acquiring_lock(rig):
    coordinator = rig.coordinator
    await coordinator._lock.acquire()
    task = asyncio.create_task(coordinator.async_export_profile())
    await asyncio.sleep(0)
    coordinator._stopped = True
    coordinator._lock.release()
    with pytest.raises(HomeAssistantError, match="unloaded"):
        await task
    assert not coordinator._tasks


async def test_safe_adapter_uses_device_and_denies_every_unapproved_opcode():
    device = BLEDevice("synthetic-device", "Test", {})
    client = SafeLumalouClient(device, lambda state: None)
    assert client.address is device
    with patch.object(client, "_write", new_callable=AsyncMock) as write:
        for opcode in set(range(256)) - ALLOWED_OPCODES:
            with pytest.raises(HomeAssistantError, match="Unsupported"):
                await client.send(bytes([opcode]))
        with pytest.raises(HomeAssistantError):
            await client.send(b"")
        write.assert_not_awaited()
        await client.send(bytes([0x37, 1]))
        write.assert_awaited_once()


async def test_upstream_connect_receives_current_ble_device_and_only_main_gatt():
    """Test the actual pinned upstream forwarding boundary, not a client fake."""
    device = BLEDevice("synthetic-device", "Test", {})
    _, public_key = crypto.generate_keypair()
    token = bytes(25) + public_key + bytes(4)
    transport = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        start_notify=AsyncMock(),
        read_gatt_char=AsyncMock(return_value=token),
        write_gatt_char=AsyncMock(),
    )
    client = SafeLumalouClient(device, lambda state: None)
    with (
        patch("lumalou.client.BleakClient", return_value=transport) as factory,
        patch("lumalou.client.BleakScanner.discover", new_callable=AsyncMock) as scan,
    ):
        await client.connect()
        factory.assert_called_once_with(device)
        scan.assert_not_awaited()
        assert client.connected
        written = [call.args[0] for call in transport.write_gatt_char.await_args_list]
        assert written == [
            "4cea0005-c678-4202-b5d3-712dbb5e5b14",
            "4cea0002-c678-4202-b5d3-712dbb5e5b14",
        ]
        assert len(transport.write_gatt_char.await_args_list[0].args[1]) == 37
        transport.start_notify.assert_awaited_once()
        await client.disconnect()
        transport.disconnect.assert_awaited_once()
