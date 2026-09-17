"""Lumalou switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from .entity import LumalouEntity


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou switches."""
    async_add_entities([LumalouMaintenanceSwitch(entry)])


class LumalouMaintenanceSwitch(LumalouEntity, SwitchEntity):
    """Release Bluetooth and block integration device operations."""

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Maintenance", "maintenance")
        self._attr_translation_key = "maintenance"

    @property
    def is_on(self) -> bool | None:
        record = getattr(self._entry.runtime_data, "profile_record", None)
        return getattr(record, "maintenance", None) if record is not None else None

    @property
    def available(self) -> bool:
        return getattr(self._entry.runtime_data, "profile_record", None) is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_maintenance(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_maintenance(False)
