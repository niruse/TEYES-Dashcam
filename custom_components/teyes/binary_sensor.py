"""Binary sensors for the Teyes integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import carinfo
from .const import DOMAIN
from .coordinator import TeyesCoordinator
from .entity import TeyesEntity

_TRUE = ("1", "on", "true", "yes", "open")


def _flag(record: dict[str, Any], key: str) -> bool | None:
    """Read one of the cloud's 0/1 (or on/off) flags."""
    value = record.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _TRUE


@dataclass(frozen=True, kw_only=True)
class TeyesBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Teyes binary sensor."""

    value_fn: Callable[[TeyesCoordinator, str], bool | None]


def _epoch(value: Any) -> float | None:
    """One of the cloud's millisecond stamps, as epoch seconds."""
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    return stamp / 1000 if stamp > 0 else None


def _ignition(coordinator: TeyesCoordinator, device_id: str) -> bool | None:
    """ACC state: the car's own push, unless the cloud heard a newer edge.

    The car pushes "acc off" as it shuts down (it is still online then) but
    rarely "acc on": it is not connected yet when the engine starts, so only the
    cloud hears it (`acc` in the state, `acc_on` in the car record). A pushed
    value used to win until the next restart, so drives after a pushed "off"
    were missed (2026-10-18..22: 9 of 11, the other 2 ~4 min late). The cloud's
    level now wins when its own stamp for that edge is newer than the push.
    """
    state, car = coordinator.state(device_id), coordinator.car(device_id)
    cloud = None
    for record in (state, car):
        if (value := _flag(record, "acc")) is not None:
            cloud = value
            break
    telemetry = coordinator.telemetry_fields(device_id)
    if "acc" not in telemetry:
        return cloud
    pushed = telemetry["acc"]
    pushed_at = coordinator.acc_pushed_at.get(device_id)
    if cloud is None or cloud == pushed or pushed_at is None:
        return pushed
    if cloud:
        edge = _epoch(car.get("acc_on"))
    else:
        edge = _epoch(state.get("acc_off") or car.get("acc_off"))
    return cloud if edge is not None and edge > pushed_at else pushed


SENSORS: tuple[TeyesBinarySensorDescription, ...] = (
    TeyesBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda c, d: c.is_online(d),
    ),
    TeyesBinarySensorDescription(
        key="ignition",
        translation_key="ignition",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_ignition,
    ),
    TeyesBinarySensorDescription(
        key="push_connected",
        translation_key="push_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c, d: c.push_connected(d),
    ),
    TeyesBinarySensorDescription(
        key="sim_present",
        translation_key="sim_present",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c, d: carinfo.flag(c.car(d), "sim"),
    ),
    TeyesBinarySensorDescription(
        key="privacy_mode",
        translation_key="privacy_mode",
        icon="mdi:eye-off",
        value_fn=lambda c, d: carinfo.flag(c.car(d), "isprivacymode"),
    ),
    TeyesBinarySensorDescription(
        key="update_available",
        translation_key="update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c, d: c.update_waiting(d),
    ),
    TeyesBinarySensorDescription(
        key="geofence_enabled",
        translation_key="geofence_enabled",
        icon="mdi:map-marker-radius",
        value_fn=lambda c, d: carinfo.geofence(c.car(d))["enabled"]
        if c.car(d)
        else None,
    ),
    TeyesBinarySensorDescription(
        key="camera",
        translation_key="camera",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c, d: _flag(c.state(d), "iscamera")
        if "iscamera" in c.state(d)
        else _flag(c.car(d), "iscamera"),
    ),
    TeyesBinarySensorDescription(
        key="surround_view",
        translation_key="surround_view",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c, d: _flag(c.state(d), "isqj")
        if "isqj" in c.state(d)
        else _flag(c.car(d), "isqj"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the binary sensors for every selected car."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeyesBinarySensor(coordinator, device_id, description)
        for device_id in coordinator.selected
        for description in SENSORS
    )


class TeyesBinarySensor(TeyesEntity, BinarySensorEntity):
    """One yes/no fact about a car."""

    entity_description: TeyesBinarySensorDescription

    def __init__(
        self,
        coordinator: TeyesCoordinator,
        device_id: str,
        description: TeyesBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current value, or None while the cloud has not said."""
        return self.entity_description.value_fn(self.coordinator, self._device_id)
