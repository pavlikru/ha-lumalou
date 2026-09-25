"""Saved-profile Repairs flow tests; no Bluetooth access."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.const import DOMAIN, ISSUE_ID_PROFILE_STORAGE
from custom_components.lumalou.repairs import (
    CONF_CONFIRM,
    CONF_PROFILE_JSON,
    ProfileStorageRepairFlow,
    async_create_fix_flow,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _payload() -> dict:
    return {
        "schema_version": 1,
        "scope": "supported_subset",
        "profile": {"playlist": [12, 2, 2], "volume": 1},
    }


async def test_repair_validates_then_confirms_profile_recovery(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic"})
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(async_recover_profile=AsyncMock())
    )
    entry.add_to_hass(hass)
    issue_id = f"{entry.entry_id}_{ISSUE_ID_PROFILE_STORAGE}"
    flow = await async_create_fix_flow(hass, issue_id, {"entry_id": entry.entry_id})
    assert isinstance(flow, ProfileStorageRepairFlow)
    flow.hass = hass

    invalid = await flow.async_step_init({CONF_PROFILE_JSON: "not JSON"})
    assert invalid["type"].value == "form"
    assert invalid["step_id"] == "import_profile"
    assert invalid["errors"] == {"base": "invalid_profile"}

    confirm = await flow.async_step_import_profile(
        {CONF_PROFILE_JSON: json.dumps({"current_revision": 7, "profile": _payload()})}
    )
    assert confirm["step_id"] == "confirm"
    assert confirm["description_placeholders"] == {"field_count": "2"}

    missing_confirmation = await flow.async_step_confirm({CONF_CONFIRM: False})
    assert missing_confirmation["errors"] == {"base": "confirmation_required"}
    entry.runtime_data.coordinator.async_recover_profile.assert_not_awaited()

    complete = await flow.async_step_confirm({CONF_CONFIRM: True})
    assert complete["type"].value == "create_entry"
    entry.runtime_data.coordinator.async_recover_profile.assert_awaited_once_with(
        _payload(), confirmed=True
    )


async def test_repair_keeps_issue_open_when_recovery_save_fails(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic"})
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(
            async_recover_profile=AsyncMock(side_effect=HomeAssistantError("disk full"))
        )
    )
    entry.add_to_hass(hass)
    flow = ProfileStorageRepairFlow(entry.entry_id)
    flow.hass = hass
    await flow.async_step_import_profile({CONF_PROFILE_JSON: json.dumps(_payload())})

    result = await flow.async_step_confirm({CONF_CONFIRM: True})

    assert result["type"].value == "form"
    assert result["errors"] == {"base": "recovery_failed"}


async def test_repair_factory_rejects_mismatched_entry_scope(
    hass: HomeAssistant,
) -> None:
    with pytest.raises(ValueError, match="Invalid Lumalou"):
        await async_create_fix_flow(
            hass,
            f"other_{ISSUE_ID_PROFILE_STORAGE}",
            {"entry_id": "synthetic"},
        )


async def test_repair_aborts_cleanly_when_entry_is_not_loaded(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic"})
    entry.add_to_hass(hass)
    flow = ProfileStorageRepairFlow(entry.entry_id)
    flow.hass = hass
    await flow.async_step_import_profile({CONF_PROFILE_JSON: json.dumps(_payload())})

    result = await flow.async_step_confirm({CONF_CONFIRM: True})

    assert result["type"].value == "abort"
    assert result["reason"] == "entry_not_loaded"


async def test_real_repairs_manager_removes_issue_only_after_verified_recovery(
    hass: HomeAssistant,
) -> None:
    """Exercise platform loading, confirmation, and issue lifecycle together."""
    # Repairs lazily imports platforms only for loaded integrations. Mark this
    # custom domain loaded without starting HA's real USB/Bluetooth stack.
    hass.config.top_level_components.add(DOMAIN)
    assert await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic"})
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(async_recover_profile=AsyncMock())
    )
    entry.add_to_hass(hass)
    issue_id = f"{entry.entry_id}_{ISSUE_ID_PROFILE_STORAGE}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        data={"entry_id": entry.entry_id},
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_ID_PROFILE_STORAGE,
    )
    manager = hass.data["repairs"]["flow_manager"]

    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["step_id"] == "import_profile"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    result = await manager.async_configure(
        result["flow_id"], {CONF_PROFILE_JSON: json.dumps(_payload())}
    )
    assert result["step_id"] == "confirm"
    result = await manager.async_configure(result["flow_id"], {CONF_CONFIRM: True})

    assert result["type"].value == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    entry.runtime_data.coordinator.async_recover_profile.assert_awaited_once_with(
        _payload(), confirmed=True
    )
