"""Camera platform for the Teyes integration.

The head unit streams each of its cameras through the Teyes cloud. Asking for
the stream tells the car to start (an MQTT command), then `lives/getLives`
hands back play URLs — HLS and HTTP-FLV among them, both of which Home
Assistant can play directly, so no WebRTC bridge is needed. The car has to be
online for the URLs to carry actual video.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import carinfo
from .const import CAMERA_DIRECTIONS, CMD_START_LIVE, CMD_STOP_LIVE, DOMAIN
from .coordinator import TeyesCommandError, TeyesCoordinator
from .entity import TeyesEntity

_LOGGER = logging.getLogger(__name__)

# Re-fetch the play URLs once they are this old; the URL's own auth_key lasts
# a while, but a fresh one is cheap and avoids a mid-view expiry.
_URL_TTL = 120


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a camera per fitted lens on each selected car."""
    coordinator: TeyesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TeyesCamera] = []
    for device_id in coordinator.selected:
        fitted = carinfo.cameras(coordinator.car(device_id))
        # Fall back to every direction if the car does not list its lenses.
        directions = [d for d in CAMERA_DIRECTIONS if d in fitted] or list(
            CAMERA_DIRECTIONS
        )
        entities += [TeyesCamera(coordinator, device_id, d) for d in directions]
    async_add_entities(entities)


class TeyesCamera(TeyesEntity, Camera):
    """One of the head unit's cameras, streamed from the cloud."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self, coordinator: TeyesCoordinator, device_id: str, direction: str
    ) -> None:
        TeyesEntity.__init__(self, coordinator, device_id, f"camera_{direction}")
        Camera.__init__(self)
        self._direction = direction
        self._attr_translation_key = f"camera_{direction}"
        self._urls: dict[str, Any] = {}
        self._urls_at = 0.0

    @property
    def available(self) -> bool:
        """There is only a feed to show while the car is online and streaming.

        Unlike the command controls, a camera has nothing to offer offline, so
        it reads unavailable then rather than showing a broken feed.
        """
        return super().available and self.coordinator.is_online(self._device_id)

    async def _async_urls(self) -> dict[str, Any]:
        """Return fresh play URLs, telling the car to start streaming first."""
        try:
            await self.coordinator.async_send_command(
                self._device_id,
                CMD_START_LIVE,
                {"type": self._direction, "name": ""},
            )
        except TeyesCommandError as err:
            # The car is offline; ask for the URLs anyway so the entity still
            # behaves, they simply will not carry video.
            _LOGGER.warning("Could not start live for %s: %s", self._device_id, err)
        if not self._urls or (time.monotonic() - self._urls_at) > _URL_TTL:
            app_version = self.car.get("app_version") or 0
            self._urls = await self.coordinator.client.async_get_lives(
                self._device_id, self._direction, app_version=app_version
            )
            self._urls_at = time.monotonic()
        return self._urls

    async def stream_source(self) -> str | None:
        """The URL Home Assistant should play — HLS, falling back to FLV."""
        urls = await self._async_urls()
        return urls.get("hls_play_url") or urls.get("flv_play_url")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """A still frame, pulled from the FLV stream with ffmpeg."""
        urls = await self._async_urls()
        source = urls.get("flv_play_url") or urls.get("hls_play_url")
        if not source:
            return None
        try:
            from homeassistant.components.ffmpeg import async_get_image

            return await async_get_image(
                self.hass, source, width=width, height=height
            )
        except Exception as err:  # noqa: BLE001 - ffmpeg may be absent/timeout
            _LOGGER.debug("Could not grab a still for %s: %s", self.entity_id, err)
            return None

    async def async_will_remove_from_hass(self) -> None:
        """Tell the car to stop streaming when the entity goes away."""
        try:
            await self.coordinator.async_send_command(
                self._device_id, CMD_STOP_LIVE, {"type": self._direction, "name": ""}
            )
        except TeyesCommandError:
            pass
        await super().async_will_remove_from_hass()
