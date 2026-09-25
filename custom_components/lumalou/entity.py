"""Shared entities for Lumalou."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class LumalouEntity(Entity):
    """Base class that reads only the coordinator snapshot.

    Identifiers derive from the entry unique ID, the signed-device
    fingerprint. The BLE address is only a device connection.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Stable unique-ID suffix; defaults to the translation key.
    _unique_key: str | None = None

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._coordinator = entry.runtime_data.coordinator
        key = self._unique_key or self._attr_translation_key
        self._attr_unique_id = f"{entry.unique_id}_{key}"

    @property
    def coordinator(self) -> Any:
        return self._coordinator

    @property
    def snapshot(self) -> dict[str, Any] | None:
        data = self._coordinator.data
        return data if isinstance(data, dict) else None

    def snapshot_value(self, key: str) -> Any:
        """Return one observed value, or None while no snapshot exists."""
        data = self.snapshot
        return data.get(key) if data is not None else None

    @property
    def available(self) -> bool:
        return bool(self._coordinator.available and self.snapshot is not None)

    @property
    def device_info(self) -> DeviceInfo:
        # The model stays generic: enrollment verifies a signed device key,
        # not a retail SKU.
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._entry.unique_id))},
            connections={(CONNECTION_BLUETOOTH, self._coordinator.address)},
            manufacturer="Fisher-Price",
            model="Lumalou",
            name=self._coordinator.device_name,
            sw_version=self._coordinator.sw_version,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class LumalouControlEntity(LumalouEntity):
    """An entity that writes to the device.

    It is unavailable until the coordinator has verified the protocol through a
    complete fresh profile read; the coordinator still enforces the same gate.
    """

    @property
    def available(self) -> bool:
        return super().available and self._coordinator.protocol_verified is True
