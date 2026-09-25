"""Strict logical model for persistent settings, independent of BLE codecs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .const import PROFILE_SCHEMA_VERSION

if TYPE_CHECKING:
    from .coordinator import LumalouCoordinator

DAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)
# Settings the device also changes in everyday use (volume and brightness
# buttons). A power loss resets them too, so they are restored, but they are
# compared with the device only when the clock shows a reset.
LIVE_BLOCK = "light_and_sound"
FULL_PROFILE_FIELDS = frozenset(
    {
        "playlist",
        "clock_settings",
        "routine_settings",
        "ready_to_rise",
        "sleepy_times",
        "alarm",
        "routines",
        LIVE_BLOCK,
    }
)
# Only persistent configuration. Intentionally absent: light colour and
# on/off, audio playing state, current date/time, nap state, executing alarm,
# current routine step and task status.
# The `alarm` block is only the established seven alarm nibbles plus sound nibble.
# GLOBAL_STATE reports routine music and volume as 4-bit values, so only
# 0..15 can be verified after a restore.
ROUTINE_NIBBLE_MAX = 15
SYNC_STATUSES = frozenset({"empty", "saved", "pending", "applying", "error"})


class ProfileValidationError(ValueError):
    """Profile contains unsupported or invalid data."""


class RevisionConflictError(ValueError):
    """An editor attempted to overwrite a newer saved revision."""


def validate_integer(value: Any, minimum: int, maximum: int, name: str) -> int:
    """Reject coercions, including booleans, instead of silently wrapping."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProfileValidationError(f"Invalid {name}")
    return value


def is_device_fingerprint(value: Any) -> bool:
    """Return whether a value is a 64-character lowercase hex fingerprint."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_mapping(value: Any, fields: set[str] | frozenset[str], name: str) -> dict:
    """Require a JSON object with exactly the documented fields."""
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ProfileValidationError(f"Invalid {name}")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ProfileValidationError(f"Invalid {name}")
    return value


def _time(value: Any, name: str) -> dict[str, int] | None:
    """Validate a time; null is the proven no-scheduled-time wire sentinel."""
    if value is None:
        return None
    data = _strict_mapping(value, {"hour", "minute"}, name)
    return {
        "hour": validate_integer(data["hour"], 0, 23, f"{name} hour"),
        "minute": validate_integer(data["minute"], 0, 59, f"{name} minute"),
    }


def _week(value: Any, name: str) -> dict[str, dict[str, int] | None]:
    data = _strict_mapping(value, set(DAYS), name)
    return {day: _time(data[day], f"{name} {day}") for day in DAYS}


def _playlist(value: Any) -> list[int]:
    if not isinstance(value, list) or len(value) > 12:
        raise ProfileValidationError("Playlist must contain at most 12 songs")
    return [validate_integer(song, 1, 12, "playlist song") for song in value]


def _clock_settings(value: Any) -> dict[str, Any]:
    data = _strict_mapping(value, {"display", "brightness", "format"}, "clock settings")
    return {
        "display": _boolean(data["display"], "clock display"),
        "brightness": validate_integer(data["brightness"], 0, 9, "clock brightness"),
        "format": validate_integer(data["format"], 0, 1, "clock format"),
    }


def _routine_settings(value: Any) -> dict[str, Any]:
    fields = {
        "enabled",
        "music",
        "volume",
        "task_reward_sfx",
        "routine_reward_sfx",
    }
    data = _strict_mapping(value, fields, "routine settings")
    return {
        "enabled": _boolean(data["enabled"], "routine mode"),
        # The setters carry a byte, but GLOBAL_STATE reports both as nibbles.
        "music": validate_integer(
            data["music"], 0, ROUTINE_NIBBLE_MAX, "routine music"
        ),
        "volume": validate_integer(
            data["volume"], 0, ROUTINE_NIBBLE_MAX, "routine volume"
        ),
        "task_reward_sfx": validate_integer(
            data["task_reward_sfx"], 0, 15, "task reward sound"
        ),
        "routine_reward_sfx": validate_integer(
            data["routine_reward_sfx"], 0, 15, "routine reward sound"
        ),
    }


def _light_and_sound(value: Any) -> dict[str, int]:
    fields = {"volume", "light_brightness", "light_duration", "playlist_duration"}
    data = _strict_mapping(value, fields, "light and sound settings")
    return {
        "volume": validate_integer(data["volume"], 0, 9, "volume"),
        "light_brightness": validate_integer(
            data["light_brightness"], 0, 9, "light brightness"
        ),
        "light_duration": validate_integer(
            data["light_duration"], 0, 5, "light duration"
        ),
        "playlist_duration": validate_integer(
            data["playlist_duration"], 0, 6, "playlist duration"
        ),
    }


def _ready_to_rise(value: Any) -> dict[str, Any]:
    data = _strict_mapping(value, {"enabled", "times"}, "ready-to-rise settings")
    return {
        "enabled": _boolean(data["enabled"], "ready-to-rise status"),
        "times": _week(data["times"], "ready-to-rise times"),
    }


def _alarm(value: Any) -> dict[str, Any]:
    data = _strict_mapping(value, {"days", "sound"}, "alarm block")
    days = _strict_mapping(data["days"], set(DAYS), "alarm days")
    return {
        "days": {
            day: validate_integer(days[day], 0, 10, f"{day} alarm") for day in DAYS
        },
        "sound": validate_integer(data["sound"], 0, 15, "alarm sound"),
    }


def _routine(value: Any, day: str) -> dict[str, Any]:
    data = _strict_mapping(value, {"time", "slots"}, f"{day} routine")
    slots = data["slots"]
    if not isinstance(slots, list) or len(slots) != 12:
        raise ProfileValidationError("A daily routine must contain exactly 12 slots")
    validated_slots: list[dict[str, int] | None] = []
    for index, slot in enumerate(slots):
        if slot is None:
            validated_slots.append(None)
            continue
        item = _strict_mapping(slot, {"step", "task"}, f"{day} slot {index}")
        validated_slots.append(
            {
                "step": validate_integer(item["step"], 1, 12, "routine step"),
                "task": validate_integer(item["task"], 0, 11, "routine task"),
            }
        )
    return {
        "time": _time(data["time"], f"{day} routine time"),
        "slots": validated_slots,
    }


def routine_from_tasks(time: dict[str, int] | None, tasks: list[int]) -> dict[str, Any]:
    """Build one day routine: one task per step, in the given order.

    Tasks are ids 1..11 without duplicates (the device reports task status by
    task id). No tasks means no routine that day, so the time is dropped. A
    routine without a time only starts manually.
    """
    if not isinstance(tasks, list) or len(tasks) > 12:
        raise ProfileValidationError("A routine holds at most 12 tasks")
    for task in tasks:
        validate_integer(task, 1, 11, "routine task")
    if len(set(tasks)) != len(tasks):
        raise ProfileValidationError("A routine task can appear only once")
    slots: list[dict[str, int] | None] = [
        {"step": step, "task": task} for step, task in enumerate(tasks, 1)
    ]
    return _routine(
        {
            "time": time if tasks else None,
            "slots": slots + [None] * (12 - len(slots)),
        },
        "edited",
    )


def routine_task_ids(routine: dict[str, Any]) -> list[int]:
    """Return a day routine's named tasks in slot order."""
    return [
        slot["task"]
        for slot in routine["slots"]
        if slot is not None and slot["task"] != 0
    ]


def _routines(value: Any) -> dict[str, dict[str, Any]]:
    data = _strict_mapping(value, set(DAYS), "daily routines")
    return {day: _routine(data[day], day) for day in DAYS}


def validate_profile(value: Any) -> dict[str, Any]:
    """Copy a strict profile; absent top-level fields remain unknown."""
    if not isinstance(value, dict) or set(value) - FULL_PROFILE_FIELDS:
        raise ProfileValidationError("Unsupported profile fields")
    result = deepcopy(value)
    for name, item in result.items():
        if name == "playlist":
            result[name] = _playlist(item)
        elif name == "clock_settings":
            result[name] = _clock_settings(item)
        elif name == "routine_settings":
            result[name] = _routine_settings(item)
        elif name == "ready_to_rise":
            result[name] = _ready_to_rise(item)
        elif name == "sleepy_times":
            result[name] = _week(item, "sleepy times")
        elif name == "alarm":
            result[name] = _alarm(item)
        elif name == LIVE_BLOCK:
            result[name] = _light_and_sound(item)
        else:
            result[name] = _routines(item)
    return result


def profile_is_complete(value: Any) -> bool:
    """Return whether every persistent block is structurally present and valid."""
    try:
        result = validate_profile(value)
    except ProfileValidationError:
        return False
    return set(result) == FULL_PROFILE_FIELDS


def require_complete_profile(value: Any) -> dict[str, Any]:
    """Require structural completeness; this does not prove restore safety."""
    result = validate_profile(value)
    if set(result) != FULL_PROFILE_FIELDS:
        raise ProfileValidationError("Persistent profile is incomplete")
    return result


def export_profile_payload(value: Any) -> dict[str, Any]:
    """Wrap a saved profile in the versioned export envelope."""
    result = validate_profile(value)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "scope": "persistent_profile",
        "profile": result,
    }


def import_profile_payload(value: Any) -> dict[str, Any]:
    """Validate the export envelope and return its profile."""
    data = _strict_mapping(
        value, {"schema_version", "scope", "profile"}, "profile import"
    )
    version = data["schema_version"]
    if type(version) is not int:
        raise ProfileValidationError("Unsupported profile import schema")
    if version == PROFILE_SCHEMA_VERSION and data["scope"] == "persistent_profile":
        return validate_profile(data["profile"])
    raise ProfileValidationError("Unsupported profile import schema")


@dataclass(frozen=True)
class ProfileRecord:
    """Persisted user intent and synchronization metadata for a single entry."""

    schema_version: int = PROFILE_SCHEMA_VERSION
    revision: int = 0
    desired_profile: dict[str, Any] = field(default_factory=dict)
    previous: dict[str, Any] | None = None
    verified_revision: int | None = None
    pending: bool = False
    sync_status: str = "empty"
    last_error: str | None = None
    maintenance: bool = False
    # Private device-key fingerprint of the session that verified
    # `verified_revision`.
    verified_fingerprint: str | None = None

    @property
    def is_verified(self) -> bool:
        """Whether the current revision was verified against a device."""
        return (
            self.verified_revision is not None
            and self.verified_revision == self.revision
            and self.verified_fingerprint is not None
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached serializable record."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> ProfileRecord:
        """Reject unsupported schemas and corrupt synchronization metadata."""
        return cls(**_validate_record(value))


def _validate_record(value: Any) -> dict[str, Any]:
    """Validate record metadata and both saved profiles."""
    if not isinstance(value, dict) or set(value) != set(
        ProfileRecord.__dataclass_fields__
    ):
        raise ProfileValidationError("Invalid profile record")
    fingerprint = value.get("verified_fingerprint")
    if fingerprint is not None and not is_device_fingerprint(fingerprint):
        raise ProfileValidationError("Invalid verified device identity")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != PROFILE_SCHEMA_VERSION
    ):
        raise ProfileValidationError("Unsupported profile schema")
    validate_integer(value["revision"], 0, 2**63 - 1, "revision")
    verified = value["verified_revision"]
    if verified is not None:
        validate_integer(verified, 0, value["revision"], "verified revision")
    if value["sync_status"] not in SYNC_STATUSES:
        raise ProfileValidationError("Invalid synchronization status")
    if any(type(value[key]) is not bool for key in ("maintenance", "pending")):
        raise ProfileValidationError("Invalid boolean metadata")
    if value["last_error"] is not None and not isinstance(value["last_error"], str):
        raise ProfileValidationError("Invalid error metadata")
    data = deepcopy(value)
    data["desired_profile"] = validate_profile(data["desired_profile"])
    previous = data["previous"]
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {"revision", "profile"}:
            raise ProfileValidationError("Invalid previous revision")
        validate_integer(previous["revision"], 0, data["revision"], "previous")
        previous["profile"] = validate_profile(previous["profile"])
    return data


@dataclass
class LumalouRuntimeData:
    """Runtime belongs to one config entry; it is not persistent storage."""

    coordinator: LumalouCoordinator
