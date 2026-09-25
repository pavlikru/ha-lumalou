"""Routine control, progress events and one-off routines; all Bluetooth is mocked."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from lumalou.schedules import (
    ClockTime,
    DailyRoutine,
    RoutineTask,
    RoutineTaskStatus,
    decode_daily_routine,
)

from custom_components.lumalou.const import (
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    CONF_TEMPORARY_ROUTINE_DAY,
)
from custom_components.lumalou.coordinator import LumalouCoordinator
from custom_components.lumalou.models import (
    ProfileRecord,
    ProfileValidationError,
    RevisionConflictError,
    routine_from_tasks,
    routine_task_ids,
)
from custom_components.lumalou.restore import day_routine_payload
from tests import test_coordinator
from tests.test_coordinator import FINGERPRINT, live, sends, verified_profile

# The shared synthetic device session (a pytest fixture).
rig = test_coordinator.rig

# The rig's clock is Sunday noon; Sunday's saved routine is brush teeth at 20:00.
SAVED_SUNDAY = routine_from_tasks({"hour": 20, "minute": 0}, [3])
START = bytes([0x7B])
COMPLETE = bytes([0x6B, 0])


def status(step: int, **states: int) -> RoutineTaskStatus:
    """Build a task status: ``t3=1`` means task id 3 is current."""
    nibbles = [0] * 12
    for name, value in states.items():
        nibbles[int(name[1:]) - 1] = value
    return RoutineTaskStatus(step, tuple(nibbles))


def push_status(rig, value: RoutineTaskStatus) -> None:
    """Deliver a pushed ROUTINE_TASK_STATUS frame of the live session."""
    rig.clients[-1].on_response(SimpleNamespace(opcode=0x94, decode=lambda: value))


def push_mode(rig, mode: int) -> None:
    """The device pushes GLOBAL_STATE with a new operation mode."""
    rig.state["operationMode"] = mode
    rig.clients[-1].on_state(deepcopy(rig.state))


def events(coordinator) -> list[tuple[str, dict[str, str]]]:
    fired: list[tuple[str, dict[str, str]]] = []
    coordinator.async_add_routine_listener(
        lambda event, attributes: fired.append((event, attributes))
    )
    return fired


def saved_data(rig) -> list[dict]:
    """Entry data written through async_update_entry, oldest first."""
    return [
        call.kwargs["data"]
        for call in rig.hass.config_entries.async_update_entry.call_args_list
    ]


async def drain(rig) -> None:
    await asyncio.gather(*rig.background_tasks, return_exceptions=True)


async def verified_with_routine(rig) -> None:
    rig.fake.routines["sunday"] = DailyRoutine(
        ClockTime(20, 0), (RoutineTask(1, 3), *([None] * 11))
    )
    await verified_profile(rig)
    assert rig.coordinator.profile_record.desired_profile["routines"]["sunday"] == (
        SAVED_SUNDAY
    )


async def test_routine_settings_are_confirmed_and_kept_in_the_profile(rig):
    coordinator = rig.coordinator
    rig.state.update(taskRewardSfx=1, routineRewardSfx=0, routineVolume=5)
    await verified_profile(rig)
    revision = coordinator.profile_record.revision
    start = len(rig.journal)

    await coordinator.async_set_routine_settings(enabled=True)
    await coordinator.async_set_routine_settings(music=1)
    await coordinator.async_set_routine_settings(volume=2)

    assert sends(rig, start) == [
        bytes([0x58, 1]),
        bytes([0x69, 1, 0x10]),
        bytes([0x77, 2]),
    ]
    record = coordinator.profile_record
    assert record.desired_profile["routine_settings"] == {
        "enabled": True,
        "music": 1,
        "volume": 2,
        "task_reward_sfx": 1,
        "routine_reward_sfx": 0,
    }
    assert record.revision == revision
    assert record.is_verified


@pytest.mark.parametrize(
    "changes",
    [{}, {"enabled": 1}, {"music": 2}, {"volume": 10}, {"volume": True}, {"x": 1}],
)
async def test_routine_settings_validation_before_ble(rig, changes):
    await live(rig)
    with pytest.raises(ProfileValidationError):
        await rig.coordinator.async_set_routine_settings(**changes)
    assert not sends(rig)


async def test_routine_control_only_while_a_routine_runs(rig):
    coordinator = rig.coordinator
    await live(rig)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_routine_control(0)
    assert err.value.translation_key == "routine_not_running"
    with pytest.raises(ProfileValidationError):
        await coordinator.async_routine_control(5)

    push_mode(rig, 7)
    await coordinator.async_routine_control(0)
    await coordinator.async_routine_control(1)
    await coordinator.async_routine_control(4)

    assert sends(rig) == [COMPLETE, bytes([0x6B, 1]), bytes([0x6B, 4])]
    assert rig.state["operationMode"] == 0


async def test_start_routine_starts_then_makes_the_first_task_current(rig):
    """Like a scheduled start: preview, then task 1 with its music."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    start = len(rig.journal)

    await coordinator.async_start_routine()

    assert sends(rig, start) == [START, COMPLETE]
    assert not coordinator.temporary_routine_active
    rig.hass.config_entries.async_update_entry.reset_mock()

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_start_routine()
    assert err.value.translation_key == "routine_running"
    rig.hass.config_entries.async_update_entry.assert_not_called()


async def test_routine_progress_events_and_sensor_values(rig):
    """Pushed task status drives the sensors and the task/routine events."""
    coordinator = rig.coordinator
    fired = events(coordinator)
    assert coordinator.routine_phase is None
    assert coordinator.current_task is None
    await live(rig)
    assert (coordinator.routine_phase, coordinator.current_task) == ("off", "none")

    # A status while no routine runs changes nothing and fires nothing.
    push_status(rig, status(1, t3=1))
    push_mode(rig, 7)
    push_status(rig, status(0))
    assert (coordinator.routine_phase, coordinator.current_task) == ("ready", "none")
    push_status(rig, status(1, t3=1))
    assert coordinator.routine_phase == "in_progress"
    assert coordinator.current_task == "brush_teeth"
    push_status(rig, status(2, t3=2, t4=1))
    assert coordinator.current_task == "toilet"
    push_status(rig, status(3, t3=2, t4=2))
    assert (coordinator.routine_phase, coordinator.current_task) == (
        "completed",
        "none",
    )
    # The device resets the status, then leaves routine mode.
    push_status(rig, status(0))
    push_mode(rig, 0)

    assert fired == [
        ("task_completed", {"task": "brush_teeth"}),
        ("task_completed", {"task": "toilet"}),
        ("routine_completed", {}),
    ]
    assert coordinator.routine_status is None
    assert (coordinator.routine_phase, coordinator.current_task) == ("off", "none")


async def test_routine_left_before_its_last_step_is_cancelled(rig):
    coordinator = rig.coordinator
    fired = events(coordinator)
    await live(rig)
    push_mode(rig, 7)
    # Unknown until the device pushes a status.
    assert (coordinator.routine_phase, coordinator.current_task) == (None, None)
    push_status(rig, status(1, t1=1))
    push_mode(rig, 0)

    assert fired == [("routine_cancelled", {})]


async def test_routine_progress_is_not_compared_across_sessions(rig):
    """A reconnect mid-routine neither completes a task nor cancels."""
    coordinator = rig.coordinator
    fired = events(coordinator)
    await live(rig)
    push_mode(rig, 7)
    push_status(rig, status(1, t1=1))
    await coordinator._disconnect()
    assert coordinator.routine_status is None
    rig.state["operationMode"] = 0
    await live(rig)
    push_status(rig, status(1, t1=2))

    assert fired == []


async def test_one_off_routine_runs_once_then_the_saved_routine_returns(rig):
    """Tasks for this run only: written for today, written back at the end."""
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    record = coordinator.profile_record
    temporary = routine_from_tasks({"hour": 20, "minute": 0}, [8, 7])
    start = len(rig.journal)

    await coordinator.async_start_routine([8, 7])

    assert sends(rig, start) == [
        day_routine_payload("sunday", temporary),
        START,
        COMPLETE,
    ]
    assert coordinator.temporary_routine_active
    assert saved_data(rig)[-1][CONF_TEMPORARY_ROUTINE_DAY] == "sunday"
    assert rig.fake.routines["sunday"].slots[:2] == (
        RoutineTask(1, 8),
        RoutineTask(2, 7),
    )

    # The child finishes; the device leaves routine mode.
    start = len(rig.journal)
    push_mode(rig, 0)
    await drain(rig)

    assert sends(rig, start) == [day_routine_payload("sunday", SAVED_SUNDAY)]
    assert not coordinator.temporary_routine_active
    assert CONF_TEMPORARY_ROUTINE_DAY not in saved_data(rig)[-1]
    assert (
        decode_daily_routine(day_routine_payload("sunday", SAVED_SUNDAY)[1:])
        == (rig.fake.routines["sunday"])
    )
    # The saved profile never changed.
    assert coordinator.profile_record == record


async def test_one_off_routine_is_written_back_after_a_dropped_session(rig):
    """No false Repair while it runs; the saved routine returns on reconnect."""
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    await coordinator.async_start_routine([8, 7])
    await coordinator._disconnect()

    # Reconnect while the one-off routine still runs: nothing is flagged.
    await coordinator._async_recover()
    assert coordinator.restore_needed is None
    assert coordinator.temporary_routine_active
    assert rig.fake.routines["sunday"].slots[0] == RoutineTask(1, 8)

    # It ended while disconnected: the next connection writes it back.
    await coordinator._disconnect()
    rig.state["operationMode"] = 0
    start = len(rig.journal)
    await coordinator._async_recover()

    assert sends(rig, start) == [day_routine_payload("sunday", SAVED_SUNDAY)]
    assert coordinator.restore_needed is None
    assert not coordinator.temporary_routine_active

    # Already back (e.g. after a restore): only the marker is cleared.
    coordinator._set_temporary_routine_day("sunday")
    await coordinator._disconnect()
    start = len(rig.journal)
    await coordinator._async_recover()
    assert not sends(rig, start)
    assert not coordinator.temporary_routine_active


async def test_one_off_routine_marker_survives_a_restart(rig):
    entry = rig.coordinator.entry
    entry.data = {**entry.data, CONF_TEMPORARY_ROUTINE_DAY: "friday"}
    assert LumalouCoordinator(rig.hass, entry, rig.store).temporary_routine_active
    entry.data[CONF_TEMPORARY_ROUTINE_DAY] = "someday"
    assert not LumalouCoordinator(rig.hass, entry, rig.store).temporary_routine_active


async def test_failed_write_back_drops_the_session_and_keeps_the_marker(rig):
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    await coordinator.async_start_routine([8, 7])
    rig.settings.fail_opcode = 0x5A  # Sunday routine setter

    push_mode(rig, 0)
    await drain(rig)

    assert not coordinator.available
    assert coordinator.temporary_routine_active


async def test_write_back_waits_for_a_usable_session(rig):
    """Queued write-back rechecks the session, mode and maintenance."""
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    await coordinator.async_start_routine([8, 7])
    start = len(rig.journal)

    for prepare in (
        lambda: coordinator._set_temporary_routine_day(None),
        lambda: setattr(coordinator, "_client", None),
        lambda: setattr(coordinator, "data", None),
        lambda: coordinator.data.update(operationMode=7),
    ):
        coordinator._set_temporary_routine_day("sunday")
        client, data = coordinator._client, deepcopy(coordinator.data)
        prepare()
        await coordinator._async_end_temporary_routine()
        coordinator._client, coordinator.data = client, data

    assert not sends(rig, start)
    assert coordinator.temporary_routine_active


async def test_one_off_routine_equal_to_the_saved_one_changes_nothing(rig):
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    start = len(rig.journal)

    await coordinator.async_start_routine([3])

    assert sends(rig, start) == [START, COMPLETE]
    assert not coordinator.temporary_routine_active


async def test_start_writes_back_an_earlier_one_off_routine_first(rig):
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    coordinator._set_temporary_routine_day("saturday")
    saturday = coordinator.profile_record.desired_profile["routines"]["saturday"]
    start = len(rig.journal)

    await coordinator.async_start_routine()

    assert sends(rig, start) == [
        day_routine_payload("saturday", saturday),
        START,
        COMPLETE,
    ]
    assert not coordinator.temporary_routine_active


@pytest.mark.parametrize("tasks", [[], [3, 3], [12]])
async def test_start_routine_rejects_invalid_tasks_before_ble(rig, tasks):
    await verified_profile(rig)
    start = len(rig.journal)
    with pytest.raises(ProfileValidationError):
        await rig.coordinator.async_start_routine(tasks)
    assert not sends(rig, start)


async def test_one_off_routine_needs_trusted_time_and_a_profile(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    with (
        patch(
            "custom_components.lumalou.coordinator.dt_util.now",
            return_value=datetime(2020, 1, 1),
        ),
        pytest.raises(ServiceValidationError, match="not trustworthy"),
    ):
        await coordinator.async_start_routine([3])

    coordinator._profile_record = ProfileRecord(revision=1)
    with pytest.raises(ServiceValidationError, match="Read and verify"):
        await coordinator.async_start_routine([3])
    # An earlier one-off day without a saved profile is only forgotten.
    coordinator._set_temporary_routine_day("monday")
    start = len(rig.journal)
    await coordinator.async_start_routine()
    assert sends(rig, start) == [START, COMPLETE]
    assert not coordinator.temporary_routine_active


async def test_set_routines_writes_verifies_and_saves_them(rig):
    """Mon/Tue 20:00 brush teeth -> toilet -> story, through the restore path."""
    coordinator = rig.coordinator
    await verified_profile(rig)
    record = coordinator.profile_record
    # A device button changed the volume; the action does not revert it.
    rig.state["currentVolume"] = 7
    start = len(rig.journal)

    result = await coordinator.async_set_routines(
        ["monday", "tuesday"], {"hour": 20, "minute": 0}, [3, 4, 7]
    )

    routine = routine_from_tasks({"hour": 20, "minute": 0}, [3, 4, 7])
    assert sends(rig, start) == [
        day_routine_payload("monday", routine),
        day_routine_payload("tuesday", routine),
    ]
    assert result.verified
    assert result.applied_steps == ("routines.monday", "routines.tuesday")
    saved = coordinator.profile_record
    assert saved.revision == record.revision + 1
    assert saved.is_verified
    assert saved.desired_profile["routines"]["monday"] == routine
    assert saved.desired_profile["routines"]["tuesday"] == routine
    assert saved.desired_profile["light_and_sound"]["volume"] == 7
    assert rig.fake.routines["monday"].slots[2] == RoutineTask(3, 7)

    # The same routine again: nothing to write, still verified.
    start = len(rig.journal)
    again = await coordinator.async_set_routines(
        ["monday"], {"hour": 20, "minute": 0}, [3, 4, 7]
    )
    assert again.verified
    assert not sends(rig, start)
    assert coordinator.profile_record.revision == saved.revision


async def test_set_routines_clears_a_one_off_routine(rig):
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    await coordinator.async_start_routine([8, 7])
    # It ended unseen; saving routines restores the whole saved profile,
    # today's routine included.
    rig.state["operationMode"] = 0
    await coordinator.async_set_routines(["monday"], None, [])
    await drain(rig)

    assert not coordinator.temporary_routine_active
    assert rig.fake.routines["sunday"].slots[0] == RoutineTask(1, 3)


@pytest.mark.parametrize(
    ("days", "time", "tasks"),
    [
        (["monday"], None, [3]),
        ([], {"hour": 20, "minute": 0}, [3]),
        (["someday"], {"hour": 20, "minute": 0}, [3]),
        ("monday", {"hour": 20, "minute": 0}, [3]),
        (["monday"], {"hour": 20, "minute": 0}, [3, 3]),
    ],
)
async def test_set_routines_validation_before_ble(rig, days, time, tasks):
    await verified_profile(rig)
    start = len(rig.journal)
    with pytest.raises(ProfileValidationError):
        await rig.coordinator.async_set_routines(days, time, tasks)
    assert rig.journal[start:] == []


async def test_set_routines_refusals_and_unreachable_device(rig):
    coordinator = rig.coordinator
    await verified_profile(rig)
    at_20 = {"hour": 20, "minute": 0}

    # A running routine refuses the change; nothing is saved.
    rig.state["operationMode"] = 7
    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_set_routines(["monday"], at_20, [3])
    assert err.value.translation_key == "routine_running"
    assert coordinator.profile_record.revision == 1
    rig.state["operationMode"] = 0

    # Unreachable: saved as a pending revision, and the error says so.
    original = rig.client_factory.side_effect

    def failing(*args, **kwargs):
        client = original(*args, **kwargs)
        client.mode = "error"
        return client

    rig.client_factory.side_effect = failing
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_routines(["monday"], at_20, [3])
    assert err.value.translation_key == "profile_saved_not_applied"
    record = coordinator.profile_record
    assert (record.revision, record.pending) == (2, True)
    assert record.desired_profile["routines"]["monday"]["slots"][0] == {
        "step": 1,
        "task": 3,
    }
    rig.client_factory.side_effect = original

    # Maintenance: saved too, never written.
    await coordinator.async_set_maintenance(True)
    start = len(rig.journal)
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_routines(["tuesday"], at_20, [3])
    assert err.value.translation_key == "profile_saved_not_applied"
    assert coordinator.profile_record.revision == 3
    assert not sends(rig, start)
    await coordinator.async_set_maintenance(False)

    # The same content again saves nothing new.
    rig.client_factory.side_effect = failing
    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_routines(["tuesday"], at_20, [3])
    assert coordinator.profile_record.revision == 3
    rig.client_factory.side_effect = original

    with pytest.raises(RevisionConflictError):
        await coordinator.async_apply_profile_edit({"playlist": [1]}, 1)

    coordinator._profile_record = ProfileRecord(
        **{**coordinator.profile_record.to_dict(), "verified_fingerprint": "b" * 64}
    )
    with pytest.raises(HomeAssistantError, match="different device"):
        await coordinator.async_set_routines(["monday"], None, [])
    coordinator.protocol_verified = False
    with pytest.raises(ServiceValidationError, match="Read and verify"):
        await coordinator.async_apply_profile_edit({"playlist": [1]}, 3)
    coordinator._profile_record = ProfileRecord(revision=1)
    with pytest.raises(ServiceValidationError, match="Read and verify"):
        await coordinator.async_set_routines(["monday"], None, [])


async def test_keeping_the_device_profile_forgets_a_one_off_routine(rig):
    coordinator = rig.coordinator
    await verified_with_routine(rig)
    coordinator._set_temporary_routine_day("sunday")

    snapshot, revision = await coordinator.async_read_profile_snapshot()
    await coordinator.async_accept_device_profile(snapshot, revision, confirmed=True)

    assert not coordinator.temporary_routine_active


def test_entry_fixture_is_verified(rig):
    """The shared rig enrolls this key with controls unlocked."""
    data = rig.coordinator.entry.data
    assert data[CONF_DEVICE_FINGERPRINT] == FINGERPRINT
    assert data[CONF_PROTOCOL_VERIFIED] is True


def test_routine_from_tasks_one_task_per_step():
    assert routine_from_tasks({"hour": 7, "minute": 5}, [2, 1]) == {
        "time": {"hour": 7, "minute": 5},
        "slots": [{"step": 1, "task": 2}, {"step": 2, "task": 1}, *([None] * 10)],
    }
    # No tasks: no routine, so no time either; no time: manual start only.
    assert routine_from_tasks({"hour": 7, "minute": 5}, []) == {
        "time": None,
        "slots": [None] * 12,
    }
    assert routine_from_tasks(None, [4])["time"] is None
    assert routine_task_ids(
        {"time": None, "slots": [{"step": 2, "task": 0}, None, {"step": 1, "task": 9}]}
    ) == [9]
    for tasks in ((1,), [1] * 13, [0], [12], [True], [2, 2]):
        with pytest.raises(ProfileValidationError):
            routine_from_tasks(None, tasks)  # type: ignore[arg-type]


async def test_undecodable_task_status_is_ignored(rig):
    await live(rig)
    push_mode(rig, 7)
    rig.clients[-1].on_response(SimpleNamespace(opcode=0x94, decode=lambda: b"\x00"))
    assert rig.coordinator.routine_status is None
