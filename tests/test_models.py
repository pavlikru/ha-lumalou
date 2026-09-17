"""Full profile validation, partial intent, and persisted-record invariants."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.lumalou.models import (
    DAYS,
    FULL_PROFILE_FIELDS,
    PROFILE_RANGES,
    LumalouRuntimeData,
    ProfileRecord,
    ProfileValidationError,
    export_profile_payload,
    import_profile_payload,
    migrate_v1_record,
    profile_is_complete,
    require_complete_profile,
    validate_profile,
)


def full_profile() -> dict:
    """Return a complete profile with deliberate null/midnight distinctions."""
    week = {day: None for day in DAYS}
    week["sunday"] = {"hour": 0, "minute": 0}
    routines = {day: {"time": None, "slots": [None] * 12} for day in DAYS}
    routines["sunday"] = {
        "time": {"hour": 0, "minute": 0},
        "slots": [
            {"step": 2, "task": 0},
            None,
            {"step": 1, "task": 11},
            *([None] * 9),
        ],
    }
    return {
        "brightness": 1,
        "color": 9,
        "light_duration": 5,
        "volume": 0,
        "playlist": [18, 2, 2, 1],
        "playlist_duration": 6,
        "clock_settings": {"display": False, "brightness": 0, "format": 1},
        "routine_settings": {
            "enabled": False,
            "music": 255,
            "volume": 0,
            "task_reward_sfx": 15,
            "routine_reward_sfx": 0,
        },
        "ready_to_rise": {"enabled": False, "times": deepcopy(week)},
        "sleepy_times": deepcopy(week),
        "alarm": {
            "days": {day: index for index, day in enumerate(DAYS)},
            "sound": 15,
        },
        "routines": routines,
    }


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


def test_complete_profile_preserves_order_slots_and_detaches_copy():
    profile = full_profile()
    result = require_complete_profile(profile)
    assert result == profile
    assert set(result) == FULL_PROFILE_FIELDS
    assert result["playlist"] == [18, 2, 2, 1]
    assert result["routines"]["sunday"]["slots"][:3] == [
        {"step": 2, "task": 0},
        None,
        {"step": 1, "task": 11},
    ]
    result["playlist"].append(3)
    result["sleepy_times"]["sunday"]["hour"] = 1
    assert profile["playlist"] == [18, 2, 2, 1]
    assert profile["sleepy_times"]["sunday"] == {"hour": 0, "minute": 0}


def test_partial_profile_valid_but_never_complete():
    assert validate_profile({}) == {}
    assert validate_profile({"volume": 2}) == {"volume": 2}
    assert not profile_is_complete({})
    assert not profile_is_complete({"volume": 2})
    with pytest.raises(ProfileValidationError, match="incomplete"):
        require_complete_profile({"volume": 2})


def test_legacy_subset_envelope_remains_versioned_and_strict():
    profile = {"volume": 2, "playlist": [3, 1]}
    envelope = export_profile_payload(profile)
    assert envelope == {
        "schema_version": 1,
        "scope": "supported_subset",
        "profile": profile,
    }
    assert import_profile_payload(envelope) == profile
    envelope["profile"]["clock_settings"] = {
        "display": False,
        "brightness": 0,
        "format": 0,
    }
    with pytest.raises(ProfileValidationError):
        import_profile_payload(envelope)


def test_v2_envelope_does_not_masquerade_as_legacy_subset():
    profile = full_profile()
    envelope = export_profile_payload(profile)
    assert envelope["schema_version"] == 2
    assert envelope["scope"] == "persistent_profile"
    assert import_profile_payload(envelope) == profile
    for field, value in (
        ("schema_version", 1),
        ("schema_version", True),
        ("scope", "supported_subset"),
    ):
        corrupted = deepcopy(envelope)
        corrupted[field] = value
        with pytest.raises(ProfileValidationError):
            import_profile_payload(corrupted)


@pytest.mark.parametrize("payload", [None, {}, {"schema_version": 2}, {"extra": 1}])
def test_profile_envelope_rejects_unknown_or_missing_fields(payload):
    with pytest.raises(ProfileValidationError):
        import_profile_payload(payload)


def test_null_is_no_scheduled_time_and_midnight_remains_a_time():
    profile = full_profile()
    result = require_complete_profile(profile)
    assert result["sleepy_times"]["sunday"] == {"hour": 0, "minute": 0}
    assert result["sleepy_times"]["monday"] is None
    assert result["routines"]["sunday"]["time"] == {"hour": 0, "minute": 0}
    assert result["routines"]["monday"]["time"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clock_settings", {"display": False, "brightness": 0, "format": 2}),
        ("clock_settings", {"display": 0, "brightness": 0, "format": 0}),
        (
            "routine_settings",
            {
                "enabled": False,
                "music": 256,
                "volume": 0,
                "task_reward_sfx": 0,
                "routine_reward_sfx": 0,
            },
        ),
        (
            "routine_settings",
            {
                "enabled": False,
                "music": 0,
                "volume": 256,
                "task_reward_sfx": 0,
                "routine_reward_sfx": 0,
            },
        ),
        (
            "routine_settings",
            {
                "enabled": False,
                "music": 0,
                "volume": 0,
                "task_reward_sfx": 16,
                "routine_reward_sfx": 0,
            },
        ),
    ],
)
def test_unknown_settings_enums_and_ranges_rejected(field, value):
    with pytest.raises(ProfileValidationError):
        validate_profile({field: value})


@pytest.mark.parametrize(
    "value",
    [
        {day: None for day in DAYS[:-1]},
        {**{day: None for day in DAYS}, "extra": None},
        {**{day: None for day in DAYS}, "sunday": {"hour": 24, "minute": 0}},
        {**{day: None for day in DAYS}, "sunday": {"hour": 0, "minute": 60}},
        {**{day: None for day in DAYS}, "sunday": {"hour": 0}},
        [None] * 7,
    ],
)
def test_weekly_schedule_requires_exact_days_and_times(value):
    with pytest.raises(ProfileValidationError):
        validate_profile({"sleepy_times": value})


@pytest.mark.parametrize("alarm", [-1, 11, True, "inactive"])
def test_alarm_enum_rejected(alarm):
    days = {day: 9 for day in DAYS}
    days["sunday"] = alarm
    with pytest.raises(ProfileValidationError):
        validate_profile({"alarm": {"days": days, "sound": 0}})


@pytest.mark.parametrize("sound", [-1, 16, True, "1"])
def test_alarm_sound_range_rejected(sound):
    with pytest.raises(ProfileValidationError):
        validate_profile({"alarm": {"days": {day: 9 for day in DAYS}, "sound": sound}})


@pytest.mark.parametrize(
    "slots",
    [
        [],
        [None] * 11,
        [None] * 13,
        [True] + [None] * 11,
        [{"step": 0, "task": 0}] + [None] * 11,
        [{"step": 13, "task": 0}] + [None] * 11,
        [{"step": 1, "task": 12}] + [None] * 11,
        [{"step": 1, "task": 0, "extra": 1}] + [None] * 11,
    ],
)
def test_routine_requires_exact_lossless_slots(slots):
    routines = {day: {"time": None, "slots": [None] * 12} for day in DAYS}
    routines["friday"] = {"time": None, "slots": slots}
    with pytest.raises(ProfileValidationError):
        validate_profile({"routines": routines})


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


def test_v1_migration_preserves_metadata_and_partialness():
    source = {
        "schema_version": 1,
        "revision": 8,
        "desired_profile": {"volume": 4, "playlist": [3, 1]},
        "previous": {"revision": 7, "profile": {"volume": 3}},
        "verified_revision": 6,
        "pending": True,
        "sync_status": "pending",
        "last_error": "offline",
        "maintenance": True,
    }
    migrated = migrate_v1_record(source)
    assert migrated.schema_version == 2
    assert migrated.revision == 8
    assert migrated.desired_profile == {"volume": 4, "playlist": [3, 1]}
    assert migrated.previous == {"revision": 7, "profile": {"volume": 3}}
    assert migrated.verified_revision == 6
    assert migrated.pending is True
    assert migrated.sync_status == "pending"
    assert migrated.last_error == "offline"
    assert migrated.maintenance is True
    assert not profile_is_complete(migrated.desired_profile)


@pytest.mark.parametrize(
    "profile",
    [
        {"unknown": 0},
        {"audio_source": 0},
        {"playlist": [0]},
        {"brightness": 0},
    ],
)
def test_v1_migration_rejects_non_v1_or_corrupt_subset(profile):
    source = ProfileRecord().to_dict()
    source["schema_version"] = 1
    source["desired_profile"] = profile
    with pytest.raises(ProfileValidationError):
        migrate_v1_record(source)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("schema_version", 3),
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
