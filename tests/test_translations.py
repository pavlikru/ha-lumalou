"""Every translation key the integration uses exists in all shipped locales."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lumalou import Audio, Color, LightDuration, PlaylistDuration

from custom_components.lumalou.config_flow import EDITORS
from custom_components.lumalou.const import (
    ISSUE_ID_IDENTITY_ENROLLMENT,
    ISSUE_ID_PROFILE_RESTORE_NEEDED,
)
from custom_components.lumalou.coordinator import _ERRORS

COMPONENT = Path(__file__).parents[1] / "custom_components" / "lumalou"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))


def _keys(value: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}{key}"
        keys |= _keys(child, f"{path}.") if isinstance(child, dict) else {path}
    return keys


def _literals(pattern: str, source: str) -> set[str]:
    return set(re.findall(pattern, source))


def test_locales_match_strings() -> None:
    """English is generated from strings.json; Russian has exactly the same keys."""
    english = json.loads((COMPONENT / "translations/en.json").read_text("utf-8"))
    russian = json.loads((COMPONENT / "translations/ru.json").read_text("utf-8"))

    assert english == STRINGS
    assert _keys(russian) == _keys(STRINGS)


def test_flow_steps_errors_and_aborts_are_translated() -> None:
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    config_source, options_source = source.split("class LumalouOptionsFlow")
    config, options = STRINGS["config"], STRINGS["options"]

    config_steps = _literals(r'step_id="(\w+)"', config_source)
    assert config_steps == {"bluetooth_confirm", "reconfigure", "user"}
    assert config_steps <= set(config["step"])
    # Includes the reasons raised by Home Assistant's own flow helpers.
    assert _literals(r'reason="(\w+)"', config_source) | {
        "already_configured",
        "already_in_progress",
        "reconfigure_successful",
    } <= set(config["abort"])
    assert _literals(r'"base"\]? ?[:=] ?"(\w+)"', config_source) | {
        "cannot_connect",
        "identity_unconfirmed",
        "unsupported_product_code",
    } == set(config["error"])

    edit_steps = _literals(r'_async_edit\(\s*"(\w+)"', options_source)
    option_steps = (
        _literals(r'step_id="(\w+)"', options_source)
        | edit_steps
        | {f"{editor}_confirm" for editor in EDITORS}
        | {"read_profile_confirm"}
    )
    assert option_steps == set(options["step"])
    assert set(options["step"]["init"]["menu_options"]) == {
        "read_profile",
        "behavior",
        *EDITORS,
    }
    assert set(options["step"]["read_first"]["menu_options"]) == {
        "read_profile",
        "behavior",
    }
    # _ensure_draft returns its abort reasons as plain strings.
    assert _literals(r'reason="(\w+)"', options_source) | {"profile_not_read"} == set(
        options["abort"]
    )
    assert _literals(r'"base"\]? ?[:=] ?"(\w+)"', options_source) | {
        "confirmation_required",
        *(f"invalid_{step}" for step in edit_steps),
    } == set(options["error"])


def test_issues_and_exceptions_are_translated() -> None:
    assert {
        ISSUE_ID_IDENTITY_ENROLLMENT,
        ISSUE_ID_PROFILE_RESTORE_NEEDED,
    } == set(STRINGS["issues"])
    restore_flow = STRINGS["issues"][ISSUE_ID_PROFILE_RESTORE_NEEDED]["fix_flow"]
    menu = set(restore_flow["step"]["init"]["menu_options"])
    assert menu == {"restore", "keep_device"}
    assert {f"{step}_failed" for step in menu} == set(restore_flow["error"])

    used = set(_ERRORS)
    for module in ("media_player.py", "services.py"):
        source = (COMPONENT / module).read_text(encoding="utf-8")
        used |= _literals(r'translation_key="(\w+)"', source)
    # Restore executor outcomes and profile validation from services.py.
    used |= {
        "restore_write",
        "restore_verify",
        "restore_mismatch",
        "revision_conflict",
        "invalid_profile",
    }
    assert used == set(STRINGS["exceptions"])


def test_entity_names_and_states_are_translated() -> None:
    entity = STRINGS["entity"]
    for platform in entity:
        source = (COMPONENT / f"{platform}.py").read_text(encoding="utf-8")
        keys = _literals(r'_attr_translation_key = "(\w+)"', source)
        assert keys == set(entity[platform]), platform

    effects = entity["light"]["light"]["state_attributes"]["effect"]["state"]
    assert set(effects) == {color.name.lower() for color in Color}
    sources = entity["media_player"]["audio"]["state_attributes"]["source"]["state"]
    assert set(sources) == {audio.name.lower() for audio in Audio}
    for key, enum in (
        ("light_duration", LightDuration),
        ("playlist_duration", PlaylistDuration),
    ):
        assert set(entity["select"][key]["state"]) == {
            item.name.lower() for item in enum
        }
