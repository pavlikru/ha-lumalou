"""Config entry lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou import (
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.lumalou.const import DOMAIN, ISSUE_ID_PROFILE_STORAGE, PLATFORMS


def _profile_storage_issue_id(entry: MockConfigEntry) -> str:
    """Return the entry-scoped saved-profile Repairs issue id."""
    return f"{entry.entry_id}_{ISSUE_ID_PROFILE_STORAGE}"


async def test_offline_setup_starts_callbacks_and_forwards_platforms(
    hass: HomeAssistant,
) -> None:
    """Setup starts HA-owned callbacks without requiring an online device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"address": "synthetic-device"},
        unique_id="synthetic-device",
    )
    coordinator = Mock()
    coordinator.async_setup = AsyncMock()
    coordinator.async_start = Mock()
    coordinator.async_shutdown = AsyncMock()

    with (
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ) as coordinator_class,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ) as forward,
    ):
        assert await async_setup_entry(hass, entry)

    coordinator_class.assert_called_once_with(hass, entry)
    coordinator.async_setup.assert_awaited_once_with()
    coordinator.async_start.assert_called_once_with()
    assert entry.runtime_data.coordinator is coordinator
    forward.assert_awaited_once_with(entry, PLATFORMS)


async def test_unhealthy_profile_creates_entry_scoped_repair_issue(
    hass: HomeAssistant,
) -> None:
    """A failed saved-profile load is visible without replacing the profile."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic-device"})
    coordinator = Mock(
        profile_storage_healthy=False,
        async_setup=AsyncMock(),
        async_start=Mock(),
        async_shutdown=AsyncMock(),
    )

    with (
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _profile_storage_issue_id(entry))
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.is_persistent is False
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == ISSUE_ID_PROFILE_STORAGE
    assert issue.data == {"entry_id": entry.entry_id}


async def test_healthy_entry_only_deletes_its_own_stale_profile_issue(
    hass: HomeAssistant,
) -> None:
    """A recovered entry cannot clear another entry's profile recovery issue."""
    unhealthy_entry = MockConfigEntry(
        domain=DOMAIN, data={"address": "unhealthy-device"}
    )
    healthy_entry = MockConfigEntry(domain=DOMAIN, data={"address": "healthy-device"})
    ir.async_create_issue(
        hass,
        DOMAIN,
        _profile_storage_issue_id(unhealthy_entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_ID_PROFILE_STORAGE,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        _profile_storage_issue_id(healthy_entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_ID_PROFILE_STORAGE,
    )
    coordinator = Mock(
        profile_storage_healthy=True,
        async_setup=AsyncMock(),
        async_start=Mock(),
        async_shutdown=AsyncMock(),
    )

    with (
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, healthy_entry)

    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(DOMAIN, _profile_storage_issue_id(unhealthy_entry))
        is not None
    )
    assert (
        registry.async_get_issue(DOMAIN, _profile_storage_issue_id(healthy_entry))
        is None
    )


async def test_removing_entry_only_deletes_its_own_profile_issue(
    hass: HomeAssistant,
) -> None:
    """Removing an entry clears its Repairs issue but retains other entries' issues."""
    removed_entry = MockConfigEntry(domain=DOMAIN, data={"address": "removed-device"})
    retained_entry = MockConfigEntry(domain=DOMAIN, data={"address": "retained-device"})
    for entry in (removed_entry, retained_entry):
        ir.async_create_issue(
            hass,
            DOMAIN,
            _profile_storage_issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_ID_PROFILE_STORAGE,
        )

    with patch(
        "custom_components.lumalou.ProfileStore.async_remove", new=AsyncMock()
    ) as remove_store:
        await async_remove_entry(hass, removed_entry)

    remove_store.assert_awaited_once_with()
    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(DOMAIN, _profile_storage_issue_id(removed_entry))
        is None
    )
    assert (
        registry.async_get_issue(DOMAIN, _profile_storage_issue_id(retained_entry))
        is not None
    )


async def test_unload_closes_coordinator_after_platforms(
    hass: HomeAssistant,
) -> None:
    """Successful platform unload closes coordinator resources."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic-device"})
    coordinator = Mock(async_shutdown=AsyncMock())
    entry.runtime_data = Mock(coordinator=coordinator)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ) as unload:
        assert await async_unload_entry(hass, entry)

    unload.assert_awaited_once_with(entry, PLATFORMS)
    coordinator.async_shutdown.assert_awaited_once_with()


async def test_failed_platform_unload_keeps_coordinator(
    hass: HomeAssistant,
) -> None:
    """A failed unload leaves the still-loaded runtime intact."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic-device"})
    coordinator = Mock(async_shutdown=AsyncMock())
    entry.runtime_data = Mock(coordinator=coordinator)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        assert not await async_unload_entry(hass, entry)

    coordinator.async_shutdown.assert_not_awaited()


async def test_platform_forward_failure_cleans_up(hass: HomeAssistant) -> None:
    """Partial setup cannot leak coordinator resources."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic-device"})
    coordinator = Mock(async_setup=AsyncMock(), async_shutdown=AsyncMock())

    with (
        pytest.raises(RuntimeError, match="synthetic forward failure"),
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=RuntimeError("synthetic forward failure")),
        ),
    ):
        await async_setup_entry(hass, entry)

    coordinator.async_shutdown.assert_awaited_once_with()


async def test_callback_start_failure_cleans_up(hass: HomeAssistant) -> None:
    """A partial Bluetooth callback registration cannot leak resources."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "synthetic-device"})
    coordinator = Mock(
        async_setup=AsyncMock(),
        async_start=Mock(side_effect=RuntimeError("synthetic callback failure")),
        async_shutdown=AsyncMock(),
    )

    with (
        pytest.raises(RuntimeError, match="synthetic callback failure"),
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
    ):
        await async_setup_entry(hass, entry)

    assert entry.runtime_data.coordinator is coordinator
    coordinator.async_shutdown.assert_awaited_once_with()


async def test_removing_entry_deletes_only_its_private_profile_store(
    hass: HomeAssistant,
) -> None:
    """HA convention: entry removal leaves no orphaned .storage profile."""
    entry = MockConfigEntry(domain=DOMAIN, data={"address": "removed-device"})
    backend = Mock(async_remove=AsyncMock(), path="/nonexistent/lumalou.profile")

    with patch(
        "custom_components.lumalou.storage.Store", return_value=backend
    ) as store_class:
        await async_remove_entry(hass, entry)

    assert store_class.call_args.args[2] == f"lumalou.{entry.entry_id}.profile"
    backend.async_remove.assert_awaited_once_with()
