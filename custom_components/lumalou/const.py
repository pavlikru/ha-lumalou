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
    Platform.EVENT,
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
# A connect right after a disconnect fails on hardware ("BLE connection was
# lost"); this gap is kept from the moment the previous link was closed.
RECONNECT_DELAY = 2.0
# Attempts to open a session when the link drops during connect/handshake.
SESSION_CONNECT_ATTEMPTS = 3
# The device pushes GLOBAL_STATE right after each write; wait this long for
# the push that confirms a setting before it is saved to the profile.
STATE_CONFIRM_TIMEOUT = 3
# A live command waits up to this long for a session that is being opened
# (for example right after a profile write) instead of failing at once.
LIVE_SESSION_WAIT = 15
# Aggregate SET_GLOBAL_STATE 0x01 and SET_GLOBAL_ON 0x03 (soother),
# SEND_PAIRING_COMPLETE 0x34, nap start 0x4D and nap alarm 0x4F, and
# SET_TIME_PRESCALER 0x52 are never sent.
FORBIDDEN_OPCODES = frozenset({0x01, 0x03, 0x34, 0x4D, 0x4F, 0x52})
# Routine start 0x7B and routine control 0x6B are live controls since the
# hardware validation (docs/hardware-validation.md): start only enters routine
# mode with a silent icon preview (step 0), control codes 0..4 are what the
# remote's check-mark button and the app do (complete task, previous task,
# restart, complete all, cancel), and cancel silently returns to normal mode.
# Only these exact payloads are ever sent.
EXACT_SEND_PAYLOADS: dict[int, frozenset[bytes]] = {
    0x7B: frozenset({bytes([0x7B])}),
    0x6B: frozenset(bytes([0x6B, code]) for code in range(5)),
}
# Every application command that is ever sent (0x53 is the state request):
# - live controls: clock 0x30, volume 0x37, audio off 0x38, brightness 0x3A,
#   colour 0x3C, light off 0x3E, play 0x3F, playlist timer 0x42, light timer
#   0x6C, routine start 0x7B and routine control 0x6B;
# - profile setters (restore executor, clock and routine setting entities):
#   playlist 0x40, ready-to-rise status 0x44/times 0x46, sleepy times 0x48,
#   ready-to-rise alarms 0x4A, routine mode status 0x58, the seven day-routine
#   setters 0x5A..0x66, routine music/rewards 0x69, routine volume 0x77 and
#   clock settings 0x79.
# Never nap start/alarm 0x4D/0x4F or anything firmware/DFU related.
ALLOWED_SEND_OPCODES = frozenset(
    {
        0x30,
        0x37,
        0x38,
        0x3A,
        0x3C,
        0x3E,
        0x3F,
        0x40,
        0x42,
        0x44,
        0x46,
        0x48,
        0x4A,
        0x53,
        0x58,
        0x5A,
        0x5C,
        0x5E,
        0x60,
        0x62,
        0x64,
        0x66,
        0x69,
        0x6C,
        0x77,
        0x79,
        *EXACT_SEND_PAYLOADS,
    }
)
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
# A power loss resets the device clock to 05:00:00 on Sunday, from where it
# runs on. A reset needs all of: a clock further off than RESET_CLOCK_OFFSET,
# an offset that is not whole hours (within WHOLE_HOUR_TOLERANCE; that is DST
# or a time zone change, only the clock is set), and a device clock on Sunday
# between 05:00 and 05:00 plus the time since Home Assistant last heard from
# the device (plus RESET_CLOCK_OFFSET slack), at most RESET_WINDOW_MAX.
RESET_CLOCK_OFFSET = 10 * 60
WHOLE_HOUR_TOLERANCE = 2 * 60
# A whole-hour offset is only ambiguous (DST vs power loss) when Home
# Assistant has not heard the device for longer than this.
DST_AMBIGUITY_WINDOW = 60 * 60
RESET_WINDOW_MAX = 12 * 60 * 60
# Automatic clock writes pause this long (seconds) after a failed one, and
# corrections of drift seen in pushed CURRENT_DATE happen at most this often
# (except offsets above RESET_CLOCK_OFFSET, such as a DST change).
# The clock-sync button and a profile restore still write immediately.
CLOCK_SYNC_RETRY_INTERVAL = 60 * 60
# The device pushes CURRENT_DATE every minute; an open session without any
# frame for this long is treated as lost.
SESSION_SILENCE_TIMEOUT = 3 * 60
# Automatic restore attempts per detected power-loss/reset event.
AUTO_RESTORE_MAX_ATTEMPTS = 2
# GLOBAL_STATE operationMode while a routine runs (preview or tasks).
ROUTINE_OPERATION_MODE = 7
# A manual start shows a silent preview (step 0); the first "complete task"
# after this pause makes task 1 current with its music, like a scheduled start.
ROUTINE_START_DELAY = 1.0
# Routine settings profile key -> GLOBAL_STATE field.
ROUTINE_SETTING_FIELDS = {
    "enabled": "routineModeStatus",
    "music": "routineMusicStatus",
    "task_reward_sfx": "taskRewardSfx",
    "routine_reward_sfx": "routineRewardSfx",
    "volume": "routineVolume",
}
# Routine task ids 1..11 as translation keys (device face icons).
ROUTINE_TASKS = (
    "get_dressed",
    "wash_up",
    "brush_teeth",
    "toilet",
    "backpack",
    "meal",
    "story",
    "tidy_up",
    "heart",
    "swirl",
    "star",
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
