"""Buttons for the Teyes integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TeyesError
from .const import CMD_LOCATE, DOMAIN
from .coordinator import TeyesCommandError, TeyesCoordinator
from .entity import TeyesEntity

# Buttons that send an MQTT command (only reach the car while it is online).
COMMAND_BUTTONS: tuple[tuple[ButtonEntityDescription, str], ...] = (
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
    entities: list[ButtonEntity] = []
    for device_id in coordinator.selected:
        entities += [
            TeyesCommandButton(coordinator, device_id, description, command)
            for description, command in COMMAND_BUTTONS
        ]
        entities.append(TeyesDisable4gButton(coordinator, device_id))
    async_add_entities(entities)


class TeyesCommandButton(TeyesEntity, ButtonEntity):
    """A button that sends one MQTT command to the car."""

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


class TeyesDisable4gButton(TeyesEntity, ButtonEntity):
    """Disables the car's mobile data via the cloud.

    This is a REST call, so it works even while the car is offline. There is no
    matching "enable" from the cloud — the car is brought back with a wake SMS
    or from the head unit — so this is a one-way button, not a switch.
    """

    _attr_translation_key = "disable_4g"
    _attr_icon = "mdi:signal-off"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TeyesCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "disable_4g")

    async def async_press(self) -> None:
        client_id = str(self.state_data.get("clientid") or "")
        try:
            await self.coordinator.client.async_close_4g(self._device_id, client_id)
        except TeyesError as err:
            raise HomeAssistantError(str(err)) from err
