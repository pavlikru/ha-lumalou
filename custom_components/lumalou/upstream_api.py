"""Compatibility shim for callers that predate the exact 0.2.0 dependency pin.

The manifest pins ``lumalou-gld09==0.2.0``, whose public API includes the
signed FACTORY verifier and per-session identity binding, so runtime
capability probing is no longer needed. Remove this module once
``config_flow`` and ``identity`` import ``parse_factory_device_fingerprint``
directly.
"""

from __future__ import annotations

from collections.abc import Callable

from lumalou.factory import parse_factory_device_fingerprint


class MissingUpstreamCapabilities(RuntimeError):
    """Kept only so existing callers' error handling stays importable."""


def require_factory_identity_api() -> Callable[[bytes], str]:
    """Return the pinned upstream signed device-key fingerprint parser."""
    return parse_factory_device_fingerprint
