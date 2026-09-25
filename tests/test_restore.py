"""Pure restore planning: minimal diffs, fixed order and opcode allowlist."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from lumalou.responses import CurrentDate

from custom_components.lumalou.const import (
    ALLOWED_SEND_OPCODES,
    FORBIDDEN_OPCODES,
    GLOBAL_STATE_FIELDS,
)
from custom_components.lumalou.models import DAYS, ProfileValidationError
from custom_components.lumalou.restore import (
    build_restore_steps,
    changed_blocks,
    clock_offset_seconds,
    profile_from_readback,
    set_current_date_payload,
)


@pytest.mark.parametrize("broken", ["week", "playlist", "routine"])
def test_readback_with_wrong_block_shapes_is_rejected(broken):
    """Structural guards in front of the logical validator."""
    state = dict.fromkeys(GLOBAL_STATE_FIELDS, 0)
    week = SimpleNamespace(days=(None,) * (6 if broken == "week" else 7))
    routine = SimpleNamespace(
        time=None, slots=(None,) * (11 if broken == "routine" else 12)
    )
    with pytest.raises(ProfileValidationError, match="not typed"):
        profile_from_readback(
            state,
            playlist=SimpleNamespace(slots=(0,) * (13 if broken == "playlist" else 12)),
            clock=SimpleNamespace(display_on=False, brightness=0, format=0),
            ready_to_rise=week,
            sleepy_times=week,
            alarms=SimpleNamespace(days=(9,) * 7, sound=0),
            routines=dict.fromkeys(DAYS, routine),
        )


def complete_profile() -> dict:
    """A complete synthetic profile; no real family schedule."""
    return {
        "brightness": 3,
        "color": 4,
        "light_duration": 1,
        "volume": 2,
        "playlist_duration": 2,
        "playlist": [1, 2, 3],
        "clock_settings": {"display": True, "brightness": 2, "format": 1},
        "routine_settings": {
            "enabled": False,
            "music": 1,
            "volume": 5,
            "task_reward_sfx": 1,
            "routine_reward_sfx": 1,
        },
        "ready_to_rise": {
            "enabled": False,
            "times": dict.fromkeys(DAYS),
        },
        "sleepy_times": dict.fromkeys(DAYS),
        "alarm": {"days": dict.fromkeys(DAYS, 9), "sound": 0},
        "routines": {day: {"time": None, "slots": [None] * 12} for day in DAYS},
    }


def everything_changed() -> dict:
    desired = complete_profile()
    desired.update(
        brightness=5,
        color=9,
        light_duration=4,
        volume=6,
        playlist_duration=5,
        playlist=[12, 2, 2],
    )
    desired["clock_settings"] = {"display": False, "brightness": 9, "format": 0}
    desired["routine_settings"] = {
        "enabled": True,
        "music": 15,
        "volume": 7,
        "task_reward_sfx": 15,
        "routine_reward_sfx": 0,
    }
    desired["ready_to_rise"] = {
        "enabled": True,
        "times": {day: {"hour": 7, "minute": 5} for day in DAYS},
    }
    desired["sleepy_times"] = {day: {"hour": 19, "minute": 45} for day in DAYS}
    desired["alarm"] = {"days": dict.fromkeys(DAYS, 0), "sound": 3}
    desired["routines"] = {
        day: {
            "time": {"hour": 7, "minute": 0},
            "slots": [{"step": 1, "task": 2}] + [None] * 11,
        }
        for day in DAYS
    }
    return desired


def test_matching_profiles_need_no_writes():
    assert build_restore_steps(complete_profile(), complete_profile()) == ()
    assert changed_blocks(complete_profile(), complete_profile()) == ()


def test_full_diff_uses_documented_order_and_only_allowlisted_setters():
    steps = build_restore_steps(everything_changed(), complete_profile())

    assert [step.name for step in steps] == [
        "clock_settings",
        "light_duration",
        "playlist_duration",
        "volume",
        "playlist",
        "routine_settings.music",
        "routine_settings.volume",
        "sleepy_times",
        "ready_to_rise.times",
        "alarm",
        *(f"routines.{day}" for day in DAYS),
        "color",
        "brightness",
        "ready_to_rise.enabled",
        "routine_settings.enabled",
    ]
    for step in steps:
        assert step.payload[0] in ALLOWED_SEND_OPCODES
        assert step.payload[0] not in FORBIDDEN_OPCODES
    payloads = {step.name: step.payload for step in steps}
    assert payloads["clock_settings"] == bytes([0x79, 0, 0x90])
    assert payloads["playlist"] == bytes([0x40, 12, 2, 2] + [0] * 9)
    assert payloads["routine_settings.music"] == bytes([0x69, 15, 0xF0])
    assert payloads["routine_settings.volume"] == bytes([0x77, 7])
    assert payloads["sleepy_times"] == bytes([0x48] + [0x19, 0x45] * 7)
    assert payloads["ready_to_rise.times"] == bytes([0x46] + [0x07, 0x05] * 7)
    assert payloads["alarm"] == bytes([0x4A, 0x00, 0x00, 0x00, 0x03])
    assert payloads["routines.sunday"] == bytes([0x5A, 0x07, 0x00, 0x12] + [0] * 11)
    assert payloads["routines.saturday"][0] == 0x66
    assert payloads["ready_to_rise.enabled"] == bytes([0x44, 1])
    assert payloads["routine_settings.enabled"] == bytes([0x58, 1])
    assert payloads["brightness"] == bytes([0x3A, 5])


def test_only_differing_sub_blocks_are_written():
    desired = complete_profile()
    observed = deepcopy(desired)
    desired["routines"]["wednesday"]["time"] = {"hour": 18, "minute": 30}
    desired["ready_to_rise"]["enabled"] = True
    desired["routine_settings"]["volume"] = 9

    steps = build_restore_steps(desired, observed)

    assert [step.name for step in steps] == [
        "routine_settings.volume",
        "routines.wednesday",
        "ready_to_rise.enabled",
    ]
    assert changed_blocks(desired, observed) == (
        "ready_to_rise",
        "routine_settings",
        "routines",
    )


def test_midnight_and_no_time_stay_distinct():
    desired = complete_profile()
    desired["sleepy_times"]["monday"] = {"hour": 0, "minute": 0}

    (step,) = build_restore_steps(desired, complete_profile())

    assert step.payload == bytes([0x48, 0xFF, 0xFF, 0, 0] + [0xFF] * 10)


@pytest.mark.parametrize("partial", [{}, {"volume": 1}])
def test_incomplete_profiles_are_never_planned(partial):
    with pytest.raises(ProfileValidationError):
        build_restore_steps(partial, complete_profile())
    with pytest.raises(ProfileValidationError):
        build_restore_steps(complete_profile(), partial)


def test_clock_offset_is_circular_over_the_week():
    zone = ZoneInfo("Etc/GMT-9")  # synthetic fixed offset
    sunday = datetime(2026, 9, 20, 0, 0, 5, tzinfo=zone)  # device weekday 0
    saturday = datetime(2026, 9, 26, 23, 59, 55, tzinfo=zone)  # device weekday 6

    assert clock_offset_seconds(CurrentDate(0, 0, 5, 0), sunday) == 0
    assert clock_offset_seconds(CurrentDate(23, 59, 55, 6), sunday) == 10
    assert clock_offset_seconds(CurrentDate(0, 0, 5, 0), saturday) == 10
    assert clock_offset_seconds(CurrentDate(12, 0, 5, 3), sunday) == (
        3 * 86400 + 12 * 3600
    )
    assert set_current_date_payload(saturday) == bytes([0x30, 0x23, 0x59, 0x55, 6])
