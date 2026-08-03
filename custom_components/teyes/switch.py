"""Switches for the Teyes integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TeyesError
from .const import DOMAIN
from .coordinator import TeyesCoordinator
from .entity import TeyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the switches for every selected car."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeyesStealthSwitch(coordinator, device_id) for device_id in coordinator.selected
    )


class TeyesStealthSwitch(TeyesEntity, SwitchEntity):
    """Hides the car from the account's shared views (stealth mode).

    Backed by the car's `sm_status` (`hidden` / `normal`), a cloud setting, so
    it works whether or not the car is online.
    """

    _attr_translation_key = "stealth_mode"
    _attr_icon = "mdi:incognito"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TeyesCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "stealth")

    @property
    def is_on(self) -> bool | None:
        """Stealth is on while the car's status is hidden."""
        status = self.car.get("sm_status")
        if status in (None, ""):
            return None
        return str(status).lower() == "hidden"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, hidden: bool) -> None:
        car_id = self.car.get("id")
        if car_id is None:
            raise HomeAssistantError("The car's id is not known yet")
        try:
            await self.coordinator.client.async_set_stealth(car_id, hidden)
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
        # Reflect immediately, then confirm on the next poll.
        self.car["sm_status"] = "hidden" if hidden else "normal"
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
