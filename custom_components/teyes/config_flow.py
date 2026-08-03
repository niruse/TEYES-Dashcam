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
from homeassistant.helpers import config_validation as cv
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
    CONF_DEVICE_IDS,
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
    {vol.Required(CONF_TOKEN): str, vol.Required(CONF_UID): str}
)


def _car_labels(cars: list[dict[str, Any]]) -> dict[str, str]:
    """Map each car's device id to a human label, for the checkbox list."""
    labels: dict[str, str] = {}
    for car in cars:
        imei = str(car.get("imei") or "")
        name = str(car.get("title") or "").strip()
        labels[imei] = f"{name} ({imei})" if name else imei
    return labels


class TeyesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Teyes."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._cars: list[dict[str, Any]] = []
        self._reauth_entry: ConfigEntry | None = None

    def _client(self, data: Mapping[str, Any]) -> TeyesApiClient:
        return TeyesApiClient(
            async_get_clientsession(self.hass),
            email=data.get(CONF_EMAIL),
            password=data.get(CONF_PASSWORD),
            token=data.get(CONF_TOKEN),
            uid=data.get(CONF_UID),
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
            self._cars = await client.async_get_cars()
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

        if not self._cars:
            # The account has no car, or an older reply this could not read.
            # Fall back to asking for the id rather than dead-ending.
            return await self.async_step_device()

        if self._reauth_entry is not None:
            # Renewing the session only; keep the car selection as it was.
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data={**self._reauth_entry.data, **self._data},
            )

        if len(self._cars) == 1:
            return await self._async_create([str(self._cars[0]["imei"])])
        return await self.async_step_select()

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use a session token copied from a signed-in browser.

        The car is discovered from the token, the same as the password path;
        the user only supplies the token and the account id it is scoped to.
        """
        if user_input is None:
            return self.async_show_form(step_id="token", data_schema=TOKEN_SCHEMA)

        self._data = {
            CONF_AUTH_MODE: AUTH_MODE_TOKEN,
            CONF_TOKEN: user_input[CONF_TOKEN].strip(),
            CONF_UID: user_input[CONF_UID].strip(),
        }
        client = self._client(self._data)
        errors: dict[str, str] = {}
        try:
            self._cars = await client.async_get_cars()
        except TeyesAuthError:
            errors["base"] = "invalid_auth"
        except TeyesConnectionError:
            errors["base"] = "cannot_connect"
        except TeyesError as err:
            _LOGGER.debug("Teyes refused selectCar: %s", err)
            errors["base"] = "unknown"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error reading the Teyes car list")
            errors["base"] = "unknown"

        if errors or not self._cars:
            return self.async_show_form(
                step_id="token",
                data_schema=TOKEN_SCHEMA,
                errors=errors or {"base": "unknown_device"},
            )

        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data={**self._reauth_entry.data, **self._data},
            )

        if len(self._cars) == 1:
            return await self._async_create([str(self._cars[0]["imei"])])
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tick the cars to bring into Home Assistant."""
        labels = _car_labels(self._cars)
        if user_input is not None:
            chosen = [d for d in user_input.get(CONF_DEVICE_IDS, []) if d in labels]
            if chosen:
                return await self._async_create(chosen)
            return self.async_show_form(
                step_id="select",
                data_schema=self._select_schema(labels, list(labels)),
                errors={"base": "no_cars_selected"},
            )
        return self.async_show_form(
            step_id="select",
            data_schema=self._select_schema(labels, list(labels)),
        )

    @staticmethod
    def _select_schema(labels: dict[str, str], default: list[str]) -> vol.Schema:
        """A checkbox list of cars, each ticked by `default`."""
        return vol.Schema(
            {
                vol.Required(CONF_DEVICE_IDS, default=default): cv.multi_select(
                    labels
                )
            }
        )

    async def _async_create(self, device_ids: list[str]) -> ConfigFlowResult:
        """Create the account entry with the chosen cars."""
        await self.async_set_unique_id(str(self._data[CONF_UID]))
        self._abort_if_unique_id_configured()
        title = str(self._data.get(CONF_EMAIL) or self._data[CONF_UID])
        return self.async_create_entry(
            title=title, data={**self._data, CONF_DEVICE_IDS: device_ids}
        )

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
    """Which cars are enabled, polling interval, live push, and coordinates."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options, including which cars this account follows."""
        entry = self.config_entry
        labels = await self._async_car_labels()
        selected = list(entry.data.get(CONF_DEVICE_IDS) or [])

        if user_input is not None:
            chosen = [d for d in user_input.pop(CONF_DEVICE_IDS, selected) if d in labels]
            if labels and not chosen:
                return self._show(labels, selected, user_input, {"base": "no_cars_selected"})
            # The car list lives in entry.data (it changes what is set up), so
            # write it there; the rest are ordinary options. Either way the
            # update listener reloads the entry.
            if labels and set(chosen) != set(selected):
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_DEVICE_IDS: chosen}
                )
            return self.async_create_entry(data=user_input)

        return self._show(labels, selected, dict(entry.options), {})

    async def _async_car_labels(self) -> dict[str, str]:
        """The account's current cars, or empty if they cannot be read now."""
        data = self.config_entry.data
        client = TeyesApiClient(
            async_get_clientsession(self.hass),
            email=data.get(CONF_EMAIL),
            password=data.get(CONF_PASSWORD),
            token=data.get(CONF_TOKEN),
            uid=data.get(CONF_UID),
            timezone=self.hass.config.time_zone or "UTC",
            language=self.hass.config.language or "en",
        )
        try:
            return _car_labels(await client.async_get_cars())
        except TeyesError as err:
            _LOGGER.debug("Options flow could not list cars: %s", err)
            return {}

    def _show(
        self,
        labels: dict[str, str],
        selected: list[str],
        values: dict[str, Any],
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        options = self.config_entry.options
        schema: dict[Any, Any] = {}
        # Only offer the car list when it could actually be read, so a
        # temporary outage cannot wipe the selection.
        if labels:
            schema[
                vol.Required(
                    CONF_DEVICE_IDS,
                    default=[d for d in selected if d in labels] or list(labels),
                )
            ] = cv.multi_select(labels)
        schema.update(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=values.get(
                        CONF_SCAN_INTERVAL,
                        options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
                vol.Required(
                    CONF_ENABLE_PUSH,
                    default=values.get(
                        CONF_ENABLE_PUSH,
                        options.get(CONF_ENABLE_PUSH, DEFAULT_ENABLE_PUSH),
                    ),
                ): bool,
                vol.Required(
                    CONF_COORD_SYSTEM,
                    default=values.get(
                        CONF_COORD_SYSTEM,
                        options.get(CONF_COORD_SYSTEM, DEFAULT_COORD_SYSTEM),
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=COORD_SYSTEMS,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_COORD_SYSTEM,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )
