"""Base entity for the Teyes integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import TeyesCoordinator


def _first(record: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value)
    return None


class TeyesEntity(CoordinatorEntity[TeyesCoordinator]):
    """Base entity for one car on a Teyes account."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TeyesCoordinator, device_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._key = key
        self._attr_unique_id = f"{device_id}_{key}"
        car = coordinator.car(device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=_first(car, ("title", "name", "nickname")) or device_id,
            manufacturer=MANUFACTURER,
            model=_first(car, ("channel", "model", "mcu")),
            sw_version=_first(car, ("version", "app_version_name", "mcu")),
            serial_number=device_id,
        )

    @property
    def car(self) -> dict[str, Any]:
        """The car's latest `selectCar` record."""
        return self.coordinator.car(self._device_id)

    @property
    def state_data(self) -> dict[str, Any]:
        """The car's latest `getCarMqState` reply."""
        return self.coordinator.state(self._device_id)

    @property
    def telemetry(self) -> dict[str, Any]:
        """The car's merged live values."""
        return self.coordinator.telemetry_fields(self._device_id)

    @property
    def available(self) -> bool:
        """Available while the car is still on the account."""
        return super().available and self._device_id in self.coordinator.cars
