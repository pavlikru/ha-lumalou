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
TRANSLATIONS_PATH = PROJECT_ROOT / "custom_components" / "lumalou" / "translations"
STRINGS_PATH = PROJECT_ROOT / "custom_components" / "lumalou" / "strings.json"


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


def test_hacs_metadata_uses_current_supported_fields() -> None:
    """Use branch sources until a ZIP-bearing release exists."""
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


def test_profile_storage_repair_translations_are_shipped() -> None:
    """Keep the profile recovery issue and fix flow translated in both locales."""
    for path in (
        STRINGS_PATH,
        TRANSLATIONS_PATH / "en.json",
        TRANSLATIONS_PATH / "ru.json",
    ):
        issue = json.loads(path.read_text(encoding="utf-8"))["issues"][
            "profile_storage"
        ]
        assert issue["title"]
        assert "description" not in issue
        fix_flow = issue["fix_flow"]
        assert fix_flow["step"]["import_profile"]["description"]
        assert fix_flow["step"]["import_profile"]["data"]["profile_json"]
        assert fix_flow["step"]["confirm"]["data"]["confirm"]
        assert fix_flow["error"]["invalid_profile"]
