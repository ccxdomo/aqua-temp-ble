"""Climate platform for aqua_temp_ble.

Reads the coordinator snapshot (regs 8/13/14/19-25/26) and writes regs 8, 13
and 14 only. Writes go through the coordinator (serialized, confirmed by a
debounced refresh) so the entity state always reflects the *device-confirmed*
value, never an optimistic guess.

Spec 015 (FR-8): a "push" entry gets the climate ONLY behind the control
confirmation gate (`push_control_confirmed`, contract 015-A §4) — and then
strictly READ-ONLY. The pinned control homes (registers.PUSHED_CONTROL —
the owner's 2026-10-12 diff-revert evidence) prove what the pushed registers
REFLECT, never what writing them controls (contract 015-A §8): no write
feature is advertised, no write method succeeds, and nothing ever reaches
the coordinator's write funnel (`ensure_writable` stays byte-identical,
contract 015-B §3.4). Classic entries keep today's exact entity
(byte-identical, US-6).
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator, HVAC_TO_MODE_VALUE, decode_temp_logged
from .entity import AquaTempEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if str(entry.data.get("mode", "classic")) == "push":
        # Spec 015 FR-8 (contract 013 §4.4): in push mode the climate exists
        # ONLY behind the control confirmation gate — unconfirmed control
        # homes never reach the entity plane (the 0.10.0 code created the
        # entity unconditionally with an all-unknown state; this closes that
        # contract gap). Classic entries keep today's exact path (US-6).
        if not coordinator.push_control_confirmed:
            _LOGGER.debug(
                "climate skipped in push mode: control mapping not confirmed "
                "yet (quickstart 015 §5)"
            )
            return
        async_add_entities([AquaTempClimateEntity(coordinator, entry, read_only=True)])
        return
    async_add_entities([AquaTempClimateEntity(coordinator, entry)])


class AquaTempClimateEntity(ClimateEntity, AquaTempEntity):
    """The heat pump as a climate entity (device-level entity)."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step = 0.5

    def __init__(
        self, coordinator: AquaTempCoordinator, entry: ConfigEntry, read_only: bool = False
    ) -> None:
        ClimateEntity.__init__(self)
        AquaTempEntity.__init__(self, coordinator, entry, "climate")
        # Spec 015 FR-8: the push-mode climate is READ-ONLY — no write
        # feature advertised, no settable hvac_modes (an empty list renders
        # no mode buttons; the reported hvac_mode stays a free-form state).
        self._read_only = read_only
        if read_only:
            self._attr_supported_features = ClimateEntityFeature(0)
            self._attr_hvac_modes = []

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
        if data is None:
            return None
        if data.mode == "push":
            # Spec 015: the pushed inlet mirror (contract 013 §4.1 — one
            # quantity, one source): the same confirmed T-block register
            # the sensor entity reads, through the shared decode helper;
            # unconfirmed temperatures decode None (honest, never a
            # certain name on unconfirmed data).
            address = registers.PUSHED_TEMPS.get("inlet_water_temp")
            if address is None:
                return None
            return self._to_display_unit(
                decode_temp_logged(address, data.registers.get(address))
            )
        return self._to_display_unit(data.inlet_temp)

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
    def _refuse_write(self) -> None:
        """Spec 015 FR-8: the push-mode climate never writes — the pinned
        homes prove what the registers REFLECT, not what writing them
        controls (contract 015-A §8), and the write allowlist is
        unchanged (contract 015-B §1). Refuse loudly, never silently."""
        raise HomeAssistantError(
            "the push-mode climate is read-only (spec 015 FR-8); control "
            "writes are not available"
        )

    async def async_turn_on(self) -> None:
        """Spec 015 FR-8: refused on the read-only entity; the classic
        entity defers to the base implementation (byte-identical)."""
        if self._read_only:
            self._refuse_write()
        await super().async_turn_on()

    async def async_turn_off(self) -> None:
        """Spec 015 FR-8: refused on the read-only entity; the classic
        entity defers to the base implementation (byte-identical)."""
        if self._read_only:
            self._refuse_write()
        await super().async_turn_off()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._read_only:
            self._refuse_write()
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
        if self._read_only:
            self._refuse_write()
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