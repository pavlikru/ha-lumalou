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

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from lumalou import commands  # type: ignore[attr-defined]
from lumalou.advertisement import MANUFACTURER_ID, parse_advertisement
from lumalou.client import FreshSessionRequiredError, ResponseEnvelope
from lumalou.responses import CurrentDate

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
    GATT_TIMEOUT,
    GLOBAL_STATE_FIELDS,
    RECONNECT_DELAY,
    RECOVERY_COOLDOWN,
    RECOVERY_MAX_COOLDOWN,
    RESET_CLOCK_OFFSET,
    RESPONSE_TIMEOUT,
    STATE_CONFIRM_TIMEOUT,
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
    validate_integer,
    validate_profile,
)
from .restore import (
    ProfileRestoreResult,
    RestoreNeeded,
    build_restore_steps,
    changed_blocks,
    clock_offset_seconds,
    profile_from_readback,
    set_current_date_payload,
)
from .storage import ProfileStore
from .transport import SafeLumalouClient

_LOGGER = logging.getLogger(__name__)
_MAX_REVISION = 2**63 - 1
_CURRENT_DATE = 0x13
# Light and sound profile key -> (GLOBAL_STATE field, maximum, setter).
_LEVELS: dict[str, tuple[str, int, Callable[[int], bytes]]] = {
    "volume": ("currentVolume", 9, commands.set_volume),
    "light_duration": ("lightDuration", 5, commands.set_light_duration),
    "playlist_duration": ("playlistDuration", 6, commands.set_playlist_duration),
}
_STATE_RANGES = {
    "currentSong": (0, 18),
    "currentVolume": (0, 9),
    "lightBrightness": (0, 9),
    "lightColor": (0, 9),
    "playlistDuration": (0, 6),
    "lightDuration": (0, 5),
    "currentStage": (0, 3),
    "clockFormat": (0, 1),
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
    """Build the next pending revision, keeping the previous one for undo."""
    return replace(
        old,
        revision=old.revision + 1,
        desired_profile=desired,
        previous={"revision": old.revision, "profile": old.desired_profile},
        pending=True,
        sync_status="pending",
        last_error=None,
    )


_ERRORS = {
    "maintenance_mode": "Lumalou is in maintenance mode",
    "control_locked": "Read and verify the complete Lumalou profile before control",
    "profile_read_failed": "Could not read a complete, consistent Lumalou profile",
    "profile_other_device": "The saved profile was verified on a different device",
    "command_failed": "Lumalou command failed; it will not be replayed",
    "clock_untrusted": "Home Assistant clock is not trustworthy",
}


# Caused by the entry's state, which the user can change; not a device fault.
_USER_STATE_ERRORS = frozenset(
    {"maintenance_mode", "control_locked", "clock_untrusted"}
)


def _error(key: str) -> HomeAssistantError:
    """Build a translated user-facing error with an English log message."""
    error = ServiceValidationError if key in _USER_STATE_ERRORS else HomeAssistantError
    return error(_ERRORS[key], translation_domain=DOMAIN, translation_key=key)


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
        self._state_event = asyncio.Event()
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
            if self._stopped or not self.present or self._profile_record.maintenance:
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
        already matches becomes verified. A device clock far off plus a
        profile that differs from the verified one is a power-loss reset:
        it is restored automatically unless the user switched that off
        (bounded attempts per event), otherwise a Repair is raised.
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
                client, snapshot = await self._async_fresh_snapshot()
                # Judge the reset marker before the clock is corrected.
                reset = _trusted_now() is not None and (
                    clock_offset_seconds(snapshot.clock, snapshot.read_at)
                    > RESET_CLOCK_OFFSET
                )
                try:
                    clock_synced = await self._async_sync_clock_if_needed(
                        client, snapshot.clock, snapshot.read_at, automatic=True
                    )
                except ClockSyncError:
                    # The failed write retired the session and further
                    # automatic clock writes are paused: read once more
                    # without it, so recovery itself still completes.
                    client, snapshot = await self._async_fresh_snapshot()
                    clock_synced = False
            except Exception as err:
                await self._disconnect()
                raise HomeAssistantError("Lumalou recovery failed") from err
            record = self._profile_record
            if (
                not record.is_verified
                and record.verified_fingerprint in (None, self.device_fingerprint)
                and record.desired_profile == snapshot.profile
            ):
                await self._save(self._as_verified(record))
                record = self._profile_record
            need = self._detect_restore_needed(record, snapshot.profile, reset=reset)
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

    def _detect_restore_needed(
        self, record: ProfileRecord, observed: dict[str, Any], *, reset: bool
    ) -> RestoreNeeded | None:
        """Flag a verified revision that the device no longer matches.

        Only a current revision verified on this same device key qualifies;
        pending/unverified intent is never treated as a power-loss signal.
        The light and sound block also changes in everyday use (device
        buttons), so it is compared only with the reset marker (a far-off
        device clock). A reset stays a reset until it is resolved, even after
        the clock was corrected.
        """
        need: RestoreNeeded | None = None
        previous = self.restore_needed
        reset = reset or (previous is not None and previous.reset)
        if (
            record.is_verified
            and record.verified_fingerprint == self.device_fingerprint
            and profile_is_complete(record.desired_profile)
        ):
            blocks = changed_blocks(record.desired_profile, observed, live=reset)
            if blocks:
                need = (
                    RestoreNeeded(record.revision, blocks, dt_util.utcnow(), reset)
                    if previous is None
                    else replace(previous, changed_blocks=blocks, reset=reset)
                )
        if need != self._restore_needed:
            self._restore_needed = need
            self._notify()
        return need

    async def _async_close_detached(self, client: SafeLumalouClient) -> None:
        """Close a detached session after any active serialized operation."""
        async with self._lock:
            await self._close_client(client)

    # ---- Durable intent ----

    async def _save(self, record: ProfileRecord) -> None:
        await self._store.async_save(record)
        self._profile_record = deepcopy(record)
        self._notify()

    async def async_edit_profile(
        self, changes: dict[str, Any], expected_revision: int
    ) -> ProfileRecord:
        """Atomically merge profile block changes without using Bluetooth.

        `changes` is a partial logical profile, not a protocol payload. The
        supplied revision is mandatory so independent editors cannot silently
        overwrite each other. Applying it is a separate explicit restore.
        """
        validated_changes = validate_profile(changes)
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        async with self._profile_edit_operation():
            old = self.profile_record
            if not profile_is_complete(old.desired_profile):
                raise HomeAssistantError("Read the device profile before editing")
            if expected_revision != old.revision:
                raise RevisionConflictError(
                    "The saved profile changed; reopen the editor"
                )
            # Merges only supplied blocks: never removes saved blocks and
            # never fabricates hardware values.
            desired = {**old.desired_profile, **validated_changes}
            await self._save(_new_revision(old, desired))
            return self.profile_record

    # ---- BLE session ----

    @callback
    def _receive(self, generation: int, state: dict) -> None:
        """Take a GLOBAL_STATE frame of the live session (pushed or requested)."""
        if self._stopped or generation != self._generation:
            return
        if not isinstance(state, dict) or set(state) != GLOBAL_STATE_FIELDS:
            return
        if any(
            type(value) is not int or not 0 <= value <= 255 for value in state.values()
        ):
            return
        if any(
            not low <= state[key] <= high for key, (low, high) in _STATE_RANGES.items()
        ):
            return
        self.data = dict(state)
        self.available = True
        self._state_event.set()
        if self._unavailable_logged:
            _LOGGER.info("%s is available again", self.device_name)
            self._unavailable_logged = False
        self._notify()

    @callback
    def _on_response(self, generation: int, envelope: ResponseEnvelope) -> None:
        """Keep the device clock the session sees; correct drift it shows.

        The device pushes CURRENT_DATE at least every minute, so DST changes
        and drift are caught without reconnecting.
        """
        if self._stopped or generation != self._generation:
            return
        if envelope.opcode != _CURRENT_DATE:
            return
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
        """Correct the clock from the latest pushed frame, at most hourly."""
        async with self._operation():
            pushed, client = self._pushed_clock, self._client
            if (
                pushed is None
                or client is None
                or pushed[0] != self._generation
                or not self.protocol_verified
                or self._profile_record.maintenance
                or (
                    self.last_clock_sync is not None
                    and dt_util.now() - self.last_clock_sync
                    < timedelta(seconds=CLOCK_SYNC_RETRY_INTERVAL)
                )
            ):
                return
            _generation, clock, read_at = pushed
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
        await self._disconnect()
        delay = self._reconnect_at - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
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
        try:
            await client.connect(timeout=CONNECT_TIMEOUT)
        except Exception as err:
            await self._disconnect()
            self._log_connect_failure(err)
            raise
        except BaseException:
            await self._disconnect()
            raise
        return client

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
        self.data = None
        client, self._client = self._client, None
        if client is not None:
            self._reconnect_at = asyncio.get_running_loop().time() + RECONNECT_DELAY
        self._notify()
        return client

    async def _close_client(self, client: SafeLumalouClient) -> None:
        try:
            async with asyncio.timeout(GATT_TIMEOUT):
                await client.disconnect()
        except Exception:
            # Local invalidation is authoritative. Teardown failure must not hide
            # the original command error or prevent config-entry unload.
            pass

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
        return DeviceSnapshot(profile, state, device_clock, read_at)

    async def _async_fresh_snapshot(
        self,
    ) -> tuple[SafeLumalouClient, DeviceSnapshot]:
        """Open a new strict session and read the complete profile in it."""
        client = await self._async_new_session()
        return client, await self._read_snapshot(client)

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
            return
        record = self._profile_record
        if not profile_is_complete(record.desired_profile):
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
        async with self._device_write_operation():
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
        async with self._device_write_operation():
            await self._write(commands.turn_off_backlight())

    async def async_set_level(self, key: str, value: int) -> None:
        """Set the volume or a timer; none of them starts sound or light."""
        field, maximum, setter = _LEVELS[key]
        validate_integer(value, 0, maximum, key)
        async with self._device_write_operation():
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
        async with self._device_write_operation():
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
        async with self._device_write_operation():
            await self._write(commands.play_audio(source))

    async def async_stop_audio(self) -> None:
        """Stop audio only; a soother light stays on until turned off."""
        async with self._device_write_operation():
            await self._write(commands.turn_off_audio())

    async def async_sync_clock(self) -> None:
        """Explicit action only; never send a stored or naive host timestamp."""
        async with self._device_write_operation():
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
            mismatched = changed_blocks(desired, verification.profile)
        if mismatched:
            raise await self._async_restore_failed(
                result(error="restore_mismatch", mismatched_blocks=mismatched)
            )
        await self._save(self._as_verified(self._profile_record))
        self._restore_needed = None
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
