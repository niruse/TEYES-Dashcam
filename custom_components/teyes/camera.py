"""Camera platform for the Teyes integration.

The head unit streams each of its cameras through the Teyes cloud. Asking for
the stream tells the car to start (an MQTT command), then `lives/getLives`
hands back play URLs — HLS and HTTP-FLV among them, both of which Home
Assistant can play directly, so no WebRTC bridge is needed. The car has to be
online for the URLs to carry actual video.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import Stream
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import carinfo
from .const import CAMERA_DIRECTIONS, DOMAIN
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
        self._last_image: bytes | None = None

    @property
    def available(self) -> bool:
        """There is only a feed to show while the car is online and streaming.

        Unlike the command controls, a camera has nothing to offer offline, so
        it reads unavailable then rather than showing a broken feed.
        """
        return super().available and self.coordinator.is_online(self._device_id)

    def _watched(self) -> bool:
        """Whether someone is playing this camera's stream right now."""
        return bool(self.stream and self.stream.outputs())

    async def _async_go_live(self, *, take_over: bool) -> dict[str, Any] | None:
        """Fetch the play URLs and get the car pushing this camera.

        `lives/getLives` returns routes, each with a `push_url` (where the car must publish)
        and the play URLs that serve that same stream; the coordinator sends the start with
        that push URL and keeps the car on one camera at a time. Returns None when the car
        stays on another camera (take_over=False) or cannot be reached.
        """
        if not self._urls or (time.monotonic() - self._urls_at) > _URL_TTL:
            app_version = self.car.get("app_version") or 0
            self._urls = await self.coordinator.client.async_get_lives(
                self._device_id, self._direction, app_version=app_version
            )
            self._urls_at = time.monotonic()
        try:
            live = await self.coordinator.async_live(
                self._device_id,
                self._direction,
                self._urls.get("push_url") or "",
                take_over=take_over,
                watched=self._watched,
            )
        except TeyesCommandError as err:
            _LOGGER.warning("Could not start live for %s: %s", self._device_id, err)
            return None
        return self._urls if live else None

    @staticmethod
    def _source(urls: dict[str, Any] | None) -> str | None:
        """FLV first (video within seconds of the start), HLS as the fallback."""
        return (urls.get("flv_play_url") or urls.get("hls_play_url")) if urls else None

    async def _async_wait_for_video(self, url: str, timeout: float = 20) -> None:
        """Wait until the play URL serves video: the car takes 5-20 s to start pushing,
        and a stream opened before that fails and only retries 10+ s later.

        The CDN answers 200 at once even when nothing is being pushed and then sends no
        body, so only actual bytes count (an FLV starts with b"FLV")."""
        session = async_get_clientsession(self.hass)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5, sock_read=4)
                ) as resp:
                    if resp.status == 200 and await resp.content.read(3):
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(1)

    async def async_create_stream(self) -> Stream | None:
        """Switch the car to this camera every time someone opens it.

        Home Assistant builds a camera's Stream once and reuses it, with the URL it got
        first, for every later viewer — so without this a second viewing never asked the
        car to start again, and played a URL whose auth key may have expired.
        """
        source = self._source(await self._async_go_live(take_over=True))
        if source:
            await self._async_wait_for_video(source)
            if self.stream is not None and self.stream.source != source:
                self.stream.update_source(source)
        return await super().async_create_stream()

    async def stream_source(self) -> str | None:
        """The play URL, with the car switched to this camera."""
        return self._source(await self._async_go_live(take_over=True))

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """A still frame pulled from the stream with ffmpeg.

        A still never switches the car away from another camera: it then shows the last
        frame it got, so a dashboard of camera cards does not keep flipping the car.
        """
        source = self._source(await self._async_go_live(take_over=False))
        if not source:
            return self._last_image
        try:
            from homeassistant.components.ffmpeg import async_get_image

            image = await async_get_image(
                self.hass, source, width=width, height=height
            )
        except Exception as err:  # noqa: BLE001 - ffmpeg may be absent/timeout
            _LOGGER.debug("Could not grab a still for %s: %s", self.entity_id, err)
            image = None
        if image:
            self._last_image = image
        return image or self._last_image

    async def async_will_remove_from_hass(self) -> None:
        """Stop the push if it is this camera's when the entity goes away."""
        await self.coordinator.async_release_live(self._device_id, self._direction)
        await super().async_will_remove_from_hass()
