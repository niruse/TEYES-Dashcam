"""Read the fields a `selectCar` record carries.

The record is the account's own description of a car — its subscription, SIM,
firmware, cameras and geofence. The values arrive as strings, so this turns
them into the types the entities want.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.util import dt as dt_util

# A geofence side is "off" when the cloud hides it; anything else means the
# fence is configured.
_HIDDEN = ("", "hidden", "close", "0", "off", "none")


def flag(record: dict[str, Any], key: str) -> bool | None:
    """Read one of the record's 0/1 (or on/off) flags."""
    value = record.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "on", "true", "yes", "open")


def text(record: dict[str, Any], key: str) -> str | None:
    """Read a non-empty text field."""
    value = record.get(key)
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    return None


def validity_date(record: dict[str, Any]) -> date | None:
    """The service's expiry date, sent as `dd.mm.yyyy`."""
    raw = text(record, "validity")
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def epoch(record: dict[str, Any], key: str) -> datetime | None:
    """Read a whole-second epoch field as a UTC datetime."""
    value = record.get(key)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return dt_util.utc_from_timestamp(seconds)


def cameras(record: dict[str, Any]) -> list[str]:
    """The fitted cameras, sent as a comma-separated list."""
    raw = text(record, "camers") or text(record, "cameras")
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def geofence(record: dict[str, Any]) -> dict[str, Any]:
    """The electronic-fence configuration, if any.

    Two independent fences exist: a `scope` (a radius around a point) and a
    `region` (an area). Each is off until the app configures it.
    """
    scope_on = str(record.get("ef_scope_status") or "").strip().lower() not in _HIDDEN
    region_on = str(record.get("ef_region_status") or "").strip().lower() not in _HIDDEN
    return {
        "enabled": scope_on or region_on,
        "scope_enabled": scope_on,
        "region_enabled": region_on,
        "scope_position": record.get("ef_scope_postion"),
        "scope_range": record.get("ef_scope_range"),
        "region_position": record.get("ef_region_postion"),
        "alert_on_exit": bool(record.get("ef_out")),
        "alert_on_entry": bool(record.get("ef_in")),
    }
