"""Confirmation-gated Repairs flow for a device that differs from its profile."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import ISSUE_ID_PROFILE_RESTORE_NEEDED


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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the entry-scoped restore choice flow."""
    entry_id = data.get("entry_id") if data is not None else None
    if isinstance(entry_id, str) and (
        issue_id == f"{entry_id}_{ISSUE_ID_PROFILE_RESTORE_NEEDED}"
    ):
        return ProfileRestoreRepairFlow(entry_id)
    raise ValueError("Invalid Lumalou repair")
