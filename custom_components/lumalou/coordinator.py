"""Serialized HA adaptation with HA-owned, read-only Bluetooth recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from lumalou import commands  # type: ignore[attr-defined]
from lumalou.client import LumalouClient

from .const import (
    ALLOWED_OPCODES,
    CONNECT_TIMEOUT,
    FORBIDDEN_OPCODES,
    GLOBAL_STATE_FIELDS,
    RECOVERY_COOLDOWN,
    RESPONSE_TIMEOUT,
)
from .models import (
    ProfileRecord,
    ProfileValidationError,
    RevisionConflictError,
    validate_integer,
    validate_profile,
)
from .storage import ProfileStore

_LOGGER = logging.getLogger(__name__)


class SafeLumalouClient(LumalouClient):
    """Explicit BLEDevice adaptation; no scanners or global monkeypatches.

    The pinned client forwards its address argument unchanged to BleakClient.
    Bleak accepts BLEDevice; this local adaptation is tested at that boundary.
    Upstream must still add strict frame validation and full-profile readback.
    """

    def __init__(self, device: BLEDevice, on_state: Callable[[dict], None]) -> None:
        super().__init__(cast(str, device), on_state=on_state)

    async def send(self, app_data: bytes) -> None:
        """Reject every unapproved application operation before any I/O."""
        if (
            not app_data
            or app_data[0] in FORBIDDEN_OPCODES
            or app_data[0] not in ALLOWED_OPCODES
        ):
            raise HomeAssistantError("Unsupported Lumalou operation")
        await super().send(app_data)


class LumalouCoordinator:
    """Own device I/O and saved intent, never claim complete synchronization."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, store: ProfileStore | None = None
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.address = entry.data["address"]
        self.device_name = entry.title or "Lumalou"
        self.sw_version: str | None = None
        self.data: dict[str, int] | None = None
        self.present = False
        self.available = False
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
        self._unsubscribers: list[Callable[[], None]] = []
        self._callbacks_started = False
        self._stopped = False
        self._storage_healthy = True

    @property
    def profile_record(self) -> ProfileRecord:
        """Return a detached saved revision so callers cannot mutate intent."""
        return deepcopy(self._profile_record)

    @property
    def observed_state(self) -> dict[str, int] | None:
        """Return a detached observation, never the desired profile."""
        return deepcopy(self.data)

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
    async def _operation(self):
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
                    await self._disconnect()
                    raise
        finally:
            if task is not None:
                self._tasks.discard(task)

    async def async_setup(self) -> None:
        """Load private intent without connecting or changing the device."""
        try:
            self._profile_record = await self._store.async_load()
        except HomeAssistantError:
            self._storage_healthy = False
            self._profile_record = replace(
                self.profile_record, sync_status="error", last_error="storage_load"
            )
        self._notify()

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
                    replay=bluetooth.BluetoothCallbackReplay.DISABLED,
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
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Record presence and coalesce read-only recovery from advertisements."""
        if self._stopped:
            return
        self.present = True
        self._notify()
        self._schedule_recovery()

    @callback
    def _async_handle_unavailable(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Invalidate immediately, then close only the detached old session."""
        if self._stopped:
            return
        self.present = False
        self._cancel_recovery()
        client = self._invalidate()
        if client is not None:
            self._create_background_task(
                self._async_close_detached(client), "lumalou unavailable disconnect"
            )

    @callback
    def _schedule_recovery(self) -> None:
        """Schedule one rate-limited read-only refresh."""
        if (
            self._stopped
            or not self.present
            or self.profile_record.maintenance
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
        """Rate-limit reconnect attempts and perform only a state read."""
        delay = self._next_recovery_at - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
        if self._stopped or not self.present or self.profile_record.maintenance:
            return
        self._next_recovery_at = asyncio.get_running_loop().time() + RECOVERY_COOLDOWN
        try:
            await self.async_request_refresh()
        except HomeAssistantError:
            _LOGGER.debug("Background Lumalou refresh failed", exc_info=True)

    async def _async_close_detached(self, client: SafeLumalouClient) -> None:
        """Close a detached session after any active serialized operation."""
        async with self._lock:
            await self._close_client(client)

    async def _save(self, record: ProfileRecord) -> None:
        if not self._storage_healthy:
            raise HomeAssistantError("Saved profile requires recovery before editing")
        try:
            await self._store.async_save(record)
        except asyncio.CancelledError:
            # ProfileStore guarantees that a started commit has finished before
            # propagating cancellation. Publish the same durable revision in RAM.
            self._profile_record = deepcopy(record)
            self._notify()
            raise
        self._profile_record = deepcopy(record)
        self._notify()

    async def _save_edit(
        self, changes: dict[str, Any], expected_revision: int | None = None
    ) -> None:
        old = self.profile_record
        if expected_revision is not None:
            validate_integer(expected_revision, 0, 2**63 - 1, "expected revision")
        if expected_revision is not None and expected_revision != old.revision:
            raise RevisionConflictError("The saved profile changed; reopen the editor")
        desired = validate_profile({**old.desired_profile, **changes})
        await self._save(
            replace(
                old,
                revision=old.revision + 1,
                desired_profile=desired,
                previous={"revision": old.revision, "profile": old.desired_profile},
                pending=True,
                sync_status="pending",
                last_error=None,
            )
        )

    def _receive(self, generation: int, state: dict) -> None:
        if self._stopped or generation != self._generation:
            return
        # The release has already discarded framing evidence. This is only a
        # decoded observation, never an importable/verified profile snapshot.
        if not isinstance(state, dict) or set(state) != GLOBAL_STATE_FIELDS:
            return
        if any(
            type(value) is not int or not 0 <= value <= 255 for value in state.values()
        ):
            return
        ranges = {
            "currentSong": (0, 18),
            "currentVolume": (0, 9),
            "lightBrightness": (0, 9),
            "lightColor": (0, 9),
            "playlistDuration": (0, 6),
            "lightDuration": (0, 5),
            "currentStage": (0, 3),
            "clockFormat": (0, 1),
        }
        if any(not low <= state[key] <= high for key, (low, high) in ranges.items()):
            return
        self._callback_state = dict(state)
        self._received += 1
        self.data = dict(state)
        self.available = True
        self._notify()

    async def _connect(self) -> SafeLumalouClient:
        if self.profile_record.maintenance:
            raise HomeAssistantError("Lumalou is in maintenance mode")
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
            device, lambda state: self._receive(generation, state)
        )
        self._client = client
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await client.connect()
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

    async def _refresh(self) -> None:
        client = await self._connect()
        generation, received = self._generation, self._received
        async with asyncio.timeout(RESPONSE_TIMEOUT + 1):
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

    async def async_request_refresh(self) -> None:
        """Reject cache fallback; invalidation isolates the next request session."""
        async with self._operation():
            try:
                await self._refresh()
            except Exception as err:
                await self._disconnect()
                raise HomeAssistantError("Lumalou refresh failed") from err

    async def _send_commands(self, payloads: list[bytes]) -> None:
        client = await self._connect()
        for payload in payloads:
            async with asyncio.timeout(RESPONSE_TIMEOUT):
                await client.send(payload)
        await self._refresh()

    async def _apply_edit(self, payloads: list[bytes]) -> None:
        # Saved intent stays pending even on successful writes: full-profile
        # verification is impossible with the released upstream client.
        if self.profile_record.maintenance:
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
        else:
            await self._save(
                replace(self.profile_record, sync_status="partial", last_error=None)
            )

    async def _transient(self, payloads: list[bytes]) -> None:
        try:
            await self._send_commands(payloads)
        except Exception as err:
            await self._disconnect()
            raise HomeAssistantError(
                "Lumalou command failed; it will not be replayed"
            ) from err

    async def async_set_light(
        self, on: bool, brightness: int | None = None, color: int | None = None
    ) -> None:
        """Persist explicit settings; on/off alone never changes saved intent."""
        if type(on) is not bool:
            raise ProfileValidationError("Invalid light state")
        changes = {}
        if brightness is not None:
            changes["brightness"] = brightness
        if color is not None:
            changes["color"] = color
        validate_profile(changes)
        async with self._operation():
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
                # The user explicitly requested light on. Do not affect audio
                # via SET_GLOBAL_ON; reuse only an explicit saved light setting.
                level = self.profile_record.desired_profile.get("brightness", 1)
                payloads.append(commands.set_led_brightness(level))
            if changes:
                await self._apply_edit(payloads)
            else:
                await self._transient(payloads)

    async def async_set_volume(self, level: int) -> None:
        validate_profile({"volume": level})
        async with self._operation():
            await self._save_edit({"volume": level})
            await self._apply_edit([commands.set_volume(level)])

    async def async_set_light_duration(self, duration: int) -> None:
        validate_profile({"light_duration": duration})
        async with self._operation():
            await self._save_edit({"light_duration": duration})
            await self._apply_edit([commands.set_light_duration(duration)])

    async def async_play(self, source: int) -> None:
        validate_integer(source, 0, 7, "audio source")
        async with self._operation():
            await self._transient([commands.play_audio(source)])

    async def async_stop_audio(self) -> None:
        async with self._operation():
            await self._transient([commands.turn_off_audio()])

    async def async_sync_clock(self) -> None:
        """Explicit action only; never send a stored or naive host timestamp."""
        async with self._operation():
            now = dt_util.now()
            if now.year < 2026:
                raise HomeAssistantError("Home Assistant clock is not trustworthy")
            await self._transient(
                [
                    commands.set_current_date(
                        now.hour, now.minute, now.second, (now.weekday() + 1) % 7
                    )
                ]
            )

    async def async_set_maintenance(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ProfileValidationError("Invalid maintenance state")
        async with self._operation():
            await self._save(replace(self.profile_record, maintenance=enabled))
            if enabled:
                await self._disconnect()

    async def async_export_profile(self) -> dict[str, Any]:
        """Atomically export intent and its CAS revision without identifiers."""
        async with self._operation():
            record = self.profile_record
            return {
                "current_revision": record.revision,
                "profile": {
                    "schema_version": 1,
                    "scope": "supported_subset",
                    "profile": deepcopy(record.desired_profile),
                },
            }

    async def async_import_profile(
        self,
        payload: dict[str, Any],
        expected_revision: int,
        *,
        confirmed: bool = False,
    ) -> None:
        """Import a validated backup only; never read/restore the device first."""
        if (
            not confirmed
            or not isinstance(payload, dict)
            or set(payload) != {"schema_version", "scope", "profile"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["scope"] != "supported_subset"
        ):
            raise ProfileValidationError("Confirm a supported subset profile import")
        desired = validate_profile(payload["profile"])
        validate_integer(expected_revision, 0, 2**63 - 1, "expected revision")
        async with self._operation():
            old = self.profile_record
            if expected_revision != old.revision:
                raise RevisionConflictError("The saved profile changed")
            await self._save(
                replace(
                    old,
                    revision=old.revision + 1,
                    desired_profile=desired,
                    previous={"revision": old.revision, "profile": old.desired_profile},
                    pending=True,
                    sync_status="pending",
                    last_error=None,
                )
            )

    async def async_restore_profile(self) -> None:
        """Do not present unproven setter behaviour as safe restoration."""
        raise HomeAssistantError(
            "Restore requires upstream full-profile readback and hardware validation"
        )

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
