"""Lumalou diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .entity import LumalouEntity
from .models import SYNC_STATUSES

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up diagnostic sensors."""
    async_add_entities(
        [LumalouFirmwareSensor(entry), LumalouProfileSyncStatusSensor(entry)]
    )


class LumalouFirmwareSensor(LumalouEntity, SensorEntity):
    """Report firmware version without performing I/O."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        return self.coordinator.sw_version


class LumalouProfileSyncStatusSensor(LumalouEntity, SensorEntity):
    """Report saved-profile synchronization state (details are in diagnostics)."""

    _attr_translation_key = "profile_sync_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(SYNC_STATUSES)

    @property
    def available(self) -> bool:
        """Saved metadata remains available without a BLE state snapshot."""
        return True

    @property
    def native_value(self) -> str:
        return self.coordinator.profile_record.sync_status
