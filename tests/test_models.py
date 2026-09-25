"""Full profile validation, partial intent, and persisted-record invariants."""

from copy import deepcopy

import pytest

from custom_components.lumalou.models import (
    DAYS,
    FULL_PROFILE_FIELDS,
    ProfileRecord,
    ProfileValidationError,
    export_profile_payload,
    import_profile_payload,
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
        "playlist": [12, 2, 2, 1],
        "clock_settings": {"display": False, "brightness": 0, "format": 1},
        "routine_settings": {
            "enabled": False,
            "music": 15,
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
        "light_and_sound": {
            "volume": 0,
            "light_brightness": 9,
            "light_duration": 5,
            "playlist_duration": 6,
        },
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "profile",
        {"raw_opcode": 0x52},
        {"routine": []},
        # Colour and on/off are live state, never part of the profile.
        {"color": 1},
        {"volume": 3},
        {"light_and_sound": {"volume": 3}},
        {
            "light_and_sound": {
                "volume": 10,
                "light_brightness": 1,
                "light_duration": 0,
                "playlist_duration": 0,
            }
        },
        {
            "light_and_sound": {
                "volume": 1,
                "light_brightness": 1,
                "light_duration": 6,
                "playlist_duration": 0,
            }
        },
    ],
)
def test_unknown_profile_rejected(value):
    with pytest.raises(ProfileValidationError):
        validate_profile(value)


@pytest.mark.parametrize("playlist", [[0], [13], [True], [1] * 13, "1", (1, 2)])
def test_invalid_playlist_rejected_not_truncated(playlist):
    with pytest.raises(ProfileValidationError):
        validate_profile({"playlist": playlist})


def test_complete_profile_preserves_order_slots_and_detaches_copy():
    profile = full_profile()
    result = require_complete_profile(profile)
    assert result == profile
    assert set(result) == FULL_PROFILE_FIELDS
    assert result["playlist"] == [12, 2, 2, 1]
    assert result["routines"]["sunday"]["slots"][:3] == [
        {"step": 2, "task": 0},
        None,
        {"step": 1, "task": 11},
    ]
    result["playlist"].append(3)
    result["sleepy_times"]["sunday"]["hour"] = 1
    assert profile["playlist"] == [12, 2, 2, 1]
    assert profile["sleepy_times"]["sunday"] == {"hour": 0, "minute": 0}


def test_partial_profile_valid_but_never_complete():
    assert validate_profile({}) == {}
    assert validate_profile({"playlist": [2]}) == {"playlist": [2]}
    assert not profile_is_complete({})
    assert not profile_is_complete({"playlist": [2]})
    assert not profile_is_complete({"brightness": 5})
    with pytest.raises(ProfileValidationError, match="incomplete"):
        require_complete_profile({"playlist": [2]})


def test_export_envelope_is_versioned_and_strict():
    profile = full_profile()
    envelope = export_profile_payload(profile)
    assert envelope["schema_version"] == 3
    assert envelope["scope"] == "persistent_profile"
    assert import_profile_payload(envelope) == profile
    for field, value in (
        ("schema_version", 2),
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
                "music": 16,
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
                "volume": 16,
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


def test_record_roundtrip_is_detached():
    record = ProfileRecord(
        revision=2,
        desired_profile={"playlist": [2]},
        verified_revision=1,
        temporary_routine_day="friday",
        pending=True,
        sync_status="pending",
        last_error="synthetic",
        maintenance=True,
    )
    serialized = record.to_dict()
    assert ProfileRecord.from_dict(serialized) == record
    serialized["desired_profile"]["playlist"].append(3)
    assert record.desired_profile == {"playlist": [2]}


@pytest.mark.parametrize("field", ["music", "volume"])
def test_routine_values_are_limited_to_the_readback_nibble(field):
    """Only 0..15 round-trips through GLOBAL_STATE."""
    profile = full_profile()
    profile["routine_settings"][field] = 15
    assert validate_profile(profile)["routine_settings"][field] == 15

    profile["routine_settings"][field] = 16
    with pytest.raises(ProfileValidationError):
        validate_profile(profile)
    with pytest.raises(ProfileValidationError):
        import_profile_payload(
            {"schema_version": 3, "scope": "persistent_profile", "profile": profile}
        )
    with pytest.raises(ProfileValidationError):
        ProfileRecord.from_dict(
            ProfileRecord(revision=1, desired_profile=profile).to_dict()
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", 4),
        ("schema_version", True),
        ("revision", -1),
        ("verified_revision", 1),
        ("sync_status", "verified"),
        ("pending", 1),
        ("maintenance", "false"),
        ("last_error", 42),
        ("desired_profile", {"unknown": 0}),
        ("temporary_routine_day", "someday"),
        ("temporary_routine_day", 0),
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


def test_record_verification_is_bound_to_a_fingerprint():
    data = ProfileRecord(revision=2, verified_revision=2).to_dict()
    assert not ProfileRecord.from_dict(data).is_verified
    del data["verified_fingerprint"]
    with pytest.raises(ProfileValidationError):
        ProfileRecord.from_dict(data)
    bound = ProfileRecord(
        revision=2, verified_revision=2, verified_fingerprint="c" * 64
    )
    assert ProfileRecord.from_dict(bound.to_dict()) == bound
    assert bound.is_verified
    assert not ProfileRecord(
        revision=3, verified_revision=2, verified_fingerprint="c" * 64
    ).is_verified


@pytest.mark.parametrize("fingerprint", ["", "C" * 64, "c" * 63, 7, True])
def test_record_rejects_invalid_verified_fingerprint(fingerprint):
    data = ProfileRecord().to_dict()
    data["verified_fingerprint"] = fingerprint
    with pytest.raises(ProfileValidationError):
        ProfileRecord.from_dict(data)
