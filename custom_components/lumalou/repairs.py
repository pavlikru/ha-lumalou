"""Confirmation-gated recovery for unreadable private profile storage."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .config_flow import CONF_CONFIRM, CONF_PROFILE_JSON, parse_profile_json
from .const import ISSUE_ID_PROFILE_STORAGE
from .models import ProfileValidationError


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
    """Create an entry-scoped saved-profile recovery flow."""
    if (
        data is None
        or not isinstance(entry_id := data.get("entry_id"), str)
        or issue_id != f"{entry_id}_{ISSUE_ID_PROFILE_STORAGE}"
    ):
        raise ValueError("Invalid Lumalou profile-storage repair")
    return ProfileStorageRepairFlow(entry_id)
