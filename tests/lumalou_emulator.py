"""A byte-level Lumalou emulator for end-to-end tests of the real client.

It speaks the encrypted MPID/FE protocol of the pinned library: the real
``SafeLumalouClient`` handshake, encryption, strict requests and push handling
run unchanged. The emulator answers queries, applies setters and pushes
GLOBAL_STATE after each command and CURRENT_DATE when asked, the way firmware
0.3.7 did on hardware. It never touches Bluetooth.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

from lumalou import crypto
from lumalou import protocol as P
from lumalou._generated import COMMANDS, DAY_ROUTINE
from lumalou.profile import (
    ClockSettings,
    MusicPlaylist,
    decode_clock_settings_set,
    decode_music_playlist_set,
    decode_routine_music_settings_set,
    encode_clock_settings,
)
from lumalou.schedules import (
    DAY_ROUTINE_RESPONSES,
    DAYS,
    ClockTime,
    DailyRoutine,
    WeeklyAlarms,
    WeeklyTimes,
    decode_daily_routine,
    decode_weekly_alarms,
    decode_weekly_times,
    encode_daily_routine,
    encode_weekly_alarms,
    encode_weekly_times,
)

FACTORY = "4cea0004-c678-4202-b5d3-712dbb5e5b14"
SESSION = "4cea0005-c678-4202-b5d3-712dbb5e5b14"
TX = "4cea0002-c678-4202-b5d3-712dbb5e5b14"
_NIBBLES = (
    "operationMode",
    "activityState",
    "musicStatus",
    None,  # currentSong high nibble
    None,  # currentSong low nibble
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
)
_WEEK_SETTERS = {
    COMMANDS["SET_R2R_TIMES"]: "r2r_times",
    COMMANDS["SET_SLEEPY_TIMES"]: "sleepy_times",
}
_STATE_SETTERS = {
    COMMANDS["SET_VOLUME"]: "currentVolume",
    COMMANDS["SET_LED_BRIGHTNESS"]: "lightBrightness",
    COMMANDS["SET_PLAYLIST_DURATION"]: "playlistDuration",
    COMMANDS["SET_SOOTHER_MODE_LIGHT_DURATION"]: "lightDuration",
    COMMANDS["SET_R2R_STATUS"]: "ready2RiseStatus",
    COMMANDS["SET_ROUTINE_MODE_STATUS"]: "routineModeStatus",
    COMMANDS["SET_ROUTINE_MODE_VOLUME"]: "routineVolume",
}


def _bcd(value: int) -> int:
    return (value // 10) << 4 | value % 10


def _unbcd(value: int) -> int:
    return 10 * (value >> 4) + (value & 0x0F)


def factory_state() -> dict[str, int]:
    """GLOBAL_STATE after a power loss (hardware findings)."""
    state = dict.fromkeys((name for name in _NIBBLES if name), 0)
    state.update(
        currentSong=0,
        currentVolume=5,
        playlistDuration=5,
        lightBrightness=5,
        clockDisplay=1,
        clockBrightness=2,
        clockFormat=0,
        routineMusicStatus=1,
        taskRewardSfx=1,
        routineRewardSfx=1,
        lightDuration=4,
        routineVolume=5,
    )
    return state


class LumalouEmulator:
    """One device: state, persistent blocks, a running clock and a transport."""

    def __init__(self, token: bytes, device_private_key: Any) -> None:
        self.token = token
        self._device_key = device_private_key
        self.state = factory_state()
        midnight = WeeklyTimes((ClockTime(0, 0),) * 7)
        self.blocks: dict[str, Any] = {
            "playlist": MusicPlaylist(tuple(range(1, 13))),
            "r2r_times": midnight,
            "sleepy_times": midnight,
            "r2r_alarms": WeeklyAlarms((9,) * 7, 0),
        }
        self.routines = {
            day: DailyRoutine(ClockTime(0, 0), (None,) * 12) for day in DAYS
        }
        # Device clock: (hour, minute, second, weekday) at ``clock_base``.
        self.clock = (5, 0, 32, 0)
        self.clock_base = 0.0
        self.push_clock_on_connect = False
        # Routine progress: current step and one state nibble per task id.
        self.routine_step = 0
        self.task_states = [0] * 12
        # Hardware: after a scheduled routine start the session got nothing.
        # Set to drop every push of the current session (a new one works).
        self.silent = False
        # Handshakes that lose the link (as seen on hardware after a replug).
        self.drop_handshakes = 0
        self.link_lost: Any = None
        self.writes: list[bytes] = []
        self._notify: Any = None
        self._key: bytes | None = None
        self._nonce = b""
        self._seq = 0
        self.connected = False

    # ---- device clock ----

    def now(self) -> tuple[int, int, int, int]:
        hour, minute, second, weekday = self.clock
        elapsed = int(asyncio.get_running_loop().time() - self.clock_base)
        total = (weekday * 86400 + hour * 3600 + minute * 60 + second + elapsed) % (
            7 * 86400
        )
        weekday, rest = divmod(total, 86400)
        hour, rest = divmod(rest, 3600)
        minute, second = divmod(rest, 60)
        return hour, minute, second, weekday

    def set_clock(self, hour: int, minute: int, second: int, weekday: int) -> None:
        self.clock = (hour, minute, second, weekday)
        self.clock_base = asyncio.get_running_loop().time()

    # ---- the BleakClient surface used by the restricted transport ----

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        self._key = None

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        assert characteristic == FACTORY
        if self.drop_handshakes:
            self.drop_handshakes -= 1
            self.connected = False
            self.link_lost(self)
        return bytearray(self.token)

    async def start_notify(self, _characteristic: str, callback: Any) -> None:
        self._notify = callback

    async def write_gatt_char(
        self, characteristic: str, data: bytes, *, response: bool
    ) -> None:
        if characteristic == SESSION:
            app_pub, self._nonce = bytes(data[:33]), bytes(data[33:])
            self._key = crypto.derive_session_key(self._device_key, app_pub)[:16]
            self._seq = 0
            self.silent = False
            return
        assert characteristic == TX
        seq = struct.unpack(">I", data[1:5])[0]
        body = P.aes128_ctr(
            self._key,
            P._iv(seq, self._nonce, crypto.token_device_salt(self.token)),
            bytes(data[8:]),
        )
        plaintext = bytes(body[:-1])
        if plaintext == b"\x01\x50\x01":  # ENABLE_RX ends the handshake
            if self.push_clock_on_connect:
                self.push_clock()
            return
        fe = plaintext[2:]
        app = bytes(fe[2 : 2 + fe[1]])
        self.writes.append(app)
        self._handle(app)

    # ---- frames ----

    def _push(self, opcode: int, args: bytes) -> None:
        if self.silent or self._key is None:  # quiet, or no session
            return
        plaintext = P.SSI0_RX_HEADER + P.compose_request(bytes([opcode]) + args)
        self._seq += 1
        h0 = (
            bytes([0x7E])
            + struct.pack(">I", self._seq)
            + struct.pack(">H", len(plaintext) + 1)
        )
        body = plaintext + bytes([P.crc8(plaintext)])
        frame = (
            h0
            + bytes([P.crc8(h0)])
            + P.aes128_ctr(
                self._key,
                P._iv(self._seq, crypto.token_device_salt(self.token), self._nonce),
                body,
            )
        )
        self._notify(None, bytearray(frame))

    def push_state(self) -> None:
        nibbles = []
        for name in _NIBBLES:
            nibbles.append(self.state[name] if name else 0)
        nibbles[3], nibbles[4] = (
            self.state["currentSong"] >> 4,
            (self.state["currentSong"] & 0x0F),
        )
        self._push(
            0x02, bytes(nibbles[i] << 4 | nibbles[i + 1] for i in range(0, 26, 2))
        )

    def push_routine_status(self) -> None:
        states = self.task_states
        self._push(
            0x94,
            bytes([self.routine_step])
            + bytes(states[i] << 4 | states[i + 1] for i in range(0, 12, 2)),
        )

    def push_clock(self) -> None:
        self._push(0x13, bytes(_bcd(value) for value in self.now()))

    def _clock_settings(self) -> bytes:
        return encode_clock_settings(
            ClockSettings(
                bool(self.state["clockDisplay"]),
                self.state["clockBrightness"],
                self.state["clockFormat"],
            )
        )

    def _handle(self, app: bytes) -> None:
        opcode, args = app[0], app[1:]
        requests = {
            COMMANDS["REQUEST_GLOBAL_STATE"]: self.push_state,
            COMMANDS["REQUEST_CURRENT_DATE"]: self.push_clock,
            COMMANDS["REQUEST_ROUTINE_TASK_STATUS"]: self.push_routine_status,
            COMMANDS["REQUEST_MUSIC_PLAYLIST"]: lambda: self._push(
                0x19, bytes(self.blocks["playlist"].slots)
            ),
            COMMANDS["REQUEST_CLOCK_SETTINGS"]: lambda: self._push(
                0x99, self._clock_settings()
            ),
            COMMANDS["REQUEST_R2R_TIMES"]: lambda: self._push(
                0x22, encode_weekly_times(self.blocks["r2r_times"])
            ),
            COMMANDS["REQUEST_SLEEPY_TIMES"]: lambda: self._push(
                0x23, encode_weekly_times(self.blocks["sleepy_times"])
            ),
            COMMANDS["REQUEST_R2R_ALARMS"]: lambda: self._push(
                0x27, encode_weekly_alarms(self.blocks["r2r_alarms"])
            ),
        }
        if opcode in requests:
            requests[opcode]()
            return
        for day, request in DAY_ROUTINE["REQUEST"].items():
            if opcode == request:
                self._push(
                    DAY_ROUTINE_RESPONSES[day], encode_daily_routine(self.routines[day])
                )
                return
        self._apply(opcode, args)
        self.push_state()

    def _apply(self, opcode: int, args: bytes) -> None:
        if opcode == COMMANDS["SET_CURRENT_DATE"]:
            self.set_clock(*(_unbcd(value) for value in args))
        elif opcode in _STATE_SETTERS:
            self.state[_STATE_SETTERS[opcode]] = args[0]
        elif opcode == COMMANDS["SET_LIGHT_COLOR"]:
            self.state.update(lightColor=args[0], lightStatus=1, activityState=1)
        elif opcode == COMMANDS["TURN_OFF_CLOUD_BACKLIGHT"]:
            self.state.update(lightStatus=0, activityState=0)
        elif opcode == COMMANDS["SET_CLOCK_SETTINGS"]:
            clock = decode_clock_settings_set(args)
            self.state.update(
                clockDisplay=int(clock.display_on),
                clockBrightness=clock.brightness,
                clockFormat=clock.format,
            )
        elif opcode == COMMANDS["SET_MUSIC_PLAYLIST"]:
            self.blocks["playlist"] = decode_music_playlist_set(args)
        elif opcode in _WEEK_SETTERS:
            self.blocks[_WEEK_SETTERS[opcode]] = decode_weekly_times(args)
        elif opcode == COMMANDS["SET_R2R_ALARMS"]:
            self.blocks["r2r_alarms"] = decode_weekly_alarms(args)
        elif opcode == COMMANDS["SET_ROUTINE_MUSIC_STATUS"]:
            music = decode_routine_music_settings_set(args)
            self.state.update(
                routineMusicStatus=music.music,
                taskRewardSfx=music.task_reward,
                routineRewardSfx=music.routine_reward,
            )
        else:
            for day, setter in DAY_ROUTINE["SET"].items():
                if opcode == setter:
                    self.routines[day] = decode_daily_routine(args)
                    return
            raise AssertionError(f"unexpected command 0x{opcode:02x}")
