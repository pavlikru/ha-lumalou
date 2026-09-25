"""Runtime GATT/opcode policy tests, with every backend operation mocked."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import UUID

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_SAFETY_TIMEOUT,
    MAX_CONNECT_ATTEMPTS,
    BleakClientWithServiceCache,
)
from homeassistant.exceptions import HomeAssistantError
from lumalou import crypto
from lumalou.client import FACTORY, RX, SESSION, TX

from custom_components.lumalou.const import (
    ALLOWED_REQUEST_OPCODES,
    ALLOWED_SEND_OPCODES,
    CONNECT_TIMEOUT,
    FORBIDDEN_OPCODES,
    GATT_TIMEOUT,
    WRITE_CHARACTERISTICS,
)
from custom_components.lumalou.transport import (
    RestrictedLumalouTransport,
    SafeLumalouClient,
    async_read_device_fingerprint,
)

FINGERPRINT = "a" * 64
DFU_SERVICE = "00001530-1212-efde-1523-785feabcd123"  # Nordic DFU; never allowed
DEVICE = BLEDevice("synthetic-device", "Test", {})
HASS = SimpleNamespace()


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


@pytest.fixture
def ha_bluetooth(backend):
    """Patch HA's device lookup and the standard connector, never real BLE."""
    with (
        patch(
            "custom_components.lumalou.transport.bluetooth.async_ble_device_from_address",
            return_value=DEVICE,
        ) as lookup,
        patch(
            "custom_components.lumalou.transport.establish_connection",
            new=AsyncMock(return_value=backend),
        ) as establish,
        patch("lumalou.client.BleakScanner.discover", new=AsyncMock()) as scan,
        patch("lumalou.client.BleakClient") as raw_bleak,
    ):
        yield SimpleNamespace(
            lookup=lookup, establish=establish, scan=scan, raw_bleak=raw_bleak
        )


async def _connected_transport(ha_bluetooth) -> RestrictedLumalouTransport:
    transport = RestrictedLumalouTransport(HASS, DEVICE)
    await transport.connect()
    return transport


@pytest.mark.parametrize("characteristic", sorted(WRITE_CHARACTERISTICS))
@pytest.mark.parametrize("response", [False, True])
@pytest.mark.parametrize("notation", [str, str.upper, lambda uuid: UUID(uuid).hex])
async def test_only_allowed_uuid_writes_reach_backend(
    backend, ha_bluetooth, characteristic, response, notation
):
    transport = await _connected_transport(ha_bluetooth)
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
    backend, ha_bluetooth, characteristic
):
    transport = await _connected_transport(ha_bluetooth)

    with pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"):
        await transport.write_gatt_char(characteristic, b"payload", response=True)

    for method in vars(backend).values():
        method.assert_not_called()


async def test_factory_reads_and_rx_notifications_do_not_grant_write_access(
    backend, ha_bluetooth
):
    transport = await _connected_transport(ha_bluetooth)
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
    backend, ha_bluetooth, characteristic, operation
):
    transport = await _connected_transport(ha_bluetooth)
    arguments = (
        (characteristic, Mock()) if operation == "start_notify" else (characteristic,)
    )

    with pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"):
        await getattr(transport, operation)(*arguments)

    for method in vars(backend).values():
        method.assert_not_called()


async def test_unapproved_gatt_apis_are_not_forwarded(backend, ha_bluetooth):
    transport = await _connected_transport(ha_bluetooth)

    with pytest.raises(AttributeError):
        await transport.write_gatt_descriptor(5, b"payload")

    backend.write_gatt_descriptor.assert_not_called()


async def test_adapter_connects_current_ha_device_with_standard_connector(
    backend, ha_bluetooth
):
    """The adapter replaces the old private-attribute override of upstream."""
    current = BLEDevice("synthetic-device", "Current", {})
    ha_bluetooth.lookup.return_value = current
    lost = Mock()
    transport = RestrictedLumalouTransport(HASS, DEVICE, disconnected_callback=lost)

    await transport.connect()

    ha_bluetooth.lookup.assert_called_with(HASS, DEVICE.address, connectable=True)
    establish = ha_bluetooth.establish.await_args
    assert establish.args == (BleakClientWithServiceCache, current, "Current")
    assert establish.kwargs["disconnected_callback"] is lost
    ha_bluetooth.lookup.return_value = None
    assert establish.kwargs["ble_device_callback"]() is current
    ha_bluetooth.scan.assert_not_awaited()
    ha_bluetooth.raw_bleak.assert_not_called()

    await transport.disconnect()
    await transport.disconnect()
    backend.disconnect.assert_awaited_once_with()


async def test_adapter_fails_closed_without_connectable_device(backend, ha_bluetooth):
    ha_bluetooth.lookup.return_value = None
    transport = RestrictedLumalouTransport(HASS, DEVICE)

    with pytest.raises(HomeAssistantError, match="No connectable"):
        await transport.connect()
    with pytest.raises(HomeAssistantError, match="not connected"):
        await transport.read_gatt_char(FACTORY)

    ha_bluetooth.establish.assert_not_awaited()
    await transport.disconnect()
    backend.disconnect.assert_not_awaited()


def test_adapter_requires_ha_ble_device():
    with pytest.raises(HomeAssistantError, match="BLEDevice"):
        RestrictedLumalouTransport(HASS, "synthetic-address")  # type: ignore[arg-type]


def test_safe_client_without_expected_device_is_rejected():
    """Every HA-owned control session must be pinned to a verified device key."""
    for missing in (None, ""):
        with pytest.raises(HomeAssistantError, match="device identity is required"):
            SafeLumalouClient(HASS, DEVICE, expected_device_fingerprint=missing)


async def test_safe_client_denies_every_unapproved_opcode_before_io():
    client = SafeLumalouClient(HASS, DEVICE, expected_device_fingerprint=FINGERPRINT)
    assert client.address is DEVICE
    with (
        patch("lumalou.client.LumalouClient.send", new=AsyncMock()) as send,
        patch("lumalou.client.LumalouClient.request", new=AsyncMock()) as request,
    ):
        for opcode in set(range(256)) - ALLOWED_SEND_OPCODES:
            with pytest.raises(HomeAssistantError, match="Unsupported"):
                await client.send(bytes([opcode]))
        for opcode in set(range(256)) - ALLOWED_REQUEST_OPCODES:
            with pytest.raises(HomeAssistantError, match="Unsupported"):
                await client.request(bytes([opcode]), 0x02)
        for invalid in (b"", "7", None):
            with pytest.raises(HomeAssistantError):
                await client.send(invalid)  # type: ignore[arg-type]
        send.assert_not_awaited()
        request.assert_not_awaited()

        await client.send(bytes([0x37, 1]), 2.0)
        await client.request(bytes([0x53]), 0x02, 1.0)

    send.assert_awaited_once_with(bytes([0x37, 1]), 2.0)
    request.assert_awaited_once_with(bytes([0x53]), 0x02, 1.0)
    assert not (ALLOWED_SEND_OPCODES | ALLOWED_REQUEST_OPCODES) & FORBIDDEN_OPCODES


def _token() -> bytes:
    _, public_key = crypto.generate_keypair()
    return bytes(25) + public_key + bytes(134)


async def test_upstream_handshake_send_and_reconnect_always_use_guarded_transport(
    backend, ha_bluetooth
):
    """Execute the real pinned upstream handshake through the HA adapter."""
    backend.read_gatt_char.return_value = _token()
    lost = Mock()
    client = SafeLumalouClient(
        HASS,
        DEVICE,
        expected_device_fingerprint=FINGERPRINT,
        disconnected_callback=lost,
    )

    with patch(
        "lumalou.client.parse_factory_device_fingerprint", return_value=FINGERPRINT
    ):
        for _ in range(2):
            await client.connect(timeout=5)
            assert client.connected
            assert client.device_fingerprint == FINGERPRINT
            await client.send(bytes([0x37, 1]))
            await client.disconnect()
            assert not client.connected

    ha_bluetooth.raw_bleak.assert_not_called()
    ha_bluetooth.scan.assert_not_awaited()
    assert ha_bluetooth.establish.await_count == 2
    for establish in ha_bluetooth.establish.await_args_list:
        assert establish.args[:2] == (BleakClientWithServiceCache, DEVICE)
        assert callable(establish.kwargs["disconnected_callback"])
    assert backend.disconnect.await_count == 2
    assert backend.read_gatt_char.await_args_list == [call(FACTORY), call(FACTORY)]
    assert [c.args[0] for c in backend.start_notify.await_args_list] == [RX, RX]
    writes = backend.write_gatt_char.await_args_list
    assert [c.args[0] for c in writes] == [SESSION, TX, TX, SESSION, TX, TX]
    assert [c.kwargs["response"] for c in writes] == [True, False, False] * 2
    assert len(writes[0].args[1]) == 37
    assert lost.call_count == 2  # upstream reports each ended session


async def test_wrong_device_key_is_rejected_before_session_writes(
    backend, ha_bluetooth
):
    backend.read_gatt_char.return_value = _token()
    client = SafeLumalouClient(HASS, DEVICE, expected_device_fingerprint=FINGERPRINT)

    with (
        patch("lumalou.client.parse_factory_device_fingerprint", return_value="b" * 64),
        pytest.raises(Exception, match="does not match"),
    ):
        await client.connect(timeout=5)

    assert not client.connected
    backend.start_notify.assert_not_awaited()
    backend.write_gatt_char.assert_not_awaited()
    backend.disconnect.assert_awaited_once()


@pytest.mark.parametrize("upstream_target", ["SESSION", "TX"])
async def test_changed_upstream_handshake_target_is_blocked(
    backend, ha_bluetooth, upstream_target
):
    """The policy guards upstream writes, not only direct adapter calls."""
    backend.read_gatt_char.return_value = _token()
    client = SafeLumalouClient(HASS, DEVICE, expected_device_fingerprint=FINGERPRINT)

    with (
        patch(
            "lumalou.client.parse_factory_device_fingerprint", return_value=FINGERPRINT
        ),
        patch(f"lumalou.client.{upstream_target}", DFU_SERVICE),
        pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"),
    ):
        await client.connect(timeout=5)

    assert not client.connected
    written = [c.args[0] for c in backend.write_gatt_char.await_args_list]
    assert written == ([] if upstream_target == "SESSION" else [SESSION])
    backend.disconnect.assert_awaited_once()


async def test_changed_upstream_command_target_is_blocked_before_write(
    backend, ha_bluetooth
):
    backend.read_gatt_char.return_value = _token()
    client = SafeLumalouClient(HASS, DEVICE, expected_device_fingerprint=FINGERPRINT)
    with patch(
        "lumalou.client.parse_factory_device_fingerprint", return_value=FINGERPRINT
    ):
        await client.connect(timeout=5)
    writes_before = backend.write_gatt_char.await_count

    with (
        patch("lumalou.client.TX", FACTORY),
        pytest.raises(HomeAssistantError, match="Unsupported Lumalou GATT"),
    ):
        await client.send(bytes([0x37, 1]))

    assert backend.write_gatt_char.await_count == writes_before
    assert not client.connected


async def test_fingerprint_probe_reads_only_factory_and_disconnects(
    backend, ha_bluetooth
):
    backend.read_gatt_char.return_value = bytearray(b"synthetic token")
    with patch(
        "custom_components.lumalou.transport.parse_factory_device_fingerprint",
        return_value=FINGERPRINT,
    ) as parse:
        assert await async_read_device_fingerprint(HASS, DEVICE) == FINGERPRINT

    parse.assert_called_once_with(b"synthetic token")
    backend.read_gatt_char.assert_awaited_once_with(FACTORY)
    backend.write_gatt_char.assert_not_awaited()
    backend.start_notify.assert_not_awaited()
    backend.disconnect.assert_awaited_once()


async def test_fingerprint_probe_disconnects_when_the_read_fails(backend, ha_bluetooth):
    backend.read_gatt_char.side_effect = OSError("synthetic")
    backend.disconnect.side_effect = OSError("teardown failure is not reported")

    with pytest.raises(OSError, match="synthetic"):
        await async_read_device_fingerprint(HASS, DEVICE)

    backend.disconnect.assert_awaited_once()


async def test_fingerprint_probe_rejects_an_unauthenticated_token(
    backend, ha_bluetooth
):
    backend.read_gatt_char.return_value = bytearray(b"not a signed token")

    with pytest.raises(ValueError):
        await async_read_device_fingerprint(HASS, DEVICE)

    backend.disconnect.assert_awaited_once()


@contextmanager
def _fake_clock() -> Iterator[Callable[[float], None]]:
    """Let a test jump the running loop's clock so pending deadlines expire."""
    loop = asyncio.get_running_loop()
    real_time = loop.time
    offset = 0.0

    def advance(seconds: float) -> None:
        nonlocal offset
        offset += seconds

    with (
        patch.object(loop, "time", lambda: real_time() + offset),
        # Jumps are not slow callbacks; keep asyncio debug mode quiet.
        patch.object(loop, "slow_callback_duration", float("inf")),
    ):
        yield advance


async def test_fingerprint_probe_lets_establish_connection_run_its_retries(
    backend, ha_bluetooth
):
    """A slow connect is governed by bleak-retry-connector, never cut short."""
    with _fake_clock() as advance:

        async def slow_establish(*args, **kwargs):
            # The whole retry budget elapses; any outer deadline would fire.
            advance(MAX_CONNECT_ATTEMPTS * BLEAK_SAFETY_TIMEOUT)
            await asyncio.sleep(0.001)
            return backend

        ha_bluetooth.establish.side_effect = slow_establish
        with patch(
            "custom_components.lumalou.transport.parse_factory_device_fingerprint",
            return_value=FINGERPRINT,
        ):
            assert await async_read_device_fingerprint(HASS, DEVICE) == FINGERPRINT

    backend.read_gatt_char.assert_awaited_once_with(FACTORY)


async def test_fingerprint_probe_still_bounds_the_factory_read(backend, ha_bluetooth):
    """Only the GATT read on the established link keeps its own deadline."""
    with _fake_clock() as advance:

        async def stalled_read(characteristic):
            advance(GATT_TIMEOUT + 1)
            await asyncio.sleep(0.001)
            raise AssertionError("the read deadline did not fire")

        backend.read_gatt_char.side_effect = stalled_read
        with pytest.raises(TimeoutError):
            await async_read_device_fingerprint(HASS, DEVICE)

    backend.disconnect.assert_awaited_once()


async def test_session_connect_deadline_covers_the_connector_retries(
    backend, ha_bluetooth
):
    """The library's connect deadline outlasts a full retry budget."""
    backend.read_gatt_char.return_value = _token()
    client = SafeLumalouClient(HASS, DEVICE, expected_device_fingerprint=FINGERPRINT)
    with _fake_clock() as advance:

        async def slow_establish(*args, **kwargs):
            advance(MAX_CONNECT_ATTEMPTS * BLEAK_SAFETY_TIMEOUT)
            await asyncio.sleep(0.001)
            return backend

        ha_bluetooth.establish.side_effect = slow_establish
        with patch(
            "lumalou.client.parse_factory_device_fingerprint", return_value=FINGERPRINT
        ):
            await client.connect(timeout=CONNECT_TIMEOUT)
            assert client.connected
            await client.disconnect()


def test_connect_timeout_covers_the_connector_retry_budget():
    """The session deadline exceeds every retry plus the handshake."""
    assert CONNECT_TIMEOUT > MAX_CONNECT_ATTEMPTS * BLEAK_SAFETY_TIMEOUT + GATT_TIMEOUT
