"""Config flow for the NDW Charge Points integration.

Each config entry represents exactly one monitored charge point, so it can
be removed independently from Home Assistant without affecting any other
charge points found in the same bounding box.
"""
from __future__ import annotations

import math
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    ChargePointConnectionError,
    ChargePointRateLimitedError,
    async_fetch_charge_points,
    async_get_rate_limiter,
)
from .const import (
    CONF_BBOX,
    CONF_STATION_ID,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SEARCH_RADIUS_KM,
    DOMAIN,
    FEATURE_RESULT_LIMIT,
    MAX_BBOX_AREA_DEG2,
    MAX_SCAN_INTERVAL,
    MAX_SEARCH_RADIUS_KM,
    MIN_SCAN_INTERVAL,
    MIN_SEARCH_RADIUS_KM,
)
from .geocoding import (
    AddressNotFoundError,
    GeocodingConnectionError,
    async_geocode_address,
    async_get_geocoding_rate_limiter,
)

BBOX_EXAMPLE = "5.136386,52.081982,5.172843,52.097560"
BBOXFINDER_URL = "https://bboxfinder.com"

# Form field keys below are only used transiently during setup (to build a
# bbox); they aren't part of the persisted config entry data.
CONF_ADDRESS = "address"
CONF_SEARCH_RADIUS = "search_radius"

# Margin kept around a single selected charge point once it's chosen, so
# ongoing polling fetches just that station instead of re-fetching
# whatever (possibly much larger) area was used to find it.
STATION_BBOX_RADIUS_KM = 0.05

KM_PER_DEGREE_LAT = 110.57
KM_PER_DEGREE_LON_AT_EQUATOR = 111.32


class BboxTooLargeError(ValueError):
    """Raised when the bbox exceeds the API's 1.0 degree^2 area limit."""

    def __init__(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> None:
        super().__init__("bbox area exceeds the API's 1.0 degree^2 limit")
        self.min_lon = min_lon
        self.min_lat = min_lat
        self.max_lon = max_lon
        self.max_lat = max_lat


def _parse_bbox(value: str) -> str:
    """Validate and normalize a "min_lon,min_lat,max_lon,max_lat" bbox string."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must contain exactly 4 comma separated numbers")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as err:
        raise ValueError("bbox values must be numbers") from err
    if not (-180 <= min_lon < max_lon <= 180):
        raise ValueError("invalid longitude range")
    if not (-90 <= min_lat < max_lat <= 90):
        raise ValueError("invalid latitude range")
    area = (max_lon - min_lon) * (max_lat - min_lat)
    if area > MAX_BBOX_AREA_DEG2:
        raise BboxTooLargeError(min_lon, min_lat, max_lon, max_lat)
    return f"{min_lon},{min_lat},{max_lon},{max_lat}"


def _bbox_size_km(err: BboxTooLargeError) -> str:
    """Approximate width x height of a too-large bbox, for the error message."""
    lat_mid = (err.min_lat + err.max_lat) / 2
    width_km = (err.max_lon - err.min_lon) * KM_PER_DEGREE_LON_AT_EQUATOR * math.cos(
        math.radians(lat_mid)
    )
    height_km = (err.max_lat - err.min_lat) * KM_PER_DEGREE_LAT
    return f"{width_km:.0f} × {height_km:.0f} km"


def _bbox_from_point(lon: float, lat: float, radius_km: float) -> str:
    """Build a "min_lon,min_lat,max_lon,max_lat" bbox centered on a point."""
    lon_delta = radius_km / (KM_PER_DEGREE_LON_AT_EQUATOR * math.cos(math.radians(lat)))
    lat_delta = radius_km / KM_PER_DEGREE_LAT
    return f"{lon - lon_delta},{lat - lat_delta},{lon + lon_delta},{lat + lat_delta}"


def _minimal_bbox_for_feature(feature: dict[str, Any], fallback_bbox: str) -> str:
    """A small bbox tightly around one feature's own coordinates.

    Falls back to whatever bbox found it in the first place if the feature
    is somehow missing coordinates (shouldn't normally happen).
    """
    coordinates = feature.get("geometry", {}).get("coordinates")
    if not coordinates or len(coordinates) < 2:
        return fallback_bbox
    lon, lat = coordinates[0], coordinates[1]
    return _bbox_from_point(lon, lat, STATION_BBOX_RADIUS_KM)


def _result_count_notice(features: list[dict[str, Any]]) -> str:
    count = len(features)
    if count >= FEATURE_RESULT_LIMIT:
        return (
            f"Found {count} charge point(s). The API caps responses at "
            f"{FEATURE_RESULT_LIMIT} results, so this list may be incomplete "
            "— use a smaller bounding box to see everything in this area."
        )
    return f"Found {count} charge point(s) in this area."


def _station_label(feature: dict[str, Any]) -> str:
    properties = feature.get("properties", {})
    address = properties.get("address") or feature.get("id")
    operator = properties.get("operator_name") or properties.get("cpo_id") or "Unknown"
    availabilities = properties.get("availabilities") or []
    available = sum(item.get("available", 0) or 0 for item in availabilities)
    total = sum(item.get("total", 0) or 0 for item in availabilities)
    return f"{address} — {operator} ({available}/{total} available)"


def _station_options(features: list[dict[str, Any]]) -> list[SelectOptionDict]:
    ordered = sorted(
        features, key=lambda f: f.get("properties", {}).get("address") or ""
    )
    return [
        SelectOptionDict(value=feature["id"], label=_station_label(feature))
        for feature in ordered
    ]


def _station_title(feature: dict[str, Any]) -> str:
    return feature.get("properties", {}).get("address") or feature["id"]


class NwbChargePointsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow: find a bbox (drawn or from an address), then pick one charge point in it."""

    VERSION = 1

    def __init__(self) -> None:
        self._bbox: str | None = None
        self._features: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["address", "bbox"])

    async def _async_fetch_and_continue(
        self, bbox: str, errors: dict[str, str]
    ) -> config_entries.ConfigFlowResult | None:
        """Fetch charge points for `bbox`; on success, store and move on.

        Returns None (caller should re-show its form) if anything failed;
        `errors` is updated in place with the relevant error key.
        """
        session = async_get_clientsession(self.hass)
        rate_limiter = async_get_rate_limiter(self.hass)
        try:
            features = await async_fetch_charge_points(session, rate_limiter, bbox)
        except ChargePointRateLimitedError:
            errors["base"] = "rate_limited"
            return None
        except ChargePointConnectionError:
            errors["base"] = "cannot_connect"
            return None

        if not features:
            errors["base"] = "no_stations_found"
            return None

        self._bbox = bbox
        self._features = features
        return await self.async_step_stations()

    async def async_step_bbox(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {
            "bbox_example": BBOX_EXAMPLE,
            "bboxfinder_url": BBOXFINDER_URL,
        }
        if user_input is not None:
            try:
                bbox = _parse_bbox(user_input[CONF_BBOX])
            except BboxTooLargeError as err:
                errors["base"] = "bbox_too_large"
                description_placeholders["bbox_size"] = _bbox_size_km(err)
            except ValueError:
                errors["base"] = "invalid_bbox"
            else:
                result = await self._async_fetch_and_continue(bbox, errors)
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="bbox",
            data_schema=vol.Schema({vol.Required(CONF_BBOX): str}),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_address(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            radius_km = user_input[CONF_SEARCH_RADIUS]
            session = async_get_clientsession(self.hass)
            geocoding_rate_limiter = async_get_geocoding_rate_limiter(self.hass)
            try:
                lon, lat = await async_geocode_address(
                    session, geocoding_rate_limiter, address
                )
            except AddressNotFoundError:
                errors["base"] = "address_not_found"
            except GeocodingConnectionError:
                errors["base"] = "geocoding_failed"
            else:
                bbox = _bbox_from_point(lon, lat, radius_km)
                result = await self._async_fetch_and_continue(bbox, errors)
                if result is not None:
                    return result

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Required(
                    CONF_SEARCH_RADIUS, default=DEFAULT_SEARCH_RADIUS_KM
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SEARCH_RADIUS_KM,
                        max=MAX_SEARCH_RADIUS_KM,
                        step=0.1,
                        unit_of_measurement="km",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="address", data_schema=schema, errors=errors
        )

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        by_id = {feature["id"]: feature for feature in self._features}

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]
            await self.async_set_unique_id(station_id)
            self._abort_if_unique_id_configured()
            feature = by_id[station_id]
            bbox = _minimal_bbox_for_feature(feature, fallback_bbox=self._bbox)
            return self.async_create_entry(
                title=_station_title(feature),
                data={CONF_BBOX: bbox, CONF_STATION_ID: station_id},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=_station_options(self._features),
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="stations",
            data_schema=schema,
            description_placeholders={
                "result_count_notice": _result_count_notice(self._features)
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NwbChargePointsOptionsFlow:
        return NwbChargePointsOptionsFlow()


class NwbChargePointsOptionsFlow(config_entries.OptionsFlow):
    """Adjust how often this single charge point is polled.

    Deliberately has no __init__ override: recent Home Assistant versions
    set self.config_entry automatically, and assigning it manually here is
    both unnecessary and (as of newer core versions) raises an error.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
