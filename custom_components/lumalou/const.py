"""Lumalou integration constants and explicit command policy."""

from bleak_retry_connector import BLEAK_SAFETY_TIMEOUT, MAX_CONNECT_ATTEMPTS
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
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)
# Schema 3 adds the light and sound block. An older saved record is rejected
# on load, so the device profile is read again (no migration).
PROFILE_SCHEMA_VERSION = 3
# Home Assistant Store file version; independent of the profile schema.
STORE_VERSION = 2
# Bound for GATT work on an established link: the FACTORY read, the session
# handshake after the link is up, and a disconnect.
GATT_TIMEOUT = 20
# ``establish_connection`` bounds every attempt with ``BLEAK_SAFETY_TIMEOUT``
# and retries up to ``MAX_CONNECT_ATTEMPTS`` times; it must govern the
# connect. ``LumalouClient.connect(timeout=...)`` applies one deadline to the
# link *and* the handshake, so it covers the full retry budget plus the
# handshake and the retry backoffs. A shorter guard cancels the first attempt
# before bleak-retry-connector can time out and retry.
CONNECT_TIMEOUT = MAX_CONNECT_ATTEMPTS * BLEAK_SAFETY_TIMEOUT + 3 * GATT_TIMEOUT
RESPONSE_TIMEOUT = 4
RECOVERY_COOLDOWN = 30
RECOVERY_MAX_COOLDOWN = 15 * 60
# An immediate reconnect after a disconnect sometimes fails once on hardware.
RECONNECT_DELAY = 1.5
# The device pushes GLOBAL_STATE right after each write; wait this long for
# the push that confirms a setting before it is saved to the profile.
STATE_CONFIRM_TIMEOUT = 3
# Aggregate SET_GLOBAL_STATE 0x01 and SET_GLOBAL_ON 0x03 (soother),
# SEND_PAIRING_COMPLETE 0x34 and SET_TIME_PRESCALER 0x52 are never sent.
FORBIDDEN_OPCODES = frozenset({0x01, 0x03, 0x34, 0x52})
# Live controls: clock 0x30, volume 0x37, audio off 0x38, brightness 0x3A,
# colour 0x3C, light off 0x3E, play 0x3F, playlist timer 0x42, light timer 0x6C
# (0x53 is the state request). No firmware, nap or routine activation.
ALLOWED_OPCODES = frozenset(
    {0x30, 0x37, 0x38, 0x3A, 0x3C, 0x3E, 0x3F, 0x42, 0x53, 0x6C}
)
# Profile setters used by the restore executor (clock settings 0x79 also by
# the clock entities): playlist 0x40, ready-to-rise status 0x44/times 0x46,
# sleepy times 0x48, ready-to-rise alarms 0x4A, routine mode status 0x58, the
# seven day-routine setters 0x5A..0x66, routine music/rewards 0x69, routine
# volume 0x77 and clock settings 0x79. The light and sound block uses the
# live setters above.
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
# Never nap start/alarm 0x4D/0x4F, routine start or control 0x7B/0x6B, or
# anything firmware/DFU related.
ALLOWED_SEND_OPCODES = ALLOWED_OPCODES | PROFILE_SETTER_OPCODES
# Read-only queries used by strict readback (never the nap alarm queries,
# which time out on the device): global state 0x53, current date 0x31,
# playlist 0x41, ready-to-rise times 0x47, sleepy times 0x49, alarms 0x4C,
# clock settings 0x7A and the seven day-routine requests 0x5B..0x67.
ALLOWED_REQUEST_OPCODES = frozenset(
    {0x31, 0x41, 0x47, 0x49, 0x4C, 0x53, 0x5B, 0x5D, 0x5F, 0x61, 0x63, 0x65, 0x67, 0x7A}
)
CONF_AUTO_RESTORE = "auto_restore"
# A power loss resets every setting, so restoring it automatically is the
# default; users can switch it off.
DEFAULT_AUTO_RESTORE = True
# The device clock has only hour/minute/second/weekday. Deviations within this
# window are BLE/processing latency, not a reset clock.
CLOCK_SYNC_TOLERANCE = 60
# A power loss resets the device clock to 05:00 on Sunday. A clock further off
# than this on reconnect is the reset marker.
RESET_CLOCK_OFFSET = 10 * 60
# Automatic clock writes pause this long (seconds) after a failed one, and
# corrections of drift seen in pushed CURRENT_DATE happen at most this often.
# The clock-sync button and a profile restore still write immediately.
CLOCK_SYNC_RETRY_INTERVAL = 60 * 60
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
