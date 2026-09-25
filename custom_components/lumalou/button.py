"""Lumalou buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import LumalouEntity


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou buttons."""
    async_add_entities([LumalouSyncClockButton(entry), LumalouRefreshButton(entry)])


class LumalouSyncClockButton(LumalouEntity, ButtonEntity):
    """Synchronize the device clock."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Synchronize clock", "sync_clock")
        self._attr_translation_key = "sync_clock"

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()


class LumalouRefreshButton(LumalouEntity, ButtonEntity):
    """Request a fresh state snapshot."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Refresh", "refresh")
        self._attr_translation_key = "refresh"

    @property
    def available(self) -> bool:
        """Allow a refresh attempt to recover an offline device."""
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
