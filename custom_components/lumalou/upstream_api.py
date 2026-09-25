"""Explicit capability contract for the pinned upstream Python library."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from inspect import Parameter, signature
from typing import Any


class MissingUpstreamCapabilities(RuntimeError):
    """The installed upstream package does not provide a required safe API."""

    def __init__(self, operation: str, missing: tuple[str, ...]) -> None:
        self.operation = operation
        self.missing = missing
        message = f"Lumalou library cannot {operation}; missing: {', '.join(missing)}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class UpstreamCapabilities:
    """Only public APIs required for authenticated control and full reads."""

    device_fingerprint_parser: Callable[[bytes], str] | None
    expected_device_binding: bool
    request_state: bool
    request_named: bool
    request_day_routine: bool
    typed_response_decoder: bool

    @property
    def identity_ready(self) -> bool:
        """Whether the package can authenticate and pin each BLE session."""
        return (
            self.device_fingerprint_parser is not None and self.expected_device_binding
        )

    @property
    def full_profile_read_ready(self) -> bool:
        """Whether all request/decoder APIs used by strict profile read exist."""
        return (
            self.identity_ready
            and self.request_state
            and self.request_named
            and self.request_day_routine
            and self.typed_response_decoder
        )


def inspect_upstream_capabilities(
    package_module: Any,
    client_module: Any,
) -> UpstreamCapabilities:
    """Inspect explicitly named APIs; a catch-all ``**kwargs`` is insufficient."""
    parser = getattr(package_module, "parse_factory_device_fingerprint", None)
    client_class = getattr(client_module, "LumalouClient", None)
    response_class = getattr(client_module, "ResponseEnvelope", None)

    expected_device_binding = False
    if client_class is not None:
        try:
            parameter = signature(client_class).parameters.get(
                "expected_device_fingerprint"
            )
        except TypeError, ValueError:
            parameter = None
        expected_device_binding = parameter is not None and parameter.kind in (
            Parameter.POSITIONAL_OR_KEYWORD,
            Parameter.KEYWORD_ONLY,
        )

    return UpstreamCapabilities(
        device_fingerprint_parser=parser if callable(parser) else None,
        expected_device_binding=expected_device_binding,
        request_state=callable(getattr(client_class, "request_state", None)),
        request_named=callable(getattr(client_class, "request_named", None)),
        request_day_routine=callable(
            getattr(client_class, "request_day_routine", None)
        ),
        typed_response_decoder=callable(getattr(response_class, "decode", None)),
    )


def get_upstream_capabilities() -> UpstreamCapabilities:
    """Inspect the installed release without connecting to a Lumalou device."""
    try:
        package_module = import_module("lumalou")
        client_module = import_module("lumalou.client")
    except ImportError:
        return inspect_upstream_capabilities(None, None)
    return inspect_upstream_capabilities(package_module, client_module)


def require_factory_identity_api() -> Callable[[bytes], str]:
    """Require both the signature verifier and explicit session identity pin."""
    capabilities = get_upstream_capabilities()
    missing = tuple(
        name
        for name, available in (
            (
                "signed device-key verifier",
                capabilities.device_fingerprint_parser is not None,
            ),
            (
                "expected device-key session binding",
                capabilities.expected_device_binding,
            ),
        )
        if not available
    )
    if missing:
        raise MissingUpstreamCapabilities("verify device identity", missing)
    assert capabilities.device_fingerprint_parser is not None
    return capabilities.device_fingerprint_parser


def require_full_profile_read_api() -> None:
    """Fail before BLE I/O if any strict full-profile reader API is missing."""
    capabilities = get_upstream_capabilities()
    missing = tuple(
        name
        for name, available in (
            (
                "signed device-key verifier",
                capabilities.device_fingerprint_parser is not None,
            ),
            (
                "expected device-key session binding",
                capabilities.expected_device_binding,
            ),
            ("fresh state request", capabilities.request_state),
            ("typed named response request", capabilities.request_named),
            ("daily-routine request", capabilities.request_day_routine),
            ("typed response decoder", capabilities.typed_response_decoder),
        )
        if not available
    )
    if missing:
        raise MissingUpstreamCapabilities("read the complete profile", missing)
