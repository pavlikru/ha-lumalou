"""Home Assistant integration for Fisher-Price Lumalou."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import PLATFORMS
from .models import LumalouRuntimeData
from .services import async_setup_services

type LumalouConfigEntry = ConfigEntry[LumalouRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Set up Lumalou without requiring the device to be online."""
    from .coordinator import LumalouCoordinator

    coordinator = LumalouCoordinator(hass, entry)
    await coordinator.async_setup()
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
