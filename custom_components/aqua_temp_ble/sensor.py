"""Sensor platform for aqua_temp_ble — registry-driven.

Entities come from registers.SENSORS; entries whose address is None (not yet
verified live) are skipped entirely, so no unconfirmed data can ever appear
under a label. A pinned address must also lie inside the polled base block
(7..51) — outside it the entity is skipped with a loud warning (FR-7:
high-block polling is a future phase).
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
from .coordinator import AquaTempCoordinator, decode_temp_logged
from .entity import AquaTempEntity
from .registers import RegDef

_LOGGER = logging.getLogger(__name__)

SENSOR_NAMES: dict[str, str] = {
    "inlet_water_temp": "Inlet water temperature",
    "outlet_water_temp": "Outlet water temperature",
    "ambient_temp": "Ambient temperature",
    "suction_temp": "Suction temperature",
    "coil_temp": "Coil temperature",
    "exhaust_temp": "Exhaust temperature",
    "fault_code": "Fault code",
    "firmware": "Firmware",
}


def _base_block_end() -> int:
    """Last polled base-block register (7..51) — for FR-7 gating messages."""
    return registers.BASE_BLOCK_START + registers.BASE_BLOCK_COUNT - 1


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
        if not registers.in_base_block(regdef.address):
            _LOGGER.warning(
                "sensor %s skipped: register %d is outside the polled base "
                "block %d..%d; high-block polling is not implemented "
                "(spec 003 §8)",
                regdef.key,
                regdef.address,
                registers.BASE_BLOCK_START,
                _base_block_end(),
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
        if self._regdef.device_class == "temperature":
            # FR-1/FR-2: both sentinel conventions + plausibility guard ->
            # state "unknown"; the shared helper warns once per address.
            return decode_temp_logged(self._regdef.address, raw)
        if raw is None or raw == registers.SENTINEL_RAW:
            return None  # register absent or sentinel -> state "unknown"
        if self._regdef.scale == 1.0:
            return raw
        return raw / self._regdef.scale