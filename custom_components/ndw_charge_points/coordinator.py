"""DataUpdateCoordinator for the NDW Charge Points integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ChargePointConnectionError,
    ChargePointRateLimitedError,
    RateLimiter,
    async_fetch_charge_points,
    async_get_rate_limiter,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ChargePointDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetches all charge point features within a bounding box on an interval."""

    def __init__(self, hass: HomeAssistant, bbox: str, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.bbox = bbox
        self._session = async_get_clientsession(hass)
        # Shared across every config entry, so many charge points refreshing
        # at once (e.g. right after Home Assistant starts) still can't
        # exceed the API's 10 requests/second limit between them.
        self._rate_limiter: RateLimiter = async_get_rate_limiter(hass)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            features = await async_fetch_charge_points(
                self._session, self._rate_limiter, self.bbox
            )
        except ChargePointRateLimitedError as err:
            # The shared rate limiter should make this very unlikely; if it
            # still happens, the next regular update_interval tick is far
            # enough out to just retry normally.
            raise UpdateFailed(f"Rate limited by NDW API: {err}") from err
        except ChargePointConnectionError as err:
            raise UpdateFailed(f"Error communicating with NDW API: {err}") from err

        return {feature["id"]: feature for feature in features}
