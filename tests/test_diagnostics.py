"""Diagnostics privacy tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from lumalou import ClockFormat
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.const import DOMAIN
from custom_components.lumalou.diagnostics import (
    _diagnostic_value,
    _package_version,
    _selected_attributes,
    async_get_config_entry_diagnostics,
)
from custom_components.lumalou.models import LumalouRuntimeData, ProfileRecord
from custom_components.lumalou.restore import ProfileRestoreResult, RestoreNeeded


async def test_diagnostics_are_allowlisted_and_redacted(
    hass: HomeAssistant,
) -> None:
    """Diagnostics never serialize address, profile, or raw payload fields."""
    coordinator = SimpleNamespace(
        address="PRIVATE-MAC",
        available=True,
        connected=False,
        connectable=True,
        sw_version="1.2.3",
        raw_payload=b"PRIVATE-RAW",
        session_key="PRIVATE-KEY",
        profile_record=ProfileRecord(
            revision=3,
            desired_profile={"playlist": [1, 2], "volume": 4},
            verified_revision=2,
            pending=True,
            sync_status="partial",
            last_error="ble_apply",
            maintenance=False,
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"address": "PRIVATE-MAC", "secret": "PRIVATE-KEY"},
        unique_id="PRIVATE-MAC",
        options={"auto_restore": False},
    )
    entry.runtime_data = LumalouRuntimeData(coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)
    assert diagnostics["profile"]["present"] is True
    assert diagnostics["profile"]["revision"] == 3
    assert diagnostics["profile"]["verified_revision"] == 2
    assert diagnostics["profile"]["last_error"] == "ble_apply"
    assert diagnostics["connection"]["available"] is True
    assert diagnostics["restore"] == {"needed": False, "last_result": None}
    for forbidden in (
        "PRIVATE-MAC",
        "PRIVATE-RAW",
        "PRIVATE-KEY",
        "playlist",
        "desired_profile",
        "address",
        "raw_payload",
        "session_key",
    ):
        assert forbidden not in serialized


async def test_empty_record_is_not_a_saved_profile(hass: HomeAssistant) -> None:
    """Default metadata alone does not claim that a profile exists."""
    coordinator = SimpleNamespace(
        available=False,
        sw_version=None,
        profile_record=ProfileRecord(),
    )
    entry = MockConfigEntry(domain=DOMAIN, options={})
    entry.runtime_data = LumalouRuntimeData(coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["profile"]["present"] is False
    assert diagnostics["profile"]["revision"] == 0
    assert diagnostics["entry"]["auto_restore_enabled"] is False


async def test_restore_state_is_summarized_without_identity_or_values(
    hass: HomeAssistant,
) -> None:
    """Restore diagnostics carry block names and counters, never values/keys."""
    detected = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    coordinator = SimpleNamespace(
        available=True,
        present=True,
        protocol_verified=True,
        sw_version="0.3.7",
        last_clock_offset=12,
        last_clock_sync=detected,
        clock_sync_paused=True,
        device_fingerprint="f" * 64,
        restore_needed=RestoreNeeded(1, ("routines", "volume"), detected, 2, True),
        last_restore_result=ProfileRestoreResult(
            revision=1,
            automatic=True,
            planned_steps=("volume", "routines.monday"),
            applied_steps=("volume",),
            verified=False,
            error="restore_write",
            finished_at=detected,
        ),
        profile_record=ProfileRecord(
            revision=1,
            desired_profile={"volume": 4},
            verified_revision=1,
            verified_fingerprint="f" * 64,
        ),
    )
    entry = MockConfigEntry(domain=DOMAIN, options={"auto_restore": True})
    entry.runtime_data = LumalouRuntimeData(coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert diagnostics["entry"]["auto_restore_enabled"] is True
    assert diagnostics["connection"]["last_clock_sync"] == detected.isoformat()
    assert diagnostics["connection"]["clock_sync_paused"] is True
    assert diagnostics["restore"]["needed"] is True
    assert diagnostics["restore"]["changed_blocks"] == ["routines", "volume"]
    assert diagnostics["restore"]["auto_restore_exhausted"] is True
    assert diagnostics["restore"]["last_result"]["applied_steps"] == ["volume"]
    assert diagnostics["restore"]["last_result"]["error"] == "restore_write"
    assert diagnostics["profile"]["is_verified"] is True
    assert "f" * 64 not in serialized
    assert "fingerprint" not in serialized


def test_diagnostic_values_are_json_safe() -> None:
    assert _diagnostic_value(ClockFormat.H24) == 1
    assert _diagnostic_value(Enum("Kind", {"A": "a"}).A) == "a"
    assert _diagnostic_value(object()) == "object"
    assert _selected_attributes(None, ("x",)) == {}
    with patch(
        "custom_components.lumalou.diagnostics.version",
        side_effect=PackageNotFoundError,
    ):
        assert _package_version("lumalou-gld09") == "unknown"
