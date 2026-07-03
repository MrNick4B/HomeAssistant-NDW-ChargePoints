"""Sensor platform for the NDW Charge Points integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import ChargePointDataUpdateCoordinator
from .entity import ChargePointEntity

# The OCPI connector_type, power_type and connector_format enums as used by
# the NDW API. Kept in sync with the "state" translations in
# strings.json/translations — add a new value here *and* there if the API
# ever adds one.
CONNECTOR_TYPE_OPTIONS: list[str] = [
    "CHADEMO",
    "CHAOJI",
    "DOMESTIC_A",
    "DOMESTIC_B",
    "DOMESTIC_C",
    "DOMESTIC_D",
    "DOMESTIC_E",
    "DOMESTIC_F",
    "DOMESTIC_G",
    "DOMESTIC_H",
    "DOMESTIC_I",
    "DOMESTIC_J",
    "DOMESTIC_K",
    "DOMESTIC_L",
    "DOMESTIC_M",
    "DOMESTIC_N",
    "DOMESTIC_O",
    "GBT_AC",
    "GBT_DC",
    "IEC_60309_2_single_16",
    "IEC_60309_2_three_16",
    "IEC_60309_2_three_32",
    "IEC_60309_2_three_64",
    "IEC_62196_T1",
    "IEC_62196_T1_COMBO",
    "IEC_62196_T2",
    "IEC_62196_T2_COMBO",
    "IEC_62196_T3A",
    "IEC_62196_T3C",
    "NEMA_5_20",
    "NEMA_6_30",
    "NEMA_6_50",
    "NEMA_10_30",
    "NEMA_10_50",
    "NEMA_14_30",
    "NEMA_14_50",
    "PANTOGRAPH_BOTTOM_UP",
    "PANTOGRAPH_TOP_DOWN",
    "TESLA_R",
    "TESLA_S",
]

POWER_TYPE_OPTIONS: list[str] = ["AC1", "AC2", "AC2_SPLIT", "AC3", "DC"]

CONNECTOR_FORMAT_OPTIONS: list[str] = ["CABLE", "SOCKET"]

# Icon shown when we know exactly which connector shape a type/format is;
# anything without a dedicated MDI glyph falls back to the generic
# ev-plug-type2 look shared by the plain count sensors, so the whole
# "connector family" of sensors reads as one visual group.
CONNECTOR_FAMILY_ICON = "mdi:ev-plug-type2"

CONNECTOR_TYPE_ICONS: dict[str, str] = {
    "IEC_62196_T1": "mdi:ev-plug-type1",
    "IEC_62196_T1_COMBO": "mdi:ev-plug-ccs1",
    "IEC_62196_T2": "mdi:ev-plug-type2",
    "IEC_62196_T2_COMBO": "mdi:ev-plug-ccs2",
    "CHADEMO": "mdi:ev-plug-chademo",
    "TESLA_R": "mdi:ev-plug-tesla",
    "TESLA_S": "mdi:ev-plug-tesla",
}

# CABLE isn't listed here: its icon comes from the connector's own type
# instead (the plug on the end of the tethered cable), same as
# CONNECTOR_TYPE_ICONS. SOCKET has no specific plug to show. It's
# whatever cable you bring, so it keeps a generic icon instead.
SOCKET_ICON = "mdi:power-plug-outline"


def _availabilities(properties: dict[str, Any]) -> list[dict[str, Any]]:
    return properties.get("availabilities") or []


def _sum_field(properties: dict[str, Any], field: str) -> int:
    return sum(item.get(field, 0) or 0 for item in _availabilities(properties))


def _distinct_values(properties: dict[str, Any], field: str) -> list[str]:
    return sorted({item[field] for item in _availabilities(properties) if item.get(field)})


def _primary_value(properties: dict[str, Any], field: str) -> str | None:
    """Most-represented value of `field` across availability groups.

    Most charge points only have one availability group, so this is just
    that group's value. When a station mixes e.g. AC and DC connectors, the
    value backed by the most physical connectors (summed `total`) wins,
    with ties broken alphabetically for a stable result.
    """
    totals: dict[str, int] = {}
    for item in _availabilities(properties):
        value = item.get(field)
        if not value:
            continue
        totals[value] = totals.get(value, 0) + (item.get("total", 0) or 0)
    if not totals:
        return None
    return max(sorted(totals), key=lambda value: totals[value])


def _power_values_w(properties: dict[str, Any]) -> list[float]:
    """power_max per availability group, in Watts.

    This is each connector's *rated* maximum, not a live/actual charging
    power — the API has no such field. A car will often draw less than
    this (e.g. 11 kW on a connector rated at 22 kW) depending on its
    onboard charger; that's normal and not something this integration can
    report on. When there's only one availability group, min and max below
    are the same number by definition.
    """
    return [
        value
        for value in (item.get("power_max") for item in _availabilities(properties))
        if value
    ]


def _max_power_kw(properties: dict[str, Any]) -> int | None:
    values = _power_values_w(properties)
    return round(max(values) / 1000) if values else None


def _min_power_kw(properties: dict[str, Any]) -> int | None:
    values = _power_values_w(properties)
    return round(min(values) / 1000) if values else None


def _last_updated(properties: dict[str, Any]):
    value = properties.get("last_updated")
    if not value:
        return None
    return dt_util.parse_datetime(value)


@dataclass(frozen=True, kw_only=True)
class ChargePointSensorEntityDescription(SensorEntityDescription):
    """Describes a charge point sensor and how to derive its value."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[ChargePointSensorEntityDescription, ...] = (
    # -- Availability: the single most time-sensitive stat, shown first. --
    ChargePointSensorEntityDescription(
        # No unit on purpose: this is a plain count, not a physical
        # quantity. A unit like "connectors" would print as "24 connectors"
        # instead of a clean number and adds nothing for graphing — plain
        # numeric sensors get history/statistics graphs just fine.
        key="available",
        translation_key="available",
        icon=CONNECTOR_FAMILY_ICON,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda feature: _sum_field(feature.get("properties", {}), "available"),
        attrs_fn=lambda feature: {
            "availabilities": _availabilities(feature.get("properties", {}))
        },
    ),
    ChargePointSensorEntityDescription(
        key="total",
        translation_key="total",
        icon=CONNECTOR_FAMILY_ICON,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda feature: _sum_field(feature.get("properties", {}), "total"),
    ),
    # -- Connector: what you'd need to plug in here. --
    ChargePointSensorEntityDescription(
        key="connector_type",
        translation_key="connector_type",
        icon=CONNECTOR_FAMILY_ICON,
        device_class=SensorDeviceClass.ENUM,
        options=CONNECTOR_TYPE_OPTIONS,
        value_fn=lambda feature: _primary_value(
            feature.get("properties", {}), "connector_type"
        ),
        attrs_fn=lambda feature: {
            "types": _distinct_values(feature.get("properties", {}), "connector_type")
        },
    ),
    ChargePointSensorEntityDescription(
        key="connector_format",
        translation_key="connector_format",
        icon=CONNECTOR_FAMILY_ICON,
        device_class=SensorDeviceClass.ENUM,
        options=CONNECTOR_FORMAT_OPTIONS,
        value_fn=lambda feature: _primary_value(
            feature.get("properties", {}), "connector_format"
        ),
        attrs_fn=lambda feature: {
            "formats": _distinct_values(feature.get("properties", {}), "connector_format")
        },
    ),
    # -- Power: how fast you'd charge, once you know it fits. --
    ChargePointSensorEntityDescription(
        key="power_max",
        translation_key="power_max",
        icon="mdi:lightning-bolt",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda feature: _max_power_kw(feature.get("properties", {})),
    ),
    ChargePointSensorEntityDescription(
        key="power_min",
        translation_key="power_min",
        icon="mdi:lightning-bolt-outline",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda feature: _min_power_kw(feature.get("properties", {})),
    ),
    ChargePointSensorEntityDescription(
        key="power_type",
        translation_key="power_type",
        icon="mdi:current-ac",
        device_class=SensorDeviceClass.ENUM,
        options=POWER_TYPE_OPTIONS,
        value_fn=lambda feature: _primary_value(feature.get("properties", {}), "power_type"),
        attrs_fn=lambda feature: {
            "types": _distinct_values(feature.get("properties", {}), "power_type")
        },
    ),
    # -- Where and who: context you'll mostly already know from setup. --
    ChargePointSensorEntityDescription(
        key="address",
        translation_key="address",
        icon="mdi:map-marker",
        value_fn=lambda feature: feature.get("properties", {}).get("address"),
    ),
    ChargePointSensorEntityDescription(
        key="operator",
        translation_key="operator",
        icon="mdi:domain",
        value_fn=lambda feature: (
            feature.get("properties", {}).get("operator_name")
            or feature.get("properties", {}).get("cpo_id")
        ),
        attrs_fn=lambda feature: {
            "owner_name": feature.get("properties", {}).get("owner_name"),
            "suboperator_name": feature.get("properties", {}).get("suboperator_name"),
        },
    ),
    # -- Diagnostic: technical/freshness details, least relevant day to day. --
    ChargePointSensorEntityDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda feature: _last_updated(feature.get("properties", {})),
    ),
    ChargePointSensorEntityDescription(
        key="country",
        translation_key="country",
        icon="mdi:flag-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda feature: feature.get("properties", {}).get("country"),
    ),
    ChargePointSensorEntityDescription(
        key="station_id",
        translation_key="station_id",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda feature: feature.get("id"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors for this entry's charge point."""
    coordinator: ChargePointDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    station_id: str = entry.data[CONF_STATION_ID]

    async_add_entities(
        ChargePointSensor(coordinator, station_id, description)
        for description in SENSOR_DESCRIPTIONS
    )


class ChargePointSensor(ChargePointEntity, SensorEntity):
    """A single field of a monitored charge point."""

    entity_description: ChargePointSensorEntityDescription

    def __init__(
        self,
        coordinator: ChargePointDataUpdateCoordinator,
        station_id: str,
        description: ChargePointSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, station_id)
        self.entity_description = description
        self._attr_unique_id = f"{station_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        if self.station_id not in self.coordinator.data:
            return None
        return self.entity_description.value_fn(self._feature)

    @property
    def icon(self) -> str | None:
        # Available/total/connector_type all reflect this station's actual
        # connector shape once known (e.g. a CHAdeMO station shows the
        # CHAdeMO icon everywhere), so the whole "connector family" of
        # sensors stays visually consistent with each other.
        if self.entity_description.key in ("available", "total", "connector_type"):
            connector_type = _primary_value(self._properties, "connector_type")
            return CONNECTOR_TYPE_ICONS.get(connector_type, CONNECTOR_FAMILY_ICON)
        if self.entity_description.key == "connector_format":
            if self.native_value == "SOCKET":
                return SOCKET_ICON
            # CABLE (or unknown): show the plug on the end of the cable.
            connector_type = _primary_value(self._properties, "connector_type")
            return CONNECTOR_TYPE_ICONS.get(connector_type, CONNECTOR_FAMILY_ICON)
        return super().icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        if self.station_id not in self.coordinator.data:
            return None
        return self.entity_description.attrs_fn(self._feature)
