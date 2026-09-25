"""Lumalou select entities."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from lumalou import (  # type: ignore[attr-defined]
    ClockFormat,
    LightDuration,
    PlaylistDuration,
)

from .entity import LumalouControlEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou selects."""
    async_add_entities(
        [
            LumalouLightDurationSelect(entry),
            LumalouPlaylistDurationSelect(entry),
            LumalouClockFormatSelect(entry),
        ]
    )


class _LumalouEnumSelect(LumalouControlEntity, SelectEntity):
    """Select one member of a persistent device setting enum."""

    _attr_entity_category = EntityCategory.CONFIG
    _enum: type[IntEnum]
    _state_key: str

    def __init__(self, entry: Any) -> None:
        super().__init__(entry)
        # Lower-case option names double as translation keys.
        self._attr_options = [item.name.lower() for item in self._enum]

    @property
    def current_option(self) -> str | None:
        value = self.snapshot_value(self._state_key)
        try:
            return self._enum(int(value)).name.lower() if value is not None else None
        except ValueError:
            return None

    async def async_select_option(self, option: str) -> None:
        # Home Assistant has already validated the option against `options`.
        await self._async_set(int(self._enum[option.upper()]))

    async def _async_set(self, value: int) -> None:
        # The timers' translation keys are their profile keys.
        await self.coordinator.async_set_level(self._attr_translation_key, value)


class LumalouLightDurationSelect(_LumalouEnumSelect):
    """Select the persistent light timer."""

    _attr_translation_key = "light_duration"
    _enum = LightDuration
    _state_key = "lightDuration"


class LumalouPlaylistDurationSelect(_LumalouEnumSelect):
    """Select the persistent audio playlist timer."""

    _attr_translation_key = "playlist_duration"
    _enum = PlaylistDuration
    _state_key = "playlistDuration"


class LumalouClockFormatSelect(_LumalouEnumSelect):
    """Select the 12-hour or 24-hour clock."""

    _attr_translation_key = "clock_format"
    _enum = ClockFormat
    _state_key = "clockFormat"

    async def _async_set(self, value: int) -> None:
        await self.coordinator.async_set_clock_settings(clock_format=value)
