"""Read-only standard GATT identity probe tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.backends.device import BLEDevice

from custom_components.lumalou.identity import (
    FACTORY_CHARACTERISTIC_UUID,
    FIRMWARE_REVISION_UUID,
    HARDWARE_REVISION_UUID,
    MANUFACTURER_NAME_UUID,
    MODEL_NUMBER_UUID,
    FactoryIdentityProbeError,
    async_read_device_information,
    async_read_factory_device_fingerprint,
)


def _device() -> BLEDevice:
    return BLEDevice("synthetic-device", "Lumalou test", {})


@pytest.fixture(autouse=True)
def identity_api_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests use a synthetic verifier instead of real signed tokens."""
    monkeypatch.setattr(
        "custom_components.lumalou.identity.parse_factory_device_fingerprint",
        lambda _token: "a" * 64,
    )


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


async def test_factory_probe_returns_verified_fingerprint_without_writes() -> None:
    """FACTORY is read once; upstream verifier returns a key fingerprint."""
    token = b"synthetic-token"
    client = _client({FACTORY_CHARACTERISTIC_UUID: token})
    fingerprint = "b" * 64
    parser = Mock(return_value=fingerprint)
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
    ):
        item_fingerprint = await async_read_factory_device_fingerprint(
            _device(), "Factory probe", parser=parser
        )

    assert item_fingerprint == fingerprint
    parser.assert_called_once_with(token)
    client.read_gatt_char.assert_awaited_once()
    client.write_gatt_char.assert_not_called()
    client.start_notify.assert_not_called()
    client.disconnect.assert_awaited_once()


async def test_factory_probe_rejects_unverifiable_token_without_disclosure() -> None:
    """Verification errors become a generic failure without token disclosure."""
    token = b"synthetic-private-token"
    client = _client({FACTORY_CHARACTERISTIC_UUID: token})
    parser = Mock(side_effect=ValueError("signature rejected"))
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        pytest.raises(FactoryIdentityProbeError) as exc_info,
    ):
        await async_read_factory_device_fingerprint(
            _device(), "Factory probe", parser=parser
        )

    assert token.decode() not in str(exc_info.value)
    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()
    client.start_notify.assert_not_called()


@pytest.mark.parametrize("fingerprint", ["f" * 63, "F" * 64, "g" * 64, "serial-123"])
async def test_factory_probe_rejects_invalid_fingerprint_without_disclosure(
    fingerprint: str,
) -> None:
    """Only lowercase 64-hex fingerprints can leave the authenticated probe."""
    token = b"synthetic-private-token"
    client = _client({FACTORY_CHARACTERISTIC_UUID: token})
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        pytest.raises(FactoryIdentityProbeError) as exc_info,
    ):
        await async_read_factory_device_fingerprint(
            _device(), "Factory probe", parser=Mock(return_value=fingerprint)
        )

    assert token.decode() not in str(exc_info.value)
    assert fingerprint not in str(exc_info.value)
    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()
    client.start_notify.assert_not_called()


async def test_factory_probe_timeout_disconnects_and_fails_closed() -> None:
    """A stalled FACTORY read is bounded and does not leave BLE connected."""
    never = asyncio.Event()
    client = _client({FACTORY_CHARACTERISTIC_UUID: b"synthetic-token"})

    async def read_never(_characteristic: SimpleNamespace) -> bytes:
        await never.wait()
        return b"synthetic-token"

    client.read_gatt_char.side_effect = read_never
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch("custom_components.lumalou.identity.CONNECT_TIMEOUT", 0.01),
        pytest.raises(FactoryIdentityProbeError),
    ):
        await async_read_factory_device_fingerprint(
            _device(), "Factory probe", parser=Mock(return_value="c" * 64)
        )

    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()
    client.start_notify.assert_not_called()


async def test_factory_probe_cancellation_disconnects_and_propagates() -> None:
    """Cancellation is never converted into an unidentified-device result."""
    client = _client({FACTORY_CHARACTERISTIC_UUID: asyncio.CancelledError()})
    with (
        patch(
            "custom_components.lumalou.identity.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await async_read_factory_device_fingerprint(
            _device(), "Factory probe", parser=Mock(return_value="d" * 64)
        )

    client.disconnect.assert_awaited_once()
    client.write_gatt_char.assert_not_called()
    client.start_notify.assert_not_called()
