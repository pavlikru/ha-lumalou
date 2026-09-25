"""Serialized HA adaptation: strict BLE sessions, profile restore and recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from lumalou import commands  # type: ignore[attr-defined]
from lumalou.advertisement import MANUFACTURER_ID, parse_advertisement
from lumalou.client import FreshSessionRequiredError
from lumalou.responses import CurrentDate

from .const import (
    AUTO_RESTORE_MAX_ATTEMPTS,
    CLOCK_SYNC_TOLERANCE,
    CONF_AUTO_RESTORE,
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    CONNECT_TIMEOUT,
    DEFAULT_AUTO_RESTORE,
    DOMAIN,
    GLOBAL_STATE_FIELDS,
    RECOVERY_COOLDOWN,
    RECOVERY_MAX_COOLDOWN,
    RESPONSE_TIMEOUT,
)
from .models import (
    DAYS,
    ProfileReconciliationPlan,
    ProfileRecord,
    ProfileValidationError,
    RevisionConflictError,
    export_profile_payload,
    import_profile_payload,
    plan_profile_reconciliation,
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
# Profile scalars that GLOBAL_STATE reports back after a live setter.
_LIVE_STATE_FIELDS = {
    "brightness": "lightBrightness",
    "color": "lightColor",
    "volume": "currentVolume",
    "light_duration": "lightDuration",
    "playlist_duration": "playlistDuration",
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
    "refresh_failed": "Lumalou refresh failed",
    "clock_untrusted": "Home Assistant clock is not trustworthy",
}


def _error(key: str) -> HomeAssistantError:
    """Build a translated user-facing error with an English log message."""
    return HomeAssistantError(
        _ERRORS[key], translation_domain=DOMAIN, translation_key=key
    )


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
        self.data: dict[str, int] | None = None
        self.present = False
        self.available = False
        # Runtime-only restore/recovery state for entities, Repairs and
        # diagnostics. Never persisted and never contains schedule values.
        self.last_restore_result: ProfileRestoreResult | None = None
        self.last_clock_offset: int | None = None
        self.last_clock_sync: datetime | None = None
        self._restore_needed: RestoreNeeded | None = None
        # Last strict read offered for confirmation; only it can be committed.
        self._previewed_profile: dict[str, Any] | None = None
        self._profile_record = ProfileRecord()
        self._store = store or ProfileStore(hass, entry.entry_id)
        self._client: SafeLumalouClient | None = None
        self._generation = 0
        self._received = 0
        self._callback_state: dict[str, int] | None = None
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
    def observed_state(self) -> dict[str, int] | None:
        """Return a detached observation, never the desired profile."""
        return deepcopy(self.data)

    @property
    def auto_restore_enabled(self) -> bool:
        """Return the user's opt-in for automatic restore (default off)."""
        return self.entry.options.get(CONF_AUTO_RESTORE, DEFAULT_AUTO_RESTORE) is True

    @property
    def restore_needed(self) -> RestoreNeeded | None:
        """Return the current verified-profile mismatch, if still relevant.

        Set by background recovery when a fresh complete read no longer
        matches the verified current revision (power-loss/reset heuristic).
        A newer saved revision makes it obsolete automatically.
        """
        need = self._restore_needed
        if need is None or need.revision != self._profile_record.revision:
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
        self.present = True
        try:
            advertisement = parse_advertisement(
                service_info.manufacturer_data[MANUFACTURER_ID]
            )
        except KeyError, TypeError, ValueError, AttributeError:
            pass
        else:
            # Passive and unauthenticated: a display value, never a gate.
            if advertisement.firmware_version:
                self.sw_version = advertisement.firmware_version
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
    def _async_handle_session_lost(self, generation: int) -> None:
        """React to a remote link loss of the live session (e.g. power loss).

        The upstream client has already invalidated and cleans up its own
        transport. A short power cycle may never make HA mark the device
        unavailable, so schedule recovery once it advertises again.
        """
        if self._stopped or generation != self._generation or not self.available:
            return
        self.available = False
        self.data = None
        self._callback_state = None
        self._notify()
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
        """Reconnect after the device reappears.

        Unverified entries only read GLOBAL_STATE. Verified entries take one
        strict full read, correct a deviating device clock from HA local time,
        compare the verified saved revision and, only if the user opted in,
        restore it automatically (bounded attempts per detected event).
        """
        if not self.protocol_verified:
            await self.async_request_refresh()
            return
        async with self._device_write_operation():
            try:
                client, snapshot = await self._async_fresh_snapshot()
                clock_synced = await self._async_sync_clock_if_needed(client, snapshot)
            except Exception as err:
                await self._disconnect()
                raise HomeAssistantError("Lumalou recovery failed") from err
            record = self._profile_record
            need = self._detect_restore_needed(record, snapshot.profile)
            if need is None or not self.auto_restore_enabled:
                return
            if need.auto_restore_attempts >= AUTO_RESTORE_MAX_ATTEMPTS:
                if not need.auto_restore_exhausted:
                    self._restore_needed = replace(need, auto_restore_exhausted=True)
                    self._notify()
                return
            self._restore_needed = replace(
                need, auto_restore_attempts=need.auto_restore_attempts + 1
            )
            _LOGGER.info("Lumalou profile differs from device; restoring it")
            await self._async_restore(
                record, client, snapshot, automatic=True, clock_synced=clock_synced
            )

    def _detect_restore_needed(
        self, record: ProfileRecord, observed: dict[str, Any]
    ) -> RestoreNeeded | None:
        """Flag a verified revision that the device no longer matches.

        Only a current revision verified on this same device key qualifies;
        pending/unverified intent is never treated as a power-loss signal.
        """
        need: RestoreNeeded | None = None
        if (
            record.is_verified
            and record.verified_fingerprint == self.device_fingerprint
            and profile_is_complete(record.desired_profile)
        ):
            blocks = changed_blocks(record.desired_profile, observed)
            if blocks:
                previous = self.restore_needed
                need = (
                    RestoreNeeded(record.revision, blocks, dt_util.utcnow())
                    if previous is None
                    else replace(previous, changed_blocks=blocks)
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

    async def _save_edit(
        self, changes: dict[str, Any], expected_revision: int | None = None
    ) -> None:
        old = self.profile_record
        if expected_revision is not None:
            validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        if expected_revision is not None and expected_revision != old.revision:
            raise RevisionConflictError("The saved profile changed; reopen the editor")
        desired = validate_profile({**old.desired_profile, **changes})
        await self._save(_new_revision(old, desired))

    async def async_edit_profile(
        self, changes: dict[str, Any], expected_revision: int
    ) -> ProfileRecord:
        """Atomically merge schema-v2 intent changes without using Bluetooth.

        `changes` is a partial logical profile, not a protocol payload. The
        supplied revision is mandatory so independent editors cannot silently
        overwrite each other. Applying it is a separate explicit restore.
        """
        validated_changes = validate_profile(changes)
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        async with self._profile_edit_operation():
            if not profile_is_complete(self._profile_record.desired_profile):
                raise HomeAssistantError("Read the device profile before editing")
            # Merges only supplied logical blocks: never removes saved blocks
            # and never fabricates hardware values.
            await self._save_edit(validated_changes, expected_revision)
            return self.profile_record

    # ---- BLE session ----

    def _receive(self, generation: int, state: dict) -> None:
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
        self._callback_state = dict(state)
        self._received += 1
        self.data = dict(state)
        self.available = True
        if self._unavailable_logged:
            _LOGGER.info("%s is available again", self.device_name)
            self._unavailable_logged = False
        self._notify()

    async def _connect(self) -> SafeLumalouClient:
        if self._profile_record.maintenance:
            raise _error("maintenance_mode")
        if self._callbacks_started and not self.present:
            raise HomeAssistantError("Lumalou is not advertising")
        if self._client is not None and self._client.connected:
            return self._client
        await self._disconnect()
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
            disconnected_callback=lambda _client: self._async_handle_session_lost(
                generation
            ),
        )
        self._client = client
        try:
            await client.connect(timeout=CONNECT_TIMEOUT)
        except BaseException:
            await self._disconnect()
            raise
        return client

    @callback
    def _invalidate(self) -> SafeLumalouClient | None:
        """Invalidate local state and detach the current session synchronously."""
        self._generation += 1
        self._callback_state = None
        self.available = False
        self.data = None
        client, self._client = self._client, None
        self._notify()
        return client

    async def _close_client(self, client: SafeLumalouClient) -> None:
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await client.disconnect()
        except Exception:
            # Local invalidation is authoritative. Teardown failure must not hide
            # the original command error or prevent config-entry unload.
            pass

    async def _disconnect(self) -> None:
        if client := self._invalidate():
            await self._close_client(client)

    async def _request_fresh_state(self, client: SafeLumalouClient) -> None:
        generation, received = self._generation, self._received
        result = await client.request_state(timeout=RESPONSE_TIMEOUT)
        if (
            generation != self._generation
            or self._received <= received
            or self._callback_state is None
            or result != self._callback_state
        ):
            raise HomeAssistantError("No fresh Lumalou state response")
        self.data = dict(self._callback_state)
        self.available = True
        self._notify()

    async def _refresh(self) -> None:
        """Read GLOBAL_STATE; a strict session allows one read, so reconnect."""
        client = await self._connect()
        try:
            await self._request_fresh_state(client)
        except FreshSessionRequiredError:
            await self._disconnect()
            await self._request_fresh_state(await self._connect())

    async def async_request_refresh(self) -> None:
        """Reject cache fallback; invalidation isolates the next request session."""
        async with self._operation():
            try:
                await self._refresh()
            except Exception as err:
                await self._disconnect()
                raise _error("refresh_failed") from err

    async def _read_snapshot(self, client: SafeLumalouClient) -> DeviceSnapshot:
        """Read every persistent block once in the current strict session."""

        async def read(name: str) -> Any:
            return (await client.request_named(name, timeout=RESPONSE_TIMEOUT)).decode()

        state = dict(await client.request_state(timeout=RESPONSE_TIMEOUT))
        device_clock = await read("current_date")
        read_at = dt_util.now()
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
        await self._disconnect()
        client = await self._connect()
        return client, await self._read_snapshot(client)

    async def _async_sync_clock_if_needed(
        self, client: SafeLumalouClient, snapshot: DeviceSnapshot
    ) -> bool:
        """Correct the device clock from HA local time beyond a small tolerance."""
        if _trusted_now() is None:
            _LOGGER.warning("Home Assistant clock is not trustworthy; not syncing")
            return False
        self.last_clock_offset = clock_offset_seconds(snapshot.clock, snapshot.read_at)
        if self.last_clock_offset <= CLOCK_SYNC_TOLERANCE:
            return False
        now = dt_util.now()
        await client.send(set_current_date_payload(now), timeout=RESPONSE_TIMEOUT)
        self.last_clock_sync = now
        self.last_clock_offset = 0
        return True

    # ---- Live controls ----

    async def _send_commands(self, payloads: list[bytes]) -> None:
        self._assert_device_writes_allowed()
        # A preview taken before this write no longer describes the device.
        self._previewed_profile = None
        client = await self._connect()
        for payload in payloads:
            await client.send(payload, timeout=RESPONSE_TIMEOUT)
        await self._refresh()

    def _live_edit_verified(self, record: ProfileRecord) -> bool:
        """Return whether a fresh GLOBAL_STATE proves a live scalar edit.

        Only an edit directly on top of a revision verified on this device
        qualifies, and only if every changed block is a GLOBAL_STATE scalar
        that the fresh state now reports. Other blocks were not written, so
        the verified baseline still covers them and power-loss detection
        stays armed. Anything else stays pending until an explicit restore.
        """
        previous, state = record.previous, self.data
        if (
            state is None
            or previous is None
            or record.verified_revision != previous["revision"]
            or record.verified_fingerprint != self.device_fingerprint
            or not profile_is_complete(record.desired_profile)
        ):
            return False
        desired, baseline = record.desired_profile, previous["profile"]
        changed = {name for name in desired if desired[name] != baseline.get(name)}
        return changed <= set(_LIVE_STATE_FIELDS) and all(
            state[_LIVE_STATE_FIELDS[name]] == desired[name] for name in changed
        )

    async def _apply_edit(self, payloads: list[bytes]) -> None:
        if self._profile_record.maintenance:
            return
        try:
            await self._send_commands(payloads)
        except Exception:
            await self._disconnect()
            await self._save(
                replace(
                    self.profile_record, sync_status="error", last_error="ble_apply"
                )
            )
            return
        record = self.profile_record
        if self._live_edit_verified(record):
            record = replace(
                record,
                verified_revision=record.revision,
                pending=False,
                sync_status="saved",
                last_error=None,
            )
        else:
            record = replace(record, sync_status="partial", last_error=None)
        await self._save(record)

    async def _transient(self, payloads: list[bytes]) -> None:
        try:
            await self._send_commands(payloads)
        except Exception as err:
            await self._disconnect()
            raise _error("command_failed") from err

    async def async_set_light(
        self, on: bool, brightness: int | None = None, color: int | None = None
    ) -> None:
        """Persist explicit settings; on/off alone never changes saved intent."""
        if type(on) is not bool:
            raise ProfileValidationError("Invalid light state")
        changes = {}
        if brightness is not None:
            # Schema v2 can preserve an observed zero, but the live control
            # path does not write it until its side effect is accepted.
            validate_integer(brightness, 1, 9, "brightness")
            changes["brightness"] = brightness
        if color is not None:
            changes["color"] = color
        validate_profile(changes)
        async with self._device_write_operation():
            if changes:
                await self._save_edit(changes)
            if not on:
                await self._transient([commands.turn_off_backlight()])
                return
            payloads = []
            if color is not None:
                payloads.append(commands.set_light_color(color))
            if brightness is not None:
                payloads.append(commands.set_led_brightness(brightness))
            if not payloads:
                # Explicit light on: never SET_GLOBAL_ON (it also affects audio)
                # and never replay a saved zero brightness here.
                level = self._profile_record.desired_profile.get("brightness", 1) or 1
                payloads.append(commands.set_led_brightness(level))
            if changes:
                await self._apply_edit(payloads)
            else:
                await self._transient(payloads)

    async def async_set_volume(self, level: int) -> None:
        validate_profile({"volume": level})
        async with self._device_write_operation():
            await self._save_edit({"volume": level})
            await self._apply_edit([commands.set_volume(level)])

    async def async_set_light_duration(self, duration: int) -> None:
        validate_profile({"light_duration": duration})
        async with self._device_write_operation():
            await self._save_edit({"light_duration": duration})
            await self._apply_edit([commands.set_light_duration(duration)])

    async def async_set_playlist_duration(self, duration: int) -> None:
        """Persist and apply a supported playlist duration setting."""
        validate_profile({"playlist_duration": duration})
        async with self._device_write_operation():
            await self._save_edit({"playlist_duration": duration})
            await self._apply_edit([commands.set_playlist_duration(duration)])

    async def async_play(self, source: int) -> None:
        validate_integer(source, 0, 7, "audio source")
        async with self._device_write_operation():
            await self._transient([commands.play_audio(source)])

    async def async_stop_audio(self) -> None:
        async with self._device_write_operation():
            await self._transient([commands.turn_off_audio()])

    async def async_sync_clock(self) -> None:
        """Explicit action only; never send a stored or naive host timestamp."""
        async with self._device_write_operation():
            now = _trusted_now()
            if now is None:
                raise _error("clock_untrusted")
            await self._transient([set_current_date_payload(now)])
            self.last_clock_sync = now

    async def async_set_maintenance(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ProfileValidationError("Invalid maintenance state")
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
        """Import a validated backup only; never read/restore the device first."""
        if not confirmed:
            raise ProfileValidationError("Confirm a supported subset profile import")
        desired = import_profile_payload(payload)
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
            verified_revision = old.revision + 1
            await self._save(
                replace(
                    _new_revision(old, desired),
                    verified_revision=verified_revision,
                    verified_fingerprint=self.device_fingerprint,
                    pending=False,
                    sync_status="saved",
                )
            )
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
                raise _error("profile_read_failed") from err
            finally:
                await self._disconnect()
            self._previewed_profile = deepcopy(snapshot.profile)
            self._schedule_recovery()
            return snapshot.profile, record.revision

    # ---- Profile restore ----

    async def async_plan_profile_restore(
        self, expected_revision: int
    ) -> ProfileReconciliationPlan:
        """Freshly compare a saved full profile without issuing any BLE writes.

        This preview does not apply setters or advance verified_revision. The
        executor (`async_restore_profile`) re-reads the device itself.
        """
        validate_integer(expected_revision, 0, _MAX_REVISION, "expected revision")
        record = self.profile_record
        if record.maintenance:
            raise _error("maintenance_mode")
        if record.revision != expected_revision:
            raise RevisionConflictError("The saved profile changed")
        require_complete_profile(record.desired_profile)

        observed, read_revision = await self.async_read_profile_snapshot()
        if read_revision != expected_revision:
            raise RevisionConflictError("The saved profile changed during readback")

        async with self._profile_edit_operation():
            record = self.profile_record
            if record.maintenance:
                raise _error("maintenance_mode")
            return plan_profile_reconciliation(
                record, observed, expected_revision=expected_revision
            )

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
            if record.maintenance:
                raise _error("maintenance_mode")
            if record.revision != expected_revision:
                raise RevisionConflictError("The saved profile changed")
            if record.verified_fingerprint not in (None, self.device_fingerprint):
                raise _error("profile_other_device")
            require_complete_profile(record.desired_profile)
            try:
                client, snapshot = await self._async_fresh_snapshot()
                clock_synced = await self._async_sync_clock_if_needed(client, snapshot)
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
        await self._save(
            replace(
                self._profile_record,
                verified_revision=record.revision,
                verified_fingerprint=self.device_fingerprint,
                pending=False,
                sync_status="saved",
                last_error=None,
            )
        )
        self._restore_needed = None
        self.last_restore_result = result(verified=True)
        self._notify()
        return self.last_restore_result

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
