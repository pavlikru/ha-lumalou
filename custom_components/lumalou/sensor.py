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
        [
            LumalouFirmwareSensor(entry),
            LumalouProfileRevisionSensor(entry),
            LumalouProfileVerifiedRevisionSensor(entry),
            LumalouProfileSyncStatusSensor(entry),
            LumalouProfileLastErrorSensor(entry),
        ]
    )


class LumalouFirmwareSensor(LumalouEntity, SensorEntity):
    """Report firmware version without performing I/O."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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

    _attr_translation_key = "profile_revision"

    @property
    def native_value(self) -> int:
        return self.profile_record.revision


class LumalouProfileVerifiedRevisionSensor(_LumalouProfileDiagnosticSensor):
    """Report the last profile revision with verified device state."""

    _attr_translation_key = "profile_verified_revision"

    @property
    def native_value(self) -> int | None:
        return self.profile_record.verified_revision


class LumalouProfileSyncStatusSensor(_LumalouProfileDiagnosticSensor):
    """Report saved-profile synchronization state."""

    _attr_translation_key = "profile_sync_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(SYNC_STATUSES)

    @property
    def native_value(self) -> str:
        return self.profile_record.sync_status


class LumalouProfileLastErrorSensor(_LumalouProfileDiagnosticSensor):
    """Report the current symbolic saved-profile error, if any."""

    _attr_translation_key = "profile_last_error"

    @property
    def native_value(self) -> str | None:
        return self.profile_record.last_error
