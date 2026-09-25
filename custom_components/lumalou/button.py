"""Lumalou buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import LumalouControlEntity, LumalouEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou buttons."""
    async_add_entities([LumalouSyncClockButton(entry), LumalouRefreshButton(entry)])


class LumalouSyncClockButton(LumalouControlEntity, ButtonEntity):
    """Synchronize the device clock."""

    _attr_translation_key = "sync_clock"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()


class LumalouRefreshButton(LumalouEntity, ButtonEntity):
    """Request a fresh read-only state snapshot."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Allow a refresh attempt to recover an offline device."""
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
