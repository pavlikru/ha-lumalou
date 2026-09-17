"""Narrow GATT boundary for the pinned upstream client; no protocol handling."""

from collections.abc import Callable
from uuid import UUID

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from homeassistant.exceptions import HomeAssistantError

from .const import WRITE_CHARACTERISTICS

_FACTORY_CHARACTERISTICS = frozenset({"4cea0004-c678-4202-b5d3-712dbb5e5b14"})
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


class RestrictedLumalouTransport:
    """Expose only the five transport operations used by lumalou==0.1.0.

    Do not forward arbitrary attributes: descriptor writes and other GATT APIs
    must remain unavailable. Read and notify permissions do not grant writes.
    The upstream client still owns all handshake, encryption and frame logic.
    """

    def __init__(self, client: BleakClient) -> None:
        self.__client = client

    async def connect(self) -> None:
        """Connect using the HA-provided BLEDevice, without discovery."""
        await self.__client.connect()

    async def disconnect(self) -> None:
        """Release the underlying connection."""
        await self.__client.disconnect()

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        """Permit the factory-token read, never DFU or arbitrary reads."""
        uuid = _allowed_uuid(characteristic, _FACTORY_CHARACTERISTICS)
        return await self.__client.read_gatt_char(uuid)

    async def start_notify(
        self,
        characteristic: str,
        callback: Callable[[BleakGATTCharacteristic, bytearray], None],
    ) -> None:
        """Permit RX subscription without adding RX to the write allowlist."""
        uuid = _allowed_uuid(characteristic, _RX_CHARACTERISTICS)
        await self.__client.start_notify(uuid, callback)

    async def write_gatt_char(
        self, characteristic: str, data: bytes, *, response: bool
    ) -> None:
        """Fail closed before backend I/O unless this is SESSION or TX."""
        uuid = _allowed_uuid(characteristic, WRITE_CHARACTERISTICS)
        await self.__client.write_gatt_char(uuid, data, response=response)
