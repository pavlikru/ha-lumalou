"""Lumalou light entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)

from lumalou import Color  # type: ignore[attr-defined]

from .entity import LumalouControlEntity

# Lower-case effect names double as translation keys.
COLORS = {color.name.lower(): int(color) for color in Color}
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the Lumalou light."""
    async_add_entities([LumalouLight(entry)])


class LumalouLight(LumalouControlEntity, LightEntity):
    """The night light; the fixed colour palette is exposed as effects.

    The device keeps brightness and colour while the light is off, so both
    always show the values the next "on" uses.
    """

    _attr_translation_key = "light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, entry: Any) -> None:
        super().__init__(entry)
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_effect_list = list(COLORS)

    @property
    def is_on(self) -> bool | None:
        value = self.snapshot_value("lightStatus")
        return None if value is None else bool(value)

    @property
    def brightness(self) -> int | None:
        value = self.snapshot_value("lightBrightness")
        return None if value is None else round(int(value) * 255 / 9)

    @property
    def effect(self) -> str | None:
        value = self.snapshot_value("lightColor")
        try:
            return Color(int(value)).name.lower() if value is not None else None
        except ValueError:
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        effect = kwargs.get(ATTR_EFFECT)
        color = COLORS.get(effect) if isinstance(effect, str) else None
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            brightness = max(1, min(9, round(int(brightness) * 9 / 255)))
        await self.coordinator.async_turn_on_light(brightness, color)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off_light()
