"""Home Assistant integration for Fisher-Price Lumalou."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DEVICE_FINGERPRINT,
    DOMAIN,
    ISSUE_ID_PROFILE_RESTORE_NEEDED,
    PLATFORMS,
)
from .models import LumalouRuntimeData
from .services import async_setup_services
from .storage import ProfileStore

if TYPE_CHECKING:
    from .restore import RestoreNeeded

type LumalouConfigEntry = ConfigEntry[LumalouRuntimeData]


@callback
def async_sync_restore_issue(
    hass: HomeAssistant, entry_id: str, need: RestoreNeeded | None
) -> None:
    """Show the restore issue exactly while the coordinator reports a mismatch."""
    issue_id = f"{entry_id}_{ISSUE_ID_PROFILE_RESTORE_NEEDED}"
    if need is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        data={"entry_id": entry_id},
        is_fixable=True,
        severity=(
            ir.IssueSeverity.ERROR
            if need.auto_restore_exhausted
            else ir.IssueSeverity.WARNING
        ),
        translation_key=ISSUE_ID_PROFILE_RESTORE_NEEDED,
        translation_placeholders={
            "block_count": str(len(need.changed_blocks)),
            "attempts": str(need.auto_restore_attempts),
        },
    )


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@callback
def _time_zone_changed(data: Mapping[str, Any]) -> bool:
    """Match core configuration updates that change the time zone."""
    return "time_zone" in data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Set up Lumalou without requiring the device to be online."""
    from .coordinator import LumalouCoordinator

    if not entry.data.get(CONF_DEVICE_FINGERPRINT):
        # Only unreleased development builds created such entries.
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="identity_not_enrolled"
        )

    coordinator = LumalouCoordinator(hass, entry)
    await coordinator.async_setup()
    entry.runtime_data = LumalouRuntimeData(coordinator)

    restore_needed = coordinator.restore_needed

    @callback
    def _async_sync_restore_issue() -> None:
        nonlocal restore_needed
        if (need := coordinator.restore_needed) != restore_needed:
            restore_needed = need
            async_sync_restore_issue(hass, entry.entry_id, need)

    entry.async_on_unload(coordinator.async_add_listener(_async_sync_restore_issue))
    # Reconnects correct the clock; these catch DST and drift in long sessions.
    entry.async_on_unload(
        async_track_time_change(
            hass, coordinator.async_schedule_clock_check, hour=3, minute=5, second=0
        )
    )
    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_CORE_CONFIG_UPDATE,
            coordinator.async_schedule_clock_check,
            event_filter=_time_zone_changed,
        )
    )

    try:
        coordinator.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Unload a Lumalou config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.coordinator.async_shutdown()
    # Restore detection is runtime state; the next setup detects it again.
    async_sync_restore_issue(hass, entry.entry_id, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> None:
    """Remove this entry's Repairs issue and its private profile Store.

    The saved profile is bound to this entry and device key; users keep a copy
    with the export action before removing the device.
    """
    async_sync_restore_issue(hass, entry.entry_id, None)
    await ProfileStore(hass, entry.entry_id).async_remove()
