"""Config and options flows for Lumalou."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import CONF_PRODUCT_CODE, DOMAIN, SUPPORTED_PRODUCT_CODE

CONF_AUTO_RESTORE = "auto_restore"
DEFAULT_AUTO_RESTORE = False
MANUFACTURER_ID = 950
MANUFACTURER_PREFIX = b"MB"


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


class LumalouOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle Lumalou behavior options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure opt-in behavior."""
        if user_input is not None:
            if user_input[CONF_AUTO_RESTORE]:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._options_schema(),
                    errors={CONF_AUTO_RESTORE: "auto_restore_unavailable"},
                )
            return self.async_create_entry(data=user_input)

        return self.async_show_form(step_id="init", data_schema=self._options_schema())

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
