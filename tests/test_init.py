"""Config entry lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou import async_setup_entry, async_unload_entry
from custom_components.lumalou.const import DOMAIN, PLATFORMS


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
