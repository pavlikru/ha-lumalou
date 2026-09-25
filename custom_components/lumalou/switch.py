"""Lumalou switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import LumalouControlEntity, LumalouEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou switches."""
    async_add_entities(
        [LumalouMaintenanceSwitch(entry), LumalouClockDisplaySwitch(entry)]
    )


class LumalouMaintenanceSwitch(LumalouEntity, SwitchEntity):
    """Release Bluetooth and block integration device operations."""

    _attr_translation_key = "maintenance"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Maintenance is saved locally, so it stays usable while offline."""
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.profile_record.maintenance

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_maintenance(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_maintenance(False)


class LumalouClockDisplaySwitch(LumalouControlEntity, SwitchEntity):
    """Show or hide the device clock."""

    _attr_translation_key = "clock_display"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        value = self.snapshot_value("clockDisplay")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_clock_settings(display=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_clock_settings(display=False)
