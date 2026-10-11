"""Binary sensor platform for aqua_temp_ble — registry-driven.

The O-family outputs (Compressor, Circulate pump, High fan, 4-way valve)
and the S-family safety inputs (HP/LP/Flow switches) are [V] placeholders
until calibration pins their registers (spec 003,
contracts/calibration-contract.md §4): entries with address=None create NO
entity, so no unconfirmed data can ever appear under a label. A pinned
address must also lie inside the polled base block (7..51) — outside it the
entity is skipped with a loud warning (FR-7). Polarity resolves per-RegDef
(active_when_nonzero), falling back to the global
BINARY_ACTIVE_WHEN_NONZERO default (FR-4).
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
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

BINARY_NAMES: dict[str, str] = {
    "compressor": "Compressor",
    "circulate_pump": "Circulate pump",
    "high_fan": "High fan",
    "four_way_valve": "4-way valve",
    "hp_switch": "HP switch",
    "lp_switch": "LP switch",
    "flow_switch": "Flow switch",
}


def _base_block_end() -> int:
    """Last polled base-block register (7..51) — for FR-7 gating messages."""
    return registers.BASE_BLOCK_START + registers.BASE_BLOCK_COUNT - 1


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
        if not registers.in_base_block(regdef.address):
            _LOGGER.warning(
                "binary sensor %s skipped: register %d is outside the polled "
                "base block %d..%d; high-block polling is not implemented "
                "(spec 003 §8)",
                regdef.key,
                regdef.address,
                registers.BASE_BLOCK_START,
                _base_block_end(),
            )
            continue
        entities.append(AquaTempBinarySensorEntity(coordinator, entry, regdef))
    async_add_entities(entities)


class AquaTempBinarySensorEntity(AquaTempEntity, BinarySensorEntity):
    """O-family output / S-family safety-input flag (per-RegDef polarity, FR-4)."""

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
        if regdef.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.binaries.get(self._regdef.key)