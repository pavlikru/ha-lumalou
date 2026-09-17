"""Lumalou light entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
)

from lumalou import Color  # type: ignore[attr-defined]

from .entity import LumalouEntity

COLORS = {color.name: int(color) for color in Color}


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the Lumalou light."""
    async_add_entities([LumalouLight(entry)])


class LumalouLight(LumalouEntity, LightEntity):
    """The night light, with the device's fixed colour palette."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_effect = None

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Light", "light")
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_effect_list = list(COLORS)
        self._attr_translation_key = "light"

    @property
    def is_on(self) -> bool | None:
        value = self.snapshot_value("lightStatus")
        return None if value is None else bool(value)

    @property
    def brightness(self) -> int | None:
        value = self.snapshot_value("lightBrightness")
        return None if value is None else max(1, min(255, round(int(value) * 255 / 9)))

    @property
    def effect(self) -> str | None:
        value = self.snapshot_value("lightColor")
        if value is None:
            return None
        try:
            return Color(int(value)).name
        except ValueError:
            return None

    def snapshot_value(self, key: str) -> Any:
        data = self.snapshot
        return data.get(key) if data is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        effect = kwargs.get(ATTR_EFFECT)
        color = COLORS.get(effect) if isinstance(effect, str) else None
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            brightness = max(1, min(9, round(int(brightness) * 9 / 255)))
        await self.coordinator.async_set_light(True, brightness, color)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_light(False)
