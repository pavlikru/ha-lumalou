"""Lumalou connectivity binary sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .entity import LumalouEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the connectivity binary sensor."""
    async_add_entities([LumalouConnectionBinarySensor(entry)])


class LumalouConnectionBinarySensor(LumalouEntity, BinarySensorEntity):
    """Report whether the coordinator has a live device connection."""

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """The diagnostic itself remains readable while BLE is offline."""
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.available)
