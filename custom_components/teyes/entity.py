"""Base entity for the Teyes integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import TeyesCoordinator


def _first(state: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = state.get(name)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value)
    return None


class TeyesEntity(CoordinatorEntity[TeyesCoordinator]):
    """Base entity for the one car an entry follows."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: TeyesCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        state = coordinator.state
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
            name=_first(state, ("carname", "name", "nickname", "title"))
            or coordinator.device_id,
            manufacturer=MANUFACTURER,
            model=_first(state, ("model", "cartype", "type", "devicetype")),
            sw_version=_first(state, ("version", "fwver", "swversion", "romversion")),
            serial_number=coordinator.device_id,
        )

    @property
    def telemetry(self) -> dict[str, Any]:
        """The last values the car pushed."""
        return self.coordinator.telemetry.fields
