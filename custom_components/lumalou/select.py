"""Lumalou select entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity

from lumalou import LightDuration  # type: ignore[attr-defined]

from .entity import LumalouEntity


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up Lumalou selects."""
    async_add_entities([LumalouLightDurationSelect(entry)])


class LumalouLightDurationSelect(LumalouEntity, SelectEntity):
    """Select the persistent light timer."""

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Light duration", "light_duration")
        self._attr_translation_key = "light_duration"
        self._options = [item.name for item in LightDuration]

    @property
    def options(self) -> list[str]:
        return self._options

    @property
    def current_option(self) -> str | None:
        value = self.snapshot_value("lightDuration")
        try:
            return LightDuration(int(value)).name if value is not None else None
        except ValueError:
            return None

    def snapshot_value(self, key: str) -> Any:
        data = self.snapshot
        return data.get(key) if data is not None else None

    async def async_select_option(self, option: str) -> None:
        if option not in self._options:
            raise ValueError(option)
        await self.coordinator.async_set_light_duration(int(LightDuration[option]))
