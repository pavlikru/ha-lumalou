"""Tests for the initial repository scaffold."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import custom_components.lumalou

PROJECT_ROOT = Path(__file__).parents[1]
MANIFEST_PATH = PROJECT_ROOT / "custom_components" / "lumalou" / "manifest.json"


def load_manifest() -> dict[str, Any]:
    """Load the integration manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_identity() -> None:
    """Keep the immutable integration identity stable."""
    manifest = load_manifest()

    assert custom_components.lumalou.__doc__
    assert manifest["domain"] == "lumalou"
    assert manifest["name"] == "Lumalou"
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "local_push"


def test_manifest_uses_released_upstream_library() -> None:
    """Require an exact version of the upstream protocol library."""
    requirements = load_manifest()["requirements"]

    assert requirements == ["lumalou==0.1.0"]
