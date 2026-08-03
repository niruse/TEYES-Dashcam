"""Minimal MQTT 3.1 client for the Teyes telemetry socket.

The head unit's live data is pushed over raw MQTT inside a WebSocket, which
Home Assistant's own MQTT integration cannot be pointed at. Only four packet
types are needed to listen, so they are built and parsed here rather than
pulling in a broker client as a dependency.

Handshake (captured on the wire):
    CONNECT   protocol name "MQIsdp", level 3, flags 0xC2, keepalive 10s
              client id `app_<device id>`
              username `<device id>`, password the car's `apikey`
    SUBSCRIBE `carinfo_<device id>` at QoS 0
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

import aiohttp

from .const import MQTT_KEEPALIVE

_LOGGER = logging.getLogger(__name__)

CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
SUBSCRIBE = 8
SUBACK = 9
PINGREQ = 12
PINGRESP = 13
DISCONNECT = 14

_BACKOFF_START = 5
_BACKOFF_MAX = 300
# CONNACK return codes; 4 and 5 mean the broker refused these credentials.
_AUTH_REFUSED = (4, 5)


class TeyesMqttAuthError(Exception):
    """The broker refused the credentials from getCarMqState."""


class MqttConnection(NamedTuple):
    """Everything one connection attempt needs, read fresh each time.

    The broker address and the car's key both come from the same cloud reply,
    so they are fetched together rather than pinned at start-up.
    """

    url: str
    client_id: str
    username: str
    password: str
    topics: list[str]


def _string(value: str) -> bytes:
    """Encode an MQTT length-prefixed string."""
    raw = value.encode()
    return len(raw).to_bytes(2, "big") + raw


def _remaining_length(length: int) -> bytes:
    """Encode MQTT's variable-length integer."""
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length:
            digit |= 0x80
        out.append(digit)
        if not length:
            return bytes(out)


def _packet(packet_type: int, flags: int, body: bytes) -> bytes:
    return bytes([packet_type << 4 | flags]) + _remaining_length(len(body)) + body


class TeyesMqttClient:
    """Keeps a subscription to one car's topic alive."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        connection: Callable[[], Awaitable[MqttConnection]],
        on_message: Callable[[str, bytes], None],
        on_connected: Callable[[bool], None] | None = None,
        create_task: Callable[[Any, str], asyncio.Task] | None = None,
    ) -> None:
        self._session = session
        self._connection = connection
        self._on_message = on_message
        self._on_connected = on_connected
        # Home Assistant wants long-running work tied to the config entry, so
        # it is stopped and reported on properly; fall back for standalone use.
        self._create_task = create_task or (
            lambda coro, name: asyncio.create_task(coro, name=name)
        )
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._buffer = bytearray()
        self.connected = False

    def start(self) -> None:
        """Begin connecting, and keep reconnecting until stopped."""
        if self._task is None or self._task.done():
            self._task = self._create_task(self._run(), "teyes telemetry")

    async def async_stop(self) -> None:
        """Close the socket and stop reconnecting."""
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close()

    async def _close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            try:
                await ws.send_bytes(_packet(DISCONNECT, 0, b""))
            except Exception:  # noqa: BLE001 - the socket is going away anyway
                pass
            await ws.close()
        self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            if self._on_connected:
                self._on_connected(connected)

    async def _run(self) -> None:
        """Connect, listen, and back off between failures."""
        backoff = _BACKOFF_START
        while True:
            try:
                await self._session_once()
                backoff = _BACKOFF_START
            except asyncio.CancelledError:
                raise
            except TeyesMqttAuthError as err:
                # The apikey rotates with the car's cloud state, so wait for
                # the coordinator's next poll to hand over a fresh one.
                _LOGGER.warning("Teyes broker refused the credentials: %s", err)
            except Exception as err:  # noqa: BLE001 - never kill the listener
                _LOGGER.debug("Teyes telemetry socket dropped: %s", err)
            finally:
                await self._close()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _session_once(self) -> None:
        """One connection: handshake, subscribe, then pump packets."""
        connection = await self._connection()
        topics = connection.topics
        self._buffer.clear()
        async with self._session.ws_connect(
            connection.url, protocols=("mqtt",), heartbeat=None, timeout=20
        ) as ws:
            self._ws = ws
            flags = 0x02  # clean session
            body = _string("MQIsdp") + bytes([3, flags | 0x80 | 0x40])
            body += MQTT_KEEPALIVE.to_bytes(2, "big")
            body += (
                _string(connection.client_id)
                + _string(connection.username)
                + _string(connection.password)
            )
            await ws.send_bytes(_packet(CONNECT, 0, body))

            subscribed = False
            while True:
                try:
                    message = await asyncio.wait_for(
                        ws.receive(), timeout=MQTT_KEEPALIVE
                    )
                except asyncio.TimeoutError:
                    await ws.send_bytes(_packet(PINGREQ, 0, b""))
                    continue
                if message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    raise ConnectionError(f"socket closed ({message.type.name})")
                if message.type is not aiohttp.WSMsgType.BINARY:
                    continue
                self._buffer.extend(message.data)
                for packet_type, flags, payload in self._drain():
                    if packet_type == CONNACK:
                        code = payload[1] if len(payload) > 1 else -1
                        if code in _AUTH_REFUSED:
                            raise TeyesMqttAuthError(f"CONNACK {code}")
                        if code != 0:
                            raise ConnectionError(f"CONNACK {code}")
                        for index, topic in enumerate(topics, start=1):
                            await ws.send_bytes(
                                _packet(
                                    SUBSCRIBE,
                                    2,
                                    index.to_bytes(2, "big")
                                    + _string(topic)
                                    + bytes([0]),
                                )
                            )
                    elif packet_type == SUBACK:
                        if not subscribed:
                            subscribed = True
                            _LOGGER.debug("Subscribed to %s", ", ".join(topics))
                            self._set_connected(True)
                    elif packet_type == PUBLISH:
                        await self._handle_publish(ws, flags, payload)
                    elif packet_type == PINGRESP:
                        continue

    async def _handle_publish(
        self, ws: aiohttp.ClientWebSocketResponse, flags: int, payload: bytes
    ) -> None:
        """Hand a car message on, acknowledging it when the broker asked."""
        length = int.from_bytes(payload[:2], "big")
        topic = payload[2 : 2 + length].decode("utf-8", "replace")
        rest = payload[2 + length :]
        qos = (flags >> 1) & 3
        if qos > 0:
            packet_id, rest = rest[:2], rest[2:]
            if qos == 1:
                await ws.send_bytes(_packet(PUBACK, 0, packet_id))
        self._on_message(topic, bytes(rest))

    def _drain(self) -> list[tuple[int, int, bytes]]:
        """Pull every whole packet out of the buffer, leaving any remainder.

        A WebSocket frame can hold several MQTT packets or half of one, so the
        bytes are parsed as a stream rather than per frame.
        """
        packets: list[tuple[int, int, bytes]] = []
        while self._buffer:
            header = self._buffer[0]
            length = 0
            multiplier = 1
            index = 1
            while True:
                if index >= len(self._buffer):
                    return packets  # length prefix not fully arrived yet
                digit = self._buffer[index]
                length += (digit & 127) * multiplier
                multiplier *= 128
                index += 1
                if not digit & 0x80:
                    break
                if multiplier > 128**3:
                    # Not MQTT; drop what is buffered rather than spin.
                    self._buffer.clear()
                    return packets
            if len(self._buffer) < index + length:
                return packets  # body still in flight
            packets.append(
                (header >> 4, header & 15, bytes(self._buffer[index : index + length]))
            )
            del self._buffer[: index + length]
        return packets
