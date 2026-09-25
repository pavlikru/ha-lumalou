"""Confirmation-gated Repairs flows for saved-profile storage and restore."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import ISSUE_ID_PROFILE_RESTORE_NEEDED, ISSUE_ID_PROFILE_STORAGE
from .models import ProfileValidationError, import_profile_payload

CONF_CONFIRM = "confirm"
CONF_PROFILE_JSON = "profile_json"


def parse_profile_json(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate an export envelope or the complete export action response.

    Returns the envelope and its validated profile. The revision in an export
    action response is ignored; recovery starts a new revision.
    """
    if not isinstance(value, str):
        raise ValueError("Profile JSON must be text")
    document = json.loads(value)
    if (
        isinstance(document, dict)
        and set(document) == {"current_revision", "profile"}
        and isinstance(document["profile"], dict)
    ):
        document = document["profile"]
    if not isinstance(document, dict):
        raise ValueError("Profile export must be a JSON object")
    return document, import_profile_payload(document)


def _coordinator(hass: HomeAssistant, entry_id: str) -> Any | None:
    """Return a loaded entry's coordinator, never creating one."""
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime_data = getattr(entry, "runtime_data", None)
    return None if runtime_data is None else runtime_data.coordinator


class ProfileRestoreRepairFlow(RepairsFlow):
    """Let the user choose which side wins after the device settings changed."""

    def __init__(self, entry_id: str) -> None:
        """Initialize an entry-scoped restore choice."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Offer restoring the saved profile or keeping the device settings."""
        return self.async_show_menu(
            step_id="init", menu_options=["restore", "keep_device"]
        )

    async def async_step_restore(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Write the saved profile back and verify it with a fresh read."""
        return await self._async_resolve("restore", user_input)

    async def async_step_keep_device(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Save a fresh complete device read as the new verified profile."""
        return await self._async_resolve("keep_device", user_input)

    async def _async_resolve(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> RepairsFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (coordinator := _coordinator(self.hass, self._entry_id)) is None:
                return self.async_abort(reason="entry_not_loaded")
            if (need := coordinator.restore_needed) is None:
                return self.async_abort(reason="not_needed")
            try:
                if step_id == "restore":
                    await coordinator.async_restore_profile(
                        need.revision, confirmed=True
                    )
                else:
                    profile, revision = await coordinator.async_read_profile_snapshot()
                    await coordinator.async_accept_device_profile(
                        profile, revision, confirmed=True
                    )
            except HomeAssistantError, ValueError:
                errors["base"] = f"{step_id}_failed"
            else:
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema({}), errors=errors
        )


class ProfileStorageRepairFlow(RepairsFlow):
    """Recover only from an explicit, validated user backup."""

    def __init__(self, entry_id: str) -> None:
        """Initialize an entry-scoped recovery flow."""
        self._entry_id = entry_id
        self._payload: dict[str, Any] | None = None
        self._field_count = 0

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Start with the backup import step."""
        return await self.async_step_import_profile(user_input)

    async def async_step_import_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Validate pasted JSON without changing storage."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._payload, profile = parse_profile_json(
                    user_input[CONF_PROFILE_JSON]
                )
                self._field_count = len(profile)
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_profile"
            else:
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="import_profile",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE_JSON): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Back up corrupt bytes and replace them only after confirmation."""
        assert self._payload is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_CONFIRM) is not True:
                errors["base"] = "confirmation_required"
            elif (
                entry := self.hass.config_entries.async_get_entry(self._entry_id)
            ) is None:
                return self.async_abort(reason="entry_removed")
            elif (runtime_data := getattr(entry, "runtime_data", None)) is None:
                return self.async_abort(reason="entry_not_loaded")
            else:
                try:
                    await runtime_data.coordinator.async_recover_profile(
                        self._payload, confirmed=True
                    )
                except HomeAssistantError, ProfileValidationError:
                    errors["base"] = "recovery_failed"
                else:
                    return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(CONF_CONFIRM, default=False): bool}),
            description_placeholders={"field_count": str(self._field_count)},
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create an entry-scoped profile repair flow."""
    entry_id = data.get("entry_id") if data is not None else None
    if isinstance(entry_id, str):
        if issue_id == f"{entry_id}_{ISSUE_ID_PROFILE_STORAGE}":
            return ProfileStorageRepairFlow(entry_id)
        if issue_id == f"{entry_id}_{ISSUE_ID_PROFILE_RESTORE_NEEDED}":
            return ProfileRestoreRepairFlow(entry_id)
    raise ValueError("Invalid Lumalou repair")
