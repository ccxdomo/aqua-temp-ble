"""Binary sensor platform for aqua_temp_ble — registry-driven.

Registers 29..46 are not yet mapped to labels (runbook §4.4 step 4), so all
BINARIES entries have address=None and NO entity is created until the user
pins each register in registers.py. Nothing named "compressor" can show
wrong data.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator
from .entity import AquaTempEntity
from .registers import RegDef

_LOGGER = logging.getLogger(__name__)

BINARY_NAMES: dict[str, str] = {
    "compressor": "Compressor",
    "circulate_pump": "Circulate pump",
    "four_way_valve": "4-way valve",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[AquaTempBinarySensorEntity] = []
    for regdef in registers.BINARIES:
        if regdef.address is None:
            _LOGGER.debug(
                "binary sensor %s skipped: register not calibrated yet", regdef.key
            )
            continue
        entities.append(AquaTempBinarySensorEntity(coordinator, entry, regdef))
    async_add_entities(entities)


class AquaTempBinarySensorEntity(AquaTempEntity, BinarySensorEntity):
    """Compressor/pump/valve running flag (polarity via BINARY_ACTIVE_WHEN_NONZERO)."""

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
        regdef: RegDef,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, regdef.key)
        self._regdef = regdef
        self._attr_name = BINARY_NAMES.get(regdef.key, regdef.key)
        self._attr_icon = regdef.icon

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.binaries.get(self._regdef.key)