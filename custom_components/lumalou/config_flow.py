"""Config and options flows for Lumalou."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import time
from typing import Any, override

import voluptuous as vol
from bleak.backends.device import BLEDevice
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, FlowType
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_FINGERPRINT,
    CONF_IDENTIFICATION_SOURCE,
    CONF_PROTOCOL_VERIFIED,
    DOMAIN,
    IDENTIFICATION_SOURCE_FACTORY_TOKEN,
    SUPPORTED_PRODUCT_CODE,
)
from .identity import (
    FactoryIdentityLibraryUnavailable,
    FactoryIdentityProbeError,
    async_read_device_information,
    async_read_factory_device_fingerprint,
)
from .models import (
    DAYS,
    ProfileValidationError,
    RevisionConflictError,
    export_profile_payload,
    import_profile_payload,
    validate_profile,
)
from .upstream_api import MissingUpstreamCapabilities, require_factory_identity_api

CONF_AUTO_RESTORE = "auto_restore"
DEFAULT_AUTO_RESTORE = False
MANUFACTURER_ID = 950
MANUFACTURER_PREFIX = b"MB"
MAX_ROUTINE_TASKS = 12
MAX_PLAYLIST_SONGS = 12
CONF_PROFILE_JSON = "profile_json"

_ALARM_OPTIONS = [str(value) for value in range(11)]
_ALARM_SOUND_OPTIONS = [str(value) for value in range(16)]
_ROUTINE_TASK_OPTIONS = [str(value) for value in range(1, 12)]
_BASIC_VALUE_OPTIONS = [str(value) for value in range(10)]
_LIGHT_DURATION_OPTIONS = [str(value) for value in range(6)]
_PLAYLIST_DURATION_OPTIONS = [str(value) for value in range(7)]
_SONG_OPTIONS = [str(value) for value in range(1, 13)]
_CLOCK_FORMAT_OPTIONS = ["0", "1"]


def _is_supported(info: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement is a connectable Lumalou."""
    manufacturer_data = info.manufacturer_data.get(MANUFACTURER_ID, b"")
    return info.connectable and manufacturer_data.startswith(MANUFACTURER_PREFIX)


def _device_title(info: BluetoothServiceInfoBleak) -> str:
    """Build a user-facing title without exposing the numeric BLE name."""
    if info.name and info.name != info.address and not info.name.isdecimal():
        return info.name
    return "Lumalou"


def _read_profile_summary(profile: dict[str, Any]) -> str:
    """Build a compact preview of persistent values without IDs or raw bytes."""
    ready_times = profile["ready_to_rise"]["times"]
    sleepy_times = profile["sleepy_times"]
    routines = profile["routines"]
    wake_count = sum(value is not None for value in ready_times.values())
    wake_midnight = sum(
        value == {"hour": 0, "minute": 0} for value in ready_times.values()
    )
    sleepy_count = sum(value is not None for value in sleepy_times.values())
    sleepy_midnight = sum(
        value == {"hour": 0, "minute": 0} for value in sleepy_times.values()
    )
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
        if any(
            entry.data.get(CONF_ADDRESS) == discovery_info.address
            for entry in self._async_current_entries()
        ):
            return self.async_abort(reason="already_configured")

        self._discovered = discovery_info
        title = _device_title(discovery_info)
        self.context["title_placeholders"] = {"name": title}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Identify a discovered device using read-only authenticated fields."""
        assert self._discovered is not None

        if user_input is not None:
            fingerprint, error = await self._async_resolve_device_fingerprint(
                self._discovered.device,
                _device_title(self._discovered),
            )
            if error is not None:
                return self.async_show_form(
                    step_id="bluetooth_confirm",
                    data_schema=vol.Schema({}),
                    errors=error,
                    description_placeholders=self.context["title_placeholders"],
                )
            assert fingerprint is not None
            await self.async_set_unique_id(fingerprint)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_device_title(self._discovered),
                data={
                    CONF_ADDRESS: self._discovered.address,
                    CONF_DEVICE_FINGERPRINT: fingerprint,
                    CONF_IDENTIFICATION_SOURCE: IDENTIFICATION_SOURCE_FACTORY_TOKEN,
                    CONF_PROTOCOL_VERIFIED: False,
                },
                options={CONF_AUTO_RESTORE: DEFAULT_AUTO_RESTORE},
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self.context["title_placeholders"],
        )

    async def _async_resolve_device_fingerprint(
        self,
        device: BLEDevice | None,
        name: str,
    ) -> tuple[str | None, dict[str, str] | None]:
        """Verify the user-selected signed device key without guessing a SKU."""
        if device is None:
            return None, {"base": "device_unavailable"}
        try:
            require_factory_identity_api()
        except MissingUpstreamCapabilities:
            return None, {"base": "factory_verifier_unavailable"}
        try:
            identity = await async_read_device_information(device, name)
        except Exception:
            return None, {"base": "cannot_connect"}
        detected = (
            identity.model_number.strip().upper() if identity.model_number else ""
        )
        if detected and detected != SUPPORTED_PRODUCT_CODE:
            return None, {"base": "unsupported_product_code"}

        # Device Information is only a conflict check. The signed key binds
        # every later session to the device selected and confirmed by the user.
        try:
            fingerprint = await async_read_factory_device_fingerprint(device, name)
        except FactoryIdentityLibraryUnavailable:
            return None, {"base": "factory_verifier_unavailable"}
        except FactoryIdentityProbeError:
            return None, {"base": "identity_unconfirmed"}
        return fingerprint, None

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

            self._discovered = discovery_info
            self.context["title_placeholders"] = {"name": _device_title(discovery_info)}
            return await self.async_step_bluetooth_confirm()

        configured_addresses = {
            entry.data.get(CONF_ADDRESS) for entry in self._async_current_entries()
        }
        self._discovered_devices = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            if _is_supported(info) and info.address not in configured_addresses
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
        """Rebind an entry to a user-selected, signed device after address changes."""
        from homeassistant.components import bluetooth

        entry = self._get_reconfigure_entry()
        other_entries = [
            other
            for other in self._async_current_entries()
            if other.entry_id != entry.entry_id
        ]
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            selected = self._discovered_devices.get(address)
            if selected is None:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=self._reconfigure_schema(),
                    errors={"base": "device_unavailable"},
                )
            if any(other.data.get(CONF_ADDRESS) == address for other in other_entries):
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=self._reconfigure_schema(),
                    errors={"base": "already_configured"},
                )
            # Resolve the selected address again through HA so an old form
            # cannot retain a stale or no longer connectable BLEDevice.
            live = next(
                (
                    info
                    for info in bluetooth.async_discovered_service_info(
                        self.hass, connectable=True
                    )
                    if info.address == address and _is_supported(info)
                ),
                None,
            )
            if live is None:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=self._reconfigure_schema(),
                    errors={"base": "device_unavailable"},
                )
            fingerprint, error = await self._async_resolve_device_fingerprint(
                live.device,
                _device_title(live),
            )
            if error is None:
                assert fingerprint is not None
                enrolled = entry.data.get(CONF_DEVICE_FINGERPRINT)
                if enrolled is None and entry.unique_id != entry.data.get(CONF_ADDRESS):
                    # Older entries use the address as unique ID. A migrated
                    # entry may already carry its signed key as unique ID.
                    enrolled = entry.unique_id
                if enrolled is not None and fingerprint != enrolled:
                    error = {"base": "identity_unconfirmed"}
                elif any(
                    other.unique_id == fingerprint
                    or other.data.get(CONF_DEVICE_FINGERPRINT) == fingerprint
                    or other.data.get(CONF_ADDRESS) == address
                    for other in self._async_current_entries()
                    if other.entry_id != entry.entry_id
                ):
                    error = {"base": "already_configured"}
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=fingerprint,
                    data={
                        CONF_ADDRESS: address,
                        CONF_DEVICE_FINGERPRINT: fingerprint,
                        CONF_IDENTIFICATION_SOURCE: IDENTIFICATION_SOURCE_FACTORY_TOKEN,
                        CONF_PROTOCOL_VERIFIED: False,
                    },
                )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._reconfigure_schema(),
                errors=error,
            )

        self._discovered_devices = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            if _is_supported(info)
            and not any(
                other.data.get(CONF_ADDRESS) == info.address for other in other_entries
            )
        }
        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._reconfigure_schema()
        )

    def _reconfigure_schema(self) -> vol.Schema:
        """List discovered candidates without revealing advertising serials."""
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        address: f"{_device_title(info)} {index}"
                        for index, (address, info) in enumerate(
                            self._discovered_devices.items(), start=1
                        )
                    }
                )
            }
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
        self._import_payload: dict[str, Any] | None = None
        self._import_profile: dict[str, Any] | None = None
        self._import_revision: int | None = None
        self._import_removed_fields: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the behavior or one private profile editor."""
        # Keep compatibility with the original single-step options submission.
        if user_input is not None and CONF_AUTO_RESTORE in user_input:
            return await self.async_step_behavior(user_input)
        if self._has_no_desired_profile():
            return self.async_show_menu(
                step_id="init",
                menu_options=("create", "import_profile", "read_profile", "behavior"),
            )
        return self.async_show_menu(
            step_id="init",
            menu_options=(
                "behavior",
                "basic",
                "playlist",
                "clock_settings",
                "routine_settings",
                "schedule",
                "routine",
                "import_profile",
                "read_profile",
            ),
        )

    async def async_step_create(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an offline editor for a new, initially empty profile."""
        return self.async_show_menu(
            step_id="create",
            menu_options=(
                "basic",
                "playlist",
                "clock_settings",
                "routine_settings",
                "schedule",
                "routine",
            ),
        )

    async def async_step_import_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate an exported profile before showing an explicit preview."""
        if user_input is not None:
            try:
                raw_payload = json.loads(user_input[CONF_PROFILE_JSON])
                if not isinstance(raw_payload, dict):
                    raise ValueError("Profile export must be a JSON object")
                # The export action includes the revision alongside the actual
                # schema envelope. The current entry's captured revision is
                # deliberately used for CAS instead of trusting this backup's
                # revision from another device or point in time.
                if set(raw_payload) == {"current_revision", "profile"} and isinstance(
                    raw_payload["profile"], dict
                ):
                    raw_payload = raw_payload["profile"]
                desired = import_profile_payload(raw_payload)
                record = self._profile_record()
                if record is None:
                    return self.async_abort(reason="entry_not_loaded")
            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                return self.async_show_form(
                    step_id="import_profile",
                    data_schema=self._import_schema(),
                    errors={"base": "invalid_profile_import"},
                )
            self._import_payload = deepcopy(raw_payload)
            self._import_profile = deepcopy(desired)
            self._import_revision = record.revision
            self._import_removed_fields = sorted(
                set(record.desired_profile) - set(desired)
            )
            return await self.async_step_import_profile_confirm()

        return self.async_show_form(
            step_id="import_profile", data_schema=self._import_schema()
        )

    async def async_step_import_profile_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save a previously previewed import using the captured CAS revision."""
        if (
            self._import_payload is None
            or self._import_profile is None
            or self._import_revision is None
        ):
            return self.async_abort(reason="profile_import_unavailable")

        if user_input is not None:
            if not user_input["confirm"]:
                return self._import_confirm_form(
                    errors={"confirm": "confirmation_required"}
                )
            coordinator = self._coordinator()
            if coordinator is None:
                return self.async_abort(reason="entry_not_loaded")
            try:
                await coordinator.async_import_profile(
                    deepcopy(self._import_payload),
                    self._import_revision,
                    confirmed=True,
                )
            except RevisionConflictError:
                return self._import_confirm_form(errors={"base": "revision_conflict"})
            except HomeAssistantError, ProfileValidationError:
                return self._import_confirm_form(
                    errors={"base": "profile_import_failed"}
                )
            return self.async_create_entry(data=dict(self.config_entry.options))

        return self._import_confirm_form()

    async def async_step_read_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read one complete fresh profile, then show a CAS-protected preview."""
        coordinator = self._coordinator()
        record = self._profile_record()
        if coordinator is None or record is None:
            return self.async_abort(reason="entry_not_loaded")
        try:
            (
                snapshot,
                expected_revision,
            ) = await coordinator.async_read_profile_snapshot()
            self._import_payload = export_profile_payload(snapshot)
        except HomeAssistantError, ProfileValidationError, ValueError:
            return self.async_abort(reason="profile_read_failed")

        self._import_profile = deepcopy(snapshot)
        self._import_revision = expected_revision
        self._import_removed_fields = sorted(
            set(record.desired_profile) - set(snapshot)
        )
        return await self.async_step_read_profile_confirm()

    async def async_step_read_profile_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace saved intent only after the user confirms a full fresh read."""
        if (
            self._import_payload is None
            or self._import_profile is None
            or self._import_revision is None
        ):
            return self.async_abort(reason="profile_read_failed")
        if user_input is not None:
            if not user_input["confirm"]:
                return self._read_profile_confirm_form(
                    errors={"confirm": "confirmation_required"}
                )
            coordinator = self._coordinator()
            if coordinator is None:
                return self.async_abort(reason="entry_not_loaded")
            try:
                await coordinator.async_accept_device_profile(
                    deepcopy(self._import_profile),
                    self._import_revision,
                    confirmed=True,
                )
            except RevisionConflictError:
                return self._read_profile_confirm_form(
                    errors={"base": "revision_conflict"}
                )
            except HomeAssistantError, ProfileValidationError, ValueError:
                return self._read_profile_confirm_form(
                    errors={"base": "profile_import_failed"}
                )
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self._read_profile_confirm_form()

    def _read_profile_confirm_form(
        self, *, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show snapshot completeness and saved revision without device secrets."""
        assert self._import_payload is not None
        assert self._import_profile is not None
        assert self._import_revision is not None
        return self.async_show_form(
            step_id="read_profile_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            errors=errors or {},
            description_placeholders={
                "field_count": str(len(self._import_profile)),
                "revision": str(self._import_revision),
                "summary": _read_profile_summary(self._import_profile),
            },
            last_step=True,
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

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit private light and audio values as one draft."""
        if not self._ensure_profile_draft("basic"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                basic = {
                    "brightness": int(user_input["brightness"]),
                    "color": int(user_input["color"]),
                    "light_duration": int(user_input["light_duration"]),
                    "volume": int(user_input["volume"]),
                    "playlist_duration": int(user_input["playlist_duration"]),
                }
                validate_profile(basic)
            except KeyError, ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="basic",
                    data_schema=self._basic_schema(),
                    errors={"base": "invalid_basic"},
                )
            self._draft_profile.update(basic)
            self._changes.update(deepcopy(basic))
            return await self.async_step_confirm()

        return self.async_show_form(step_id="basic", data_schema=self._basic_schema())

    async def async_step_playlist(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit an ordered, duplicate-preserving private playlist draft."""
        if not self._ensure_profile_draft("playlist"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                playlist = [
                    int(user_input[f"song_{index}"])
                    for index in range(1, MAX_PLAYLIST_SONGS + 1)
                    if f"song_{index}" in user_input
                ]
                validate_profile({"playlist": playlist})
            except ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="playlist",
                    data_schema=self._playlist_schema(),
                    errors={"base": "invalid_playlist"},
                )
            self._draft_profile["playlist"] = playlist
            self._changes["playlist"] = deepcopy(playlist)
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="playlist", data_schema=self._playlist_schema()
        )

    async def async_step_clock_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit a complete private clock settings block."""
        if not self._ensure_profile_draft("clock_settings"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                clock_settings = {
                    "display": user_input["clock_display"],
                    "brightness": int(user_input["clock_brightness"]),
                    "format": int(user_input["clock_format"]),
                }
                validate_profile({"clock_settings": clock_settings})
            except KeyError, ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="clock_settings",
                    data_schema=self._clock_settings_schema(),
                    errors={"base": "invalid_clock_settings"},
                )
            self._draft_profile["clock_settings"] = clock_settings
            self._changes["clock_settings"] = deepcopy(clock_settings)
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="clock_settings", data_schema=self._clock_settings_schema()
        )

    async def async_step_routine_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit a complete private routine settings block."""
        if not self._ensure_profile_draft("routine_settings"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                routine_settings = {
                    "enabled": user_input["routine_enabled"],
                    "music": self._integer_input(user_input["routine_music"]),
                    "volume": self._integer_input(user_input["routine_volume"]),
                    "task_reward_sfx": int(user_input["task_reward_sfx"]),
                    "routine_reward_sfx": int(user_input["routine_reward_sfx"]),
                }
                validate_profile({"routine_settings": routine_settings})
            except KeyError, ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="routine_settings",
                    data_schema=self._routine_settings_schema(),
                    errors={"base": "invalid_routine_settings"},
                )
            self._draft_profile["routine_settings"] = routine_settings
            self._changes["routine_settings"] = deepcopy(routine_settings)
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="routine_settings", data_schema=self._routine_settings_schema()
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
            return await self.async_step_schedule_copy()

        return self.async_show_form(
            step_id="schedule", data_schema=self._schedule_schema()
        )

    async def async_step_schedule_copy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally copy each weekly time to selected days."""
        if not self._ensure_profile_draft("schedule"):
            return self.async_abort(reason="profile_editor_unavailable")
        assert self._draft_profile is not None

        if user_input is not None:
            try:
                ready_source = user_input["ready_to_rise_copy_from"]
                sleepy_source = user_input["sleepy_copy_from"]
                ready_targets = self._schedule_copy_targets(
                    user_input.get("ready_to_rise_copy_to", []), ready_source
                )
                sleepy_targets = self._schedule_copy_targets(
                    user_input.get("sleepy_copy_to", []), sleepy_source
                )
                ready_to_rise = deepcopy(self._draft_profile["ready_to_rise"])
                sleepy_times = deepcopy(self._draft_profile["sleepy_times"])
                for day in ready_targets:
                    ready_to_rise["times"][day] = deepcopy(
                        ready_to_rise["times"][ready_source]
                    )
                for day in sleepy_targets:
                    sleepy_times[day] = deepcopy(sleepy_times[sleepy_source])
                validate_profile(
                    {
                        "ready_to_rise": ready_to_rise,
                        "sleepy_times": sleepy_times,
                    }
                )
            except KeyError, ProfileValidationError, TypeError, ValueError:
                return self.async_show_form(
                    step_id="schedule_copy",
                    data_schema=self._schedule_copy_schema(),
                    errors={"base": "invalid_schedule_copy"},
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
            step_id="schedule_copy", data_schema=self._schedule_copy_schema()
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

    async def async_step_basic_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the shared confirmation step for light and audio values."""
        return await self.async_step_confirm(user_input)

    async def async_step_playlist_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the shared confirmation step for a playlist."""
        return await self.async_step_confirm(user_input)

    async def async_step_clock_settings_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the shared confirmation step for clock settings."""
        return await self.async_step_confirm(user_input)

    async def async_step_routine_settings_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the shared confirmation step for routine settings."""
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
        elif self._editor == "routine":
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
        elif self._editor == "basic":
            placeholders.update(
                {
                    name: str(self._draft_profile[name])
                    for name in (
                        "brightness",
                        "color",
                        "light_duration",
                        "volume",
                        "playlist_duration",
                    )
                }
            )
        elif self._editor == "playlist":
            playlist = self._draft_profile["playlist"]
            placeholders.update(
                {
                    "song_count": str(len(playlist)),
                    "songs": ", ".join(map(str, playlist)) or "—",
                }
            )
        elif self._editor == "clock_settings":
            clock = self._draft_profile["clock_settings"]
            placeholders.update(
                {
                    "display": str(clock["display"]),
                    "brightness": str(clock["brightness"]),
                    "format": str(clock["format"]),
                }
            )
        elif self._editor == "routine_settings":
            routine_settings = self._draft_profile["routine_settings"]
            placeholders.update(
                {name: str(value) for name, value in routine_settings.items()}
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
        record = self._profile_record()
        if record is None:
            return False
        self._draft_profile = deepcopy(record.desired_profile)
        self._expected_revision = record.revision
        self._editor = editor
        return True

    def _has_no_desired_profile(self) -> bool:
        """Return whether this entry needs its first profile-source choice.

        An unloaded entry cannot safely expose a stored profile.  Treat it as
        requiring a source choice; actions that need the private Store then
        clearly abort instead of inventing default values.
        """
        record = self._profile_record()
        return record is None or not record.desired_profile

    def _profile_record(self) -> Any | None:
        """Get the loaded immutable profile record without touching Bluetooth."""
        coordinator = self._coordinator()
        if coordinator is None:
            return None
        try:
            return coordinator.profile_record
        except AttributeError, RuntimeError:
            return None

    def _coordinator(self) -> Any | None:
        """Return the loaded coordinator, never creating one from a flow."""
        try:
            return self.config_entry.runtime_data.coordinator
        except AttributeError, RuntimeError:
            return None

    def _import_schema(self) -> vol.Schema:
        """Request an export envelope as multiline JSON, not entry options."""
        return vol.Schema(
            {
                vol.Required(CONF_PROFILE_JSON): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                )
            }
        )

    def _import_confirm_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show the validated import preview before the only Store mutation."""
        assert self._import_payload is not None
        assert self._import_profile is not None
        assert self._import_revision is not None
        return self.async_show_form(
            step_id="import_profile_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            errors=errors,
            description_placeholders={
                "schema_version": str(self._import_payload["schema_version"]),
                "scope": str(self._import_payload["scope"]),
                "field_count": str(len(self._import_profile)),
                "revision": str(self._import_revision),
                "removed_count": str(len(self._import_removed_fields)),
                "removed_fields": ", ".join(self._import_removed_fields) or "—",
            },
            last_step=True,
        )

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

    def _schedule_copy_schema(self) -> vol.Schema:
        """Return source and multi-day target pickers for weekly times."""
        fields: dict[vol.Marker, Any] = {}
        for prefix in ("ready_to_rise", "sleepy"):
            fields[vol.Required(f"{prefix}_copy_from", default=DAYS[0])] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(DAYS), translation_key="weekday"
                    )
                )
            )
            fields[vol.Optional(f"{prefix}_copy_to", default=[])] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(DAYS),
                        multiple=True,
                        translation_key="weekday",
                    )
                )
            )
        return vol.Schema(fields)

    def _basic_schema(self) -> vol.Schema:
        """Return native selectors for private light and audio values."""
        assert self._draft_profile is not None
        fields: dict[vol.Marker, Any] = {}
        for name, options in (
            ("brightness", _BASIC_VALUE_OPTIONS),
            ("color", _BASIC_VALUE_OPTIONS),
            ("light_duration", _LIGHT_DURATION_OPTIONS),
            ("volume", _BASIC_VALUE_OPTIONS),
            ("playlist_duration", _PLAYLIST_DURATION_OPTIONS),
        ):
            marker = vol.Required(name, default=str(self._draft_profile.get(name, 0)))
            fields[marker] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=options)
            )
        return vol.Schema(fields)

    def _playlist_schema(self) -> vol.Schema:
        """Return twelve fixed ordered playlist rows, including empty rows."""
        assert self._draft_profile is not None
        playlist = self._draft_profile.get("playlist", [])
        suggested_values = {
            f"song_{index}": str(song) for index, song in enumerate(playlist, start=1)
        }
        fields: dict[vol.Marker, Any] = {}
        for index in range(1, MAX_PLAYLIST_SONGS + 1):
            fields[vol.Optional(f"song_{index}")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=_SONG_OPTIONS)
            )
        return self.add_suggested_values_to_schema(vol.Schema(fields), suggested_values)

    def _clock_settings_schema(self) -> vol.Schema:
        """Return native controls for one complete clock settings block."""
        assert self._draft_profile is not None
        clock = self._draft_profile.get(
            "clock_settings", {"display": False, "brightness": 0, "format": 0}
        )
        return vol.Schema(
            {
                vol.Required("clock_display", default=clock["display"]): (
                    selector.BooleanSelector()
                ),
                vol.Required("clock_brightness", default=str(clock["brightness"])): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(options=_BASIC_VALUE_OPTIONS)
                    )
                ),
                vol.Required("clock_format", default=str(clock["format"])): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(options=_CLOCK_FORMAT_OPTIONS)
                    )
                ),
            }
        )

    def _routine_settings_schema(self) -> vol.Schema:
        """Return native controls for one complete routine settings block."""
        assert self._draft_profile is not None
        routine_settings = self._draft_profile.get(
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
                vol.Required("routine_enabled", default=routine_settings["enabled"]): (
                    selector.BooleanSelector()
                ),
                vol.Required("routine_music", default=routine_settings["music"]): (
                    self._byte_selector()
                ),
                vol.Required("routine_volume", default=routine_settings["volume"]): (
                    self._byte_selector()
                ),
                vol.Required(
                    "task_reward_sfx", default=str(routine_settings["task_reward_sfx"])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=_ALARM_SOUND_OPTIONS)
                ),
                vol.Required(
                    "routine_reward_sfx",
                    default=str(routine_settings["routine_reward_sfx"]),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=_ALARM_SOUND_OPTIONS)
                ),
            }
        )

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

    @staticmethod
    def _schedule_copy_targets(value: Any, source: str) -> list[str]:
        """Validate and normalize selected copy targets, excluding the source."""
        if source not in DAYS or not isinstance(value, list):
            raise ValueError("Unsupported schedule copy selection")
        targets: list[str] = []
        for day in value:
            if day not in DAYS:
                raise ValueError("Unsupported schedule copy target")
            if day != source and day not in targets:
                targets.append(day)
        return targets

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
    def _byte_selector() -> selector.NumberSelector:
        """Return an integer byte selector instead of a guessed music enum."""
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=255,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        )

    @staticmethod
    def _integer_input(value: Any) -> int:
        """Accept selector floats only when they exactly represent an integer."""
        if type(value) not in (int, float) or int(value) != value:
            raise ValueError("Value must be an integer")
        return int(value)

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
