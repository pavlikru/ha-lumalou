"""Serialized HA adaptation: one live BLE session, profile restore, recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from lumalou import commands  # type: ignore[attr-defined]
from lumalou.advertisement import MANUFACTURER_ID, parse_advertisement
from lumalou.client import (
    DisconnectedError,
    FreshSessionRequiredError,
    ResponseEnvelope,
)
from lumalou.profile import RoutineMusicSettings
from lumalou.responses import CurrentDate
from lumalou.schedules import RoutineTaskStatus

from .const import (
    AUTO_RESTORE_MAX_ATTEMPTS,
    CLOCK_SYNC_RETRY_INTERVAL,
    CLOCK_SYNC_TOLERANCE,
    CONF_AUTO_RESTORE,
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    CONNECT_TIMEOUT,
    DEFAULT_AUTO_RESTORE,
    DOMAIN,
    DST_AMBIGUITY_WINDOW,
    GATT_TIMEOUT,
    GLOBAL_STATE_FIELDS,
    LIVE_SESSION_WAIT,
    RECONNECT_DELAY,
    RECOVERY_COOLDOWN,
    RECOVERY_MAX_COOLDOWN,
    RESET_CLOCK_OFFSET,
    RESET_WINDOW_MAX,
    RESPONSE_TIMEOUT,
    ROUTINE_OPERATION_MODE,
    ROUTINE_SETTING_FIELDS,
    ROUTINE_SILENCE_TIMEOUT,
    ROUTINE_START_DELAY,
    ROUTINE_TASKS,
    SESSION_CONNECT_ATTEMPTS,
    SESSION_SILENCE_TIMEOUT,
    STATE_CONFIRM_TIMEOUT,
    WHOLE_HOUR_TOLERANCE,
)
from .models import (
    DAYS,
    LIVE_BLOCK,
    ProfileRecord,
    ProfileValidationError,
    RevisionConflictError,
    export_profile_payload,
    import_profile_payload,
    profile_is_complete,
    require_complete_profile,
    routine_from_tasks,
    validate_integer,
    validate_profile,
)
from .restore import (
    ProfileRestoreResult,
    RestoreNeeded,
    build_restore_steps,
    changed_blocks,
    clock_offset_seconds,
    day_routine_payload,
    device_weekday,
    is_factory_clock,
    is_factory_default,
    is_whole_hour_offset,
    profile_from_readback,
    set_current_date_payload,
)
from .storage import ProfileStore
from .transport import SafeLumalouClient

_LOGGER = logging.getLogger(__name__)
_MAX_REVISION = 2**63 - 1
_GLOBAL_STATE = 0x02
_CURRENT_DATE = 0x13
_ROUTINE_TASK_STATUS = 0x94
# ROUTINE_TASK_STATUS nibble values (hardware): index = task id - 1.
_TASK_CURRENT = 1
_TASK_DONE = 2
# Written together by one SET_ROUTINE_MUSIC_STATUS command.
_ROUTINE_SOUNDS = frozenset({"music", "task_reward_sfx", "routine_reward_sfx"})
# Light and sound profile key -> (GLOBAL_STATE field, maximum, setter).
_LEVELS: dict[str, tuple[str, int, Callable[[int], bytes]]] = {
    "volume": ("currentVolume", 9, commands.set_volume),
    "light_duration": ("lightDuration", 5, commands.set_light_duration),
    "playlist_duration": ("playlistDuration", 6, commands.set_playlist_duration),
}


class ClockSyncError(HomeAssistantError):
    """Writing the device clock failed; the session that sent it is gone."""


class ProfileRestoreError(HomeAssistantError):
    """A restore did not reach a verified state; ``result`` says how far it got."""

    def __init__(self, message: str, result: ProfileRestoreResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """One strict fresh-session read of every persistent block plus the clock."""

    profile: dict[str, Any]
    state: dict[str, int]
    clock: CurrentDate
    read_at: datetime


def _new_revision(old: ProfileRecord, desired: dict[str, Any]) -> ProfileRecord:
    """Build the next pending revision."""
    return replace(
        old,
        revision=old.revision + 1,
        desired_profile=desired,
        pending=True,
        sync_status="pending",
        last_error=None,
    )


# English messages for logs and tracebacks; users see the translation.
_ERRORS = {
    "maintenance_mode": "Lumalou is in maintenance mode",
    "control_locked": "Read and verify the complete Lumalou profile before control",
    "profile_read_failed": "Could not read a complete, consistent Lumalou profile",
    "profile_other_device": "The saved profile was verified on a different device",
    "command_failed": "Lumalou command failed; it will not be replayed",
    "clock_untrusted": "Home Assistant clock is not trustworthy",
    "routine_running": "A Lumalou routine is already running",
    "routine_not_running": "No Lumalou routine is running",
    "routine_not_started": "Lumalou did not start the routine",
    "setting_not_confirmed": "Lumalou did not confirm the setting",
    "profile_saved_not_applied": (
        "The change was saved but could not be written to Lumalou"
    ),
}


# Caused by the entry's state, which the user can change; not a device fault.
_USER_STATE_ERRORS = frozenset(
    {
        "maintenance_mode",
        "control_locked",
        "clock_untrusted",
        "routine_running",
        "routine_not_running",
    }
)


def _error(key: str) -> HomeAssistantError:
    """Build a translated user-facing error with an English log message."""
    error = ServiceValidationError if key in _USER_STATE_ERRORS else HomeAssistantError
    return error(_ERRORS[key], translation_domain=DOMAIN, translation_key=key)


def _all_tasks_done(status: RoutineTaskStatus) -> bool:
    """Return whether a routine reached its final step (N+1): all tasks done."""
    return (
        status.current_step > 0
        and _TASK_CURRENT not in status.task_states
        and _TASK_DONE in status.task_states
    )


def _verifiable(desired: dict[str, Any], read: DeviceSnapshot) -> dict[str, Any]:
    """Return ``desired`` without light and sound values the read cannot show.

    While the soother (or a sleep stage) runs, the reported light and sound
    values are its own; while the light is off, a written brightness is kept
    for the next "on" but not proven to be reported. Those fields take the
    read value, so they neither fail nor fake a verification (logged).
    """
    state = read.state
    skipped: tuple[str, ...] = ()
    if state.get("currentStage"):
        skipped = ("volume", "light_brightness", "light_duration", "playlist_duration")
    elif not state.get("lightStatus"):
        skipped = ("light_brightness",)
    result = deepcopy(desired)
    for key in skipped:
        if result[LIVE_BLOCK][key] != read.profile[LIVE_BLOCK][key]:
            _LOGGER.info(
                "Not verifying light and sound %s now: saved %s, device shows %s",
                key,
                result[LIVE_BLOCK][key],
                read.profile[LIVE_BLOCK][key],
            )
        result[LIVE_BLOCK][key] = read.profile[LIVE_BLOCK][key]
    return result


def _field_differences(
    desired: dict[str, Any], observed: dict[str, Any], blocks: tuple[str, ...]
) -> list[str]:
    """Describe each differing field of ``blocks``: path, saved and device value."""
    differences: list[str] = []

    def walk(path: str, want: Any, have: Any) -> None:
        if isinstance(want, dict) and isinstance(have, dict):
            for key in want:
                walk(f"{path}.{key}", want[key], have.get(key))
        elif (
            isinstance(want, list) and isinstance(have, list) and len(want) == len(have)
        ):
            for index, (item, other) in enumerate(zip(want, have, strict=True)):
                walk(f"{path}[{index}]", item, other)
        elif want != have:
            differences.append(f"{path}: saved {want!r}, device {have!r}")

    for block in blocks:
        walk(block, desired[block], observed[block])
    return differences


def _monotonic() -> float:
    """Event-loop time (patched in tests)."""
    return asyncio.get_running_loop().time()


def _trusted_now() -> datetime | None:
    """Return HA local time, or None when the host clock is obviously unset."""
    now = dt_util.now()
    return now if now.year >= 2026 else None


class LumalouCoordinator:
    """Own device I/O and saved intent; claim sync only after fresh readback."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, store: ProfileStore | None = None
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.address = entry.data["address"]
        self.device_fingerprint: str = entry.data[CONF_DEVICE_FINGERPRINT]
        self.protocol_verified = entry.data.get(CONF_PROTOCOL_VERIFIED) is True
        self.device_name = entry.title or "Lumalou"
        self.sw_version: str | None = None
        # Latest GLOBAL_STATE pushed by the live session.
        self.data: dict[str, int] | None = None
        # Audio source Home Assistant started, while it plays: the playlists
        # both report songs 1..12, so the song alone cannot tell them apart.
        self.playing_source: int | None = None
        self.present = False
        self.available = False
        # Runtime-only restore/recovery state for entities, Repairs and
        # diagnostics. Never persisted and never contains schedule values.
        self.last_restore_result: ProfileRestoreResult | None = None
        self.last_clock_offset: int | None = None
        self.last_clock_sync: datetime | None = None
        # Event-loop time before which automatic clock writes are skipped
        # after one failed (a failed write retires its session).
        self._clock_sync_retry_at = 0.0
        self._clock_task: asyncio.Task[Any] | None = None
        # Latest CURRENT_DATE frame: (generation, clock, received at).
        self._pushed_clock: tuple[int, CurrentDate, datetime] | None = None
        self._restore_needed: RestoreNeeded | None = None
        # Last strict read offered for confirmation; only it can be committed.
        self._previewed_profile: dict[str, Any] | None = None
        self._profile_record = ProfileRecord()
        self._store = store or ProfileStore(hass, entry.entry_id)
        self._client: SafeLumalouClient | None = None
        self._generation = 0
        # Event-loop time before which no new session is opened.
        self._reconnect_at = 0.0
        # Detached sessions whose link is not closed yet.
        self._detached: dict[int, SafeLumalouClient] = {}
        self._state_event = asyncio.Event()
        # Set while a live session has delivered its state.
        self._available_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._listeners: set[Callable[[], None]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._recovery_task: asyncio.Task[Any] | None = None
        self._next_recovery_at = 0.0
        self._recovery_failures = 0
        self._unsubscribers: list[Callable[[], None]] = []
        self._callbacks_started = False
        self._stopped = False
        # Log device loss and return once each (quality scale).
        self._unavailable_logged = False
        # Latest pushed ROUTINE_TASK_STATUS of the live session (runtime only).
        self.routine_status: RoutineTaskStatus | None = None
        # The running routine reached its final step in this session.
        self._routine_finished = False
        # Last status seen in this session while a routine runs.
        self._running_status: RoutineTaskStatus | None = None
        # Home Assistant sent "cancel" to the running routine.
        self._cancel_sent = False
        self._routine_listeners: set[Callable[[str, dict[str, str]], None]] = set()
        self._routine_task: asyncio.Task[Any] | None = None
        # Event-loop time of the last frame from the device (any session);
        # bounds how long a power-loss clock can have run since 05:00.
        self._last_frame_at: float | None = None
        # A fresh read showed the power-loss clock; cleared once resolved.
        self._reset_seen = False
        # Closes a session that stopped pushing (the clock comes every minute).
        self._silence_timer: asyncio.TimerHandle | None = None

    @property
    def profile_record(self) -> ProfileRecord:
        """Return a detached saved revision so callers cannot mutate intent."""
        return deepcopy(self._profile_record)

    @property
    def clock_sync_paused(self) -> bool:
        """Return whether automatic clock writes pause after a failed one."""
        return self._clock_sync_retry_at > 0 and (
            asyncio.get_running_loop().time() < self._clock_sync_retry_at
        )

    @property
    def auto_restore_enabled(self) -> bool:
        """Return whether a detected reset is restored automatically (default on)."""
        return self.entry.options.get(CONF_AUTO_RESTORE, DEFAULT_AUTO_RESTORE) is True

    @property
    def restore_needed(self) -> RestoreNeeded | None:
        """Return the current verified-profile mismatch, if still relevant.

        Set by background recovery when a fresh complete read no longer
        matches the verified current revision (see ``_detect_restore_needed``).
        A newer saved revision makes it obsolete automatically.
        """
        need = self._restore_needed
        if need is None or need.revision != self._profile_record.revision:
            return None
        return need

    @property
    def repair_needed(self) -> RestoreNeeded | None:
        """Return the mismatch the user must resolve in Repairs.

        A reset that automatic restore still handles needs no Repair; one it
        gave up on, or any mismatch without a reset, does.
        """
        need = self.restore_needed
        if (
            need is not None
            and need.reset
            and self.auto_restore_enabled
            and not need.auto_restore_exhausted
        ):
            return None
        return need

    @property
    def maintenance(self) -> bool:
        """Return whether maintenance mode is on (saved)."""
        return self._profile_record.maintenance

    @property
    def sync_status(self) -> str:
        """Return the saved profile's synchronization status."""
        return self._profile_record.sync_status

    @property
    def _temporary_routine_day(self) -> str | None:
        return self._profile_record.temporary_routine_day

    @property
    def temporary_routine_active(self) -> bool:
        """Return whether a one-off routine still replaces a saved day routine."""
        return self._temporary_routine_day is not None

    @property
    def routine_phase(self) -> str | None:
        """Return off, ready (silent preview), in_progress or completed."""
        if self.data is None:
            return None
        if self.data["operationMode"] != ROUTINE_OPERATION_MODE:
            return "off"
        if (status := self.routine_status) is None:
            return None
        if status.current_step == 0:
            return "ready"
        return "completed" if _all_tasks_done(status) else "in_progress"

    @property
    def current_task(self) -> str | None:
        """Return the current task key, or "none" outside a task."""
        if self.data is None:
            return None
        if self.data["operationMode"] != ROUTINE_OPERATION_MODE:
            return "none"
        if (status := self.routine_status) is None:
            return None
        for task, state in zip(ROUTINE_TASKS, status.task_states, strict=False):
            if state == _TASK_CURRENT:
                return task
        return "none"

    @callback
    def async_add_routine_listener(
        self, listener: Callable[[str, dict[str, str]], None]
    ) -> Callable[[], None]:
        """Register for routine events (task and routine completion)."""
        self._routine_listeners.add(listener)
        return lambda: self._routine_listeners.discard(listener)

    @callback
    def _fire_routine_event(self, event_type: str, **attributes: str) -> None:
        for listener in tuple(self._routine_listeners):
            listener(event_type, attributes)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity listener without owning a background recovery loop."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @asynccontextmanager
    async def _locked(self, *, bluetooth_session: bool) -> AsyncIterator[None]:
        """Serialize one complete operation and track it for shutdown.

        Cancelling a Bluetooth operation tears its session down. Durable-only
        edits pass ``bluetooth_session=False`` and have no BLE side effects.
        """
        task = asyncio.current_task()
        if self._stopped:
            raise HomeAssistantError("Lumalou integration is unloaded")
        if task is not None:
            self._tasks.add(task)
        try:
            async with self._lock:
                if self._stopped:
                    raise HomeAssistantError("Lumalou integration is unloaded")
                try:
                    yield
                except asyncio.CancelledError:
                    if bluetooth_session:
                        await self._disconnect()
                    raise
        finally:
            if task is not None:
                self._tasks.discard(task)

    def _operation(self):
        return self._locked(bluetooth_session=True)

    def _profile_edit_operation(self):
        return self._locked(bluetooth_session=False)

    @asynccontextmanager
    async def _device_write_operation(self) -> AsyncIterator[None]:
        """Serialize a mutation and require verified controls."""
        async with self._operation():
            self._assert_device_writes_allowed()
            yield

    @asynccontextmanager
    async def _live_write_operation(self) -> AsyncIterator[None]:
        """A command on the live session: wait briefly for one to be ready.

        Right after a session change (a profile write, a reconnect) the live
        session may still be opening. Wait up to ``LIVE_SESSION_WAIT`` for it
        (outside the lock, which the reconnect needs) instead of failing;
        a command is then sent or fails with an error, never dropped.
        """
        if not self.available and self.present and not self._stopped:
            with suppress(TimeoutError):
                async with asyncio.timeout(LIVE_SESSION_WAIT):
                    await self._available_event.wait()
        async with self._device_write_operation():
            yield

    def _assert_device_writes_allowed(self) -> None:
        """Guard every device mutation, including callers without a connection."""
        if not self.protocol_verified:
            raise _error("control_locked")
        if self._profile_record.maintenance:
            raise _error("maintenance_mode")

    @callback
    def _set_protocol_verified(self, verified: bool) -> None:
        """Persist whether controls are unlocked for this entry."""
        if self.protocol_verified != verified:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_PROTOCOL_VERIFIED: verified}
            )
            self.protocol_verified = verified
            self._notify()

    async def async_setup(self) -> None:
        """Load private intent without connecting or changing the device."""
        self._profile_record = await self._store.async_load()
        if self._profile_record.revision == 0:
            # No usable saved profile (never read, or unreadable): keep
            # controls locked until a device read is confirmed again.
            self._set_protocol_verified(False)
        self._notify()

    # ---- Home Assistant Bluetooth callbacks and background recovery ----

    @callback
    def async_start(self) -> None:
        """Start Home Assistant-owned Bluetooth reachability callbacks."""
        if self._callbacks_started or self._stopped:
            return
        self._callbacks_started = True
        self.present = bluetooth.async_address_present(
            self.hass, self.address, connectable=True
        )
        try:
            self._unsubscribers.append(
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_handle_advertisement,
                    bluetooth.BluetoothCallbackMatcher(
                        address=self.address, connectable=True
                    ),
                    bluetooth.BluetoothScanningMode.PASSIVE,
                    replay=bluetooth.BluetoothCallbackReplay.NEWEST_FIRST,
                )
            )
            self._unsubscribers.append(
                bluetooth.async_track_unavailable(
                    self.hass,
                    self._async_handle_unavailable,
                    self.address,
                    connectable=True,
                )
            )
        except Exception:
            self.async_stop_callbacks()
            raise
        if self.present:
            self._schedule_recovery()
        self._notify()

    @callback
    def async_stop_callbacks(self) -> None:
        """Stop Bluetooth callbacks; safe to call more than once."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._callbacks_started = False

    @callback
    def _async_handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Record presence and firmware, then coalesce recovery."""
        if self._stopped:
            return
        changed = not self.present
        self.present = True
        try:
            advertisement = parse_advertisement(
                service_info.manufacturer_data[MANUFACTURER_ID]
            )
        except KeyError, TypeError, ValueError, AttributeError:
            pass
        else:
            # Passive and unauthenticated: a display value, never a gate.
            version = advertisement.firmware_version
            if version and version != self.sw_version:
                self.sw_version = version
                changed = True
        if changed:
            self._notify()
        self._schedule_recovery()

    @callback
    def _async_handle_unavailable(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Invalidate immediately, then close only the detached old session."""
        if self._stopped:
            return
        if not self._unavailable_logged:
            _LOGGER.info("%s is unavailable", self.device_name)
            self._unavailable_logged = True
        self.present = False
        self._cancel_recovery()
        self._recovery_failures = 0
        self._next_recovery_at = 0.0
        client = self._invalidate()
        if client is not None:
            self._create_background_task(
                self._async_close_detached(client), "lumalou unavailable disconnect"
            )

    @callback
    def _async_handle_session_lost(
        self, generation: int, reason: BaseException | None = None
    ) -> None:
        """React to a remote link loss of the live session (e.g. power loss).

        The upstream client has already invalidated and cleans up its own
        transport. A short power cycle may never make HA mark the device
        unavailable, so schedule recovery once it advertises again.
        """
        if self._stopped or generation != self._generation:
            return
        _LOGGER.debug(
            "%s session ended: %s: %s",
            self.device_name,
            type(reason).__name__,
            reason,
        )
        self._invalidate()
        self._schedule_recovery()

    @callback
    def _schedule_recovery(self) -> None:
        """Schedule one rate-limited recovery pass."""
        if (
            self._stopped
            or not self.present
            or self._profile_record.maintenance
            or self.available
            or self._recovery_task is not None
        ):
            return
        task = self._create_background_task(
            self._async_background_refresh(), "lumalou advertisement refresh"
        )
        self._recovery_task = task
        task.add_done_callback(self._recovery_done)

    @callback
    def _recovery_done(self, task: asyncio.Task[Any]) -> None:
        if self._recovery_task is task:
            self._recovery_task = None

    @callback
    def _cancel_recovery(self) -> None:
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            self._recovery_task = None

    @callback
    def _cancel_background_device_work(self) -> None:
        """Cancel automatic Bluetooth work: recovery and clock correction."""
        self._cancel_recovery()
        if self._clock_task is not None:
            self._clock_task.cancel()

    @callback
    def _create_background_task(
        self, coro: Coroutine[Any, Any, Any], name: str
    ) -> asyncio.Task[Any]:
        """Create config-entry-owned work and retain it for ordered shutdown."""
        task = self.entry.async_create_background_task(
            self.hass, coro, name, eager_start=False
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _async_background_refresh(self) -> None:
        """Retry recovery with one bounded exponential-backoff loop."""
        while True:
            delay = self._next_recovery_at - asyncio.get_running_loop().time()
            if delay > 0:
                await asyncio.sleep(delay)
            if (
                self._stopped
                or not self.present
                or self._profile_record.maintenance
                or self.available
            ):
                return
            try:
                await self._async_recover()
            except HomeAssistantError:
                self._recovery_failures += 1
                exponent = min(self._recovery_failures - 1, 10)
                cooldown = min(RECOVERY_COOLDOWN * 2**exponent, RECOVERY_MAX_COOLDOWN)
                self._next_recovery_at = asyncio.get_running_loop().time() + cooldown
                _LOGGER.debug(
                    "Background Lumalou recovery failed; retrying in %s seconds",
                    cooldown,
                    exc_info=True,
                )
                continue
            self._recovery_failures = 0
            self._next_recovery_at = (
                asyncio.get_running_loop().time() + RECOVERY_COOLDOWN
            )
            return

    async def _async_recover(self) -> None:
        """Open the session after the device (re)appears; it stays open.

        Unverified entries only read GLOBAL_STATE. Verified entries read the
        complete profile and the device clock in one fresh session and set
        the clock from HA local time first. A pending revision the device
        already matches becomes verified. The power-loss clock (see
        ``_is_reset``) plus a profile that differs from the saved one is a
        reset: it is restored automatically unless the user switched that off
        (bounded attempts per event), otherwise a Repair is raised. A failed
        clock write fails this pass; the next one skips the paused write.
        """
        if not self.protocol_verified:
            async with self._operation():
                try:
                    client = await self._async_new_session()
                    await client.request_state(timeout=RESPONSE_TIMEOUT)
                except Exception as err:
                    await self._disconnect()
                    raise HomeAssistantError("Lumalou recovery failed") from err
            return
        async with self._device_write_operation():
            try:
                # The reset is judged in the read, before the clock is set.
                client, snapshot = await self._async_fresh_snapshot()
                reset = self._reset_seen
                clock_synced = await self._async_sync_clock_if_needed(
                    client, snapshot.clock, snapshot.read_at, automatic=True
                )
                if (
                    self._temporary_routine_day is not None
                    and snapshot.state["operationMode"] != ROUTINE_OPERATION_MODE
                ):
                    # A one-off routine ended while disconnected.
                    await self._async_write_back_routine(client, snapshot.profile)
            except Exception as err:
                await self._disconnect()
                raise HomeAssistantError("Lumalou recovery failed") from err
            record = self._profile_record
            # A one-off routine that still runs is not a profile change.
            observed = self._with_saved_routine(snapshot.profile)
            if (
                not record.is_verified
                and record.verified_fingerprint in (None, self.device_fingerprint)
                and record.desired_profile == observed
            ):
                await self._save(self._as_verified(record))
                record = self._profile_record
            need = self._detect_restore_needed(record, observed, reset=reset)
            if need is None or not need.reset or not self.auto_restore_enabled:
                return
            if need.auto_restore_attempts >= AUTO_RESTORE_MAX_ATTEMPTS:
                if not need.auto_restore_exhausted:
                    self._restore_needed = replace(need, auto_restore_exhausted=True)
                    self._notify()
                return
            self._restore_needed = replace(
                need, auto_restore_attempts=need.auto_restore_attempts + 1
            )
            _LOGGER.info("%s was reset; restoring the saved profile", self.device_name)
            try:
                await self._async_restore(
                    record, client, snapshot, automatic=True, clock_synced=clock_synced
                )
            except ProfileRestoreError:
                # The next attempt runs in a new session after the backoff.
                await self._disconnect()
                raise

    def _is_reset(self, snapshot: DeviceSnapshot, last_frame_at: float | None) -> bool:
        """Return whether a fresh device read shows a power loss.

        Either every setting is at its factory default (hardware: a short
        outage can reset the settings while the clock keeps running), or the
        clock restarted at 05:00 on Sunday: far off, running from 05:00 for no
        longer than since the last frame Home Assistant received from the
        device (unknown after a restart: capped), and not off by whole hours
        (DST) unless the device was heard within the last hour. Every input
        and criterion is logged at info level (no identifiers).
        """
        clock, now = snapshot.clock, _trusted_now()
        offset = clock_offset_seconds(clock, snapshot.read_at)
        window = RESET_WINDOW_MAX
        unseen: float | None = None
        if last_frame_at is not None:
            unseen = _monotonic() - last_frame_at
            window = min(window, round(unseen) + RESET_CLOCK_OFFSET)
        factory = is_factory_default(snapshot.profile)
        far_off = offset > RESET_CLOCK_OFFSET
        power_loss_clock = is_factory_clock(clock, window)
        whole_hours = is_whole_hour_offset(offset, WHOLE_HOUR_TOLERANCE)
        heard_recently = unseen is not None and unseen <= DST_AMBIGUITY_WINDOW
        reset = now is not None and (
            factory
            or (far_off and power_loss_clock and (not whole_hours or heard_recently))
        )
        _LOGGER.info(
            "%s clock on connect %02d:%02d:%02d weekday %d, Home Assistant "
            "%02d:%02d:%02d weekday %d, offset %d s",
            self.device_name,
            clock.hour,
            clock.minute,
            clock.second,
            clock.weekday,
            snapshot.read_at.hour,
            snapshot.read_at.minute,
            snapshot.read_at.second,
            device_weekday(snapshot.read_at),
            offset,
        )
        _LOGGER.info(
            "%s reset check: factory settings %s, far off %s, power-loss clock "
            "%s (window %d s), whole hours %s, heard within an hour %s, trusted "
            "time %s: reset %s",
            self.device_name,
            factory,
            far_off,
            power_loss_clock,
            window,
            whole_hours,
            heard_recently,
            now is not None,
            reset,
        )
        return reset

    def _detect_restore_needed(
        self, record: ProfileRecord, observed: dict[str, Any], *, reset: bool
    ) -> RestoreNeeded | None:
        """Flag a saved revision that the device does not match.

        The current revision qualifies when it is, or was edited from, a
        revision verified on this same device key: a verified profile the
        device lost (for example after a power loss) or a pending edit that
        could not be written yet. The light and sound block also changes in
        everyday use (device buttons), so it is compared only with the reset
        marker (a far-off device clock). A reset stays a reset until it is
        resolved, even after the clock was corrected.
        """
        need: RestoreNeeded | None = None
        previous = self.restore_needed
        reset = reset or (previous is not None and previous.reset)
        if record.verified_fingerprint == self.device_fingerprint and (
            profile_is_complete(record.desired_profile)
        ):
            blocks = changed_blocks(record.desired_profile, observed, live=reset)
            if blocks:
                need = (
                    RestoreNeeded(record.revision, blocks, dt_util.utcnow(), reset)
                    if previous is None
                    else replace(previous, changed_blocks=blocks, reset=reset)
                )
        if need is None:
            self._reset_seen = False
        if need != self._restore_needed:
            self._restore_needed = need
            self._notify()
        return need

    def _saved_routine(self, day: str) -> dict[str, Any] | None:
        """Return the saved routine of one day, if a complete profile exists."""
        profile = self._profile_record.desired_profile
        return profile["routines"][day] if profile_is_complete(profile) else None

    def _with_saved_routine(self, observed: dict[str, Any]) -> dict[str, Any]:
        """Return a device read with a one-off routine's day as saved."""
        day = self._temporary_routine_day
        if day is None or (saved := self._saved_routine(day)) is None:
            return observed
        result = deepcopy(observed)
        result["routines"][day] = deepcopy(saved)
        return result

    async def _set_temporary_routine_day(self, day: str | None) -> None:
        """Persist which day a one-off routine replaced (None: nothing)."""
        if day != self._temporary_routine_day:
            await self._save(replace(self._profile_record, temporary_routine_day=day))

    async def _async_write_back_routine(
        self, client: SafeLumalouClient, observed: dict[str, Any] | None = None
    ) -> None:
        """Write the saved routine over a one-off routine's day.

        ``observed`` is a fresh device read of this session: the write is
        skipped when the device already has the saved routine, and the read
        is updated to what the device now has.
        """
        day = self._temporary_routine_day
        assert day is not None
        saved = self._saved_routine(day)
        if saved is not None and (
            observed is None or observed["routines"][day] != saved
        ):
            self._previewed_profile = None
            await client.send(day_routine_payload(day, saved), timeout=RESPONSE_TIMEOUT)
            if observed is not None:
                observed["routines"][day] = deepcopy(saved)
        await self._set_temporary_routine_day(None)

    async def _async_end_temporary_routine(self) -> None:
        """After a one-off routine ended, put the saved day routine back.

        A failed write drops the session; recovery writes it on reconnect.
        """
        async with self._operation():
            client = self._client
            if (
                self._temporary_routine_day is None
                or client is None
                or self.data is None
                or self.data["operationMode"] == ROUTINE_OPERATION_MODE
                or not self.protocol_verified
                or self._profile_record.maintenance
            ):
                return
            try:
                await self._async_write_back_routine(client)
            except Exception:
                _LOGGER.debug("Restoring the saved routine failed", exc_info=True)
                await self._disconnect()

    async def _async_close_detached(self, client: SafeLumalouClient) -> None:
        """Close a detached session after any active serialized operation."""
        async with self._lock:
            await self._close_client(client)

    # ---- Durable intent ----

    async def _save(self, record: ProfileRecord) -> None:
        await self._store.async_save(record)
        self._profile_record = deepcopy(record)
        self._notify()

    # ---- BLE session ----

    @callback
    def _receive(self, generation: int, state: dict) -> None:
        """Take a GLOBAL_STATE frame of the live session (pushed or requested)."""
        if self._stopped or generation != self._generation:
            return
        # The library decodes every field as a nibble (or byte); entities
        # handle values they do not know, so only the shape is checked.
        if (
            not isinstance(state, dict)
            or not set(state) >= GLOBAL_STATE_FIELDS
            or any(type(state[key]) is not int for key in GLOBAL_STATE_FIELDS)
        ):
            _LOGGER.debug("Ignoring a malformed Lumalou state: %s", state)
            return
        previous, self.data = self.data, dict(state)
        self._heard_from_device(generation)
        if not state["musicStatus"]:
            self.playing_source = None
        if (
            previous is not None
            and previous["operationMode"] == ROUTINE_OPERATION_MODE
            and state["operationMode"] != ROUTINE_OPERATION_MODE
        ):
            self._routine_ended()
        self.available = True
        self._available_event.set()
        self._state_event.set()
        if self._unavailable_logged:
            _LOGGER.info("%s is available again", self.device_name)
            self._unavailable_logged = False
        self._notify()

    @callback
    def _heard_from_device(self, generation: int) -> None:
        """Note a state, clock or routine frame and re-arm the silence timer.

        The device pushes its clock every minute, so a session without any of
        these frames for longer than the timeout no longer delivers state
        (hardware: after a scheduled routine start nothing arrived). While a
        routine runs the timeout is shorter, so its progress resumes soon.
        """
        loop = asyncio.get_running_loop()
        self._last_frame_at = loop.time()
        if self._silence_timer is not None:
            self._silence_timer.cancel()
        routine = (
            self.data is not None
            and self.data["operationMode"] == ROUTINE_OPERATION_MODE
        )
        self._silence_timer = loop.call_later(
            ROUTINE_SILENCE_TIMEOUT if routine else SESSION_SILENCE_TIMEOUT,
            self._session_silent,
            generation,
        )

    @callback
    def _session_silent(self, generation: int) -> None:
        """No frame (not even the minute clock) for too long: the link is dead."""
        self._silence_timer = None
        if self._stopped or generation != self._generation or self._client is None:
            return
        _LOGGER.info(
            "%s sent no state, clock or routine frame for too long; reconnecting",
            self.device_name,
        )
        client = self._invalidate()
        if client is not None:
            self._create_background_task(
                self._async_close_detached(client), "lumalou silent session"
            )
        self._schedule_recovery()

    @callback
    def _routine_ended(self) -> None:
        """A routine left routine mode (7 -> other) in this live session.

        A routine that never reached its final step was cancelled when Home
        Assistant sent the cancel command, else it expired (ended by the
        device, for example after nobody completed a task for a long time).
        A one-off routine gives its day back to the saved routine.
        """
        if not self._routine_finished:
            self._fire_routine_event(
                "routine_cancelled" if self._cancel_sent else "routine_expired"
            )
        self._routine_finished = False
        self._cancel_sent = False
        self.routine_status = None
        self._running_status = None
        if self._temporary_routine_day is not None and self._routine_task is None:
            task = self._create_background_task(
                self._async_end_temporary_routine(), "lumalou routine write-back"
            )
            self._routine_task = task
            task.add_done_callback(self._routine_task_done)

    @callback
    def _routine_task_done(self, task: asyncio.Task[Any]) -> None:
        if self._routine_task is task:
            self._routine_task = None

    @callback
    def _take_routine_status(self, status: RoutineTaskStatus) -> None:
        """Derive routine events from consecutive pushed task statuses.

        A task nibble going current -> done is a completed task; reaching the
        final step (no current task, some done) completes the routine. Events
        only come from two statuses seen in one session while a routine runs
        (operationMode 7), never from a first status after connecting.
        """
        self.routine_status = status
        _LOGGER.debug(
            "Routine status: step %s, task states %s",
            status.current_step,
            status.task_states,
        )
        if self.data is None or self.data["operationMode"] != ROUTINE_OPERATION_MODE:
            # Only statuses seen while a routine runs count (hardware: each
            # reconnect outside a routine fired "routine completed").
            self._notify()
            return
        previous, self._running_status = self._running_status, status
        if previous is not None:
            for task, before, after in zip(
                ROUTINE_TASKS, previous.task_states, status.task_states, strict=False
            ):
                if before == _TASK_CURRENT and after == _TASK_DONE:
                    self._fire_routine_event("task_completed", task=task)
            if (
                not self._routine_finished
                and _all_tasks_done(status)
                and not _all_tasks_done(previous)
            ):
                self._routine_finished = True
                self._fire_routine_event("routine_completed")
        self._notify()

    @callback
    def _on_response(self, generation: int, envelope: ResponseEnvelope) -> None:
        """Track routine progress and the device clock the session sees.

        The device pushes ROUTINE_TASK_STATUS on every routine change and
        CURRENT_DATE at least every minute, so DST changes and drift are
        caught without reconnecting.
        """
        if self._stopped or generation != self._generation:
            return
        if envelope.opcode == _ROUTINE_TASK_STATUS:
            status = envelope.decode()
            if isinstance(status, RoutineTaskStatus):
                self._heard_from_device(generation)
                self._take_routine_status(status)
            return
        if envelope.opcode == _GLOBAL_STATE:
            return  # The library delivers it to ``_receive`` (on_state) too.
        if envelope.opcode != _CURRENT_DATE:
            # Single-value pushes (song, stage, ...) are not used and do not
            # prove the session still delivers state (see the watchdog).
            _LOGGER.debug(
                "Unused Lumalou push 0x%02x: %s",
                envelope.opcode,
                bytes(getattr(envelope, "args", b"")).hex(" "),
            )
            return
        self._heard_from_device(generation)
        clock = envelope.decode()
        if not isinstance(clock, CurrentDate):
            return
        now = dt_util.now()
        self._pushed_clock = (generation, clock, now)
        if (
            self._clock_task is None
            and self.available
            and self.protocol_verified
            and clock_offset_seconds(clock, now) > CLOCK_SYNC_TOLERANCE
        ):
            task = self._create_background_task(
                self._async_correct_pushed_clock(), "lumalou clock correction"
            )
            self._clock_task = task
            task.add_done_callback(self._clock_task_done)

    @callback
    def _clock_task_done(self, task: asyncio.Task[Any]) -> None:
        if self._clock_task is task:
            self._clock_task = None

    async def _async_correct_pushed_clock(self) -> None:
        """Correct the clock from the latest pushed frame.

        Small drift is corrected at most hourly; a large offset (such as a
        DST change) at once.
        """
        async with self._operation():
            pushed, client = self._pushed_clock, self._client
            if (
                pushed is None
                or client is None
                or pushed[0] != self._generation
                or not self.protocol_verified
                or self._profile_record.maintenance
            ):
                return
            _generation, clock, read_at = pushed
            if (
                self.last_clock_sync is not None
                and dt_util.now() - self.last_clock_sync
                < timedelta(seconds=CLOCK_SYNC_RETRY_INTERVAL)
                and clock_offset_seconds(clock, read_at) <= RESET_CLOCK_OFFSET
            ):
                return
            # A failure is logged; the failed write retired the session,
            # which schedules recovery.
            with suppress(ClockSyncError):
                await self._async_sync_clock_if_needed(
                    client, clock, read_at, automatic=True
                )

    async def _async_new_session(self) -> SafeLumalouClient:
        """Close any session, wait out the reconnect gap, open a new one."""
        if self._profile_record.maintenance:
            raise _error("maintenance_mode")
        if self._callbacks_started and not self.present:
            raise HomeAssistantError("Lumalou is not advertising")
        for attempt in range(1, SESSION_CONNECT_ATTEMPTS + 1):
            # The live or a lost session must be fully closed first; the gap
            # counts from that close.
            await self._async_close_all()
            delay = self._reconnect_at - _monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                return await self._async_connect()
            except (DisconnectedError, BleakError) as err:
                await self._async_close_all()
                if attempt == SESSION_CONNECT_ATTEMPTS:
                    self._log_connect_failure(err)
                    raise
                _LOGGER.debug(
                    "Connecting to %s failed (%s: %s); retrying",
                    self.device_name,
                    type(err).__name__,
                    err,
                )
            except Exception as err:
                await self._async_close_all()
                self._log_connect_failure(err)
                raise
            except BaseException:
                await self._async_close_all()
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _async_connect(self) -> SafeLumalouClient:
        """Create the session client for the current device and connect it."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise HomeAssistantError("No connectable Lumalou device is available")
        generation = self._generation
        client = SafeLumalouClient(
            self.hass,
            device,
            expected_device_fingerprint=self.device_fingerprint,
            on_state=lambda state: self._receive(generation, state),
            on_response=lambda envelope: self._on_response(generation, envelope),
            disconnected_callback=lambda lost: self._async_handle_session_lost(
                generation, getattr(lost, "last_error", None)
            ),
        )
        self._client = client
        await client.connect(timeout=CONNECT_TIMEOUT)
        return client

    async def _async_close_all(self) -> None:
        """Close the current session and any detached one still open."""
        await self._disconnect()
        for client in tuple(self._detached.values()):
            await self._close_client(client)

    def _log_connect_failure(self, err: Exception) -> None:
        """Log the first failure while unavailable once; repeats go to debug."""
        if not self._unavailable_logged:
            _LOGGER.info(
                "%s is unavailable: could not connect: %s: %s",
                self.device_name,
                type(err).__name__,
                err,
            )
            self._unavailable_logged = True
        _LOGGER.debug(
            "Connecting to %s (%s) failed",
            self.device_name,
            self.address,
            exc_info=True,
        )

    @callback
    def _invalidate(self) -> SafeLumalouClient | None:
        """Invalidate local state and detach the current session synchronously."""
        self._generation += 1
        self.available = False
        self._available_event.clear()
        self.data = None
        if self._silence_timer is not None:
            self._silence_timer.cancel()
            self._silence_timer = None
        # Routine progress is only compared within one session.
        self.routine_status = None
        self._running_status = None
        self.playing_source = None
        self._routine_finished = False
        self._cancel_sent = False
        client, self._client = self._client, None
        if client is not None:
            self._detached[id(client)] = client
            self._reconnect_at = max(self._reconnect_at, _monotonic() + RECONNECT_DELAY)
        self._notify()
        return client

    async def _close_client(self, client: SafeLumalouClient) -> None:
        """Close a detached session once; the reconnect gap starts after it."""
        if id(client) not in self._detached:
            return
        try:
            async with asyncio.timeout(GATT_TIMEOUT):
                await client.disconnect()
        except Exception:
            # Local invalidation is authoritative. Teardown failure must not hide
            # the original command error or prevent config-entry unload.
            pass
        finally:
            self._detached.pop(id(client), None)
            self._reconnect_at = max(self._reconnect_at, _monotonic() + RECONNECT_DELAY)

    async def _disconnect(self) -> None:
        if client := self._invalidate():
            await self._close_client(client)

    async def _read_snapshot(self, client: SafeLumalouClient) -> DeviceSnapshot:
        """Read every persistent block once in the current strict session."""

        async def read(name: str) -> Any:
            return (await client.request_named(name, timeout=RESPONSE_TIMEOUT)).decode()

        state = dict(await client.request_state(timeout=RESPONSE_TIMEOUT))
        try:
            device_clock = await read("current_date")
            read_at = dt_util.now()
        except FreshSessionRequiredError:
            # A minute push came first; this session's frame is as fresh.
            if (pushed := self._pushed_clock) is None or pushed[0] != self._generation:
                raise
            _generation, device_clock, read_at = pushed
        routine_status: RoutineTaskStatus | None = None
        if state["operationMode"] == ROUTINE_OPERATION_MODE:
            # Reconnected during a routine: resume its progress (no events for
            # steps this session did not see).
            try:
                routine_status = await read("routine_task_status")
            except FreshSessionRequiredError:
                routine_status = self.routine_status  # pushed in this session
        playlist = await read("music_playlist")
        clock = await read("clock_settings")
        ready = await read("r2r_times")
        sleepy = await read("sleepy_times")
        alarms = await read("r2r_alarms")
        routines = {
            day: (
                await client.request_day_routine(day, timeout=RESPONSE_TIMEOUT)
            ).decode()
            for day in DAYS
        }
        current_state = client.state
        if current_state is not None and dict(current_state) != state:
            raise HomeAssistantError("GLOBAL_STATE changed while reading the profile")
        if not isinstance(device_clock, CurrentDate):
            raise HomeAssistantError("Current date response is not typed")
        profile = profile_from_readback(
            state,
            playlist=playlist,
            clock=clock,
            ready_to_rise=ready,
            sleepy_times=sleepy,
            alarms=alarms,
            routines=routines,
        )
        if isinstance(routine_status, RoutineTaskStatus):
            self.routine_status = self._running_status = routine_status
            self._notify()
        return DeviceSnapshot(profile, state, device_clock, read_at)

    async def _async_fresh_snapshot(
        self,
    ) -> tuple[SafeLumalouClient, DeviceSnapshot]:
        """Open a new strict session and read the complete profile in it.

        Every fresh read judges the power-loss clock before anything can
        write the clock, and a reset seen once stays pending until it is
        resolved, even if the pass that saw it fails afterwards.
        """
        last_frame_at = self._last_frame_at
        client = await self._async_new_session()
        snapshot = await self._read_snapshot(client)
        if self._is_reset(snapshot, last_frame_at):
            self._reset_seen = True
        return client, snapshot

    async def _async_sync_clock_if_needed(
        self,
        client: SafeLumalouClient,
        clock: CurrentDate,
        read_at: datetime,
        *,
        automatic: bool = False,
    ) -> bool:
        """Correct the device clock from HA local time beyond a small tolerance.

        A failed write raises ``ClockSyncError``. When ``automatic`` (recovery
        and pushed-clock correction), it also pauses automatic writes for
        ``CLOCK_SYNC_RETRY_INTERVAL`` so a write that keeps failing is not
        repeated on every recovery pass.
        """
        if _trusted_now() is None:
            _LOGGER.warning("Home Assistant clock is not trustworthy; not syncing")
            return False
        self.last_clock_offset = clock_offset_seconds(clock, read_at)
        if self.last_clock_offset <= CLOCK_SYNC_TOLERANCE:
            return False
        if automatic and self.clock_sync_paused:
            _LOGGER.debug(
                "Not correcting the %s clock (%s s off) after a recent failure",
                self.device_name,
                self.last_clock_offset,
            )
            return False
        now = dt_util.now()
        try:
            await client.send(set_current_date_payload(now), timeout=RESPONSE_TIMEOUT)
        except Exception as err:
            if automatic:
                self._clock_sync_retry_at = (
                    asyncio.get_running_loop().time() + CLOCK_SYNC_RETRY_INTERVAL
                )
                _LOGGER.warning(
                    "Could not correct the %s clock (%s s off): %s: %s; retrying "
                    "automatically in %s minutes or with the clock sync button",
                    self.device_name,
                    self.last_clock_offset,
                    type(err).__name__,
                    err,
                    CLOCK_SYNC_RETRY_INTERVAL // 60,
                )
            raise ClockSyncError("Lumalou clock sync failed") from err
        self._clock_sync_retry_at = 0.0
        self.last_clock_sync = now
        self.last_clock_offset = 0
        return True

    # ---- Live controls ----

    async def _write(self, *payloads: bytes) -> None:
        """Send on the live session; the acknowledged write is the result.

        The device pushes the resulting GLOBAL_STATE itself, so nothing is
        read back, no new session is opened and nothing is ever replayed.
        """
        # A preview taken before this write no longer describes the device.
        self._previewed_profile = None
        client = self._client
        try:
            if client is None or not client.connected:
                raise HomeAssistantError("No live Lumalou session")
            for payload in payloads:
                await client.send(payload, timeout=RESPONSE_TIMEOUT)
        except Exception as err:
            await self._disconnect()
            raise _error("command_failed") from err

    def _live_state(self) -> dict[str, int]:
        if self.data is None:
            raise _error("command_failed")
        return self.data

    async def _state_confirms(self, expected: dict[str, int]) -> bool:
        """Wait briefly for a pushed GLOBAL_STATE that shows ``expected``."""
        try:
            async with asyncio.timeout(STATE_CONFIRM_TIMEOUT):
                while self.data is None or any(
                    self.data[key] != value for key, value in expected.items()
                ):
                    self._state_event.clear()
                    await self._state_event.wait()
        except TimeoutError:
            _LOGGER.debug("No pushed Lumalou state confirmed %s", expected)
            return False
        return True

    async def _write_setting(
        self,
        payloads: list[bytes],
        confirm: dict[str, int],
        block: str,
        values: dict[str, Any],
    ) -> None:
        """Write a persistent setting and keep it once the device confirms it.

        The saved profile is updated in place (same revision): the device
        still matches it, so restore state and open editors are unaffected,
        and a power-loss restore brings back this last choice.
        """
        await self._write(*payloads)
        if not await self._state_confirms(confirm):
            # Seen on hardware right after a session change: acknowledged,
            # but not applied. A setting may safely be written again.
            _LOGGER.debug("Lumalou did not confirm %s; writing it again", confirm)
            await self._write(*payloads)
            if not await self._state_confirms(confirm):
                raise _error("setting_not_confirmed")
        record = self._profile_record
        if not profile_is_complete(record.desired_profile):
            return
        if block == LIVE_BLOCK and self._live_state().get("currentStage"):
            # While the soother (or a sleep stage) runs, the light and sound
            # values the device shows are not proven to persist; keep the
            # saved ones (they are written back only after a power loss).
            _LOGGER.info(
                "%s: not saving %s while the soother runs", self.device_name, values
            )
            return
        profile = deepcopy(record.desired_profile)
        profile[block] = {**profile[block], **values}
        if profile != record.desired_profile:
            await self._save(replace(record, desired_profile=profile))

    async def async_turn_on_light(
        self, brightness: int | None = None, color: int | None = None
    ) -> None:
        """Switch the light on; colour is live state, brightness is kept.

        SET_LIGHT_COLOR switches the light on at the stored brightness, so a
        brightness is written first (the device keeps it while off). A plain
        "on" uses the current colour. With the light already on, a
        brightness alone is only a brightness change.
        """
        if brightness is not None:
            validate_integer(brightness, 1, 9, "brightness")
        if color is not None:
            validate_integer(color, 0, 9, "color")
        async with self._live_write_operation():
            state = self._live_state()
            on = []
            if color is not None or brightness is None or not state["lightStatus"]:
                on.append(
                    commands.set_light_color(
                        state["lightColor"] if color is None else color
                    )
                )
            if brightness is None:
                await self._write(*on)
                return
            await self._write_setting(
                [commands.set_led_brightness(brightness), *on],
                {"lightBrightness": brightness},
                LIVE_BLOCK,
                {"light_brightness": brightness},
            )

    async def async_turn_off_light(self) -> None:
        async with self._live_write_operation():
            await self._write(commands.turn_off_backlight())

    async def async_set_level(self, key: str, value: int) -> None:
        """Set the volume or a timer; none of them starts sound or light."""
        field, maximum, setter = _LEVELS[key]
        validate_integer(value, 0, maximum, key)
        async with self._live_write_operation():
            await self._write_setting(
                [setter(value)], {field: value}, LIVE_BLOCK, {key: value}
            )

    async def async_set_clock_settings(
        self,
        *,
        display: bool | None = None,
        brightness: int | None = None,
        clock_format: int | None = None,
    ) -> None:
        """Change one clock setting; the others come from the device state."""
        if display is not None and type(display) is not bool:
            raise ProfileValidationError("Invalid clock display")
        if brightness is not None:
            validate_integer(brightness, 0, 9, "clock brightness")
        if clock_format is not None:
            validate_integer(clock_format, 0, 1, "clock format")
        async with self._live_write_operation():
            state = self._live_state()
            clock = {
                "display": (
                    bool(state["clockDisplay"]) if display is None else display
                ),
                "brightness": (
                    state["clockBrightness"] if brightness is None else brightness
                ),
                "format": (
                    state["clockFormat"] if clock_format is None else clock_format
                ),
            }
            await self._write_setting(
                [
                    commands.set_clock_settings(
                        clock["display"], clock["brightness"], clock["format"]
                    )
                ],
                {
                    "clockDisplay": int(clock["display"]),
                    "clockBrightness": clock["brightness"],
                    "clockFormat": clock["format"],
                },
                "clock_settings",
                clock,
            )

    async def async_play(self, source: int) -> None:
        """Play a built-in sound; source 0 is the soother (music and light)."""
        validate_integer(source, 0, 7, "audio source")
        async with self._live_write_operation():
            await self._write(commands.play_audio(source))
            self.playing_source = source

    async def async_stop_audio(self) -> None:
        """Stop audio only; a soother light stays on until turned off."""
        async with self._live_write_operation():
            await self._write(commands.turn_off_audio())

    # ---- Routines ----

    async def async_set_routine_settings(self, **changes: Any) -> None:
        """Change routine settings; the others come from the device state.

        ``enabled`` (automatic start at the scheduled time) is a boolean;
        ``music``, ``task_reward_sfx`` and ``routine_reward_sfx`` are 0/1 (one
        device command writes all three); ``volume`` is 0..9.
        """
        if not changes or set(changes) - set(ROUTINE_SETTING_FIELDS):
            raise ProfileValidationError("Invalid routine settings")
        for key, value in changes.items():
            if key == "enabled":
                if type(value) is not bool:
                    raise ProfileValidationError("Invalid routine mode")
            else:
                validate_integer(value, 0, 9 if key == "volume" else 1, key)
        async with self._live_write_operation():
            state = self._live_state()
            target: dict[str, Any] = {
                key: state[field] for key, field in ROUTINE_SETTING_FIELDS.items()
            }
            target["enabled"] = bool(target["enabled"])
            target.update(changes)
            keys = set(changes)
            payloads = []
            if "enabled" in keys:
                payloads.append(commands.set_routine_status(target["enabled"]))
            if keys & _ROUTINE_SOUNDS:
                keys |= _ROUTINE_SOUNDS
                payloads.append(
                    commands.set_routine_music_settings(
                        RoutineMusicSettings(
                            target["music"],
                            target["task_reward_sfx"],
                            target["routine_reward_sfx"],
                        )
                    )
                )
            if "volume" in keys:
                payloads.append(commands.set_routine_volume(target["volume"]))
            await self._write_setting(
                payloads,
                {ROUTINE_SETTING_FIELDS[key]: int(target[key]) for key in keys},
                "routine_settings",
                {key: target[key] for key in keys},
            )

    async def async_routine_control(self, control: int) -> None:
        """Send one routine control code to the running routine.

        0 completes the current task (the remote's check-mark button),
        1 goes back one task, 4 cancels the routine.
        """
        validate_integer(control, 0, 4, "routine control")
        async with self._live_write_operation():
            if self._live_state()["operationMode"] != ROUTINE_OPERATION_MODE:
                raise _error("routine_not_running")
            # Set first: the device pushes the ended routine during the write.
            self._cancel_sent = control == 4
            await self._write(commands.routine_control(control))

    async def async_start_routine(self, tasks: list[int] | None = None) -> None:
        """Start today's routine now, like the scheduled start.

        Starting shows a silent preview; the first "complete task" makes task
        1 current with its music. With ``tasks``, today's routine is replaced
        by them (keeping today's time) only until this routine ends: the
        saved routine is written back when the device leaves routine mode, or
        on the next connection. "Today" is the device clock's weekday when it
        is known. If the device does not enter routine mode, a one-off
        routine is written back and ``routine_not_started`` is raised.
        """
        if tasks is not None:
            if not tasks:
                raise ProfileValidationError("A routine needs at least one task")
            routine_from_tasks(None, tasks)
        async with self._live_write_operation():
            if self._live_state()["operationMode"] == ROUTINE_OPERATION_MODE:
                raise _error("routine_running")
            temporary: tuple[str, dict[str, Any]] | None = None
            saved_routine: dict[str, Any] = {}
            if tasks is not None:
                day = self._device_day()
                if (saved := self._saved_routine(day)) is None:
                    raise _error("control_locked")
                routine = routine_from_tasks(saved["time"], tasks)
                if routine != saved:
                    temporary, saved_routine = (day, routine), saved
            if (previous := self._temporary_routine_day) is not None:
                # An earlier one-off routine was not written back yet.
                if (saved_previous := self._saved_routine(previous)) is not None:
                    await self._write(day_routine_payload(previous, saved_previous))
                await self._set_temporary_routine_day(None)
            if temporary is not None:
                # Recorded first: whatever happens next, the saved routine is
                # written back.
                await self._set_temporary_routine_day(temporary[0])
                await self._write(day_routine_payload(*temporary))
            await self._write(commands.start_routine_mode())
            if not await self._state_confirms(
                {"operationMode": ROUTINE_OPERATION_MODE}
            ):
                if temporary is not None:
                    # A failed write drops the session; recovery writes it.
                    with suppress(HomeAssistantError):
                        await self._write(
                            day_routine_payload(temporary[0], saved_routine)
                        )
                        await self._set_temporary_routine_day(None)
                raise _error("routine_not_started")
            await asyncio.sleep(ROUTINE_START_DELAY)
            await self._write(commands.routine_control(0))

    def _device_day(self) -> str:
        """Return today's weekday on the device clock (else Home Assistant's)."""
        pushed = self._pushed_clock
        if pushed is not None and pushed[0] == self._generation:
            return DAYS[pushed[1].weekday]
        if (now := _trusted_now()) is None:
            raise _error("clock_untrusted")
        return DAYS[device_weekday(now)]

    async def async_set_routines(
        self,
        days: list[str],
        time: dict[str, int] | None,
        tasks: list[int],
    ) -> ProfileRestoreResult:
        """Save day routines and write them to Lumalou, verified.

        See ``async_apply_profile_edit``. No tasks means no routine on those
        days.
        """
        if tasks and time is None:
            raise ProfileValidationError("A routine with tasks needs a start time")
        routine = routine_from_tasks(time, tasks)
        if (
            not isinstance(days, list)
            or not days
            or any(day not in DAYS for day in days)
        ):
            raise ProfileValidationError("Invalid routine days")
        record = self._profile_record
        if not profile_is_complete(record.desired_profile):
            raise _error("control_locked")
        routines = deepcopy(record.desired_profile["routines"])
        for day in days:
            routines[day] = deepcopy(routine)
        return await self.async_apply_profile_edit(
            {"routines": routines}, record.revision
        )

    async def async_apply_profile_edit(
        self, changes: dict[str, Any], expected_revision: int
    ) -> ProfileRestoreResult:
        """Save profile block changes and write them to Lumalou, verified.

        The restore path: a new session reads the device, the saved profile
        gets the changes as a new revision (the everyday light and sound levels
        are taken from the device read, so button changes are not reverted),
        and only the differing blocks are written and then verified with a
        fresh read. When Lumalou cannot be reached (or maintenance is on) the
        changes are still saved, as a pending revision that a reconnect offers
        to write (Repair, or automatic restore after a reset), and
        ``profile_saved_not_applied`` is raised. A running routine refuses the
        edit and saves nothing.
        """
        validated = validate_profile(changes)
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        async with self._operation():
            record = self._profile_record
            if expected_revision != record.revision:
                raise RevisionConflictError(
                    "The saved profile changed; reopen the editor"
                )
            if not self.protocol_verified or not profile_is_complete(
                record.desired_profile
            ):
                raise _error("control_locked")
            if record.verified_fingerprint not in (None, self.device_fingerprint):
                raise _error("profile_other_device")
            desired = {**record.desired_profile, **validated}
            try:
                if record.maintenance:
                    raise _error("maintenance_mode")
                client, snapshot = await self._async_fresh_snapshot()
                clock_synced = await self._async_sync_clock_if_needed(
                    client, snapshot.clock, snapshot.read_at
                )
            except Exception as err:
                await self._disconnect()
                if desired != record.desired_profile:
                    await self._save(_new_revision(record, desired))
                raise _error("profile_saved_not_applied") from err
            if snapshot.state["operationMode"] == ROUTINE_OPERATION_MODE:
                raise _error("routine_running")
            if LIVE_BLOCK not in validated:
                desired[LIVE_BLOCK] = deepcopy(snapshot.profile[LIVE_BLOCK])
            if desired != record.desired_profile:
                await self._save(_new_revision(record, desired))
            return await self._async_restore(
                self._profile_record,
                client,
                snapshot,
                automatic=False,
                clock_synced=clock_synced,
            )

    async def async_sync_clock(self) -> None:
        """Explicit action only; never send a stored or naive host timestamp."""
        async with self._live_write_operation():
            now = _trusted_now()
            if now is None:
                raise _error("clock_untrusted")
            await self._write(set_current_date_payload(now))
            self.last_clock_sync = now
            self.last_clock_offset = 0
            self._clock_sync_retry_at = 0.0

    async def async_set_maintenance(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ProfileValidationError("Invalid maintenance state")
        if enabled:
            # Never wait behind background Bluetooth work (a recovery pass
            # can hold the lock through a full connect budget): cancel it
            # first; its cancellation tears its session down.
            self._cancel_background_device_work()
        async with self._operation():
            await self._save(replace(self.profile_record, maintenance=enabled))
            self._recovery_failures = 0
            self._next_recovery_at = 0.0
            if enabled:
                self._cancel_recovery()
                await self._disconnect()
            else:
                self._schedule_recovery()

    # ---- Import / export / enrollment ----

    async def async_export_profile(self) -> dict[str, Any]:
        """Atomically export intent and its CAS revision without identifiers."""
        async with self._operation():
            record = self.profile_record
            return {
                "current_revision": record.revision,
                "profile": export_profile_payload(record.desired_profile),
            }

    async def async_import_profile(
        self,
        payload: dict[str, Any],
        expected_revision: int,
        *,
        confirmed: bool = False,
    ) -> None:
        """Import a complete validated backup; never touch the device.

        A partial import could silently drop saved blocks, so only a complete
        profile replaces the saved one.
        """
        if not confirmed:
            raise ProfileValidationError("Confirm the profile import")
        desired = require_complete_profile(import_profile_payload(payload))
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        async with self._operation():
            old = self.profile_record
            if expected_revision != old.revision:
                raise RevisionConflictError("The saved profile changed")
            await self._save(_new_revision(old, desired))

    async def async_accept_device_profile(
        self,
        profile: dict[str, Any],
        expected_revision: int,
        *,
        confirmed: bool = False,
    ) -> None:
        """Commit: save the previewed device read as the verified revision.

        `profile` must equal the last `async_read_profile_snapshot` result of
        this coordinator, so only a real strict device read can be committed.
        This user-confirmed save is what marks the protocol verified and
        unlocks device control.
        """
        if not confirmed:
            raise ProfileValidationError("Confirm the full device profile snapshot")
        desired = require_complete_profile(profile)
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        async with self._profile_edit_operation():
            if self._previewed_profile is None or desired != self._previewed_profile:
                raise HomeAssistantError("Read the device profile again to confirm it")
            old = self.profile_record
            if expected_revision != old.revision:
                raise RevisionConflictError("The saved profile changed")
            await self._save(self._as_verified(_new_revision(old, desired)))
            self._previewed_profile = None
            # The user chose the device read as it is.
            await self._set_temporary_routine_day(None)
            self._reset_seen = False
            self._set_protocol_verified(True)
            self._schedule_recovery()

    async def async_read_profile_snapshot(self) -> tuple[dict[str, Any], int]:
        """Read + preview: all persistent blocks in one strict fresh session.

        Nothing is saved and control stays locked. The caller previews the
        returned complete snapshot and commits it with
        `async_accept_device_profile`, which only accepts this exact read.
        Missing, malformed, inconsistent, or stale responses abort the whole
        read and leave the private Store unchanged.
        """
        async with self._operation():
            record = self.profile_record
            try:
                _client, snapshot = await self._async_fresh_snapshot()
            except Exception as err:
                await self._disconnect()
                raise _error("profile_read_failed") from err
            # Keep this session: it already delivered a fresh GLOBAL_STATE and
            # keeps pushing updates. Closing it only forced an immediate
            # reconnect (recovery) to read the same state again.
            self._previewed_profile = deepcopy(snapshot.profile)
            # No-op while the kept session is available.
            self._schedule_recovery()
            return snapshot.profile, record.revision

    # ---- Profile restore ----

    async def async_restore_profile(
        self, expected_revision: int, *, confirmed: bool = False
    ) -> ProfileRestoreResult:
        """Apply the saved complete profile to the device (explicit user action).

        Sequence, all under the coordinator lock so no edit can interleave:
        revision CAS -> new strict session and full readback -> clock sync if
        it deviates -> only differing blocks in the documented order of
        `restore.build_restore_steps` -> new session and full readback ->
        verified only if every block matches. Returns the verified result;
        any failure raises `ProfileRestoreError` whose `result` lists the
        applied steps and mismatching blocks. Nothing is retried here.

        Requires protocol_verified, the enrolled device key, maintenance off
        and a structurally complete saved revision. The
        revision may be a pending edit (this is how offline schedule edits
        reach the device) but never one verified on a different device key.
        Automatic restore is stricter: see `_async_recover`.
        """
        if not confirmed:
            raise ProfileValidationError("Confirm the profile restore")
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        async with self._device_write_operation():
            record = self._profile_record
            if record.revision != expected_revision:
                raise RevisionConflictError("The saved profile changed")
            if record.verified_fingerprint not in (None, self.device_fingerprint):
                raise _error("profile_other_device")
            require_complete_profile(record.desired_profile)
            try:
                client, snapshot = await self._async_fresh_snapshot()
                clock_synced = await self._async_sync_clock_if_needed(
                    client, snapshot.clock, snapshot.read_at
                )
            except Exception as err:
                await self._disconnect()
                raise _error("profile_read_failed") from err
            if snapshot.state["operationMode"] == ROUTINE_OPERATION_MODE:
                raise _error("routine_running")
            return await self._async_restore(
                record, client, snapshot, automatic=False, clock_synced=clock_synced
            )

    async def _async_restore(
        self,
        record: ProfileRecord,
        client: SafeLumalouClient,
        snapshot: DeviceSnapshot,
        *,
        automatic: bool,
        clock_synced: bool,
    ) -> ProfileRestoreResult:
        """Write the minimal diff and prove it with a fresh complete readback."""
        desired = record.desired_profile
        steps = build_restore_steps(desired, snapshot.profile)
        planned = tuple(step.name for step in steps)
        applied: list[str] = []

        def result(**changes: Any) -> ProfileRestoreResult:
            return ProfileRestoreResult(
                revision=record.revision,
                automatic=automatic,
                planned_steps=planned,
                applied_steps=tuple(applied),
                clock_synced=clock_synced,
                finished_at=dt_util.utcnow(),
                **{"verified": False, **changes},
            )

        mismatched: tuple[str, ...] = ()
        if steps:
            # A preview taken before these writes no longer describes the device.
            self._previewed_profile = None
            await self._save(
                replace(self._profile_record, sync_status="applying", last_error=None)
            )
            try:
                for step in steps:
                    await client.send(step.payload, timeout=RESPONSE_TIMEOUT)
                    applied.append(step.name)
                _client, verification = await self._async_fresh_snapshot()
            except Exception as err:
                await self._disconnect()
                error = (
                    "restore_verify" if len(applied) == len(steps) else "restore_write"
                )
                raise await self._async_restore_failed(result(error=error)) from err
            comparable = _verifiable(desired, verification)
            mismatched = changed_blocks(comparable, verification.profile)
            for difference in _field_differences(
                comparable, verification.profile, mismatched
            ):
                _LOGGER.warning("Lumalou restore mismatch: %s", difference)
        if mismatched:
            raise await self._async_restore_failed(
                result(error="restore_mismatch", mismatched_blocks=mismatched)
            )
        await self._save(self._as_verified(self._profile_record))
        self._restore_needed = None
        self._reset_seen = False
        # The device now has every saved routine, one-off or not.
        await self._set_temporary_routine_day(None)
        self.last_restore_result = result(verified=True)
        self._notify()
        return self.last_restore_result

    def _as_verified(self, record: ProfileRecord) -> ProfileRecord:
        """Mark a revision that a fresh full read on this device key matched."""
        return replace(
            record,
            verified_revision=record.revision,
            verified_fingerprint=self.device_fingerprint,
            pending=False,
            sync_status="saved",
            last_error=None,
        )

    async def _async_restore_failed(
        self, outcome: ProfileRestoreResult
    ) -> ProfileRestoreError:
        """Record a failed restore durably and build the error to raise."""
        self.last_restore_result = outcome
        need = self.restore_needed
        if (
            outcome.automatic
            and need is not None
            and need.auto_restore_attempts >= AUTO_RESTORE_MAX_ATTEMPTS
        ):
            self._restore_needed = replace(need, auto_restore_exhausted=True)
        await self._save(
            replace(self._profile_record, sync_status="error", last_error=outcome.error)
        )
        _LOGGER.warning(
            "Lumalou profile restore failed (%s); applied %s of %s steps; "
            "mismatched blocks: %s",
            outcome.error,
            len(outcome.applied_steps),
            len(outcome.planned_steps),
            ", ".join(outcome.mismatched_blocks) or "-",
        )
        return ProfileRestoreError("Lumalou profile restore was not verified", outcome)

    async def async_shutdown(self) -> None:
        """Cancel entry operations and release BLE without deleting saved intent."""
        self.async_stop_callbacks()
        self._stopped = True
        self.present = False
        self._cancel_recovery()
        client = self._invalidate()
        current = asyncio.current_task()
        operations = [task for task in self._tasks if task is not current]
        for task in operations:
            task.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
        cleanup = [task for task in self._background_tasks if task is not current]
        await asyncio.gather(*cleanup, return_exceptions=True)
        async with self._lock:
            if client is not None:
                await self._close_client(client)
        self._listeners.clear()
