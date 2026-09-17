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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

ATTR_PROFILE = "profile"
ATTR_EXPECTED_REVISION = "expected_revision"
ATTR_ENABLED = "enabled"

SERVICE_REFRESH_STATE = "refresh_state"
SERVICE_SYNC_CLOCK = "sync_clock"
SERVICE_EXPORT_PROFILE = "export_profile"
SERVICE_IMPORT_PROFILE = "import_profile"
SERVICE_RESTORE_PROFILE = "restore_profile"
SERVICE_SET_MAINTENANCE = "set_maintenance"


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
MAINTENANCE_SCHEMA = ENTRY_SCHEMA.extend({vol.Required(ATTR_ENABLED): cv.boolean})


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


async def _async_refresh_state(hass: HomeAssistant, call: ServiceCall) -> None:
    await _coordinator(hass, call).async_request_refresh()


async def _async_sync_clock(hass: HomeAssistant, call: ServiceCall) -> None:
    await _coordinator(hass, call).async_sync_clock()


async def _async_export_profile(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    response = await _coordinator(hass, call).async_export_profile()
    return cast(ServiceResponse, response)


async def _async_import_profile(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse | None:
    coordinator = _coordinator(hass, call)
    await coordinator.async_import_profile(
        call.data[ATTR_PROFILE], call.data[ATTR_EXPECTED_REVISION], confirmed=True
    )
    if call.return_response:
        return {
            "revision": coordinator.profile_record.revision,
            "pending": coordinator.profile_record.pending,
        }
    return None


async def _async_restore_profile(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse | None:
    await _coordinator(hass, call).async_restore_profile()
    if call.return_response:
        return {"accepted": True}
    return None


async def _async_set_maintenance(hass: HomeAssistant, call: ServiceCall) -> None:
    await _coordinator(hass, call).async_set_maintenance(call.data[ATTR_ENABLED])


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
            SERVICE_REFRESH_STATE,
            partial(_async_refresh_state, hass),
            ENTRY_SCHEMA,
            SupportsResponse.NONE,
        ),
        (
            SERVICE_SYNC_CLOCK,
            partial(_async_sync_clock, hass),
            ENTRY_SCHEMA,
            SupportsResponse.NONE,
        ),
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
            ENTRY_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_SET_MAINTENANCE,
            partial(_async_set_maintenance, hass),
            MAINTENANCE_SCHEMA,
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
