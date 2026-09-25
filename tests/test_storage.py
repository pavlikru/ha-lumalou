"""Profile persistence through Home Assistant's Store helper."""

from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.lumalou.const import PROFILE_SCHEMA_VERSION
from custom_components.lumalou.models import ProfileRecord
from custom_components.lumalou.storage import ProfileStore
from tests.test_restore import complete_profile

KEY = "lumalou.synthetic-entry.profile"


async def test_missing_then_saved_profile_survives_new_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = ProfileStore(hass, "synthetic-entry")
    assert await store.async_load() == ProfileRecord()

    record = ProfileRecord(revision=1, desired_profile=complete_profile(), pending=True)
    await store.async_save(record)
    await hass.async_block_till_done()

    assert hass_storage[KEY]["version"] == PROFILE_SCHEMA_VERSION
    assert hass_storage[KEY]["data"] == record.to_dict()
    assert await ProfileStore(hass, "synthetic-entry").async_load() == record


async def test_invalid_record_is_ignored_with_a_warning(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass_storage[KEY] = {
        "version": PROFILE_SCHEMA_VERSION,
        "minor_version": 1,
        "key": KEY,
        "data": {"revision": "not a record"},
    }

    assert await ProfileStore(hass, "synthetic-entry").async_load() == ProfileRecord()
    assert "Ignoring an invalid saved Lumalou profile" in caplog.text


async def test_remove_deletes_the_saved_profile(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = ProfileStore(hass, "synthetic-entry")
    await store.async_save(ProfileRecord(revision=1))
    await hass.async_block_till_done()

    await store.async_remove()

    assert KEY not in hass_storage
