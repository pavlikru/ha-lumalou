"""Strict logical model for persistent settings, independent of BLE codecs."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .const import PROFILE_SCHEMA_VERSION

if TYPE_CHECKING:
    from .coordinator import LumalouCoordinator

PROFILE_RANGES = {
    "brightness": (0, 9),
    "color": (0, 9),
    "light_duration": (0, 5),
    "volume": (0, 9),
    "playlist_duration": (0, 6),
}
V1_PROFILE_RANGES = {**PROFILE_RANGES, "brightness": (1, 9)}
V1_PROFILE_FIELDS = frozenset(
    {"brightness", "color", "light_duration", "volume", "playlist_duration", "playlist"}
)
DAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)
FULL_PROFILE_FIELDS = frozenset(
    {
        *PROFILE_RANGES,
        "playlist",
        "clock_settings",
        "routine_settings",
        "ready_to_rise",
        "sleepy_times",
        "alarm",
        "routines",
    }
)
# Intentionally absent: current date/time, light/audio on/off, current song,
# timer remainder, nap state/alarm, executing alarm, current routine step, and
# task status. Those are transient or lack a persistent setter/readback contract.
# The `alarm` block is only the established seven alarm nibbles plus sound nibble.
# GLOBAL_STATE reports routine music and volume as 4-bit values, so only
# 0..15 can be verified after a restore. Saved records written by earlier
# editors may still hold a full setter byte (see `validate_profile`).
ROUTINE_NIBBLE_MAX = 15
LEGACY_ROUTINE_BYTE_MAX = 255
SYNC_STATUSES = frozenset({"empty", "saved", "pending", "applying", "partial", "error"})


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


def _routine_settings(value: Any, byte_max: int) -> dict[str, Any]:
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
        "music": validate_integer(data["music"], 0, byte_max, "routine music"),
        "volume": validate_integer(data["volume"], 0, byte_max, "routine volume"),
        "task_reward_sfx": validate_integer(
            data["task_reward_sfx"], 0, 15, "task reward sound"
        ),
        "routine_reward_sfx": validate_integer(
            data["routine_reward_sfx"], 0, 15, "routine reward sound"
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


def _routines(value: Any) -> dict[str, dict[str, Any]]:
    data = _strict_mapping(value, set(DAYS), "daily routines")
    return {day: _routine(data[day], day) for day in DAYS}


def _validate_v1_profile(value: Any) -> dict[str, Any]:
    """Validate the exact v1 subset without inventing newly supported blocks."""
    if not isinstance(value, dict) or set(value) - V1_PROFILE_FIELDS:
        raise ProfileValidationError("Unsupported profile fields")
    result = deepcopy(value)
    for name, item in result.items():
        if name == "playlist":
            result[name] = _playlist(item)
        else:
            validate_integer(item, *V1_PROFILE_RANGES[name], name)
    return result


def validate_profile(value: Any, *, stored: bool = False) -> dict[str, Any]:
    """Copy a strict v2 profile; absent top-level fields remain unknown.

    `stored=True` is only for loading and merging already saved records: it
    still accepts the full routine music/volume byte that older editors
    allowed, so such a record loads instead of requiring storage recovery.
    Every new value, import and restore uses the device-verifiable range.
    """
    if not isinstance(value, dict) or set(value) - FULL_PROFILE_FIELDS:
        raise ProfileValidationError("Unsupported profile fields")
    result = deepcopy(value)
    for name, item in result.items():
        if name == "playlist":
            result[name] = _playlist(item)
        elif name == "clock_settings":
            result[name] = _clock_settings(item)
        elif name == "routine_settings":
            byte_max = LEGACY_ROUTINE_BYTE_MAX if stored else ROUTINE_NIBBLE_MAX
            result[name] = _routine_settings(item, byte_max)
        elif name == "ready_to_rise":
            result[name] = _ready_to_rise(item)
        elif name == "sleepy_times":
            result[name] = _week(item, "sleepy times")
        elif name == "alarm":
            result[name] = _alarm(item)
        elif name == "routines":
            result[name] = _routines(item)
        else:
            validate_integer(item, *PROFILE_RANGES[name], name)
    return result


def _validate_stored_profile(value: Any) -> dict[str, Any]:
    return validate_profile(value, stored=True)


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
    """Version an exported partial/full profile without relabelling v2 as v1."""
    result = validate_profile(value)
    if set(result) <= V1_PROFILE_FIELDS:
        return {
            "schema_version": 1,
            "scope": "supported_subset",
            "profile": result,
        }
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "scope": "persistent_profile",
        "profile": result,
    }


def import_profile_payload(value: Any) -> dict[str, Any]:
    """Validate both the legacy subset envelope and current profile envelope."""
    data = _strict_mapping(
        value, {"schema_version", "scope", "profile"}, "profile import"
    )
    version = data["schema_version"]
    if type(version) is not int:
        raise ProfileValidationError("Unsupported profile import schema")
    if version == 1 and data["scope"] == "supported_subset":
        return _validate_v1_profile(data["profile"])
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
    # `verified_revision`. Absent in records written before 0.2.0 support.
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
        return cls(
            **_validate_record(value, PROFILE_SCHEMA_VERSION, _validate_stored_profile)
        )


@dataclass(frozen=True, slots=True)
class ProfileReconciliationPlan:
    """Compare two complete persistent profiles without choosing wire order.

    `changed_blocks` is a stable display/test order only; it is not an approved
    BLE setter sequence. Current time and transient actions are outside this
    persistent-profile plan.
    """

    revision: int
    changed_blocks: tuple[str, ...]

    @property
    def already_matches(self) -> bool:
        """Return whether the complete snapshots differ in no persistent block."""
        return not self.changed_blocks


def plan_profile_reconciliation(
    record: ProfileRecord,
    observed_profile: Any,
    *,
    expected_revision: int,
) -> ProfileReconciliationPlan:
    """Plan a revision-bound full-profile diff without any I/O or writes.

    The observed input must be a fresh, complete profile returned by the
    strict read path. This function cannot establish freshness itself and does
    not authorize applying setters or claim hardware verification.
    """
    validate_integer(expected_revision, 0, 2**63 - 1, "expected revision")
    if expected_revision != record.revision:
        raise RevisionConflictError("The saved profile changed")
    target = require_complete_profile(record.desired_profile)
    observed = require_complete_profile(observed_profile)
    changed_blocks = tuple(
        sorted(
            field for field in FULL_PROFILE_FIELDS if target[field] != observed[field]
        )
    )
    return ProfileReconciliationPlan(
        revision=record.revision,
        changed_blocks=changed_blocks,
    )


def _validate_record(
    value: Any, schema_version: int, profile_validator: Callable[[Any], dict[str, Any]]
) -> dict[str, Any]:
    """Validate record metadata while allowing an explicit profile schema."""
    fields = set(ProfileRecord.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) not in (
        fields,
        fields - {"verified_fingerprint"},
    ):
        raise ProfileValidationError("Invalid profile record")
    fingerprint = value.get("verified_fingerprint")
    if fingerprint is not None and not is_device_fingerprint(fingerprint):
        raise ProfileValidationError("Invalid verified device identity")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != schema_version
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
    data.setdefault("verified_fingerprint", None)
    data["desired_profile"] = profile_validator(data["desired_profile"])
    previous = data["previous"]
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {"revision", "profile"}:
            raise ProfileValidationError("Invalid previous revision")
        validate_integer(previous["revision"], 0, data["revision"], "previous")
        previous["profile"] = profile_validator(previous["profile"])
    return data


def migrate_v1_record(value: Any) -> ProfileRecord:
    """Upgrade only validated v1 subset data and preserve all record metadata."""
    data = _validate_record(value, 1, _validate_v1_profile)
    data["schema_version"] = PROFILE_SCHEMA_VERSION
    return ProfileRecord.from_dict(data)


@dataclass
class LumalouRuntimeData:
    """Runtime belongs to one config entry; it is not persistent storage."""

    coordinator: LumalouCoordinator

    @property
    def profile_record(self) -> ProfileRecord:
        """Expose the current record after immutable revision replacement."""
        return self.coordinator.profile_record


RuntimeData = LumalouRuntimeData
