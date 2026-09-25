"""Serialized lifecycle, restore and recovery tests; all Bluetooth is mocked."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BLEAK_SAFETY_TIMEOUT, MAX_CONNECT_ATTEMPTS
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from lumalou import crypto
from lumalou.client import FreshSessionRequiredError
from lumalou.profile import (
    ClockSettings,
    MusicPlaylist,
    decode_clock_settings_set,
    decode_music_playlist_set,
    decode_routine_music_settings_set,
)
from lumalou.responses import CurrentDate, parse_current_date
from lumalou.schedules import (
    ClockTime,
    DailyRoutine,
    WeeklyAlarms,
    WeeklyTimes,
    decode_daily_routine,
    decode_weekly_alarms,
    decode_weekly_times,
)

from custom_components.lumalou.const import (
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    CONNECT_TIMEOUT,
    GLOBAL_STATE_FIELDS,
)
from custom_components.lumalou.coordinator import (
    LumalouCoordinator,
    ProfileRestoreError,
)
from custom_components.lumalou.models import (
    DAYS,
    FULL_PROFILE_FIELDS,
    ProfileRecord,
    ProfileValidationError,
    RevisionConflictError,
)
from custom_components.lumalou.restore import build_restore_steps
from tests.test_restore import complete_profile

FINGERPRINT = "a" * 64
ZONE = ZoneInfo("Etc/GMT-9")  # synthetic fixed offset
# Sunday (device weekday 0) at noon; the fake device clock starts equal.
NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=ZONE)
DAY_SETTERS = dict(zip((0x5A, 0x5C, 0x5E, 0x60, 0x62, 0x64, 0x66), DAYS, strict=True))
GLOBAL_SETTERS = {
    0x37: "currentVolume",
    0x3A: "lightBrightness",
    0x3C: "lightColor",
    0x42: "playlistDuration",
    0x44: "ready2RiseStatus",
    0x58: "routineModeStatus",
    0x6C: "lightDuration",
    0x77: "routineVolume",
}


class FakeDevice:
    """Synthetic device model that answers strict reads and applies setters."""

    def __init__(self) -> None:
        self.state = dict.fromkeys(GLOBAL_STATE_FIELDS, 0)
        self.state.update(currentVolume=2, lightBrightness=3, lightColor=4)
        self.playlist = MusicPlaylist(tuple(range(1, 13)))
        week = WeeklyTimes(tuple(ClockTime(20, index) for index in range(7)))
        self.blocks = {
            "r2r_times": week,
            "sleepy_times": week,
            "r2r_alarms": WeeklyAlarms((9,) * 7, 0),
        }
        self.routines = {day: DailyRoutine(None, (None,) * 12) for day in DAYS}
        self.clock = CurrentDate(12, 0, 0, 0)
        self.retain_writes = True

    def query(self, name: str):
        if name == "current_date":
            return self.clock
        if name == "music_playlist":
            return self.playlist
        if name == "clock_settings":
            return ClockSettings(
                bool(self.state["clockDisplay"]),
                self.state["clockBrightness"],
                self.state["clockFormat"],
            )
        return self.blocks[name]

    def apply(self, payload: bytes) -> None:
        opcode, args = payload[0], bytes(payload[1:])
        if opcode == 0x30:
            self.clock = parse_current_date(args)
            return
        if not self.retain_writes:
            return
        if opcode in GLOBAL_SETTERS:
            self.state[GLOBAL_SETTERS[opcode]] = args[0]
            if opcode == 0x3C:  # a colour switches the light on
                self.state.update(lightStatus=1, activityState=1)
        elif opcode == 0x3E:
            self.state.update(lightStatus=0, activityState=0)
        elif opcode == 0x7B:  # routine start: routine mode, silent preview
            self.state["operationMode"] = 7
        elif opcode == 0x6B and args[0] in (3, 4):  # complete all / cancel
            self.state["operationMode"] = 0
        elif opcode == 0x3F:  # source 0 is the soother: music and light
            self.state.update(musicStatus=1, lightStatus=int(args[0] == 0) or 0)
        elif opcode == 0x38:
            self.state["musicStatus"] = 0
        elif opcode == 0x40:
            self.playlist = decode_music_playlist_set(args)
        elif opcode == 0x46:
            self.blocks["r2r_times"] = decode_weekly_times(args)
        elif opcode == 0x48:
            self.blocks["sleepy_times"] = decode_weekly_times(args)
        elif opcode == 0x4A:
            self.blocks["r2r_alarms"] = decode_weekly_alarms(args)
        elif opcode == 0x69:
            music = decode_routine_music_settings_set(args)
            self.state.update(
                routineMusicStatus=music.music,
                taskRewardSfx=music.task_reward,
                routineRewardSfx=music.routine_reward,
            )
        elif opcode == 0x79:
            clock = decode_clock_settings_set(args)
            self.state.update(
                clockDisplay=int(clock.display_on),
                clockBrightness=clock.brightness,
                clockFormat=clock.format,
            )
        elif opcode in DAY_SETTERS:
            self.routines[DAY_SETTERS[opcode]] = decode_daily_routine(args)


@pytest.fixture
def rig():
    """Exercise public coordinator APIs against a deterministic fake session."""
    journal = []
    device = BLEDevice("synthetic-device", "Test Lumalou", {})
    fake = FakeDevice()
    # push: the device pushes GLOBAL_STATE after every write (hardware).
    settings = SimpleNamespace(mode="fresh", fail_opcode=None, push=True)
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
            CONF_DEVICE_FINGERPRINT: FINGERPRINT,
            CONF_PROTOCOL_VERIFIED: True,
        },
        options={},
        title="Test Lumalou",
        entry_id="synthetic",
        async_create_background_task=Mock(side_effect=create_background_task),
    )
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))
    coordinator = LumalouCoordinator(hass, entry, store)
    clients = []

    def create_client(
        _hass,
        ble_device,
        *,
        expected_device_fingerprint,
        on_state,
        on_response,
        disconnected_callback,
    ):
        assert _hass is hass
        assert ble_device is device
        assert expected_device_fingerprint == FINGERPRINT
        client = SimpleNamespace(
            connected=False,
            on_state=on_state,
            on_response=on_response,
            lost=disconnected_callback,
            mode=settings.mode,
            requested=set(),
            state=None,
        )

        def once(name):
            # The pinned strict client allows each response type once.
            if name in client.requested:
                raise FreshSessionRequiredError("synthetic repeated request")
            client.requested.add(name)

        async def connect(**kwargs):
            assert kwargs["timeout"] > 0
            journal.append(("connect", ble_device))
            client.connected = True

        async def disconnect():
            client.connected = False
            journal.append(("disconnect", ble_device))

        async def send(payload, **kwargs):
            assert kwargs["timeout"] > 0
            if settings.fail_opcode == payload[0]:
                raise OSError("Synthetic write failure")
            journal.append(("send", payload))
            fake.apply(payload)
            if settings.push and client.connected:
                on_state(deepcopy(fake.state))

        async def request_state(**kwargs):
            assert kwargs["timeout"] > 0
            if client.mode == "error":
                raise OSError("Synthetic BLE disconnect")
            once("global_state")
            journal.append(("request", None))
            if client.mode == "fresh":
                on_state(deepcopy(fake.state))
                client.state = deepcopy(fake.state)
            return deepcopy(fake.state)

        async def request_named(name, **kwargs):
            assert kwargs["timeout"] > 0
            once(name)
            journal.append(("request_named", name))
            value = fake.query(name)
            return SimpleNamespace(decode=lambda: value)

        async def request_day_routine(day, **kwargs):
            assert kwargs["timeout"] > 0
            once(day)
            journal.append(("request_day_routine", day))
            value = fake.routines[day]
            return SimpleNamespace(decode=lambda: value)

        client.connect = AsyncMock(side_effect=connect)
        client.disconnect = AsyncMock(side_effect=disconnect)
        client.send = AsyncMock(side_effect=send)
        client.request_state = AsyncMock(side_effect=request_state)
        client.request_named = AsyncMock(side_effect=request_named)
        client.request_day_routine = AsyncMock(side_effect=request_day_routine)
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
        patch("custom_components.lumalou.coordinator.dt_util.now", return_value=NOW),
        # Tests do not wait out the hardware reconnect gap.
        patch("custom_components.lumalou.coordinator.RECONNECT_DELAY", 0),
        patch("custom_components.lumalou.coordinator.STATE_CONFIRM_TIMEOUT", 0.05),
        patch("custom_components.lumalou.coordinator.ROUTINE_START_DELAY", 0),
    ):
        yield SimpleNamespace(
            coordinator=coordinator,
            store=store,
            device=device,
            fake=fake,
            settings=settings,
            clients=clients,
            discovery=discovery,
            client_factory=client_factory,
            journal=journal,
            state=fake.state,
            background_tasks=background_tasks,
            address_present=address_present,
            register_callback=register_callback,
            track_unavailable=track_unavailable,
            hass=hass,
        )


def sends(rig, start: int = 0) -> list[bytes]:
    return [item[1] for item in rig.journal[start:] if item[0] == "send"]


async def live(rig) -> None:
    """Open the live session the way background recovery does."""
    await rig.coordinator._async_recover()


def push_clock(client, clock: CurrentDate) -> None:
    """Deliver a pushed CURRENT_DATE frame from the device."""
    client.on_response(SimpleNamespace(opcode=0x13, decode=lambda: clock))


async def verified_profile(rig) -> dict:
    """Enroll: read + preview + confirm the device profile as verified."""
    coordinator = rig.coordinator
    await coordinator.async_setup()
    snapshot, revision = await coordinator.async_read_profile_snapshot()
    await coordinator.async_accept_device_profile(snapshot, revision, confirmed=True)
    return snapshot


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
    assert rig.coordinator.data is None
    assert not rig.coordinator.available
    assert rig.coordinator.sw_version is None
    rig.discovery.assert_not_called()
    rig.client_factory.assert_not_called()
    rig.store.async_save.assert_not_awaited()
    listener.assert_called_once()
    remove()
    rig.coordinator._notify()
    listener.assert_called_once()


def _weekly_times(hour: int) -> dict[str, dict[str, int]]:
    """Build one complete schema-v2 weekly schedule block."""
    return {day: {"hour": hour, "minute": index} for index, day in enumerate(DAYS)}


def _weekly_routines() -> dict[str, dict[str, object]]:
    """Build the exact seven-day, twelve-slot routine schema."""
    return {
        day: {
            "time": {"hour": 7, "minute": index},
            "slots": [
                {"step": 1, "task": index} if slot == 0 else None for slot in range(12)
            ],
        }
        for index, day in enumerate(DAYS)
    }


async def test_public_offline_profile_edit_merges_complex_blocks_without_ble(rig):
    """Saved intent editing is product-independent and never opens a session."""
    coordinator = rig.coordinator
    base = {**complete_profile(), "playlist": [2, 1]}
    rig.store.async_load.return_value = ProfileRecord(
        revision=4, desired_profile=deepcopy(base)
    )
    await coordinator.async_setup()
    changes = {
        "sleepy_times": _weekly_times(20),
        "routines": _weekly_routines(),
        "routine_settings": {
            "enabled": True,
            "music": 15,
            "volume": 15,
            "task_reward_sfx": 15,
            "routine_reward_sfx": 0,
        },
    }

    saved = await coordinator.async_edit_profile(changes, expected_revision=4)

    assert saved.revision == 5
    assert saved.previous == {"revision": 4, "profile": base}
    assert saved.desired_profile["playlist"] == [2, 1]
    assert saved.desired_profile["sleepy_times"] == _weekly_times(20)
    assert saved.desired_profile["routines"] == _weekly_routines()
    assert saved.pending is True
    assert saved.sync_status == "pending"
    assert saved.last_error is None
    rig.store.async_save.assert_awaited_once()
    rig.discovery.assert_not_called()
    rig.client_factory.assert_not_called()
    assert not [item for item in rig.journal if item[0] != "save"]

    # Inputs and returned records are detached from the saved immutable revision.
    changes["routines"]["monday"]["slots"][0]["task"] = 11
    saved.desired_profile["sleepy_times"]["monday"]["hour"] = 0
    monday_slot = coordinator.profile_record.desired_profile["routines"]["monday"][
        "slots"
    ][0]
    assert monday_slot == {"step": 1, "task": 1}
    assert coordinator.profile_record.desired_profile["sleepy_times"]["monday"] == {
        "hour": 20,
        "minute": 1,
    }


async def test_public_offline_profile_edit_requires_a_complete_profile(rig):
    """Editors never fabricate blocks: a device read must come first."""
    coordinator = rig.coordinator
    rig.store.async_load.return_value = ProfileRecord(desired_profile={"playlist": [3]})
    await coordinator.async_setup()
    with pytest.raises(HomeAssistantError, match="Read the device profile"):
        await coordinator.async_edit_profile({"playlist": [4]}, expected_revision=0)
    rig.store.async_save.assert_not_awaited()


async def test_public_offline_profile_edit_rejects_stale_concurrent_revision(rig):
    coordinator = rig.coordinator
    rig.store.async_load.return_value = ProfileRecord(
        desired_profile=complete_profile()
    )
    await coordinator.async_setup()
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def delayed_save(_record):
        save_started.set()
        await release_save.wait()

    rig.store.async_save.side_effect = delayed_save
    first = asyncio.create_task(
        coordinator.async_edit_profile({"playlist": [3]}, expected_revision=0)
    )
    await save_started.wait()
    stale = asyncio.create_task(
        coordinator.async_edit_profile({"playlist": [2]}, expected_revision=0)
    )
    await asyncio.sleep(0)
    assert not stale.done()

    release_save.set()
    first_record = await first
    with pytest.raises(RevisionConflictError, match="reopen the editor"):
        await stale

    assert first_record.revision == 1
    assert coordinator.profile_record.desired_profile == {
        **complete_profile(),
        "playlist": [3],
    }
    assert rig.store.async_save.await_count == 1
    rig.discovery.assert_not_called()
    rig.client_factory.assert_not_called()


async def test_public_offline_profile_edit_fails_after_unload(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    await coordinator.async_shutdown()

    with pytest.raises(HomeAssistantError, match="integration is unloaded"):
        await coordinator.async_edit_profile({"playlist": [3]}, expected_revision=0)

    rig.store.async_save.assert_not_awaited()
    rig.discovery.assert_not_called()
    rig.client_factory.assert_not_called()


async def test_complete_profile_read_uses_fresh_typed_blocks_without_saving(rig):
    """Full read consumes unique fresh responses and only returns a preview."""
    coordinator = rig.coordinator
    await coordinator.async_setup()

    snapshot, revision = await coordinator.async_read_profile_snapshot()

    assert set(snapshot) == FULL_PROFILE_FIELDS
    assert revision == 0
    assert snapshot["playlist"] == list(range(1, 13))
    assert snapshot["clock_settings"] == {
        "display": False,
        "brightness": 0,
        "format": 0,
    }
    assert snapshot["ready_to_rise"]["times"]["sunday"] == {"hour": 20, "minute": 0}
    assert snapshot["alarm"]["days"]["sunday"] == 9
    assert type(snapshot["alarm"]["days"]["sunday"]) is int
    assert snapshot["routines"]["sunday"]["slots"] == [None] * 12
    assert [event for event in rig.journal if event[0] == "request_named"] == [
        ("request_named", name)
        for name in (
            "current_date",
            "music_playlist",
            "clock_settings",
            "r2r_times",
            "sleepy_times",
            "r2r_alarms",
        )
    ]
    assert [event for event in rig.journal if event[0] == "request_day_routine"] == [
        ("request_day_routine", day) for day in DAYS
    ]
    assert not any(event[0] == "send" for event in rig.journal)
    rig.store.async_save.assert_not_awaited()
    assert not coordinator.profile_record.desired_profile
    # The read session stays open for push updates instead of being replaced
    # by an immediate reconnect that reads the same state again.
    assert coordinator.available
    assert coordinator.data == rig.state
    assert rig.clients[0].connected
    assert len(rig.clients) == 1
    assert not rig.background_tasks


async def test_preview_read_then_confirm_opens_one_session(rig):
    """Hardware: read + confirm used to reconnect right after the read."""
    coordinator = rig.coordinator
    coordinator.protocol_verified = False
    coordinator.present = True
    await coordinator.async_setup()

    snapshot, revision = await coordinator.async_read_profile_snapshot()
    await coordinator.async_accept_device_profile(snapshot, revision, confirmed=True)
    await asyncio.gather(*rig.background_tasks)

    assert len(rig.clients) == 1
    assert [event[0] for event in rig.journal].count("connect") == 1
    assert coordinator.available and coordinator.protocol_verified

    # A strict session answers GLOBAL_STATE once; another read reconnects.
    await coordinator.async_read_profile_snapshot()
    assert len(rig.clients) == 2 and coordinator.available


async def test_failed_preview_read_releases_the_session(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    original = rig.client_factory.side_effect

    def failing_read(*args, **kwargs):
        client = original(*args, **kwargs)
        client.request_named.side_effect = OSError("synthetic")
        return client

    rig.client_factory.side_effect = failing_read
    with pytest.raises(HomeAssistantError):
        await coordinator.async_read_profile_snapshot()
    assert not coordinator.available
    assert coordinator._client is None
    assert not rig.clients[0].connected


async def test_control_unlocks_only_after_confirmed_verified_save(rig):
    """Read + preview never unlocks control; only the confirmed commit does."""
    coordinator = rig.coordinator
    coordinator.protocol_verified = False
    coordinator.entry.data[CONF_PROTOCOL_VERIFIED] = False
    update_entry = rig.hass.config_entries.async_update_entry
    await coordinator.async_setup()

    with pytest.raises(HomeAssistantError, match="Read and verify the complete"):
        await coordinator.async_set_level("volume", 3)
    rig.client_factory.assert_not_called()

    snapshot, revision = await coordinator.async_read_profile_snapshot()

    # A cancelled confirm step leaves control locked.
    assert not coordinator.protocol_verified
    update_entry.assert_not_called()
    with pytest.raises(HomeAssistantError, match="Read and verify the complete"):
        await coordinator.async_set_level("volume", 3)
    rig.store.async_save.assert_not_awaited()

    await coordinator.async_accept_device_profile(snapshot, revision, confirmed=True)

    assert coordinator.protocol_verified
    update_entry.assert_called_once_with(
        coordinator.entry,
        data={**coordinator.entry.data, CONF_PROTOCOL_VERIFIED: True},
    )


async def test_only_the_previewed_device_read_can_be_committed(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    snapshot, revision = await coordinator.async_read_profile_snapshot()
    edited = deepcopy(snapshot)
    edited["playlist"] = [9]

    with pytest.raises(HomeAssistantError, match="Read the device profile again"):
        await coordinator.async_accept_device_profile(edited, revision, confirmed=True)

    await coordinator.async_accept_device_profile(snapshot, revision, confirmed=True)
    with pytest.raises(HomeAssistantError, match="Read the device profile again"):
        await coordinator.async_accept_device_profile(snapshot, 1, confirmed=True)
    assert rig.store.async_save.await_count == 1


async def test_malformed_profile_read_disconnects_without_saving(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    original_factory = rig.client_factory.side_effect

    def create_malformed_client(*args, **kwargs):
        client = original_factory(*args, **kwargs)
        client.request_named.side_effect = OSError("synthetic response timeout")
        return client

    rig.client_factory.side_effect = create_malformed_client
    with pytest.raises(HomeAssistantError, match="Could not read a complete"):
        await coordinator.async_read_profile_snapshot()

    client = rig.clients[-1]
    assert not client.connected
    rig.store.async_save.assert_not_awaited()
    assert not coordinator.profile_record.desired_profile


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda rig: setattr(rig.fake, "playlist", MusicPlaylist((1, 0, 2) + (0,) * 9)),
        lambda rig: setattr(
            rig.fake,
            "query",
            lambda name, query=rig.fake.query: (
                ClockSettings(True, 9, 1) if name == "clock_settings" else query(name)
            ),
        ),
        lambda rig: rig.state.update(routineModeStatus=2),
        lambda rig: setattr(rig.fake, "clock", None),
    ],
)
async def test_inconsistent_profile_read_is_rejected(rig, corrupt):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    corrupt(rig)

    with pytest.raises(HomeAssistantError, match="Could not read a complete"):
        await coordinator.async_read_profile_snapshot()

    rig.store.async_save.assert_not_awaited()


async def test_global_state_change_during_read_is_rejected(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    original_factory = rig.client_factory.side_effect

    def create_changing_client(*args, **kwargs):
        client = original_factory(*args, **kwargs)
        original_named = client.request_named.side_effect

        async def named_then_push(name, **kwargs):
            if name == "r2r_alarms":
                client.state = {**client.state, "currentVolume": 9}
            return await original_named(name, **kwargs)

        client.request_named.side_effect = named_then_push
        return client

    rig.client_factory.side_effect = create_changing_client
    with pytest.raises(HomeAssistantError, match="Could not read a complete"):
        await coordinator.async_read_profile_snapshot()


async def test_user_confirmed_device_snapshot_saves_as_verified_revision(rig):
    """A full fresh read is saved as the current verified desired revision."""
    snapshot = await verified_profile(rig)

    record = rig.coordinator.profile_record
    assert record.revision == 1
    assert record.verified_revision == 1
    assert record.verified_fingerprint == FINGERPRINT
    assert record.is_verified
    assert record.pending is False
    assert record.sync_status == "saved"
    assert record.desired_profile == snapshot
    rig.store.async_save.assert_awaited_once()


async def test_device_snapshot_acceptance_requires_complete_profile_and_cas(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    snapshot, revision = await coordinator.async_read_profile_snapshot()

    with pytest.raises(ProfileValidationError):
        await coordinator.async_accept_device_profile(
            {"volume": 3}, revision, confirmed=True
        )
    with pytest.raises(ProfileValidationError, match="Confirm"):
        await coordinator.async_accept_device_profile(
            snapshot, revision, confirmed=False
        )
    with pytest.raises(RevisionConflictError):
        await coordinator.async_accept_device_profile(
            snapshot, revision + 1, confirmed=True
        )

    rig.store.async_save.assert_not_awaited()


# ---- Profile restore executor ----


async def test_restore_writes_minimal_diff_in_order_and_verifies_fresh(rig):
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    desired = deepcopy(snapshot)
    desired["routine_settings"]["volume"] = 6
    desired["routines"]["wednesday"] = {
        "time": {"hour": 18, "minute": 30},
        "slots": [{"step": 1, "task": 3}, {"step": 2, "task": 11}] + [None] * 10,
    }
    desired["ready_to_rise"]["enabled"] = True
    desired["clock_settings"] = {"display": True, "brightness": 2, "format": 1}
    await coordinator.async_edit_profile(desired, expected_revision=1)
    expected = [step.payload for step in build_restore_steps(desired, snapshot)]
    clients_before = len(rig.clients)
    start = len(rig.journal)

    result = await coordinator.async_restore_profile(2, confirmed=True)

    assert sends(rig, start) == expected
    assert [payload[0] for payload in expected] == [0x79, 0x77, 0x60, 0x44]
    assert result.verified
    assert result.revision == 2
    assert not result.automatic
    assert (
        result.planned_steps
        == result.applied_steps
        == (
            "clock_settings",
            "routine_settings.volume",
            "routines.wednesday",
            "ready_to_rise.enabled",
        )
    )
    assert result.mismatched_blocks == ()
    assert not result.clock_synced
    # Read session + separate fresh verification session.
    assert len(rig.clients) == clients_before + 2
    assert rig.clients[-1].request_day_routine.await_count == 7
    record = coordinator.profile_record
    assert record.revision == 2
    assert record.is_verified
    assert record.verified_fingerprint == FINGERPRINT
    assert not record.pending
    assert record.sync_status == "saved"
    statuses = [
        item[1].sync_status for item in rig.journal[start:] if item[0] == "save"
    ]
    assert statuses == ["applying", "saved"]
    assert coordinator.last_restore_result == result
    assert coordinator.available


async def test_restore_of_matching_device_only_verifies(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    start = len(rig.journal)

    result = await coordinator.async_restore_profile(1, confirmed=True)

    assert result.verified
    assert result.planned_steps == ()
    assert not sends(rig, start)


async def test_restore_syncs_deviating_clock_before_profile_writes(rig):
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    routine = {**snapshot["routine_settings"], "volume": 8}
    await coordinator.async_edit_profile(
        {"routine_settings": routine}, expected_revision=1
    )
    rig.fake.clock = CurrentDate(0, 0, 0, 0)
    start = len(rig.journal)

    result = await coordinator.async_restore_profile(2, confirmed=True)

    assert sends(rig, start) == [bytes([0x30, 0x12, 0, 0, 0]), bytes([0x77, 8])]
    assert result.clock_synced
    assert coordinator.last_clock_sync == NOW
    assert coordinator.last_clock_offset == 0
    assert snapshot["routine_settings"]["volume"] == 0


async def test_restore_write_failure_reports_applied_steps_and_never_verifies(rig):
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    desired = deepcopy(snapshot)
    desired["routine_settings"]["volume"] = 7
    desired["ready_to_rise"]["enabled"] = True
    await coordinator.async_edit_profile(desired, expected_revision=1)
    rig.settings.fail_opcode = 0x44

    with pytest.raises(ProfileRestoreError) as caught:
        await coordinator.async_restore_profile(2, confirmed=True)

    result = caught.value.result
    assert result.planned_steps == ("routine_settings.volume", "ready_to_rise.enabled")
    assert result.applied_steps == ("routine_settings.volume",)
    assert result.error == "restore_write"
    assert not result.verified
    record = coordinator.profile_record
    assert record.sync_status == "error"
    assert record.last_error == "restore_write"
    assert record.verified_revision == 1
    assert not record.is_verified
    assert coordinator._client is None
    assert coordinator.last_restore_result == result


async def test_restore_reports_blocks_the_device_did_not_keep(rig):
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    desired = deepcopy(snapshot)
    desired["sleepy_times"]["friday"] = None
    await coordinator.async_edit_profile(desired, expected_revision=1)
    rig.fake.retain_writes = False

    with pytest.raises(ProfileRestoreError, match="not verified") as caught:
        await coordinator.async_restore_profile(2, confirmed=True)

    result = caught.value.result
    assert result.applied_steps == ("sleepy_times",)
    assert result.mismatched_blocks == ("sleepy_times",)
    assert result.error == "restore_mismatch"
    assert coordinator.profile_record.last_error == "restore_mismatch"
    assert not coordinator.profile_record.is_verified


async def test_restore_verification_read_failure_is_not_success(rig):
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    desired = deepcopy(snapshot)
    desired["routine_settings"]["volume"] = 4
    await coordinator.async_edit_profile(desired, expected_revision=1)
    original_factory = rig.client_factory.side_effect
    created = []

    def fail_second_session(*args, **kwargs):
        client = original_factory(*args, **kwargs)
        created.append(client)
        if len(created) == 2:
            client.request_state.side_effect = OSError("synthetic verify failure")
        return client

    rig.client_factory.side_effect = fail_second_session

    with pytest.raises(ProfileRestoreError) as caught:
        await coordinator.async_restore_profile(2, confirmed=True)

    assert caught.value.result.applied_steps == ("routine_settings.volume",)
    assert caught.value.result.error == "restore_verify"
    assert coordinator.profile_record.sync_status == "error"


@pytest.mark.parametrize(
    ("prepare", "expected_revision", "error", "match"),
    [
        (lambda c: None, 1, ProfileValidationError, "Confirm"),
        (lambda c: None, 9, RevisionConflictError, "changed"),
        (
            lambda c: setattr(
                c, "_profile_record", replace(c._profile_record, maintenance=True)
            ),
            1,
            HomeAssistantError,
            "maintenance",
        ),
        (
            lambda c: setattr(c, "protocol_verified", False),
            1,
            HomeAssistantError,
            "Read and verify",
        ),
        (
            lambda c: setattr(
                c,
                "_profile_record",
                replace(c._profile_record, verified_fingerprint="b" * 64),
            ),
            1,
            HomeAssistantError,
            "different device",
        ),
    ],
)
async def test_restore_gates_fail_before_any_ble(
    rig, prepare, expected_revision, error, match
):
    coordinator = rig.coordinator
    await verified_profile(rig)
    prepare(coordinator)
    clients_before = len(rig.clients)

    with pytest.raises(error, match=match):
        await coordinator.async_restore_profile(
            expected_revision, confirmed=match != "Confirm"
        )

    assert len(rig.clients) == clients_before


async def test_restore_read_failure_is_reported_without_writes(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.settings.mode = "error"
    start = len(rig.journal)

    with pytest.raises(HomeAssistantError, match="Could not read a complete"):
        await coordinator.async_restore_profile(1, confirmed=True)

    assert not sends(rig, start)
    assert coordinator.profile_record.is_verified


# ---- Power-loss recovery ----


async def test_recovery_of_unverified_entry_only_reads_state(rig):
    coordinator = rig.coordinator
    coordinator.protocol_verified = False
    await coordinator.async_setup()

    await coordinator._async_recover()

    assert coordinator.available
    assert not [item for item in rig.journal if item[0] == "request_named"]
    assert not sends(rig)


async def test_recovery_syncs_deviating_clock_and_detects_nothing_when_matching(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.fake.clock = CurrentDate(0, 0, 3, 0)
    start = len(rig.journal)

    await coordinator._async_recover()

    assert sends(rig, start) == [bytes([0x30, 0x12, 0, 0, 0])]
    assert coordinator.restore_needed is None
    assert coordinator.available
    assert coordinator.last_clock_offset == 0

    rig.fake.clock = CurrentDate(12, 0, 59, 0)
    start = len(rig.journal)
    await coordinator._async_recover()
    assert not sends(rig, start)
    assert coordinator.last_clock_offset == 59


async def test_failed_recovery_clock_write_pauses_and_recovery_completes(rig, caplog):
    """Hardware: a failing clock write was repeated on every recovery retry."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.fake.clock = CurrentDate(0, 0, 3, 0)  # reset by a power cycle
    rig.settings.fail_opcode = 0x30
    clients = len(rig.clients)

    await coordinator._async_recover()

    # One failed write, then one more read without it: available again.
    assert coordinator.available
    assert len(rig.clients) == clients + 2
    assert [client.send.await_count for client in rig.clients[clients:]] == [1, 0]
    assert coordinator.last_clock_sync is None
    assert coordinator.last_clock_offset > 60
    assert "Could not correct the Test Lumalou clock" in caplog.text
    assert coordinator.clock_sync_paused

    # The next recovery pass reads the device but does not retry the write.
    await coordinator._async_recover()
    assert rig.clients[-1].send.await_count == 0
    assert coordinator.available

    # Pushed clock frames honour the same pause.
    push_clock(rig.clients[-1], rig.fake.clock)
    await asyncio.gather(*rig.background_tasks)
    assert rig.clients[-1].send.await_count == 0


async def test_clock_write_retries_after_the_pause_and_button_clears_it(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.fake.clock = CurrentDate(0, 0, 3, 0)
    rig.settings.fail_opcode = 0x30
    await coordinator._async_recover()
    rig.settings.fail_opcode = None

    coordinator._clock_sync_retry_at = asyncio.get_running_loop().time() - 1
    start = len(rig.journal)
    await coordinator._async_recover()
    assert sends(rig, start) == [bytes([0x30, 0x12, 0, 0, 0])]
    assert not coordinator.clock_sync_paused
    assert coordinator.last_clock_sync == NOW

    # An explicit sync always writes and clears a pause.
    coordinator._clock_sync_retry_at = asyncio.get_running_loop().time() + 3600
    await coordinator.async_sync_clock()
    assert coordinator._clock_sync_retry_at == 0


async def test_restore_clock_write_failure_is_not_paused_or_retried(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.fake.clock = CurrentDate(0, 0, 3, 0)
    rig.settings.fail_opcode = 0x30

    with pytest.raises(HomeAssistantError):
        await coordinator.async_restore_profile(
            coordinator.profile_record.revision, confirmed=True
        )
    assert coordinator._clock_sync_retry_at == 0
    assert not coordinator.available


async def test_changed_settings_without_reset_raise_a_repair_and_never_write(rig):
    """E.g. a schedule changed in the Fisher-Price app: the user decides."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    listener = Mock()
    coordinator.async_add_listener(listener)
    rig.state.update(routineVolume=5)
    rig.fake.routines["monday"] = DailyRoutine(ClockTime(0, 0), (None,) * 12)
    start = len(rig.journal)

    await coordinator._async_recover()

    need = coordinator.restore_needed
    assert need is not None
    assert need.revision == 1
    assert need.changed_blocks == ("routine_settings", "routines")
    assert not need.reset
    assert need.auto_restore_attempts == 0
    # Automatic restore (on by default) only handles a reset.
    assert coordinator.auto_restore_enabled
    assert coordinator.repair_needed == need
    assert not sends(rig, start)
    listener.assert_called()
    assert coordinator.profile_record.is_verified

    # The user fixes it explicitly (e.g. from the Repairs flow).
    result = await coordinator.async_restore_profile(1, confirmed=True)
    assert result.verified
    assert coordinator.restore_needed is None


async def test_everyday_live_state_changes_are_not_power_loss(rig):
    """Light off, colour, volume, brightness, timers: no flag, no restore."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.state.update(
        lightStatus=0,
        lightBrightness=1,
        lightColor=7,
        currentVolume=9,
        musicStatus=1,
        lightDuration=4,
        playlistDuration=5,
    )
    start = len(rig.journal)

    await coordinator._async_recover()

    assert coordinator.restore_needed is None
    assert not sends(rig, start)
    assert coordinator.profile_record.is_verified


async def test_power_loss_reset_is_restored_automatically_by_default(rig):
    """Hardware: a power loss resets the clock to 05:00 Sunday and all settings."""
    coordinator = rig.coordinator
    user = {
        "clockDisplay": 1,
        "clockBrightness": 2,
        "clockFormat": 1,
        "currentVolume": 2,
        "lightBrightness": 3,
        "lightDuration": 0,
        "playlistDuration": 0,
    }
    rig.state.update(user)
    rig.fake.playlist = MusicPlaylist.from_songs([3, 1])
    await verified_profile(rig)
    assert coordinator.entry.options == {}  # automatic restore is the default
    # Factory defaults after the power loss.
    rig.fake.clock = CurrentDate(5, 0, 7, 0)
    rig.state.update(
        clockFormat=0,
        currentVolume=5,
        lightBrightness=5,
        lightDuration=4,
        playlistDuration=5,
    )
    rig.fake.playlist = MusicPlaylist(tuple(range(1, 13)))
    start = len(rig.journal)

    await coordinator._async_recover()

    assert sends(rig, start) == [
        bytes([0x30, 0x12, 0, 0, 0]),  # the clock first
        bytes([0x79, 1, 0x21]),  # 24-hour clock again
        bytes([0x40, 3, 1] + [0] * 10),
        bytes([0x6C, 0]),
        bytes([0x42, 0]),
        bytes([0x37, 2]),
        bytes([0x3A, 3]),
    ]
    # The restore never switched light or sound on.
    assert rig.state["lightStatus"] == 0
    assert rig.state["musicStatus"] == 0
    assert coordinator.last_restore_result.verified
    assert coordinator.last_restore_result.automatic
    assert coordinator.restore_needed is None
    assert coordinator.repair_needed is None
    assert coordinator.available


async def test_reset_without_automatic_restore_raises_a_repair(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    coordinator.entry.options = {"auto_restore": False}
    rig.fake.clock = CurrentDate(5, 0, 0, 0)
    rig.state.update(currentVolume=5)
    start = len(rig.journal)

    await coordinator._async_recover()

    assert sends(rig, start) == [bytes([0x30, 0x12, 0, 0, 0])]
    need = coordinator.restore_needed
    assert need.reset
    assert need.changed_blocks == ("light_and_sound",)
    assert coordinator.repair_needed == need

    # The clock is right now, but the event is still a reset.
    await coordinator._async_recover()
    assert coordinator.restore_needed.reset


async def test_recovery_auto_restores_a_reset(rig):
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    coordinator.entry.options = {"auto_restore": True}
    # Brightness was reset too; writing it back leaves the light off.
    rig.state.update(routineVolume=3, ready2RiseStatus=1, lightBrightness=5)
    rig.fake.clock = CurrentDate(0, 0, 0, 0)
    start = len(rig.journal)

    await coordinator._async_recover()

    assert sends(rig, start) == [
        bytes([0x30, 0x12, 0, 0, 0]),
        bytes([0x3A, snapshot["light_and_sound"]["light_brightness"]]),
        bytes([0x77, snapshot["routine_settings"]["volume"]]),
        bytes([0x44, 0]),
    ]
    assert rig.state["lightStatus"] == 0
    result = coordinator.last_restore_result
    assert result.verified
    assert result.automatic
    assert result.clock_synced
    assert coordinator.restore_needed is None
    assert coordinator.profile_record.is_verified
    assert coordinator.available


async def test_auto_restore_attempts_are_bounded_per_event(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.state.update(routineVolume=3)
    rig.fake.clock = CurrentDate(5, 0, 0, 0)
    rig.fake.retain_writes = False

    for attempt in (1, 2):
        with pytest.raises(ProfileRestoreError):
            await coordinator._async_recover()
        # A failed attempt ends the session; the next one reconnects.
        assert coordinator._client is None
        assert coordinator.restore_needed.auto_restore_attempts == attempt
        assert coordinator.restore_needed.reset
        if attempt == 1:  # automatic restore still handles it: no Repair yet
            assert coordinator.repair_needed is None
    assert coordinator.restore_needed.auto_restore_exhausted
    assert coordinator.repair_needed is coordinator.restore_needed
    start = len(rig.journal)

    await coordinator._async_recover()

    assert not sends(rig, start)
    assert coordinator.restore_needed.auto_restore_exhausted
    assert coordinator.last_restore_result.error == "restore_mismatch"

    # The device later matches again: the event ends and a new one may retry.
    rig.state.update(routineVolume=0)
    await coordinator._async_recover()
    assert coordinator.restore_needed is None


@pytest.mark.parametrize("reason", ["pending_edit", "other_device", "no_option"])
async def test_auto_restore_requires_verified_revision_of_same_device(rig, reason):
    coordinator = rig.coordinator
    await verified_profile(rig)
    coordinator.entry.options = {"auto_restore": reason != "no_option"}
    if reason == "pending_edit":
        await coordinator.async_edit_profile({"playlist": [9]}, expected_revision=1)
    elif reason == "other_device":
        coordinator._profile_record = replace(
            coordinator._profile_record, verified_fingerprint="b" * 64
        )
    rig.state.update(routineVolume=3)
    rig.fake.clock = CurrentDate(5, 0, 0, 0)
    start = len(rig.journal)

    await coordinator._async_recover()

    # Only the clock is written.
    assert sends(rig, start) == [bytes([0x30, 0x12, 0, 0, 0])]
    assert (coordinator.restore_needed is not None) is (reason == "no_option")


async def test_recovery_verifies_a_pending_revision_the_device_matches(rig):
    """E.g. the user applied an edit in the official app: detection re-arms."""
    coordinator = rig.coordinator
    snapshot = await verified_profile(rig)
    await coordinator.async_edit_profile({"playlist": [4, 5]}, expected_revision=1)
    assert not coordinator.profile_record.is_verified
    rig.fake.playlist = MusicPlaylist.from_songs([4, 5])
    start = len(rig.journal)

    await coordinator._async_recover()

    record = coordinator.profile_record
    assert record.revision == 2
    assert record.is_verified
    assert record.verified_fingerprint == FINGERPRINT
    assert not record.pending
    assert record.sync_status == "saved"
    assert not sends(rig, start)

    # A later reset of that revision is detected again.
    rig.fake.playlist = MusicPlaylist.from_songs(snapshot["playlist"])
    await coordinator._async_recover()
    assert coordinator.restore_needed is not None
    assert coordinator.restore_needed.changed_blocks == ("playlist",)


async def test_recovery_keeps_a_differing_pending_revision_pending(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    await coordinator.async_edit_profile({"playlist": [4, 5]}, expected_revision=1)
    saves = rig.store.async_save.await_count

    await coordinator._async_recover()

    assert coordinator.profile_record.pending
    assert not coordinator.profile_record.is_verified
    assert coordinator.restore_needed is None
    assert rig.store.async_save.await_count == saves


async def test_restore_needed_is_obsolete_after_a_new_revision(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.state.update(routineVolume=3)
    await coordinator._async_recover()
    assert coordinator.restore_needed is not None

    await coordinator.async_edit_profile({"playlist": [1]}, expected_revision=1)

    assert coordinator.restore_needed is None


async def test_recovery_read_failure_disconnects_and_raises_for_backoff(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.settings.mode = "error"

    with pytest.raises(HomeAssistantError, match="recovery failed"):
        await coordinator._async_recover()

    assert coordinator._client is None
    assert not coordinator.available


async def test_session_lost_marks_unavailable_and_schedules_recovery(rig, caplog):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    await live(rig)
    client = rig.clients[0]
    coordinator.present = True
    listener = Mock()
    coordinator.async_add_listener(listener)

    client.last_error = OSError("synthetic link loss")
    with (
        patch.object(coordinator, "_async_recover", new=AsyncMock()) as recover,
        caplog.at_level("DEBUG", logger="custom_components.lumalou.coordinator"),
    ):
        client.lost(client)
        assert "session ended: OSError: synthetic link loss" in caplog.text
        assert not coordinator.available
        assert coordinator.data is None
        listener.assert_called()
        assert len(rig.background_tasks) == 1
        await rig.background_tasks[0]
        recover.assert_awaited_once_with()

    # A later callback from a replaced session is ignored.
    await live(rig)
    assert coordinator.available
    client.lost(client)
    client.on_state(dict(rig.state))
    assert coordinator.available
    assert coordinator._client is rig.clients[-1]
    await coordinator.async_shutdown()


async def test_advertisements_notify_only_on_presence_or_firmware_change(rig):
    coordinator = rig.coordinator
    listener = Mock()
    coordinator.async_add_listener(listener)
    advertisement = SimpleNamespace(manufacturer_data={0x03B6: b"MB\x01\x000.3.7\x00"})

    coordinator._async_handle_advertisement(advertisement, None)
    assert listener.call_count == 1
    coordinator._async_handle_advertisement(advertisement, None)
    coordinator._async_handle_advertisement(SimpleNamespace(manufacturer_data={}), None)
    assert listener.call_count == 1
    coordinator._async_handle_advertisement(
        SimpleNamespace(manufacturer_data={0x03B6: b"MB\x01\x000.3.8\x00"}), None
    )
    assert listener.call_count == 2
    assert coordinator.sw_version == "0.3.8"
    await asyncio.gather(*rig.background_tasks)


async def test_advertisement_records_passive_firmware_version(rig):
    coordinator = rig.coordinator

    coordinator._async_handle_advertisement(
        SimpleNamespace(manufacturer_data={0x03B6: b"MB\x01\x000.3.7\x00"}), None
    )
    assert coordinator.sw_version == "0.3.7"
    for payload in (b"MB\x01\x00bad version", b"XX", b"MB\x01\x00"):
        coordinator._async_handle_advertisement(
            SimpleNamespace(manufacturer_data={0x03B6: payload}), None
        )
    coordinator._async_handle_advertisement(SimpleNamespace(manufacturer_data={}), None)
    assert coordinator.sw_version == "0.3.7"


# ---- Live controls and session lifecycle ----


async def test_missing_saved_profile_locks_controls(rig):
    """Without a usable saved profile, a device read must be confirmed again."""
    await rig.coordinator.async_setup()

    assert not rig.coordinator.protocol_verified
    rig.hass.config_entries.async_update_entry.assert_called_once()
    with pytest.raises(HomeAssistantError, match="Read and verify"):
        await rig.coordinator.async_set_level("volume", 3)
    rig.client_factory.assert_not_called()
    rig.store.async_save.assert_not_awaited()


async def test_user_state_errors_are_service_validation_errors(rig):
    coordinator = rig.coordinator
    coordinator.protocol_verified = False
    with pytest.raises(ServiceValidationError) as locked:
        await coordinator.async_set_level("volume", 3)
    assert locked.value.translation_key == "control_locked"

    coordinator.protocol_verified = True
    await coordinator.async_set_maintenance(True)
    with pytest.raises(ServiceValidationError) as maintenance:
        await coordinator.async_play(1)
    assert maintenance.value.translation_key == "maintenance_mode"
    await coordinator.async_set_maintenance(False)

    with (
        patch(
            "custom_components.lumalou.coordinator.dt_util.now",
            return_value=datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC")),
        ),
        pytest.raises(ServiceValidationError) as clock,
    ):
        await coordinator.async_sync_clock()
    assert clock.value.translation_key == "clock_untrusted"
    rig.client_factory.assert_not_called()


async def test_setup_propagates_unexpected_store_errors(rig):
    rig.store.async_load.side_effect = HomeAssistantError("Synthetic unexpected error")

    with pytest.raises(HomeAssistantError, match="Synthetic unexpected error"):
        await rig.coordinator.async_setup()

    rig.store.async_save.assert_not_awaited()


async def test_fresh_callback_is_observation_not_desired_profile(rig):
    coordinator = rig.coordinator
    await coordinator.async_setup()
    await live(rig)
    assert coordinator.available
    assert coordinator.data == rig.state
    assert coordinator.profile_record.desired_profile == {}
    assert coordinator.profile_record.verified_revision is None
    rig.discovery.assert_called_once_with(
        coordinator.hass, rig.device.address, connectable=True
    )
    rig.clients[0].send.assert_not_awaited()
    assert not [item for item in rig.journal if item[0] == "request_named"]


async def test_unsolicited_valid_state_updates_entities_not_saved_intent(rig):
    """E.g. the remote's buttons: pushed, shown, never saved or restored."""
    coordinator = rig.coordinator
    await coordinator.async_setup()
    await live(rig)
    listener = Mock()
    coordinator.async_add_listener(listener)
    changed = {**rig.state, "currentVolume": 8}

    rig.clients[0].on_state(changed)

    assert coordinator.data == changed
    assert coordinator.available
    assert coordinator.profile_record.desired_profile == {}
    listener.assert_called_once_with()
    rig.store.async_save.assert_not_awaited()


@pytest.mark.parametrize("invalid", [None, {}, {"unknown": 1}])
def test_wrong_callback_schema_ignored(rig, invalid):
    coordinator = rig.coordinator
    coordinator._receive(coordinator._generation, invalid)
    assert coordinator.data is None
    assert not coordinator.available


@pytest.mark.parametrize("value", [True, "1", -1, 256, 19])
def test_invalid_decoded_callback_values_ignored(rig, value):
    rig.state["currentSong"] = value
    rig.coordinator._receive(rig.coordinator._generation, rig.state)
    assert rig.coordinator.data is None


async def test_live_settings_are_kept_in_the_saved_profile_once_confirmed(rig):
    """Volume, brightness and timers come back after a power loss; colour not."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    record = coordinator.profile_record
    clients = len(rig.clients)
    start = len(rig.journal)

    await coordinator.async_set_level("volume", 7)
    await coordinator.async_turn_on_light(brightness=6, color=9)
    await coordinator.async_set_level("light_duration", 5)
    await coordinator.async_set_level("playlist_duration", 6)

    assert sends(rig, start) == [
        bytes([0x37, 7]),
        bytes([0x3A, 6]),
        bytes([0x3C, 9]),
        bytes([0x6C, 5]),
        bytes([0x42, 6]),
    ]
    # One live session: no reconnect and no read after the commands.
    assert len(rig.clients) == clients
    assert not [item for item in rig.journal[start:] if item[0].startswith("request")]
    saved = coordinator.profile_record
    assert saved.desired_profile["light_and_sound"] == {
        "volume": 7,
        "light_brightness": 6,
        "light_duration": 5,
        "playlist_duration": 6,
    }
    # Same revision, still verified: restore state and editors are unaffected.
    assert saved.revision == record.revision
    assert saved.is_verified
    assert {
        key: value
        for key, value in saved.desired_profile.items()
        if key != "light_and_sound"
    } == {
        key: value
        for key, value in record.desired_profile.items()
        if key != "light_and_sound"
    }


async def test_unconfirmed_setting_is_sent_but_not_saved(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    record = coordinator.profile_record
    rig.settings.push = False

    await coordinator.async_set_level("volume", 8)

    assert sends(rig)[-1] == bytes([0x37, 8])
    assert coordinator.profile_record == record


async def test_setting_without_a_saved_profile_is_only_sent(rig):
    coordinator = rig.coordinator
    await live(rig)

    await coordinator.async_set_level("volume", 8)

    assert sends(rig) == [bytes([0x37, 8])]
    rig.store.async_save.assert_not_awaited()


async def test_concurrent_live_commands_are_serialized(rig):
    coordinator = rig.coordinator
    await live(rig)
    client = rig.clients[0]
    rig.settings.push = False
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    original = client.send.side_effect

    async def delayed_send(payload, **kwargs):
        if payload == bytes([0x37, 3]):
            send_started.set()
            await release_send.wait()
        await original(payload, **kwargs)

    client.send.side_effect = delayed_send
    first = asyncio.create_task(coordinator.async_set_level("volume", 3))
    await send_started.wait()
    second = asyncio.create_task(coordinator.async_set_level("volume", 4))
    await asyncio.sleep(0)
    assert sends(rig) == []
    release_send.set()
    await asyncio.gather(first, second)
    assert sends(rig) == [bytes([0x37, 3]), bytes([0x37, 4])]


async def test_command_without_a_live_session_never_connects(rig):
    """Only recovery opens sessions, so every session starts with its checks."""
    with pytest.raises(HomeAssistantError, match="will not be replayed") as caught:
        await rig.coordinator.async_set_level("volume", 4)

    assert caught.value.translation_key == "command_failed"
    rig.client_factory.assert_not_called()
    rig.store.async_save.assert_not_awaited()


async def test_failed_live_command_raises_and_cleans_up(rig):
    """A failed live change is reported to the caller, never swallowed."""
    coordinator = rig.coordinator
    await live(rig)
    rig.settings.fail_opcode = 0x37

    with pytest.raises(HomeAssistantError, match="will not be replayed") as caught:
        await coordinator.async_set_level("volume", 4)

    assert caught.value.translation_key == "command_failed"
    assert coordinator._client is None
    assert not coordinator.available
    rig.clients[0].disconnect.assert_awaited_once()
    rig.store.async_save.assert_not_awaited()


async def test_disconnect_failure_does_not_mask_recovery_error(rig):
    coordinator = rig.coordinator
    await live(rig)
    client = rig.clients[0]
    client.disconnect.side_effect = OSError("Synthetic teardown failure")
    rig.settings.mode = "error"

    with pytest.raises(HomeAssistantError, match="recovery failed"):
        await live(rig)

    assert coordinator._client is None
    assert not coordinator.available
    assert coordinator.data is None


async def test_light_on_writes_brightness_before_the_colour(rig):
    """Hardware: colour switches the light on at the stored brightness."""
    coordinator = rig.coordinator
    await live(rig)
    rig.state.update(lightStatus=0, lightBrightness=3, lightColor=4)
    rig.clients[0].on_state(dict(rig.state))

    await coordinator.async_turn_on_light(brightness=7, color=9)
    assert sends(rig) == [bytes([0x3A, 7]), bytes([0x3C, 9])]
    assert coordinator.data["lightStatus"] == 1

    await coordinator.async_turn_off_light()
    assert sends(rig)[-1] == bytes([0x3E])
    # The device keeps brightness and colour while off.
    assert coordinator.data["lightStatus"] == 0
    assert coordinator.data["lightBrightness"] == 7


async def test_plain_light_on_uses_the_current_colour(rig):
    """E.g. Apple Home: "on" means the last colour at the stored brightness."""
    coordinator = rig.coordinator
    await live(rig)
    rig.state.update(lightStatus=0, lightBrightness=3, lightColor=8)
    rig.clients[0].on_state(dict(rig.state))

    await coordinator.async_turn_on_light()
    assert sends(rig) == [bytes([0x3C, 8])]

    await coordinator.async_turn_off_light()
    await coordinator.async_turn_on_light(brightness=2)
    assert sends(rig)[-2:] == [bytes([0x3A, 2]), bytes([0x3C, 8])]


async def test_brightness_change_of_a_lit_light_sends_only_brightness(rig):
    coordinator = rig.coordinator
    await live(rig)
    rig.state.update(lightStatus=1, lightBrightness=3, lightColor=8)
    rig.clients[0].on_state(dict(rig.state))

    await coordinator.async_turn_on_light(brightness=5)

    assert sends(rig) == [bytes([0x3A, 5])]


async def test_transient_play_stop_off_never_persist_or_retry(rig):
    coordinator = rig.coordinator
    await live(rig)
    await coordinator.async_play(7)
    await coordinator.async_stop_audio()
    assert sends(rig) == [bytes([0x3F, 7]), bytes([0x38])]
    rig.store.async_save.assert_not_awaited()
    current = coordinator._client
    current.send.side_effect = OSError("Ambiguous write")
    with pytest.raises(HomeAssistantError, match="will not be replayed"):
        await coordinator.async_play(1)
    assert current.send.await_count == 3
    await live(rig)
    assert coordinator._client is not current
    coordinator._client.send.assert_not_awaited()
    assert sends(rig) == [bytes([0x3F, 7]), bytes([0x38])]
    assert coordinator.profile_record.revision == 0


async def test_soother_stop_leaves_its_light_on(rig):
    """Hardware: source 0 is music plus light; stopping audio keeps the light."""
    coordinator = rig.coordinator
    await live(rig)

    await coordinator.async_play(0)
    assert coordinator.data["musicStatus"] == 1
    assert coordinator.data["lightStatus"] == 1

    await coordinator.async_stop_audio()
    assert coordinator.data["musicStatus"] == 0
    assert coordinator.data["lightStatus"] == 1
    assert sends(rig) == [bytes([0x3F, 0]), bytes([0x38])]


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("async_turn_on_light", {"brightness": 0}),
        ("async_turn_on_light", {"brightness": 10}),
        ("async_turn_on_light", {"color": 10}),
        ("async_set_level", {"key": "volume", "value": True}),
        ("async_set_level", {"key": "volume", "value": 10}),
        ("async_set_level", {"key": "light_duration", "value": 6}),
        ("async_set_level", {"key": "playlist_duration", "value": 7}),
        ("async_set_clock_settings", {"display": 1}),
        ("async_set_clock_settings", {"brightness": 10}),
        ("async_set_clock_settings", {"clock_format": 2}),
        ("async_play", {"source": 8}),
        ("async_play", {"source": True}),
        ("async_set_maintenance", {"enabled": 1}),
        ("async_restore_profile", {"expected_revision": 0}),
    ],
)
async def test_method_validation_before_storage_or_ble(rig, method, arguments):
    with pytest.raises(ProfileValidationError):
        await getattr(rig.coordinator, method)(**arguments)
    rig.store.async_save.assert_not_awaited()
    rig.discovery.assert_not_called()


async def test_restore_rejects_non_integer_revision_before_ble(rig):
    with pytest.raises(ProfileValidationError):
        await rig.coordinator.async_restore_profile(True, confirmed=True)
    rig.discovery.assert_not_called()


async def test_maintenance_releases_ble_and_blocks_device_commands(rig):
    coordinator = rig.coordinator
    await live(rig)
    await coordinator.async_set_maintenance(True)
    assert coordinator.profile_record.maintenance
    assert not coordinator.available
    rig.clients[0].disconnect.assert_awaited_once()
    with pytest.raises(HomeAssistantError, match="maintenance"):
        await coordinator.async_set_level("volume", 8)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_play(1)
    assert len(rig.clients) == 1
    await coordinator.async_set_maintenance(False)
    assert not coordinator.profile_record.maintenance
    assert len(rig.clients) == 1  # no automatic reconnect or restore
    rig.clients[0].send.assert_not_awaited()


async def test_maintenance_does_not_wait_for_an_in_flight_recovery(rig):
    """Hardware: maintenance stayed off while recovery held the lock."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    await coordinator._disconnect()
    original = rig.client_factory.side_effect
    connecting = asyncio.Event()

    def hanging_connect(*args, **kwargs):
        client = original(*args, **kwargs)

        async def connect(**_kwargs):
            connecting.set()
            await asyncio.Event().wait()  # a connect budget that never ends

        client.connect.side_effect = connect
        return client

    rig.client_factory.side_effect = hanging_connect
    coordinator.present = True
    coordinator._schedule_recovery()
    recovery = coordinator._recovery_task
    assert recovery is not None
    await asyncio.wait_for(connecting.wait(), 1)

    await asyncio.wait_for(coordinator.async_set_maintenance(True), 1)

    assert coordinator.profile_record.maintenance
    assert recovery.cancelled()
    assert coordinator._recovery_task is None
    assert coordinator._client is None
    rig.clients[-1].disconnect.assert_awaited()


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
    saved = coordinator.profile_record
    rig.address_present.return_value = True
    coordinator.async_start()
    assert coordinator.present
    assert not rig.background_tasks

    await coordinator.async_set_maintenance(False)
    assert len(rig.background_tasks) == 1
    await rig.background_tasks[0]

    assert coordinator.available
    assert coordinator.profile_record == replace(saved, maintenance=False)
    assert coordinator.restore_needed is None
    rig.clients[0].request_state.assert_awaited_once()
    rig.clients[0].send.assert_not_awaited()
    await coordinator.async_shutdown()


async def test_leaving_maintenance_resets_old_backoff_without_replaying(rig):
    coordinator = rig.coordinator
    coordinator.async_start()
    coordinator._next_recovery_at = asyncio.get_running_loop().time() + 3600
    coordinator._recovery_failures = 6
    coordinator._async_handle_advertisement(Mock(), Mock())
    await asyncio.sleep(0)
    delayed_recovery = coordinator._recovery_task
    assert delayed_recovery is not None

    await coordinator.async_set_maintenance(True)
    await asyncio.sleep(0)
    assert delayed_recovery.cancelled()
    assert coordinator._recovery_task is None
    await coordinator.async_set_maintenance(False)

    immediate_recovery = coordinator._recovery_task
    assert immediate_recovery is not None
    assert immediate_recovery is not delayed_recovery
    assert coordinator._recovery_failures == 0
    assert coordinator._next_recovery_at == 0
    await immediate_recovery
    assert coordinator.available
    rig.clients[0].send.assert_not_awaited()
    await coordinator.async_shutdown()


async def test_entries_do_not_share_saved_intent(rig):
    other_store = SimpleNamespace(
        async_load=AsyncMock(
            return_value=ProfileRecord(revision=10, desired_profile={"playlist": [9]})
        ),
        async_save=AsyncMock(),
    )
    other_entry = SimpleNamespace(
        data={"address": "synthetic-other", CONF_DEVICE_FINGERPRINT: "b" * 64},
        title="Other",
        entry_id="other",
    )
    other = LumalouCoordinator(rig.coordinator.hass, other_entry, other_store)
    await other.async_setup()
    await rig.coordinator.async_set_maintenance(True)
    rig.store.async_save.assert_awaited_once()
    assert other.profile_record.revision == 10
    assert other.profile_record.desired_profile == {"playlist": [9]}
    other_store.async_save.assert_not_awaited()


async def test_clock_uses_supplied_ha_timezone_and_never_persists(rig):
    await live(rig)
    now = datetime(2026, 9, 20, 0, 1, 2, tzinfo=ZONE)
    with patch("custom_components.lumalou.coordinator.dt_util.now", return_value=now):
        await rig.coordinator.async_sync_clock()
    # Sunday=0, local midnight is valid; no stored calendar timestamp.
    assert ("send", bytes([0x30, 0, 1, 2, 0])) in rig.journal
    assert rig.coordinator.last_clock_sync == now
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


async def test_pushed_clock_drift_is_corrected_in_the_live_session(rig):
    """The device pushes CURRENT_DATE every minute: DST and drift, no reconnect."""
    coordinator = rig.coordinator
    await live(rig)
    client = rig.clients[0]

    push_clock(client, CurrentDate(11, 0, 0, 0))  # e.g. after a DST change
    push_clock(client, CurrentDate(11, 0, 0, 0))  # one correction at a time
    await asyncio.gather(*rig.background_tasks)

    assert sends(rig) == [bytes([0x30, 0x12, 0, 0, 0])]
    assert coordinator.last_clock_sync == NOW
    assert coordinator.available
    assert len(rig.clients) == 1
    rig.store.async_save.assert_not_awaited()

    # At most one automatic correction per hour.
    push_clock(client, CurrentDate(11, 0, 0, 0))
    await asyncio.gather(*rig.background_tasks)
    assert len(sends(rig)) == 1


async def test_pushed_clock_within_tolerance_or_other_frames_are_ignored(rig):
    coordinator = rig.coordinator
    await live(rig)
    client = rig.clients[0]

    push_clock(client, CurrentDate(12, 0, 59, 0))
    client.on_response(SimpleNamespace(opcode=0x18, decode=Mock()))
    client.on_response(SimpleNamespace(opcode=0x13, decode=lambda: None))

    assert not rig.background_tasks
    assert not sends(rig)
    assert coordinator.available


async def test_pushed_clock_is_not_corrected_for_locked_or_stale_sessions(rig):
    coordinator = rig.coordinator
    await live(rig)
    client = rig.clients[0]
    coordinator.protocol_verified = False
    push_clock(client, CurrentDate(0, 0, 0, 0))
    assert not rig.background_tasks

    coordinator.protocol_verified = True
    push_clock(client, CurrentDate(0, 0, 0, 0))
    # The session is replaced before the correction runs.
    await live(rig)
    await asyncio.gather(*rig.background_tasks)
    assert not sends(rig)

    # A frame of an old session is ignored.
    push_clock(client, CurrentDate(0, 0, 0, 0))
    assert len(rig.background_tasks) == 1


async def test_queued_clock_correction_skips_after_maintenance(rig):
    coordinator = rig.coordinator
    await live(rig)
    push_clock(rig.clients[0], CurrentDate(0, 0, 0, 0))
    await coordinator.async_set_maintenance(True)

    results = await asyncio.gather(*rig.background_tasks, return_exceptions=True)

    # Maintenance cancels the queued automatic correction.
    assert any(isinstance(result, asyncio.CancelledError) for result in results)
    assert not sends(rig)


async def test_failed_pushed_clock_correction_only_drops_the_session(rig):
    coordinator = rig.coordinator
    await live(rig)
    rig.settings.fail_opcode = 0x30

    push_clock(rig.clients[0], CurrentDate(0, 0, 0, 0))
    await asyncio.gather(*rig.background_tasks)

    assert not sends(rig)
    assert coordinator.clock_sync_paused
    assert coordinator.last_clock_sync is None


async def test_recovery_never_syncs_from_untrusted_host_time(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    rig.fake.clock = CurrentDate(0, 0, 0, 0)
    start = len(rig.journal)
    with patch(
        "custom_components.lumalou.coordinator.dt_util.now",
        return_value=datetime(2000, 1, 2, tzinfo=ZoneInfo("UTC")),
    ):
        await coordinator._async_recover()
    assert not sends(rig, start)


async def test_import_export_confirmation_conflicts_and_no_restore(rig):
    coordinator = rig.coordinator
    payload = {
        "schema_version": 3,
        "scope": "persistent_profile",
        "profile": {**complete_profile(), "playlist": [12, 2, 2]},
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
    assert coordinator.profile_record.desired_profile["playlist"] == [12, 2, 2]
    rig.discovery.assert_not_called()


async def test_partial_import_never_replaces_a_complete_profile(rig):
    coordinator = rig.coordinator
    rig.store.async_load.return_value = ProfileRecord(
        revision=3, desired_profile=complete_profile()
    )
    await coordinator.async_setup()
    partial = {
        "schema_version": 3,
        "scope": "persistent_profile",
        "profile": {"playlist": [1]},
    }

    with pytest.raises(ProfileValidationError, match="incomplete"):
        await coordinator.async_import_profile(partial, 3, confirmed=True)

    assert coordinator.profile_record.desired_profile == complete_profile()
    rig.store.async_save.assert_not_awaited()


async def test_export_revision_and_profile_are_one_serialized_snapshot(rig):
    coordinator = rig.coordinator
    rig.store.async_load.return_value = ProfileRecord(
        revision=1, desired_profile=complete_profile()
    )
    await coordinator.async_setup()
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def blocked_save(_record):
        save_started.set()
        await release_save.wait()

    rig.store.async_save.side_effect = blocked_save
    edit = asyncio.create_task(
        coordinator.async_edit_profile({"playlist": [6]}, expected_revision=1)
    )
    await save_started.wait()
    export = asyncio.create_task(coordinator.async_export_profile())
    await asyncio.sleep(0)
    assert not export.done()
    release_save.set()
    await edit

    exported = await export
    assert exported["current_revision"] == 2
    assert exported["profile"]["profile"]["playlist"] == [6]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"schema_version": 2, "scope": "supported_subset", "profile": {}},
        {"schema_version": True, "scope": "supported_subset", "profile": {}},
        {"schema_version": 1, "scope": "supported_subset", "profile": {}},
        {"schema_version": 2, "scope": "persistent_profile", "profile": {"raw": 1}},
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
    assert rig.register_callback.call_args.kwargs["replay"].name == "NEWEST_FIRST"
    assert rig.register_callback.call_args.args[2] == {
        "address": rig.device.address,
        "connectable": True,
    }
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_recovery():
        started.set()
        await release.wait()

    with patch.object(
        coordinator, "_async_recover", side_effect=blocked_recovery
    ) as recover:
        advertisement(Mock(), Mock())
        advertisement(Mock(), Mock())
        advertisement(Mock(), Mock())
        await started.wait()
        assert coordinator.present
        assert not coordinator.available
        assert len(rig.background_tasks) == 1
        recover.assert_awaited_once_with()
        release.set()
        await rig.background_tasks[0]

    await coordinator.async_shutdown()


async def test_start_replays_cached_presence_without_gatt_in_maintenance(rig):
    """A cached HA advertisement restores presence without taking Bluetooth."""
    coordinator = rig.coordinator
    rig.store.async_load.return_value = ProfileRecord(maintenance=True)
    await coordinator.async_setup()

    def register_with_replay(_hass, callback, _matcher, _mode, **_kwargs):
        callback(Mock(), Mock())
        return Mock()

    rig.register_callback.side_effect = register_with_replay
    coordinator.async_start()

    assert coordinator.present is True
    assert rig.register_callback.call_args.kwargs["replay"].name == "NEWEST_FIRST"
    rig.discovery.assert_not_called()
    rig.client_factory.assert_not_called()
    assert not rig.background_tasks


async def test_background_refresh_uses_bounded_exponential_backoff(rig):
    """Persistent connect failures retry without advertisement-driven hammering."""
    coordinator = rig.coordinator
    coordinator.present = True
    clock = SimpleNamespace(time=Mock(return_value=100.0))
    failures = [HomeAssistantError("Synthetic refresh failure") for _ in range(7)]

    with (
        patch.object(
            coordinator,
            "_async_recover",
            new=AsyncMock(side_effect=[*failures, None]),
        ) as recover,
        patch(
            "custom_components.lumalou.coordinator.asyncio.get_running_loop",
            return_value=clock,
        ),
        patch(
            "custom_components.lumalou.coordinator.asyncio.sleep", new=AsyncMock()
        ) as sleep,
    ):
        await coordinator._async_background_refresh()

    assert recover.await_count == 8
    assert [call.args[0] for call in sleep.await_args_list] == [
        30,
        60,
        120,
        240,
        480,
        900,
        900,
    ]
    assert coordinator._recovery_failures == 0
    assert coordinator._next_recovery_at == 130


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
    await live(rig)
    old_client = rig.clients[0]
    rig.address_present.return_value = True
    coordinator.async_start()
    unavailable = rig.track_unavailable.call_args.args[1]

    unavailable(Mock())

    assert not coordinator.present
    assert not coordinator.available
    assert coordinator.data is None
    assert coordinator._client is None
    old_client.on_state(rig.state)
    assert coordinator.data is None
    await asyncio.gather(*list(coordinator._background_tasks))
    old_client.disconnect.assert_awaited_once_with()
    await coordinator.async_shutdown()


async def test_unavailable_cleanup_cannot_close_reconnected_session(rig):
    coordinator = rig.coordinator
    await live(rig)
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
    await coordinator.async_setup()
    await live(rig)
    started = asyncio.Event()
    original = rig.client_factory.side_effect

    def hanging_read(*args, **kwargs):
        client = original(*args, **kwargs)

        async def hang(**kwargs):
            started.set()
            await asyncio.Event().wait()

        client.request_state.side_effect = hang
        return client

    rig.client_factory.side_effect = hanging_read
    listener = Mock()
    coordinator.async_add_listener(listener)
    task = asyncio.create_task(live(rig))
    await started.wait()
    client = rig.clients[-1]
    await coordinator.async_shutdown()
    assert task.cancelled()
    assert not coordinator._tasks
    assert not coordinator._listeners
    assert coordinator._client is None
    assert not coordinator.available
    client.on_state(rig.state)
    assert coordinator.data is None
    with pytest.raises(HomeAssistantError, match="unloaded"):
        await live(rig)


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


async def test_upstream_connect_receives_current_ble_device_and_only_main_gatt():
    """Real coordinator -> SafeLumalouClient -> HA adapter -> mocked backend."""
    device = BLEDevice("synthetic-device", "Test", {})
    _, public_key = crypto.generate_keypair()
    backend = SimpleNamespace(
        disconnect=AsyncMock(),
        start_notify=AsyncMock(),
        read_gatt_char=AsyncMock(return_value=bytes(25) + public_key + bytes(134)),
        write_gatt_char=AsyncMock(),
    )
    entry = SimpleNamespace(
        data={"address": device.address, CONF_DEVICE_FINGERPRINT: FINGERPRINT},
        options={},
        title="Test",
        entry_id="synthetic",
    )
    coordinator = LumalouCoordinator(SimpleNamespace(), entry, Mock())
    with (
        patch(
            "custom_components.lumalou.coordinator.bluetooth.async_ble_device_from_address",
            return_value=device,
        ),
        patch(
            "custom_components.lumalou.transport.establish_connection",
            new=AsyncMock(return_value=backend),
        ) as establish,
        patch("lumalou.client.BleakClient") as raw_bleak,
        patch("lumalou.client.BleakScanner.discover", new_callable=AsyncMock) as scan,
        patch(
            "lumalou.client.parse_factory_device_fingerprint", return_value=FINGERPRINT
        ),
    ):
        client = await coordinator._async_new_session()
        assert establish.await_args.args[1] is device
        raw_bleak.assert_not_called()
        scan.assert_not_awaited()
        assert client.connected
        written = [call.args[0] for call in backend.write_gatt_char.await_args_list]
        assert written == [
            "4cea0005-c678-4202-b5d3-712dbb5e5b14",
            "4cea0002-c678-4202-b5d3-712dbb5e5b14",
        ]
        assert len(backend.write_gatt_char.await_args_list[0].args[1]) == 37
        backend.start_notify.assert_awaited_once()
        await coordinator._disconnect()
        backend.disconnect.assert_awaited_once()


async def test_unavailability_and_return_are_logged_once(rig, caplog):
    """Device loss and recovery are logged at info level exactly once each."""
    coordinator = rig.coordinator
    await coordinator.async_setup()
    caplog.set_level("INFO", logger="custom_components.lumalou.coordinator")

    coordinator._async_handle_unavailable(Mock())
    coordinator._async_handle_unavailable(Mock())
    coordinator._receive(coordinator._generation, dict(rig.state))
    coordinator._receive(coordinator._generation, dict(rig.state))

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Test Lumalou is unavailable") == 1
    assert messages.count("Test Lumalou is available again") == 1


async def test_connect_failures_are_logged_once_until_available(rig, caplog):
    """The first failed connect logs type and message; repeats go to debug."""
    coordinator = rig.coordinator
    await coordinator.async_setup()
    original = rig.client_factory.side_effect

    def fail_connect(*args, **kwargs):
        client = original(*args, **kwargs)
        client.connect.side_effect = OSError("Synthetic connect error")
        return client

    rig.client_factory.side_effect = fail_connect
    caplog.set_level("DEBUG", logger="custom_components.lumalou.coordinator")
    for _ in range(2):
        with pytest.raises(HomeAssistantError):
            await live(rig)

    records = [
        record
        for record in caplog.records
        if record.name == "custom_components.lumalou.coordinator"
    ]
    info = [record for record in records if record.levelname == "INFO"]
    assert [record.getMessage() for record in info] == [
        "Test Lumalou is unavailable: could not connect: OSError: "
        "Synthetic connect error"
    ]
    assert info[0].exc_info is None
    debug = [record for record in records if record.exc_info]
    assert len(debug) == 2
    assert all(record.levelname == "DEBUG" for record in debug)
    assert FINGERPRINT not in caplog.text

    # A successful session logs the return once and re-arms the first log.
    rig.client_factory.side_effect = original
    caplog.clear()
    await live(rig)
    rig.client_factory.side_effect = fail_connect
    with pytest.raises(HomeAssistantError):
        await live(rig)
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Test Lumalou is available again") == 1
    assert (
        messages.count(
            "Test Lumalou is unavailable: could not connect: OSError: "
            "Synthetic connect error"
        )
        == 1
    )


async def test_session_connect_uses_the_connector_retry_budget(rig):
    """The coordinator never passes a deadline shorter than the retries."""
    await live(rig)

    rig.clients[0].connect.assert_awaited_once_with(timeout=CONNECT_TIMEOUT)
    assert CONNECT_TIMEOUT > MAX_CONNECT_ATTEMPTS * BLEAK_SAFETY_TIMEOUT


async def test_device_write_invalidates_a_pending_preview(rig):
    """A stale preview cannot be saved after the device was written meanwhile."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    snapshot, revision = await coordinator.async_read_profile_snapshot()

    await coordinator.async_set_level("volume", 7)

    with pytest.raises(HomeAssistantError, match="Read the device profile again"):
        await coordinator.async_accept_device_profile(
            snapshot, coordinator.profile_record.revision, confirmed=True
        )
    assert revision == 1


async def test_clock_entities_write_one_field_and_keep_it_in_the_profile(rig):
    """Owner needs 24 h: format 1; the other fields come from device state."""
    coordinator = rig.coordinator
    rig.state.update(clockDisplay=1, clockBrightness=2, clockFormat=0)
    await verified_profile(rig)
    revision = coordinator.profile_record.revision
    start = len(rig.journal)

    await coordinator.async_set_clock_settings(clock_format=1)
    await coordinator.async_set_clock_settings(brightness=9)
    await coordinator.async_set_clock_settings(display=False)

    assert sends(rig, start) == [
        bytes([0x79, 1, 0x21]),
        bytes([0x79, 1, 0x91]),
        bytes([0x79, 0, 0x91]),
    ]
    record = coordinator.profile_record
    assert record.desired_profile["clock_settings"] == {
        "display": False,
        "brightness": 9,
        "format": 1,
    }
    assert record.revision == revision
    assert record.is_verified

    # Writing the saved value again changes nothing.
    saves = rig.store.async_save.await_count
    await coordinator.async_set_clock_settings(display=False)
    assert rig.store.async_save.await_count == saves


async def test_commands_need_a_pushed_state(rig):
    coordinator = rig.coordinator
    await live(rig)
    coordinator.data = None

    with pytest.raises(HomeAssistantError, match="will not be replayed"):
        await coordinator.async_turn_on_light()
    with pytest.raises(HomeAssistantError, match="will not be replayed"):
        await coordinator.async_set_clock_settings(display=True)
    assert not sends(rig)


async def test_reconnect_waits_for_the_hardware_gap(rig):
    """Hardware: an immediate reconnect after a disconnect sometimes fails."""
    coordinator = rig.coordinator
    await coordinator.async_setup()
    await live(rig)
    with (
        patch("custom_components.lumalou.coordinator.RECONNECT_DELAY", 1.5),
        patch(
            "custom_components.lumalou.coordinator.asyncio.sleep", new=AsyncMock()
        ) as sleep,
    ):
        await coordinator._disconnect()
        await live(rig)

    (delay,) = [call.args[0] for call in sleep.await_args_list]
    assert 0 < delay <= 1.5
    assert coordinator.available


@pytest.mark.parametrize("reason", ["maintenance", "absent", "no_device"])
async def test_new_sessions_need_presence_and_no_maintenance(rig, reason):
    coordinator = rig.coordinator
    if reason == "maintenance":
        await coordinator.async_set_maintenance(True)
    elif reason == "absent":
        coordinator.async_start()
    else:
        rig.discovery.return_value = None

    with pytest.raises(HomeAssistantError, match="Could not read a complete"):
        await coordinator.async_read_profile_snapshot()

    rig.client_factory.assert_not_called()
    coordinator.async_stop_callbacks()


async def test_profile_read_uses_a_pushed_clock_frame_of_the_same_session(rig):
    """A minute push can arrive before the current-date request of a read."""
    coordinator = rig.coordinator
    original = rig.client_factory.side_effect
    pushed = {"frame": True}

    def early_push(*args, **kwargs):
        client = original(*args, **kwargs)
        request = client.request_named.side_effect

        async def connect(**_kwargs):
            client.connected = True
            if pushed["frame"]:
                push_clock(client, CurrentDate(12, 0, 30, 0))

        async def request_named(name, **kwargs):
            if name == "current_date":
                raise FreshSessionRequiredError("pushed first")
            return await request(name, **kwargs)

        client.connect.side_effect = connect
        client.request_named.side_effect = request_named
        return client

    rig.client_factory.side_effect = early_push

    await coordinator.async_read_profile_snapshot()
    assert coordinator.last_clock_offset is None  # a read never syncs

    pushed["frame"] = False
    with pytest.raises(HomeAssistantError, match="Could not read a complete"):
        await coordinator.async_read_profile_snapshot()


async def test_exhausted_flag_is_set_once_the_attempts_are_used(rig):
    """E.g. a final attempt interrupted by an unload never marked itself."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    coordinator.entry.options = {"auto_restore": False}
    rig.state.update(currentVolume=1)
    rig.fake.clock = CurrentDate(5, 0, 0, 0)
    await coordinator._async_recover()
    coordinator._restore_needed = replace(
        coordinator.restore_needed, auto_restore_attempts=2
    )
    coordinator.entry.options = {}
    start = len(rig.journal)

    await coordinator._async_recover()

    assert coordinator.restore_needed.auto_restore_exhausted
    assert coordinator.repair_needed is coordinator.restore_needed
    assert not sends(rig, start)
