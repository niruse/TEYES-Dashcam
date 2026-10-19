"""The Teyes integration for Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeyesApiClient, TeyesAuthError, TeyesError
from .const import (
    CONF_DEVICE_IDS,
    CONF_TOKEN,
    CONF_UID,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import TeyesCoordinator
from .services import async_setup_services, async_unload_services


def build_client(hass: HomeAssistant, entry: ConfigEntry) -> TeyesApiClient:
    """Create an API client from an entry, keeping its session on disk."""

    @callback
    def _store_session(token: str, uid: str) -> None:
        """Persist a refreshed session so restarts do not need a new login."""
        if entry.data.get(CONF_TOKEN) == token and entry.data.get(CONF_UID) == uid:
            return
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token, CONF_UID: uid}
        )

    return TeyesApiClient(
        async_get_clientsession(hass),
        email=entry.data.get(CONF_EMAIL),
        password=entry.data.get(CONF_PASSWORD),
        token=entry.data.get(CONF_TOKEN),
        uid=entry.data.get(CONF_UID),
        timezone=hass.config.time_zone or "UTC",
        language=hass.config.language or "en",
        on_session=_store_session,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Teyes from a config entry."""
    client = build_client(hass, entry)
    if not client.token and client.can_relogin:
        try:
            await client.async_login()
        except TeyesAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TeyesError as err:
            raise ConfigEntryNotReady(str(err)) from err

    coordinator = TeyesCoordinator(hass, entry, client)
    await coordinator.async_restore_presence()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    # Listen for the car's own reports. The head unit only publishes while it
    # is powered and online, so the socket sits idle the rest of the time.
    # Started before the platforms: commands (a camera's start) go over it.
    coordinator.start_push()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    @callback
    def _stop_push() -> None:
        """Close the telemetry socket when the entry unloads."""
        hass.async_create_task(coordinator.async_stop_push())

    entry.async_on_unload(_stop_push)
    async_setup_services(hass)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options or the selected cars change.

    A refreshed session token is also written back to the entry, which fires
    this listener; reloading only when the options or the car list actually
    changed avoids a needless reload then.
    """
    coordinator: TeyesCoordinator | None = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    options_changed = dict(entry.options) != coordinator.options_snapshot
    cars_changed = list(entry.data.get(CONF_DEVICE_IDS) or []) != coordinator.selected
    if options_changed or cars_changed:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entries = hass.data.get(DOMAIN) or {}
        coordinator: TeyesCoordinator | None = entries.pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_stop_push()
        if not entries:
            async_unload_services(hass)
    return unload_ok
