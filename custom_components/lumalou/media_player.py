"""Lumalou audio media player."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)

from lumalou import Audio, Song  # type: ignore[attr-defined]

from .entity import LumalouEntity

AUDIOS = {audio.name: int(audio) for audio in Audio}
SONGS = {song.name: int(song) for song in Song if song.name != "NO_SONG"}


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the Lumalou media player."""
    async_add_entities([LumalouMediaPlayer(entry)])


class LumalouMediaPlayer(LumalouEntity, MediaPlayerEntity):
    """Expose only controls implemented by the toy (no streaming claims)."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, entry: Any) -> None:
        super().__init__(entry, "Audio", "media_player")
        self._attr_translation_key = "audio"

    def value(self, key: str) -> Any:
        data = self.snapshot
        return data.get(key) if data is not None else None

    @property
    def state(self) -> MediaPlayerState | None:
        status = self.value("musicStatus")
        if status is None:
            return None
        return MediaPlayerState.PLAYING if status else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        volume = self.value("currentVolume")
        return None if volume is None else max(0.0, min(1.0, int(volume) / 9))

    @property
    def source(self) -> str | None:
        audio = self.value("currentAudio")
        if audio is not None:
            try:
                return Audio(int(audio)).name
            except ValueError:
                return None
        song = self.value("currentSong")
        try:
            song_name = Song(int(song)).name if song is not None else None
        except ValueError:
            return None
        # Only these built-in sounds have a one-to-one Audio mapping.
        return song_name if song_name in AUDIOS else None

    @property
    def source_list(self) -> list[str]:
        return list(AUDIOS)

    @property
    def media_title(self) -> str | None:
        song = self.value("currentSong")
        try:
            return Song(int(song)).name if song is not None else None
        except ValueError:
            return None

    async def async_turn_on(self) -> None:
        await self.coordinator.async_play(int(Audio.SLEEP_PLAYLIST))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_stop_audio()

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_volume(round(max(0.0, min(1.0, volume)) * 9))

    async def async_volume_up(self) -> None:
        current = self.value("currentVolume")
        if current is not None:
            await self.coordinator.async_set_volume(min(9, int(current) + 1))

    async def async_volume_down(self) -> None:
        current = self.value("currentVolume")
        if current is not None:
            await self.coordinator.async_set_volume(max(0, int(current) - 1))

    async def async_select_source(self, source: str) -> None:
        if source not in AUDIOS:
            raise ValueError(f"Unsupported Lumalou source: {source}")
        await self.coordinator.async_play(AUDIOS[source])
