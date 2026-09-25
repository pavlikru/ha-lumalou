"""Power loss end to end: the real client and frames against a device emulator.

Reproduces the 0.1.0b6 hardware run: Home Assistant in UTC-3 on a Friday
evening, the device replugged and back with its factory clock (CURRENT_DATE
``05 00 32 00``: Sunday 05:00:32) and factory settings, and a CURRENT_DATE
push right after the session starts.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from bleak.backends.device import BLEDevice
from lumalou import crypto
from lumalou.schedules import ClockTime, DailyRoutine, RoutineTask

from custom_components.lumalou.const import (
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
)
from custom_components.lumalou.coordinator import LumalouCoordinator
from custom_components.lumalou.models import DAYS, ProfileRecord, routine_from_tasks
from tests.lumalou_emulator import LumalouEmulator

FINGERPRINT = "c" * 64
ZONE = ZoneInfo("Etc/GMT+3")  # UTC-3
FRIDAY_EVENING = datetime(2026, 9, 25, 18, 27, 31, tzinfo=ZONE)


def _daily(routine: dict) -> DailyRoutine:
    """The device form of a saved day routine."""
    return DailyRoutine(
        ClockTime(routine["time"]["hour"], routine["time"]["minute"]),
        tuple(
            None if slot is None else RoutineTask(slot["step"], slot["task"])
            for slot in routine["slots"]
        ),
    )


def owner_profile() -> dict:
    """The saved, verified profile before the power loss."""
    midnight = {"hour": 0, "minute": 0}
    routines = {day: {"time": midnight, "slots": [None] * 12} for day in DAYS}
    routines["friday"] = routine_from_tasks({"hour": 16, "minute": 0}, [3, 4])
    return {
        "playlist": list(range(1, 13)),
        "clock_settings": {"display": True, "brightness": 2, "format": 1},
        "routine_settings": {
            "enabled": True,
            "music": 1,
            "volume": 2,
            "task_reward_sfx": 1,
            "routine_reward_sfx": 1,
        },
        "ready_to_rise": {"enabled": False, "times": dict.fromkeys(DAYS, midnight)},
        "sleepy_times": dict.fromkeys(DAYS, midnight),
        "alarm": {"days": dict.fromkeys(DAYS, 9), "sound": 0},
        "routines": routines,
        "light_and_sound": {
            "volume": 3,
            "light_brightness": 3,
            "light_duration": 4,
            "playlist_duration": 5,
        },
    }


@pytest.fixture
async def power_loss():
    private_key, public_key = crypto.generate_keypair()
    device = LumalouEmulator(bytes(25) + public_key + bytes(134), private_key)
    device.set_clock(5, 0, 32, 0)
    ble_device = BLEDevice("synthetic-device", "Test Lumalou", {})
    store = SimpleNamespace(
        async_load=AsyncMock(
            return_value=ProfileRecord(
                revision=1,
                desired_profile=owner_profile(),
                verified_revision=1,
                verified_fingerprint=FINGERPRINT,
                sync_status="saved",
            )
        ),
        async_save=AsyncMock(),
    )

    def background(hass, coro, name, *, eager_start=True):
        return asyncio.create_task(coro)

    entry = SimpleNamespace(
        data={
            "address": ble_device.address,
            CONF_DEVICE_FINGERPRINT: FINGERPRINT,
            CONF_PROTOCOL_VERIFIED: True,
        },
        options={"auto_restore": True},
        title="Test Lumalou",
        entry_id="synthetic",
        async_create_background_task=Mock(side_effect=background),
    )
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))

    async def connect(*args, disconnected_callback, **kwargs):
        device.link_lost = disconnected_callback
        return device

    coordinator = LumalouCoordinator(hass, entry, store)
    with (
        patch(
            "custom_components.lumalou.transport.establish_connection",
            new=AsyncMock(side_effect=connect),
        ),
        patch(
            "custom_components.lumalou.transport.bluetooth.async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.lumalou.coordinator.bluetooth.async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "lumalou.client.parse_factory_device_fingerprint",
            return_value=FINGERPRINT,
        ),
        patch("lumalou.client.WRITE_SPACING", 0),
        patch(
            "custom_components.lumalou.coordinator.dt_util.now",
            return_value=FRIDAY_EVENING,
        ),
        patch("custom_components.lumalou.coordinator.RECONNECT_DELAY", 0),
        patch("custom_components.lumalou.coordinator.STATE_CONFIRM_TIMEOUT", 0.2),
        patch("custom_components.lumalou.coordinator.ROUTINE_SILENCE_TIMEOUT", 0.3),
    ):
        await coordinator.async_setup()
        yield SimpleNamespace(
            coordinator=coordinator,
            device=device,
            store=store,
            friday_routine=_daily(owner_profile()["routines"]["friday"]),
        )
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("clock_push", "dropped", "clock"),
    [
        (False, 0, (5, 0, 32, 0)),
        # The device streams its clock at connect: the correction it asks
        # for waits behind the reconnect and never runs before the check.
        (True, 0, (5, 0, 32, 0)),
        # As logged: the first handshake loses the link, the retry connects.
        (True, 1, (5, 0, 32, 0)),
        # A short outage whose clock survived (stopped for the outage):
        # only the factory settings show the reset.
        (True, 1, (18, 26, 1, 5)),
    ],
)
async def test_power_loss_after_replug_is_restored_automatically(
    power_loss, clock_push, dropped, clock, caplog
):
    coordinator, device = power_loss.coordinator, power_loss.device
    device.push_clock_on_connect = clock_push
    device.drop_handshakes = dropped
    device.set_clock(*clock)
    # Home Assistant last heard the device a minute before the power loss.
    coordinator._last_frame_at = asyncio.get_running_loop().time() - 60

    with caplog.at_level("INFO"):
        await coordinator._async_recover()

    assert coordinator.restore_needed is None
    result = coordinator.last_restore_result
    assert result is not None and result.verified and result.automatic
    assert device.state["clockFormat"] == 1  # 24-hour clock again
    assert deepcopy(device.state["routineModeStatus"]) == 1
    # The raw device clock and every criterion are in the log.
    hour, minute, _second, _weekday = clock
    assert f"clock on connect {hour:02d}:{minute:02d}:" in caplog.text
    assert "reset check: factory settings True" in caplog.text
    assert "reset True" in caplog.text


async def test_session_that_goes_quiet_at_a_routine_start_is_reconnected(
    power_loss,
):
    """0.1.0b7 on hardware: after a scheduled start nothing arrived for 4 min.

    The device keeps sending single-value frames that Home Assistant does not
    use; no state, clock or routine frame comes. Within the routine timeout
    Home Assistant reconnects, reads the routine status and shows the current
    task, without events for steps it did not see; later steps fire events.
    """
    coordinator, device = power_loss.coordinator, power_loss.device
    device.set_clock(18, 27, 31, 5)
    device.state.update(clockFormat=1, routineModeStatus=1, routineVolume=2)
    device.routines["friday"] = power_loss.friday_routine
    coordinator.present = True
    fired = []
    coordinator.async_add_routine_listener(
        lambda event, attributes: fired.append((event, attributes))
    )
    await coordinator._async_recover()
    assert coordinator.available and coordinator.restore_needed is None

    # The scheduled start: routine mode with the silent preview ...
    device.state["operationMode"] = 7
    device.push_state()
    device.push_routine_status()
    assert coordinator.routine_phase == "ready"
    # ... then the session goes quiet except for unused single-value frames,
    # while the device moves on to the first task.
    device.silent = True
    first = coordinator._client
    device.routine_step, device.task_states[2] = 1, 1  # brush teeth current
    for _ in range(4):
        if coordinator._client is not first:
            break
        device.silent = False
        device._push(0x1E, bytes([7]))  # OPERATION_MODE: not a state frame
        device.silent = True
        await asyncio.sleep(0.1)

    for _ in range(50):
        if coordinator.available and coordinator._client is not first:
            break
        await asyncio.sleep(0.05)
    assert coordinator._client is not first
    assert coordinator.routine_phase == "in_progress"
    assert coordinator.current_task == "brush_teeth"
    assert fired == []

    # The new session sees the next step: that transition is an event.
    device.routine_step, device.task_states[2], device.task_states[3] = 2, 2, 1
    device.push_routine_status()
    assert fired == [("task_completed", {"task": "brush_teeth"})]
    assert coordinator.current_task == "toilet"
