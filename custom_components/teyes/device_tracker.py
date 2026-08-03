"""Device tracker for the Teyes integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeyesCoordinator
from .entity import TeyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the car tracker."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TeyesDeviceTracker(coordinator)])


class TeyesDeviceTracker(TeyesEntity, TrackerEntity):
    """Where the car last reported itself."""

    _attr_name = None  # the entity takes the device's name

    def __init__(self, coordinator: TeyesCoordinator) -> None:
        super().__init__(coordinator, "tracker")

    @property
    def source_type(self) -> SourceType:
        """The position comes from the head unit's GPS."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Latitude of the car."""
        return self.telemetry.get("latitude")

    @property
    def longitude(self) -> float | None:
        """Longitude of the car."""
        return self.telemetry.get("longitude")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the rest of the fix."""
        telemetry = self.telemetry
        return {
            key: telemetry[key]
            for key in ("speed", "heading", "altitude", "satellites", "gps_time")
            if key in telemetry
        }
