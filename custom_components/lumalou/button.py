"""Lumalou buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import LumalouControlEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou buttons."""
    async_add_entities([LumalouSyncClockButton(entry)])


class LumalouSyncClockButton(LumalouControlEntity, ButtonEntity):
    """Synchronize the device clock."""

    _attr_translation_key = "sync_clock"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()
