"""Lumalou number entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from .entity import LumalouControlEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou numbers."""
    async_add_entities(
        [LumalouClockBrightnessNumber(entry), LumalouRoutineVolumeNumber(entry)]
    )


class LumalouClockBrightnessNumber(LumalouControlEntity, NumberEntity):
    """Clock display brightness, device levels 0-9."""

    _attr_translation_key = "clock_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 9
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int | None:
        return self.snapshot_value("clockBrightness")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_clock_settings(brightness=round(value))


class LumalouRoutineVolumeNumber(LumalouControlEntity, NumberEntity):
    """Routine music and reward sound volume, device levels 0-9."""

    _attr_translation_key = "routine_volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 9
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int | None:
        return self.snapshot_value("routineVolume")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_routine_settings(volume=round(value))
