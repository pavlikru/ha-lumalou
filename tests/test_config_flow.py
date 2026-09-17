"""Config flow tests without real Bluetooth access."""

from __future__ import annotations

import time
from collections.abc import Generator
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
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.config_flow import CONF_AUTO_RESTORE
from custom_components.lumalou.const import (
    CONF_PRODUCT_CODE,
    DOMAIN,
    SUPPORTED_PRODUCT_CODE,
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
        result["flow_id"], user_input={CONF_AUTO_RESTORE: True}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_AUTO_RESTORE: "auto_restore_unavailable"}
    assert entry.options[CONF_AUTO_RESTORE] is False
