"""Lumalou select entities."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from lumalou import LightDuration, PlaylistDuration  # type: ignore[attr-defined]

from .entity import LumalouControlEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou selects."""
    async_add_entities(
        [LumalouLightDurationSelect(entry), LumalouPlaylistDurationSelect(entry)]
    )


class _LumalouEnumSelect(LumalouControlEntity, SelectEntity):
    """Select one member of a persistent device timer enum."""

    _attr_entity_category = EntityCategory.CONFIG
    _enum: type[IntEnum]
    _state_key: str
    _setter: str

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
        value = int(self._enum[option.upper()])
        await getattr(self.coordinator, self._setter)(value)


class LumalouLightDurationSelect(_LumalouEnumSelect):
    """Select the persistent light timer."""

    _attr_translation_key = "light_duration"
    _enum = LightDuration
    _state_key = "lightDuration"
    _setter = "async_set_light_duration"


class LumalouPlaylistDurationSelect(_LumalouEnumSelect):
    """Select the persistent audio playlist timer."""

    _attr_translation_key = "playlist_duration"
    _enum = PlaylistDuration
    _state_key = "playlistDuration"
    _setter = "async_set_playlist_duration"
