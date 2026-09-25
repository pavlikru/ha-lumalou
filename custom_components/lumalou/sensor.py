"""Lumalou routine and diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .const import ROUTINE_TASKS
from .entity import LumalouEntity
from .models import SYNC_STATUSES

PARALLEL_UPDATES = 0
ROUTINE_PHASES = ("off", "ready", "in_progress", "completed")


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up routine and diagnostic sensors."""
    async_add_entities(
        [
            LumalouFirmwareSensor(entry),
            LumalouProfileSyncStatusSensor(entry),
            LumalouRoutineSensor(entry),
            LumalouCurrentTaskSensor(entry),
        ]
    )


class LumalouFirmwareSensor(LumalouEntity, SensorEntity):
    """Report firmware version without performing I/O."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Known from advertisements even while no session is connected."""
        return self.coordinator.sw_version is not None

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
        return self.coordinator.sync_status


class LumalouRoutineSensor(LumalouEntity, SensorEntity):
    """Routine progress from pushed GLOBAL_STATE and task status.

    ``ready`` is the silent preview (all icons blink) before the first task.
    """

    _attr_translation_key = "routine"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, entry: Any) -> None:
        super().__init__(entry)
        self._attr_options = list(ROUTINE_PHASES)

    @property
    def native_value(self) -> str | None:
        return self.coordinator.routine_phase


class LumalouCurrentTaskSensor(LumalouEntity, SensorEntity):
    """The routine task the device currently shows."""

    _attr_translation_key = "current_task"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, entry: Any) -> None:
        super().__init__(entry)
        self._attr_options = ["none", *ROUTINE_TASKS]

    @property
    def native_value(self) -> str | None:
        return self.coordinator.current_task
