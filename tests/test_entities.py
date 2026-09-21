"""Unit tests for Lumalou Home Assistant entities (no Bluetooth required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.light import ATTR_EFFECT, ColorMode, LightEntityFeature
from homeassistant.components.light.const import DATA_COMPONENT
from homeassistant.components.light.const import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.setup import async_setup_component
from lumalou import Audio, Color

from custom_components.lumalou import (
    binary_sensor as binary_sensor_platform,
)
from custom_components.lumalou import (
    button as button_platform,
)
from custom_components.lumalou import (
    light as light_platform,
)
from custom_components.lumalou import (
    media_player as media_player_platform,
)
from custom_components.lumalou import (
    select as select_platform,
)
from custom_components.lumalou import (
    sensor as sensor_platform,
)
from custom_components.lumalou import (
    switch as switch_platform,
)
from custom_components.lumalou.binary_sensor import (
    LumalouProfilePendingBinarySensor,
    LumalouProfilePresentBinarySensor,
)
from custom_components.lumalou.button import (
    LumalouRefreshButton,
    LumalouSyncClockButton,
)
from custom_components.lumalou.const import SUPPORTED_PRODUCT_CODE
from custom_components.lumalou.light import LumalouLight
from custom_components.lumalou.media_player import LumalouMediaPlayer
from custom_components.lumalou.select import (
    LumalouLightDurationSelect,
    LumalouPlaylistDurationSelect,
)
from custom_components.lumalou.sensor import (
    LumalouAvailabilitySensor,
    LumalouFirmwareSensor,
    LumalouProfileLastErrorSensor,
    LumalouProfileRevisionSensor,
    LumalouProfileSyncStatusSensor,
    LumalouProfileVerifiedRevisionSensor,
)
from custom_components.lumalou.switch import LumalouMaintenanceSwitch


class FakeCoordinator:
    """Small coordinator double matching the entity contract."""

    address = "AA:BB:CC:DD:EE:FF"
    device_name = "Lumalou test"
    sw_version = "test"
    product_code = SUPPORTED_PRODUCT_CODE

    def __init__(
        self,
        data: dict | None,
        available: bool = True,
        profile_record=None,
        profile_storage_healthy: bool = True,
    ) -> None:
        self.data = data
        self.available = available
        self.profile_storage_healthy = profile_storage_healthy
        self.async_set_light = AsyncMock()
        self.async_set_volume = AsyncMock()
        self.async_play = AsyncMock()
        self.async_stop_audio = AsyncMock()
        self.async_set_light_duration = AsyncMock()
        self.async_set_playlist_duration = AsyncMock()
        self.async_set_maintenance = AsyncMock()
        self.async_sync_clock = AsyncMock()
        self.async_request_refresh = AsyncMock()

    def async_add_listener(self, callback):
        return lambda: None


def make_entry(
    data: dict | None,
    *,
    available: bool = True,
    maintenance=False,
    revision=0,
    verified_revision=None,
    desired_profile=None,
    profile_storage_healthy=True,
    pending=False,
    sync_status="empty",
    last_error=None,
):
    profile_record = SimpleNamespace(
        maintenance=maintenance,
        revision=revision,
        verified_revision=verified_revision,
        desired_profile=desired_profile or {},
        pending=pending,
        sync_status=sync_status,
        last_error=last_error,
    )
    coordinator = FakeCoordinator(
        data, available, profile_record, profile_storage_healthy
    )
    runtime = SimpleNamespace(
        coordinator=coordinator,
        profile_record=profile_record,
    )
    return SimpleNamespace(runtime_data=runtime), coordinator


@pytest.mark.asyncio
async def test_light_scale_effects_and_commands():
    entry, coordinator = make_entry(
        {"lightStatus": 1, "lightBrightness": 9, "lightColor": int(Color.BLUE)}
    )
    entity = LumalouLight(entry)

    assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert entity.supported_features == LightEntityFeature.EFFECT
    assert entity.brightness == 255
    assert entity.effect == "BLUE"
    await entity.async_turn_on(brightness=128, effect="RED")
    coordinator.async_set_light.assert_awaited_once_with(True, 5, int(Color.RED))
    await entity.async_turn_off()
    coordinator.async_set_light.assert_awaited_with(False)


async def test_light_service_forwards_fixed_palette_effect(
    hass: HomeAssistant,
) -> None:
    """HA's light service must not filter the declared palette effect."""
    assert await async_setup_component(hass, LIGHT_DOMAIN, {})
    entry, coordinator = make_entry(
        {"lightStatus": 0, "lightBrightness": 0, "lightColor": int(Color.WARM)}
    )
    entity = LumalouLight(entry)
    await hass.data[DATA_COMPONENT].async_add_entities([entity])

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity.entity_id, ATTR_EFFECT: "RED"},
        blocking=True,
    )

    coordinator.async_set_light.assert_awaited_once_with(True, None, int(Color.RED))


def test_light_preserves_observed_zero_brightness():
    """An off device value must remain zero, not be fabricated as one."""
    entry, _coordinator = make_entry({"lightStatus": 0, "lightBrightness": 0})

    assert LumalouLight(entry).brightness == 0


@pytest.mark.asyncio
async def test_media_controls_and_exact_features():
    entry, coordinator = make_entry(
        {"musicStatus": 1, "currentVolume": 5, "currentSong": 13}
    )
    entity = LumalouMediaPlayer(entry)
    expected = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
    assert entity.supported_features == expected
    assert entity.source == "PINK_NOISE"
    assert entity.volume_level == pytest.approx(5 / 9)
    await entity.async_turn_on()
    coordinator.async_play.assert_awaited_once_with(int(Audio.SLEEP_PLAYLIST))
    await entity.async_turn_off()
    coordinator.async_stop_audio.assert_awaited_once_with()
    await entity.async_set_volume_level(0.5)
    coordinator.async_set_volume.assert_awaited_with(4)
    await entity.async_select_source("OCEAN")
    coordinator.async_play.assert_awaited_with(int(Audio.OCEAN))


def test_unavailable_semantics_and_no_io_from_properties():
    entry, coordinator = make_entry(None, available=False)
    light = LumalouLight(entry)
    media = LumalouMediaPlayer(entry)
    assert light.available is False
    assert light.is_on is None
    assert light.brightness is None
    assert media.available is False
    assert media.volume_level is None
    assert media.source is None
    coordinator.async_request_refresh.assert_not_called()


def test_maintenance_remains_usable_offline():
    entry, _coordinator = make_entry(None, available=False, maintenance=True)
    entity = LumalouMaintenanceSwitch(entry)
    assert entity.available is True
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_device_info_unique_ids_and_buttons():
    entry, coordinator = make_entry({})
    light = LumalouLight(entry)
    info = light.device_info
    assert isinstance(info, dict)
    assert info["identifiers"] == {("lumalou", coordinator.address)}
    assert info["connections"] == {(CONNECTION_BLUETOOTH, coordinator.address)}
    assert info["model"] == "Lumalou (GLD09)"
    assert light.unique_id == f"{coordinator.address}_light"

    await LumalouSyncClockButton(entry).async_press()
    coordinator.async_sync_clock.assert_awaited_once_with()
    await LumalouRefreshButton(entry).async_press()
    coordinator.async_request_refresh.assert_awaited_once_with()


def test_unconfirmed_device_info_does_not_claim_gld09():
    """Legacy entries remain model-neutral until the label is confirmed."""
    entry, coordinator = make_entry({})
    coordinator.product_code = None

    assert LumalouLight(entry).device_info["model"] == "Lumalou"


@pytest.mark.asyncio
async def test_light_duration_select_routes_enum():
    entry, coordinator = make_entry({"lightDuration": 4})
    entity = LumalouLightDurationSelect(entry)
    assert entity.current_option == "CONTINUOUS"
    await entity.async_select_option("MIN_15")
    coordinator.async_set_light_duration.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_playlist_duration_select_routes_supported_enum():
    entry, coordinator = make_entry({"playlistDuration": 6})
    entity = LumalouPlaylistDurationSelect(entry)

    assert entity.current_option == "MIN_1"
    await entity.async_select_option("CONTINUOUS")
    coordinator.async_set_playlist_duration.assert_awaited_once_with(5)

    with pytest.raises(ValueError, match="INVALID"):
        await entity.async_select_option("INVALID")


@pytest.mark.asyncio
async def test_platform_setup_callbacks_add_all_entities():
    """Every entity platform exposes its expected entities."""
    entry, _coordinator = make_entry({})
    add_entities = Mock()

    for platform in (
        binary_sensor_platform,
        button_platform,
        light_platform,
        media_player_platform,
        select_platform,
        sensor_platform,
        switch_platform,
    ):
        await platform.async_setup_entry(None, entry, add_entities)

    assert sum(len(call.args[0]) for call in add_entities.call_args_list) == 15


def test_diagnostic_sensor_values_remain_readable_offline():
    """Diagnostic sensors expose cached connection and firmware state."""
    entry, coordinator = make_entry(None, available=False)
    coordinator.sw_version = None

    availability = LumalouAvailabilitySensor(entry)
    firmware = LumalouFirmwareSensor(entry)
    assert availability.available is True
    assert availability.native_value == "unavailable"
    assert firmware.native_value is None

    coordinator.available = True
    coordinator.sw_version = "1.2.3"
    assert availability.native_value == "available"
    assert firmware.native_value == "1.2.3"


def test_profile_diagnostics_remain_readable_offline_without_profile_contents():
    entry, coordinator = make_entry(
        None,
        available=False,
        revision=4,
        verified_revision=2,
        desired_profile={"volume": 3},
        pending=True,
        sync_status="partial",
        last_error="ble_apply",
    )

    revision = LumalouProfileRevisionSensor(entry)
    verified_revision = LumalouProfileVerifiedRevisionSensor(entry)
    pending = LumalouProfilePendingBinarySensor(entry)
    present = LumalouProfilePresentBinarySensor(entry)
    status = LumalouProfileSyncStatusSensor(entry)
    error = LumalouProfileLastErrorSensor(entry)
    assert revision.available is True
    assert revision.native_value == 4
    assert verified_revision.native_value == 2
    assert pending.available is True
    assert pending.is_on is True
    assert present.available is True
    assert present.is_on is True
    entry.runtime_data.profile_record.revision = 0
    assert present.is_on is True
    entry.runtime_data.profile_record.verified_revision = None
    assert verified_revision.native_value is None
    assert status.native_value == "partial"
    assert status.options == [
        "applying",
        "empty",
        "error",
        "partial",
        "pending",
        "saved",
    ]
    assert error.native_value == "ble_apply"

    coordinator.data = {"playlistDuration": 3}
    entry.runtime_data.profile_record.pending = False
    entry.runtime_data.profile_record.revision = 0
    entry.runtime_data.profile_record.desired_profile = {}
    entry.runtime_data.profile_record.last_error = None
    assert pending.is_on is False
    assert present.is_on is False
    assert error.native_value is None


def test_profile_present_unavailable_when_profile_storage_is_unhealthy():
    entry, _coordinator = make_entry(
        None,
        revision=4,
        desired_profile={"volume": 3},
        profile_storage_healthy=False,
    )
    present = LumalouProfilePresentBinarySensor(entry)

    assert present.available is False


@pytest.mark.asyncio
async def test_media_state_sources_volume_steps_and_validation():
    """Media properties and commands handle boundaries and unknown values."""
    entry, coordinator = make_entry(
        {"musicStatus": 0, "currentVolume": 99, "currentAudio": int(Audio.OCEAN)}
    )
    entity = LumalouMediaPlayer(entry)

    assert entity.state is MediaPlayerState.OFF
    assert entity.volume_level == 1.0
    assert entity.source == "OCEAN"
    assert "OCEAN" in entity.source_list
    await entity.async_volume_up()
    coordinator.async_set_volume.assert_awaited_once_with(9)

    coordinator.data = {
        "musicStatus": 1,
        "currentVolume": -5,
        "currentAudio": 999,
        "currentSong": 999,
    }
    assert entity.state is MediaPlayerState.PLAYING
    assert entity.volume_level == 0.0
    assert entity.source is None
    assert entity.media_title is None
    await entity.async_volume_down()
    coordinator.async_set_volume.assert_awaited_with(0)

    with pytest.raises(ValueError, match="Unsupported Lumalou source"):
        await entity.async_select_source("NOT_REAL")


@pytest.mark.asyncio
async def test_media_missing_snapshot_commands_are_noops():
    """Volume steps do nothing until a cached volume exists."""
    entry, coordinator = make_entry({})
    entity = LumalouMediaPlayer(entry)

    assert entity.state is None
    assert entity.media_title is None
    await entity.async_volume_up()
    await entity.async_volume_down()
    coordinator.async_set_volume.assert_not_awaited()
