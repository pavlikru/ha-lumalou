"""Lumalou diagnostic binary sensors."""

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
    """Set up saved-profile diagnostic binary sensors."""
    async_add_entities(
        [
            LumalouConnectionBinarySensor(entry),
            LumalouProfilePendingBinarySensor(entry),
            LumalouProfilePresentBinarySensor(entry),
        ]
    )


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


class LumalouProfilePendingBinarySensor(LumalouEntity, BinarySensorEntity):
    """Report whether saved profile changes still need verification."""

    _attr_translation_key = "profile_pending"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Saved metadata remains available without a BLE state snapshot."""
        return True

    @property
    def is_on(self) -> bool:
        return self._entry.runtime_data.profile_record.pending


class LumalouProfilePresentBinarySensor(LumalouEntity, BinarySensorEntity):
    """Report whether any saved profile revision or content exists."""

    _attr_translation_key = "profile_present"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Hide state when saved profile storage could not be read."""
        return self.coordinator.profile_storage_healthy

    @property
    def is_on(self) -> bool:
        record = self._entry.runtime_data.profile_record
        return record.revision > 0 or bool(record.desired_profile)
