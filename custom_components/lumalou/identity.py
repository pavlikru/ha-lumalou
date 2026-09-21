"""Read-only standard GATT identity probe for config flows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .const import CONNECT_TIMEOUT

MODEL_NUMBER_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
FIRMWARE_REVISION_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
MANUFACTURER_NAME_UUID = "00002a29-0000-1000-8000-00805f9b34fb"

_TEXT_CHARACTERISTICS = {
    "model_number": MODEL_NUMBER_UUID,
    "firmware_revision": FIRMWARE_REVISION_UUID,
    "hardware_revision": HARDWARE_REVISION_UUID,
    "manufacturer_name": MANUFACTURER_NAME_UUID,
}
_MAX_GATT_TEXT_BYTES = 64


@dataclass(frozen=True, slots=True)
class DeviceInformation:
    """Non-secret fields from the standard Device Information service."""

    model_number: str | None = None
    firmware_revision: str | None = None
    hardware_revision: str | None = None
    manufacturer_name: str | None = None


def _decode_gatt_text(raw: bytes | bytearray) -> str | None:
    """Decode a small printable UTF-8 Device Information value."""
    value = bytes(raw)
    if not value or len(value) > _MAX_GATT_TEXT_BYTES:
        return None
    try:
        decoded = value.decode("utf-8").strip(" \x00")
    except UnicodeDecodeError:
        return None
    if not decoded or not decoded.isprintable() or "\x00" in decoded:
        return None
    return decoded


async def _async_disconnect(client: BleakClient) -> None:
    """Best-effort disconnect with a bounded teardown time."""
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            await client.disconnect()
    except Exception:
        pass


async def async_read_device_information(
    device: BLEDevice, name: str
) -> DeviceInformation:
    """Read standard identity fields without protocol writes or notifications."""
    client: BleakClient | None = None
    values: dict[str, str | None] = dict.fromkeys(_TEXT_CHARACTERISTICS)
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            client = await establish_connection(
                BleakClient,
                device,
                name,
                max_attempts=2,
                use_services_cache=False,
                timeout=CONNECT_TIMEOUT,
            )
            for field, uuid in _TEXT_CHARACTERISTICS.items():
                characteristic = client.services.get_characteristic(uuid)
                if characteristic is None or "read" not in characteristic.properties:
                    continue
                try:
                    raw = await client.read_gatt_char(characteristic)
                except Exception:
                    continue
                values[field] = _decode_gatt_text(raw)
        return DeviceInformation(**values)
    finally:
        if client is not None:
            cleanup = asyncio.create_task(_async_disconnect(client))
            cancelled = False
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    if cleanup.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
