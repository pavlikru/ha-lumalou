"""Lumalou diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import LumalouEntity


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up diagnostic sensors."""
    async_add_entities([LumalouAvailabilitySensor(entry), LumalouFirmwareSensor(entry)])


class LumalouAvailabilitySensor(LumalouEntity, SensorEntity):
    """Report coordinator availability for diagnostics."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = None

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Connection", "connection")
        self._attr_translation_key = "connection"

    @property
    def available(self) -> bool:
        """The diagnostic itself remains readable while BLE is offline."""
        return True

    @property
    def native_value(self) -> str:
        return "available" if self.coordinator.available else "unavailable"


class LumalouFirmwareSensor(LumalouEntity, SensorEntity):
    """Report firmware version without performing I/O."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Firmware", "firmware")
        self._attr_translation_key = "firmware"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.sw_version
