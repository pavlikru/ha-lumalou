"""Shared entities for Lumalou."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
)
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class LumalouEntity(Entity):
    """Base class that reads only the coordinator snapshot."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, name: str, key: str | None = None) -> None:
        self._entry = entry
        self._coordinator = entry.runtime_data.coordinator
        suffix = key or name.lower().replace(" ", "_")
        self._attr_unique_id = f"{self._coordinator.address}_{suffix}"

    @property
    def coordinator(self) -> Any:
        return self._coordinator

    @property
    def snapshot(self) -> dict[str, Any] | None:
        data = self._coordinator.data
        return data if isinstance(data, dict) else None

    @property
    def available(self) -> bool:
        return bool(self._coordinator.available and self.snapshot is not None)

    @property
    def device_info(self) -> DeviceInfo:
        product_code = getattr(self._coordinator, "product_code", None)
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self._coordinator.address)},
            manufacturer="Fisher-Price",
            model=f"Lumalou ({product_code})" if product_code else "Lumalou",
            name=self._coordinator.device_name,
            sw_version=self._coordinator.sw_version,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._remove_listener = self._coordinator.async_add_listener(
            self.async_write_ha_state
        )
        self.async_on_remove(self._remove_listener)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
