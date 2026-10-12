"""Button platform: the diagnostic buttons (§4.1, spec 016 FR-5).

- "Probe registers": read a block and log every register (calibration).
- "Power write probe": run the spec-016 power-write proof probe — a
  DIAGNOSTIC instrument (a measurement, not a control surface): it
  toggles the unit ON then back OFF while watching the pushed mirror
  register 2011, and logs ONE verdict line at WARNING. The unit must
  be OFF and the vendor app closed before pressing (quickstart 016 §0).
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AquaTempCoordinator
from .entity import AquaTempEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            AquaTempProbeButtonEntity(coordinator, entry),
            AquaTempPowerProbeButtonEntity(coordinator, entry),
        ]
    )


class AquaTempProbeButtonEntity(AquaTempEntity, ButtonEntity):
    """Press to read the base block and log every register (calibration aid)."""

    def __init__(
        self, coordinator: AquaTempCoordinator, entry: ConfigEntry
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "probe_registers")
        self._attr_name = "Probe registers"
        self._attr_icon = "mdi:tune"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Probe the base block; the log goes to home-assistant.log (§4.3)."""
        _LOGGER.debug("probe button pressed")
        await self.coordinator.async_probe_block("base")


class AquaTempPowerProbeButtonEntity(AquaTempEntity, ButtonEntity):
    """Spec 016 FR-5: press to run the power-write proof probe.

    A DIAGNOSTIC entity (category diagnostic), NOT a control surface:
    the probe is a measurement instrument that writes the two
    disassembly-pinned power frames (ON then OFF), verifies the pushed
    mirror register 2011 follows and reverts, and logs ONE verdict
    line at WARNING (quickstart 016 §2's table). It always returns the
    unit to OFF when it can — and says so when it could not. The
    climate stays read-only; flipping WRITE_CONTROL_PROVEN after an
    EXECUTED + REVERSIBLE verdict is the owner's separate decision
    (contract 016 §4).
    """

    def __init__(
        self, coordinator: AquaTempCoordinator, entry: ConfigEntry
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "power_write_probe")
        self._attr_name = "Power write probe"
        self._attr_icon = "mdi:test-tube"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Run the diff-revert protocol; the verdict goes to the log."""
        _LOGGER.debug("power write probe button pressed")
        await self.coordinator.async_power_write_probe()