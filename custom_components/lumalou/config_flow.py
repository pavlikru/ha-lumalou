"""Config and options flows for Lumalou."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import time
from typing import Any, override

import voluptuous as vol
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, FlowType
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    DOMAIN,
    ISSUE_ID_IDENTITY_ENROLLMENT,
    SUPPORTED_PRODUCT_CODE,
)
from .entity import async_migrate_identifiers
from .identity import (
    FactoryIdentityLibraryUnavailable,
    FactoryIdentityProbeError,
    async_read_device_information,
    async_read_factory_device_fingerprint,
)
from .models import (
    DAYS,
    PROFILE_RANGES,
    RevisionConflictError,
    import_profile_payload,
    validate_profile,
)
from .upstream_api import MissingUpstreamCapabilities, require_factory_identity_api

CONF_AUTO_RESTORE = "auto_restore"
CONF_CONFIRM = "confirm"
CONF_PROFILE_JSON = "profile_json"
DEFAULT_AUTO_RESTORE = False
MANUFACTURER_ID = 950
MANUFACTURER_PREFIX = b"MB"
MAX_ROUTINE_TASKS = 12
MAX_PLAYLIST_SONGS = 12
EDITORS = (
    "basic",
    "playlist",
    "clock_settings",
    "routine_settings",
    "schedule",
    "routine",
)

type _SaveProfile = Callable[[Any], Awaitable[None]]


def _is_supported(info: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement is a connectable Lumalou."""
    manufacturer_data = info.manufacturer_data.get(MANUFACTURER_ID, b"")
    return info.connectable and manufacturer_data.startswith(MANUFACTURER_PREFIX)


def _device_title(info: BluetoothServiceInfoBleak) -> str:
    """Build a user-facing title without exposing the numeric BLE name."""
    if info.name and info.name != info.address and not info.name.isdecimal():
        return info.name
    return "Lumalou"


def _enrolled_fingerprint(entry: config_entries.ConfigEntry) -> str | None:
    """Return the signed-device binding, or None for a pre-enrollment entry."""
    if fingerprint := entry.data.get(CONF_DEVICE_FINGERPRINT):
        return str(fingerprint)
    # Pre-enrollment entries use their address as unique ID.
    if entry.unique_id != entry.data.get(CONF_ADDRESS):
        return entry.unique_id
    return None


def parse_profile_json(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate an export envelope or the complete export action response.

    Returns the envelope and its validated profile. The revision in an export
    action response is ignored; callers use the entry's current revision.
    """
    if not isinstance(value, str):
        raise ValueError("Profile JSON must be text")
    document = json.loads(value)
    if (
        isinstance(document, dict)
        and set(document) == {"current_revision", "profile"}
        and isinstance(document["profile"], dict)
    ):
        document = document["profile"]
    if not isinstance(document, dict):
        raise ValueError("Profile export must be a JSON object")
    return document, import_profile_payload(document)


def _read_profile_summary(profile: dict[str, Any]) -> str:
    """Build a compact preview of persistent values without IDs or raw bytes."""
    ready_times = profile["ready_to_rise"]["times"]
    sleepy_times = profile["sleepy_times"]
    routines = profile["routines"]
    midnight = {"hour": 0, "minute": 0}
    wake_count = sum(value is not None for value in ready_times.values())
    wake_midnight = sum(value == midnight for value in ready_times.values())
    sleepy_count = sum(value is not None for value in sleepy_times.values())
    sleepy_midnight = sum(value == midnight for value in sleepy_times.values())
    routine_days = sum(value["time"] is not None for value in routines.values())
    routine_tasks = sum(
        slot is not None for value in routines.values() for slot in value["slots"]
    )
    active_alarms = sum(value != 9 for value in profile["alarm"]["days"].values())
    clock = profile["clock_settings"]
    return (
        f"Light {profile['brightness']}/9, color {profile['color']}; "
        f"volume {profile['volume']}/9; playlist {len(profile['playlist'])}/12; "
        f"clock {'on' if clock['display'] else 'off'}, "
        f"brightness {clock['brightness']}/9, "
        f"{'24' if clock['format'] else '12'}-hour; "
        f"wake {wake_count}/7 ({wake_midnight} at midnight); "
        f"bedtime {sleepy_count}/7 ({sleepy_midnight} at midnight); "
        f"alarms {active_alarms}/7; routines {routine_days}/7, "
        f"{routine_tasks}/84 tasks"
    )


class LumalouConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Lumalou config flow."""

    VERSION = 1
    # 1.2: registry identifiers derive from the entry unique ID, not the address.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None
        self._candidates: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LumalouOptionsFlow:
        """Return the options flow."""
        return LumalouOptionsFlow()

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery without accessing the device."""
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")
        if not _is_supported(discovery_info):
            return self.async_abort(reason="unsupported_device")
        return await self._async_select(discovery_info)

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a currently discovered connectable Lumalou."""
        if user_input is not None:
            return await self._async_select(self._candidates[user_input[CONF_ADDRESS]])

        self._candidates = self._discovered_candidates()
        if not self._candidates:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(step_id="user", data_schema=self._address_schema())

    async def _async_select(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Deduplicate a candidate before any connection, then ask to confirm.

        The address is only a provisional unique ID: it stops duplicate
        discovery flows and allows ignoring a candidate. Confirmation replaces
        it with the signed-device fingerprint.
        """
        await self.async_set_unique_id(
            info.address, raise_on_progress=self.source != config_entries.SOURCE_USER
        )
        self._abort_if_unique_id_configured()
        self._async_abort_entries_match({CONF_ADDRESS: info.address})
        self._discovered = info
        self.context["title_placeholders"] = {"name": _device_title(info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify the user-confirmed device's signed identity and create the entry."""
        assert self._discovered is not None
        address = self._discovered.address
        errors: dict[str, str] = {}
        if user_input is not None:
            # Another flow may have configured this address meanwhile.
            self._async_abort_entries_match({CONF_ADDRESS: address})
            fingerprint, errors = await self._async_probe(self._discovered)
            if fingerprint is not None:
                await self.async_set_unique_id(fingerprint)
                # The same signed device at a new address: follow it, but keep
                # control locked until its profile is read again.
                self._abort_if_unique_id_configured(
                    updates={CONF_ADDRESS: address, CONF_PROTOCOL_VERIFIED: False}
                )
                return self.async_create_entry(
                    title=_device_title(self._discovered),
                    data=_entry_data(address, fingerprint),
                    options={CONF_AUTO_RESTORE: DEFAULT_AUTO_RESTORE},
                )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            errors=errors,
            description_placeholders=self.context["title_placeholders"],
        )

    @override
    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Open the private-profile choice only after the entry exists.

        A profile belongs to the entry-private Store, never to config entry
        data or options.  Options flows need the entry id, so they can only be
        created once Home Assistant has added the entry.
        """
        entry = result["result"]
        options_result = await self.hass.config_entries.options.async_init(
            entry.entry_id
        )
        result["next_flow"] = (FlowType.OPTIONS_FLOW, options_result["flow_id"])
        return result

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rebind an entry to its signed device after an address change.

        This also enrolls a pre-enrollment entry and moves its registry
        identifiers from the address to the fingerprint.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            self._async_abort_entries_match({CONF_ADDRESS: address})
            # Resolve the address again so a stale form cannot probe a
            # BLEDevice that is no longer connectable.
            candidates = self._discovered_candidates(entry.entry_id)
            if (info := candidates.get(address)) is None:
                errors["base"] = "device_unavailable"
            else:
                fingerprint, errors = await self._async_probe(info)
                if fingerprint is not None:
                    return await self._async_rebind(entry, address, fingerprint)
        else:
            self._candidates = self._discovered_candidates(entry.entry_id)
            if not self._candidates:
                return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._address_schema(), errors=errors
        )

    async def _async_rebind(
        self, entry: config_entries.ConfigEntry, address: str, fingerprint: str
    ) -> ConfigFlowResult:
        """Update a verified entry without orphaning its entities."""
        if _enrolled_fingerprint(entry) not in (None, fingerprint):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._address_schema(),
                errors={"base": "wrong_device"},
            )
        if fingerprint != entry.unique_id:
            if self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, fingerprint
            ):
                return self.async_abort(reason="already_configured")
            await async_migrate_identifiers(
                self.hass, entry, str(entry.unique_id), fingerprint
            )
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{entry.entry_id}_{ISSUE_ID_IDENTITY_ENROLLMENT}"
        )
        return self.async_update_reload_and_abort(
            entry, unique_id=fingerprint, data=_entry_data(address, fingerprint)
        )

    async def _async_probe(
        self, info: BluetoothServiceInfoBleak
    ) -> tuple[str | None, dict[str, str]]:
        """Verify the selected signed device key without guessing a SKU."""
        name = _device_title(info)
        try:
            require_factory_identity_api()
        except MissingUpstreamCapabilities:
            return None, {"base": "factory_verifier_unavailable"}
        try:
            identity = await async_read_device_information(info.device, name)
        except Exception:
            return None, {"base": "cannot_connect"}
        detected = (identity.model_number or "").strip().upper()
        if detected and detected != SUPPORTED_PRODUCT_CODE:
            return None, {"base": "unsupported_product_code"}

        # Device Information is only a conflict check. The signed key binds
        # every later session to the device selected and confirmed by the user.
        try:
            fingerprint = await async_read_factory_device_fingerprint(info.device, name)
        except FactoryIdentityLibraryUnavailable:
            return None, {"base": "factory_verifier_unavailable"}
        except FactoryIdentityProbeError:
            return None, {"base": "identity_unconfirmed"}
        return fingerprint, {}

    def _discovered_candidates(
        self, exclude_entry_id: str | None = None
    ) -> dict[str, BluetoothServiceInfoBleak]:
        """Return connectable Lumalou candidates not owned by another entry."""
        from homeassistant.components import bluetooth

        configured = {
            entry.data.get(CONF_ADDRESS)
            for entry in self._async_current_entries(include_ignore=False)
            if entry.entry_id != exclude_entry_id
        }
        return {
            info.address: info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            if _is_supported(info) and info.address not in configured
        }

    def _address_schema(self) -> vol.Schema:
        """List candidates by title and position, never by advertised serial."""
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        address: f"{_device_title(info)} {index}"
                        for index, (address, info) in enumerate(
                            self._candidates.items(), start=1
                        )
                    }
                )
            }
        )


def _entry_data(address: str, fingerprint: str) -> dict[str, Any]:
    """Build entry data; control stays locked until a verified profile read."""
    return {
        CONF_ADDRESS: address,
        CONF_DEVICE_FINGERPRINT: fingerprint,
        CONF_PROTOCOL_VERIFIED: False,
    }


def _options(first: int, last: int) -> list[str]:
    return [str(value) for value in range(first, last + 1)]


def _select(
    options: list[str], translation_key: str | None = None, *, multiple: bool = False
) -> selector.SelectSelector:
    config = selector.SelectSelectorConfig(options=options, multiple=multiple)
    if translation_key is not None:
        config["translation_key"] = translation_key
    return selector.SelectSelector(config)


def _byte_selector() -> selector.NumberSelector:
    """Return an integer byte selector instead of a guessed music enum."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=255, step=1, mode=selector.NumberSelectorMode.BOX
        )
    )


def _integer(value: Any) -> int:
    """Accept selector floats only when they exactly represent an integer."""
    if type(value) not in (int, float) or int(value) != value:
        raise ValueError("Value must be an integer")
    return int(value)


def _parse_time(value: Any) -> dict[str, int]:
    """Parse a minute-resolution native time selector value."""
    if not isinstance(value, str):
        raise ValueError("Invalid time")
    parsed = time.fromisoformat(value)
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("Time must have minute resolution")
    return {"hour": parsed.hour, "minute": parsed.minute}


def _format_time(value: dict[str, int] | None) -> str:
    """Format a profile time for the native selector."""
    if value is None:
        return "00:00:00"
    return f"{value['hour']:02d}:{value['minute']:02d}:00"


def _week_from_input(
    user_input: dict[str, Any], prefix: str
) -> dict[str, dict[str, int] | None]:
    """Decode toggled time selectors without conflating null and midnight."""
    return {
        day: (
            _parse_time(user_input[f"{prefix}_{day}_time"])
            if user_input[f"{prefix}_{day}_has_time"]
            else None
        )
        for day in DAYS
    }


def _copy_targets(value: Any, source: str) -> list[str]:
    """Validate and normalize selected copy targets, excluding the source."""
    if source not in DAYS or not isinstance(value, list):
        raise ValueError("Unsupported copy selection")
    if any(day not in DAYS for day in value):
        raise ValueError("Unsupported copy target")
    return [day for day in DAYS if day in value and day != source]


def _parse_basic(user_input: dict[str, Any]) -> dict[str, Any]:
    return {name: int(user_input[name]) for name in PROFILE_RANGES}


def _parse_playlist(user_input: dict[str, Any]) -> dict[str, Any]:
    rows = (f"song_{index}" for index in range(1, MAX_PLAYLIST_SONGS + 1))
    return {"playlist": [int(user_input[row]) for row in rows if row in user_input]}


def _parse_clock_settings(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "clock_settings": {
            "display": user_input["clock_display"],
            "brightness": int(user_input["clock_brightness"]),
            "format": int(user_input["clock_format"]),
        }
    }


def _parse_routine_settings(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "routine_settings": {
            "enabled": user_input["routine_enabled"],
            "music": _integer(user_input["routine_music"]),
            "volume": _integer(user_input["routine_volume"]),
            "task_reward_sfx": int(user_input["task_reward_sfx"]),
            "routine_reward_sfx": int(user_input["routine_reward_sfx"]),
        }
    }


def _parse_schedule(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "ready_to_rise": {
            "enabled": user_input["ready_to_rise_enabled"],
            "times": _week_from_input(user_input, "ready_to_rise"),
        },
        "sleepy_times": _week_from_input(user_input, "sleepy"),
    }


def _parse_alarm(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "alarm": {
            "days": {day: int(user_input[f"alarm_{day}"]) for day in DAYS},
            "sound": int(user_input["alarm_sound"]),
        }
    }


class LumalouOptionsFlow(config_entries.OptionsFlow):
    """Edit behavior options and the private desired profile.

    Every profile change is a detached draft of one captured revision and is
    saved only after an explicit confirmation, with a revision check.
    """

    def __init__(self) -> None:
        """Initialize an isolated profile draft."""
        self._draft: dict[str, Any] | None = None
        self._revision: int | None = None
        self._changes: dict[str, Any] = {}
        self._editor: str | None = None
        self._routine_day: str | None = None
        self._copied_days: list[str] = []
        self._pending: tuple[str, dict[str, str], _SaveProfile] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a profile source, one profile editor, or behavior options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["read_profile", "import_profile", *EDITORS, "behavior"],
        )

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure opt-in automatic restore (off by default)."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="behavior",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTO_RESTORE,
                        default=self.config_entry.options.get(
                            CONF_AUTO_RESTORE, DEFAULT_AUTO_RESTORE
                        ),
                    ): selector.BooleanSelector()
                }
            ),
            last_step=True,
        )

    # Profile sources: a full fresh device read or an exported backup.

    async def async_step_read_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read one complete fresh profile, then show a CAS-protected preview."""
        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="entry_not_loaded")
        try:
            snapshot, revision = await coordinator.async_read_profile_snapshot()
        except HomeAssistantError, ValueError:
            return self.async_abort(reason="profile_read_failed")

        async def save(coordinator: Any) -> None:
            await coordinator.async_accept_device_profile(
                deepcopy(snapshot), revision, confirmed=True
            )

        placeholders = {
            "field_count": str(len(snapshot)),
            "revision": str(revision),
            "summary": _read_profile_summary(snapshot),
        }
        self._pending = ("read_profile_confirm", placeholders, save)
        return await self._async_confirm()

    async def async_step_import_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate an exported profile before showing an explicit preview."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                payload, profile = parse_profile_json(user_input[CONF_PROFILE_JSON])
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_profile_import"
            else:
                if (record := self._record()) is None:
                    return self.async_abort(reason="entry_not_loaded")
                # The entry's captured revision is used for CAS instead of any
                # revision carried by a backup from another point in time.
                revision = record.revision

                async def save(coordinator: Any) -> None:
                    await coordinator.async_import_profile(
                        deepcopy(payload), revision, confirmed=True
                    )

                removed = sorted(set(record.desired_profile) - set(profile))
                placeholders = {
                    "schema_version": str(payload["schema_version"]),
                    "scope": str(payload["scope"]),
                    "field_count": str(len(profile)),
                    "revision": str(revision),
                    "removed_count": str(len(removed)),
                    "removed_fields": ", ".join(removed) or "—",
                }
                self._pending = ("import_profile_confirm", placeholders, save)
                return await self._async_confirm()

        return self.async_show_form(
            step_id="import_profile",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE_JSON): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
        )

    # Offline block editors.

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit private light and audio values."""
        return await self._async_edit(
            "basic", user_input, self._basic_schema, _parse_basic
        )

    async def async_step_playlist(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit an ordered, duplicate-preserving playlist."""
        return await self._async_edit(
            "playlist", user_input, self._playlist_schema, _parse_playlist
        )

    async def async_step_clock_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one complete clock settings block."""
        return await self._async_edit(
            "clock_settings",
            user_input,
            self._clock_settings_schema,
            _parse_clock_settings,
        )

    async def async_step_routine_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one complete routine settings block."""
        return await self._async_edit(
            "routine_settings",
            user_input,
            self._routine_settings_schema,
            _parse_routine_settings,
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit both weekly time blocks."""
        return await self._async_edit(
            "schedule",
            user_input,
            self._schedule_schema,
            _parse_schedule,
            self.async_step_schedule_copy,
            editor="schedule",
        )

    async def async_step_schedule_copy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally copy each weekly time to selected days."""
        return await self._async_edit(
            "schedule_copy",
            user_input,
            self._schedule_copy_schema,
            self._parse_schedule_copy,
            self.async_step_schedule_alarm,
            editor="schedule",
        )

    async def async_step_schedule_alarm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit weekly alarm offsets and sound."""
        return await self._async_edit(
            "schedule_alarm",
            user_input,
            self._alarm_schema,
            _parse_alarm,
            editor="schedule",
        )

    async def async_step_routine(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one daily routine to edit."""
        if not self._ensure_draft("routine"):
            return self.async_abort(reason="entry_not_loaded")
        if user_input is not None:
            self._routine_day = user_input["routine_day"]
            return await self.async_step_routine_tasks()
        return self.async_show_form(
            step_id="routine",
            data_schema=vol.Schema(
                {
                    vol.Required("routine_day", default=DAYS[0]): _select(
                        list(DAYS), "weekday"
                    )
                }
            ),
        )

    async def async_step_routine_tasks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit fixed ordered task rows without exposing task zero."""
        return await self._async_edit(
            "routine_tasks",
            user_input,
            self._routine_tasks_schema,
            self._parse_routine_tasks,
            self.async_step_routine_copy,
            editor="routine",
        )

    async def async_step_routine_copy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally copy the edited routine to independent day values."""
        assert self._draft is not None
        assert self._routine_day is not None
        if user_input is not None:
            routines = deepcopy(self._draft["routines"])
            self._copied_days = _copy_targets(
                user_input.get("copy_to", []), self._routine_day
            )
            for day in self._copied_days:
                routines[day] = deepcopy(routines[self._routine_day])
            self._draft["routines"] = routines
            self._changes["routines"] = deepcopy(routines)
            return await self._async_confirm_edit()

        days = [day for day in DAYS if day != self._routine_day]
        return self.async_show_form(
            step_id="routine_copy",
            data_schema=vol.Schema(
                {
                    vol.Optional("copy_to", default=[]): _select(
                        days, "weekday", multiple=True
                    )
                }
            ),
        )

    async def _async_edit(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        schema: Callable[[], vol.Schema],
        parse: Callable[[dict[str, Any]], dict[str, Any]],
        next_step: Callable[[], Awaitable[ConfigFlowResult]] | None = None,
        *,
        editor: str | None = None,
    ) -> ConfigFlowResult:
        """Show one draft form, or validate it into the draft and continue."""
        if not self._ensure_draft(editor or step_id):
            return self.async_abort(reason="entry_not_loaded")
        assert self._draft is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                changes = parse(user_input)
                validate_profile(changes)
            except KeyError, TypeError, ValueError:
                errors["base"] = f"invalid_{step_id}"
            else:
                self._draft.update(changes)
                self._changes.update(deepcopy(changes))
                return await (next_step or self._async_confirm_edit)()
        return self.async_show_form(
            step_id=step_id, data_schema=schema(), errors=errors
        )

    # Confirmation and the only profile write of this flow.

    async def _async_confirm_edit(self) -> ConfigFlowResult:
        """Preview the complete draft change before it is saved."""
        assert self._revision is not None
        changes, revision = deepcopy(self._changes), self._revision

        async def save(coordinator: Any) -> None:
            await coordinator.async_edit_profile(deepcopy(changes), revision)

        self._pending = (f"{self._editor}_confirm", self._edit_summary(), save)
        return await self._async_confirm()

    async def _async_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save a previewed profile change only after explicit confirmation."""
        assert self._pending is not None
        step_id, placeholders, save = self._pending
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM]:
                errors[CONF_CONFIRM] = "confirmation_required"
            elif (coordinator := self._coordinator()) is None:
                return self.async_abort(reason="entry_not_loaded")
            else:
                try:
                    await save(coordinator)
                except RevisionConflictError:
                    errors["base"] = "revision_conflict"
                except HomeAssistantError, ValueError:
                    errors["base"] = "profile_save_failed"
                else:
                    return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM, default=False): selector.BooleanSelector()}
            ),
            errors=errors,
            description_placeholders=placeholders,
            last_step=True,
        )

    # Each preview has its own translated step; all submit to one handler.
    async_step_basic_confirm = _async_confirm
    async_step_playlist_confirm = _async_confirm
    async_step_clock_settings_confirm = _async_confirm
    async_step_routine_settings_confirm = _async_confirm
    async_step_schedule_confirm = _async_confirm
    async_step_routine_confirm = _async_confirm
    async_step_import_profile_confirm = _async_confirm
    async_step_read_profile_confirm = _async_confirm

    def _edit_summary(self) -> dict[str, str]:
        """Return description placeholders for the current editor's preview."""
        assert self._draft is not None
        draft = self._draft
        summary: dict[str, Any] = {"revision": self._revision}
        if self._editor == "basic":
            summary |= {name: draft[name] for name in PROFILE_RANGES}
        elif self._editor == "playlist":
            playlist = draft["playlist"]
            summary |= {
                "song_count": len(playlist),
                "songs": ", ".join(map(str, playlist)) or "—",
            }
        elif self._editor in ("clock_settings", "routine_settings"):
            summary |= draft[self._editor]
        elif self._editor == "schedule":
            alarm = draft["alarm"]
            summary |= {
                "ready_count": sum(
                    value is not None
                    for value in draft["ready_to_rise"]["times"].values()
                ),
                "sleepy_count": sum(
                    value is not None for value in draft["sleepy_times"].values()
                ),
                "alarm_count": sum(value != 9 for value in alarm["days"].values()),
                "alarm_sound": alarm["sound"] + 1,
            }
        else:
            assert self._routine_day is not None
            slots = draft["routines"][self._routine_day]["slots"]
            summary |= {
                "day": self._routine_day,
                "task_count": sum(slot is not None for slot in slots),
                "copy_count": len(self._copied_days),
                "zero_count": sum(
                    slot is not None and slot["task"] == 0 for slot in slots
                ),
            }
        return {name: str(value) for name, value in summary.items()}

    def _ensure_draft(self, editor: str) -> bool:
        """Capture one detached profile revision for this flow."""
        if self._draft is None:
            if (record := self._record()) is None:
                return False
            self._draft = deepcopy(record.desired_profile)
            self._revision = record.revision
            self._editor = editor
        return True

    def _record(self) -> Any | None:
        """Get the loaded profile record without touching Bluetooth."""
        coordinator = self._coordinator()
        return None if coordinator is None else coordinator.profile_record

    def _coordinator(self) -> Any | None:
        """Return the loaded coordinator, never creating one from a flow."""
        try:
            return self.config_entry.runtime_data.coordinator
        except AttributeError:
            return None

    # Draft parsing that needs the current draft.

    def _parse_schedule_copy(self, user_input: dict[str, Any]) -> dict[str, Any]:
        assert self._draft is not None
        ready_to_rise = deepcopy(self._draft["ready_to_rise"])
        sleepy_times = deepcopy(self._draft["sleepy_times"])
        for week, prefix in (
            (ready_to_rise["times"], "ready_to_rise"),
            (sleepy_times, "sleepy"),
        ):
            source = user_input[f"{prefix}_copy_from"]
            for day in _copy_targets(user_input.get(f"{prefix}_copy_to", []), source):
                week[day] = deepcopy(week[source])
        return {"ready_to_rise": ready_to_rise, "sleepy_times": sleepy_times}

    def _parse_routine_tasks(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Build twelve strict slots while retaining unknown task-zero rows."""
        assert self._routine_day is not None
        routines = self._current_routines()
        current = routines[self._routine_day]
        slots: list[dict[str, int] | None] = []
        for index, existing in enumerate(current["slots"], start=1):
            selected = user_input.get(f"task_{index}")
            if selected is None:
                keep = existing is not None and existing["task"] == 0
                slots.append(deepcopy(existing) if keep else None)
                continue
            task = int(selected)
            if task not in range(1, 12):
                raise ValueError("Unsupported routine task")
            step = existing["step"] if existing is not None else index
            slots.append({"step": step, "task": task})
        routines[self._routine_day] = {
            "time": (
                _parse_time(user_input["routine_time"])
                if user_input["routine_has_time"]
                else None
            ),
            "slots": slots,
        }
        return {"routines": routines}

    def _current_routines(self) -> dict[str, dict[str, Any]]:
        """Return a detached complete seven-day routine block."""
        assert self._draft is not None
        if "routines" in self._draft:
            return deepcopy(self._draft["routines"])
        return {
            day: {"time": None, "slots": [None] * MAX_ROUTINE_TASKS} for day in DAYS
        }

    # Form schemas. Absent blocks are shown as new draft values.

    def _basic_schema(self) -> vol.Schema:
        """Return native selectors for private light and audio values."""
        assert self._draft is not None
        return vol.Schema(
            {
                vol.Required(name, default=str(self._draft.get(name, 0))): _select(
                    _options(first, last)
                )
                for name, (first, last) in PROFILE_RANGES.items()
            }
        )

    def _playlist_schema(self) -> vol.Schema:
        """Return twelve fixed ordered playlist rows, including empty rows."""
        assert self._draft is not None
        playlist = self._draft.get("playlist", [])
        schema = vol.Schema(
            {
                vol.Optional(f"song_{index}"): _select(_options(1, 12))
                for index in range(1, MAX_PLAYLIST_SONGS + 1)
            }
        )
        return self.add_suggested_values_to_schema(
            schema,
            {f"song_{index}": str(song) for index, song in enumerate(playlist, 1)},
        )

    def _clock_settings_schema(self) -> vol.Schema:
        """Return native controls for one complete clock settings block."""
        assert self._draft is not None
        clock = self._draft.get(
            "clock_settings", {"display": False, "brightness": 0, "format": 0}
        )
        return vol.Schema(
            {
                vol.Required(
                    "clock_display", default=clock["display"]
                ): selector.BooleanSelector(),
                vol.Required(
                    "clock_brightness", default=str(clock["brightness"])
                ): _select(_options(0, 9)),
                vol.Required("clock_format", default=str(clock["format"])): _select(
                    _options(0, 1)
                ),
            }
        )

    def _routine_settings_schema(self) -> vol.Schema:
        """Return native controls for one complete routine settings block."""
        assert self._draft is not None
        settings = self._draft.get(
            "routine_settings",
            {
                "enabled": False,
                "music": 0,
                "volume": 0,
                "task_reward_sfx": 0,
                "routine_reward_sfx": 0,
            },
        )
        return vol.Schema(
            {
                vol.Required(
                    "routine_enabled", default=settings["enabled"]
                ): selector.BooleanSelector(),
                vol.Required("routine_music", default=settings["music"]): (
                    _byte_selector()
                ),
                vol.Required("routine_volume", default=settings["volume"]): (
                    _byte_selector()
                ),
                vol.Required(
                    "task_reward_sfx", default=str(settings["task_reward_sfx"])
                ): _select(_options(0, 15)),
                vol.Required(
                    "routine_reward_sfx", default=str(settings["routine_reward_sfx"])
                ): _select(_options(0, 15)),
            }
        )

    def _schedule_schema(self) -> vol.Schema:
        """Return native controls for both seven-day time blocks."""
        assert self._draft is not None
        empty_week = dict.fromkeys(DAYS)
        ready = self._draft.get(
            "ready_to_rise", {"enabled": False, "times": empty_week}
        )
        sleepy = self._draft.get("sleepy_times", empty_week)
        fields: dict[vol.Marker, Any] = {
            vol.Required(
                "ready_to_rise_enabled", default=ready["enabled"]
            ): selector.BooleanSelector()
        }
        for prefix, week in (("ready_to_rise", ready["times"]), ("sleepy", sleepy)):
            for day in DAYS:
                value = week[day]
                fields[
                    vol.Required(f"{prefix}_{day}_has_time", default=value is not None)
                ] = selector.BooleanSelector()
                fields[
                    vol.Required(f"{prefix}_{day}_time", default=_format_time(value))
                ] = selector.TimeSelector()
        return vol.Schema(fields)

    def _schedule_copy_schema(self) -> vol.Schema:
        """Return source and multi-day target pickers for weekly times."""
        fields: dict[vol.Marker, Any] = {}
        for prefix in ("ready_to_rise", "sleepy"):
            fields[vol.Required(f"{prefix}_copy_from", default=DAYS[0])] = _select(
                list(DAYS), "weekday"
            )
            fields[vol.Optional(f"{prefix}_copy_to", default=[])] = _select(
                list(DAYS), "weekday", multiple=True
            )
        return vol.Schema(fields)

    def _alarm_schema(self) -> vol.Schema:
        """Return translated alarm offset and sound selectors."""
        assert self._draft is not None
        alarm = self._draft.get("alarm", {"days": dict.fromkeys(DAYS, 9), "sound": 0})
        fields: dict[vol.Marker, Any] = {
            vol.Required(f"alarm_{day}", default=str(alarm["days"][day])): _select(
                _options(0, 10), "alarm_offset"
            )
            for day in DAYS
        }
        fields[vol.Required("alarm_sound", default=str(alarm["sound"]))] = _select(
            _options(0, 15), "alarm_sound"
        )
        return vol.Schema(fields)

    def _routine_tasks_schema(self) -> vol.Schema:
        """Return twelve ordered task rows plus an explicit no-time toggle."""
        assert self._routine_day is not None
        routine = self._current_routines()[self._routine_day]
        fields: dict[vol.Marker, Any] = {
            vol.Required(
                "routine_has_time", default=routine["time"] is not None
            ): selector.BooleanSelector(),
            vol.Required(
                "routine_time", default=_format_time(routine["time"])
            ): selector.TimeSelector(),
        }
        for index in range(1, MAX_ROUTINE_TASKS + 1):
            fields[vol.Optional(f"task_{index}")] = _select(
                _options(1, 11), "routine_task"
            )
        return self.add_suggested_values_to_schema(
            vol.Schema(fields),
            {
                f"task_{index}": str(slot["task"])
                for index, slot in enumerate(routine["slots"], start=1)
                if slot is not None and slot["task"] != 0
            },
        )
