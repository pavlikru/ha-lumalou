"""Config and options flows for Lumalou."""

from __future__ import annotations

from copy import deepcopy
from datetime import time
from typing import Any, override

import voluptuous as vol
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import CONF_PRODUCT_CODE, DOMAIN, SUPPORTED_PRODUCT_CODE
from .models import (
    DAYS,
    ProfileValidationError,
    RevisionConflictError,
    validate_profile,
)

CONF_AUTO_RESTORE = "auto_restore"
DEFAULT_AUTO_RESTORE = False
MANUFACTURER_ID = 950
MANUFACTURER_PREFIX = b"MB"
MAX_ROUTINE_TASKS = 12

_ALARM_OPTIONS = [str(value) for value in range(11)]
_ALARM_SOUND_OPTIONS = [str(value) for value in range(16)]
_ROUTINE_TASK_OPTIONS = [str(value) for value in range(1, 12)]


def _is_supported(info: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement is a connectable Lumalou."""
    manufacturer_data = info.manufacturer_data.get(MANUFACTURER_ID, b"")
    return info.connectable and manufacturer_data.startswith(MANUFACTURER_PREFIX)


def _device_title(info: BluetoothServiceInfoBleak) -> str:
    """Build a user-facing discovery title."""
    if info.name and info.name != info.address:
        return info.name
    return "Lumalou"


def _normalize_product_code(value: Any) -> str:
    """Normalize user-confirmed label text without guessing compatibility."""
    return value.strip().upper() if isinstance(value, str) else ""


def _product_code_schema() -> vol.Schema:
    """Require an explicit product-code transcription from the device label."""
    return vol.Schema({vol.Required(CONF_PRODUCT_CODE): str})


class LumalouConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Lumalou config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

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

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovered = discovery_info
        title = _device_title(discovery_info)
        self.context["title_placeholders"] = {"name": title}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered Lumalou and its supported product label."""
        assert self._discovered is not None

        if user_input is not None:
            product_code = _normalize_product_code(user_input[CONF_PRODUCT_CODE])
            if product_code != SUPPORTED_PRODUCT_CODE:
                return self.async_show_form(
                    step_id="bluetooth_confirm",
                    data_schema=_product_code_schema(),
                    errors={CONF_PRODUCT_CODE: "unsupported_product_code"},
                    description_placeholders=self.context["title_placeholders"],
                )
            return self.async_create_entry(
                title=_device_title(self._discovered),
                data={
                    CONF_ADDRESS: self._discovered.address,
                    CONF_PRODUCT_CODE: product_code,
                },
                options={CONF_AUTO_RESTORE: DEFAULT_AUTO_RESTORE},
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=_product_code_schema(),
            description_placeholders=self.context["title_placeholders"],
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a currently discovered connectable Lumalou."""
        from homeassistant.components import bluetooth

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices.get(address)
            if discovery_info is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._user_schema(),
                    errors={"base": "device_unavailable"},
                )

            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._discovered = discovery_info
            self.context["title_placeholders"] = {"name": _device_title(discovery_info)}
            return await self.async_step_bluetooth_confirm()

        configured_ids = self._async_current_ids(include_ignore=False)
        self._discovered_devices = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            if _is_supported(info) and info.address not in configured_ids
        }
        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(step_id="user", data_schema=self._user_schema())

    def _user_schema(self) -> vol.Schema:
        """Return the manual device-picker schema."""
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        address: _device_title(info)
                        for address, info in self._discovered_devices.items()
                    }
                )
            }
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let legacy entries explicitly confirm the supported product code."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            product_code = _normalize_product_code(user_input[CONF_PRODUCT_CODE])
            if product_code == SUPPORTED_PRODUCT_CODE:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PRODUCT_CODE: product_code}
                )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_product_code_schema(),
                errors={CONF_PRODUCT_CODE: "unsupported_product_code"},
            )

        return self.async_show_form(
            step_id="reconfigure", data_schema=_product_code_schema()
        )


class LumalouOptionsFlow(config_entries.OptionsFlow):
    """Edit behavior options and a private profile draft."""

    def __init__(self) -> None:
        """Initialize an isolated profile draft."""
        self._draft_profile: dict[str, Any] | None = None
        self._expected_revision: int | None = None
        self._changes: dict[str, Any] = {}
        self._editor: str | None = None
        self._routine_day: str | None = None
        self._copied_days: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the behavior, schedule, or routine editor."""
        # Keep compatibility with the original single-step options submission.
        if user_input is not None and CONF_AUTO_RESTORE in user_input:
            return await self.async_step_behavior(user_input)
        return self.async_show_menu(
            step_id="init", menu_options=("behavior", "schedule", "routine")
        )

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure opt-in behavior."""
        if user_input is not None:
            if user_input[CONF_AUTO_RESTORE]:
                return self.async_show_form(
                    step_id="behavior",
                    data_schema=self._options_schema(),
                    errors={CONF_AUTO_RESTORE: "auto_restore_unavailable"},
                )
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="behavior", data_schema=self._options_schema(), last_step=True
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit both weekly time blocks in one draft."""
        if not self._ensure_profile_draft("schedule"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                ready_to_rise = {
                    "enabled": user_input["ready_to_rise_enabled"],
                    "times": self._week_from_input(user_input, "ready_to_rise"),
                }
                sleepy_times = self._week_from_input(user_input, "sleepy")
                validate_profile(
                    {
                        "ready_to_rise": ready_to_rise,
                        "sleepy_times": sleepy_times,
                    }
                )
            except KeyError, ProfileValidationError, ValueError:
                return self.async_show_form(
                    step_id="schedule",
                    data_schema=self._schedule_schema(),
                    errors={"base": "invalid_schedule"},
                )
            self._draft_profile["ready_to_rise"] = ready_to_rise
            self._draft_profile["sleepy_times"] = sleepy_times
            self._changes.update(
                {
                    "ready_to_rise": deepcopy(ready_to_rise),
                    "sleepy_times": deepcopy(sleepy_times),
                }
            )
            return await self.async_step_schedule_alarm()

        return self.async_show_form(
            step_id="schedule", data_schema=self._schedule_schema()
        )

    async def async_step_schedule_alarm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit weekly alarm offsets and sound."""
        if not self._ensure_profile_draft("schedule"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                alarm = {
                    "days": {day: int(user_input[f"alarm_{day}"]) for day in DAYS},
                    "sound": int(user_input["alarm_sound"]),
                }
                validate_profile({"alarm": alarm})
            except KeyError, ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="schedule_alarm",
                    data_schema=self._alarm_schema(),
                    errors={"base": "invalid_alarm"},
                )
            self._draft_profile["alarm"] = alarm
            self._changes["alarm"] = deepcopy(alarm)
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="schedule_alarm", data_schema=self._alarm_schema()
        )

    async def async_step_routine(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one daily routine to edit."""
        if not self._ensure_profile_draft("routine"):
            return self.async_abort(reason="profile_editor_unavailable")
        if user_input is not None:
            self._routine_day = user_input["routine_day"]
            return await self.async_step_routine_tasks()

        return self.async_show_form(
            step_id="routine", data_schema=self._routine_day_schema()
        )

    async def async_step_routine_tasks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit fixed ordered task rows without exposing task zero."""
        if not self._ensure_profile_draft("routine"):
            return self.async_abort(reason="profile_editor_unavailable")
        if self._routine_day is None:
            return await self.async_step_routine()
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                routine = self._routine_from_input(user_input)
                validate_profile(
                    {"routines": self._routines_with(self._routine_day, routine)}
                )
            except KeyError, ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="routine_tasks",
                    data_schema=self._routine_tasks_schema(),
                    errors={"base": "invalid_routine"},
                )
            routines = self._current_routines()
            routines[self._routine_day] = routine
            self._draft_profile["routines"] = routines
            self._changes["routines"] = deepcopy(routines)
            return await self.async_step_routine_copy()

        return self.async_show_form(
            step_id="routine_tasks", data_schema=self._routine_tasks_schema()
        )

    async def async_step_routine_copy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally copy the edited routine to independent day values."""
        if not self._ensure_profile_draft("routine"):
            return self.async_abort(reason="profile_editor_unavailable")
        if self._routine_day is None:
            return await self.async_step_routine()
        assert self._draft_profile is not None

        if user_input is not None:
            routines = self._current_routines()
            source = routines[self._routine_day]
            self._copied_days = [
                day for day in user_input.get("copy_to", []) if day != self._routine_day
            ]
            for day in self._copied_days:
                routines[day] = deepcopy(source)
            self._draft_profile["routines"] = routines
            self._changes["routines"] = deepcopy(routines)
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="routine_copy", data_schema=self._routine_copy_schema()
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a summary and atomically save only after confirmation."""
        if self._draft_profile is None or self._expected_revision is None:
            return self.async_abort(reason="profile_editor_unavailable")

        if user_input is not None:
            if not user_input["confirm"]:
                return self._confirm_form(errors={"confirm": "confirmation_required"})
            try:
                coordinator = self.config_entry.runtime_data.coordinator
            except AttributeError, RuntimeError:
                return self.async_abort(reason="profile_editor_unavailable")
            try:
                await coordinator.async_edit_profile(
                    deepcopy(self._changes), self._expected_revision
                )
            except RevisionConflictError:
                return self._confirm_form(errors={"base": "revision_conflict"})
            except HomeAssistantError, ProfileValidationError:
                return self._confirm_form(errors={"base": "profile_save_failed"})
            return self.async_create_entry(data=dict(self.config_entry.options))

        return self._confirm_form()

    async def async_step_schedule_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the shared confirmation step for schedules."""
        return await self.async_step_confirm(user_input)

    async def async_step_routine_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the shared confirmation step for routines."""
        return await self.async_step_confirm(user_input)

    def _confirm_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        """Build the final confirmation form and summary values."""
        assert self._draft_profile is not None
        assert self._expected_revision is not None
        placeholders = {"revision": str(self._expected_revision)}
        if self._editor == "schedule":
            ready = self._draft_profile["ready_to_rise"]["times"]
            sleepy = self._draft_profile["sleepy_times"]
            alarm = self._draft_profile["alarm"]
            placeholders.update(
                {
                    "ready_count": str(
                        sum(value is not None for value in ready.values())
                    ),
                    "sleepy_count": str(
                        sum(value is not None for value in sleepy.values())
                    ),
                    "alarm_count": str(
                        sum(value != 9 for value in alarm["days"].values())
                    ),
                    "alarm_sound": str(alarm["sound"] + 1),
                }
            )
        else:
            assert self._routine_day is not None
            routine = self._draft_profile["routines"][self._routine_day]
            placeholders.update(
                {
                    "day": self._routine_day,
                    "task_count": str(
                        sum(slot is not None for slot in routine["slots"])
                    ),
                    "copy_count": str(len(self._copied_days)),
                    "zero_count": str(
                        sum(
                            slot is not None and slot["task"] == 0
                            for slot in routine["slots"]
                        )
                    ),
                }
            )
        return self.async_show_form(
            step_id=f"{self._editor}_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            errors=errors,
            description_placeholders=placeholders,
            last_step=True,
        )

    def _ensure_profile_draft(self, editor: str) -> bool:
        """Capture one detached profile revision for this flow."""
        if self._draft_profile is not None:
            return self._editor == editor
        try:
            record = self.config_entry.runtime_data.coordinator.profile_record
        except AttributeError, RuntimeError:
            return False
        self._draft_profile = deepcopy(record.desired_profile)
        self._expected_revision = record.revision
        self._editor = editor
        return True

    def _schedule_schema(self) -> vol.Schema:
        """Return native controls for both seven-day time blocks."""
        assert self._draft_profile is not None
        ready = self._draft_profile.get(
            "ready_to_rise",
            {"enabled": False, "times": {day: None for day in DAYS}},
        )
        sleepy = self._draft_profile.get("sleepy_times", {day: None for day in DAYS})
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
                    vol.Required(
                        f"{prefix}_{day}_time", default=self._format_time(value)
                    )
                ] = selector.TimeSelector()
        return vol.Schema(fields)

    def _alarm_schema(self) -> vol.Schema:
        """Return translated alarm offset and sound selectors."""
        assert self._draft_profile is not None
        alarm = self._draft_profile.get(
            "alarm", {"days": {day: 9 for day in DAYS}, "sound": 0}
        )
        fields: dict[vol.Marker, Any] = {}
        for day in DAYS:
            fields[vol.Required(f"alarm_{day}", default=str(alarm["days"][day]))] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_ALARM_OPTIONS, translation_key="alarm_offset"
                    )
                )
            )
        fields[vol.Required("alarm_sound", default=str(alarm["sound"]))] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_ALARM_SOUND_OPTIONS, translation_key="alarm_sound"
                )
            )
        )
        return vol.Schema(fields)

    def _routine_day_schema(self) -> vol.Schema:
        """Return a translated day picker."""
        return vol.Schema(
            {
                vol.Required("routine_day", default=DAYS[0]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(DAYS), translation_key="weekday"
                    )
                )
            }
        )

    def _routine_tasks_schema(self) -> vol.Schema:
        """Return twelve ordered task rows plus an explicit no-time toggle."""
        assert self._routine_day is not None
        routine = self._current_routines()[self._routine_day]
        suggested_values: dict[str, str] = {}
        fields: dict[vol.Marker, Any] = {
            vol.Required("routine_has_time", default=routine["time"] is not None): (
                selector.BooleanSelector()
            ),
            vol.Required(
                "routine_time", default=self._format_time(routine["time"])
            ): selector.TimeSelector(),
        }
        for index, slot in enumerate(routine["slots"], start=1):
            if slot is not None and slot["task"] != 0:
                suggested_values[f"task_{index}"] = str(slot["task"])
            marker = vol.Optional(f"task_{index}")
            fields[marker] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_ROUTINE_TASK_OPTIONS,
                    translation_key="routine_task",
                )
            )
        return self.add_suggested_values_to_schema(vol.Schema(fields), suggested_values)

    def _routine_copy_schema(self) -> vol.Schema:
        """Return a multi-day copy target picker."""
        assert self._routine_day is not None
        return vol.Schema(
            {
                vol.Optional("copy_to", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[day for day in DAYS if day != self._routine_day],
                        multiple=True,
                        translation_key="weekday",
                    )
                )
            }
        )

    def _week_from_input(
        self, user_input: dict[str, Any], prefix: str
    ) -> dict[str, dict[str, int] | None]:
        """Decode toggled time selectors without conflating null and midnight."""
        return {
            day: (
                self._parse_time(user_input[f"{prefix}_{day}_time"])
                if user_input[f"{prefix}_{day}_has_time"]
                else None
            )
            for day in DAYS
        }

    def _routine_from_input(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Build twelve strict slots while retaining unknown task-zero rows."""
        assert self._routine_day is not None
        current = self._current_routines()[self._routine_day]
        slots: list[dict[str, int] | None] = []
        for index in range(1, MAX_ROUTINE_TASKS + 1):
            existing = current["slots"][index - 1]
            selected = user_input.get(f"task_{index}")
            if selected is None:
                slots.append(
                    deepcopy(existing)
                    if existing is not None and existing["task"] == 0
                    else None
                )
                continue
            task = int(selected)
            if task not in range(1, 12):
                raise ValueError("Unsupported routine task")
            step = existing["step"] if existing is not None else index
            slots.append({"step": step, "task": task})
        return {
            "time": (
                self._parse_time(user_input["routine_time"])
                if user_input["routine_has_time"]
                else None
            ),
            "slots": slots,
        }

    def _current_routines(self) -> dict[str, dict[str, Any]]:
        """Return a detached complete seven-day routine block."""
        assert self._draft_profile is not None
        routines = self._draft_profile.get("routines")
        if routines is None:
            routines = {
                day: {"time": None, "slots": [None] * MAX_ROUTINE_TASKS} for day in DAYS
            }
        return deepcopy(routines)

    def _routines_with(
        self, day: str, routine: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        routines = self._current_routines()
        routines[day] = routine
        return routines

    @staticmethod
    def _parse_time(value: Any) -> dict[str, int]:
        """Parse a minute-resolution native time selector value."""
        if not isinstance(value, str):
            raise ValueError("Invalid time")
        parsed = time.fromisoformat(value)
        if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
            raise ValueError("Time must have minute resolution")
        return {"hour": parsed.hour, "minute": parsed.minute}

    @staticmethod
    def _format_time(value: dict[str, int] | None) -> str:
        """Format a profile time for the native selector."""
        if value is None:
            return "00:00:00"
        return f"{value['hour']:02d}:{value['minute']:02d}:00"

    def _options_schema(self) -> vol.Schema:
        """Return options while keeping unavailable restore disabled."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_AUTO_RESTORE,
                    default=self.config_entry.options.get(
                        CONF_AUTO_RESTORE, DEFAULT_AUTO_RESTORE
                    ),
                ): bool
            }
        )
