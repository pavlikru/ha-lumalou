"""Config entry 1.1 -> 1.2 migration: registry identifiers follow the unique ID."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou import async_migrate_entry, async_remove_entry
from custom_components.lumalou.const import (
    CONF_DEVICE_FINGERPRINT,
    DOMAIN,
    ISSUE_ID_IDENTITY_ENROLLMENT,
)

ADDRESS = "AA:BB:CC:DD:EE:01"
FINGERPRINT = "a" * 64


def _entry(hass: HomeAssistant, *, enrolled: bool) -> MockConfigEntry:
    """Add a 1.1 entry whose registry entries are still keyed by address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Nursery",
        unique_id=FINGERPRINT if enrolled else ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            **({CONF_DEVICE_FINGERPRINT: FINGERPRINT} if enrolled else {}),
        },
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    return entry


def _registry_entries(hass: HomeAssistant, entry: MockConfigEntry) -> tuple:
    entity_registry = er.async_get(hass)
    light = entity_registry.async_get_or_create(
        "light", DOMAIN, f"{ADDRESS}_light", config_entry=entry
    )
    sync_clock = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{ADDRESS}_sync_clock", config_entry=entry
    )
    old_connection = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{ADDRESS}_connection", config_entry=entry
    )
    foreign = entity_registry.async_get_or_create(
        "light", "other", f"{ADDRESS}_light", config_entry=None
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, ADDRESS)}
    )
    return light, sync_clock, old_connection, foreign, device


async def test_enrolled_entry_moves_identifiers_to_fingerprint(
    hass: HomeAssistant,
) -> None:
    """Existing entities keep their registry entry, entity ID and history."""
    entry = _entry(hass, enrolled=True)
    light, sync_clock, old_connection, foreign, device = _registry_entries(hass, entry)

    assert await async_migrate_entry(hass, entry)

    entity_registry = er.async_get(hass)
    migrated_light = entity_registry.async_get(light.entity_id)
    assert migrated_light.id == light.id
    assert migrated_light.unique_id == f"{FINGERPRINT}_light"
    assert entity_registry.async_get(sync_clock.entity_id).unique_id == (
        f"{FINGERPRINT}_sync_clock"
    )
    # The text connection sensor was replaced by a connectivity binary sensor.
    assert entity_registry.async_get(old_connection.entity_id) is None
    assert entity_registry.async_get(foreign.entity_id).unique_id == (
        f"{ADDRESS}_light"
    )
    assert dr.async_get(hass).async_get(device.id).identifiers == {
        (DOMAIN, FINGERPRINT)
    }
    assert (entry.version, entry.minor_version) == (1, 2)
    assert not ir.async_get(hass).issues


async def test_pre_enrollment_entry_keeps_address_ids_and_asks_to_reconfigure(
    hass: HomeAssistant,
) -> None:
    """Without a verified fingerprint the address stays the stable identifier."""
    entry = _entry(hass, enrolled=False)
    light, _, old_connection, _, device = _registry_entries(hass, entry)

    assert await async_migrate_entry(hass, entry)

    entity_registry = er.async_get(hass)
    assert entity_registry.async_get(light.entity_id).unique_id == f"{ADDRESS}_light"
    assert entity_registry.async_get(old_connection.entity_id) is None
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, ADDRESS)}
    assert entry.minor_version == 2
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{entry.entry_id}_{ISSUE_ID_IDENTITY_ENROLLMENT}"
    )
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {"title": "Nursery"}

    await async_remove_entry(hass, entry)
    assert not ir.async_get(hass).issues


async def test_future_major_version_is_not_migrated(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=FINGERPRINT, data={CONF_ADDRESS: ADDRESS}, version=2
    )
    entry.add_to_hass(hass)

    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 2


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_runs_migration_before_platforms(hass: HomeAssistant) -> None:
    """Home Assistant migrates the entry before entities are created."""
    entry = _entry(hass, enrolled=True)
    light, *_ = _registry_entries(hass, entry)
    coordinator = Mock(async_setup=AsyncMock(), async_shutdown=AsyncMock())

    with (
        patch("homeassistant.setup.async_process_deps_reqs", new_callable=AsyncMock),
        patch(
            "homeassistant.config_entries.async_process_deps_reqs",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert er.async_get(hass).async_get(light.entity_id).unique_id == (
        f"{FINGERPRINT}_light"
    )
