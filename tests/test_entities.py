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
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.setup import async_setup_component
from lumalou import Audio, Color, Song

from custom_components.lumalou import (
    binary_sensor as binary_sensor_platform,
)
from custom_components.lumalou import (
    button as button_platform,
)
from custom_components.lumalou import (
    event as event_platform,
)
from custom_components.lumalou import (
    light as light_platform,
)
from custom_components.lumalou import (
    media_player as media_player_platform,
)
from custom_components.lumalou import (
    number as number_platform,
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
from custom_components.lumalou.binary_sensor import LumalouConnectionBinarySensor
from custom_components.lumalou.button import (
    LumalouCancelRoutineButton,
    LumalouCompleteTaskButton,
    LumalouPreviousTaskButton,
    LumalouStartRoutineButton,
    LumalouSyncClockButton,
)
from custom_components.lumalou.event import LumalouRoutineEvent
from custom_components.lumalou.light import LumalouLight
from custom_components.lumalou.media_player import LumalouMediaPlayer
from custom_components.lumalou.number import (
    LumalouClockBrightnessNumber,
    LumalouRoutineVolumeNumber,
)
from custom_components.lumalou.select import (
    LumalouClockFormatSelect,
    LumalouLightDurationSelect,
    LumalouPlaylistDurationSelect,
)
from custom_components.lumalou.sensor import (
    LumalouCurrentTaskSensor,
    LumalouFirmwareSensor,
    LumalouProfileSyncStatusSensor,
    LumalouRoutineSensor,
)
from custom_components.lumalou.switch import (
    LumalouClockDisplaySwitch,
    LumalouMaintenanceSwitch,
    LumalouRoutineMusicSwitch,
    LumalouRoutineRewardSoundSwitch,
    LumalouRoutinesSwitch,
    LumalouTaskRewardSoundSwitch,
)

FINGERPRINT = "f" * 64


class FakeCoordinator:
    """Small coordinator double matching the entity contract."""

    address = "AA:BB:CC:DD:EE:FF"
    device_name = "Lumalou test"
    sw_version = "test"
    protocol_verified = True

    def __init__(
        self,
        data: dict | None,
        available: bool = True,
        profile_record=None,
    ) -> None:
        self.data = data
        self.available = available
        self.profile_record = profile_record
        self.async_turn_on_light = AsyncMock()
        self.async_turn_off_light = AsyncMock()
        self.async_set_clock_settings = AsyncMock()
        self.async_set_level = AsyncMock()
        self.async_play = AsyncMock()
        self.async_stop_audio = AsyncMock()
        self.async_set_maintenance = AsyncMock()
        self.async_sync_clock = AsyncMock()
        self.async_set_routine_settings = AsyncMock()
        self.async_start_routine = AsyncMock()
        self.async_routine_control = AsyncMock()
        self.routine_phase = "off"
        self.playing_source = None
        self.current_task = "none"
        self.routine_listeners = []

    @property
    def maintenance(self):
        return self.profile_record.maintenance

    @property
    def sync_status(self):
        return self.profile_record.sync_status

    def async_add_listener(self, callback):
        return lambda: None

    def async_add_routine_listener(self, callback):
        self.routine_listeners.append(callback)
        return lambda: self.routine_listeners.remove(callback)


def make_entry(
    data: dict | None,
    *,
    available: bool = True,
    maintenance=False,
    revision=0,
    verified_revision=None,
    desired_profile=None,
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
    coordinator = FakeCoordinator(data, available, profile_record)
    runtime = SimpleNamespace(
        coordinator=coordinator,
        profile_record=profile_record,
    )
    return SimpleNamespace(runtime_data=runtime, unique_id=FINGERPRINT), coordinator


def test_homekit_support_controls_are_not_primary_entities():
    """Default HomeKit export keeps user controls, not maintenance utilities."""
    entry, _ = make_entry({})

    assert LumalouLight(entry).entity_category is None
    assert LumalouMediaPlayer(entry).entity_category is None
    assert LumalouMaintenanceSwitch(entry).entity_category is EntityCategory.CONFIG
    assert LumalouSyncClockButton(entry).entity_category is EntityCategory.CONFIG
    assert LumalouClockFormatSelect(entry).entity_category is EntityCategory.CONFIG
    assert LumalouClockDisplaySwitch(entry).entity_category is EntityCategory.CONFIG
    assert LumalouClockBrightnessNumber(entry).entity_category is EntityCategory.CONFIG
    assert LumalouLightDurationSelect(entry).entity_category is EntityCategory.CONFIG
    assert LumalouPlaylistDurationSelect(entry).entity_category is EntityCategory.CONFIG


@pytest.mark.asyncio
async def test_light_scale_effects_and_commands():
    entry, coordinator = make_entry(
        {"lightStatus": 1, "lightBrightness": 9, "lightColor": int(Color.BLUE)}
    )
    entity = LumalouLight(entry)

    assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert entity.supported_features == LightEntityFeature.EFFECT
    assert entity.brightness == 255
    assert entity.effect == "blue"
    assert entity.effect_list == [color.name.lower() for color in Color]
    await entity.async_turn_on(brightness=128, effect="red")
    coordinator.async_turn_on_light.assert_awaited_once_with(5, int(Color.RED))
    await entity.async_turn_on(brightness=1)
    coordinator.async_turn_on_light.assert_awaited_with(1, None)
    await entity.async_turn_off()
    coordinator.async_turn_off_light.assert_awaited_once_with()


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
        {ATTR_ENTITY_ID: entity.entity_id, ATTR_EFFECT: "red"},
        blocking=True,
    )

    coordinator.async_turn_on_light.assert_awaited_once_with(None, int(Color.RED))


def test_light_off_keeps_the_brightness_the_next_on_uses():
    """The device keeps brightness while off; the entity shows it honestly."""
    entry, _coordinator = make_entry({"lightStatus": 0, "lightBrightness": 3})

    light = LumalouLight(entry)
    assert light.is_on is False
    assert light.brightness == 85


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
    assert entity.source == "pink_noise"
    assert entity.media_title == "Pink Noise"
    assert entity.volume_level == pytest.approx(5 / 9)
    await entity.async_turn_on()
    coordinator.async_play.assert_awaited_once_with(int(Audio.SLEEP_PLAYLIST))
    await entity.async_turn_off()
    coordinator.async_stop_audio.assert_awaited_once_with()
    await entity.async_set_volume_level(0.5)
    coordinator.async_set_level.assert_awaited_with("volume", 4)
    await entity.async_select_source("ocean")
    coordinator.async_play.assert_awaited_with(int(Audio.OCEAN))


def test_unavailable_semantics_and_no_io_from_properties():
    entry, _coordinator = make_entry(None, available=False)
    light = LumalouLight(entry)
    media = LumalouMediaPlayer(entry)
    assert light.available is False
    assert light.is_on is None
    assert light.brightness is None
    assert media.available is False
    assert media.volume_level is None
    assert media.source is None
    clock = (
        LumalouClockFormatSelect(entry),
        LumalouClockDisplaySwitch(entry),
        LumalouClockBrightnessNumber(entry),
    )
    assert not any(entity.available for entity in clock)
    assert clock[0].current_option is None
    assert clock[1].is_on is None
    assert clock[2].native_value is None


async def test_maintenance_remains_usable_offline():
    entry, coordinator = make_entry(None, available=False, maintenance=True)
    entity = LumalouMaintenanceSwitch(entry)
    assert entity.available is True
    assert entity.is_on is True
    await entity.async_turn_off()
    coordinator.async_set_maintenance.assert_awaited_with(False)
    await entity.async_turn_on()
    coordinator.async_set_maintenance.assert_awaited_with(True)


def test_light_effect_is_none_for_unknown_or_missing_color():
    entry, coordinator = make_entry({"lightStatus": 1})
    light = LumalouLight(entry)
    assert light.effect is None
    coordinator.data = {"lightStatus": 1, "lightColor": 99}
    assert light.effect is None


@pytest.mark.asyncio
async def test_device_info_unique_ids_and_buttons():
    entry, coordinator = make_entry({})
    light = LumalouLight(entry)
    info = light.device_info
    assert isinstance(info, dict)
    # The signed-device fingerprint is the identity; the address a connection.
    assert info["identifiers"] == {("lumalou", FINGERPRINT)}
    assert info["connections"] == {(CONNECTION_BLUETOOTH, coordinator.address)}
    assert info["model"] == "Lumalou"
    assert light.unique_id == f"{FINGERPRINT}_light"
    assert LumalouMediaPlayer(entry).unique_id == f"{FINGERPRINT}_media_player"

    await LumalouSyncClockButton(entry).async_press()
    coordinator.async_sync_clock.assert_awaited_once_with()


async def test_clock_entities_show_state_and_change_one_setting():
    """24-hour format is option h24 (device format 1); each writes one field."""
    entry, coordinator = make_entry(
        {"clockDisplay": 1, "clockBrightness": 2, "clockFormat": 0}
    )
    clock_format = LumalouClockFormatSelect(entry)
    display = LumalouClockDisplaySwitch(entry)
    brightness = LumalouClockBrightnessNumber(entry)

    assert clock_format.options == ["h12", "h24"]
    assert clock_format.current_option == "h12"
    assert display.is_on is True
    assert brightness.native_value == 2
    assert (brightness.native_min_value, brightness.native_max_value) == (0, 9)

    await clock_format.async_select_option("h24")
    coordinator.async_set_clock_settings.assert_awaited_with(clock_format=1)
    await display.async_turn_off()
    coordinator.async_set_clock_settings.assert_awaited_with(display=False)
    await display.async_turn_on()
    coordinator.async_set_clock_settings.assert_awaited_with(display=True)
    await brightness.async_set_native_value(7.0)
    coordinator.async_set_clock_settings.assert_awaited_with(brightness=7)


@pytest.mark.asyncio
async def test_light_duration_select_routes_enum():
    entry, coordinator = make_entry({"lightDuration": 4})
    entity = LumalouLightDurationSelect(entry)
    assert entity.options == [
        "min_15",
        "min_30",
        "min_60",
        "min_90",
        "continuous",
        "min_1",
    ]
    assert entity.current_option == "continuous"
    await entity.async_select_option("min_15")
    coordinator.async_set_level.assert_awaited_once_with("light_duration", 0)


@pytest.mark.asyncio
async def test_playlist_duration_select_routes_supported_enum():
    entry, coordinator = make_entry({"playlistDuration": 6})
    entity = LumalouPlaylistDurationSelect(entry)

    assert entity.current_option == "min_1"
    await entity.async_select_option("continuous")
    coordinator.async_set_level.assert_awaited_once_with("playlist_duration", 5)
    coordinator.data = {"playlistDuration": 99}
    assert entity.current_option is None


@pytest.mark.asyncio
async def test_platform_setup_callbacks_add_all_entities():
    """Every entity platform exposes its expected entities."""
    entry, _coordinator = make_entry({})
    add_entities = Mock()

    for platform in (
        binary_sensor_platform,
        button_platform,
        event_platform,
        light_platform,
        media_player_platform,
        number_platform,
        select_platform,
        sensor_platform,
        switch_platform,
    ):
        await platform.async_setup_entry(None, entry, add_entities)

    assert sum(len(call.args[0]) for call in add_entities.call_args_list) == 24


def test_diagnostic_sensor_values_remain_readable_offline():
    """Diagnostic sensors expose cached connection and firmware state."""
    entry, coordinator = make_entry(None, available=False)
    coordinator.sw_version = None

    connection = LumalouConnectionBinarySensor(entry)
    firmware = LumalouFirmwareSensor(entry)
    assert connection.available is True
    assert connection.is_on is False
    assert connection.entity_category is EntityCategory.DIAGNOSTIC
    assert firmware.native_value is None
    assert firmware.available is False

    coordinator.sw_version = "1.2.3"  # advertised while not connected
    assert firmware.available is True
    assert firmware.native_value == "1.2.3"
    coordinator.available = True
    assert connection.is_on is True


def test_profile_sync_status_remains_readable_offline():
    entry, _coordinator = make_entry(None, available=False, sync_status="pending")

    status = LumalouProfileSyncStatusSensor(entry)
    assert status.available is True
    assert status.native_value == "pending"
    assert status.entity_category is EntityCategory.DIAGNOSTIC
    assert status.options == [
        "applying",
        "empty",
        "error",
        "pending",
        "saved",
    ]


@pytest.mark.asyncio
async def test_media_state_sources_volume_steps_and_validation():
    """Media properties and commands handle boundaries and unknown values."""
    entry, coordinator = make_entry(
        {"musicStatus": 0, "currentVolume": 99, "currentSong": int(Song.OCEAN)}
    )
    entity = LumalouMediaPlayer(entry)

    assert entity.state is MediaPlayerState.OFF
    assert entity.volume_level == 1.0
    assert entity.source == "ocean"
    assert "ocean" in entity.source_list
    await entity.async_volume_up()
    coordinator.async_set_level.assert_awaited_once_with("volume", 9)

    coordinator.data = {
        "musicStatus": 1,
        "currentVolume": -5,
        "currentSong": 999,
    }
    assert entity.state is MediaPlayerState.PLAYING
    assert entity.volume_level == 0.0
    assert entity.source is None
    assert entity.media_title is None
    await entity.async_volume_down()
    coordinator.async_set_level.assert_awaited_with("volume", 0)

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_select_source("not_real")
    assert err.value.translation_key == "unsupported_source"
    assert err.value.translation_placeholders == {"source": "not_real"}


@pytest.mark.asyncio
async def test_media_missing_snapshot_commands_are_noops():
    """Volume steps do nothing until a cached volume exists."""
    entry, coordinator = make_entry({})
    entity = LumalouMediaPlayer(entry)

    assert entity.state is None
    assert entity.media_title is None
    await entity.async_volume_up()
    await entity.async_volume_down()
    coordinator.async_set_level.assert_not_awaited()


def test_controls_stay_unavailable_until_protocol_is_verified():
    """Write-capable entities follow the coordinator's verified-control gate."""
    entry, coordinator = make_entry({"lightStatus": 1, "musicStatus": 0})
    coordinator.protocol_verified = False
    controls = (
        LumalouLight(entry),
        LumalouMediaPlayer(entry),
        LumalouLightDurationSelect(entry),
        LumalouPlaylistDurationSelect(entry),
        LumalouSyncClockButton(entry),
        LumalouClockFormatSelect(entry),
        LumalouClockDisplaySwitch(entry),
        LumalouClockBrightnessNumber(entry),
    )

    assert not any(entity.available for entity in controls)
    # Local entities remain usable for recovery.
    assert LumalouMaintenanceSwitch(entry).available is True
    assert LumalouFirmwareSensor(entry).available is True

    coordinator.protocol_verified = True
    assert all(entity.available for entity in controls)
    coordinator.available = False
    assert not any(entity.available for entity in controls)


async def test_routine_buttons_start_and_control_only_while_running():
    """Start is a plain control; the others need a running routine (mode 7)."""
    entry, coordinator = make_entry({"operationMode": 0})
    start = LumalouStartRoutineButton(entry)
    controls = {
        LumalouCompleteTaskButton(entry): 0,
        LumalouPreviousTaskButton(entry): 1,
        LumalouCancelRoutineButton(entry): 4,
    }
    assert start.entity_category is None
    assert start.available
    assert not any(button.available for button in controls)

    coordinator.data = {"operationMode": 7}
    assert all(button.available for button in controls)
    assert not start.available
    coordinator.protocol_verified = False
    assert not any(button.available for button in (start, *controls))
    coordinator.protocol_verified = True

    coordinator.data = {"operationMode": 0}
    await start.async_press()
    coordinator.async_start_routine.assert_awaited_once_with()
    coordinator.data = {"operationMode": 7}
    for button, code in controls.items():
        assert button.entity_category is None
        await button.async_press()
        coordinator.async_routine_control.assert_awaited_with(code)
    assert {button.unique_id for button in controls} == {
        f"{FINGERPRINT}_complete_task",
        f"{FINGERPRINT}_previous_task",
        f"{FINGERPRINT}_cancel_routine",
    }


async def test_routine_setting_switches_and_volume():
    """Routines and the three sounds are booleans; volume is 0..9."""
    entry, coordinator = make_entry(
        {
            "routineModeStatus": 1,
            "routineMusicStatus": 0,
            "taskRewardSfx": 1,
            "routineRewardSfx": 0,
            "routineVolume": 2,
        }
    )
    switches = {
        LumalouRoutinesSwitch(entry): ("enabled", True, True),
        LumalouRoutineMusicSwitch(entry): ("music", False, 1),
        LumalouTaskRewardSoundSwitch(entry): ("task_reward_sfx", True, 1),
        LumalouRoutineRewardSoundSwitch(entry): ("routine_reward_sfx", False, 1),
    }
    for switch, (setting, is_on, on_value) in switches.items():
        assert switch.entity_category is EntityCategory.CONFIG
        assert switch.is_on is is_on
        await switch.async_turn_on()
        coordinator.async_set_routine_settings.assert_awaited_with(
            **{setting: on_value}
        )
        await switch.async_turn_off()
        coordinator.async_set_routine_settings.assert_awaited_with(
            **{setting: False if setting == "enabled" else 0}
        )

    volume = LumalouRoutineVolumeNumber(entry)
    assert volume.entity_category is EntityCategory.CONFIG
    assert volume.native_value == 2
    assert (volume.native_min_value, volume.native_max_value) == (0, 9)
    await volume.async_set_native_value(4.0)
    coordinator.async_set_routine_settings.assert_awaited_with(volume=4)

    coordinator.data = None
    assert LumalouRoutinesSwitch(entry).is_on is None


def test_routine_sensors_are_enums_fed_by_the_coordinator():
    entry, coordinator = make_entry({"operationMode": 7})
    routine = LumalouRoutineSensor(entry)
    task = LumalouCurrentTaskSensor(entry)

    assert routine.options == ["off", "ready", "in_progress", "completed"]
    assert task.options[:4] == ["none", "get_dressed", "wash_up", "brush_teeth"]
    assert len(task.options) == 12
    assert routine.entity_category is None
    coordinator.routine_phase = "in_progress"
    coordinator.current_task = "brush_teeth"
    assert routine.native_value == "in_progress"
    assert task.native_value == "brush_teeth"


async def test_routine_event_entity_fires_coordinator_events(hass: HomeAssistant):
    entry, coordinator = make_entry({"operationMode": 7})
    event = LumalouRoutineEvent(entry)
    event.hass = hass
    event.entity_id = "event.lumalou_routine"
    event.async_write_ha_state = Mock()
    assert event.event_types == [
        "task_completed",
        "routine_completed",
        "routine_cancelled",
    ]

    await event.async_added_to_hass()
    (listener,) = coordinator.routine_listeners
    listener("task_completed", {"task": "brush_teeth"})

    assert event.state_attributes == {
        "event_type": "task_completed",
        "task": "brush_teeth",
    }
    event.async_write_ha_state.assert_called_once()
    for remove in event._on_remove or []:
        remove()
    assert coordinator.routine_listeners == []


def test_media_source_for_playlists_and_the_soother():
    """Songs 1..12 belong to either playlist; the stage or HA's choice tells."""
    entry, coordinator = make_entry(
        {"musicStatus": 1, "currentSong": 1, "currentStage": 1, "lightStatus": 1}
    )
    media = LumalouMediaPlayer(entry)
    light = LumalouLight(entry)
    # The soother (also from the remote): sleep playlist, colours cycling.
    assert media.source == "sleep_playlist"
    assert light.effect is None

    coordinator.data = {"musicStatus": 1, "currentSong": 4, "currentStage": 0}
    assert media.source is None  # a playlist, but not which one
    coordinator.playing_source = int(Audio.CUSTOM_PLAYLIST)
    assert media.source == "custom_playlist"
    coordinator.playing_source = int(Audio.OCEAN)  # replaced on the device
    assert media.source is None

    coordinator.data = {"musicStatus": 0, "currentSong": 4, "currentStage": 1}
    assert media.source is None
    coordinator.data = {"lightStatus": 1, "lightColor": 5, "currentStage": 0}
    assert light.effect == "blue"
