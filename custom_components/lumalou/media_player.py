"""Lumalou audio media player."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.exceptions import ServiceValidationError

from lumalou import Audio, Song  # type: ignore[attr-defined]

from .const import DOMAIN
from .entity import LumalouControlEntity

# Lower-case source names double as translation keys.
AUDIOS = {audio.name.lower(): int(audio) for audio in Audio}
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the Lumalou media player."""
    async_add_entities([LumalouMediaPlayer(entry)])


class LumalouMediaPlayer(LumalouControlEntity, MediaPlayerEntity):
    """Expose only controls implemented by the toy (no streaming claims).

    HomeKit Bridge maps this speaker to a switch accessory: on plays the sleep
    playlist and off stops audio. Volume and source stay Home Assistant-only.
    """

    _attr_translation_key = "audio"
    _unique_key = "media_player"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, entry: Any) -> None:
        super().__init__(entry)
        self._attr_source_list = list(AUDIOS)

    def _song(self) -> Song | None:
        song = self.snapshot_value("currentSong")
        try:
            return Song(int(song)) if song is not None else None
        except ValueError:
            return None

    @property
    def state(self) -> MediaPlayerState | None:
        status = self.snapshot_value("musicStatus")
        if status is None:
            return None
        return MediaPlayerState.PLAYING if status else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        volume = self.snapshot_value("currentVolume")
        return None if volume is None else max(0.0, min(1.0, int(volume) / 9))

    @property
    def source(self) -> str | None:
        audio = self.snapshot_value("currentAudio")
        if audio is not None:
            try:
                return Audio(int(audio)).name.lower()
            except ValueError:
                return None
        song = self._song()
        # Only these built-in sounds have a one-to-one Audio mapping.
        name = song.name.lower() if song is not None else None
        return name if name in AUDIOS else None

    @property
    def media_title(self) -> str | None:
        song = self._song()
        return song.name.replace("_", " ").title() if song is not None else None

    async def async_turn_on(self) -> None:
        await self.coordinator.async_play(int(Audio.SLEEP_PLAYLIST))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_stop_audio()

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_volume(round(max(0.0, min(1.0, volume)) * 9))

    async def async_volume_up(self) -> None:
        current = self.snapshot_value("currentVolume")
        if current is not None:
            await self.coordinator.async_set_volume(min(9, int(current) + 1))

    async def async_volume_down(self) -> None:
        current = self.snapshot_value("currentVolume")
        if current is not None:
            await self.coordinator.async_set_volume(max(0, int(current) - 1))

    async def async_select_source(self, source: str) -> None:
        if source not in AUDIOS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_source",
                translation_placeholders={"source": source},
            )
        await self.coordinator.async_play(AUDIOS[source])
