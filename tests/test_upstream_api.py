"""The exact dependency pin provides the identity API without probing."""

import lumalou

from custom_components.lumalou.upstream_api import require_factory_identity_api


def test_identity_api_is_the_pinned_upstream_verifier() -> None:
    assert lumalou.__version__ == "0.2.0"
    assert require_factory_identity_api() is lumalou.parse_factory_device_fingerprint
