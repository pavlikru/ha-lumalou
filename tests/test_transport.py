"""Runtime GATT policy tests, with every backend operation mocked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import UUID

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.exceptions import HomeAssistantError
from lumalou import crypto
from lumalou.client import FACTORY, RX, SESSION, TX

from custom_components.lumalou.const import DFU_SERVICE, WRITE_CHARACTERISTICS
from custom_components.lumalou.coordinator import SafeLumalouClient
from custom_components.lumalou.transport import RestrictedLumalouTransport


@pytest.fixture
def backend():
    """No real Bleak client or device is constructed by transport tests."""
    return SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        read_gatt_char=AsyncMock(return_value=bytearray(b"factory token")),
        start_notify=AsyncMock(),
        write_gatt_char=AsyncMock(),
        write_gatt_descriptor=AsyncMock(),
    )


@pytest.mark.parametrize("characteristic", sorted(WRITE_CHARACTERISTICS))
@pytest.mark.parametrize("response", [False, True])
@pytest.mark.parametrize("notation", [str, str.upper, lambda uuid: UUID(uuid).hex])
async def test_only_allowed_uuid_writes_reach_backend(
    backend, characteristic, response, notation
):
    transport = RestrictedLumalouTransport(backend)
    payload = b"opaque upstream payload"

    await transport.write_gatt_char(
        notation(characteristic), payload, response=response
    )

    backend.write_gatt_char.assert_awaited_once_with(
        characteristic, payload, response=response
    )


@pytest.mark.parametrize(
    "characteristic",
    [
        DFU_SERVICE,
        "00001531-1212-efde-1523-785feabcd123",  # Nordic DFU control point
        "00001532-1212-efde-1523-785feabcd123",  # Nordic DFU packet
        FACTORY,
        RX,
        "4cea0001-c678-4202-b5d3-712dbb5e5b14",  # main service, not a char
        "00002a00-0000-1000-8000-00805f9b34fb",
        "invalid",
        "",
        None,
        5,
        UUID(SESSION),
        SimpleNamespace(uuid=SESSION),
    ],
)
async def test_forbidden_or_ambiguous_writes_fail_before_any_backend_io(
    backend, characteristic
):
    transport = RestrictedLumalouTransport(backend)

    with pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"):
        await transport.write_gatt_char(characteristic, b"payload", response=True)

    for method in vars(backend).values():
        method.assert_not_called()


async def test_factory_reads_and_rx_notifications_do_not_grant_write_access(backend):
    transport = RestrictedLumalouTransport(backend)
    callback = Mock()

    assert await transport.read_gatt_char(FACTORY.upper()) == bytearray(
        b"factory token"
    )
    await transport.start_notify(RX.upper(), callback)

    backend.read_gatt_char.assert_awaited_once_with(FACTORY)
    backend.start_notify.assert_awaited_once_with(RX, callback)
    backend.write_gatt_char.assert_not_called()


@pytest.mark.parametrize("characteristic", [DFU_SERVICE, TX, SESSION, "invalid", 5])
@pytest.mark.parametrize("operation", ["read_gatt_char", "start_notify"])
async def test_read_and_notify_permissions_are_separate_and_fail_closed(
    backend, characteristic, operation
):
    transport = RestrictedLumalouTransport(backend)
    arguments = (
        (characteristic, Mock()) if operation == "start_notify" else (characteristic,)
    )

    with pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"):
        await getattr(transport, operation)(*arguments)

    for method in vars(backend).values():
        method.assert_not_called()


async def test_unapproved_gatt_apis_are_not_forwarded(backend):
    transport = RestrictedLumalouTransport(backend)

    with pytest.raises(AttributeError):
        await transport.write_gatt_descriptor(5, b"payload")

    backend.write_gatt_descriptor.assert_not_called()


async def test_upstream_handshake_send_and_reconnect_always_use_guarded_transport(
    backend,
):
    """Execute the real pinned upstream crypto/handshake and command writer."""
    _, public_key = crypto.generate_keypair()
    backend.read_gatt_char.return_value = bytes(25) + public_key + bytes(4)
    device = BLEDevice("synthetic-device", "Test", {})
    client = SafeLumalouClient(device, lambda state: None)
    assert client._client is None

    with patch("lumalou.client.BleakClient", return_value=backend) as factory:
        for _ in range(2):
            await client.connect()
            assert isinstance(client._client, RestrictedLumalouTransport)
            assert client.connected
            await client.send(bytes([0x37, 1]))
            await client.disconnect()
            assert not client.connected

    assert factory.call_args_list == [call(device), call(device)]
    assert backend.connect.await_count == 2
    assert backend.disconnect.await_count == 2
    assert backend.read_gatt_char.await_args_list == [call(FACTORY), call(FACTORY)]
    assert [c.args[0] for c in backend.start_notify.await_args_list] == [RX, RX]
    writes = backend.write_gatt_char.await_args_list
    assert [c.args[0] for c in writes] == [SESSION, TX, TX, SESSION, TX, TX]
    assert [c.kwargs["response"] for c in writes] == [True, False, False] * 2
    assert len(writes[0].args[1]) == 37


@pytest.mark.parametrize("upstream_target", ["SESSION", "TX"])
async def test_changed_upstream_handshake_target_is_blocked(backend, upstream_target):
    """The policy guards upstream writes, not only direct proxy calls."""
    _, public_key = crypto.generate_keypair()
    backend.read_gatt_char.return_value = bytes(25) + public_key + bytes(4)
    client = SafeLumalouClient(
        BLEDevice("synthetic-device", "Test", {}), lambda state: None
    )

    with (
        patch("lumalou.client.BleakClient", return_value=backend),
        patch(f"lumalou.client.{upstream_target}", DFU_SERVICE),
        pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"),
    ):
        await client.connect()

    assert not client.connected
    written = [c.args[0] for c in backend.write_gatt_char.await_args_list]
    assert written == ([] if upstream_target == "SESSION" else [SESSION])
    await client.disconnect()
    backend.disconnect.assert_awaited_once()


async def test_changed_upstream_command_target_is_blocked_before_write(backend):
    client = SafeLumalouClient(
        BLEDevice("synthetic-device", "Test", {}), lambda state: None
    )
    client._client = backend

    with (
        patch("lumalou.client.P.build_tx_frame", return_value=b"opaque frame"),
        patch("lumalou.client.TX", FACTORY),
        pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"),
    ):
        await client.send(bytes([0x37, 1]))

    backend.write_gatt_char.assert_not_called()
