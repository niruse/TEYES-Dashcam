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

from .const import DOMAIN
from .coordinator import TeyesCoordinator
from .entity import TeyesEntity

_TRUE = ("1", "on", "true", "yes", "open")


def _flag(state: dict[str, Any], key: str) -> bool | None:
    """Read one of the cloud's 0/1 (or on/off) flags."""
    value = state.get(key)
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

    value_fn: Callable[[TeyesCoordinator], bool | None]


SENSORS: tuple[TeyesBinarySensorDescription, ...] = (
    TeyesBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda c: _flag(c.state, "isonline"),
    ),
    TeyesBinarySensorDescription(
        key="ignition",
        translation_key="ignition",
        device_class=BinarySensorDeviceClass.RUNNING,
        # The car's own reports win when it is sending them, since the polled
        # state only changes as often as the cloud is asked.
        value_fn=lambda c: (
            c.telemetry.fields["acc"]
            if "acc" in c.telemetry.fields
            else _flag(c.state, "acc")
        ),
    ),
    TeyesBinarySensorDescription(
        key="push_connected",
        translation_key="push_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.push_connected,
    ),
    TeyesBinarySensorDescription(
        key="camera",
        translation_key="camera",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: _flag(c.state, "iscamera"),
    ),
    TeyesBinarySensorDescription(
        key="surround_view",
        translation_key="surround_view",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: _flag(c.state, "isqj"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the binary sensors."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeyesBinarySensor(coordinator, description) for description in SENSORS
    )


class TeyesBinarySensor(TeyesEntity, BinarySensorEntity):
    """One yes/no fact about the car."""

    entity_description: TeyesBinarySensorDescription

    def __init__(
        self, coordinator: TeyesCoordinator, description: TeyesBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current value, or None while the cloud has not said."""
        return self.entity_description.value_fn(self.coordinator)
