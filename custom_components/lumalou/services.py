"""Integration-wide actions for Lumalou."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, cast

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, ROUTINE_TASKS
from .models import DAYS, ProfileValidationError, RevisionConflictError

ATTR_PROFILE = "profile"
ATTR_EXPECTED_REVISION = "expected_revision"
ATTR_DAYS = "days"
ATTR_TIME = "time"
ATTR_TASKS = "tasks"

SERVICE_EXPORT_PROFILE = "export_profile"
SERVICE_IMPORT_PROFILE = "import_profile"
SERVICE_RESTORE_PROFILE = "restore_profile"
SERVICE_SET_ROUTINE = "set_routine"
SERVICE_START_ROUTINE = "start_routine"


def _strict_revision(value: Any) -> int:
    """Reject bool/string coercion for optimistic-concurrency tokens."""
    if type(value) is not int or value < 0:
        raise vol.Invalid("expected_revision must be a non-negative integer")
    return value


ENTRY_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})
IMPORT_SCHEMA = ENTRY_SCHEMA.extend(
    {
        vol.Required(ATTR_PROFILE): dict,
        vol.Required(ATTR_EXPECTED_REVISION): _strict_revision,
    }
)
RESTORE_SCHEMA = ENTRY_SCHEMA.extend(
    {vol.Optional(ATTR_EXPECTED_REVISION): _strict_revision}
)
_TASKS = vol.All(cv.ensure_list, [vol.In(ROUTINE_TASKS)])
SET_ROUTINE_SCHEMA = ENTRY_SCHEMA.extend(
    {
        vol.Required(ATTR_DAYS): vol.All(
            cv.ensure_list, [vol.In(DAYS)], vol.Length(min=1)
        ),
        vol.Optional(ATTR_TIME): cv.time,
        vol.Required(ATTR_TASKS): _TASKS,
    }
)
START_ROUTINE_SCHEMA = ENTRY_SCHEMA.extend(
    {vol.Optional(ATTR_TASKS): vol.All(_TASKS, vol.Length(min=1))}
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> Any:
    """Resolve a loaded Lumalou coordinator from an explicit entry target."""
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_not_found",
        )
    if entry.state is not ConfigEntryState.LOADED or not hasattr(entry, "runtime_data"):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_not_loaded",
        )
    return entry.runtime_data.coordinator


def _validation_error(err: ValueError) -> ServiceValidationError:
    """Translate saved-profile validation and revision conflicts."""
    key = (
        "revision_conflict"
        if isinstance(err, RevisionConflictError)
        else "invalid_profile"
    )
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


async def _async_export_profile(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    try:
        response = await _coordinator(hass, call).async_export_profile()
    except ProfileValidationError as err:
        raise _validation_error(err) from err
    return cast(ServiceResponse, response)


async def _async_import_profile(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse | None:
    coordinator = _coordinator(hass, call)
    try:
        await coordinator.async_import_profile(
            call.data[ATTR_PROFILE], call.data[ATTR_EXPECTED_REVISION], confirmed=True
        )
    except (ProfileValidationError, RevisionConflictError) as err:
        raise _validation_error(err) from err
    if call.return_response:
        return {
            "revision": coordinator.profile_record.revision,
            "pending": coordinator.profile_record.pending,
        }
    return None


def _restore_error(err: Any) -> HomeAssistantError:
    """Translate a restore that did not reach a verified state."""
    outcome = err.result
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=outcome.error or "restore_verify",
        translation_placeholders={
            "applied": str(len(outcome.applied_steps)),
            "planned": str(len(outcome.planned_steps)),
            # Block names stay in the log and diagnostics.
            "count": str(len(outcome.mismatched_blocks)),
        },
    )


def _task_ids(tasks: list[str]) -> list[int]:
    """Map task keys to device task ids 1..11."""
    return [ROUTINE_TASKS.index(task) + 1 for task in tasks]


def _routine_error() -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key="invalid_routine"
    )


async def _async_restore_profile(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse | None:
    """Write the saved profile, defaulting to the current saved revision."""
    from .coordinator import ProfileRestoreError

    coordinator = _coordinator(hass, call)
    revision = call.data.get(
        ATTR_EXPECTED_REVISION, coordinator.profile_record.revision
    )
    try:
        result = await coordinator.async_restore_profile(revision, confirmed=True)
    except ProfileRestoreError as err:
        raise _restore_error(err) from err
    except (ProfileValidationError, RevisionConflictError) as err:
        raise _validation_error(err) from err
    if call.return_response:
        return {
            "revision": result.revision,
            "verified": result.verified,
            "applied_steps": list(result.applied_steps),
            "clock_synced": result.clock_synced,
        }
    return None


async def _async_set_routine(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse | None:
    """Save day routines and write them to the device, verified."""
    from .coordinator import ProfileRestoreError

    coordinator = _coordinator(hass, call)
    start = call.data.get(ATTR_TIME)
    try:
        result = await coordinator.async_set_routines(
            list(dict.fromkeys(call.data[ATTR_DAYS])),
            None if start is None else {"hour": start.hour, "minute": start.minute},
            _task_ids(call.data[ATTR_TASKS]),
        )
    except ProfileRestoreError as err:
        raise _restore_error(err) from err
    except ProfileValidationError as err:
        raise _routine_error() from err
    if call.return_response:
        return {
            "revision": result.revision,
            "verified": result.verified,
            "applied_steps": list(result.applied_steps),
        }
    return None


async def _async_start_routine(hass: HomeAssistant, call: ServiceCall) -> None:
    """Start today's routine now, optionally with other tasks this once."""
    tasks = call.data.get(ATTR_TASKS)
    try:
        await _coordinator(hass, call).async_start_routine(
            None if tasks is None else _task_ids(tasks)
        )
    except ProfileValidationError as err:
        raise _routine_error() from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Lumalou actions once."""
    registrations: tuple[
        tuple[
            str,
            Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]],
            vol.Schema,
            SupportsResponse,
        ],
        ...,
    ] = (
        (
            SERVICE_EXPORT_PROFILE,
            partial(_async_export_profile, hass),
            ENTRY_SCHEMA,
            SupportsResponse.ONLY,
        ),
        (
            SERVICE_IMPORT_PROFILE,
            partial(_async_import_profile, hass),
            IMPORT_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_RESTORE_PROFILE,
            partial(_async_restore_profile, hass),
            RESTORE_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_SET_ROUTINE,
            partial(_async_set_routine, hass),
            SET_ROUTINE_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_START_ROUTINE,
            partial(_async_start_routine, hass),
            START_ROUTINE_SCHEMA,
            SupportsResponse.NONE,
        ),
    )

    for service, handler, schema, supports_response in registrations:
        if hass.services.has_service(DOMAIN, service):
            continue
        hass.services.async_register(
            DOMAIN,
            service,
            handler,
            schema=schema,
            supports_response=supports_response,
        )
