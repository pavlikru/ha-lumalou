"""Tests for the initial repository scaffold."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import custom_components.lumalou

PROJECT_ROOT = Path(__file__).parents[1]
MANIFEST_PATH = PROJECT_ROOT / "custom_components" / "lumalou" / "manifest.json"
HACS_PATH = PROJECT_ROOT / "hacs.json"
BRAND_PATH = PROJECT_ROOT / "custom_components" / "lumalou" / "brand"


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


def test_hacs_metadata_uses_only_current_supported_fields() -> None:
    """Keep custom-repository metadata minimal and version-gated."""
    assert json.loads(HACS_PATH.read_text(encoding="utf-8")) == {
        "name": "Lumalou",
        "homeassistant": "2026.9.2",
    }


def test_local_brand_icons_match_home_assistant_dimensions() -> None:
    """Ship transparent square PNGs for normal and high-density displays."""
    for filename, expected_size in (("icon.png", 256), ("icon@2x.png", 512)):
        contents = (BRAND_PATH / filename).read_bytes()
        assert contents[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", contents[16:24])
        assert (width, height) == (expected_size, expected_size)
        assert contents[25] in (4, 6)  # Grayscale/RGB with an alpha channel.
