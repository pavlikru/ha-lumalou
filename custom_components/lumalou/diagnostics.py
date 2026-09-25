"""Privacy-preserving diagnostics for Lumalou."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from . import LumalouConfigEntry
from .const import CONF_AUTO_RESTORE, DEFAULT_AUTO_RESTORE


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


def _diagnostic_value(value: Any) -> bool | int | float | str | None:
    """Convert a known scalar to a JSON-safe diagnostic value."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return type(value).__name__


def _selected_attributes(obj: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """Return only explicitly allowlisted attributes."""
    if obj is None:
        return {}
    return {
        name: _diagnostic_value(getattr(obj, name))
        for name in names
        if hasattr(obj, name)
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LumalouConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without addresses, profiles, or raw BLE data."""
    coordinator = entry.runtime_data.coordinator
    profile_record = coordinator.profile_record

    restore_needed = getattr(coordinator, "restore_needed", None)
    last_restore = getattr(coordinator, "last_restore_result", None)
    return {
        "versions": {
            "home_assistant": HA_VERSION,
            "lumalou_library": _package_version("lumalou-gld09"),
        },
        "entry": {
            "source": entry.source,
            "auto_restore_enabled": bool(
                entry.options.get(CONF_AUTO_RESTORE, DEFAULT_AUTO_RESTORE)
            ),
        },
        "connection": _selected_attributes(
            coordinator,
            (
                "present",
                "available",
                "protocol_verified",
                "sw_version",
                "last_clock_offset",
                "last_clock_sync",
                "clock_sync_paused",
            ),
        ),
        "restore": {
            "needed": restore_needed is not None,
            # Block names only (e.g. "routines"), never schedule values.
            **(
                {
                    "changed_blocks": list(restore_needed.changed_blocks),
                    **_selected_attributes(
                        restore_needed,
                        (
                            "detected_at",
                            "reset",
                            "auto_restore_attempts",
                            "auto_restore_exhausted",
                        ),
                    ),
                }
                if restore_needed is not None
                else {}
            ),
            "last_result": (
                {
                    "planned_steps": list(last_restore.planned_steps),
                    "applied_steps": list(last_restore.applied_steps),
                    "mismatched_blocks": list(last_restore.mismatched_blocks),
                    **_selected_attributes(
                        last_restore,
                        (
                            "revision",
                            "automatic",
                            "verified",
                            "clock_synced",
                            "error",
                            "finished_at",
                        ),
                    ),
                }
                if last_restore is not None
                else None
            ),
        },
        "profile": {
            "present": bool(profile_record.revision or profile_record.desired_profile),
            **_selected_attributes(
                profile_record,
                (
                    "schema_version",
                    "revision",
                    "verified_revision",
                    "is_verified",
                    "pending",
                    "sync_status",
                    "maintenance",
                ),
            ),
            "last_error": profile_record.last_error,
        },
    }
