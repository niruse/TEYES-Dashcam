"""Sensors for the Teyes integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    DEGREE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import TeyesCoordinator
from .entity import TeyesEntity


def _time(value: Any) -> datetime | None:
    """Parse one of the ISO stamps the telemetry parser produces."""
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _epoch_ms(value: Any) -> datetime | None:
    """Read one of the millisecond stamps the car state carries."""
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return dt_util.utc_from_timestamp(stamp / 1000)


@dataclass(frozen=True, kw_only=True)
class TeyesSensorDescription(SensorEntityDescription):
    """Describes a Teyes sensor."""

    value_fn: Callable[[TeyesCoordinator], Any]
    attributes_fn: Callable[[TeyesCoordinator], dict[str, Any]] | None = None


SENSORS: tuple[TeyesSensorDescription, ...] = (
    TeyesSensorDescription(
        key="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.telemetry.fields.get("speed"),
    ),
    TeyesSensorDescription(
        key="heading",
        translation_key="heading",
        native_unit_of_measurement=DEGREE,
        icon="mdi:compass",
        value_fn=lambda c: c.telemetry.fields.get("heading"),
    ),
    TeyesSensorDescription(
        key="altitude",
        translation_key="altitude",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.telemetry.fields.get("altitude"),
    ),
    TeyesSensorDescription(
        key="satellites",
        translation_key="satellites",
        icon="mdi:satellite-variant",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.telemetry.fields.get("satellites"),
    ),
    TeyesSensorDescription(
        key="mileage",
        translation_key="mileage",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.telemetry.fields.get("mileage"),
    ),
    TeyesSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.telemetry.fields.get("voltage"),
    ),
    TeyesSensorDescription(
        key="gps_time",
        translation_key="gps_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: _time(c.telemetry.fields.get("gps_time")),
    ),
    # The cloud remembers when the ignition was last switched off, which is
    # the only timestamp available while the car is parked.
    TeyesSensorDescription(
        key="ignition_off_since",
        translation_key="ignition_off_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: _epoch_ms(c.state.get("acc_off")),
    ),
    TeyesSensorDescription(
        key="last_report",
        translation_key="last_report",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: _time(c.last_message_at),
    ),
    # The payload the car pushes is not documented, so this keeps the whole of
    # the last one where it can be read: the state counts the values that were
    # understood and the attributes hold the message itself.
    TeyesSensorDescription(
        key="last_message",
        translation_key="last_message",
        icon="mdi:car-connected",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: len(c.telemetry.fields),
        attributes_fn=lambda c: {
            "messages_received": c.message_count,
            "parsed": c.telemetry.fields,
            "raw": c.telemetry.raw,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensors."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TeyesSensor(coordinator, description) for description in SENSORS)


class TeyesSensor(TeyesEntity, SensorEntity):
    """One measured value from the car."""

    entity_description: TeyesSensorDescription

    def __init__(
        self, coordinator: TeyesCoordinator, description: TeyesSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the value, or None until the car has reported it."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra detail, for the sensors that carry any."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator)
