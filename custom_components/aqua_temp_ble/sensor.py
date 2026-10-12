"""Sensor platform for aqua_temp_ble — registry-driven.

Entities come from registers.SENSORS; entries whose address is None (not yet
verified live) are skipped entirely, so no unconfirmed data can ever appear
under a label. A pinned address must also lie inside the polled base block
(7..51) — outside it the entity is skipped with a loud warning (FR-7:
high-block polling is a future phase).

Spec 013 (FR-5/FR-6) adds the pushed-plane surface: entries with
mode == "push" get ALL six T-family sensors (values from the pushed
registers — spec 022: active by default, the calibration gate is
gone) plus the push_stream diagnostic. Spec 024 (FR-1) adds the
Fault code sensor — the display face of the pushed fault word
(register 2074): the panel code on the tile, where a problem
binary can only show its localized on/off words. The CLASSIC
fault_code and firmware entries are NOT created in push mode —
their base registers (47 / 48-51) are unreachable on this firmware
(law L1, spec 020 §5). Classic entries keep today's exact creation
rules (US-6).
"""

from __future__ import annotations

import logging
from typing import Any

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
    # Spec 013 FR-6: the push-stream health diagnostic (push-mode entries).
    "push_stream": "Push stream",
    # Spec 024 (FR-1): the fault-code display face (push-mode entries)
    # — the code the panel shows, on the tile.
    "pushed_fault_code": "Fault code",
}


def _base_block_end() -> int:
    """Last polled base-block register (7..51) — for FR-7 gating messages."""
    return registers.BASE_BLOCK_START + registers.BASE_BLOCK_COUNT - 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if str(entry.data.get("mode", "classic")) == "push":
        # Spec 013 FR-5/FR-6 + spec 022 FR-1/FR-3: the pushed-plane
        # entity surface — ACTIVE BY DEFAULT. ALL six T-family sensors
        # are created unconditionally (values from the pushed registers,
        # sentinel/plausibility-guarded by the shared helper); the
        # classic fault_code/firmware RegDefs stay uncreated (law L1);
        # the push_stream diagnostic always rides along (FR-6).
        entities: list[
            AquaTempSensorEntity
            | AquaTempPushStreamEntity
            | AquaTempFaultCodeSensorEntity
        ] = []
        for regdef in registers.SENSORS:
            if regdef.key not in registers.PUSHED_TEMPS:
                _LOGGER.debug(
                    "sensor %s skipped in push mode: no pushed register "
                    "(spec 013 FR-5)",
                    regdef.key,
                )
                continue
            entities.append(AquaTempSensorEntity(coordinator, entry, regdef))
        entities.append(AquaTempPushStreamEntity(coordinator, entry))
        # Spec 024 (FR-1): the fault-code display face — the code on
        # the tile (a problem binary renders localized on/off words,
        # so a code cannot ride its state — contract 024 §4).
        entities.append(AquaTempFaultCodeSensorEntity(coordinator, entry))
        async_add_entities(entities)
        return
    entities = []
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
        if data.mode == "push":
            # Spec 013 FR-5 (contract 013 §4.3): the pushed-plane read.
            # The key's register comes from registers.PUSHED_TEMPS at
            # render time — the single source of truth (a permuted
            # calibration outcome is a one-line edit there). The
            # capture's raws ride data.registers; the shared decode
            # helper handles the sentinels + plausibility on this
            # plane identically (no second decoder, contract 013 §7.2).
            address = registers.PUSHED_TEMPS.get(self._regdef.key)
            if address is None:
                return None
            return decode_temp_logged(address, data.registers.get(address))
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


class AquaTempPushStreamEntity(AquaTempEntity, SensorEntity):
    """The push-stream health diagnostic (spec 013 FR-6, contract 013 §6.3).

    State: `on` while the T-block store entry is fresh (age within the
    freshness window), `off` when it went stale between cycles;
    unavailable rides the coordinator's UpdateFailed (a stale stream
    fails the cycle honestly — the entities go unavailable together).
    Attributes: the mode, the per-start push counts and the first/last
    push ages — U-3 (the unproven long-run stream behavior, research
    013 §7) becomes observable by the owner without a terminal.
    """

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "push_stream")
        self._attr_name = SENSOR_NAMES["push_stream"]
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:bluetooth"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.mode != "push" or data.push_last_seen is None:
            return None
        age = self.coordinator.now() - data.push_last_seen
        return "on" if age <= self.coordinator.push_freshness_window() else "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        now = self.coordinator.now()
        attrs: dict[str, Any] = {
            "mode": data.mode,
            "per_start_counts": {
                str(start): count for start, count in sorted(data.push_counts.items())
            },
        }
        if data.push_first_seen is not None:
            attrs["first_push_age_s"] = round(now - data.push_first_seen, 1)
        if data.push_last_seen is not None:
            attrs["last_push_age_s"] = round(now - data.push_last_seen, 1)
        # Spec 015 FR-10: the control-candidate raws with their evidence
        # tags — the owner re-runs the diff (quickstart 015 §1) without a
        # terminal. Gate-independent diagnostics, never entity states.
        if data.mode == "push" and data.push_control_raws:
            attrs["control_candidates"] = {
                str(addr): {
                    "raw": raw,
                    "evidence": registers.PUSH_CONTROL_CANDIDATES[addr],
                }
                for addr, raw in sorted(data.push_control_raws.items())
            }
        return attrs


class AquaTempFaultCodeSensorEntity(AquaTempEntity, SensorEntity):
    """The fault word 2074 as a code string (spec 024, contract 024 §3).

    The DISPLAY face of the same register the Fault binary reads
    (one register, one source, two faces): "No fault" when the
    latched fault memory is empty since the last power-up, the
    PROVEN panel code when the raw maps (E03 = 256), "unknown code
    (raw N)" otherwise — never a guessed code, never a bit name
    (contract 023 §3). The fault word is LATCHED (research 024 §1 —
    the owner's 2026-10-12 power-cut proof): a non-zero state reports
    a MEMORIZED fault, not necessarily an active one; the README
    carries that semantics. `unknown` when the register is absent;
    unavailable with every entity while the stream is silent (the
    SHARED freshness gate — no fault-specific staleness code exists,
    contract 024 §5). NO device_class (a free-form string state; an
    enum class would demand a closed options list that the open-ended
    unknown-code family violates); NO timing attributes (contract
    024 §6 — the store's first_seen/last_seen date the listening
    window, never the fault; the recorder is the honest timeline).
    """

    _attr_icon = "mdi:alert-circle"  # the classic fault_code icon

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "pushed_fault_code")
        self._attr_name = SENSOR_NAMES["pushed_fault_code"]

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return None
        # The same fresh 2046-block capture the binary reads (one
        # register, one source — the two faces can never disagree).
        return registers.fault_code_state(
            data.registers.get(registers.REG_PUSHED_FAULT)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return {}
        raw = data.registers.get(registers.REG_PUSHED_FAULT)
        attrs: dict[str, Any] = {"register": registers.REG_PUSHED_FAULT}
        if raw is not None:
            attrs["raw"] = raw
            attrs["hex"] = f"0x{raw & 0xFFFF:04X}"
            attrs["set_bits"] = registers.fault_set_bits(raw)
            code = registers.fault_code(raw)
            if code is not None:
                attrs["code"] = code
                label = registers.fault_code_app_label(raw)
                if label is not None:
                    attrs["code_label"] = label
        return attrs