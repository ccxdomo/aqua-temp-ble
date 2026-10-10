"""Sensor platform for aqua_temp_ble — registry-driven.

Entities come from registers.SENSORS; entries whose address is None (not yet
verified live) are skipped entirely, so no unconfirmed data can ever appear
under a label.
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator
from .entity import AquaTempEntity
from .registers import RegDef

_LOGGER = logging.getLogger(__name__)

SENSOR_NAMES: dict[str, str] = {
    "inlet_water_temp": "Inlet water temperature",
    "outlet_water_temp": "Outlet water temperature",
    "ambient_temp": "Ambient temperature",
    "fault_code": "Fault code",
    "firmware": "Firmware",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[AquaTempSensorEntity] = []
    for regdef in registers.SENSORS:
        if regdef.address is None:
            _LOGGER.debug(
                "sensor %s skipped: register not calibrated yet", regdef.key
            )
            continue
        entities.append(AquaTempSensorEntity(coordinator, entry, regdef))
    async_add_entities(entities)


class AquaTempSensorEntity(AquaTempEntity, SensorEntity):
    """One temperature/fault/firmware sensor driven by a RegDef."""

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
        regdef: RegDef,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, regdef.key)
        self._regdef = regdef
        self._attr_name = SENSOR_NAMES.get(regdef.key, regdef.key)
        self._attr_device_class = regdef.device_class
        self._attr_native_unit_of_measurement = regdef.unit
        self._attr_state_class = regdef.state_class
        self._attr_icon = regdef.icon
        if regdef.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | int | str | None:
        data = self.coordinator.data
        if data is None:
            return None
        if self._regdef.key == "firmware":
            # Special-case: regs 48..51 joined as "a.b.c.d". [V] semantics
            return data.firmware
        raw = data.registers.get(self._regdef.address)
        if raw is None or raw == registers.SENTINEL_RAW:
            return None  # sensor absent -> state "unknown"
        if self._regdef.device_class == "temperature":
            return round(raw / self._regdef.scale, 1)
        if self._regdef.scale == 1.0:
            return raw
        return raw / self._regdef.scale