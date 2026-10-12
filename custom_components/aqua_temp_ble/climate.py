"""Climate platform for aqua_temp_ble.

Reads the coordinator snapshot (regs 8/13/14/19-25/26) and writes regs 8, 13
and 14 only. Writes go through the coordinator (serialized, confirmed by a
debounced refresh) so the entity state always reflects the *device-confirmed*
value, never an optimistic guess.

Spec 022 (FR-1/FR-3): the push-mode climate is ACTIVE BY DEFAULT — the
owner-confirmation gates are gone. A push entry always gets the full
surface: the OFF/HEAT display (the heat-only rule, spec 018), the setpoint
read, power writes through the confirmation cycle (1011) and setpoint
writes through the setpoint cycle (1046). The AUTOMATIC protections stay
byte-identical: the permanent value rails (registers.ensure_writable), the
mirror confirmation (the state only follows a fresh pushed block that
postdates the write) and the heat-only rule (OFF/HEAT only — the mode is
not pilotable). Classic entries keep today's exact entity (byte-identical,
US-6).
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
        # Spec 022 FR-1/FR-3: the push-mode climate is created
        # UNCONDITIONALLY, with the full surface — active by default
        # (the owner-confirmation gates are gone; the protections that
        # remain are automatic: value rails, mirror confirmation,
        # heat-only rule). Classic entries keep today's exact path (US-6).
        async_add_entities([AquaTempClimateEntity(coordinator, entry, push=True)])
        return
    async_add_entities([AquaTempClimateEntity(coordinator, entry)])


class AquaTempClimateEntity(ClimateEntity, AquaTempEntity):
    """The heat pump as a climate entity (device-level entity)."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    # Spec 019 FR-8: the step reads the guard's own constant (one source
    # of truth with ALLOWED_WRITE_SETPOINT — 5 raw / x10 = the 0.5 °C
    # granularity).
    _attr_target_temperature_step = (
        registers.SETPOINT_STEP_RAW / registers.TEMP_SCALE
    )

    def __init__(
        self, coordinator: AquaTempCoordinator, entry: ConfigEntry, push: bool = False
    ) -> None:
        ClimateEntity.__init__(self)
        AquaTempEntity.__init__(self, coordinator, entry, "climate")
        # Spec 022 FR-3: the push flag distinguishes the two entity
        # shapes — the push entity's surface is UNCONDITIONAL (the
        # gates are gone); the classic entity is byte-identical (US-6).
        self._push = push

    # ------------------------------------------------- the push surface
    # Spec 022 (FR-1/FR-3): active by default — the push entity always
    # advertises the full surface. The protections that shape it are
    # AUTOMATIC and permanent: the heat-only rule (OFF/HEAT only — the
    # mode is not pilotable), the value rails behind the write cycles
    # and the mirror confirmation.
    @property
    def hvac_modes(self) -> list[HVACMode]:
        if self._push:
            # Heat-only unit (the 017 FR-8 rule stands): COOL/AUTO are
            # unsupported — the vendor's own app refuses mode changes
            # here and its mode write would clobber an unpinned pair
            # register (research 017 §5). OFF/HEAT map to the proven
            # power writes only.
            return [HVACMode.OFF, HVACMode.HEAT]
        return self._attr_hvac_modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        if self._push:
            # Spec 022: unconditional — TURN_ON/TURN_OFF (where the
            # installed HA defines them) + TARGET_TEMPERATURE.
            features = ClimateEntityFeature(0)
            if getattr(ClimateEntityFeature, "TURN_ON", None) is not None:
                features |= ClimateEntityFeature.TURN_ON
            if getattr(ClimateEntityFeature, "TURN_OFF", None) is not None:
                features |= ClimateEntityFeature.TURN_OFF
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
            return features
        return self._attr_supported_features

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The write diagnostics: each cycle's last stage dict (the
        mirror's verdict — never an entity state; the tile's truth
        stays the pushed mirror alone, contracts 017 §5 / 019 §5.2).
        The classic entity has none."""
        if not self._push:
            return None
        attrs: dict[str, Any] = {}
        last = self.coordinator.last_control_write
        if last is not None:
            attrs["last_power_write"] = dict(last)
        last_sp = self.coordinator.last_setpoint_write
        if last_sp is not None:
            attrs["last_setpoint_write"] = dict(last_sp)
        return attrs or None

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
        # Real activity (the compressor) is not observable — the binary
        # registers were never pinned and the surface was pruned (spec
        # 021) — do not guess.
        return None

    @property
    def current_temperature(self) -> float | None:
        data = self.coordinator.data
        if data is None:
            return None
        if data.mode == "push":
            # FR-12 (spec 022): the climate's current temperature is
            # the INLET water temperature — the quantity the machine
            # regulates (register 2047, T02 — the live-proven inlet
            # [P]; the same source, sentinel and plausibility rules
            # as the inlet sensor entity — one quantity, one source,
            # contract 013 §4.1). The ambient temperature (2050) NEVER
            # feeds the climate.
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
        if self._push:
            # Spec 019 FR-8: the push-mode bounds are the guard's own
            # range — one source of truth with ALLOWED_WRITE_SETPOINT
            # (the static [D] bounds 10.0/40.0; the [A-weak] ladder
            # bound candidates are NOT displayed until a future spec
            # pins them, research 019 §10 U-5).
            celsius = registers.SETPOINT_MIN_RAW / registers.TEMP_SCALE
            value = self._to_display_unit(celsius)
            return value if value is not None else celsius
        data = self.coordinator.data
        value = self._to_display_unit(data.min_target) if data else None
        if value is not None:
            return value
        fallback = self._to_display_unit(registers.FALLBACK_MIN_TARGET)
        return fallback if fallback is not None else registers.FALLBACK_MIN_TARGET

    @property
    def max_temp(self) -> float:
        if self._push:
            # Spec 019 FR-8: the push-mode upper bound — the guard's own
            # constant (40.0 °C), the same number the funnel enforces.
            celsius = registers.SETPOINT_MAX_RAW / registers.TEMP_SCALE
            value = self._to_display_unit(celsius)
            return value if value is not None else celsius
        data = self.coordinator.data
        value = self._to_display_unit(data.max_target) if data else None
        if value is not None:
            return value
        fallback = self._to_display_unit(registers.FALLBACK_MAX_TARGET)
        return fallback if fallback is not None else registers.FALLBACK_MAX_TARGET

    # ------------------------------------------------------------ write path
    async def async_turn_on(self) -> None:
        """The push entity sends the PROVEN power-ON write (1011 = 1)
        through the confirmation cycle — the mirror decides the
        displayed state, never the button (spec 022: unconditional).
        The classic entity defers to the base implementation."""
        if self._push:
            await self.coordinator.async_control_write_power(1)
            return
        await super().async_turn_on()

    async def async_turn_off(self) -> None:
        """The OFF twin — the PROVEN power-OFF write (1011 = 0) through
        the confirmation cycle; an unconfirmed OFF is never silent (the
        persistent notification, FR-3 step 7). The classic entity
        defers to the base implementation."""
        if self._push:
            await self.coordinator.async_control_write_power(0)
            return
        await super().async_turn_off()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._push:
            # OFF/HEAT are both PURE power writes (the proven pair);
            # anything else is unsupported — the mode is NOT pilotable
            # on this heat-only unit (the vendor's own app refuses
            # mode changes and its mode write would clobber register
            # 1013 with an unpinned setpoint-shaped value — spec 017
            # FR-8).
            if hvac_mode == HVACMode.OFF:
                await self.coordinator.async_control_write_power(0)
                return
            if hvac_mode == HVACMode.HEAT:
                await self.coordinator.async_control_write_power(1)
                return
            raise HomeAssistantError(
                f"unsupported hvac mode {hvac_mode!r}: this unit is "
                "heat-only — set_hvac_mode exposes OFF and HEAT only, "
                "both pure power writes (spec 017 FR-8)"
            )
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
        if self._push:
            # The push entity writes the target through the setpoint
            # confirmation cycle (validate -> funnel -> write ->
            # trigger -> the MIRROR decides — contract 019 §5). The
            # write is allowed regardless of power state (contract
            # 019 §5.4 — the register is the machine's stored
            # parameter) and is NEVER clamped: a value off the 0.5
            # step or outside 10.0-40.0 °C raises loudly through the
            # cycle (contract 019 §2.2).
            temp = kwargs.get(ATTR_TEMPERATURE)
            if temp is None:
                return
            celsius = self._from_display_unit(temp)
            await self.coordinator.async_control_write_setpoint(celsius)
            return
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