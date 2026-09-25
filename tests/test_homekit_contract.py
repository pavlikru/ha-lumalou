"""HomeKit Bridge mapping contracts for the exposed Lumalou controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from homeassistant.components.homekit.accessories import TYPES, get_accessory
from homeassistant.components.homekit.config_flow import (
    DEFAULT_DOMAINS,
    _exclude_by_entity_registry,
)
from homeassistant.components.homekit.const import (
    CHAR_BRIGHTNESS,
    CHAR_ON,
    FEATURE_ON_OFF,
)
from homeassistant.components.homekit.type_lights import Light as HomeKitLight
from homeassistant.components.homekit.type_media_players import (
    MediaPlayer as HomeKitMediaPlayer,
)
from homeassistant.components.homekit.util import get_media_player_features
from homeassistant.components.light import ColorMode, color_supported
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ADDRESS,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    EntityCategory,
    EntityStateAttribute,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lumalou.const import CONF_DEVICE_FINGERPRINT, DOMAIN
from custom_components.lumalou.media_player import LumalouMediaPlayer
from custom_components.lumalou.models import ProfileRecord


def test_lumalou_speaker_exports_only_homekit_on_off() -> None:
    """Generic HomeKit speaker switches do not expose source or volume controls."""
    entry = SimpleNamespace(
        unique_id="f" * 64, runtime_data=SimpleNamespace(coordinator=Mock())
    )
    features = int(LumalouMediaPlayer(entry).supported_features)
    state = State(
        "media_player.lumalou_audio",
        "off",
        {EntityStateAttribute.SUPPORTED_FEATURES: features},
    )

    assert get_media_player_features(state) == [FEATURE_ON_OFF]


def test_homekit_audio_switch_dispatches_only_play_and_stop() -> None:
    """HomeKit on/off maps to Lumalou's existing player turn_on/turn_off path."""
    accessory = object.__new__(HomeKitMediaPlayer)
    accessory.entity_id = "media_player.lumalou_audio"
    accessory.async_call_service = Mock()

    accessory.set_on_off(True)
    accessory.set_on_off(False)

    assert accessory.async_call_service.call_args_list == [
        call("media_player", SERVICE_TURN_ON, {ATTR_ENTITY_ID: accessory.entity_id}),
        call("media_player", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: accessory.entity_id}),
    ]


def test_homekit_light_has_brightness_but_not_fixed_palette_color() -> None:
    """HomeKit brightness writes do not invent RGB for the preset-only palette."""
    assert not color_supported({ColorMode.BRIGHTNESS})

    accessory = object.__new__(HomeKitLight)
    accessory.entity_id = "light.lumalou_light"
    accessory._pending_events = {CHAR_ON: True, CHAR_BRIGHTNESS: 42}
    accessory.char_brightness = SimpleNamespace(value=100)
    accessory.color_temp_supported = False
    accessory.rgbww_supported = False
    accessory.rgbw_supported = False
    accessory.white_supported = False
    accessory.async_call_service = Mock()

    accessory._async_send_events(None)

    call = accessory.async_call_service.call_args
    assert call.args[:3] == (
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: accessory.entity_id, "brightness_pct": 42},
    )


def test_homekit_default_filter_excludes_lumalou_config_entities() -> None:
    """Only an explicit include lets config controls through the default filter."""
    registry = Mock()
    registry.async_get.return_value = SimpleNamespace(
        entity_category=EntityCategory.CONFIG,
        hidden_by=None,
    )

    assert _exclude_by_entity_registry(
        registry, "switch.lumalou_maintenance", False, False
    )
    assert not _exclude_by_entity_registry(
        registry, "switch.lumalou_maintenance", True, False
    )


@pytest.fixture
async def loaded_lumalou(hass: HomeAssistant) -> MockConfigEntry:
    """Set up every real Lumalou platform against a connected, verified double."""
    coordinator = SimpleNamespace(
        address="AA:BB:CC:DD:EE:01",
        device_name="Lumalou",
        sw_version="1.0",
        available=True,
        protocol_verified=True,
        profile_record=ProfileRecord(),
        maintenance=False,
        sync_status="empty",
        data={
            "lightStatus": 1,
            "lightBrightness": 5,
            "lightColor": 0,
            "musicStatus": 0,
            "currentVolume": 3,
            "lightDuration": 4,
            "playlistDuration": 5,
            "clockDisplay": 1,
            "clockBrightness": 2,
            "clockFormat": 1,
            "operationMode": 0,
            "routineModeStatus": 1,
            "routineMusicStatus": 1,
            "taskRewardSfx": 1,
            "routineRewardSfx": 1,
            "routineVolume": 2,
        },
        routine_phase="off",
        current_task="none",
        async_add_routine_listener=Mock(return_value=lambda: None),
        async_setup=AsyncMock(),
        async_start=Mock(),
        async_shutdown=AsyncMock(),
        async_add_listener=Mock(return_value=lambda: None),
        repair_needed=None,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lumalou",
        unique_id="f" * 64,
        data={CONF_ADDRESS: coordinator.address, CONF_DEVICE_FINGERPRINT: "f" * 64},
    )
    entry.add_to_hass(hass)
    hass.http = Mock()  # media_player registers its image proxy view.
    with (
        patch("homeassistant.setup.async_process_deps_reqs", new_callable=AsyncMock),
        patch(
            "homeassistant.config_entries.async_process_deps_reqs",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.lumalou.coordinator.LumalouCoordinator",
            return_value=coordinator,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_default_homekit_bridge_exports_only_light_and_audio_switch(
    hass: HomeAssistant, loaded_lumalou: MockConfigEntry
) -> None:
    """Apply HomeKit Bridge's default filter and accessory mapping to every entity."""
    entity_registry = er.async_get(hass)
    lumalou_entities = er.async_entries_for_config_entry(
        entity_registry, loaded_lumalou.entry_id
    )
    # Light, audio, three selects, six switches, two numbers, five buttons,
    # five sensors (three diagnostic) and the routine event.
    assert len(lumalou_entities) == 24

    exported: dict[str, str | None] = {}
    uncategorized: set[str] = set()
    accessory_types = {name: Mock(return_value=name) for name in TYPES}
    for registry_entry in lumalou_entities:
        # HomeKit Bridge skips categorized and hidden entities by default.
        if registry_entry.entity_category or registry_entry.hidden_by:
            continue
        uncategorized.add(registry_entry.entity_id)
        state = hass.states.get(registry_entry.entity_id)
        assert state is not None
        # A bridge created in the UI includes its default domains.
        if state.domain not in DEFAULT_DOMAINS:
            continue
        with patch.dict(TYPES, accessory_types):
            exported[state.domain] = get_accessory(hass, Mock(), state, 2, {})

    assert exported == {"light": "Light", "media_player": "MediaPlayer"}
    # Routine controls and status stay in Home Assistant (the Routines and
    # routine sound switches are configuration entities).
    assert uncategorized == {
        "light.lumalou_light",
        "media_player.lumalou_audio",
        "button.lumalou_start_routine",
        "button.lumalou_complete_task",
        "button.lumalou_previous_task",
        "button.lumalou_cancel_routine",
        "sensor.lumalou_routine",
        "sensor.lumalou_current_task",
        "event.lumalou_routine",
    }
    audio = hass.states.get("media_player.lumalou_audio")
    assert audio.attributes["device_class"] == "speaker"
    assert get_media_player_features(audio) == [FEATURE_ON_OFF]
    light = hass.states.get("light.lumalou_light")
    assert light.attributes["supported_color_modes"] == [ColorMode.BRIGHTNESS]
