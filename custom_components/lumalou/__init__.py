"""Home Assistant integration for Fisher-Price Lumalou."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, ISSUE_ID_PROFILE_STORAGE, PLATFORMS
from .models import LumalouRuntimeData
from .services import async_setup_services
from .storage import ProfileStore

type LumalouConfigEntry = ConfigEntry[LumalouRuntimeData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide actions."""
    async_setup_services(hass)
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
    """Remove this entry's Repairs issue and its private profile Store.

    The saved profile is bound to this entry and device key; users keep a copy
    with the export action before removing the device.
    """
    ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{ISSUE_ID_PROFILE_STORAGE}")
    await ProfileStore(hass, entry.entry_id).async_remove()
