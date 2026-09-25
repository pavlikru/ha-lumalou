"""HomeKit Bridge mapping contracts for the exposed Lumalou controls."""

from types import SimpleNamespace
from unittest.mock import Mock, call

from homeassistant.components.homekit.config_flow import (
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
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    EntityCategory,
    EntityStateAttribute,
)
from homeassistant.core import State


def test_lumalou_speaker_exports_only_homekit_on_off() -> None:
    """Generic HomeKit speaker switches do not expose source or volume controls."""
    features = int(
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
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
