"""Config flow for the Teyes integration.

Two ways in: the account's email and password, which is what the portal uses,
or a session token copied out of a browser that is already signed in. The
token route exists because the portal has been known to be unreachable while
the API behind it kept working.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import TeyesApiClient, TeyesAuthError, TeyesConnectionError, TeyesError
from .const import (
    AUTH_MODE_PASSWORD,
    AUTH_MODE_TOKEN,
    CONF_AUTH_MODE,
    CONF_COORD_SYSTEM,
    CONF_DEVICE_ID,
    CONF_ENABLE_PUSH,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_UID,
    COORD_SYSTEMS,
    DEFAULT_COORD_SYSTEM,
    DEFAULT_ENABLE_PUSH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)
REAUTH_PASSWORD_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})
TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): str,
        vol.Required(CONF_UID): str,
        vol.Required(CONF_DEVICE_ID): str,
    }
)


class TeyesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Teyes."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._devices: list[str] = []
        self._reauth_entry: ConfigEntry | None = None

    def _client(self, data: Mapping[str, Any]) -> TeyesApiClient:
        return TeyesApiClient(
            async_get_clientsession(self.hass),
            email=data.get(CONF_EMAIL),
            password=data.get(CONF_PASSWORD),
            token=data.get(CONF_TOKEN),
            uid=data.get(CONF_UID),
            device_id=data.get(CONF_DEVICE_ID),
            timezone=self.hass.config.time_zone or "UTC",
            language=self.hass.config.language or "en",
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which way the account should be connected."""
        return self.async_show_menu(
            step_id="user", menu_options=["credentials", "token"]
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with the account email and password."""
        if user_input is None:
            return self.async_show_form(
                step_id="credentials", data_schema=CREDENTIALS_SCHEMA
            )

        client = self._client(user_input)
        errors: dict[str, str] = {}
        try:
            await client.async_login()
            self._devices = await client.async_list_devices()
        except TeyesAuthError as err:
            _LOGGER.debug("Teyes rejected the login: %s", err)
            errors["base"] = "invalid_auth"
        except TeyesConnectionError:
            errors["base"] = "cannot_connect"
        except TeyesError as err:
            _LOGGER.debug("Teyes refused the login call: %s", err)
            errors["base"] = "unknown"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error signing in to Teyes")
            errors["base"] = "unknown"

        if errors:
            return self.async_show_form(
                step_id="credentials", data_schema=CREDENTIALS_SCHEMA, errors=errors
            )

        self._data = {
            CONF_AUTH_MODE: AUTH_MODE_PASSWORD,
            CONF_EMAIL: user_input[CONF_EMAIL],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_TOKEN: client.token,
            CONF_UID: client.uid,
        }
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data={**self._reauth_entry.data, **self._data},
            )
        return await self.async_step_device()

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use a session token copied from a signed-in browser."""
        if user_input is None:
            return self.async_show_form(step_id="token", data_schema=TOKEN_SCHEMA)

        data = {
            CONF_AUTH_MODE: AUTH_MODE_TOKEN,
            CONF_TOKEN: user_input[CONF_TOKEN].strip(),
            CONF_UID: user_input[CONF_UID].strip(),
            CONF_DEVICE_ID: user_input[CONF_DEVICE_ID].strip(),
        }
        if self._reauth_entry is not None:
            data = {**self._reauth_entry.data, **data}
        errors = await self._async_check(data)
        if errors:
            return self.async_show_form(
                step_id="token", data_schema=TOKEN_SCHEMA, errors=errors
            )
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        return await self._async_create(data)

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which car this entry follows."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self._data, CONF_DEVICE_ID: user_input[CONF_DEVICE_ID].strip()}
            errors = await self._async_check(data)
            if not errors:
                return await self._async_create(data)

        default = self._devices[0] if self._devices else ""
        if len(self._devices) > 1:
            field: Any = SelectSelector(
                SelectSelectorConfig(
                    options=self._devices, mode=SelectSelectorMode.DROPDOWN, custom_value=True
                )
            )
        else:
            field = str
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID, default=default): field}
            ),
            errors=errors,
        )

    async def _async_check(self, data: dict[str, Any]) -> dict[str, str]:
        """Confirm the session and device id actually read the car's state."""
        client = self._client(data)
        try:
            await client.async_get_car_mq_state()
        except TeyesAuthError:
            return {"base": "invalid_auth"}
        except TeyesConnectionError:
            return {"base": "cannot_connect"}
        except TeyesError as err:
            _LOGGER.debug("Teyes refused getCarMqState: %s", err)
            return {"base": "unknown_device"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error reading the Teyes car state")
            return {"base": "unknown"}
        return {}

    async def _async_create(self, data: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(data[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=data[CONF_DEVICE_ID], data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The session expired; renew it the same way it was set up."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for whatever this entry signs in with."""
        assert self._reauth_entry is not None
        entry_data = self._reauth_entry.data
        if entry_data.get(CONF_AUTH_MODE) == AUTH_MODE_TOKEN:
            if user_input is None:
                return self.async_show_form(
                    step_id="token",
                    data_schema=self.add_suggested_values_to_schema(
                        TOKEN_SCHEMA, entry_data
                    ),
                )
            return await self.async_step_token(user_input)
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=REAUTH_PASSWORD_SCHEMA,
                description_placeholders={CONF_EMAIL: entry_data.get(CONF_EMAIL, "")},
            )
        return await self.async_step_credentials(
            {CONF_EMAIL: entry_data[CONF_EMAIL], **user_input}
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TeyesOptionsFlow:
        """Create the options flow."""
        return TeyesOptionsFlow()


class TeyesOptionsFlow(OptionsFlow):
    """Polling interval, live push, and how coordinates are read."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                    vol.Required(
                        CONF_ENABLE_PUSH,
                        default=options.get(CONF_ENABLE_PUSH, DEFAULT_ENABLE_PUSH),
                    ): bool,
                    vol.Required(
                        CONF_COORD_SYSTEM,
                        default=options.get(CONF_COORD_SYSTEM, DEFAULT_COORD_SYSTEM),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=COORD_SYSTEMS,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_COORD_SYSTEM,
                        )
                    ),
                }
            ),
        )
