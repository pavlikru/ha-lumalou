"""Lumalou integration constants and explicit command policy."""

from homeassistant.const import Platform

DOMAIN = "lumalou"
ISSUE_ID_PROFILE_STORAGE = "profile_storage"
CONF_PRODUCT_CODE = "product_code"
CONF_IDENTIFICATION_SOURCE = "identification_source"
CONF_DEVICE_FINGERPRINT = "device_fingerprint"
# Read only while replacing legacy entry data during reconfigure.
CONF_FACTORY_ITEM_CODE = "factory_item_code"
CONF_PROTOCOL_VERIFIED = "protocol_verified"
IDENTIFICATION_SOURCE_DEVICE_INFORMATION = "device_information"
IDENTIFICATION_SOURCE_FACTORY_TOKEN = "factory_token"
SUPPORTED_PRODUCT_CODE = "GLD09"
PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)
PROFILE_SCHEMA_VERSION = 2
CONNECT_TIMEOUT = 20
RESPONSE_TIMEOUT = 4
RECOVERY_COOLDOWN = 30
RECOVERY_MAX_COOLDOWN = 15 * 60
DFU_SERVICE = "00001530-1212-efde-1523-785feabcd123"
FORBIDDEN_OPCODES = frozenset({0x34, 0x52})
# No aggregate state, firmware, routine activation, or raw service dispatch.
ALLOWED_OPCODES = frozenset(
    {0x30, 0x37, 0x38, 0x3A, 0x3C, 0x3E, 0x3F, 0x42, 0x53, 0x6C}
)
WRITE_CHARACTERISTICS = frozenset(
    {
        "4cea0002-c678-4202-b5d3-712dbb5e5b14",
        "4cea0005-c678-4202-b5d3-712dbb5e5b14",
    }
)

GLOBAL_STATE_FIELDS = frozenset(
    {
        "operationMode",
        "activityState",
        "musicStatus",
        "currentSong",
        "currentVolume",
        "playlistDuration",
        "lightStatus",
        "lightBrightness",
        "lightColor",
        "napTimeStatus",
        "napDuration",
        "ready2RiseStatus",
        "ready2RiseAlarmStatus",
        "timePrescaler",
        "currentStage",
        "clockDisplay",
        "clockBrightness",
        "clockFormat",
        "routineMusicStatus",
        "taskRewardSfx",
        "routineRewardSfx",
        "lightDuration",
        "routineVolume",
        "routineModeStatus",
        "alarmExecuting",
    }
)
