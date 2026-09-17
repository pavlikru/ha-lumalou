"""Host compatibility only; tests never create real USB or BLE scanners."""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from typing import TypedDict

# HA's Linux USB watcher is not installed by its platform markers on macOS.
# Keep real packages on supported hosts; fail closed if a test tries to use it.
if sys.platform == "darwin" and importlib.util.find_spec("aiousbwatcher") is None:
    watcher_stub = ModuleType("aiousbwatcher")

    class UnavailableWatcher:
        """Import stand-in that cannot accidentally interact with hardware."""

        def __init__(self, *args, **kwargs):
            raise AssertionError("USB hardware access is forbidden in unit tests")

    watcher_stub.AIOUSBWatcher = UnavailableWatcher
    watcher_stub.InotifyNotAvailableError = RuntimeError
    sys.modules["aiousbwatcher"] = watcher_stub

if sys.platform == "darwin" and importlib.util.find_spec("serialx") is None:
    serial_stub = ModuleType("serialx")
    serial_common_stub = ModuleType("serialx.common")

    class UnavailableSerial:
        """Permit HA class declarations but prevent hardware instantiation."""

        def __init__(self, *args, **kwargs):
            raise AssertionError("Serial hardware access is forbidden in unit tests")

    class ConnectKwargs(TypedDict):
        """Annotation stand-in only; serial transport is never started."""

    def no_serial_access(*args, **kwargs):
        """Fail rather than silently simulate real serial discovery."""
        raise AssertionError("Serial hardware access is forbidden in unit tests")

    serial_stub.BaseSerial = UnavailableSerial
    serial_stub.BaseSerialTransport = UnavailableSerial
    serial_stub.ModemPins = UnavailableSerial
    serial_stub.SerialPortInfo = UnavailableSerial
    serial_stub.register_uri_handler = no_serial_access
    serial_stub.list_serial_ports = no_serial_access
    serial_common_stub.ConnectKwargs = ConnectKwargs
    sys.modules["serialx"] = serial_stub
    sys.modules["serialx.common"] = serial_common_stub
