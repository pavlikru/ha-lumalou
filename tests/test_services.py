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
from custom_components.lumalou.coordinator import ProfileRestoreError
from custom_components.lumalou.models import (
    ProfileValidationError,
    RevisionConflictError,
)
from custom_components.lumalou.restore import ProfileRestoreResult
from custom_components.lumalou.services import (
    SERVICE_EXPORT_PROFILE,
    SERVICE_IMPORT_PROFILE,
    SERVICE_RESTORE_PROFILE,
    SERVICE_SET_ROUTINE,
    SERVICE_START_ROUTINE,
    async_setup_services,
)


def loaded_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, SimpleNamespace]:
    """Add a loaded entry with a deterministic coordinator double."""
    record = SimpleNamespace(revision=4, pending=False)
    coordinator = SimpleNamespace(
        profile_record=record,
        async_export_profile=AsyncMock(
            return_value={
                "current_revision": 4,
                "profile": {"schema_version": 2, "profile": {"playlist": [2]}},
            }
        ),
        async_import_profile=AsyncMock(),
        async_restore_profile=AsyncMock(
            return_value=ProfileRestoreResult(
                revision=4,
                automatic=False,
                planned_steps=("playlist",),
                applied_steps=("playlist",),
                verified=True,
            )
        ),
        async_set_routines=AsyncMock(
            return_value=ProfileRestoreResult(
                revision=5,
                automatic=False,
                planned_steps=("routines.monday",),
                applied_steps=("routines.monday",),
                verified=True,
            )
        ),
        async_start_routine=AsyncMock(),
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
        await hass.services.async_call(
            DOMAIN, SERVICE_EXPORT_PROFILE, {}, blocking=True, return_response=True
        )

    foreign = MockConfigEntry(
        domain="test", state=ConfigEntryState.LOADED, entry_id="foreign"
    )
    foreign.runtime_data = SimpleNamespace(coordinator=SimpleNamespace())
    foreign.add_to_hass(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_PROFILE,
            {ATTR_CONFIG_ENTRY_ID: foreign.entry_id},
            blocking=True,
            return_response=True,
        )

    unloaded = MockConfigEntry(domain=DOMAIN, entry_id="unloaded")
    unloaded.add_to_hass(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_PROFILE,
            {ATTR_CONFIG_ENTRY_ID: unloaded.entry_id},
            blocking=True,
            return_response=True,
        )


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
        "profile": {"schema_version": 2, "profile": {"playlist": [2]}},
    }
    coordinator.async_export_profile.assert_awaited_once_with()


async def test_export_of_unsupported_saved_profile_is_translated(
    hass: HomeAssistant,
) -> None:
    """A saved value outside the device range fails with a translated error."""
    entry, coordinator = loaded_entry(hass)
    coordinator.async_export_profile.side_effect = ProfileValidationError("music")
    async_setup_services(hass)

    with pytest.raises(ServiceValidationError) as caught:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_PROFILE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
            blocking=True,
            return_response=True,
        )
    assert caught.value.translation_key == "invalid_profile"


async def test_import_response_and_revision(hass: HomeAssistant) -> None:
    """Import passes concurrency revision and returns the saved result."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    profile = {"schema_version": 2, "scope": "persistent_profile", "profile": {}}
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
                    "schema_version": 2,
                    "scope": "persistent_profile",
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
                    "schema_version": 2,
                    "scope": "persistent_profile",
                    "profile": {},
                },
                "expected_revision": revision,
            },
            blocking=True,
        )
    coordinator.async_import_profile.assert_not_awaited()


async def test_removed_duplicate_actions_are_not_registered(
    hass: HomeAssistant,
) -> None:
    """Buttons and the maintenance switch cover refresh, clock and maintenance."""
    async_setup_services(hass)
    assert set(hass.services.async_services_for_domain(DOMAIN)) == {
        SERVICE_EXPORT_PROFILE,
        SERVICE_IMPORT_PROFILE,
        SERVICE_RESTORE_PROFILE,
        SERVICE_SET_ROUTINE,
        SERVICE_START_ROUTINE,
    }


async def test_restore_defaults_to_current_revision(hass: HomeAssistant) -> None:
    """Without a revision, the current saved revision is restored and reported."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_RESTORE_PROFILE,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
        blocking=True,
        return_response=True,
    )

    coordinator.async_restore_profile.assert_awaited_once_with(4, confirmed=True)
    assert response == {
        "revision": 4,
        "verified": True,
        "applied_steps": ["playlist"],
        "clock_synced": False,
    }


async def test_restore_passes_explicit_revision(hass: HomeAssistant) -> None:
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESTORE_PROFILE,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "expected_revision": 3},
        blocking=True,
    )
    coordinator.async_restore_profile.assert_awaited_once_with(3, confirmed=True)


@pytest.mark.parametrize(
    ("error", "key"),
    [
        ("restore_write", "restore_write"),
        ("restore_verify", "restore_verify"),
        ("restore_mismatch", "restore_mismatch"),
        (None, "restore_verify"),
    ],
)
async def test_restore_failure_is_translated(
    hass: HomeAssistant, error: str | None, key: str
) -> None:
    """Executor failures become translated errors, never error payloads."""
    entry, coordinator = loaded_entry(hass)
    outcome = ProfileRestoreResult(
        revision=4,
        automatic=False,
        planned_steps=("playlist", "alarm"),
        applied_steps=("playlist",),
        verified=False,
        mismatched_blocks=("playlist",),
        error=error,
    )
    coordinator.async_restore_profile.side_effect = ProfileRestoreError(
        "not verified", outcome
    )
    async_setup_services(hass)

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROFILE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
            blocking=True,
        )
    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == key
    assert caught.value.translation_placeholders == {
        "applied": "1",
        "planned": "2",
        "count": "1",
    }


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (RevisionConflictError("changed"), "revision_conflict"),
        (ProfileValidationError("incomplete"), "invalid_profile"),
    ],
)
async def test_restore_and_import_validation_errors_are_translated(
    hass: HomeAssistant, error: Exception, key: str
) -> None:
    entry, coordinator = loaded_entry(hass)
    coordinator.async_restore_profile.side_effect = error
    coordinator.async_import_profile.side_effect = error
    async_setup_services(hass)

    for service, data in (
        (SERVICE_RESTORE_PROFILE, {}),
        (
            SERVICE_IMPORT_PROFILE,
            {"profile": {}, "expected_revision": 4},
        ),
    ):
        with pytest.raises(ServiceValidationError) as caught:
            await hass.services.async_call(
                DOMAIN,
                service,
                {ATTR_CONFIG_ENTRY_ID: entry.entry_id, **data},
                blocking=True,
            )
        assert caught.value.translation_key == key


async def test_other_restore_errors_propagate(hass: HomeAssistant) -> None:
    entry, coordinator = loaded_entry(hass)
    coordinator.async_restore_profile.side_effect = HomeAssistantError("offline")
    async_setup_services(hass)

    with pytest.raises(HomeAssistantError, match="offline"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_PROFILE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
            blocking=True,
        )


async def test_set_routine_maps_days_time_and_ordered_tasks(
    hass: HomeAssistant,
) -> None:
    """Task keys become device ids in the given order; days are deduplicated."""
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_ROUTINE,
        {
            ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            "days": ["monday", "tuesday", "monday"],
            "time": "20:00",
            "tasks": ["brush_teeth", "toilet", "story"],
        },
        blocking=True,
        return_response=True,
    )

    coordinator.async_set_routines.assert_awaited_once_with(
        ["monday", "tuesday"], {"hour": 20, "minute": 0}, [3, 4, 7]
    )
    assert response == {
        "revision": 5,
        "verified": True,
        "applied_steps": ["routines.monday"],
    }

    # Without tasks there is no routine on that day; no time is needed.
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_ROUTINE,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "days": "sunday", "tasks": []},
        blocking=True,
    )
    coordinator.async_set_routines.assert_awaited_with(["sunday"], None, [])


@pytest.mark.parametrize(
    "data",
    [
        {"days": [], "tasks": ["meal"], "time": "20:00"},
        {"days": ["someday"], "tasks": ["meal"], "time": "20:00"},
        {"days": ["monday"], "tasks": ["nap"], "time": "20:00"},
        {"days": ["monday"], "tasks": ["meal"], "time": "25:00"},
    ],
)
async def test_set_routine_schema_rejects_bad_input(
    hass: HomeAssistant, data: dict
) -> None:
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ROUTINE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id, **data},
            blocking=True,
        )
    coordinator.async_set_routines.assert_not_awaited()


async def test_set_routine_translates_validation_and_restore_errors(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)
    call = {
        ATTR_CONFIG_ENTRY_ID: entry.entry_id,
        "days": ["monday"],
        "tasks": ["meal", "meal"],
    }
    coordinator.async_set_routines.side_effect = ProfileValidationError("dup")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_SET_ROUTINE, call, blocking=True)
    assert err.value.translation_key == "invalid_routine"

    coordinator.async_set_routines.side_effect = ProfileRestoreError(
        "failed",
        ProfileRestoreResult(
            revision=5,
            automatic=False,
            planned_steps=("routines.monday",),
            applied_steps=(),
            verified=False,
            error="restore_write",
        ),
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_SET_ROUTINE, call, blocking=True)
    assert err.value.translation_key == "restore_write"
    assert err.value.translation_placeholders == {
        "applied": "0",
        "planned": "1",
        "count": "0",
    }


async def test_start_routine_with_and_without_tasks(hass: HomeAssistant) -> None:
    entry, coordinator = loaded_entry(hass)
    async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_START_ROUTINE,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id},
        blocking=True,
    )
    coordinator.async_start_routine.assert_awaited_once_with(None)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_START_ROUTINE,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "tasks": ["tidy_up", "story"]},
        blocking=True,
    )
    coordinator.async_start_routine.assert_awaited_with([8, 7])

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_START_ROUTINE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "tasks": []},
            blocking=True,
        )

    coordinator.async_start_routine.side_effect = ProfileValidationError("dup")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_START_ROUTINE,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "tasks": ["meal", "meal"]},
            blocking=True,
        )
    assert err.value.translation_key == "invalid_routine"
