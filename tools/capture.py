# -*- coding: utf-8 -*-
"""Capture the car's live data, to extend the Teyes integration.

Several features — the live telemetry payload, plus radio, wallpaper and any
remote video — ride paths whose shapes are only visible while the head unit is
actually online (ignition on AND its own internet: a data SIM or a hotspot it
joins). This waits for the car to come online, then records what it sends so a
new entity can be built from real data rather than guesswork.

It reuses the integration's own crypto and MQTT code, so whatever it captures
is exactly what Home Assistant would see.

Usage:
    python tools/capture.py --token <ucenterToken> --uid <uid> --imei <deviceId>

Get those three from a signed-in browser at https://online.teyes.cn:
    localStorage.getItem('ucenterToken')
    localStorage.getItem('uid')
    localStorage.getItem('imei')

    --once     print the current cloud state and exit (no waiting)
    --minutes  how long to listen once online (default 10)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import types
from pathlib import Path

# Load the integration's modules without running its Home Assistant __init__.
_PKG = Path(__file__).resolve().parent.parent / "custom_components" / "teyes"
_teyes = types.ModuleType("teyes")
_teyes.__path__ = [str(_PKG)]
sys.modules["teyes"] = _teyes

import aiohttp  # noqa: E402

from teyes.api import TeyesApiClient, TeyesError  # noqa: E402
from teyes.mqtt import MqttConnection, TeyesMqttClient  # noqa: E402
from teyes.telemetry import normalise, parse_message  # noqa: E402
from teyes.const import (  # noqa: E402
    MQTT_CLIENT_PREFIX,
    MQTT_TOPIC_PREFIX,
    MQTT_WS_URL,
)


def _broker_url(server: dict) -> str:
    host = str(server.get("url") or "").strip()
    if not host:
        return MQTT_WS_URL
    path = str(server.get("path") or "/websocket")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{server.get('protocol') or 'wss'}://{host}{path}"


async def _wait_online(client: TeyesApiClient, imei: str, poll: int) -> dict:
    """Poll until the head unit reports itself online, then return its state."""
    while True:
        state = await client.async_get_car_mq_state(imei)
        online = str(state.get("isonline")) in ("1", "true", "True")
        acc = state.get("acc")
        print(f"  isonline={state.get('isonline')} acc={acc} — "
              f"{'ONLINE' if online else 'waiting'}", flush=True)
        if online:
            return state
        await asyncio.sleep(poll)


async def _listen(client: TeyesApiClient, imei: str, state: dict, minutes: int) -> None:
    server = state.get("server") if isinstance(state.get("server"), dict) else {}
    seen = 0

    async def connection() -> MqttConnection:
        fresh = await client.async_get_car_mq_state(imei)
        srv = fresh.get("server") if isinstance(fresh.get("server"), dict) else server
        return MqttConnection(
            url=_broker_url(srv),
            client_id=f"{MQTT_CLIENT_PREFIX}{imei}",
            username=imei,
            password=str((srv or {}).get("apikey") or ""),
            topics=[f"{MQTT_TOPIC_PREFIX}{imei}"],
        )

    def on_message(topic: str, payload: bytes) -> None:
        nonlocal seen
        seen += 1
        raw = parse_message(payload)
        print(f"\n*** PUBLISH #{seen} on {topic} ({len(payload)} bytes) ***")
        print(json.dumps(raw, ensure_ascii=False, indent=1))
        print("  parsed ->", normalise(raw, "wgs84").fields)

    def on_connected(up: bool) -> None:
        print("  subscribed — waiting for the car to publish…" if up else "  disconnected")

    mqtt = TeyesMqttClient(aiohttp_session, connection, on_message, on_connected)
    mqtt.start()
    try:
        await asyncio.sleep(minutes * 60)
    finally:
        await mqtt.async_stop()
    print(f"\ndone — captured {seen} message(s).")
    if not seen:
        print("No PUBLISH arrived. The car was online but quiet; try again while "
              "driving, or leave it running longer.")


aiohttp_session: aiohttp.ClientSession


async def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Teyes car data.")
    parser.add_argument("--token", required=True)
    parser.add_argument("--uid", required=True)
    parser.add_argument("--imei", required=True)
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--poll", type=int, default=30)
    parser.add_argument("--once", action="store_true", help="print state and exit")
    args = parser.parse_args()

    global aiohttp_session
    async with aiohttp.ClientSession() as session:
        aiohttp_session = session
        client = TeyesApiClient(
            session, token=args.token, uid=args.uid, device_id=args.imei
        )
        try:
            state = await client.async_get_car_mq_state(args.imei)
        except TeyesError as err:
            print("Could not read the car state:", err)
            return 1
        print("car state:", json.dumps(
            {k: v for k, v in state.items() if k != "server"}, ensure_ascii=False))
        if args.once:
            return 0
        if str(state.get("isonline")) not in ("1", "true", "True"):
            print("\nHead unit is offline. Waiting for it to come online "
                  "(ignition on + its own internet)…")
            state = await _wait_online(client, args.imei, args.poll)
        await _listen(client, args.imei, state, args.minutes)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
