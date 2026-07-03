"""Base entity for the NDW Charge Points integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import ChargePointDataUpdateCoordinator


class ChargePointEntity(CoordinatorEntity[ChargePointDataUpdateCoordinator]):
    """Common base for all entities belonging to a single charge point."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: ChargePointDataUpdateCoordinator, station_id: str
    ) -> None:
        super().__init__(coordinator)
        self.station_id = station_id

        properties = coordinator.data.get(station_id, {}).get("properties", {})
        address = properties.get("address") or station_id
        operator = properties.get("operator_name") or properties.get("cpo_id")

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            name=address,
            manufacturer=operator,
            model="EV charge point",
            configuration_url="https://dotnl.ndw.nu/",
        )

    @property
    def available(self) -> bool:
        return super().available and self.station_id in self.coordinator.data

    @property
    def _feature(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.station_id, {})

    @property
    def _properties(self) -> dict[str, Any]:
        return self._feature.get("properties", {})

    @property
    def _coordinates(self) -> list[float] | None:
        return self._feature.get("geometry", {}).get("coordinates")
