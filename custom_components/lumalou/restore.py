"""Pure mapping between the HA logical profile and upstream typed blocks.

No I/O happens here. ``profile_from_readback`` converts one strict session's
typed responses into a complete logical profile. ``build_restore_steps`` maps
a profile diff onto payloads from the pinned upstream command builders only,
in one deterministic write order:

1. ``clock_settings`` - display configuration only, no activation.
2. ``playlist``.
3. ``light_and_sound.*`` - light and playlist timers, volume and LED
   brightness. Verified on hardware not to switch light or sound on.
4. ``routine_settings.music`` (music byte + reward sounds), then
   ``routine_settings.volume``.
5. ``sleepy_times``, ``ready_to_rise.times``, ``alarm``, then
   ``routines.<day>`` Sunday..Saturday - schedule data.
6. ``ready_to_rise.enabled``, ``routine_settings.enabled`` - activation flags
   last, only after every schedule they activate has been written.

Light colour, play/stop, soother, nap and routine start are never part of a
restore, so it never turns light or sound on. Current device time is not part
of the profile either; callers set it from Home Assistant before step 1,
after a fresh read. Only steps whose logical value differs from the fresh
device readback are produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lumalou import commands  # type: ignore[attr-defined]
from lumalou.profile import ClockSettings, MusicPlaylist, RoutineMusicSettings
from lumalou.responses import CurrentDate
from lumalou.schedules import (
    ClockTime,
    DailyRoutine,
    RoutineTask,
    WeeklyAlarms,
    WeeklyTimes,
)

from .models import (
    DAYS,
    FULL_PROFILE_FIELDS,
    LIVE_BLOCK,
    ProfileValidationError,
    require_complete_profile,
)

_WEEK_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class RestoreStep:
    """One allowlisted setter write, named by the logical block it restores."""

    name: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ProfileRestoreResult:
    """Outcome of one executor run; ``verified`` is only set by fresh readback."""

    revision: int
    automatic: bool
    planned_steps: tuple[str, ...]
    applied_steps: tuple[str, ...]
    verified: bool
    mismatched_blocks: tuple[str, ...] = ()
    clock_synced: bool = False
    error: str | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RestoreNeeded:
    """A verified saved profile differs from a fresh complete device read.

    ``reset`` means the device clock was also far off (a power loss resets
    the clock and every setting); only such an event is restored
    automatically. Without it, only differences outside the light and sound
    block are reported (for example a change made in the Fisher-Price app).
    """

    revision: int
    changed_blocks: tuple[str, ...]
    detected_at: datetime
    reset: bool = False
    auto_restore_attempts: int = 0
    auto_restore_exhausted: bool = False


def _wire_boolean(value: Any, name: str) -> bool:
    """Decode only explicit boolean wire values; reject other nibbles."""
    if type(value) is not int or value not in (0, 1):
        raise ProfileValidationError(f"Unsupported {name} value")
    return bool(value)


def _read_time(value: Any) -> dict[str, int] | None:
    return None if value is None else {"hour": value.hour, "minute": value.minute}


def _read_week(value: Any) -> dict[str, Any]:
    if len(value.days) != len(DAYS):
        raise ProfileValidationError("Weekly response is not typed")
    return {day: _read_time(value.days[index]) for index, day in enumerate(DAYS)}


def _read_playlist(slots: Any) -> list[int]:
    """Canonical intent is ordered songs; interior holes cannot be represented."""
    if len(slots) != 12:
        raise ProfileValidationError("Playlist response is not typed")
    songs = [int(song) for song in slots]
    count = next((index for index, song in enumerate(songs) if song == 0), 12)
    if any(songs[count:]):
        raise ProfileValidationError("Playlist has unsupported interior empty slots")
    return songs[:count]


def _read_routine(value: Any) -> dict[str, Any]:
    if len(value.slots) != 12:
        raise ProfileValidationError("Daily routine response is not typed")
    return {
        "time": _read_time(value.time),
        "slots": [
            None if slot is None else {"step": slot.step, "task": slot.task}
            for slot in value.slots
        ],
    }


def profile_from_readback(
    state: dict[str, int],
    *,
    playlist: MusicPlaylist,
    clock: ClockSettings,
    ready_to_rise: WeeklyTimes,
    sleepy_times: WeeklyTimes,
    alarms: WeeklyAlarms,
    routines: dict[str, DailyRoutine],
) -> dict[str, Any]:
    """Build a complete logical profile from one session's typed responses.

    Routine settings, the Ready-to-Rise flag and the light and sound block
    come from GLOBAL_STATE (nibbles; brightness is kept while the light is
    off). The dedicated clock settings response must agree with
    GLOBAL_STATE or the read is rejected.
    """
    expected_clock = (
        _wire_boolean(state["clockDisplay"], "clock display"),
        state["clockBrightness"],
        state["clockFormat"],
    )
    if (clock.display_on, clock.brightness, clock.format) != expected_clock:
        raise ProfileValidationError("Clock settings disagree with GLOBAL_STATE")
    return require_complete_profile(
        {
            "playlist": _read_playlist(playlist.slots),
            "clock_settings": {
                "display": clock.display_on,
                "brightness": clock.brightness,
                "format": clock.format,
            },
            "routine_settings": {
                "enabled": _wire_boolean(state["routineModeStatus"], "routine mode"),
                "music": state["routineMusicStatus"],
                "volume": state["routineVolume"],
                "task_reward_sfx": state["taskRewardSfx"],
                "routine_reward_sfx": state["routineRewardSfx"],
            },
            "ready_to_rise": {
                "enabled": _wire_boolean(state["ready2RiseStatus"], "ready-to-rise"),
                "times": _read_week(ready_to_rise),
            },
            "sleepy_times": _read_week(sleepy_times),
            "alarm": {
                "days": {
                    day: int(alarms.days[index]) for index, day in enumerate(DAYS)
                },
                "sound": alarms.sound,
            },
            "routines": {day: _read_routine(routines[day]) for day in DAYS},
            LIVE_BLOCK: {
                "volume": state["currentVolume"],
                "light_brightness": state["lightBrightness"],
                "light_duration": state["lightDuration"],
                "playlist_duration": state["playlistDuration"],
            },
        }
    )


def changed_blocks(
    desired: Any, observed: Any, *, live: bool = True
) -> tuple[str, ...]:
    """Return sorted top-level blocks where two complete profiles differ.

    ``live=False`` skips the light and sound block, which also changes in
    everyday use.
    """
    target = require_complete_profile(desired)
    actual = require_complete_profile(observed)
    fields = FULL_PROFILE_FIELDS if live else FULL_PROFILE_FIELDS - {LIVE_BLOCK}
    return tuple(sorted(field for field in fields if target[field] != actual[field]))


def _time(value: dict[str, int] | None) -> ClockTime | None:
    return None if value is None else ClockTime(value["hour"], value["minute"])


def _week(value: dict[str, dict[str, int] | None]) -> WeeklyTimes:
    return WeeklyTimes(tuple(_time(value[day]) for day in DAYS))


def _routine(value: dict[str, Any]) -> DailyRoutine:
    return DailyRoutine(
        _time(value["time"]),
        tuple(
            None if slot is None else RoutineTask(slot["step"], slot["task"])
            for slot in value["slots"]
        ),
    )


def build_restore_steps(desired: Any, observed: Any) -> tuple[RestoreStep, ...]:
    """Return the minimal ordered setter writes that turn observed into desired."""
    want = require_complete_profile(desired)
    have = require_complete_profile(observed)
    steps: list[RestoreStep] = []

    def differs(*path: str) -> bool:
        target, actual = want, have
        for key in path:
            target, actual = target[key], actual[key]
        return bool(target != actual)

    clock = want["clock_settings"]
    if differs("clock_settings"):
        steps.append(
            RestoreStep(
                "clock_settings",
                commands.set_clock_settings(
                    clock["display"], clock["brightness"], clock["format"]
                ),
            )
        )
    if differs("playlist"):
        steps.append(
            RestoreStep(
                "playlist",
                commands.set_music_playlist(MusicPlaylist.from_songs(want["playlist"])),
            )
        )

    levels = want[LIVE_BLOCK]
    for key, setter in (
        ("light_duration", commands.set_light_duration),
        ("playlist_duration", commands.set_playlist_duration),
        ("volume", commands.set_volume),
        ("light_brightness", commands.set_led_brightness),
    ):
        if differs(LIVE_BLOCK, key):
            steps.append(RestoreStep(f"{LIVE_BLOCK}.{key}", setter(levels[key])))

    routine = want["routine_settings"]
    if any(
        differs("routine_settings", key)
        for key in ("music", "task_reward_sfx", "routine_reward_sfx")
    ):
        steps.append(
            RestoreStep(
                "routine_settings.music",
                commands.set_routine_music_settings(
                    RoutineMusicSettings(
                        routine["music"],
                        routine["task_reward_sfx"],
                        routine["routine_reward_sfx"],
                    )
                ),
            )
        )
    if differs("routine_settings", "volume"):
        steps.append(
            RestoreStep(
                "routine_settings.volume",
                commands.set_routine_volume(routine["volume"]),
            )
        )

    if differs("sleepy_times"):
        steps.append(
            RestoreStep(
                "sleepy_times", commands.set_sleepy_times(_week(want["sleepy_times"]))
            )
        )
    if differs("ready_to_rise", "times"):
        steps.append(
            RestoreStep(
                "ready_to_rise.times",
                commands.set_r2r_times(_week(want["ready_to_rise"]["times"])),
            )
        )
    if differs("alarm"):
        alarm = want["alarm"]
        steps.append(
            RestoreStep(
                "alarm",
                commands.set_r2r_alarms(
                    WeeklyAlarms(
                        tuple(alarm["days"][day] for day in DAYS), alarm["sound"]
                    )
                ),
            )
        )
    for day in DAYS:
        if differs("routines", day):
            steps.append(
                RestoreStep(
                    f"routines.{day}",
                    commands.set_day_routine(day, _routine(want["routines"][day])),
                )
            )

    if differs("ready_to_rise", "enabled"):
        steps.append(
            RestoreStep(
                "ready_to_rise.enabled",
                commands.set_r2r_status(want["ready_to_rise"]["enabled"]),
            )
        )
    if differs("routine_settings", "enabled"):
        steps.append(
            RestoreStep(
                "routine_settings.enabled",
                commands.set_routine_status(routine["enabled"]),
            )
        )
    return tuple(steps)


def device_weekday(moment: datetime) -> int:
    """Map Python Monday=0 to the device's Sunday=0 weekday."""
    return (moment.weekday() + 1) % 7


def set_current_date_payload(moment: datetime) -> bytes:
    """Build the device clock setter from a Home Assistant local time."""
    return commands.set_current_date(
        moment.hour, moment.minute, moment.second, device_weekday(moment)
    )


def clock_offset_seconds(device: CurrentDate, moment: datetime) -> int:
    """Return the circular week-time distance between device and HA clocks."""
    device_seconds = (
        device.weekday * 86400 + device.hour * 3600 + device.minute * 60 + device.second
    )
    local_seconds = (
        device_weekday(moment) * 86400
        + moment.hour * 3600
        + moment.minute * 60
        + moment.second
    )
    difference = (device_seconds - local_seconds) % _WEEK_SECONDS
    return min(difference, _WEEK_SECONDS - difference)
