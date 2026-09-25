"""Home Assistant integration for Fisher-Price Lumalou."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DEVICE_FINGERPRINT,
    DOMAIN,
    ISSUE_ID_IDENTITY_ENROLLMENT,
    ISSUE_ID_PROFILE_RESTORE_NEEDED,
    PLATFORMS,
)
from .entity import async_migrate_identifiers
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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Key registry identifiers by the entry unique ID instead of the BLE address."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        address = entry.data[CONF_ADDRESS]
        if entry.data.get(CONF_DEVICE_FINGERPRINT):
            await async_migrate_identifiers(hass, entry, address, str(entry.unique_id))
        else:
            # Pre-enrollment entries keep address-keyed identifiers (their
            # unique ID is the address) and stay blocked by the coordinator
            # until Reconfigure verifies the device and migrates them.
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"{entry.entry_id}_{ISSUE_ID_IDENTITY_ENROLLMENT}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_ID_IDENTITY_ENROLLMENT,
                translation_placeholders={"title": entry.title},
            )
        # The connection diagnostic moved from a text sensor to a binary sensor.
        registry = er.async_get(hass)
        if old_sensor := registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{entry.unique_id}_connection"
        ):
            registry.async_remove(old_sensor)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LumalouConfigEntry) -> bool:
    """Set up Lumalou without requiring the device to be online."""
    from .coordinator import LumalouCoordinator

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
    """Remove this entry's Repairs issues and its private profile Store.

    The saved profile is bound to this entry and device key; users keep a copy
    with the export action before removing the device.
    """
    for issue in (ISSUE_ID_IDENTITY_ENROLLMENT, ISSUE_ID_PROFILE_RESTORE_NEEDED):
        ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{issue}")
    await ProfileStore(hass, entry.entry_id).async_remove()
