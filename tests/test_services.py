"""Lumalou action tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.const import DOMAIN
from custom_components.lumalou.services import (
    SERVICE_EXPORT_PROFILE,
    SERVICE_IMPORT_PROFILE,
    SERVICE_REFRESH_STATE,
    SERVICE_RESTORE_PROFILE,
    SERVICE_SET_MAINTENANCE,
    SERVICE_SYNC_CLOCK,
    async_setup_services,
)


def loaded_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, SimpleNamespace]:
    """Add a loaded entry with a deterministic coordinator double."""
    record = SimpleNamespace(revision=4, pending=False)
    coordinator = SimpleNamespace(
        profile_record=record,
        async_request_refresh=AsyncMock(),
        async_sync_clock=AsyncMock(),
        async_export_profile=AsyncMock(
            return_value={
                "current_revision": 4,
                "profile": {"schema_version": 1, "profile": {"volume": 2}},
            }
        ),
        async_import_profile=AsyncMock(),
        async_restore_profile=AsyncMock(),
        async_set_maintenance=AsyncMock(),
    )

    async def import_profile(*args, **kwargs):
        record.revision += 1
        record.pending = True

    coordinator.async_import_profile.side_effect = import_profile
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"address": "synthetic-device"},
        unique_id="synthetic-device",
        state=ConfigEntryState.LOADED,
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    entry.add_to_hass(hass)
    return entry, coordinator


async def test_actions_require_explicit_loaded_lumalou_target(
    hass: HomeAssistant,
) -> None:
    """Missing, foreign, and unloaded targets are rejected."""
    async_setup_services(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, SERVICE_REFRESH_STATE, {}, blocking=True)

    foreign = MockConfigEntry(
        domain="test", state=ConfigEntryState.LOADED, entry_id="foreign"
    )
    foreign.runtime_data = SimpleNamespace(coordinator=SimpleNamespace())
    foreign.add_to_hass(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH_STATE,
            {ATTR_CONFIG_ENTRY_ID: foreign.entry_id},
            blocking=True,
        )

    unloaded = MockConfigEntry(domain=DOMAIN, entry_id="unloaded")
    unloaded.add_to_hass(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH_STATE,
            {ATTR_CONFIG_ENTRY_ID: unloaded.entry_id},
            blocking=True,
        )


async def test_refresh_and_clock_target_selected_entry(hass: HomeAssistant) -> None:
    """Simple actions call only the explicit entry coordinator."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    data = {ATTR_CONFIG_ENTRY_ID: entry.entry_id}

    await hass.services.async_call(DOMAIN, SERVICE_REFRESH_STATE, data, blocking=True)
    await hass.services.async_call(DOMAIN, SERVICE_SYNC_CLOCK, data, blocking=True)

    coordinator.async_request_refresh.assert_awaited_once_with()
    coordinator.async_sync_clock.assert_awaited_once_with()


async def test_export_is_response_only(hass: HomeAssistant) -> None:
    """Export requires a response request and returns JSON-compatible data."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    data = {ATTR_CONFIG_ENTRY_ID: entry.entry_id}
    assert (
        hass.services.supports_response(DOMAIN, SERVICE_EXPORT_PROFILE)
        is SupportsResponse.ONLY
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_EXPORT_PROFILE, data, blocking=True
        )

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_EXPORT_PROFILE,
        data,
        blocking=True,
        return_response=True,
    )
    assert response == {
        "current_revision": 4,
        "profile": {"schema_version": 1, "profile": {"volume": 2}},
    }
    coordinator.async_export_profile.assert_awaited_once_with()


async def test_import_response_and_revision(hass: HomeAssistant) -> None:
    """Import passes concurrency revision and returns the saved result."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    profile = {"schema_version": 1, "scope": "supported_subset", "profile": {}}
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_IMPORT_PROFILE,
        {
            ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            "profile": profile,
            "expected_revision": 4,
        },
        blocking=True,
        return_response=True,
    )

    coordinator.async_import_profile.assert_awaited_once_with(
        profile, 4, confirmed=True
    )
    assert response == {"revision": 5, "pending": True}


async def test_import_requires_expected_revision(hass: HomeAssistant) -> None:
    """Replacement import cannot silently bypass optimistic concurrency."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_PROFILE,
            {
                ATTR_CONFIG_ENTRY_ID: entry.entry_id,
                "profile": {
                    "schema_version": 1,
                    "scope": "supported_subset",
                    "profile": {},
                },
            },
            blocking=True,
        )
    coordinator.async_import_profile.assert_not_awaited()


@pytest.mark.parametrize("revision", [True, "4", -1])
async def test_import_rejects_coerced_revision(
    hass: HomeAssistant, revision: object
) -> None:
    """Concurrency tokens preserve strict integer semantics at service boundary."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_PROFILE,
            {
                ATTR_CONFIG_ENTRY_ID: entry.entry_id,
                "profile": {
                    "schema_version": 1,
                    "scope": "supported_subset",
                    "profile": {},
                },
                "expected_revision": revision,
            },
            blocking=True,
        )
    coordinator.async_import_profile.assert_not_awaited()


async def test_restore_error_is_raised(hass: HomeAssistant) -> None:
    """Restore failures are exceptions, never error payloads."""
    entry, coordinator = loaded_entry(hass)
    coordinator.async_restore_profile.side_effect = HomeAssistantError(
        "restore unavailable"
    )
    async_setup_services(hass)

    with pytest.raises(HomeAssistantError, match="restore unavailable"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROFILE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
            blocking=True,
            return_response=True,
        )


async def test_set_maintenance(hass: HomeAssistant) -> None:
    """Maintenance validates a boolean and targets one entry."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_MAINTENANCE,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "enabled": True},
        blocking=True,
    )
    coordinator.async_set_maintenance.assert_awaited_once_with(True)
