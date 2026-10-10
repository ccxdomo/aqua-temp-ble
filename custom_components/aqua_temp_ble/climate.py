"""Climate platform for aqua_temp_ble.

Reads the coordinator snapshot (regs 8/13/14/19-25/26) and writes regs 8, 13
and 14 only. Writes go through the coordinator (serialized, confirmed by a
debounced refresh) so the entity state always reflects the *device-confirmed*
value, never an optimistic guess.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator, HVAC_TO_MODE_VALUE
from .entity import AquaTempEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([AquaTempClimateEntity(coordinator, entry)])


class AquaTempClimateEntity(ClimateEntity, AquaTempEntity):
    """The heat pump as a climate entity (device-level entity)."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step = 0.5

    def __init__(
        self, coordinator: AquaTempCoordinator, entry: ConfigEntry
    ) -> None:
        ClimateEntity.__init__(self)
        AquaTempEntity.__init__(self, coordinator, entry, "climate")

    # ------------------------------------------------------------- unit conv
    @property
    def temperature_unit(self) -> str:
        return self.coordinator.temperature_unit

    def _to_display_unit(self, celsius: float | None) -> float | None:
        """Wire values are always Celsius [P]; convert at the entity boundary."""
        if celsius is None:
            return None
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return round(
                TemperatureConverter.convert(
                    celsius, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
                ),
                1,
            )
        return round(celsius, 1)

    def _from_display_unit(self, displayed: float) -> float:
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return TemperatureConverter.convert(
                displayed, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS
            )
        return displayed

    # ------------------------------------------------------------- read path
    @property
    def hvac_mode(self) -> HVACMode | None:
        data = self.coordinator.data
        return data.hvac_mode if data else None

    @property
    def hvac_action(self) -> HVACAction | None:
        data = self.coordinator.data
        if not data:
            return None
        if data.power is False:
            return HVACAction.OFF
        # Real activity (compressor) is only known after the binary registers
        # are calibrated — do not guess.
        return None

    @property
    def current_temperature(self) -> float | None:
        data = self.coordinator.data
        return self._to_display_unit(data.inlet_temp) if data else None

    @property
    def target_temperature(self) -> float | None:
        data = self.coordinator.data
        return self._to_display_unit(data.target_temp) if data else None

    @property
    def min_temp(self) -> float:
        data = self.coordinator.data
        value = self._to_display_unit(data.min_target) if data else None
        if value is not None:
            return value
        fallback = self._to_display_unit(registers.FALLBACK_MIN_TARGET)
        return fallback if fallback is not None else registers.FALLBACK_MIN_TARGET

    @property
    def max_temp(self) -> float:
        data = self.coordinator.data
        value = self._to_display_unit(data.max_target) if data else None
        if value is not None:
            return value
        fallback = self._to_display_unit(registers.FALLBACK_MAX_TARGET)
        return fallback if fallback is not None else registers.FALLBACK_MAX_TARGET

    # ------------------------------------------------------------ write path
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_write_register(registers.REG_POWER, 0)
            return
        data = self.coordinator.data
        if data is None or data.power is not True:
            # Power first, then mode: a mode write while powered off may be
            # ignored by the controller. [V] (runbook §4.4 step W)
            await self.coordinator.async_write_register(registers.REG_POWER, 1)
        mode_value = HVAC_TO_MODE_VALUE.get(hvac_mode)
        if mode_value is None:
            raise ValueError(f"unsupported hvac mode: {hvac_mode}")
        await self.coordinator.async_write_register(registers.REG_MODE, mode_value)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        # Clamp in display units against the bounds the device reports.
        temp = min(max(temp, self.min_temp), self.max_temp)
        celsius = self._from_display_unit(temp)
        value = int(round(celsius * registers.TEMP_SCALE))
        await self.coordinator.async_write_register(
            registers.REG_SET_CURRENT_TEMP, value
        )
        if registers.WRITE_MODE_SETPOINT_TOO:
            # Flip after runbook §4.4 step S if the app also writes the
            # per-mode setpoint (reg 19/20/21) on a setpoint change.
            data = self.coordinator.data
            per_mode = {
                HVACMode.COOL: registers.REG_SET_COOL_TARGET,
                HVACMode.HEAT: registers.REG_SET_HEAT_TARGET,
                HVACMode.AUTO: registers.REG_SET_AUTO_TARGET,
            }
            if data is not None and data.hvac_mode in per_mode:
                await self.coordinator.async_write_register(
                    per_mode[data.hvac_mode], value
                )