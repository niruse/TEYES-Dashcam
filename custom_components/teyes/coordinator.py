"""Coordinator for the Teyes integration.

Two sources feed the same picture: `getCarMqState` is polled for the car's
cloud state, and the car itself pushes telemetry over MQTT while it is online.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TeyesApiClient, TeyesAuthError, TeyesError
from .const import (
    CONF_COORD_SYSTEM,
    CONF_ENABLE_PUSH,
    CONF_SCAN_INTERVAL,
    DEFAULT_COORD_SYSTEM,
    DEFAULT_ENABLE_PUSH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MQTT_CLIENT_PREFIX,
    MQTT_TOPIC_PREFIX,
    MQTT_WS_URL,
)
from .mqtt import MqttConnection, TeyesMqttClient
from .telemetry import Telemetry, normalise, parse_message

_LOGGER = logging.getLogger(__name__)


class TeyesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the car's cloud state and folds in the pushed telemetry."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TeyesApiClient
    ) -> None:
        self.client = client
        self.device_id = client.device_id or entry.unique_id or ""
        self.options_snapshot = dict(entry.options)
        self.coordinate_system = entry.options.get(
            CONF_COORD_SYSTEM, DEFAULT_COORD_SYSTEM
        )
        # The last car message, kept whole so an unrecognised payload can still
        # be read from the diagnostics and the telemetry entity.
        self.telemetry = Telemetry(None, {})
        self.last_message_at: str | None = None
        self.message_count = 0
        self._mqtt: TeyesMqttClient | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    @property
    def state(self) -> dict[str, Any]:
        """The last `getCarMqState` reply."""
        return (self.data or {}).get("state") or {}

    @property
    def server(self) -> dict[str, Any]:
        """The `server` block of that reply: broker address and car keys."""
        server = self.state.get("server")
        return server if isinstance(server, dict) else {}

    @property
    def push_connected(self) -> bool:
        """Whether the telemetry socket is subscribed right now."""
        return bool(self._mqtt and self._mqtt.connected)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            state = await self.client.async_get_car_mq_state()
        except TeyesAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TeyesError as err:
            raise UpdateFailed(str(err)) from err
        return {"state": state, "telemetry": dict(self.telemetry.fields)}

    # ── telemetry push ────────────────────────────────────────────────────

    def start_push(self) -> None:
        """Subscribe to the car's topic, unless the user turned push off."""
        if not self.config_entry.options.get(CONF_ENABLE_PUSH, DEFAULT_ENABLE_PUSH):
            return
        if not self.device_id:
            _LOGGER.debug("No device id, so no telemetry subscription")
            return
        self._mqtt = TeyesMqttClient(
            self.client.session,
            self._async_connection,
            self._handle_message,
            self._handle_push_state,
            create_task=lambda coro, name: self.config_entry.async_create_background_task(
                self.hass, coro, name
            ),
        )
        self._mqtt.start()

    async def async_stop_push(self) -> None:
        """Close the telemetry socket."""
        if self._mqtt is not None:
            await self._mqtt.async_stop()
            self._mqtt = None

    @staticmethod
    def _broker_url(server: dict[str, Any]) -> str:
        """Build the broker address out of the `server` block.

        The reply names it in three parts — `protocol`, `url` and `path` —
        which is how an account gets moved between regional brokers.
        """
        host = str(server.get("url") or "").strip()
        if not host:
            return MQTT_WS_URL
        protocol = str(server.get("protocol") or "wss").strip()
        path = str(server.get("path") or "/websocket").strip()
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{protocol}://{host}{path}"

    async def _async_connection(self) -> MqttConnection:
        """Fetch the broker address and the car's key for one attempt.

        These are read fresh rather than taken from the last poll, so a
        reconnect after the key rotated picks up the new one straight away.
        """
        try:
            state = await self.client.async_get_car_mq_state()
        except TeyesError as err:
            _LOGGER.debug("Could not refresh the car keys: %s", err)
            state = self.state
        else:
            self.async_set_updated_data(
                {"state": state, "telemetry": dict(self.telemetry.fields)}
            )
        server = state.get("server") if isinstance(state, dict) else None
        server = server if isinstance(server, dict) else {}
        device = self.device_id
        return MqttConnection(
            url=self._broker_url(server),
            client_id=f"{MQTT_CLIENT_PREFIX}{device}",
            username=device,
            password=str(server.get("apikey") or ""),
            topics=[f"{MQTT_TOPIC_PREFIX}{device}"],
        )

    @callback
    def _handle_push_state(self, connected: bool) -> None:
        """Reflect the socket going up or down on the entities."""
        self.async_update_listeners()

    @callback
    def _handle_message(self, topic: str, payload: bytes) -> None:
        """Fold a pushed car message into the coordinator's data."""
        raw = parse_message(payload)
        if raw is None:
            return
        _LOGGER.debug("Car message on %s: %s", topic, raw)
        telemetry = normalise(raw, self.coordinate_system)
        # Keep the previous values for anything this message left out, so a
        # partial report does not blank out the entities.
        merged = {**self.telemetry.fields, **telemetry.fields}
        self.telemetry = Telemetry(raw, merged)
        self.message_count += 1
        self.last_message_at = dt_util.utcnow().isoformat()
        self.async_set_updated_data({"state": self.state, "telemetry": merged})
