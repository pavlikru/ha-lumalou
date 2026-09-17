"""Config flow tests without real Bluetooth access."""

from __future__ import annotations

import time
from collections.abc import Generator
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
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
) -> tuple[MockConfigEntry, SimpleNamespace]:
    """Add a loaded-looking entry backed by an isolated coordinator mock."""
    coordinator = SimpleNamespace(
        profile_record=ProfileRecord(
            revision=revision,
            desired_profile=editable_profile(),
            sync_status="saved",
        ),
        async_edit_profile=save or AsyncMock(),
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

    assert result["step_id"] == "schedule_alarm"
    coordinator.async_edit_profile.assert_not_awaited()
    assert entry.options == original_options

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
