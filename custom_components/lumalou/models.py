"""Validated persistent subset; never a claim of a complete device backup."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .const import PROFILE_SCHEMA_VERSION

if TYPE_CHECKING:
    from .coordinator import LumalouCoordinator

PROFILE_RANGES = {
    "brightness": (1, 9),
    "color": (0, 9),
    "light_duration": (0, 5),
    "volume": (0, 9),
    "playlist_duration": (0, 6),
}
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


def validate_profile(value: Any) -> dict[str, Any]:
    """Copy a strict, explicitly supported subset; absent fields remain absent."""
    if not isinstance(value, dict) or set(value) - {*PROFILE_RANGES, "playlist"}:
        raise ProfileValidationError("Unsupported profile fields")
    result = deepcopy(value)
    for name, item in result.items():
        if name == "playlist":
            if not isinstance(item, list) or len(item) > 12:
                raise ProfileValidationError("Playlist must contain at most 12 songs")
            for song in item:
                validate_integer(song, 1, 18, "song")
        else:
            validate_integer(item, *PROFILE_RANGES[name], name)
    return result


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

    def to_dict(self) -> dict[str, Any]:
        """Return a detached serializable record."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> ProfileRecord:
        """Reject unsupported schemas and corrupt synchronization metadata."""
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ProfileValidationError("Invalid profile record")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
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
            if not isinstance(previous, dict) or set(previous) != {
                "revision",
                "profile",
            }:
                raise ProfileValidationError("Invalid previous revision")
            validate_integer(previous["revision"], 0, data["revision"], "previous")
            previous["profile"] = validate_profile(previous["profile"])
        return cls(**data)


@dataclass
class LumalouRuntimeData:
    """Runtime belongs to one config entry; it is not persistent storage."""

    coordinator: LumalouCoordinator

    @property
    def profile_record(self) -> ProfileRecord:
        """Expose the current record after immutable revision replacement."""
        return self.coordinator.profile_record


RuntimeData = LumalouRuntimeData
