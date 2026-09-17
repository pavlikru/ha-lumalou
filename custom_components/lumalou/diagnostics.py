"""Privacy-preserving diagnostics for Lumalou."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from . import LumalouConfigEntry


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
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    profile_record = getattr(runtime, "profile_record", None)
    last_error = getattr(profile_record, "last_error", None)
    profile_present = profile_record is not None and bool(
        getattr(profile_record, "revision", 0)
        or getattr(profile_record, "desired_profile", None)
    )

    return {
        "versions": {
            "home_assistant": HA_VERSION,
            "lumalou_library": _package_version("lumalou"),
        },
        "entry": {
            "source": entry.source,
            "auto_restore_enabled": bool(entry.options.get("auto_restore", False)),
        },
        "connection": _selected_attributes(
            coordinator,
            (
                "present",
                "available",
                "connected",
                "connectable",
                "sw_version",
                "last_restore_at",
                "last_verified_restore_at",
            ),
        )
        | {
            "last_error_type": type(last_error).__name__ if last_error else None,
        },
        "profile": {
            "present": profile_present,
            **_selected_attributes(
                profile_record,
                (
                    "schema_version",
                    "revision",
                    "verified_revision",
                    "last_verified_revision",
                    "pending",
                    "pending_sync",
                    "sync_status",
                    "maintenance",
                ),
            ),
            "last_error": _diagnostic_value(last_error),
        },
    }
