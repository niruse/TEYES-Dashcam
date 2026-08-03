"""Services for the Teyes integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .api import TeyesError
from .const import CAR_API, CLIENT_API, DOMAIN, SERVICE_API_CALL
from .coordinator import TeyesCoordinator

# The car API is split across regional hosts, each named in the `server` block
# of getCarMqState as "<name>url"; "car" is that block's own `ajaxurl`.
CAR_HOSTS = ("car", "track", "acc", "msg", "file", "update", "ef", "tmps")
APIS = ("client", *CAR_HOSTS)

API_CALL_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("device_id"): cv.string,
        vol.Optional("api", default="client"): vol.In(APIS),
        vol.Required("path"): cv.string,
        vol.Optional("params", default={}): dict,
    }
)


def _base_for(coordinator: TeyesCoordinator, api: str, device_id: str) -> str:
    """Resolve which host an API name points at for a given car."""
    if api == "client":
        return CLIENT_API
    key = "ajaxurl" if api == "car" else f"{api}url"
    base = coordinator.server(device_id).get(key)
    if isinstance(base, str) and base.startswith("http"):
        return base.rstrip("/")
    return CAR_API


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services, once, however many accounts are set up."""
    if hass.services.has_service(DOMAIN, SERVICE_API_CALL):
        return

    async def _async_api_call(call: ServiceCall) -> ServiceResponse:
        """Call any Teyes endpoint and hand back what it answered.

        Most of the car API's method names are obfuscated in the vendor's web
        bundle, so this is how a new one gets tried without editing code.
        """
        entries = hass.data.get(DOMAIN) or {}
        if not entries:
            raise ServiceValidationError("No Teyes account is set up")
        entry_id = call.data.get("entry_id") or next(iter(entries))
        coordinator = entries.get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"Unknown Teyes entry {entry_id}")

        # Default to the account's first selected car, so the common single-car
        # case needs no device id.
        device_id = call.data.get("device_id") or (
            coordinator.selected[0] if coordinator.selected else None
        )

        try:
            result = await coordinator.client.async_call(
                call.data["path"],
                dict(call.data.get("params") or {}),
                base=_base_for(coordinator, call.data["api"], device_id or ""),
                device_id=device_id,
            )
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        return _jsonable(result)

    hass.services.async_register(
        DOMAIN,
        SERVICE_API_CALL,
        _async_api_call,
        schema=API_CALL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the services when the last account goes away."""
    hass.services.async_remove(DOMAIN, SERVICE_API_CALL)


def _jsonable(value: Any) -> Any:
    """Coerce a reply into something a service response can carry."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
