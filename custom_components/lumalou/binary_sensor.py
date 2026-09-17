"""Lumalou diagnostic binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import LumalouEntity


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up saved-profile diagnostic binary sensors."""
    async_add_entities(
        [
            LumalouProfilePendingBinarySensor(entry),
            LumalouProfilePresentBinarySensor(entry),
        ]
    )


class LumalouProfilePendingBinarySensor(LumalouEntity, BinarySensorEntity):
    """Report whether saved profile changes still need verification."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Profile pending", "profile_pending")
        self._attr_translation_key = "profile_pending"

    @property
    def available(self) -> bool:
        """Saved metadata remains available without a BLE state snapshot."""
        return True

    @property
    def is_on(self) -> bool:
        return self._entry.runtime_data.profile_record.pending


class LumalouProfilePresentBinarySensor(LumalouEntity, BinarySensorEntity):
    """Report whether any saved profile revision or content exists."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Profile present", "profile_present")
        self._attr_translation_key = "profile_present"

    @property
    def available(self) -> bool:
        """Hide state when saved profile storage could not be read."""
        return self.coordinator.profile_storage_healthy

    @property
    def is_on(self) -> bool:
        record = self._entry.runtime_data.profile_record
        return record.revision > 0 or bool(record.desired_profile)
