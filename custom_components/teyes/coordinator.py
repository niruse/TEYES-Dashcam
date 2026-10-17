"""Coordinator for the Teyes integration.

One config entry is one Teyes account, following one or more of its cars.
Three sources feed each car's picture: `selectCar` lists every car with its
last parked position and details, `getCarMqState` gives the live cloud state
and the MQTT credentials, and the car itself pushes telemetry over MQTT while
it is online.
"""
from __future__ import annotations

import json
import logging
import time
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
    CONF_DEVICE_IDS,
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
from .telemetry import Telemetry, normalise, parse_location_field, parse_message

_LOGGER = logging.getLogger(__name__)
ONLINE_PUSH_WINDOW = 180


class TeyesCommandError(Exception):
    """A command could not be delivered to the car."""

# Poll the app-update flag every Nth refresh (~hourly at the default interval).
_UPDATE_EVERY = 60


class TeyesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one Teyes account and folds in the pushed telemetry per car."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TeyesApiClient
    ) -> None:
        self.client = client
        self.selected: list[str] = list(entry.data.get(CONF_DEVICE_IDS) or [])
        self.options_snapshot = dict(entry.options)
        self.coordinate_system = entry.options.get(
            CONF_COORD_SYSTEM, DEFAULT_COORD_SYSTEM
        )
        # Per car, keyed by device id. The last car message is kept whole so an
        # unrecognised payload is still readable from diagnostics and entities.
        self.cars: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.telemetry: dict[str, Telemetry] = {}
        self.last_message_at: dict[str, str] = {}
        self.message_count: dict[str, int] = {}
        # Whether an app update is waiting, per car; read only occasionally
        # since it rarely changes.
        self.app_update: dict[str, dict[str, Any]] = {}
        self._update_checks = 0
        # Per car: the four tyres' latest reading, the most recent trip, and
        # the SIM's data-plan usage.
        self.tpms: dict[str, dict[str, Any]] = {}
        self.last_trip: dict[str, dict[str, Any]] = {}
        self.trip_count: dict[str, int] = {}
        self.data_usage: dict[str, dict[str, Any]] = {}
        # Live values that only arrive over MQTT while the car is online.
        self.signal: dict[str, Any] = {}
        self.last_alarm: dict[str, str] = {}
        self._mqtt: dict[str, TeyesMqttClient] = {}
        # When the car last pushed a live message (monotonic seconds).
        self.last_push: dict[str, float] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    # ── per-car accessors, all safe before the first refresh ──────────────

    def car(self, device_id: str) -> dict[str, Any]:
        """The last `selectCar` record for a car."""
        return self.cars.get(device_id) or {}

    def state(self, device_id: str) -> dict[str, Any]:
        """The last `getCarMqState` reply for a car."""
        return self.states.get(device_id) or {}

    def server(self, device_id: str) -> dict[str, Any]:
        """The `server` block of that reply: broker address and car keys."""
        server = self.state(device_id).get("server")
        return server if isinstance(server, dict) else {}

    def telemetry_fields(self, device_id: str) -> dict[str, Any]:
        """The car's merged live values, with the parked position as a floor."""
        return (self.telemetry.get(device_id) or Telemetry(None, {})).fields

    def push_connected(self, device_id: str) -> bool:
        """Whether a car's telemetry socket is subscribed right now."""
        client = self._mqtt.get(device_id)
        return bool(client and client.connected)

    def update_waiting(self, device_id: str) -> bool | None:
        """Whether the head unit has an app update waiting."""
        info = self.app_update.get(device_id)
        if not info or "hasUpdate" not in info:
            return None
        return bool(info.get("hasUpdate"))

    def tyre(self, device_id: str, position: str) -> dict[str, Any]:
        """One tyre's latest reading."""
        value = (self.tpms.get(device_id) or {}).get(position)
        return value if isinstance(value, dict) else {}

    def is_online(self, device_id: str) -> bool:
        """Whether the head unit is reachable.

        A live message in the last ONLINE_PUSH_WINDOW seconds wins: the cloud's
        `isonline` flag lagged a whole drive behind (2026-10-17: ignition on and
        live telemetry arriving, `isonline` still "0"), which kept the cameras
        unavailable the entire time. Without recent pushes, fall back to the flag.
        """
        seen = self.last_push.get(device_id)
        if seen is not None and time.monotonic() - seen < ONLINE_PUSH_WINDOW:
            return True
        for record in (self.state(device_id), self.car(device_id)):
            value = record.get("isonline")
            if value not in (None, ""):
                return str(value) in ("1", "true", "True")
        return False

    async def async_send_command(
        self, device_id: str, command: str, message: dict[str, Any] | None = None
    ) -> None:
        """Publish a command to a car over its telemetry socket.

        A command is dropped by the broker if the car is not subscribed, so
        this refuses up front when the car is offline rather than reporting a
        success the car never received. The entities stay usable regardless;
        it is only the delivery that is gated on the car being reachable.
        """
        client = self._mqtt.get(device_id)
        if client is None:
            raise TeyesCommandError(
                "Live connection is off; enable it in the integration options"
            )
        if not self.is_online(device_id):
            raise TeyesCommandError(
                "The car is offline; it must be connected to the Teyes cloud "
                "(ignition on and its own internet) to receive commands"
            )
        payload = json.dumps(
            {"type": command, "message": message or {}}, separators=(",", ":")
        )
        if not await client.async_publish(f"{MQTT_CLIENT_PREFIX}{device_id}", payload):
            raise TeyesCommandError(
                "Not connected to the car; it must be online to accept commands"
            )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            cars = await self.client.async_get_cars()
        except TeyesAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TeyesError as err:
            raise UpdateFailed(str(err)) from err
        self.cars = {str(car["imei"]): car for car in cars}

        # A selected car that has left the account simply stops updating; its
        # entities go unavailable rather than erroring the whole refresh.
        for device_id in self.selected:
            if device_id not in self.cars:
                continue
            try:
                self.states[device_id] = await self.client.async_get_car_mq_state(
                    device_id
                )
            except TeyesAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except TeyesError as err:
                _LOGGER.debug("getCarMqState failed for %s: %s", device_id, err)
            self._apply_parked_location(device_id)
            await self._async_refresh_car_extras(device_id)

        # The app-update flag and data usage rarely change, so check them
        # roughly hourly rather than on every poll.
        if self._update_checks % _UPDATE_EVERY == 0:
            await self._async_refresh_app_update()
            await self._async_refresh_data_usage()
        self._update_checks += 1
        return {"cars": self.cars, "states": self.states}

    async def _async_refresh_car_extras(self, device_id: str) -> None:
        """Read the per-car extras that change with use: tyres and trips."""
        try:
            self.tpms[device_id] = await self.client.async_get_tpms(device_id)
        except TeyesError as err:
            _LOGGER.debug("tmps failed for %s: %s", device_id, err)
        try:
            trips = await self.client.async_get_trips(device_id, size=1)
        except TeyesError as err:
            _LOGGER.debug("track/list failed for %s: %s", device_id, err)
        else:
            rows = trips.get("data") if isinstance(trips, dict) else None
            if isinstance(rows, list) and rows:
                self.last_trip[device_id] = rows[0]
            self.trip_count[device_id] = (
                trips.get("total") if isinstance(trips, dict) else None
            ) or 0

    async def _async_refresh_data_usage(self) -> None:
        """Read the SIM data plan for each car that has an iccid."""
        for device_id in self.selected:
            iccid = self.car(device_id).get("iccid")
            if not iccid:
                continue
            try:
                self.data_usage[device_id] = await self.client.async_get_data_usage(
                    str(iccid)
                )
            except TeyesError as err:
                _LOGGER.debug("data usage failed for %s: %s", device_id, err)

    async def _async_refresh_app_update(self) -> None:
        """Read the app-update flag for each selected car; failures are quiet."""
        for device_id in self.selected:
            if device_id not in self.cars:
                continue
            try:
                self.app_update[device_id] = await self.client.async_check_app_update(
                    device_id
                )
            except TeyesError as err:
                _LOGGER.debug("checkCarAppUpdate failed for %s: %s", device_id, err)

    def _apply_parked_location(self, device_id: str) -> None:
        """Seed a car's telemetry from its parked position in `selectCar`.

        A live message always wins; this only fills what the car has not
        pushed, so a parked car still shows on the map.
        """
        parked = parse_location_field(
            self.car(device_id).get("location"), self.coordinate_system
        )
        if not parked:
            return
        existing = self.telemetry_fields(device_id)
        raw = (self.telemetry.get(device_id) or Telemetry(None, {})).raw
        self.telemetry[device_id] = Telemetry(raw, {**parked, **existing})

    # ── telemetry push ────────────────────────────────────────────────────

    def start_push(self) -> None:
        """Subscribe to each selected car's topic, unless push is off."""
        if not self.config_entry.options.get(CONF_ENABLE_PUSH, DEFAULT_ENABLE_PUSH):
            return
        for device_id in self.selected:
            if device_id in self._mqtt:
                continue
            self._mqtt[device_id] = TeyesMqttClient(
                self.client.session,
                lambda device_id=device_id: self._async_connection(device_id),
                lambda topic, payload, device_id=device_id: self._handle_message(
                    device_id, payload
                ),
                lambda connected, device_id=device_id: self._handle_push_state(
                    device_id, connected
                ),
                create_task=lambda coro, name: (
                    self.config_entry.async_create_background_task(
                        self.hass, coro, name
                    )
                ),
            )
            self._mqtt[device_id].start()

    async def async_stop_push(self) -> None:
        """Close every telemetry socket."""
        clients, self._mqtt = list(self._mqtt.values()), {}
        for client in clients:
            await client.async_stop()

    @staticmethod
    def _broker_url(server: dict[str, Any]) -> str:
        """Build the broker address out of a `server` block.

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

    async def _async_connection(self, device_id: str) -> MqttConnection:
        """Fetch a car's broker address and key for one attempt.

        Read fresh rather than from the last poll, so a reconnect after the
        key rotated picks up the new one straight away.
        """
        try:
            state = await self.client.async_get_car_mq_state(device_id)
        except TeyesError as err:
            _LOGGER.debug("Could not refresh keys for %s: %s", device_id, err)
            state = self.state(device_id)
        else:
            self.states[device_id] = state
            self.async_update_listeners()
        server = state.get("server") if isinstance(state, dict) else None
        server = server if isinstance(server, dict) else {}
        return MqttConnection(
            url=self._broker_url(server),
            client_id=f"{MQTT_CLIENT_PREFIX}{device_id}",
            username=device_id,
            password=str(server.get("apikey") or ""),
            topics=[f"{MQTT_TOPIC_PREFIX}{device_id}"],
        )

    @callback
    def _handle_push_state(self, device_id: str, connected: bool) -> None:
        """Reflect a socket going up or down on the entities."""
        self.async_update_listeners()

    @callback
    def _handle_message(self, device_id: str, payload: bytes) -> None:
        """Fold a pushed car message into the coordinator's data.

        The car sends `{type, data}`; the type says which subsystem reported,
        so this dispatches on it. Anything with a position (a `location`
        message, or an untyped payload that carries one) still updates the
        tracker as before.
        """
        raw = parse_message(payload)
        if raw is None:
            return
        _LOGGER.debug("Car message for %s: %s", device_id, raw)
        self.message_count[device_id] = self.message_count.get(device_id, 0) + 1
        self.last_message_at[device_id] = dt_util.utcnow().isoformat()

        message_type = raw.get("type") if isinstance(raw, dict) else None
        body = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(body, dict):
            body = raw

        if message_type in ("tmps", "tmpsNotice"):
            self.tpms.setdefault(device_id, {}).update(body)
        elif message_type == "rsrp":
            self.signal[device_id] = body.get("type", body)
        elif message_type == "shake":
            self.last_alarm[device_id] = dt_util.utcnow().isoformat()
            self.hass.bus.async_fire(
                f"{DOMAIN}_alarm", {"device_id": device_id, "kind": "shake"}
            )
        else:
            # location / acc / untyped — fold any telemetry it carries in.
            fresh = normalise(body, self.coordinate_system)
            if message_type == "acc" and "acc" not in fresh.fields:
                if (state := body.get("type")) in ("on", "off"):
                    fresh.fields["acc"] = state == "on"
            merged = {**self.telemetry_fields(device_id), **fresh.fields}
            self.telemetry[device_id] = Telemetry(raw, merged)
        self.last_push[device_id] = time.monotonic()

        self.async_update_listeners()
