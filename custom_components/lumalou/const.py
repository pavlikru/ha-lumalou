"""Lumalou integration constants and explicit command policy."""

from homeassistant.const import Platform

DOMAIN = "lumalou"
# A verified saved profile no longer matches the device (e.g. power loss).
ISSUE_ID_PROFILE_RESTORE_NEEDED = "profile_restore_needed"
CONF_DEVICE_FINGERPRINT = "device_fingerprint"
CONF_PROTOCOL_VERIFIED = "protocol_verified"
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
FORBIDDEN_OPCODES = frozenset({0x34, 0x52})
# No aggregate state, firmware, routine activation, or raw service dispatch.
ALLOWED_OPCODES = frozenset(
    {0x30, 0x37, 0x38, 0x3A, 0x3C, 0x3E, 0x3F, 0x42, 0x53, 0x6C}
)
# Persistent-profile setters used only by the profile restore executor:
# playlist 0x40, ready-to-rise status 0x44/times 0x46, sleepy times 0x48,
# ready-to-rise alarms 0x4A, routine mode status 0x58, the seven day-routine
# setters 0x5A..0x66, routine music/rewards 0x69, routine volume 0x77 and
# clock display settings 0x79.
PROFILE_SETTER_OPCODES = frozenset(
    {
        0x40,
        0x44,
        0x46,
        0x48,
        0x4A,
        0x58,
        0x5A,
        0x5C,
        0x5E,
        0x60,
        0x62,
        0x64,
        0x66,
        0x69,
        0x77,
        0x79,
    }
)
# Never SET_TIME_PRESCALER 0x52, SEND_PAIRING_COMPLETE 0x34, aggregate
# SET_GLOBAL_STATE/ON 0x01/0x03, nap start/alarm 0x4D/0x4F, routine start or
# control 0x7B/0x6B, or anything firmware/DFU related.
ALLOWED_SEND_OPCODES = ALLOWED_OPCODES | PROFILE_SETTER_OPCODES
# Read-only queries used by strict readback: global state 0x53, current date
# 0x31, playlist 0x41, ready-to-rise times 0x47, sleepy times 0x49, alarms
# 0x4C, clock settings 0x7A and the seven day-routine requests 0x5B..0x67.
ALLOWED_REQUEST_OPCODES = frozenset(
    {0x31, 0x41, 0x47, 0x49, 0x4C, 0x53, 0x5B, 0x5D, 0x5F, 0x61, 0x63, 0x65, 0x67, 0x7A}
)
CONF_AUTO_RESTORE = "auto_restore"
DEFAULT_AUTO_RESTORE = False
# The device clock has only hour/minute/second/weekday. Deviations within this
# window are BLE/processing latency, not a reset clock.
CLOCK_SYNC_TOLERANCE = 60
# Automatic restore attempts per detected power-loss/reset event.
AUTO_RESTORE_MAX_ATTEMPTS = 2
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
