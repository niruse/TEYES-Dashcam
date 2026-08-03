"""Diagnostics for the Teyes integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TeyesCoordinator

REDACT_ENTRY = {"password", "token", "uid", "device_id", "device_ids", "email"}
# The car's own MQTT credentials, and anything that identifies the account.
REDACT_CAR = {
    "apikey",
    "apiname",
    "clientid",
    "imei",
    "xlh",
    "uid",
    "sn",
    "snum",
    "token",
    "location",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    cars = {}
    for device_id in coordinator.selected:
        telemetry = coordinator.telemetry.get(device_id)
        cars[device_id] = {
            "record": async_redact_data(coordinator.car(device_id), REDACT_CAR),
            "state": async_redact_data(coordinator.state(device_id), REDACT_CAR),
            "push_connected": coordinator.push_connected(device_id),
            "messages_received": coordinator.message_count.get(device_id, 0),
            "last_message_at": coordinator.last_message_at.get(device_id),
            "telemetry": coordinator.telemetry_fields(device_id),
            # The raw payload is where a new field is spotted; it holds the
            # car's position, which the tracker already exposes anyway.
            "last_message": telemetry.raw if telemetry else None,
        }
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT_ENTRY),
            "options": dict(entry.options),
        },
        "car_count": len(coordinator.cars),
        "selected_count": len(coordinator.selected),
        "cars": cars,
    }
