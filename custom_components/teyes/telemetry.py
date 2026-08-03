"""Read the car's telemetry out of whatever shape the cloud pushes.

The MQTT payload is not documented and its field names are obfuscated in the
web bundle, so rather than hard-coding one layout this walks the message and
picks values out by name. Anything not recognised is still kept and exposed as
an attribute, so a payload this does not understand is visible rather than
lost.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

from .const import COORD_GCJ02

_LOGGER = logging.getLogger(__name__)

# Every spelling worth trying, best first.
_LATITUDE = ("lat", "latitude", "gpslat", "carlat", "la", "y")
_LONGITUDE = ("lng", "lon", "long", "longitude", "gpslng", "gpslon", "carlng", "lo", "x")
_SPEED = ("speed", "spd", "gpsspeed", "carspeed", "velocity")
_HEADING = ("direction", "course", "heading", "angle", "bearing", "dir")
_ALTITUDE = ("altitude", "alt", "height")
_SATELLITES = ("satellite", "satellites", "star", "stars", "gpsnum", "satnum")
_GPS_TIME = ("gpstime", "locationtime", "positiontime", "devicetime", "time",
             "updatetime", "timestamp", "ts", "datetime")
_ACC = ("acc", "accstatus", "ignition", "accstate")
_MILEAGE = ("mileage", "odo", "odometer", "totalmileage", "distance")
_VOLTAGE = ("voltage", "vol", "battery", "batteryvoltage", "power")

_TRUE = ("1", "on", "true", "yes", "open", "start")
_FALSE = ("0", "off", "false", "no", "close", "stop")

# GCJ-02 ("Mars coordinates") constants, for cars inside China.
_EE = 0.00669342162296594323
_A = 6378245.0


class Telemetry:
    """One normalised car report, plus the raw message it came from."""

    def __init__(self, raw: Any, fields: dict[str, Any]) -> None:
        self.raw = raw
        self.fields = fields

    def __bool__(self) -> bool:
        return bool(self.fields)


def parse_location_field(value: Any, coordinate_system: str) -> dict[str, Any]:
    """Read the `location` string a `selectCar` record carries.

    It is pipe-delimited `lat|lng|speed|heading|epoch_ms`, and gives a
    position for a parked car — before any live message has arrived.
    """
    if not isinstance(value, str) or "|" not in value:
        return {}
    parts = value.split("|")
    reading: dict[str, Any] = {}
    for name, index in (("latitude", 0), ("longitude", 1), ("speed", 2), ("heading", 3)):
        if index < len(parts) and (number := _number(parts[index])) is not None:
            reading[name] = number
    lat, lon = _rescale(reading.get("latitude"), reading.get("longitude"))
    if lat is None or lon is None:
        return {}
    if coordinate_system == COORD_GCJ02:
        lat, lon = _gcj02_to_wgs84(lat, lon)
    reading["latitude"], reading["longitude"] = round(lat, 7), round(lon, 7)
    if len(parts) > 4 and (when := _timestamp(parts[4])) is not None:
        reading["gps_time"] = when
    return reading


def parse_message(payload: bytes) -> Any:
    """Decode an MQTT payload, falling back to text when it is not JSON."""
    text = payload.decode("utf-8", "replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        _LOGGER.debug("Car message was not JSON: %s", text[:200])
        return {"message": text}


def normalise(raw: Any, coordinate_system: str) -> Telemetry:
    """Pull the values worth an entity out of a car message."""
    flat = _flatten(raw)
    fields: dict[str, Any] = {}

    latitude = _number(_pick(flat, _LATITUDE))
    longitude = _number(_pick(flat, _LONGITUDE))
    latitude, longitude = _rescale(latitude, longitude)
    if latitude is not None and longitude is not None:
        if coordinate_system == COORD_GCJ02:
            latitude, longitude = _gcj02_to_wgs84(latitude, longitude)
        fields["latitude"] = round(latitude, 7)
        fields["longitude"] = round(longitude, 7)

    for key, names in (
        ("speed", _SPEED),
        ("heading", _HEADING),
        ("altitude", _ALTITUDE),
        ("satellites", _SATELLITES),
        ("mileage", _MILEAGE),
        ("voltage", _VOLTAGE),
    ):
        if (value := _number(_pick(flat, names))) is not None:
            fields[key] = value

    if (when := _timestamp(_pick(flat, _GPS_TIME))) is not None:
        fields["gps_time"] = when
    if (acc := _boolean(_pick(flat, _ACC))) is not None:
        fields["acc"] = acc

    return Telemetry(raw, fields)


def _flatten(data: Any, out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Index every scalar in a message by its lowercased key, outermost first."""
    out = {} if out is None else out
    if isinstance(data, dict):
        for key, value in data.items():
            name = str(key).lower().replace("_", "")
            if isinstance(value, (dict, list)):
                _flatten(value, out)
            elif value not in (None, ""):
                out.setdefault(name, value)
    elif isinstance(data, list):
        for item in data:
            _flatten(item, out)
    return out


def _pick(flat: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in flat:
            return flat[name]
    return None


def _number(value: Any) -> float | None:
    """Coerce a value that may arrive as a string or already as a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _rescale(
    latitude: float | None, longitude: float | None
) -> tuple[float | None, float | None]:
    """Undo fixed-point coordinates, which trackers often send as integers."""
    if latitude is None or longitude is None:
        return None, None
    for divisor in (1, 1e6, 1e7, 1e5):
        lat, lon = latitude / divisor, longitude / divisor
        if abs(lat) <= 90 and abs(lon) <= 180 and (lat or lon):
            return lat, lon
    return None, None


def _timestamp(value: Any) -> str | None:
    """Turn an epoch or a formatted time into an ISO string in UTC."""
    if value in (None, ""):
        return None
    number = _number(value)
    if number is not None and number > 1_000_000_000:
        if number > 1_000_000_000_000:  # milliseconds
            number /= 1000
        try:
            return datetime.fromtimestamp(number, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            # The cloud sends wall-clock time for the timezone the request
            # asked for, so it is read as local time here.
            return datetime.strptime(text, fmt).astimezone().isoformat()
        except ValueError:
            continue
    return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    return None


def _out_of_china(latitude: float, longitude: float) -> bool:
    return not (73.66 < longitude < 135.05 and 3.86 < latitude < 53.55)


def _gcj02_to_wgs84(latitude: float, longitude: float) -> tuple[float, float]:
    """Undo the offset Chinese services apply to coordinates.

    Only meaningful inside China; elsewhere the pair is already WGS-84.
    """
    if _out_of_china(latitude, longitude):
        return latitude, longitude
    d_lat = _transform_lat(longitude - 105.0, latitude - 35.0)
    d_lon = _transform_lon(longitude - 105.0, latitude - 35.0)
    rad_lat = latitude / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return latitude - d_lat, longitude - d_lon


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    ret += 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    ret += 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret
