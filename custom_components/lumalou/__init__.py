"""Home Assistant integration for Fisher-Price Lumalou."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DEVICE_FINGERPRINT,
    DOMAIN,
    ISSUE_ID_IDENTITY_ENROLLMENT,
    ISSUE_ID_PROFILE_STORAGE,
    PLATFORMS,
)
from .entity import async_migrate_identifiers
from .models import LumalouRuntimeData
from .services import async_setup_services

type LumalouConfigEntry = ConfigEntry[LumalouRuntimeData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Key registry identifiers by the entry unique ID instead of the BLE address."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        address = entry.data[CONF_ADDRESS]
        if entry.data.get(CONF_DEVICE_FINGERPRINT):
            await async_migrate_identifiers(hass, entry, address, str(entry.unique_id))
        else:
            # Pre-enrollment entries keep address-keyed identifiers (their
            # unique ID is the address) and stay blocked by the coordinator
            # until Reconfigure verifies the device and migrates them.
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"{entry.entry_id}_{ISSUE_ID_IDENTITY_ENROLLMENT}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_ID_IDENTITY_ENROLLMENT,
                translation_placeholders={"title": entry.title},
            )
        # The connection diagnostic moved from a text sensor to a binary sensor.
        registry = er.async_get(hass)
        if old_sensor := registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{entry.unique_id}_connection"
        ):
            registry.async_remove(old_sensor)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Set up Lumalou without requiring the device to be online."""
    from .coordinator import LumalouCoordinator

    coordinator = LumalouCoordinator(hass, entry)
    await coordinator.async_setup()
    issue_id = f"{entry.entry_id}_{ISSUE_ID_PROFILE_STORAGE}"
    if coordinator.profile_storage_healthy:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            data={"entry_id": entry.entry_id},
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_ID_PROFILE_STORAGE,
        )
    entry.runtime_data = LumalouRuntimeData(coordinator)

    try:
        coordinator.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Unload a Lumalou config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.coordinator.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> None:
    """Remove this entry's Repairs issues without touching private profile storage."""
    for issue in (ISSUE_ID_PROFILE_STORAGE, ISSUE_ID_IDENTITY_ENROLLMENT):
        ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{issue}")
