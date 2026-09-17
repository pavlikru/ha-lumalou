"""Lumalou diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .entity import LumalouEntity
from .models import SYNC_STATUSES


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up diagnostic sensors."""
    async_add_entities(
        [
            LumalouAvailabilitySensor(entry),
            LumalouFirmwareSensor(entry),
            LumalouProfileRevisionSensor(entry),
            LumalouProfileSyncStatusSensor(entry),
            LumalouProfileLastErrorSensor(entry),
        ]
    )


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


class _LumalouProfileDiagnosticSensor(LumalouEntity, SensorEntity):
    """Expose saved-profile metadata without exposing profile contents."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Saved metadata remains available without a BLE state snapshot."""
        return True

    @property
    def profile_record(self) -> Any:
        return self._entry.runtime_data.profile_record


class LumalouProfileRevisionSensor(_LumalouProfileDiagnosticSensor):
    """Report the saved profile revision."""

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Profile revision", "profile_revision")
        self._attr_translation_key = "profile_revision"

    @property
    def native_value(self) -> int:
        return self.profile_record.revision


class LumalouProfileSyncStatusSensor(_LumalouProfileDiagnosticSensor):
    """Report saved-profile synchronization state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(SYNC_STATUSES)

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Profile sync status", "profile_sync_status")
        self._attr_translation_key = "profile_sync_status"

    @property
    def native_value(self) -> str:
        return self.profile_record.sync_status


class LumalouProfileLastErrorSensor(_LumalouProfileDiagnosticSensor):
    """Report the current symbolic saved-profile error, if any."""

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Profile last error", "profile_last_error")
        self._attr_translation_key = "profile_last_error"

    @property
    def native_value(self) -> str | None:
        return self.profile_record.last_error
