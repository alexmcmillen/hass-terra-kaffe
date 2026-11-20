"""Config flow for the Terra Kaffe integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TerraKaffeAPI, TerraKaffeAPIError, TerraKaffeAuthError
from .const import CONF_DEVICE_ID, CONF_REFRESH_TOKEN, DOMAIN, LOGGER
from .twin import normalize_reported

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class TerraKaffeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Terra Kaffe."""

    # Version 1 entries were for the retired Afero platform.
    VERSION = 2
    # 2.2: the password is no longer stored.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api: TerraKaffeAPI | None = None
        self._username: str = ""
        self._devices: dict[str, str] = {}  # device_id -> label

    async def _sign_in(self, username: str, password: str) -> str | None:
        """Sign in and load the device list. Return an error key or None."""
        api = TerraKaffeAPI(async_get_clientsession(self.hass))
        try:
            await api.async_login(username, password)
            devices = await api.async_get_devices()
            labels: dict[str, str] = {}
            for device in devices:
                device_id = str(device["device_id"])
                labels[device_id] = await self._label(api, device_id)
            if not api.refresh_token:
                # Without offline_access there is nothing to keep signed in with.
                return "cannot_connect"
        except TerraKaffeAuthError:
            return "invalid_auth"
        except TerraKaffeAPIError as err:
            LOGGER.debug("Sign-in failed: %s", err)
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected exception")
            return "unknown"
        self._api = api
        self._username = username
        self._devices = labels
        return None

    @staticmethod
    async def _label(api: TerraKaffeAPI, device_id: str) -> str:
        """Name a machine for the picker; fall back to its id."""
        try:
            twin = normalize_reported(
                (await api.async_get_twin(device_id)).get("reported")
            )
        except TerraKaffeAPIError:
            twin = {}
        name = twin.get("device.friendly_name") or twin.get("device.serial")
        return f"{name} ({device_id[-6:]})" if name else f"Terra Kaffe {device_id}"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Terra Kaffe account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._sign_in(
                user_input[CONF_USERNAME].strip(), user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            elif not self._devices:
                errors["base"] = "no_devices"
            elif len(self._devices) == 1:
                return await self._create_entry(next(iter(self._devices)))
            else:
                return await self.async_step_device()
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a machine when the account has several."""
        if user_input is not None:
            return await self._create_entry(user_input[CONF_DEVICE_ID])
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): vol.In(self._devices)}
            ),
        )

    async def _create_entry(self, device_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        if self._api is None:  # pragma: no cover
            return self.async_abort(reason="unknown")
        return self.async_create_entry(
            title=self._devices.get(device_id, "Terra Kaffe"),
            data={
                CONF_USERNAME: self._username,
                CONF_REFRESH_TOKEN: self._api.refresh_token,
                CONF_DEVICE_ID: device_id,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._sign_in(
                user_input[CONF_USERNAME].strip(), user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            elif entry.data[CONF_DEVICE_ID] not in self._devices:
                errors["base"] = "wrong_account"
            else:
                if self._api is None:  # pragma: no cover
                    return self.async_abort(reason="unknown")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: self._username,
                        CONF_REFRESH_TOKEN: self._api.refresh_token,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {CONF_USERNAME: entry.data.get(CONF_USERNAME, "")},
            ),
            errors=errors,
        )
