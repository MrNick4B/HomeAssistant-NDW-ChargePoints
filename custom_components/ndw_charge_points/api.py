"""Minimal client for the NDW DAFNE charge-point-data GeoJSON API."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback

from .const import API_URL, DATA_RATE_LIMITER, DOMAIN, MIN_REQUEST_INTERVAL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30

# The API paginates via a "cursor" param and a Link: rel="next" response
# header (no documented page size). MAX_PAGES is a safety cap so a
# misbehaving server can't make us loop forever; it's well above what a
# 1000-feature bbox (the API's own cap) should ever need.
MAX_PAGES = 20


class ChargePointApiError(Exception):
    """Base error for the charge point API client."""


class ChargePointConnectionError(ChargePointApiError):
    """Raised when the API can't be reached or returns an error."""


class ChargePointRateLimitedError(ChargePointConnectionError):
    """Raised when the API returns HTTP 429 (max 10 requests/second)."""


class RateLimiter:
    """Spaces out calls so they're never more frequent than min_interval.

    One instance is shared by every config entry's coordinator plus the
    config/options flow (see `async_get_rate_limiter`), so the NDW API's
    10 requests/second limit holds regardless of how many charge points
    are configured. This matters most right when Home Assistant starts:
    every entry's first refresh fires at roughly the same moment, and
    without a shared limiter those requests aren't spaced out at all.
    """

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


@callback
def async_get_rate_limiter(hass: HomeAssistant) -> RateLimiter:
    """Return the RateLimiter shared by this whole integration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_RATE_LIMITER not in domain_data:
        domain_data[DATA_RATE_LIMITER] = RateLimiter(MIN_REQUEST_INTERVAL)
    return domain_data[DATA_RATE_LIMITER]


async def _async_fetch_page(
    session: aiohttp.ClientSession,
    rate_limiter: RateLimiter,
    url: str,
    params: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch one page and return (features, next_page_url_or_None)."""
    await rate_limiter.acquire()
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            response = await session.get(url, params=params)
            if response.status == 429:
                raise ChargePointRateLimitedError(
                    "Rate limited by NDW API (max 10 requests/second)"
                )
            response.raise_for_status()
            payload = await response.json(content_type=None)
            next_link = response.links.get("next")
            next_url = str(response.url.join(next_link["url"])) if next_link else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise ChargePointConnectionError(str(err)) from err

    features = payload.get("features")
    if not isinstance(features, list):
        _LOGGER.debug("Unexpected payload shape from NDW API: %s", payload)
        return [], None
    return [feature for feature in features if feature.get("id")], next_url


async def async_fetch_charge_points(
    session: aiohttp.ClientSession, rate_limiter: RateLimiter, bbox: str
) -> list[dict[str, Any]]:
    """Fetch every charge point feature for a bounding box, across pages.

    bbox must be formatted as "min_lon,min_lat,max_lon,max_lat" and cover
    at most 1.0 degree^2 (enforced by the caller, not here). Every request
    (including each page) goes through `rate_limiter`, which should be the
    single shared instance from `async_get_rate_limiter`.
    """
    features: list[dict[str, Any]] = []
    url = API_URL
    params: dict[str, str] | None = {"bbox": bbox}

    for _page in range(MAX_PAGES):
        page_features, next_url = await _async_fetch_page(session, rate_limiter, url, params)
        features.extend(page_features)
        if next_url is None:
            break
        url, params = next_url, None
    else:
        _LOGGER.warning(
            "Stopped paginating NDW charge-point-data after %d pages; "
            "results may be incomplete. Try a smaller bounding box.",
            MAX_PAGES,
        )

    return features
