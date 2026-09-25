"""Config and options flows for Lumalou."""

from __future__ import annotations

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
from homeassistant.helpers import selector
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_AUTO_RESTORE,
    CONF_DEVICE_FINGERPRINT,
    CONF_PROTOCOL_VERIFIED,
    DEFAULT_AUTO_RESTORE,
    DOMAIN,
)
from .models import (
    DAYS,
    ROUTINE_NIBBLE_MAX,
    RevisionConflictError,
    profile_is_complete,
    validate_profile,
)
from .transport import async_read_device_fingerprint

CONF_CONFIRM = "confirm"
MANUFACTURER_ID = 950
MANUFACTURER_PREFIX = b"MB"
MAX_ROUTINE_TASKS = 12
MAX_PLAYLIST_SONGS = 12
EDITORS = (
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


def _read_profile_placeholders(profile: dict[str, Any]) -> dict[str, str]:
    """Summarize a device read as numbers; the sentence itself is translated."""
    routines = profile["routines"].values()
    counts = {
        "song_count": len(profile["playlist"]),
        "wake_count": sum(
            value is not None for value in profile["ready_to_rise"]["times"].values()
        ),
        "bedtime_count": sum(
            value is not None for value in profile["sleepy_times"].values()
        ),
        "alarm_count": sum(value != 9 for value in profile["alarm"]["days"].values()),
        "routine_days": sum(routine["time"] is not None for routine in routines),
        "task_count": sum(
            slot is not None for routine in routines for slot in routine["slots"]
        ),
    }
    return {name: str(value) for name, value in counts.items()}


class LumalouConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Lumalou config flow."""

    VERSION = 1

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
        await self._async_claim(discovery_info)
        self._discovered = discovery_info
        return await self.async_step_bluetooth_confirm()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a discovered connectable Lumalou; selecting it confirms it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info = self._candidates[user_input[CONF_ADDRESS]]
            await self._async_claim(info)
            result, errors = await self._async_create_verified(info)
            if result is not None:
                return result

        self._candidates = self._discovered_candidates()
        if not self._candidates:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user", data_schema=self._address_schema(), errors=errors
        )

    async def _async_claim(self, info: BluetoothServiceInfoBleak) -> None:
        """Deduplicate a candidate before any connection.

        The address is only a provisional unique ID: it stops duplicate
        discovery flows and allows ignoring a candidate. Creating the entry
        replaces it with the signed-device fingerprint.
        """
        await self.async_set_unique_id(
            info.address, raise_on_progress=self.source != config_entries.SOURCE_USER
        )
        self._abort_if_unique_id_configured()
        self._async_abort_entries_match({CONF_ADDRESS: info.address})
        self.context["title_placeholders"] = {"name": _device_title(info)}

    async def _async_create_verified(
        self, info: BluetoothServiceInfoBleak
    ) -> tuple[ConfigFlowResult | None, dict[str, str]]:
        """Verify the user-chosen device's signed identity and create the entry."""
        # Another flow may have configured this address meanwhile.
        self._async_abort_entries_match({CONF_ADDRESS: info.address})
        fingerprint, errors = await self._async_probe(info)
        if fingerprint is None:
            return None, errors
        await self.async_set_unique_id(fingerprint)
        # The same signed device at a new address: follow it, but keep
        # control locked until its profile is read again.
        self._abort_if_unique_id_configured(
            updates={CONF_ADDRESS: info.address, CONF_PROTOCOL_VERIFIED: False}
        )
        return (
            self.async_create_entry(
                title=_device_title(info),
                data=_entry_data(info.address, fingerprint),
                options={CONF_AUTO_RESTORE: DEFAULT_AUTO_RESTORE},
            ),
            {},
        )

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered device before any connection."""
        assert self._discovered is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            result, errors = await self._async_create_verified(self._discovered)
            if result is not None:
                return result

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
        """Rebind an entry to its signed device after an address change."""
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
        """Update the address only when the signed device key matches."""
        if fingerprint != entry.data[CONF_DEVICE_FINGERPRINT]:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._address_schema(),
                errors={"base": "wrong_device"},
            )
        return self.async_update_reload_and_abort(
            entry, data=_entry_data(address, fingerprint)
        )

    async def _async_probe(
        self, info: BluetoothServiceInfoBleak
    ) -> tuple[str | None, dict[str, str]]:
        """Read the signed device key that binds every later session."""
        try:
            fingerprint = await async_read_device_fingerprint(self.hass, info.device)
        except ValueError:
            return None, {"base": "identity_unconfirmed"}
        except Exception:
            return None, {"base": "cannot_connect"}
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


def _nibble_selector() -> selector.NumberSelector:
    """Return a raw 0..15 selector (the readback range), not a guessed enum."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=ROUTINE_NIBBLE_MAX,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
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

    Editors start from a complete profile read from the device. Every change
    is a detached draft of one captured revision and is saved only after an
    explicit confirmation, with a revision check.
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
        """Offer the editors only once a complete profile exists."""
        record = self._record()
        if record is None or not profile_is_complete(record.desired_profile):
            return self.async_show_menu(
                step_id="read_first", menu_options=["read_profile", "behavior"]
            )
        return self.async_show_menu(
            step_id="init", menu_options=["read_profile", *EDITORS, "behavior"]
        )

    # Menu shown instead of "init" until a complete profile exists.
    async_step_read_first = async_step_init

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
            "revision": str(revision),
            **_read_profile_placeholders(snapshot),
        }
        self._pending = ("read_profile_confirm", placeholders, save)
        return await self._async_confirm()

    # Offline block editors.

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
        if reason := self._ensure_draft("routine"):
            return self.async_abort(reason=reason)
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
        if reason := self._ensure_draft(editor or step_id):
            return self.async_abort(reason=reason)
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

        summary = self._edit_summary()
        if "day" in summary:
            summary["day"] = await self._async_day_name(summary["day"])
        self._pending = (f"{self._editor}_confirm", summary, save)
        return await self._async_confirm()

    async def _async_day_name(self, day: str) -> str:
        """Translate a weekday key for a placeholder (the frontend cannot)."""
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "selector", {DOMAIN}
        )
        return translations.get(
            f"component.{DOMAIN}.selector.weekday.options.{day}", day
        )

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
    async_step_playlist_confirm = _async_confirm
    async_step_clock_settings_confirm = _async_confirm
    async_step_routine_settings_confirm = _async_confirm
    async_step_schedule_confirm = _async_confirm
    async_step_routine_confirm = _async_confirm
    async_step_read_profile_confirm = _async_confirm

    def _edit_summary(self) -> dict[str, str]:
        """Return description placeholders for the current editor's preview."""
        assert self._draft is not None
        draft = self._draft
        summary: dict[str, Any] = {"revision": self._revision}
        if self._editor == "playlist":
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

    def _ensure_draft(self, editor: str) -> str | None:
        """Capture one detached complete revision; return an abort reason."""
        if self._draft is None:
            if (record := self._record()) is None:
                return "entry_not_loaded"
            if not profile_is_complete(record.desired_profile):
                return "profile_not_read"
            self._draft = deepcopy(record.desired_profile)
            self._revision = record.revision
            self._editor = editor
        return None

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
        return deepcopy(self._draft["routines"])

    # Form schemas, prefilled from the complete draft.

    def _playlist_schema(self) -> vol.Schema:
        """Return twelve fixed ordered playlist rows, including empty rows."""
        assert self._draft is not None
        playlist = self._draft["playlist"]
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
        clock = self._draft["clock_settings"]
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
        settings = self._draft["routine_settings"]
        return vol.Schema(
            {
                vol.Required(
                    "routine_enabled", default=settings["enabled"]
                ): selector.BooleanSelector(),
                vol.Required("routine_music", default=settings["music"]): (
                    _nibble_selector()
                ),
                vol.Required("routine_volume", default=settings["volume"]): (
                    _nibble_selector()
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
        ready = self._draft["ready_to_rise"]
        sleepy = self._draft["sleepy_times"]
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
        alarm = self._draft["alarm"]
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
