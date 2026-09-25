"""Restore-choice Repairs flow tests; no Bluetooth access."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou import async_setup_entry, async_unload_entry
from custom_components.lumalou.const import (
    CONF_DEVICE_FINGERPRINT,
    DOMAIN,
    ISSUE_ID_PROFILE_RESTORE_NEEDED,
)
from custom_components.lumalou.coordinator import ProfileRestoreError
from custom_components.lumalou.repairs import (
    ProfileRestoreRepairFlow,
    async_create_fix_flow,
)
from custom_components.lumalou.restore import ProfileRestoreResult, RestoreNeeded

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_repair_factory_rejects_mismatched_entry_scope(
    hass: HomeAssistant,
) -> None:
    with pytest.raises(ValueError, match="Invalid Lumalou"):
        await async_create_fix_flow(
            hass,
            f"other_{ISSUE_ID_PROFILE_RESTORE_NEEDED}",
            {"entry_id": "synthetic"},
        )


def _need(**changes) -> RestoreNeeded:
    return RestoreNeeded(
        revision=3,
        changed_blocks=("routines", "volume"),
        detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        **changes,
    )


def _restore_entry(hass: HomeAssistant, need: RestoreNeeded | None):
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic"})
    coordinator = SimpleNamespace(
        restore_needed=need,
        async_restore_profile=AsyncMock(),
        async_read_profile_snapshot=AsyncMock(return_value=({"volume": 1}, 3)),
        async_accept_device_profile=AsyncMock(),
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    entry.add_to_hass(hass)
    return entry, coordinator


async def _restore_flow(hass: HomeAssistant, entry) -> ProfileRestoreRepairFlow:
    issue_id = f"{entry.entry_id}_{ISSUE_ID_PROFILE_RESTORE_NEEDED}"
    flow = await async_create_fix_flow(hass, issue_id, {"entry_id": entry.entry_id})
    assert isinstance(flow, ProfileRestoreRepairFlow)
    flow.hass = hass
    return flow


async def test_restore_repair_offers_both_choices(hass: HomeAssistant) -> None:
    entry, _coordinator = _restore_entry(hass, _need())
    flow = await _restore_flow(hass, entry)

    menu = await flow.async_step_init()

    assert menu["type"].value == "menu"
    assert menu["menu_options"] == ["restore", "keep_device"]
    form = await flow.async_step_restore()
    assert form["type"].value == "form"
    assert form["step_id"] == "restore"


async def test_restore_repair_restores_the_detected_revision(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _restore_entry(hass, _need())
    flow = await _restore_flow(hass, entry)

    result = await flow.async_step_restore({})

    assert result["type"].value == "create_entry"
    coordinator.async_restore_profile.assert_awaited_once_with(3, confirmed=True)
    coordinator.async_accept_device_profile.assert_not_awaited()


async def test_restore_repair_keeps_device_settings(hass: HomeAssistant) -> None:
    entry, coordinator = _restore_entry(hass, _need())
    flow = await _restore_flow(hass, entry)

    result = await flow.async_step_keep_device({})

    assert result["type"].value == "create_entry"
    coordinator.async_accept_device_profile.assert_awaited_once_with(
        {"volume": 1}, 3, confirmed=True
    )
    coordinator.async_restore_profile.assert_not_awaited()


@pytest.mark.parametrize("step", ["restore", "keep_device"])
async def test_restore_repair_failure_keeps_the_form(
    hass: HomeAssistant, step: str
) -> None:
    entry, coordinator = _restore_entry(hass, _need())
    outcome = ProfileRestoreResult(
        revision=3, automatic=False, planned_steps=(), applied_steps=(), verified=False
    )
    coordinator.async_restore_profile.side_effect = ProfileRestoreError("no", outcome)
    coordinator.async_read_profile_snapshot.side_effect = HomeAssistantError("away")
    flow = await _restore_flow(hass, entry)

    result = await getattr(flow, f"async_step_{step}")({})

    assert result["type"].value == "form"
    assert result["errors"] == {"base": f"{step}_failed"}


async def test_restore_repair_aborts_when_resolved_or_unloaded(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _restore_entry(hass, None)
    flow = await _restore_flow(hass, entry)
    result = await flow.async_step_restore({})
    assert result["reason"] == "not_needed"
    coordinator.async_restore_profile.assert_not_awaited()

    unloaded = MockConfigEntry(domain=DOMAIN, data={"address": "other"})
    unloaded.add_to_hass(hass)
    flow = await _restore_flow(hass, unloaded)
    result = await flow.async_step_keep_device({})
    assert result["reason"] == "entry_not_loaded"


async def test_restore_issue_follows_coordinator_state(hass: HomeAssistant) -> None:
    """The issue exists exactly while a mismatch is reported, and not after unload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"address": "synthetic", CONF_DEVICE_FINGERPRINT: "a" * 64},
    )
    entry.add_to_hass(hass)
    listeners = []
    coordinator = Mock(
        restore_needed=None,
        async_setup=AsyncMock(),
        async_start=Mock(),
        async_shutdown=AsyncMock(),
    )
    coordinator.async_add_listener = lambda listener: (
        listeners.append(listener) or (lambda: None)
    )
    issue_id = f"{entry.entry_id}_{ISSUE_ID_PROFILE_RESTORE_NEEDED}"
    registry = ir.async_get(hass)
    with (
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await async_setup_entry(hass, entry)
        assert registry.async_get_issue(DOMAIN, issue_id) is None

        coordinator.restore_needed = _need(auto_restore_attempts=1)
        for listener in listeners:
            listener()
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.is_fixable is True
        assert issue.severity is ir.IssueSeverity.WARNING
        assert issue.translation_key == ISSUE_ID_PROFILE_RESTORE_NEEDED
        assert issue.translation_placeholders == {"block_count": "2", "attempts": "1"}
        assert issue.data == {"entry_id": entry.entry_id}

        coordinator.restore_needed = _need(
            auto_restore_attempts=2, auto_restore_exhausted=True
        )
        for listener in listeners:
            listener()
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue.severity is ir.IssueSeverity.ERROR

        coordinator.restore_needed = None
        for listener in listeners:
            listener()
        assert registry.async_get_issue(DOMAIN, issue_id) is None

        coordinator.restore_needed = _need()
        for listener in listeners:
            listener()
        assert registry.async_get_issue(DOMAIN, issue_id) is not None
        assert await async_unload_entry(hass, entry)
        assert registry.async_get_issue(DOMAIN, issue_id) is None
