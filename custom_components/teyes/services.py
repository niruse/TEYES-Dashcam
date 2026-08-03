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
from .const import (
    CAR_API,
    CLIENT_API,
    DOMAIN,
    NAV_APPS,
    SERVICE_API_CALL,
    SERVICE_DELETE_GEOFENCE,
    SERVICE_GET_EVENTS,
    SERVICE_GET_TRIPS,
    SERVICE_LIST_GEOFENCES,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_NAVIGATION,
    SERVICE_SET_GEOFENCE,
)
from .coordinator import TeyesCommandError, TeyesCoordinator

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

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("device_id"): cv.string,
        vol.Required("command"): cv.string,
        vol.Optional("message", default={}): dict,
    }
)

_TARGET = {
    vol.Optional("entry_id"): cv.string,
    vol.Optional("device_id"): cv.string,
}
PAGE_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Optional("count", default=20): vol.All(vol.Coerce(int), vol.Range(1, 200)),
    }
)
LIST_GEOFENCES_SCHEMA = vol.Schema(_TARGET)
SET_GEOFENCE_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Required("latitude"): vol.Coerce(float),
        vol.Required("longitude"): vol.Coerce(float),
        vol.Required("radius"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)
DELETE_GEOFENCE_SCHEMA = vol.Schema({**_TARGET, vol.Required("ids"): cv.string})
SEND_NAVIGATION_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Required("address"): cv.string,
        vol.Optional("map_app", default="google"): vol.In(NAV_APPS),
    }
)


def _resolve(hass: HomeAssistant, data: Any) -> tuple[TeyesCoordinator, str]:
    """Pick the account and car a service call targets."""
    entries = hass.data.get(DOMAIN) or {}
    if not entries:
        raise ServiceValidationError("No Teyes account is set up")
    entry_id = data.get("entry_id") or next(iter(entries))
    coordinator = entries.get(entry_id)
    if coordinator is None:
        raise ServiceValidationError(f"Unknown Teyes entry {entry_id}")
    device_id = data.get("device_id") or (
        coordinator.selected[0] if coordinator.selected else ""
    )
    return coordinator, device_id


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
        coordinator, device_id = _resolve(hass, call.data)
        try:
            result = await coordinator.client.async_call(
                call.data["path"],
                dict(call.data.get("params") or {}),
                base=_base_for(coordinator, call.data["api"], device_id),
                device_id=device_id or None,
            )
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        return _jsonable(result)

    async def _async_send_command(call: ServiceCall) -> None:
        """Publish a command to the car over its live socket.

        Covers the commands this integration does not wrap as entities yet —
        radio, wallpaper, navigation — once their payload is known (see the
        capture recipe in tools/). The car must be online to act on them.
        """
        coordinator, device_id = _resolve(hass, call.data)
        try:
            await coordinator.async_send_command(
                device_id, call.data["command"], dict(call.data.get("message") or {})
            )
        except TeyesCommandError as err:
            raise HomeAssistantError(str(err)) from err

    async def _async_get_trips(call: ServiceCall) -> ServiceResponse:
        """Return the car's recent trips."""
        coordinator, device_id = _resolve(hass, call.data)
        try:
            data = await coordinator.client.async_get_trips(
                device_id, size=call.data["count"]
            )
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        return {"trips": _jsonable((data or {}).get("data") or [])}

    async def _async_get_events(call: ServiceCall) -> ServiceResponse:
        """Return the car's recent sentry / alarm events."""
        coordinator, device_id = _resolve(hass, call.data)
        try:
            data = await coordinator.client.async_get_events(
                device_id, size=call.data["count"]
            )
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        return {"events": _jsonable((data or {}).get("data") or [])}

    async def _async_list_geofences(call: ServiceCall) -> ServiceResponse:
        """Return the car's geofences."""
        coordinator, device_id = _resolve(hass, call.data)
        try:
            data = await coordinator.client.async_get_geofences(device_id)
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        return {"geofences": _jsonable((data or {}).get("data") or [])}

    async def _async_set_geofence(call: ServiceCall) -> None:
        """Create or update a circular geofence."""
        coordinator, device_id = _resolve(hass, call.data)
        try:
            await coordinator.client.async_set_geofence(
                device_id,
                scope_status="normal",
                scope_position=f"{call.data['latitude']},{call.data['longitude']}",
                scope_range=call.data["radius"],
            )
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _async_delete_geofence(call: ServiceCall) -> None:
        """Delete geofences by id."""
        coordinator, device_id = _resolve(hass, call.data)
        try:
            await coordinator.client.async_delete_geofences(device_id, call.data["ids"])
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _async_send_navigation(call: ServiceCall) -> None:
        """Send a destination for the car to open in a map app."""
        coordinator, device_id = _resolve(hass, call.data)
        try:
            await coordinator.client.async_send_address(
                device_id, call.data["address"], call.data["map_app"]
            )
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_API_CALL,
        _async_api_call,
        schema=API_CALL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, _async_send_command, schema=SEND_COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_TRIPS, _async_get_trips,
        schema=PAGE_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_EVENTS, _async_get_events,
        schema=PAGE_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LIST_GEOFENCES, _async_list_geofences,
        schema=LIST_GEOFENCES_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_GEOFENCE, _async_set_geofence, schema=SET_GEOFENCE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_GEOFENCE, _async_delete_geofence,
        schema=DELETE_GEOFENCE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_NAVIGATION, _async_send_navigation,
        schema=SEND_NAVIGATION_SCHEMA,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the services when the last account goes away."""
    for service in (
        SERVICE_API_CALL,
        SERVICE_SEND_COMMAND,
        SERVICE_GET_TRIPS,
        SERVICE_GET_EVENTS,
        SERVICE_LIST_GEOFENCES,
        SERVICE_SET_GEOFENCE,
        SERVICE_DELETE_GEOFENCE,
        SERVICE_SEND_NAVIGATION,
    ):
        hass.services.async_remove(DOMAIN, service)


def _jsonable(value: Any) -> Any:
    """Coerce a reply into something a service response can carry."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
