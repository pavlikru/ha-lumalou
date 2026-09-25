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

from lumalou import Audio, Song, Stage  # type: ignore[attr-defined]

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

    Source ``sleep_playlist`` is the soother: sleep music plus a
    colour-cycling light. Off stops audio only; the soother light stays on
    and is shown by the light entity. HomeKit Bridge maps this speaker to a
    switch accessory. Volume and source stay Home Assistant-only.
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
        """The playing source, as far as the device state tells it.

        Built-in sounds (songs 13..18) name themselves. Both playlists play
        songs 1..12: the source Home Assistant started is used while it
        plays; otherwise the soother (sleep playlist, also started by the
        remote) is recognised by its READY stage (hardware). Else unknown.
        """
        song = self._song()
        name = song.name.lower() if song is not None else None
        if name in AUDIOS:
            return name
        if not self.snapshot_value("musicStatus"):
            return None
        started = self.coordinator.playing_source
        if started in (Audio.SLEEP_PLAYLIST, Audio.CUSTOM_PLAYLIST):
            return Audio(started).name.lower()
        if self.snapshot_value("currentStage") == Stage.READY:
            return "sleep_playlist"
        return None

    @property
    def media_title(self) -> str | None:
        song = self._song()
        return song.name.replace("_", " ").title() if song is not None else None

    async def async_turn_on(self) -> None:
        await self.coordinator.async_play(int(Audio.SLEEP_PLAYLIST))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_stop_audio()

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_level(
            "volume", round(max(0.0, min(1.0, volume)) * 9)
        )

    async def async_volume_up(self) -> None:
        current = self.snapshot_value("currentVolume")
        if current is not None:
            await self.coordinator.async_set_level("volume", min(9, int(current) + 1))

    async def async_volume_down(self) -> None:
        current = self.snapshot_value("currentVolume")
        if current is not None:
            await self.coordinator.async_set_level("volume", max(0, int(current) - 1))

    async def async_select_source(self, source: str) -> None:
        if source not in AUDIOS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_source",
                translation_placeholders={"source": source},
            )
        await self.coordinator.async_play(AUDIOS[source])
