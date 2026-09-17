"""Config flow tests without real Bluetooth access."""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    FlowType,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.config_flow import CONF_AUTO_RESTORE
from custom_components.lumalou.const import (
    CONF_PRODUCT_CODE,
    DOMAIN,
    SUPPORTED_PRODUCT_CODE,
)
from custom_components.lumalou.models import (
    DAYS,
    LumalouRuntimeData,
    ProfileRecord,
    RevisionConflictError,
    export_profile_payload,
)

ADDRESS = "AA:BB:CC:DD:EE:01"
pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def bypass_integration_dependency_setup() -> Generator[None]:
    """Load the flow without starting Home Assistant Bluetooth or USB."""
    with (
        patch(
            "homeassistant.config_entries.async_process_deps_reqs",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.setup.async_process_deps_reqs",
            new_callable=AsyncMock,
        ),
    ):
        yield


def service_info(
    *, connectable: bool = True, manufacturer_data: dict[int, bytes] | None = None
) -> BluetoothServiceInfoBleak:
    """Build synthetic discovery data."""
    device = BLEDevice(ADDRESS, "Lumalou test", {})
    advertisement = AdvertisementData(
        local_name="Lumalou test",
        manufacturer_data=manufacturer_data or {950: b"MB\x01"},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=-55,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak.from_device_and_advertisement_data(
        device, advertisement, "synthetic", time.monotonic(), connectable
    )


def editable_profile() -> dict:
    """Return profile blocks used by native schedule and routine forms."""
    empty_week = {day: None for day in DAYS}
    routines = {day: {"time": None, "slots": [None] * 12} for day in DAYS}
    routines["sunday"] = {
        "time": {"hour": 0, "minute": 0},
        "slots": [
            {"step": 2, "task": 0},
            {"step": 1, "task": 11},
            *([None] * 10),
        ],
    }
    return {
        "ready_to_rise": {"enabled": False, "times": deepcopy(empty_week)},
        "sleepy_times": deepcopy(empty_week),
        "alarm": {"days": {day: 9 for day in DAYS}, "sound": 0},
        "routines": routines,
    }


def profile_entry(
    hass: HomeAssistant,
    *,
    revision: int = 7,
    save: AsyncMock | None = None,
    desired_profile: dict[str, Any] | None = None,
) -> tuple[MockConfigEntry, SimpleNamespace]:
    """Add a loaded-looking entry backed by an isolated coordinator mock."""
    coordinator = SimpleNamespace(
        profile_record=ProfileRecord(
            revision=revision,
            desired_profile=desired_profile
            if desired_profile is not None
            else editable_profile(),
            sync_status="saved",
        ),
        async_edit_profile=save or AsyncMock(),
        async_import_profile=AsyncMock(),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = LumalouRuntimeData(coordinator)
    return entry, coordinator


async def start_editor(
    hass: HomeAssistant, entry: MockConfigEntry, section: str
) -> dict:
    """Start an options flow and select one native editor section."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    if section not in result["menu_options"] and "create" in result["menu_options"]:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "create"}
        )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": section}
    )


async def test_bluetooth_discovery_confirm(hass: HomeAssistant) -> None:
    """Discovery only confirms and creates an entry with restore disabled."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    with patch(
        "custom_components.lumalou.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PRODUCT_CODE: " gld09 "}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == ADDRESS
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_PRODUCT_CODE: SUPPORTED_PRODUCT_CODE,
    }
    assert result["options"] == {CONF_AUTO_RESTORE: False}
    setup.assert_awaited_once()


async def test_new_entry_opens_profile_source_options_flow(
    hass: HomeAssistant,
) -> None:
    """First-run profile selection begins only after HA has created the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )
    with patch("custom_components.lumalou.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_PRODUCT_CODE: SUPPORTED_PRODUCT_CODE},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["next_flow"][0] is FlowType.OPTIONS_FLOW
    options_flow_id = result["next_flow"][1]
    assert hass.config_entries.options.async_get(options_flow_id)
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_PRODUCT_CODE: SUPPORTED_PRODUCT_CODE,
    }
    assert result["options"] == {CONF_AUTO_RESTORE: False}


async def test_empty_profile_offers_source_choices_without_creating_defaults(
    hass: HomeAssistant,
) -> None:
    """An empty private Store offers Create, Import, and unavailable Read."""
    entry, coordinator = profile_entry(hass, desired_profile={})

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert {"create", "import_profile", "read_profile"} <= set(result["menu_options"])
    assert entry.data == {CONF_ADDRESS: ADDRESS}
    assert entry.options == {CONF_AUTO_RESTORE: False}
    coordinator.async_edit_profile.assert_not_awaited()


async def test_existing_profile_options_keeps_import_and_read_available(
    hass: HomeAssistant,
) -> None:
    """A normal Configure flow can replace a saved export or show Read status."""
    entry, _ = profile_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert {"import_profile", "read_profile"} <= set(result["menu_options"])


async def test_first_run_create_routes_to_offline_editor(hass: HomeAssistant) -> None:
    """Create only opens the normal private-profile editor tree."""
    entry, coordinator = profile_entry(hass, desired_profile={})
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "create"}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "create"
    assert "basic" in result["menu_options"]
    coordinator.async_edit_profile.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": 1,
            "scope": "supported_subset",
            "profile": {
                "brightness": 1,
                "color": 2,
                "light_duration": 3,
                "volume": 4,
                "playlist_duration": 5,
                "playlist": [1, 2],
            },
        },
        {
            "schema_version": 2,
            "scope": "persistent_profile",
            "profile": editable_profile(),
        },
    ],
)
async def test_first_run_import_previews_then_saves_with_cas(
    hass: HomeAssistant, payload: dict[str, Any]
) -> None:
    """Both exported schemas stay offline until an explicit CAS confirmation."""
    save = AsyncMock()
    entry, coordinator = profile_entry(hass, save=save, desired_profile={})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    original_options = dict(entry.options)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "import_profile"}
    )
    assert result["step_id"] == "import_profile"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"profile_json": json.dumps(payload)}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "import_profile_confirm"
    assert result["description_placeholders"] == {
        "schema_version": str(payload["schema_version"]),
        "scope": str(payload["scope"]),
        "field_count": str(len(payload["profile"])),
        "revision": "7",
        "removed_count": "0",
        "removed_fields": "—",
    }
    coordinator.async_import_profile.assert_not_awaited()
    assert entry.options == original_options

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator.async_import_profile.assert_awaited_once_with(
        payload, 7, confirmed=True
    )
    assert entry.options == original_options


async def test_cancelled_first_run_import_leaves_store_and_entry_unchanged(
    hass: HomeAssistant,
) -> None:
    """Closing the preview cannot create a profile or change entry options."""
    save = AsyncMock()
    entry, coordinator = profile_entry(hass, save=save, desired_profile={})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    original_options = dict(entry.options)
    payload = export_profile_payload(editable_profile())

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "import_profile"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"profile_json": json.dumps(payload)}
    )
    assert result["step_id"] == "import_profile_confirm"
    hass.config_entries.options.async_abort(result["flow_id"])

    coordinator.async_import_profile.assert_not_awaited()
    assert entry.options == original_options
    assert entry.data == {CONF_ADDRESS: ADDRESS}


async def test_import_accepts_complete_export_action_response(
    hass: HomeAssistant,
) -> None:
    """The UI accepts the same complete export JSON accepted by Repairs."""
    entry, coordinator = profile_entry(hass, desired_profile={})
    envelope = export_profile_payload(editable_profile())
    response = {"current_revision": 2, "profile": envelope}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "import_profile"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"profile_json": json.dumps(response)}
    )

    assert result["step_id"] == "import_profile_confirm"
    assert result["description_placeholders"]["revision"] == "7"
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    coordinator.async_import_profile.assert_awaited_once_with(
        envelope, 7, confirmed=True
    )


async def test_existing_full_profile_import_previews_removals_and_cas_conflict(
    hass: HomeAssistant,
) -> None:
    """A subset replacement names removed fields and cannot overwrite a new revision."""
    current = editable_profile()
    entry, coordinator = profile_entry(hass, desired_profile=current)
    coordinator.async_import_profile.side_effect = RevisionConflictError(
        "Synthetic concurrent edit"
    )
    payload = {
        "schema_version": 1,
        "scope": "supported_subset",
        "profile": {"volume": 1},
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "import_profile"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"profile_json": json.dumps(payload)}
    )

    removed = sorted(set(current) - {"volume"})
    assert result["description_placeholders"]["removed_count"] == str(len(removed))
    assert result["description_placeholders"]["removed_fields"] == ", ".join(removed)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "revision_conflict"}
    coordinator.async_import_profile.assert_awaited_once_with(
        payload, 7, confirmed=True
    )


async def test_first_run_import_aborts_when_entry_is_unloaded(
    hass: HomeAssistant,
) -> None:
    """Profile source UI never fabricates a Store while its entry is unloaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)
    payload = export_profile_payload(editable_profile())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "import_profile"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"profile_json": json.dumps(payload)}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"
    assert entry.data == {CONF_ADDRESS: ADDRESS}
    assert entry.options == {CONF_AUTO_RESTORE: False}


async def test_first_run_read_aborts_without_device_access(hass: HomeAssistant) -> None:
    """The Read choice accurately exposes the unimplemented strict readback."""
    entry, coordinator = profile_entry(hass, desired_profile={})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "read_profile"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "profile_readback_unavailable"
    coordinator.async_edit_profile.assert_not_awaited()


async def test_product_code_must_be_confirmed_from_label(
    hass: HomeAssistant,
) -> None:
    """Advertisement matching alone never unlocks device writes."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_PRODUCT_CODE: "GWM53"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["errors"] == {CONF_PRODUCT_CODE: "unsupported_product_code"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("info", "reason"),
    [
        (service_info(connectable=False), "not_connectable"),
        (service_info(manufacturer_data={950: b"XX"}), "unsupported_device"),
        (service_info(manufacturer_data={951: b"MB"}), "unsupported_device"),
    ],
)
async def test_bluetooth_discovery_rejected(
    hass: HomeAssistant, info: BluetoothServiceInfoBleak, reason: str
) -> None:
    """Reject unsupported and non-connectable advertisements."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_duplicate_discovery(hass: HomeAssistant) -> None:
    """An existing unique ID prevents a second entry."""
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_flow_without_devices(hass: HomeAssistant) -> None:
    """Manual setup only offers devices currently found by HA Bluetooth."""
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_manual_flow_also_requires_product_code(hass: HomeAssistant) -> None:
    """Manual discovery uses the same product-label gate as Bluetooth discovery."""
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[service_info()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    with patch(
        "custom_components.lumalou.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_PRODUCT_CODE: SUPPORTED_PRODUCT_CODE},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_PRODUCT_CODE: SUPPORTED_PRODUCT_CODE,
    }
    setup.assert_awaited_once()


async def test_legacy_entry_reconfigure_unlocks_only_gl_d09(
    hass: HomeAssistant,
) -> None:
    """Old address-only entries require explicit supported-label confirmation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_PRODUCT_CODE: "GWM53"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PRODUCT_CODE: "unsupported_product_code"}
    assert CONF_PRODUCT_CODE not in entry.data

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_PRODUCT_CODE: "gld09"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PRODUCT_CODE] == SUPPORTED_PRODUCT_CODE


async def test_auto_restore_cannot_be_enabled(hass: HomeAssistant) -> None:
    """Do not persist an option whose behavior is not implemented."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "behavior"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AUTO_RESTORE: True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_AUTO_RESTORE: "auto_restore_unavailable"}
    assert entry.options[CONF_AUTO_RESTORE] is False


async def test_behavior_options_flow_still_completes(hass: HomeAssistant) -> None:
    """The original behavior setting remains available behind the section menu."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)
    result = await start_editor(hass, entry, "behavior")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AUTO_RESTORE: False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_AUTO_RESTORE: False}


async def test_schedule_draft_saves_only_at_final_confirmation(
    hass: HomeAssistant,
) -> None:
    """Non-final schedule steps never persist the private draft."""
    entry, coordinator = profile_entry(hass)
    original_options = dict(entry.options)
    result = await start_editor(hass, entry, "schedule")

    schedule_input = {"ready_to_rise_enabled": True}
    for day in DAYS:
        schedule_input[f"ready_to_rise_{day}_has_time"] = day == "sunday"
        schedule_input[f"ready_to_rise_{day}_time"] = "00:00:00"
        schedule_input[f"sleepy_{day}_has_time"] = day == "monday"
        schedule_input[f"sleepy_{day}_time"] = "21:30:00"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=schedule_input
    )

    assert result["step_id"] == "schedule_copy"
    coordinator.async_edit_profile.assert_not_awaited()
    assert entry.options == original_options

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "ready_to_rise_copy_from": "sunday",
            "ready_to_rise_copy_to": [],
            "sleepy_copy_from": "sunday",
            "sleepy_copy_to": [],
        },
    )
    assert result["step_id"] == "schedule_alarm"

    alarm_input = {f"alarm_{day}": "9" for day in DAYS}
    alarm_input.update({"alarm_sunday": "0", "alarm_sound": "15"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=alarm_input
    )

    assert result["step_id"] == "schedule_confirm"
    coordinator.async_edit_profile.assert_not_awaited()
    assert result["description_placeholders"]["ready_count"] == "1"
    assert result["description_placeholders"]["sleepy_count"] == "1"

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"confirm": True}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator.async_edit_profile.assert_awaited_once()
    changes, expected_revision = coordinator.async_edit_profile.await_args.args
    assert expected_revision == 7
    assert changes["ready_to_rise"]["times"]["sunday"] == {
        "hour": 0,
        "minute": 0,
    }
    assert changes["ready_to_rise"]["times"]["monday"] is None
    assert changes["sleepy_times"]["monday"] == {"hour": 21, "minute": 30}
    assert changes["alarm"]["days"]["sunday"] == 0
    assert changes["alarm"]["sound"] == 15
    assert entry.options == original_options
    schedule_reload.assert_not_called()


async def test_cancel_discards_schedule_draft(hass: HomeAssistant) -> None:
    """Removing a flow after a draft step cannot write profile or options."""
    entry, coordinator = profile_entry(hass)
    original_options = dict(entry.options)
    result = await start_editor(hass, entry, "schedule")
    schedule_input = {"ready_to_rise_enabled": True}
    for day in DAYS:
        schedule_input[f"ready_to_rise_{day}_has_time"] = day == "sunday"
        schedule_input[f"ready_to_rise_{day}_time"] = "06:45:00"
        schedule_input[f"sleepy_{day}_has_time"] = False
        schedule_input[f"sleepy_{day}_time"] = "00:00:00"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=schedule_input
    )
    assert result["step_id"] == "schedule_copy"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "ready_to_rise_copy_from": "sunday",
            "ready_to_rise_copy_to": [],
            "sleepy_copy_from": "sunday",
            "sleepy_copy_to": [],
        },
    )
    assert result["step_id"] == "schedule_alarm"
    hass.config_entries.options.async_abort(result["flow_id"])

    coordinator.async_edit_profile.assert_not_awaited()
    assert entry.options == original_options


async def test_schedule_confirmation_rejects_revision_conflict(
    hass: HomeAssistant,
) -> None:
    """A stale captured revision remains a draft when CAS rejects it."""
    save = AsyncMock(side_effect=RevisionConflictError("stale"))
    entry, coordinator = profile_entry(hass, save=save)
    result = await start_editor(hass, entry, "schedule")
    schedule_input = {"ready_to_rise_enabled": False}
    for day in DAYS:
        schedule_input[f"ready_to_rise_{day}_has_time"] = False
        schedule_input[f"ready_to_rise_{day}_time"] = "00:00:00"
        schedule_input[f"sleepy_{day}_has_time"] = False
        schedule_input[f"sleepy_{day}_time"] = "00:00:00"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=schedule_input
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "ready_to_rise_copy_from": "sunday",
            "ready_to_rise_copy_to": [],
            "sleepy_copy_from": "sunday",
            "sleepy_copy_to": [],
        },
    )
    alarm_input = {f"alarm_{day}": "9" for day in DAYS}
    alarm_input["alarm_sound"] = "0"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=alarm_input
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "schedule_confirm"
    assert result["errors"] == {"base": "revision_conflict"}
    coordinator.async_edit_profile.assert_awaited_once()
    assert entry.options == {CONF_AUTO_RESTORE: False}


async def test_schedule_copy_applies_each_time_to_selected_days_only(
    hass: HomeAssistant,
) -> None:
    """Ready and Sleepy copies preserve midnight, null, and unselected days."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "schedule")
    schedule_input = {"ready_to_rise_enabled": True}
    for day in DAYS:
        schedule_input[f"ready_to_rise_{day}_has_time"] = True
        schedule_input[f"ready_to_rise_{day}_time"] = "08:15:00"
        schedule_input[f"sleepy_{day}_has_time"] = True
        schedule_input[f"sleepy_{day}_time"] = "23:00:00"
    schedule_input["ready_to_rise_sunday_time"] = "00:00:00"
    schedule_input["sleepy_tuesday_has_time"] = False

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=schedule_input
    )

    assert result["step_id"] == "schedule_copy"
    ready_target_selector = result["data_schema"].schema["ready_to_rise_copy_to"]
    assert ready_target_selector.config["multiple"] is True
    assert ready_target_selector.config["options"] == list(DAYS)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "ready_to_rise_copy_from": "sunday",
            "ready_to_rise_copy_to": ["monday", "thursday", "sunday"],
            "sleepy_copy_from": "tuesday",
            "sleepy_copy_to": ["wednesday"],
        },
    )

    assert result["step_id"] == "schedule_alarm"
    alarm_input = {f"alarm_{day}": "9" for day in DAYS}
    alarm_input["alarm_sound"] = "0"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=alarm_input
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    changes = coordinator.async_edit_profile.await_args.args[0]
    ready = changes["ready_to_rise"]["times"]
    sleepy = changes["sleepy_times"]
    assert (
        ready["sunday"]
        == ready["monday"]
        == ready["thursday"]
        == {
            "hour": 0,
            "minute": 0,
        }
    )
    assert ready["monday"] is not ready["sunday"]
    assert ready["tuesday"] == {"hour": 8, "minute": 15}
    assert sleepy["tuesday"] is None
    assert sleepy["wednesday"] is None
    assert sleepy["thursday"] == {"hour": 23, "minute": 0}


async def test_routine_copy_is_independent_and_preserves_task_zero(
    hass: HomeAssistant,
) -> None:
    """Copied routines detach and unnamed task zero cannot be selected or lost."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"routine_day": "sunday"}
    )
    task_selector = result["data_schema"].schema["task_2"]
    assert task_selector.config["options"] == [str(value) for value in range(1, 12)]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "routine_has_time": True,
            "routine_time": "00:00:00",
            "task_2": "3",
            "task_12": "11",
        },
    )
    assert result["step_id"] == "routine_copy"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"copy_to": ["monday", "tuesday"]}
    )
    assert result["step_id"] == "routine_confirm"
    assert result["description_placeholders"]["copy_count"] == "2"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    routines = coordinator.async_edit_profile.await_args.args[0]["routines"]
    assert routines["sunday"]["time"] == {"hour": 0, "minute": 0}
    assert routines["sunday"]["slots"][0] == {"step": 2, "task": 0}
    assert routines["sunday"] == routines["monday"] == routines["tuesday"]
    routines["monday"]["slots"][1]["task"] = 8
    assert routines["sunday"]["slots"][1]["task"] == 3
    assert routines["tuesday"]["slots"][1]["task"] == 3


async def test_routine_existing_task_can_be_cleared(hass: HomeAssistant) -> None:
    """An omitted optional row clears a visible task instead of defaulting it back."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"routine_day": "sunday"}
    )
    task_marker = next(
        marker for marker in result["data_schema"].schema if marker.schema == "task_2"
    )
    assert task_marker.description == {"suggested_value": "11"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"routine_has_time": True, "routine_time": "00:00:00"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"copy_to": []}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    routine = coordinator.async_edit_profile.await_args.args[0]["routines"]["sunday"]
    assert routine["slots"][0] == {"step": 2, "task": 0}
    assert routine["slots"][1] is None


async def test_routine_no_time_distinct_from_midnight(hass: HomeAssistant) -> None:
    """Routine toggle stores null even when visible selector contains midnight."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"routine_day": "monday"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"routine_has_time": False, "routine_time": "00:00:00"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"copy_to": []}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    routines = coordinator.async_edit_profile.await_args.args[0]["routines"]
    assert routines["monday"]["time"] is None


async def test_confirm_after_unload_aborts_cleanly(hass: HomeAssistant) -> None:
    """An open draft never crashes if the config entry unloads before save."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"routine_day": "monday"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"routine_has_time": False, "routine_time": "00:00:00"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"copy_to": []}
    )
    delattr(entry, "runtime_data")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "profile_editor_unavailable"
    coordinator.async_edit_profile.assert_not_awaited()


async def test_routine_invalid_and_overlength_input_rejected(
    hass: HomeAssistant,
) -> None:
    """Native schema rejects unsupported task IDs and a thirteenth row."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"routine_day": "friday"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "routine_has_time": True,
                "routine_time": "12:00:00",
                "task_1": "0",
            },
        )
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "routine_has_time": True,
                "routine_time": "12:00:00",
                **{f"task_{index}": "1" for index in range(1, 14)},
            },
        )
    coordinator.async_edit_profile.assert_not_awaited()


async def test_basic_editor_saves_only_confirmed_private_values(
    hass: HomeAssistant,
) -> None:
    """Light and audio values stay in a draft until one final CAS save."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "basic")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "brightness": "9",
            "color": "0",
            "light_duration": "5",
            "volume": "7",
            "playlist_duration": "6",
        },
    )

    assert result["step_id"] == "basic_confirm"
    coordinator.async_edit_profile.assert_not_awaited()

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"confirm": True}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    changes, expected_revision = coordinator.async_edit_profile.await_args.args
    assert expected_revision == 7
    assert changes == {
        "brightness": 9,
        "color": 0,
        "light_duration": 5,
        "volume": 7,
        "playlist_duration": 6,
    }
    schedule_reload.assert_not_called()


async def test_playlist_fixed_rows_preserve_order_duplicates_and_clear(
    hass: HomeAssistant,
) -> None:
    """Playlist rows are positional so duplicate songs are not collapsed."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "playlist")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"song_1": "18", "song_2": "2", "song_3": "2", "song_4": "1"},
    )
    assert result["step_id"] == "playlist_confirm"
    coordinator.async_edit_profile.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert coordinator.async_edit_profile.await_args.args[0] == {
        "playlist": [18, 2, 2, 1]
    }

    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "playlist")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert coordinator.async_edit_profile.await_args.args[0] == {"playlist": []}


async def test_absent_clock_and_routine_blocks_are_new_drafts_until_confirmed(
    hass: HomeAssistant,
) -> None:
    """Absent blocks are not fabricated merely by opening their editors."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "clock_settings")
    coordinator.async_edit_profile.assert_not_awaited()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "clock_display": True,
            "clock_brightness": "9",
            "clock_format": "1",
        },
    )
    assert result["step_id"] == "clock_settings_confirm"
    coordinator.async_edit_profile.assert_not_awaited()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert coordinator.async_edit_profile.await_args.args[0] == {
        "clock_settings": {"display": True, "brightness": 9, "format": 1}
    }

    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine_settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "routine_enabled": True,
            "routine_music": 255,
            "routine_volume": 0,
            "task_reward_sfx": "15",
            "routine_reward_sfx": "0",
        },
    )
    assert result["step_id"] == "routine_settings_confirm"
    coordinator.async_edit_profile.assert_not_awaited()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert coordinator.async_edit_profile.await_args.args[0] == {
        "routine_settings": {
            "enabled": True,
            "music": 255,
            "volume": 0,
            "task_reward_sfx": 15,
            "routine_reward_sfx": 0,
        }
    }


async def test_routine_settings_rejects_non_byte_values(
    hass: HomeAssistant,
) -> None:
    """Raw routine music and volume remain bounded whole bytes."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine_settings")
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "routine_enabled": False,
                "routine_music": 256,
                "routine_volume": 0,
                "task_reward_sfx": "0",
                "routine_reward_sfx": "0",
            },
        )
    coordinator.async_edit_profile.assert_not_awaited()


async def test_new_profile_editors_abort_without_runtime_data(
    hass: HomeAssistant,
) -> None:
    """An unloaded entry cannot expose a profile draft."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)

    for section in ("basic", "playlist", "clock_settings", "routine_settings"):
        result = await start_editor(hass, entry, section)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "profile_editor_unavailable"


@pytest.mark.parametrize(
    ("section", "step", "user_input", "error"),
    [
        (
            "basic",
            "async_step_basic",
            {
                "brightness": "10",
                "color": "0",
                "light_duration": "0",
                "volume": "0",
                "playlist_duration": "0",
            },
            "invalid_basic",
        ),
        ("playlist", "async_step_playlist", {"song_1": "19"}, "invalid_playlist"),
        (
            "clock_settings",
            "async_step_clock_settings",
            {
                "clock_display": 0,
                "clock_brightness": "0",
                "clock_format": "0",
            },
            "invalid_clock_settings",
        ),
        (
            "routine_settings",
            "async_step_routine_settings",
            {
                "routine_enabled": False,
                "routine_music": 0,
                "routine_volume": 0,
                "task_reward_sfx": "16",
                "routine_reward_sfx": "0",
            },
            "invalid_routine_settings",
        ),
    ],
)
async def test_new_editor_invalid_payloads_return_validation_form(
    hass: HomeAssistant,
    section: str,
    step: str,
    user_input: dict[str, object],
    error: str,
) -> None:
    """Model validation retains an unsaved draft when a native value is invalid."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, section)
    flow = hass.config_entries.options._progress[result["flow_id"]]

    result = await getattr(flow, step)(user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == section
    assert result["errors"] == {"base": error}
    coordinator.async_edit_profile.assert_not_awaited()


async def test_routine_settings_rejects_fractional_byte_in_validation_branch(
    hass: HomeAssistant,
) -> None:
    """The number selector cannot silently truncate a fractional raw byte."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "routine_settings")
    flow = hass.config_entries.options._progress[result["flow_id"]]

    result = await flow.async_step_routine_settings(
        {
            "routine_enabled": False,
            "routine_music": 1.5,
            "routine_volume": 0,
            "task_reward_sfx": "0",
            "routine_reward_sfx": "0",
        }
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_routine_settings"}
    coordinator.async_edit_profile.assert_not_awaited()


@pytest.mark.parametrize("invalid_row", ("song_13", "unknown_song"))
async def test_playlist_schema_rejects_unknown_rows(
    hass: HomeAssistant, invalid_row: str
) -> None:
    """The fixed playlist UI exposes exactly twelve validated rows."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "playlist")

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"song_1": "1", invalid_row: "2"}
        )

    coordinator.async_edit_profile.assert_not_awaited()


async def test_basic_confirmation_retains_draft_after_cas_conflict(
    hass: HomeAssistant,
) -> None:
    """A stale basic editor does not overwrite a newer private profile revision."""
    save = AsyncMock(side_effect=RevisionConflictError("stale"))
    entry, coordinator = profile_entry(hass, save=save)
    result = await start_editor(hass, entry, "basic")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "brightness": "0",
            "color": "0",
            "light_duration": "0",
            "volume": "0",
            "playlist_duration": "0",
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "basic_confirm"
    assert result["errors"] == {"base": "revision_conflict"}
    coordinator.async_edit_profile.assert_awaited_once_with(
        {
            "brightness": 0,
            "color": 0,
            "light_duration": 0,
            "volume": 0,
            "playlist_duration": 0,
        },
        7,
    )
