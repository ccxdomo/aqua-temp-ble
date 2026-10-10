"""Button platform: the "Probe registers" diagnostic button (§4.1)."""

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
    async_add_entities([AquaTempProbeButtonEntity(coordinator, entry)])


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