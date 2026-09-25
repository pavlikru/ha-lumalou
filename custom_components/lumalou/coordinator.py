"""Serialized HA adaptation with HA-owned, read-only Bluetooth recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

from bleak import BleakClient
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
    CONF_DEVICE_FINGERPRINT,
    CONF_PRODUCT_CODE,
    CONF_PROTOCOL_VERIFIED,
    CONNECT_TIMEOUT,
    FORBIDDEN_OPCODES,
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
    require_complete_profile,
    validate_integer,
    validate_profile,
)
from .storage import ProfileStorageError, ProfileStore
from .transport import RestrictedLumalouTransport
from .upstream_api import (
    MissingUpstreamCapabilities,
    require_factory_identity_api,
    require_full_profile_read_api,
)

_LOGGER = logging.getLogger(__name__)


class SafeLumalouClient(LumalouClient):
    """Explicit BLEDevice adaptation; no scanners or global monkeypatches.

    The pinned client forwards its address argument unchanged to BleakClient.
    Bleak accepts BLEDevice; this local adaptation is tested at that boundary.
    Its private transport assignment is wrapped before connect/handshake I/O.
    Recheck this boundary before upgrading the pinned upstream dependency.
    The required signed-identity and strict-read APIs are not in the released
    dependency yet and are checked explicitly before BLE I/O.
    """

    def __init__(
        self,
        device: BLEDevice,
        on_state: Callable[[dict], None],
        expected_device_fingerprint: str | None = None,
    ) -> None:
        self._expected_device_fingerprint = expected_device_fingerprint
        if expected_device_fingerprint is None:
            super().__init__(cast(str, device), on_state=on_state)
        else:
            try:
                require_factory_identity_api()
                super().__init__(
                    cast(str, device),
                    on_state=on_state,
                    expected_device_fingerprint=expected_device_fingerprint,
                )
            except MissingUpstreamCapabilities as error:
                raise HomeAssistantError(
                    "The installed Lumalou library cannot authenticate and "
                    "bind this device identity"
                ) from error
            except TypeError as error:
                raise HomeAssistantError(
                    "The installed Lumalou library cannot bind the verified "
                    "device identity"
                ) from error

    async def connect(
        self,
        timeout: float = CONNECT_TIMEOUT,  # noqa: ASYNC109
    ) -> SafeLumalouClient:
        """Require a pinned identity and capable upstream API before BLE I/O."""
        if self._expected_device_fingerprint is None:
            raise HomeAssistantError(
                "A verified Lumalou device identity is required before connecting"
            )
        try:
            require_factory_identity_api()
        except MissingUpstreamCapabilities as error:
            raise HomeAssistantError(
                "The installed Lumalou library cannot authenticate and "
                "bind this device identity"
            ) from error
        return await super().connect(timeout=timeout)

    @property
    def _client(self) -> RestrictedLumalouTransport | None:
        """Expose only the restricted transport to upstream protocol methods."""
        return self._restricted_transport

    @_client.setter
    def _client(self, client: BleakClient | None) -> None:
        """Guard every transport assignment, including initial/repeated connects."""
        self._restricted_transport = (
            RestrictedLumalouTransport(client) if client is not None else None
        )

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
        self.product_code = entry.data.get(CONF_PRODUCT_CODE)
        self.device_fingerprint = entry.data.get(CONF_DEVICE_FINGERPRINT)
        self.protocol_verified = entry.data.get(CONF_PROTOCOL_VERIFIED) is True
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
        self._recovery_failures = 0
        self._unsubscribers: list[Callable[[], None]] = []
        self._callbacks_started = False
        self._stopped = False
        self._storage_healthy = True
        # A public offline edit must not turn an uninitialized coordinator into
        # a new empty profile.  `async_setup` is the only point at which the
        # existing durable revision is known.
        self._profile_loaded = False

    @property
    def profile_record(self) -> ProfileRecord:
        """Return a detached saved revision so callers cannot mutate intent."""
        return deepcopy(self._profile_record)

    @property
    def profile_storage_healthy(self) -> bool:
        """Return whether the saved profile was read successfully."""
        return self._storage_healthy

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

    @asynccontextmanager
    async def _device_write_operation(self):
        """Serialize a mutation and require explicit supported-label consent."""
        async with self._operation():
            self._assert_device_writes_allowed()
            yield

    @asynccontextmanager
    async def _profile_edit_operation(self):
        """Serialize a durable-only edit without any BLE lifecycle work.

        This intentionally does not reuse `_operation`: cancelling a device
        operation safely tears down its connection, while an offline editor
        must have no Bluetooth side effects at all.
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
                yield
        finally:
            if task is not None:
                self._tasks.discard(task)

    def _assert_enrolled_device(self) -> None:
        """Block sessions until setup has bound one verified signed device key."""
        if not self.device_fingerprint:
            raise HomeAssistantError("No Lumalou device identity was enrolled")

    def _assert_device_writes_allowed(self) -> None:
        """Guard every device mutation, including callers without a connection."""
        self._assert_enrolled_device()
        if not self.protocol_verified:
            raise HomeAssistantError(
                "Read and verify the complete Lumalou profile before control"
            )

    async def async_setup(self) -> None:
        """Load private intent without connecting or changing the device."""
        try:
            self._profile_record = await self._store.async_load()
        except ProfileStorageError:
            self._storage_healthy = False
            self._profile_record = replace(
                self.profile_record, sync_status="error", last_error="storage_load"
            )
        else:
            self._profile_loaded = True
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
        self._recovery_failures = 0
        self._next_recovery_at = 0.0
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
            or not self.device_fingerprint
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
        """Retry read-only recovery with one bounded exponential-backoff loop."""
        while True:
            delay = self._next_recovery_at - asyncio.get_running_loop().time()
            if delay > 0:
                await asyncio.sleep(delay)
            if self._stopped or not self.present or self.profile_record.maintenance:
                return
            try:
                await self.async_request_refresh()
            except HomeAssistantError:
                self._recovery_failures += 1
                exponent = min(self._recovery_failures - 1, 10)
                cooldown = min(RECOVERY_COOLDOWN * 2**exponent, RECOVERY_MAX_COOLDOWN)
                self._next_recovery_at = asyncio.get_running_loop().time() + cooldown
                _LOGGER.debug(
                    "Background Lumalou refresh failed; retrying in %s seconds",
                    cooldown,
                    exc_info=True,
                )
                continue
            self._recovery_failures = 0
            self._next_recovery_at = (
                asyncio.get_running_loop().time() + RECOVERY_COOLDOWN
            )
            return

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

    async def async_edit_profile(
        self, changes: dict[str, Any], expected_revision: int
    ) -> ProfileRecord:
        """Atomically merge schema-v2 intent changes without using Bluetooth.

        `changes` is a partial logical profile, not a protocol payload.  The
        supplied revision is mandatory so independent editors cannot silently
        overwrite each other.  This works for legacy/read-only entries because
        it only updates private saved intent; applying it remains separately
        gated by the confirmed product code and hardware support.
        """
        validated_changes = validate_profile(changes)
        validate_integer(expected_revision, 0, 2**63 - 1, "expected revision")
        async with self._profile_edit_operation():
            if not self._storage_healthy or not self._profile_loaded:
                raise HomeAssistantError(
                    "Saved profile requires recovery before editing"
                )
            old = self.profile_record
            if expected_revision != old.revision:
                raise RevisionConflictError(
                    "The saved profile changed; reopen the editor"
                )
            # Merge only supplied logical blocks.  In particular, this never
            # removes unknown/saved blocks and never fabricates hardware values.
            desired = validate_profile({**old.desired_profile, **validated_changes})
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
            return self.profile_record

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
        self._assert_enrolled_device()
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
            device,
            lambda state: self._receive(generation, state),
            expected_device_fingerprint=self.device_fingerprint,
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
        self._assert_device_writes_allowed()
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
            # Schema v2 can preserve an observed/source-supported zero, but the
            # HA control path does not write it until its on/off side effect is
            # accepted on hardware.
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
                # The user explicitly requested light on. Do not affect audio
                # via SET_GLOBAL_ON. A saved zero can be preserved from a
                # source-backed profile, but its hardware side effect is not
                # accepted for the HA write path, so never replay it here.
                level = self.profile_record.desired_profile.get("brightness", 1)
                if level == 0:
                    level = 1
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
                self._cancel_recovery()
                self._recovery_failures = 0
                self._next_recovery_at = 0.0
                await self._disconnect()
            else:
                self._recovery_failures = 0
                self._next_recovery_at = 0.0
                self._schedule_recovery()

    async def async_export_profile(self) -> dict[str, Any]:
        """Atomically export intent and its CAS revision without identifiers."""
        async with self._operation():
            if not self._storage_healthy:
                raise HomeAssistantError(
                    "Saved profile requires recovery before exporting"
                )
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

    async def async_accept_device_profile(
        self,
        profile: dict[str, Any],
        expected_revision: int,
        *,
        confirmed: bool = False,
    ) -> None:
        """Save a user-confirmed full device read as the verified desired profile."""
        if not confirmed:
            raise ProfileValidationError("Confirm the full device profile snapshot")
        desired = require_complete_profile(profile)
        validate_integer(expected_revision, 0, 2**63 - 1, "expected revision")
        async with self._profile_edit_operation():
            if not self._storage_healthy:
                raise HomeAssistantError("Saved profile requires recovery first")
            old = self.profile_record
            if expected_revision != old.revision:
                raise RevisionConflictError("The saved profile changed")
            verified_revision = old.revision + 1
            await self._save(
                replace(
                    old,
                    revision=verified_revision,
                    desired_profile=desired,
                    previous={"revision": old.revision, "profile": old.desired_profile},
                    verified_revision=verified_revision,
                    pending=False,
                    sync_status="saved",
                    last_error=None,
                )
            )

    async def async_recover_profile(
        self, payload: dict[str, Any], *, confirmed: bool = False
    ) -> None:
        """Replace unreadable storage only from a confirmed validated backup."""
        if not confirmed:
            raise ProfileValidationError("Confirm saved profile recovery")
        desired = import_profile_payload(payload)
        recovered = ProfileRecord(
            revision=1,
            desired_profile=desired,
            pending=True,
            sync_status="pending",
        )
        async with self._profile_edit_operation():
            if self._storage_healthy:
                raise HomeAssistantError("Saved profile does not require recovery")
            try:
                await self._store.async_recover(recovered)
            except asyncio.CancelledError:
                # ProfileStore propagates caller cancellation only after the
                # backup, commit, and independent readback have completed.
                self._profile_record = deepcopy(recovered)
                self._storage_healthy = True
                self._profile_loaded = True
                self._notify()
                raise
            self._profile_record = deepcopy(recovered)
            self._storage_healthy = True
            self._profile_loaded = True
            self._notify()

    async def async_read_profile_snapshot(self) -> tuple[dict[str, Any], int]:
        """Read all persistent blocks in one strict fresh session, without saving.

        The caller previews and confirms the returned complete snapshot before
        replacing desired intent. Missing, malformed, inconsistent, or stale
        responses abort the whole read and leave the private Store unchanged.
        """
        async with self._operation():
            self._assert_enrolled_device()
            if not self._storage_healthy:
                raise HomeAssistantError("Saved profile requires recovery first")
            try:
                require_full_profile_read_api()
            except MissingUpstreamCapabilities as err:
                raise HomeAssistantError(
                    "The installed Lumalou library cannot read the complete profile"
                ) from err
            record = self.profile_record
            try:
                # Start an isolated request generation. Strict upstream clients
                # prohibit re-requesting a response type in the same session.
                await self._disconnect()
                client = await self._connect()
                state = getattr(client, "state", None)
                if state is None:
                    state = await client.request_state(timeout=RESPONSE_TIMEOUT)
                if not isinstance(state, dict):
                    raise HomeAssistantError("No GLOBAL_STATE snapshot was received")
                state = dict(state)

                async def read_typed(name: str) -> Any:
                    request_named = getattr(client, "request_named", None)
                    if not callable(request_named):
                        raise HomeAssistantError(
                            "The installed Lumalou library has no strict read API"
                        )
                    envelope = await request_named(name, timeout=RESPONSE_TIMEOUT)
                    decode = getattr(envelope, "decode", None)
                    if not callable(decode):
                        raise HomeAssistantError("Lumalou response is not typed")
                    return decode()

                playlist = await read_typed("music_playlist")
                clock = await read_typed("clock_settings")
                ready = await read_typed("r2r_times")
                sleepy = await read_typed("sleepy_times")
                alarms = await read_typed("r2r_alarms")
                request_day_routine = getattr(client, "request_day_routine", None)
                if not callable(request_day_routine):
                    raise HomeAssistantError(
                        "The installed Lumalou library has no daily-routine read API"
                    )
                day_order = (
                    "sunday",
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                )
                routines: dict[str, Any] = {}
                for day in day_order:
                    envelope = await request_day_routine(day, timeout=RESPONSE_TIMEOUT)
                    decode = getattr(envelope, "decode", None)
                    if not callable(decode):
                        raise HomeAssistantError("Daily routine response is not typed")
                    routines[day] = decode()

                current_state = getattr(client, "state", None)
                if current_state is not None and dict(current_state) != state:
                    raise HomeAssistantError(
                        "GLOBAL_STATE changed while reading the profile"
                    )
                if not hasattr(playlist, "slots") or len(playlist.slots) != 12:
                    raise HomeAssistantError("Playlist response is not typed")
                songs: list[int] = []
                padding_started = False
                for song in playlist.slots:
                    if type(song) is not int or not 0 <= song <= 12:
                        raise HomeAssistantError("Playlist contains an unknown song ID")
                    if song == 0:
                        padding_started = True
                    elif padding_started:
                        raise HomeAssistantError(
                            "Playlist contains unsupported interior empty slots"
                        )
                    else:
                        songs.append(song)

                if not all(
                    hasattr(clock, field)
                    for field in ("display_on", "brightness", "format")
                ):
                    raise HomeAssistantError("Clock settings response is not typed")
                expected_clock = (
                    self._profile_boolean(state["clockDisplay"], "clock display"),
                    state["clockBrightness"],
                    state["clockFormat"],
                )
                if (clock.display_on, clock.brightness, clock.format) != expected_clock:
                    raise HomeAssistantError(
                        "Clock settings disagree with GLOBAL_STATE"
                    )

                def encode_time(value: Any) -> dict[str, int] | None:
                    if value is None:
                        return None
                    if not hasattr(value, "hour") or not hasattr(value, "minute"):
                        raise HomeAssistantError("Time response is not typed")
                    return {"hour": value.hour, "minute": value.minute}

                def encode_week(value: Any) -> dict[str, Any]:
                    if not hasattr(value, "days") or len(value.days) != len(DAYS):
                        raise HomeAssistantError("Weekly response is not typed")
                    return {
                        day: encode_time(value.days[index])
                        for index, day in enumerate(DAYS)
                    }

                def encode_routine(value: Any) -> dict[str, Any]:
                    if not hasattr(value, "slots") or len(value.slots) != 12:
                        raise HomeAssistantError("Daily routine response is not typed")
                    slots: list[dict[str, int] | None] = []
                    for slot in value.slots:
                        if slot is None:
                            slots.append(None)
                        elif hasattr(slot, "step") and hasattr(slot, "task"):
                            slots.append({"step": slot.step, "task": slot.task})
                        else:
                            raise HomeAssistantError("Daily routine slot is not typed")
                    return {"time": encode_time(value.time), "slots": slots}

                raw_profile = {
                    "brightness": state["lightBrightness"],
                    "color": state["lightColor"],
                    "light_duration": state["lightDuration"],
                    "volume": state["currentVolume"],
                    "playlist_duration": state["playlistDuration"],
                    "playlist": songs,
                    "clock_settings": {
                        "display": clock.display_on,
                        "brightness": clock.brightness,
                        "format": clock.format,
                    },
                    "routine_settings": {
                        "enabled": self._profile_boolean(
                            state["routineModeStatus"], "routine mode"
                        ),
                        "music": state["routineMusicStatus"],
                        "volume": state["routineVolume"],
                        "task_reward_sfx": state["taskRewardSfx"],
                        "routine_reward_sfx": state["routineRewardSfx"],
                    },
                    "ready_to_rise": {
                        "enabled": self._profile_boolean(
                            state["ready2RiseStatus"], "ready-to-rise"
                        ),
                        "times": encode_week(ready),
                    },
                    "sleepy_times": encode_week(sleepy),
                    "alarm": {
                        "days": {
                            day: int(alarms.days[index])
                            for index, day in enumerate(DAYS)
                        },
                        "sound": alarms.sound,
                    },
                    "routines": {day: encode_routine(routines[day]) for day in DAYS},
                }
                snapshot = require_complete_profile(raw_profile)
                await self._disconnect()
            except asyncio.CancelledError:
                await self._disconnect()
                raise
            except Exception as err:
                await self._disconnect()
                raise HomeAssistantError(
                    "Could not read a complete, consistent Lumalou profile"
                ) from err
            if not self.protocol_verified:
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={**self.entry.data, CONF_PROTOCOL_VERIFIED: True},
                )
                self.protocol_verified = True
                self._notify()
            self._schedule_recovery()
            return snapshot, record.revision

    @staticmethod
    def _profile_boolean(value: Any, name: str) -> bool:
        """Decode only explicit boolean wire values; reject other nibbles."""
        if type(value) is not int or value not in (0, 1):
            raise HomeAssistantError(f"Unsupported {name} value")
        return bool(value)

    async def async_restore_profile(self) -> None:
        """Do not present unproven setter behaviour as safe restoration."""
        raise HomeAssistantError(
            "Restore requires upstream full-profile readback and hardware validation"
        )

    async def async_plan_profile_restore(
        self, expected_revision: int
    ) -> ProfileReconciliationPlan:
        """Freshly compare a saved full profile without issuing any BLE writes.

        This preview is not a restore authorization: it chooses no command
        order, does not apply setters, and does not advance verified_revision.
        An eventual executor must re-read and recheck the captured revision
        immediately before any accepted write sequence. The read session can
        update transient BLE availability/observed state, but it never saves or
        mutates the ProfileRecord.
        """
        validate_integer(expected_revision, 0, 2**63 - 1, "expected revision")
        if not self._profile_loaded or not self._storage_healthy:
            raise HomeAssistantError("Saved profile requires recovery first")
        self._assert_enrolled_device()
        record = self.profile_record
        if record.maintenance:
            raise HomeAssistantError("Lumalou is in maintenance mode")
        if record.revision != expected_revision:
            raise RevisionConflictError("The saved profile changed")
        require_complete_profile(record.desired_profile)

        observed, read_revision = await self.async_read_profile_snapshot()
        if read_revision != expected_revision:
            raise RevisionConflictError("The saved profile changed during readback")

        async with self._profile_edit_operation():
            record = self.profile_record
            if not self._storage_healthy or not self._profile_loaded:
                raise HomeAssistantError("Saved profile requires recovery first")
            if record.maintenance:
                raise HomeAssistantError("Lumalou is in maintenance mode")
            self._assert_enrolled_device()
            return plan_profile_reconciliation(
                record,
                observed,
                expected_revision=expected_revision,
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
