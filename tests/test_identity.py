"""Read-only standard GATT identity probe tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice

from custom_components.lumalou.identity import (
    FIRMWARE_REVISION_UUID,
    HARDWARE_REVISION_UUID,
    MANUFACTURER_NAME_UUID,
    MODEL_NUMBER_UUID,
    async_read_device_information,
)


def _device() -> BLEDevice:
    return BLEDevice("synthetic-device", "Lumalou test", {})


def _client(
    values: dict[str, bytes | BaseException],
    *,
    unreadable: set[str] | None = None,
) -> SimpleNamespace:
    unreadable = unreadable or set()
    characteristics = {
        uuid: SimpleNamespace(
            uuid=uuid,
            properties=[] if uuid in unreadable else ["read"],
        )
        for uuid in values
    }

    async def read(characteristic: SimpleNamespace) -> bytes:
        value = values[characteristic.uuid]
        if isinstance(value, BaseException):
            raise value
        return value

    return SimpleNamespace(
        services=SimpleNamespace(
            get_characteristic=lambda uuid: characteristics.get(uuid)
        ),
        read_gatt_char=AsyncMock(side_effect=read),
        write_gatt_char=AsyncMock(),
        start_notify=AsyncMock(),
        disconnect=AsyncMock(),
    )


async def test_reads_only_standard_printable_identity_fields() -> None:
    """The probe never writes, subscribes, pairs, or exposes malformed text."""
    client = _client(
        {
            MODEL_NUMBER_UUID: b" GLD09\x00",
            FIRMWARE_REVISION_UUID: b"1.2.3",
            HARDWARE_REVISION_UUID: b"rev-a",
            MANUFACTURER_NAME_UUID: b"invalid\x00middle",
        },
        unreadable={HARDWARE_REVISION_UUID},
    )
    with patch(
        "custom_components.lumalou.identity.establish_connection",
        new_callable=AsyncMock,
        return_value=client,
    ) as connect:
        result = await async_read_device_information(_device(), "Identity probe")

    assert result.model_number == "GLD09"
    assert result.firmware_revision == "1.2.3"
    assert result.hardware_revision is None
    assert result.manufacturer_name is None
    assert [call.args[0].uuid for call in client.read_gatt_char.await_args_list] == [
        MODEL_NUMBER_UUID,
        FIRMWARE_REVISION_UUID,
        MANUFACTURER_NAME_UUID,
    ]
    client.write_gatt_char.assert_not_called()
    client.start_notify.assert_not_called()
    client.disconnect.assert_awaited_once()
    assert connect.await_args.kwargs["use_services_cache"] is False
    assert connect.await_args.kwargs.get("pair", False) is False


async def test_characteristic_error_keeps_other_readable_fields() -> None:
    """One optional characteristic failure does not turn into model guessing."""
    client = _client(
        {
            MODEL_NUMBER_UUID: RuntimeError("synthetic read error"),
            FIRMWARE_REVISION_UUID: b"1.2.3",
        }
    )
    with patch(
        "custom_components.lumalou.identity.establish_connection",
        new_callable=AsyncMock,
        return_value=client,
    ):
        result = await async_read_device_information(_device(), "Identity probe")

    assert result.model_number is None
    assert result.firmware_revision == "1.2.3"
    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()


async def test_cancellation_disconnects_and_propagates() -> None:
    """Cancellation releases the connection and is never reported as no model."""
    client = _client({MODEL_NUMBER_UUID: asyncio.CancelledError()})
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await async_read_device_information(_device(), "Identity probe")

    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()


async def test_cancellation_during_disconnect_waits_for_bounded_cleanup() -> None:
    """Caller cancellation cannot abandon a connection during teardown."""
    disconnect_started = asyncio.Event()
    release_disconnect = asyncio.Event()
    disconnect_completed = asyncio.Event()
    client = _client({})

    async def disconnect() -> None:
        disconnect_started.set()
        await release_disconnect.wait()
        disconnect_completed.set()

    client.disconnect.side_effect = disconnect
    with patch(
        "custom_components.lumalou.identity.establish_connection",
        new_callable=AsyncMock,
        return_value=client,
    ):
        probe = asyncio.create_task(
            async_read_device_information(_device(), "Identity probe")
        )
        await disconnect_started.wait()
        probe.cancel()
        release_disconnect.set()
        with pytest.raises(asyncio.CancelledError):
            await probe

    assert disconnect_completed.is_set()
    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()


async def test_connect_failure_does_not_construct_an_untracked_client() -> None:
    """A connector failure propagates without any fallback scanner or client."""
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            side_effect=RuntimeError("synthetic connect failure"),
        ),
        pytest.raises(RuntimeError, match="synthetic connect failure"),
    ):
        await async_read_device_information(_device(), "Identity probe")


async def test_read_timeout_disconnects_and_fails_closed() -> None:
    """A stalled standard read is bounded and still releases the connection."""
    never = asyncio.Event()
    client = _client({MODEL_NUMBER_UUID: b"GLD09"})

    async def read_never(_characteristic: SimpleNamespace) -> bytes:
        await never.wait()
        return b"GLD09"

    client.read_gatt_char.side_effect = read_never
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch("custom_components.lumalou.identity.CONNECT_TIMEOUT", 0.01),
        pytest.raises(TimeoutError),
    ):
        await async_read_device_information(_device(), "Identity probe")

    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()


async def test_disconnect_timeout_is_bounded() -> None:
    """A stuck backend disconnect cannot hold the config flow forever."""
    never = asyncio.Event()
    client = _client({})

    async def disconnect_never() -> None:
        await never.wait()

    client.disconnect.side_effect = disconnect_never
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch("custom_components.lumalou.identity.CONNECT_TIMEOUT", 0.01),
    ):
        result = await async_read_device_information(_device(), "Identity probe")

    assert result.model_number is None
    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()
