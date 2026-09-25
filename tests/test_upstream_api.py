"""Fail-closed compatibility contracts for the pinned Lumalou API."""

from types import SimpleNamespace

import pytest

from custom_components.lumalou.upstream_api import (
    MissingUpstreamCapabilities,
    UpstreamCapabilities,
    inspect_upstream_capabilities,
    require_factory_identity_api,
    require_full_profile_read_api,
)


def parser(_token: bytes) -> str:
    """Synthetic signature verifier surface; no token data is needed here."""
    return "a" * 64


def test_missing_upstream_capabilities_are_not_guessed_from_version() -> None:
    capabilities = inspect_upstream_capabilities(
        SimpleNamespace(),
        SimpleNamespace(LumalouClient=None, ResponseEnvelope=None),
    )

    assert not capabilities.identity_ready
    assert not capabilities.full_profile_read_ready


def test_catch_all_constructor_kwargs_do_not_count_as_identity_binding() -> None:
    class Client:
        def __init__(self, address, **kwargs):
            pass

        async def request_state(self):
            pass

        async def request_named(self, name):
            pass

        async def request_day_routine(self, day):
            pass

    response = type("Response", (), {"decode": lambda self: None})
    capabilities = inspect_upstream_capabilities(
        SimpleNamespace(parse_factory_device_fingerprint=parser),
        SimpleNamespace(LumalouClient=Client, ResponseEnvelope=response),
    )

    assert capabilities.device_fingerprint_parser is parser
    assert not capabilities.expected_device_binding
    assert not capabilities.identity_ready
    assert not capabilities.full_profile_read_ready


def test_identity_capability_requires_named_constructor_parameter() -> None:
    class Client:
        def __init__(self, address, *, expected_device_fingerprint=None):
            pass

    capabilities = inspect_upstream_capabilities(
        SimpleNamespace(parse_factory_device_fingerprint=parser),
        SimpleNamespace(LumalouClient=Client),
    )

    assert capabilities.identity_ready
    assert not capabilities.full_profile_read_ready


def test_full_profile_capability_requires_every_typed_read_surface() -> None:
    class Client:
        def __init__(self, address, *, expected_device_fingerprint=None):
            pass

        async def request_state(self):
            pass

        async def request_named(self, name):
            pass

        async def request_day_routine(self, day):
            pass

    response = type("Response", (), {"decode": lambda self: None})
    complete = inspect_upstream_capabilities(
        SimpleNamespace(parse_factory_device_fingerprint=parser),
        SimpleNamespace(LumalouClient=Client, ResponseEnvelope=response),
    )
    missing_decoder = inspect_upstream_capabilities(
        SimpleNamespace(parse_factory_device_fingerprint=parser),
        SimpleNamespace(LumalouClient=Client, ResponseEnvelope=object),
    )

    assert complete.identity_ready
    assert complete.full_profile_read_ready
    assert not missing_decoder.full_profile_read_ready


def test_required_identity_api_returns_only_a_complete_signed_binding_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capabilities = UpstreamCapabilities(
        device_fingerprint_parser=parser,
        expected_device_binding=True,
        request_state=False,
        request_named=False,
        request_day_routine=False,
        typed_response_decoder=False,
    )
    monkeypatch.setattr(
        "custom_components.lumalou.upstream_api.get_upstream_capabilities",
        lambda: capabilities,
    )

    assert require_factory_identity_api() is parser


def test_required_identity_api_lists_missing_verifier_and_item_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capabilities = UpstreamCapabilities(
        device_fingerprint_parser=None,
        expected_device_binding=False,
        request_state=True,
        request_named=True,
        request_day_routine=True,
        typed_response_decoder=True,
    )
    monkeypatch.setattr(
        "custom_components.lumalou.upstream_api.get_upstream_capabilities",
        lambda: capabilities,
    )

    with pytest.raises(MissingUpstreamCapabilities, match="signed device-key verifier"):
        require_factory_identity_api()


def test_required_profile_read_api_rejects_partial_contract_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capabilities = UpstreamCapabilities(
        device_fingerprint_parser=parser,
        expected_device_binding=True,
        request_state=True,
        request_named=False,
        request_day_routine=False,
        typed_response_decoder=False,
    )
    monkeypatch.setattr(
        "custom_components.lumalou.upstream_api.get_upstream_capabilities",
        lambda: capabilities,
    )

    with pytest.raises(MissingUpstreamCapabilities, match="named response request"):
        require_full_profile_read_api()
