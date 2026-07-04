"""Minimal client for OpenStreetMap's Nominatim geocoding API.

Used only during setup, to turn a free-text address into coordinates so a
search bounding box can be built around it. See the Nominatim usage policy
(https://operations.osmfoundation.org/policies/nominatim/): this makes at
most one request per address lookup (no autocomplete/type-ahead against
the API), rate-limited well under their 1 request/second cap, and
identifies itself with a proper User-Agent as required.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.core import HomeAssistant, callback

from .const import DATA_GEOCODING_RATE_LIMITER, DOMAIN, NOMINATIM_MIN_REQUEST_INTERVAL
from .api import RateLimiter

_LOGGER = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "HomeAssistant-NDW-ChargePoints/1.0 "
    "(+https://github.com/MrNick4B/HomeAssistant-NDW-ChargePoints)"
)

# The NDW API isn't strictly NL-only in practice: bounding boxes near the
# border also return charge points just across it in Belgium/Germany. So
# geocoding covers those neighbours too, not just the Netherlands, rather
# than blocking border-area searches. Left off entirely, common short
# queries risk matching a same-named place worldwide.
GEOCODING_COUNTRY_CODES = "nl,be,de"


class GeocodingError(Exception):
    """Base error for the geocoding client."""


class GeocodingConnectionError(GeocodingError):
    """Raised when Nominatim can't be reached or returns an error."""


class AddressNotFoundError(GeocodingError):
    """Raised when the address doesn't match any location."""


@callback
def async_get_geocoding_rate_limiter(hass: HomeAssistant) -> RateLimiter:
    """Return the RateLimiter shared for all Nominatim requests.

    Deliberately separate from the NDW API's own rate limiter
    (`api.async_get_rate_limiter`): different service, different limit.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_GEOCODING_RATE_LIMITER not in domain_data:
        domain_data[DATA_GEOCODING_RATE_LIMITER] = RateLimiter(
            NOMINATIM_MIN_REQUEST_INTERVAL
        )
    return domain_data[DATA_GEOCODING_RATE_LIMITER]


async def async_geocode_address(
    session: aiohttp.ClientSession, rate_limiter: RateLimiter, address: str
) -> tuple[float, float]:
    """Return (longitude, latitude) for the best match for `address`.

    Restricted to the Netherlands and its direct neighbours (see
    GEOCODING_COUNTRY_CODES), which also reduces ambiguity for common
    street names. Raises AddressNotFoundError if nothing matches.
    """
    await rate_limiter.acquire()
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            response = await session.get(
                NOMINATIM_URL,
                params={
                    "q": address,
                    "format": "jsonv2",
                    "limit": "1",
                    "countrycodes": GEOCODING_COUNTRY_CODES,
                },
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            results = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise GeocodingConnectionError(str(err)) from err

    if not results:
        raise AddressNotFoundError(address)

    try:
        return float(results[0]["lon"]), float(results[0]["lat"])
    except (KeyError, ValueError, TypeError) as err:
        raise GeocodingConnectionError(f"Unexpected geocoding response: {err}") from err
