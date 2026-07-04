"""Device tracker platform for the NDW Charge Points integration.

Charge points are stationary, but modelling their coordinates as a
device_tracker entity lets Home Assistant plot them on the map.
"""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import ChargePointDataUpdateCoordinator
from .entity import ChargePointEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a location tracker for this entry's charge point."""
    coordinator: ChargePointDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id: str = entry.data[CONF_STATION_ID]

    async_add_entities([ChargePointDeviceTracker(coordinator, station_id)])


class ChargePointDeviceTracker(ChargePointEntity, TrackerEntity):
    """GPS location of a charge point."""

    _attr_translation_key = "location"
    _attr_icon = "mdi:map-marker"

    def __init__(
        self, coordinator: ChargePointDataUpdateCoordinator, station_id: str
    ) -> None:
        super().__init__(coordinator, station_id)
        self._attr_unique_id = f"{station_id}_location"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        coordinates = self._coordinates
        return coordinates[1] if coordinates else None

    @property
    def longitude(self) -> float | None:
        coordinates = self._coordinates
        return coordinates[0] if coordinates else None

    @property
    def location_accuracy(self) -> int:
        return 10
