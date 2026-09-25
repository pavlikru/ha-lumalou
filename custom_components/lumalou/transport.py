"""HA-owned BLE transport and command policy for the pinned upstream client.

The upstream client owns the handshake, encryption and framing. This module
only supplies Home Assistant's connection path (``establish_connection`` with
the current connectable ``BLEDevice``) through the public ``client_factory``
hook, and restricts which characteristics and opcodes can ever be used.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from uuid import UUID

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from lumalou.client import FACTORY, LumalouClient, ResponseEnvelope
from lumalou.factory import parse_factory_device_fingerprint

from .const import (
    ALLOWED_REQUEST_OPCODES,
    ALLOWED_SEND_OPCODES,
    FORBIDDEN_OPCODES,
    GATT_TIMEOUT,
    WRITE_CHARACTERISTICS,
)

_FACTORY_CHARACTERISTICS = frozenset({FACTORY})
_RX_CHARACTERISTICS = frozenset({"4cea0003-c678-4202-b5d3-712dbb5e5b14"})


def _allowed_uuid(characteristic: str, allowed: frozenset[str]) -> str:
    """Resolve only explicit UUID strings; never resolve handles or objects."""
    if isinstance(characteristic, str):
        try:
            normalized = str(UUID(characteristic))
        except ValueError:
            pass
        else:
            if normalized in allowed:
                return normalized
    raise HomeAssistantError("Unsupported Lumalou GATT characteristic")


def _require_opcode(app_data: bytes, allowed: frozenset[int]) -> None:
    """Deny unknown and explicitly unsafe application opcodes before I/O."""
    if (
        not isinstance(app_data, bytes | bytearray)
        or not app_data
        or app_data[0] in FORBIDDEN_OPCODES
        or app_data[0] not in allowed
    ):
        raise HomeAssistantError("Unsupported Lumalou operation")


class RestrictedLumalouTransport:
    """Thin ``client_factory`` product: HA connection path plus GATT allowlist.

    ``connect()`` looks up the current connectable ``BLEDevice`` from Home
    Assistant's Bluetooth manager and connects with ``establish_connection``.
    Only the five operations the upstream handshake needs exist; descriptor
    writes, DFU and arbitrary characteristics are unreachable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device: BLEDevice,
        *,
        disconnected_callback: Callable[[BleakClient], None] | None = None,
    ) -> None:
        if not isinstance(device, BLEDevice):
            raise HomeAssistantError("A Home Assistant BLEDevice is required")
        self._hass = hass
        self._device = device
        self._disconnected_callback = disconnected_callback
        self.__client: BleakClient | None = None

    def _current_device(self) -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(
            self._hass, self._device.address, connectable=True
        )

    async def connect(self) -> None:
        """Connect through HA's scanner/adapters; never start a Bleak scan."""
        device = self._current_device()
        if device is None:
            raise HomeAssistantError("No connectable Lumalou device is available")
        self.__client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            device.name or "Lumalou",
            disconnected_callback=self._disconnected_callback,
            ble_device_callback=lambda: self._current_device() or device,
        )

    async def disconnect(self) -> None:
        """Release the underlying connection, if one was established."""
        client, self.__client = self.__client, None
        if client is not None:
            await client.disconnect()

    def _connected_client(self) -> BleakClient:
        if self.__client is None:
            raise HomeAssistantError("Lumalou transport is not connected")
        return self.__client

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        """Permit the factory-token read, never DFU or arbitrary reads."""
        uuid = _allowed_uuid(characteristic, _FACTORY_CHARACTERISTICS)
        return await self._connected_client().read_gatt_char(uuid)

    async def start_notify(
        self,
        characteristic: str,
        callback: Callable[[BleakGATTCharacteristic, bytearray], None],
    ) -> None:
        """Permit RX subscription without adding RX to the write allowlist."""
        uuid = _allowed_uuid(characteristic, _RX_CHARACTERISTICS)
        await self._connected_client().start_notify(uuid, callback)

    async def write_gatt_char(
        self, characteristic: str, data: bytes, *, response: bool
    ) -> None:
        """Fail closed before backend I/O unless this is SESSION or TX."""
        uuid = _allowed_uuid(characteristic, WRITE_CHARACTERISTICS)
        await self._connected_client().write_gatt_char(uuid, data, response=response)


class SafeLumalouClient(LumalouClient):
    """Upstream client bound to one verified device key and the HA transport.

    Only public upstream hooks are used: ``client_factory`` supplies the
    restricted HA transport and ``send``/``request`` are narrowed to explicit
    opcode allowlists before any upstream I/O.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device: BLEDevice,
        *,
        expected_device_fingerprint: str | None,
        on_state: Callable[[dict], None] | None = None,
        disconnected_callback: Callable[[LumalouClient], None] | None = None,
    ) -> None:
        if not expected_device_fingerprint:
            raise HomeAssistantError(
                "A verified Lumalou device identity is required before connecting"
            )
        super().__init__(
            device,
            on_state=on_state,
            client_factory=partial(RestrictedLumalouTransport, hass),
            disconnected_callback=disconnected_callback,
            expected_device_fingerprint=expected_device_fingerprint,
        )

    async def send(
        self,
        app_data: bytes,
        timeout: float = 10.0,  # noqa: ASYNC109
    ) -> None:
        """Reject every unapproved application operation before any I/O."""
        _require_opcode(app_data, ALLOWED_SEND_OPCODES)
        await super().send(app_data, timeout)

    async def request(
        self,
        app_data: bytes,
        expected_opcode: int,
        timeout: float = 3.0,  # noqa: ASYNC109
    ) -> ResponseEnvelope:
        """Permit only the read-only queries used by strict readback."""
        _require_opcode(app_data, ALLOWED_REQUEST_OPCODES)
        return await super().request(app_data, expected_opcode, timeout)


async def async_read_device_fingerprint(hass: HomeAssistant, device: BLEDevice) -> str:
    """Read only the FACTORY token and return its authenticated key fingerprint.

    Nothing is written to the device. The token never leaves this function;
    the library raises ``InvalidFactoryTokenError`` (a ``ValueError``) when it
    cannot authenticate it. Any other error means the read itself failed.

    The connect is not wrapped in an outer deadline: ``establish_connection``
    bounds and retries its own attempts. Only the read on the established
    link is bounded here.
    """
    transport = RestrictedLumalouTransport(hass, device)
    try:
        await transport.connect()
        async with asyncio.timeout(GATT_TIMEOUT):
            token = bytes(await transport.read_gatt_char(FACTORY))
    finally:
        with suppress(Exception):
            await transport.disconnect()
    return parse_factory_device_fingerprint(token)
