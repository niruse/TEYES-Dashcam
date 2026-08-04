"""Screen lock for the Teyes integration.

The head unit can lock and unlock its own screen on command. There is no state
reported back, so this is an optimistic (assumed-state) lock: it shows what was
last asked for.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_LOCK_SCREEN, CMD_UNLOCK_SCREEN, DOMAIN
from .coordinator import TeyesCommandError, TeyesCoordinator
from .entity import TeyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a screen lock for every selected car."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeyesScreenLock(coordinator, device_id) for device_id in coordinator.selected
    )


class TeyesScreenLock(TeyesEntity, LockEntity):
    """Locks and unlocks the head unit's screen."""

    _attr_translation_key = "screen"
    _attr_assumed_state = True

    def __init__(self, coordinator: TeyesCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "screen_lock")
        self._is_locked: bool | None = None

    @property
    def is_locked(self) -> bool | None:
        """The last state asked for; None until first used."""
        return self._is_locked

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the screen."""
        await self._send(CMD_LOCK_SCREEN, locked=True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the screen."""
        await self._send(CMD_UNLOCK_SCREEN, locked=False)

    async def _send(self, command: str, *, locked: bool) -> None:
        try:
            await self.coordinator.async_send_command(self._device_id, command)
        except TeyesCommandError as err:
            raise HomeAssistantError(str(err)) from err
        self._is_locked = locked
        self.async_write_ha_state()
