"""Diagnostics for the Teyes integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TeyesCoordinator

REDACT_ENTRY = {"password", "token", "uid", "device_id", "email"}
# The car's own MQTT credentials, and anything that identifies the account.
REDACT_STATE = {"apikey", "apiname", "clientid", "imei", "uid", "sn", "token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT_ENTRY),
            "options": dict(entry.options),
        },
        "car_state": async_redact_data(coordinator.state, REDACT_STATE),
        "push_connected": coordinator.push_connected,
        "messages_received": coordinator.message_count,
        "last_message_at": coordinator.last_message_at,
        "telemetry": coordinator.telemetry.fields,
        # The raw payload is what a new field would be spotted in; it carries
        # the car's position, which the tracker already exposes anyway.
        "last_message": coordinator.telemetry.raw,
    }
