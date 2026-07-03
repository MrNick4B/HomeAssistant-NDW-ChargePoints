"""Constants for the NDW Charge Points integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "ndw_charge_points"

API_URL: Final = (
    "https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/"
    "charge-point-data/v1/features"
)

ATTRIBUTION: Final = "Data provided by Nationaal Dataportaal Wegverkeer (NDW)"

CONF_BBOX: Final = "bbox"
CONF_STATION_ID: Final = "station_id"

DEFAULT_SCAN_INTERVAL: Final = 120
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 3600

# NDW DAFNE API limits: max 1.0 degree^2 bbox area, max 1000 features per
# response, max 10 requests/second (returns HTTP 429 if exceeded).
MAX_BBOX_AREA_DEG2: Final = 1.0
FEATURE_RESULT_LIMIT: Final = 1000

# Minimum spacing (seconds) between any two requests this integration makes,
# enforced by a single rate limiter shared by every config entry and the
# setup/options flow. Targets ~8 requests/second, below the API's 10/s cap,
# so a fleet of charge points refreshing at once (most notably right after
# Home Assistant starts) still can't exceed it.
MIN_REQUEST_INTERVAL: Final = 0.125
DATA_RATE_LIMITER: Final = "rate_limiter"
