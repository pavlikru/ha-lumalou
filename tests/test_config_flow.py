"""Config flow tests without real Bluetooth access."""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.config_flow import CONF_AUTO_RESTORE
from custom_components.lumalou.const import DOMAIN

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
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == ADDRESS
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["options"] == {CONF_AUTO_RESTORE: False}
    setup.assert_awaited_once()


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
        result["flow_id"], user_input={CONF_AUTO_RESTORE: True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_AUTO_RESTORE: "auto_restore_unavailable"}
    assert entry.options[CONF_AUTO_RESTORE] is False
