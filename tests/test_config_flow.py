"""Config flow tests without real Bluetooth access."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import BleakNotFoundError
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from lumalou.factory import InvalidFactoryTokenError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.config_flow import (
    CONF_AUTO_RESTORE,
    LumalouOptionsFlow,
    _device_title,
)
from custom_components.lumalou.const import (
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    DOMAIN,
)
from custom_components.lumalou.models import (
    DAYS,
    LumalouRuntimeData,
    ProfileRecord,
    RevisionConflictError,
)

ADDRESS = "AA:BB:CC:DD:EE:01"
FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64
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
    *,
    address: str = ADDRESS,
    connectable: bool = True,
    manufacturer_data: dict[int, bytes] | None = None,
    name: str = "Lumalou test",
) -> BluetoothServiceInfoBleak:
    """Build synthetic discovery data."""
    device = BLEDevice(address, name, {})
    advertisement = AdvertisementData(
        local_name=name,
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


def test_numeric_ble_advertisement_name_is_not_used_as_entity_title() -> None:
    """A serial-like AP number stays out of HA titles and HomeKit names."""
    assert _device_title(service_info(name="9876543210")) == "Lumalou"
    assert _device_title(service_info(name="Lumalou nursery")) == "Lumalou nursery"


def editable_profile() -> dict:
    """Return a complete read profile with a routine exercising task-zero rows."""
    profile = complete_profile()
    profile["routines"]["sunday"] = {
        "time": {"hour": 0, "minute": 0},
        "slots": [
            {"step": 2, "task": 0},
            {"step": 1, "task": 11},
            *([None] * 10),
        ],
    }
    return profile


def complete_profile() -> dict:
    """Synthetic full device snapshot used only for options-flow tests."""
    empty_week = {day: None for day in DAYS}
    return {
        "playlist": [1, 2, 3],
        "clock_settings": {"display": True, "brightness": 2, "format": 1},
        "routine_settings": {
            "enabled": False,
            "music": 0,
            "volume": 0,
            "task_reward_sfx": 0,
            "routine_reward_sfx": 0,
        },
        "ready_to_rise": {"enabled": False, "times": deepcopy(empty_week)},
        "sleepy_times": deepcopy(empty_week),
        "alarm": {"days": {day: 9 for day in DAYS}, "sound": 0},
        "routines": {day: {"time": None, "slots": [None] * 12} for day in DAYS},
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
        async_accept_device_profile=AsyncMock(),
        async_read_profile_snapshot=AsyncMock(
            return_value=(complete_profile(), revision)
        ),
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
    assert result["step_id"] in ("init", "read_first")
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": section}
    )


async def test_bluetooth_discovery_confirm(hass: HomeAssistant) -> None:
    """Confirmed discovery binds a private device fingerprint, without a model form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
        patch(
            "custom_components.lumalou.async_setup_entry", return_value=True
        ) as setup,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == FINGERPRINT
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: False,
    }
    assert result["result"].data[CONF_PROTOCOL_VERIFIED] is False
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
    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["next_flow"][0] is FlowType.OPTIONS_FLOW
    options_flow_id = result["next_flow"][1]
    assert hass.config_entries.options.async_get(options_flow_id)
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: False,
    }
    assert result["options"] == {CONF_AUTO_RESTORE: False}


@pytest.mark.parametrize(
    "desired_profile",
    [{}, {"playlist": [1]}],
    ids=["empty", "partial"],
)
async def test_without_a_device_read_only_read_and_behavior_are_offered(
    hass: HomeAssistant, desired_profile: dict[str, Any]
) -> None:
    """Editors never start from fabricated defaults or a partial profile."""
    entry, coordinator = profile_entry(hass, desired_profile=desired_profile)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "read_first"
    assert result["menu_options"] == ["read_profile", "behavior"]
    assert entry.data == {CONF_ADDRESS: ADDRESS}
    assert entry.options == {CONF_AUTO_RESTORE: False}
    coordinator.async_edit_profile.assert_not_awaited()


async def test_read_profile_menu_offers_editors_without_json_import(
    hass: HomeAssistant,
) -> None:
    """A complete profile unlocks the editors; JSON import is an action only."""
    entry, _ = profile_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == [
        "read_profile",
        "playlist",
        "clock_settings",
        "routine_settings",
        "schedule",
        "routine",
        "behavior",
    ]


@pytest.mark.parametrize(
    ("loaded", "reason"),
    [(False, "entry_not_loaded"), (True, "profile_not_read")],
)
@pytest.mark.parametrize(
    "step", ["playlist", "clock_settings", "routine_settings", "routine"]
)
async def test_editor_step_aborts_without_a_complete_profile(
    hass: HomeAssistant, loaded: bool, reason: str, step: str
) -> None:
    """A stale menu cannot open an editor without a loaded, read profile."""
    if loaded:
        entry, _ = profile_entry(hass, desired_profile={"playlist": [2]})
    else:
        entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={})
        entry.add_to_hass(hass)
    flow = LumalouOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id

    result = await getattr(flow, f"async_step_{step}")()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_first_run_read_previews_and_imports_complete_device_snapshot(
    hass: HomeAssistant,
) -> None:
    """Read requires a full fresh snapshot and explicit CAS save confirmation."""
    entry, coordinator = profile_entry(hass, desired_profile={})
    snapshot = complete_profile()
    coordinator.async_read_profile_snapshot.return_value = (snapshot, 7)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "read_profile"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "read_profile_confirm"
    assert result["description_placeholders"] == {
        "revision": "7",
        "song_count": "3",
        "wake_count": "0",
        "bedtime_count": "0",
        "alarm_count": "0",
        "routine_days": "0",
        "task_count": "0",
    }
    coordinator.async_read_profile_snapshot.assert_awaited_once()
    coordinator.async_accept_device_profile.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator.async_accept_device_profile.assert_awaited_once_with(
        snapshot, 7, confirmed=True
    )


async def test_failed_device_read_does_not_preview_or_change_profile(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = profile_entry(hass, desired_profile={"playlist": [2]})
    coordinator.async_read_profile_snapshot.side_effect = HomeAssistantError(
        "incomplete device snapshot"
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "read_profile"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "profile_read_failed"
    coordinator.async_accept_device_profile.assert_not_awaited()


async def test_signed_device_fingerprint_needs_no_model_input(
    hass: HomeAssistant,
) -> None:
    """A signed device key enrolls the confirmed unit without a SKU map."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )

    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == FINGERPRINT
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: False,
    }


async def test_distinct_signed_fingerprint_creates_distinct_entry(
    hass: HomeAssistant,
) -> None:
    """A different verified device is not rejected by an item-code map."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(name="another-device"),
    )
    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=OTHER_FINGERPRINT,
        ),
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == OTHER_FINGERPRINT
    assert result["data"][CONF_DEVICE_FINGERPRINT] == OTHER_FINGERPRINT
    assert result["data"][CONF_PROTOCOL_VERIFIED] is False


async def test_setup_reads_the_signed_identity_once(
    hass: HomeAssistant,
) -> None:
    """Only the signed factory identity is read; nothing else is probed."""
    info = service_info()
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )

    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ) as factory_probe,
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: False,
    }
    factory_probe.assert_awaited_once_with(hass, info.device)


async def test_signed_fingerprint_is_not_displayed_in_confirmation(
    hass: HomeAssistant,
) -> None:
    """The private device fingerprint stays out of the UI."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )

    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert FINGERPRINT not in json.dumps(result.get("description_placeholders", {}))


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic connect failure"),
        BleakNotFoundError("synthetic device not found"),
        HomeAssistantError("No connectable Lumalou device is available"),
        TimeoutError(),
    ],
)
async def test_identity_connection_error_is_retryable(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    """A failed identity connection is logged and creates no entry."""
    caplog.set_level(logging.DEBUG, logger="custom_components.lumalou.config_flow")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )

    with patch(
        "custom_components.lumalou.config_flow.async_read_device_fingerprint",
        new_callable=AsyncMock,
        side_effect=error,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)
    [record] = [
        record
        for record in caplog.records
        if record.name == "custom_components.lumalou.config_flow"
    ]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == f"Could not connect to {ADDRESS}"
    assert record.exc_info is not None and record.exc_info[1] is error


async def test_unverifiable_identity_creates_no_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A token the library cannot authenticate never creates an entry."""
    caplog.set_level(logging.DEBUG, logger="custom_components.lumalou.config_flow")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )

    with patch(
        "custom_components.lumalou.config_flow.async_read_device_fingerprint",
        new_callable=AsyncMock,
        side_effect=InvalidFactoryTokenError("synthetic"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "identity_unconfirmed"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert "Could not authenticate the identity" in caplog.text


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
    """The same signed key at a new address updates, not duplicates, its entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        data={
            CONF_ADDRESS: "different-synthetic-address",
            CONF_DEVICE_FINGERPRINT: FINGERPRINT,
            CONF_PROTOCOL_VERIFIED: True,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(),
    )
    assert result["type"] is FlowResultType.FORM
    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    # The signed key is identical, so controls stay unlocked.
    assert entry.data == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: True,
    }


async def test_same_address_with_different_fingerprint_aborts_before_probe(
    hass: HomeAssistant,
) -> None:
    """A second entry cannot claim an address owned by another signed device."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_FINGERPRINT,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DEVICE_FINGERPRINT: OTHER_FINGERPRINT,
        },
    ).add_to_hass(hass)

    with patch(
        "custom_components.lumalou.config_flow.async_read_device_fingerprint",
        new_callable=AsyncMock,
    ) as factory_probe:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_BLUETOOTH},
            data=service_info(),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    factory_probe.assert_not_awaited()


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


async def test_manual_flow_also_requires_signed_identity(hass: HomeAssistant) -> None:
    """Manual discovery uses the same model-free authenticated identity flow."""
    info = service_info()
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    with (
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
        patch(
            "custom_components.lumalou.async_setup_entry", return_value=True
        ) as setup,
    ):
        # Choosing the device is the confirmation; no second form follows.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: False,
    }
    setup.assert_awaited_once()


async def test_reconfigure_changed_address_preserves_bound_identity_and_options(
    hass: HomeAssistant,
) -> None:
    """An address change keeps the same signed device and private entry."""
    new_address = "AA:BB:CC:DD:EE:02"
    info = service_info(address=new_address, name="9876543210")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        title="Nursery",
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DEVICE_FINGERPRINT: FINGERPRINT,
            CONF_PROTOCOL_VERIFIED: True,
        },
        options={CONF_AUTO_RESTORE: False, "user_preference": "keep"},
    )
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id
    original_options = dict(entry.options)

    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
    assert result["type"] is FlowResultType.FORM
    assert "9876543210" not in str(result["data_schema"].schema)

    with (
        patch(
            "homeassistant.components.bluetooth.async_discovered_service_info",
            return_value=[info],
        ),
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=FINGERPRINT,
        ),
        # Reconfigure reloads the entry; never start real Bluetooth here.
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: new_address}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.entry_id == original_entry_id
    assert entry.unique_id == FINGERPRINT
    assert entry.title == "Nursery"
    # Same signed key: controls stay unlocked, only the address changes.
    assert entry.data == {
        CONF_ADDRESS: new_address,
        CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        CONF_PROTOCOL_VERIFIED: True,
    }
    assert entry.options == original_options


async def test_reconfigure_rejects_different_signed_device(
    hass: HomeAssistant,
) -> None:
    """A new BLE address cannot silently replace the bound physical device."""
    new_address = "AA:BB:CC:DD:EE:02"
    info = service_info(address=new_address)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DEVICE_FINGERPRINT: FINGERPRINT,
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.bluetooth.async_discovered_service_info",
            return_value=[info],
        ),
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            return_value=OTHER_FINGERPRINT,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: new_address}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_device"}
    assert entry.data == {CONF_ADDRESS: ADDRESS, CONF_DEVICE_FINGERPRINT: FINGERPRINT}
    assert entry.unique_id == FINGERPRINT


async def test_reconfigure_rejects_stale_candidate_without_probe(
    hass: HomeAssistant,
) -> None:
    """A selected device must still be connectable through HA Bluetooth."""
    new_address = "AA:BB:CC:DD:EE:02"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        data={CONF_ADDRESS: ADDRESS, CONF_DEVICE_FINGERPRINT: FINGERPRINT},
    )
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[service_info(address=new_address)],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )

    with (
        patch(
            "homeassistant.components.bluetooth.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
        ) as factory_probe,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: new_address}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_unavailable"}
    factory_probe.assert_not_awaited()
    assert entry.data[CONF_ADDRESS] == ADDRESS


async def test_reconfigure_rejects_address_claimed_after_form_opened(
    hass: HomeAssistant,
) -> None:
    """An address claimed by another entry during the flow aborts before a probe."""
    new_address = "AA:BB:CC:DD:EE:02"
    info = service_info(address=new_address)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        data={CONF_ADDRESS: ADDRESS, CONF_DEVICE_FINGERPRINT: FINGERPRINT},
    )
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[info],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_FINGERPRINT,
        data={CONF_ADDRESS: new_address, CONF_DEVICE_FINGERPRINT: OTHER_FINGERPRINT},
    ).add_to_hass(hass)

    with patch(
        "custom_components.lumalou.config_flow.async_read_device_fingerprint",
        new_callable=AsyncMock,
    ) as factory_probe:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: new_address}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    factory_probe.assert_not_awaited()
    assert entry.data[CONF_ADDRESS] == ADDRESS


async def test_auto_restore_is_opt_in(hass: HomeAssistant) -> None:
    """Automatic restore defaults off and is saved only when the user enables it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_AUTO_RESTORE: False},
    )
    entry.add_to_hass(hass)

    result = await start_editor(hass, entry, "behavior")
    assert result["data_schema"]({})[CONF_AUTO_RESTORE] is False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AUTO_RESTORE: True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_AUTO_RESTORE] is True


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
    assert result["description_placeholders"]["day"] == "Sunday"
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


async def test_routine_confirm_translates_the_day(hass: HomeAssistant) -> None:
    """The day placeholder uses the configured language, not the storage key."""
    hass.config.language = "ru"
    entry, _ = profile_entry(hass)
    result = await start_editor(hass, entry, "routine")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"routine_day": "friday"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"routine_has_time": False, "routine_time": "00:00:00"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={}
    )

    assert result["step_id"] == "routine_confirm"
    assert result["description_placeholders"]["day"] == "Пятница"


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
    assert result["reason"] == "entry_not_loaded"
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


async def test_playlist_fixed_rows_preserve_order_duplicates_and_clear(
    hass: HomeAssistant,
) -> None:
    """Playlist rows are positional so duplicate songs are not collapsed."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, "playlist")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"song_1": "12", "song_2": "2", "song_3": "2", "song_4": "1"},
    )
    assert result["step_id"] == "playlist_confirm"
    coordinator.async_edit_profile.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert coordinator.async_edit_profile.await_args.args[0] == {
        "playlist": [12, 2, 2, 1]
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
                "routine_music": 16,
                "routine_volume": 0,
                "task_reward_sfx": "0",
                "routine_reward_sfx": "0",
            },
        )
    coordinator.async_edit_profile.assert_not_awaited()


@pytest.mark.parametrize(
    ("section", "step", "user_input", "error"),
    [
        ("playlist", "async_step_playlist", {"song_1": "13"}, "invalid_playlist"),
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


async def test_editor_confirmation_retains_draft_after_cas_conflict(
    hass: HomeAssistant,
) -> None:
    """A stale editor does not overwrite a newer private profile revision."""
    save = AsyncMock(side_effect=RevisionConflictError("stale"))
    entry, coordinator = profile_entry(hass, save=save)
    result = await start_editor(hass, entry, "playlist")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"song_1": "2"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "playlist_confirm"
    assert result["errors"] == {"base": "revision_conflict"}
    coordinator.async_edit_profile.assert_awaited_once_with({"playlist": [2]}, 7)


def _probe_patch(fingerprint: str = FINGERPRINT):
    """Patch the read-only identity probe."""
    return patch(
        "custom_components.lumalou.config_flow.async_read_device_fingerprint",
        new_callable=AsyncMock,
        return_value=fingerprint,
    )


async def test_repeated_discovery_does_not_start_a_second_flow(
    hass: HomeAssistant,
) -> None:
    """The provisional address ID deduplicates discovery flows without I/O."""
    with _probe_patch() as factory_mock:
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
        )
        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
        )

    assert first["type"] is FlowResultType.FORM
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1
    factory_mock.assert_not_awaited()


async def test_confirm_aborts_without_probe_if_address_was_configured_meanwhile(
    hass: HomeAssistant,
) -> None:
    """A discovery card left open never probes an address configured since."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_FINGERPRINT,
        data={CONF_ADDRESS: ADDRESS, CONF_DEVICE_FINGERPRINT: OTHER_FINGERPRINT},
    ).add_to_hass(hass)

    with _probe_patch() as factory_mock:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    factory_mock.assert_not_awaited()


async def test_manual_setup_proceeds_while_discovery_is_pending(
    hass: HomeAssistant,
) -> None:
    """A user may pick a candidate that also has an open discovery card."""
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[service_info()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with (
        _probe_patch(),
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == FINGERPRINT


async def test_manual_setup_probe_failure_returns_to_device_choice(
    hass: HomeAssistant,
) -> None:
    """A failed identity probe shows the selection form again with an error."""
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[service_info()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        with (
            patch(
                "custom_components.lumalou.config_flow.async_read_device_fingerprint",
                new_callable=AsyncMock,
                side_effect=OSError("synthetic"),
            ),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
            )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_reconfigure_address_change_keeps_registry_identifiers(
    hass: HomeAssistant,
) -> None:
    """Fingerprint-keyed identifiers do not change with the BLE address."""
    new_address = "AA:BB:CC:DD:EE:02"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        data={CONF_ADDRESS: ADDRESS, CONF_DEVICE_FINGERPRINT: FINGERPRINT},
    )
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    light = entity_registry.async_get_or_create(
        "light", DOMAIN, f"{FINGERPRINT}_light", config_entry=entry
    )

    with (
        patch(
            "homeassistant.components.bluetooth.async_discovered_service_info",
            return_value=[service_info(address=new_address)],
        ),
        _probe_patch(),
        patch("custom_components.lumalou.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: new_address}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == new_address
    assert entity_registry.async_get(light.entity_id).unique_id == (
        f"{FINGERPRINT}_light"
    )


async def test_reconfigure_without_candidates_aborts(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=FINGERPRINT, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_confirmation_checkbox_is_required_and_failures_keep_draft(
    hass: HomeAssistant,
) -> None:
    """Saving needs the explicit checkbox; a failed save leaves nothing applied."""
    entry, coordinator = profile_entry(
        hass, save=AsyncMock(side_effect=HomeAssistantError("disk"))
    )
    result = await start_editor(hass, entry, "playlist")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"song_1": "3"}
    )
    assert result["description_placeholders"] == {
        "revision": "7",
        "song_count": "1",
        "songs": "3",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": False}
    )
    assert result["errors"] == {"confirm": "confirmation_required"}
    coordinator.async_edit_profile.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )
    assert result["errors"] == {"base": "profile_save_failed"}


async def test_read_profile_aborts_when_entry_is_unloaded(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=FINGERPRINT, data={})
    entry.add_to_hass(hass)

    result = await start_editor(hass, entry, "read_profile")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"


@pytest.mark.parametrize(
    ("section", "user_input", "changes", "placeholders"),
    [
        (
            "clock_settings",
            {"clock_display": False, "clock_brightness": "4", "clock_format": "0"},
            {"clock_settings": {"display": False, "brightness": 4, "format": 0}},
            {"display": "False", "brightness": "4", "format": "0"},
        ),
        (
            "routine_settings",
            {
                "routine_enabled": True,
                "routine_music": 3.0,
                "routine_volume": 4,
                "task_reward_sfx": "1",
                "routine_reward_sfx": "2",
            },
            {
                "routine_settings": {
                    "enabled": True,
                    "music": 3,
                    "volume": 4,
                    "task_reward_sfx": 1,
                    "routine_reward_sfx": 2,
                }
            },
            {"enabled": "True", "music": "3", "volume": "4"},
        ),
    ],
)
async def test_block_editors_start_from_the_read_profile(
    hass: HomeAssistant,
    section: str,
    user_input: dict[str, Any],
    changes: dict[str, Any],
    placeholders: dict[str, str],
) -> None:
    """Block editors are prefilled from the saved profile and save one block."""
    entry, coordinator = profile_entry(hass)
    result = await start_editor(hass, entry, section)
    assert result["step_id"] == section

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result["step_id"] == f"{section}_confirm"
    assert placeholders.items() <= result["description_placeholders"].items()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"confirm": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator.async_edit_profile.assert_awaited_once_with(changes, 7)


@pytest.mark.parametrize(
    "value", [None, "07:30:15", "07:30:00+02:00"], ids=["missing", "seconds", "tz"]
)
def test_time_parser_requires_plain_minute_resolution(value: Any) -> None:
    from custom_components.lumalou.config_flow import _parse_time

    with pytest.raises(ValueError):
        _parse_time(value)


@pytest.mark.parametrize(
    ("value", "source"),
    [(["monday"], "someday"), ("monday", "sunday"), (["someday"], "sunday")],
)
def test_copy_targets_reject_unknown_days(value: Any, source: str) -> None:
    from custom_components.lumalou.config_flow import _copy_targets

    with pytest.raises(ValueError):
        _copy_targets(value, source)


async def test_reconfigure_probe_failure_keeps_the_form(hass: HomeAssistant) -> None:
    """A failed identity probe during reconfigure changes nothing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FINGERPRINT,
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:02", CONF_DEVICE_FINGERPRINT: FINGERPRINT},
    )
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[service_info()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        with patch(
            "custom_components.lumalou.config_flow.async_read_device_fingerprint",
            new_callable=AsyncMock,
            side_effect=OSError("synthetic"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
            )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_ADDRESS] == "AA:BB:CC:DD:EE:02"


def test_routine_task_parser_rejects_task_outside_named_choices() -> None:
    """Defense in depth behind the selector: task 12 is not a named task."""
    flow = LumalouOptionsFlow()
    flow._draft = editable_profile()
    flow._routine_day = "monday"

    with pytest.raises(ValueError):
        flow._parse_routine_tasks({"routine_has_time": False, "task_1": "12"})
