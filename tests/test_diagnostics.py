"""Diagnostics privacy tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.const import DOMAIN
from custom_components.lumalou.diagnostics import async_get_config_entry_diagnostics
from custom_components.lumalou.models import LumalouRuntimeData, ProfileRecord


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
