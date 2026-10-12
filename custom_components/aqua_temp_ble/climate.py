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
    # Spec 019 FR-8: the step reads the guard's own constant (one source
    # of truth with ALLOWED_WRITE_SETPOINT — 5 raw / x10 = the 0.5 °C
    # granularity).
    _attr_target_temperature_step = (
        registers.SETPOINT_STEP_RAW / registers.TEMP_SCALE
    )

    def __init__(
        self, coordinator: AquaTempCoordinator, entry: ConfigEntry, read_only: bool = False
    ) -> None:
        ClimateEntity.__init__(self)
        AquaTempEntity.__init__(self, coordinator, entry, "climate")
        # Spec 015 FR-8: the push-mode climate is READ-ONLY while the
        # 017 activation gate is closed — no write feature advertised,
        # no settable hvac_modes (an empty list renders no mode buttons;
        # the reported hvac_mode stays a free-form state). Spec 017
        # FR-5 layers the gate-open surface on TOP via the live
        # properties below (the option flips without a reload).
        self._read_only = read_only
        if read_only:
            self._attr_supported_features = ClimateEntityFeature(0)
            self._attr_hvac_modes = []

    # ------------------------------------------------- live gate surface
    # Spec 017 FR-5: the properties read the coordinator's activation
    # gate LIVE (entry.options is the live mapping), so saving the
    # write_control option re-renders the entity without a reload —
    # the quickstart 017 §1 promise. While the gate is closed these
    # return exactly the 015 read-only surface (byte-identical, AC-3);
    # the classic entity never enters the branches at all.
    @property
    def hvac_modes(self) -> list[HVACMode]:
        if self._read_only:
            if self.coordinator.write_control_allowed:
                # Heat-only unit (FR-8): COOL/AUTO are unsupported —
                # the vendor's own app refuses mode changes here and
                # its mode write would clobber an unpinned pair
                # register (research 017 §5). OFF/HEAT map to the
                # proven power writes only.
                return [HVACMode.OFF, HVACMode.HEAT]
            return []
        return self._attr_hvac_modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        if self._read_only:
            # Spec 019 FR-8/FR-9: the gates COMPOSE — TURN_ON/TURN_OFF
            # behind write_control_allowed (017), TARGET_TEMPERATURE
            # behind write_setpoint_allowed (the THIRD gate, separate
            # by design: the owner may activate either without the
            # other; the power proof authorizes NOTHING setpoint —
            # non-transference, contract 019 §4.3). With both closed:
            # the exact 015-era surface (byte-identical, AC-2).
            features = ClimateEntityFeature(0)
            if self.coordinator.write_control_allowed:
                # The shipped HA climate API's turn-on/turn-off
                # features where the installed HA defines them.
                if getattr(ClimateEntityFeature, "TURN_ON", None) is not None:
                    features |= ClimateEntityFeature.TURN_ON
                if getattr(ClimateEntityFeature, "TURN_OFF", None) is not None:
                    features |= ClimateEntityFeature.TURN_OFF
            if self.coordinator.write_setpoint_allowed:
                # The setpoint write affordance — ONLY behind the
                # setpoint gate (the read display needs no gate: the
                # home is [P owner]).
                features |= ClimateEntityFeature.TARGET_TEMPERATURE
            return features
        return self._attr_supported_features

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Spec 017 FR-5 diagnostics + the spec 019 twin: each open
        gate's marker and its last-write stage dict. The locked surface
        (both write gates closed) keeps its exact prior surface (None,
        byte-identical, AC-2/US-6); the classic entity never enters the
        branches."""
        if not self._read_only:
            return None
        attrs: dict[str, Any] = {}
        if self.coordinator.write_control_allowed:
            attrs["write_control"] = "allowed"
            last = self.coordinator.last_control_write
            if last is not None:
                attrs["last_power_write"] = dict(last)
        # Spec 019 FR-11: the setpoint gate's marker + stage dict — the
        # gates compose, so the setpoint diagnostics appear with EITHER
        # gate open (the read display needs no gate at all — the home
        # is [P owner]; the tile's truth stays the pushed mirror
        # alone, contract 019 §5.2).
        if self.coordinator.write_setpoint_allowed:
            attrs["write_setpoint"] = "allowed"
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
        if self._read_only:
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
        if self._read_only:
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
        """Spec 015 FR-8 / Spec 017 FR-5: while the activation gate is
        closed the read-only entity refuses (the 015 surface,
        byte-identical); with the gate open it sends the PROVEN
        power-ON write (1011 = 1) through the confirmation cycle —
        the mirror decides the displayed state, never the button.
        The classic entity defers to the base implementation
        (byte-identical)."""
        if self._read_only:
            if self.coordinator.write_control_allowed:
                await self.coordinator.async_control_write_power(1)
                return
            self._refuse_write()
        await super().async_turn_on()

    async def async_turn_off(self) -> None:
        """Spec 015 FR-8 / Spec 017 FR-5: the OFF twin — the PROVEN
        power-OFF write (1011 = 0) through the confirmation cycle;
        an unconfirmed OFF is never silent (the persistent
        notification, FR-3 step 7). The classic entity defers to the
        base implementation (byte-identical)."""
        if self._read_only:
            if self.coordinator.write_control_allowed:
                await self.coordinator.async_control_write_power(0)
                return
            self._refuse_write()
        await super().async_turn_off()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._read_only:
            # Spec 017 FR-5/FR-8: with the gate open, OFF/HEAT are both
            # PURE power writes (the proven pair); anything else is
            # unsupported — the mode is NOT pilotable on this heat-only
            # unit (the vendor's own app refuses mode changes and its
            # mode write would clobber register 1013 with an unpinned
            # setpoint-shaped value, research 017 §5). With the gate
            # closed the 015 refusal stands (byte-identical).
            if self.coordinator.write_control_allowed:
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
            # Spec 019 FR-11: with the setpoint gate open, the push-mode
            # climate writes the target through the confirmation cycle
            # (validate -> funnel -> write -> trigger -> the MIRROR
            # decides — contract 019 §5). With the gate closed, the
            # exact 015 refusal (byte-identical, AC-2). The write is
            # allowed regardless of power state (contract 019 §5.4 —
            # the register is the machine's stored parameter) and is
            # NEVER clamped: a value off the 0.5 step or outside
            # 10.0-40.0 °C raises loudly through the cycle
            # (contract 019 §2.2).
            if self.coordinator.write_setpoint_allowed:
                temp = kwargs.get(ATTR_TEMPERATURE)
                if temp is None:
                    return
                celsius = self._from_display_unit(temp)
                await self.coordinator.async_control_write_setpoint(celsius)
                return
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