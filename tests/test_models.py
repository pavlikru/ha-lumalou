"""Strict subset validation and persisted-record invariants."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.lumalou.models import (
    PROFILE_RANGES,
    LumalouRuntimeData,
    ProfileRecord,
    ProfileValidationError,
    validate_profile,
)


@pytest.mark.parametrize("name", PROFILE_RANGES)
def test_profile_range_boundaries(name):
    low, high = PROFILE_RANGES[name]
    for value in (low, high):
        assert validate_profile({name: value}) == {name: value}
    for value in (low - 1, high + 1, True, False, "1", 1.0, None):
        with pytest.raises(ProfileValidationError):
            validate_profile({name: value})


@pytest.mark.parametrize(
    "value", [None, [], "profile", {"raw_opcode": 0x52}, {"routine": []}]
)
def test_unknown_profile_rejected(value):
    with pytest.raises(ProfileValidationError):
        validate_profile(value)


@pytest.mark.parametrize("playlist", [[0], [19], [True], [1] * 13, "1", (1, 2)])
def test_invalid_playlist_rejected_not_truncated(playlist):
    with pytest.raises(ProfileValidationError):
        validate_profile({"playlist": playlist})


def test_playlist_order_duplicates_empty_and_detached_copy():
    profile = {"playlist": [18, 2, 2, 1], "volume": 0}
    result = validate_profile(profile)
    assert result == profile
    result["playlist"].append(3)
    assert profile["playlist"] == [18, 2, 2, 1]
    assert validate_profile({"playlist": []}) == {"playlist": []}
    assert validate_profile({}) == {}
    assert len(validate_profile({"playlist": [1] * 12})["playlist"]) == 12


def test_record_roundtrip_and_runtime_property():
    record = ProfileRecord(
        revision=2,
        desired_profile={"volume": 2},
        previous={"revision": 1, "profile": {"volume": 1}},
        verified_revision=1,
        pending=True,
        sync_status="partial",
        last_error="synthetic",
        maintenance=True,
    )
    serialized = record.to_dict()
    assert ProfileRecord.from_dict(serialized) == record
    serialized["desired_profile"]["volume"] = 3
    assert record.desired_profile == {"volume": 2}
    coordinator = SimpleNamespace(profile_record=record)
    assert LumalouRuntimeData(coordinator).profile_record is record


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("revision", -1),
        ("verified_revision", 1),
        ("sync_status", "verified"),
        ("pending", 1),
        ("maintenance", "false"),
        ("last_error", 42),
        ("desired_profile", {"unknown": 0}),
        ("previous", []),
        ("previous", {"profile": {}}),
        ("previous", {"revision": -1, "profile": {}}),
        ("previous", {"revision": 0, "profile": {"volume": 10}}),
    ],
)
def test_corrupt_record_rejected(field, value):
    data = deepcopy(ProfileRecord().to_dict())
    data[field] = value
    with pytest.raises(ProfileValidationError):
        ProfileRecord.from_dict(data)


@pytest.mark.parametrize("value", [None, {}, [], {"extra": 1}])
def test_record_requires_complete_known_schema(value):
    with pytest.raises(ProfileValidationError):
        ProfileRecord.from_dict(value)
