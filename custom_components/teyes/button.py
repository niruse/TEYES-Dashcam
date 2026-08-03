"""Buttons for the Teyes integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_LOCATE, DOMAIN
from .coordinator import TeyesCommandError, TeyesCoordinator
from .entity import TeyesEntity

# Buttons map one-to-one onto a command the head unit accepts.
BUTTONS: tuple[tuple[ButtonEntityDescription, str], ...] = (
    (
        ButtonEntityDescription(
            key="locate", translation_key="locate", icon="mdi:crosshairs-gps"
        ),
        CMD_LOCATE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the buttons for every selected car."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeyesButton(coordinator, device_id, description, command)
        for device_id in coordinator.selected
        for description, command in BUTTONS
    )


class TeyesButton(TeyesEntity, ButtonEntity):
    """A button that sends one command to the car."""

    def __init__(
        self,
        coordinator: TeyesCoordinator,
        device_id: str,
        description: ButtonEntityDescription,
        command: str,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._command = command

    @property
    def available(self) -> bool:
        """A command only reaches the car while it is online."""
        return super().available and self.coordinator.is_online(self._device_id)

    async def async_press(self) -> None:
        """Send the command."""
        try:
            await self.coordinator.async_send_command(self._device_id, self._command)
        except TeyesCommandError as err:
            raise HomeAssistantError(str(err)) from err
