"""DataUpdateCoordinator for Aqua Temp BLE.

Policy (the "don't saturate the PAC" strategy, BLUEPRINT §6):
- exactly one FC03 read of the 45 base registers per poll cycle — never
  per-register reads, no in-cycle retry storms;
- writes are serialized against reads via a single asyncio.Lock, and each
  write is confirmed by one debounced refresh afterwards;
- reconnection happens lazily on the next cycle after a failure (the unit
  accepts a single BLE connection; if the phone app holds it, HA waits).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import const, modbus, registers
from .aqua_client import (
    AquaTempBleClient,
    AquaTempError,
    AquaTempTimeoutError,
    exc_chain,
)

_LOGGER = logging.getLogger(__name__)


def _now() -> float:
    """Monotonic clock seam (the contract 007 §4 pattern — the ONLY
    clock the push-mode freshness gate reads, spec 013 FR-4; tests patch
    this module attribute, never time.monotonic globally)."""
    return time.monotonic()

# name (registers.MODE_VALUE_TO_NAME) -> HVACMode. The int<->HVAC mapping is
# derived from registers.MODE_VALUE_TO_NAME so the calibration surface stays
# the single source of truth.
MODE_NAME_TO_HVAC: dict[str, HVACMode] = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
}

HVAC_TO_MODE_VALUE: dict[HVACMode, int] = {}
for _value, _name in registers.MODE_VALUE_TO_NAME.items():
    if (_hvac := MODE_NAME_TO_HVAC.get(_name)) is not None:
        HVAC_TO_MODE_VALUE[_hvac] = _value
del _value, _name, _hvac

# High-block queries available through the probe tool (start register).
# Spec 021 (tasks.md A3): only the four blocks of the unit's answer
# burst are offered — the app's other recovered query blocks (1091/1136)
# are never in the burst, so querying them is a guaranteed timeout on
# this firmware.
HIGH_BLOCKS: dict[str, int] = {
    "1001": 1001,
    "1046": 1046,
    "2001": 2001,
    "2046": 2046,
}

# Log each unknown Mode value only once per HA session.
_logged_unknown_modes: set[int | None] = set()

# Spec 018 (contract 018-A §3): the once-per-session ledger for
# unmapped NONZERO pushed mode values — a research/calibration
# signal, never a display gate (the display follows power on this
# heat-only unit). 0 and None never enter it: 0 is the observed,
# expected value after our power-only writes (research 018 §1).
_logged_unmapped_push_modes: set[int] = set()

# Log one implausible-raw warning per register address per HA session (FR-2,
# decode-contract.md §7). Sentinels are NOT logged — they are normal "sensor
# absent" states, not errors; they stay visible as raw values in the probe
# dumps, which is exactly where they are useful.
_logged_implausible: set[int] = set()

# Spec 013 (contract 013 §4.2): a base-plane capture while in push mode
# is an anomaly — logged ONCE per HA session, otherwise ignored. The
# module-set pattern of _logged_implausible (mutable without a global).
_logged_push_mode_base: set[str] = set()

# Spec 014 (contract 014 §5.1): the revival WARNING — the trigger read
# ANSWERED (never observed; the post-pairing/E5 world) — logged ONCE
# per HA session so the future spec that re-evaluates the mode has its
# evidence. The module-set pattern of _logged_implausible.
_logged_trigger_answered: set[str] = set()


def decode_temp_logged(address: int, raw: int | None) -> float | None:
    """registers.decode_temp + the once-per-address implausible warning.

    Shared by the coordinator snapshot and the sensor platform (the two
    decode consumers) so that whichever decodes an implausible raw first
    logs exactly one WARNING per address per HA session (FR-2).
    """
    value = registers.decode_temp(raw)
    if (
        value is None
        and raw is not None
        and not registers.is_sentinel(raw)
        and address not in _logged_implausible
    ):
        _logged_implausible.add(address)
        _LOGGER.warning(
            "register %d reports implausible raw %d — treated as absent",
            address,
            raw,
        )
    return value


def _scaled(regs: dict[int, int], address: int) -> float | None:
    """Register raw -> scaled value; None when absent, sentinel or implausible."""
    return decode_temp_logged(address, regs.get(address))


@dataclass
class AquaTempData:
    """Decoded snapshot of the 45-register base block."""

    registers: dict[int, int]        # address -> raw signed int16
    power: bool | None               # reg 8 != 0
    boost: bool | None               # reg 11 != 0 (future use)
    mode_value: int | None           # reg 13
    hvac_mode: HVACMode | None       # derived via MODE_VALUE_TO_NAME
    target_temp: float | None        # reg 14 / 10
    min_target: float | None         # min(reg22, reg24) / 10
    max_target: float | None         # max(reg23, reg25) / 10
    inlet_temp: float | None         # reg 26 / 10, None if sentinel
    outlet_temp: float | None        # reg 27 / 10
    ambient_temp: float | None        # reg 28 / 10
    fault_code: int | None           # reg 47
    firmware: str | None             # regs 48..51 joined
    # --- spec 013 pushed-plane fields (appended WITH defaults; the
    # classic from_regs call site above is untouched) ---
    mode: str = "classic"             # "classic" | "push" (spec 013 FR-3)
    push_confirmed: bool = False      # the calibration gate (FR-5, contract 013 §4.3)
    push_counts: dict[int, int] = field(default_factory=dict)  # per-start push counts (FR-6)
    push_first_seen: float | None = None  # the T-block's first push ts (FR-6)
    push_last_seen: float | None = None   # the T-block's latest push ts (freshness gate)
    # Spec 015 FR-10: the control-candidate DIAGNOSTIC raws (address ->
    # raw, registers.PUSH_CONTROL_CANDIDATES) — gate-independent (the
    # owner re-runs the diff without a terminal); never an entity state.
    push_control_raws: dict[int, int | None] = field(default_factory=dict)

    @classmethod
    def from_regs(cls, regs: dict[int, int]) -> AquaTempData:
        """Decode a raw {register: signed int16} snapshot."""

        def flag(address: int) -> bool | None:
            raw = regs.get(address)
            return None if raw is None else raw != 0

        power = flag(registers.REG_POWER)
        boost = flag(registers.REG_BOOST)
        mode_value = regs.get(registers.REG_MODE)

        hvac_mode: HVACMode | None = None
        if power is False:
            hvac_mode = HVACMode.OFF
        elif power is True:
            name = (
                registers.MODE_VALUE_TO_NAME.get(mode_value)
                if mode_value is not None
                else None
            )
            hvac_mode = MODE_NAME_TO_HVAC.get(name) if name is not None else None
            if hvac_mode is None and mode_value not in _logged_unknown_modes:
                _logged_unknown_modes.add(mode_value)
                _LOGGER.warning(
                    "Unknown Mode register value %r (reg %d) — update "
                    "registers.MODE_VALUE_TO_NAME after calibration "
                    "(docs/CALIBRATION.md §4.4 step M)",
                    mode_value,
                    registers.REG_MODE,
                )

        lows = [
            v
            for v in (
                _scaled(regs, registers.REG_COOL_MIN_TARGET),
                _scaled(regs, registers.REG_HEAT_MIN_TARGET),
            )
            if v is not None
        ]
        highs = [
            v
            for v in (
                _scaled(regs, registers.REG_COOL_MAX_TARGET),
                _scaled(regs, registers.REG_HEAT_MAX_TARGET),
            )
            if v is not None
        ]

        fw_raw = [regs.get(a) for a in range(registers.REG_FIRMWARE_FIRST,
                                             registers.REG_FIRMWARE_LAST + 1)]
        firmware: str | None = None
        if all(r is not None and not registers.is_sentinel(r) for r in fw_raw):
            firmware = ".".join(str(r) for r in fw_raw)  # [V] semantics

        return cls(
            registers=regs,
            power=power,
            boost=boost,
            mode_value=mode_value,
            hvac_mode=hvac_mode,
            target_temp=_scaled(regs, registers.REG_SET_CURRENT_TEMP),
            min_target=min(lows) if lows else None,
            max_target=max(highs) if highs else None,
            inlet_temp=_scaled(regs, registers.REG_INLET_WATER_TEMP),
            outlet_temp=_scaled(regs, registers.REG_OUTLET_WATER_TEMP),
            ambient_temp=_scaled(regs, registers.REG_AMBIENT_TEMP),
            fault_code=regs.get(registers.REG_FAULT1),
            firmware=firmware,
        )

    @classmethod
    def from_pushes(
        cls, captures: dict[int, Any], confirmed: bool, control_confirmed: bool = False
    ) -> AquaTempData:
        """Build the snapshot from the pushed-block store (spec 013 FR-4,
        contract 013 §4; spec 015 FR-7, contract 015-A §5).

        The T-family block (start registers.PUSH_TEMP_BLOCK_START) feeds
        the temperature entities — and ONLY when the temperature gate is
        open (contract 013 §4.3: unconfirmed raws never enter the entity
        plane). The control block (start registers.PUSH_CONTROL_BLOCK_
        START) feeds the control keys — power/mode_value/hvac_mode/
        target_temp — ONLY when the CONTROL gate (spec 015 FR-6) is open;
        the pinned homes live in registers.PUSHED_CONTROL (the owner's
        2026-10-12 diff-revert evidence [P]): power decodes as a flag
        (raw != 0), mode_value as the raw enum, and hvac_mode follows
        POWER alone (contract 018-A §2, amending contract 015-A §5:
        power False renders OFF, power True renders HEAT — this unit
        is heat-only [P panel], so power on IS heating; the mode mirror
        2019 is a diagnostic that NEVER gates the display, and an
        unmapped NONZERO value logs ONE INFO per session per value),
        and the setpoint decodes only when a home is ever pinned
        (None today — no target is displayed, FR-5). Control-unconfirmed
        => every control key is None — the exact 0.10.0 behaviour
        (FR-11c). The FR-10 diagnostics raws (push_control_raws) ride
        the snapshot GATE-INDEPENDENTLY: they are diagnostics with
        evidence tags, never entity states. Fault/firmware have no
        pushed home [P] — None by
        design (contract 013 §4.4, optimistic state forbidden).
        """
        t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
        regs: dict[int, int] = {}
        if t_cap is not None and confirmed:
            regs = dict(t_cap.registers)
        counts = {int(start): cap.count for start, cap in captures.items()}
        # FR-10: the control-candidate raws — gate-independent diagnostics.
        control_raws: dict[int, int | None] = {}
        for addr in registers.PUSH_CONTROL_CANDIDATES:
            block = registers.pushed_block_start(addr)
            cap = captures.get(block) if block is not None else None
            control_raws[addr] = cap.registers.get(addr) if cap is not None else None
        # FR-7 (contract 015-A §5, amended by contract 018-A §2):
        # the confirmed control decode.
        power: bool | None = None
        mode_value: int | None = None
        hvac_mode: HVACMode | None = None
        target_temp: float | None = None
        if control_confirmed:
            c_cap = captures.get(registers.PUSH_CONTROL_BLOCK_START)
            if c_cap is not None:
                cregs = c_cap.registers
                p_addr = registers.PUSHED_CONTROL.get("power")
                raw = cregs.get(p_addr) if p_addr is not None else None
                power = registers.decode_flag(raw, active_when_nonzero=True)
                m_addr = registers.PUSHED_CONTROL.get("mode")
                mode_value = cregs.get(m_addr) if m_addr is not None else None
                if power is False:
                    hvac_mode = HVACMode.OFF
                elif power is True:
                    # Spec 018 (contract 018-A §2): the display follows
                    # POWER on this heat-only unit (H06 [P panel]) — a
                    # mode-gated display renders the machine's real
                    # running state `unknown`, because our proven power
                    # write (1011) updates the power mirror 2011 and
                    # never the mode mirror 2019 (research 018 §3, M-1).
                    hvac_mode = HVACMode.HEAT
                    if (
                        mode_value is not None
                        and mode_value != 0
                        and mode_value not in registers.PUSHED_MODE_VALUE_TO_NAME
                        and mode_value not in _logged_unmapped_push_modes
                    ):
                        _logged_unmapped_push_modes.add(mode_value)
                        _LOGGER.info(
                            "pushed mode value %r (reg %s) unmapped — display "
                            "follows power (heat-only, spec 018); update "
                            "registers.PUSHED_MODE_VALUE_TO_NAME for the "
                            "research map (quickstart 018 §2)",
                            mode_value,
                            m_addr,
                        )
            # Spec 019 FR-1 (AC-4): the setpoint decodes from the capture
            # of the block that CONTAINS the home — pushed_block_start
            # (the 1046-block), NOT the 2001 window (research 019 §1:
            # the home lives in the 1046 ladder; the 015 code read every
            # control key from the 2001 capture, which never carries it).
            # The gate semantics are UNCHANGED: the home rides the
            # EXISTING push_control_confirmed gate (the mapping is
            # confirmed); a store with only the 2001 block decodes NO
            # target (the block-source fix — the honesty rule, not a
            # new gate).
            s_addr = registers.PUSHED_CONTROL.get("setpoint")
            if s_addr is not None:
                s_block = registers.pushed_block_start(s_addr)
                s_cap = captures.get(s_block) if s_block is not None else None
                target_temp = decode_temp_logged(
                    s_addr,
                    s_cap.registers.get(s_addr) if s_cap is not None else None,
                )
        return cls(
            registers=regs,
            power=power,
            boost=None,
            mode_value=mode_value,
            hvac_mode=hvac_mode,
            target_temp=target_temp,
            min_target=None,
            max_target=None,
            inlet_temp=None,
            outlet_temp=None,
            ambient_temp=None,
            fault_code=None,
            firmware=None,
            mode="push",
            push_confirmed=confirmed,
            push_counts=counts,
            push_first_seen=t_cap.first_seen if t_cap is not None else None,
            push_last_seen=t_cap.last_seen if t_cap is not None else None,
            push_control_raws=control_raws,
        )

    @classmethod
    def from_merged(
        cls, regs: dict[int, int], captures: dict[int, Any], confirmed: bool
    ) -> AquaTempData:
        """Base-plane answer + pushed overlay (spec 014 FR-2, contract
        014 §5.2 — used ONLY when the trigger read completes: the
        revival world, never observed yet).

        Typed fields come from the base answer (`from_regs` semantics —
        contract 013 §4.2: the base plane wins for shared quantities);
        the `registers` dict is the base regs merged with the CONFIRMED
        T-block raws — the windows are disjoint by construction (7-51 vs
        2046-2090 [P], data-model 014 §4), so the union is
        collision-free. The honesty gate applies to pushed raws ONLY
        (contract 013 §4.3 unchanged — base values are base-pinned
        registers, not unconfirmed pushes); mode stays "push" (the
        re-evaluation is a future spec's decision, contract 014 §5.3).
        """
        base = cls.from_regs(regs)
        t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
        pushed_regs = (
            dict(t_cap.registers) if (t_cap is not None and confirmed) else {}
        )
        counts = {int(start): cap.count for start, cap in captures.items()}
        return cls(
            registers={**regs, **pushed_regs},
            power=base.power,
            boost=base.boost,
            mode_value=base.mode_value,
            hvac_mode=base.hvac_mode,
            target_temp=base.target_temp,
            min_target=base.min_target,
            max_target=base.max_target,
            inlet_temp=base.inlet_temp,
            outlet_temp=base.outlet_temp,
            ambient_temp=base.ambient_temp,
            fault_code=base.fault_code,
            firmware=base.firmware,
            mode="push",
            push_confirmed=confirmed,
            push_counts=counts,
            push_first_seen=t_cap.first_seen if t_cap is not None else None,
            push_last_seen=t_cap.last_seen if t_cap is not None else None,
        )


class AquaTempCoordinator(DataUpdateCoordinator[AquaTempData]):
    """One FC03 read per cycle. Writes are serialized against reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Any,
        client: AquaTempBleClient,
    ) -> None:
        self.client = client
        self._io_lock = asyncio.Lock()
        # Spec 017 FR-3 step 9: the last control write's stage dict —
        # the climate diagnostics' cheap support surface (read by
        # extra_state_attributes; never an entity state — the tile's
        # truth stays the pushed mirror alone).
        self._last_control_write: dict[str, Any] | None = None
        # Spec 019 FR-10 step 6: the last setpoint write's stage dict —
        # the climate's diagnostics attribute beside last_power_write
        # (never an entity state — the tile's truth stays the pushed
        # mirror alone, contract 019 §5.2).
        self._last_setpoint_write: dict[str, Any] | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=const.DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get("poll_interval", const.DEFAULT_POLL_INTERVAL)
            ),
            config_entry=entry,
            always_update=False,
        )

    # ------------------------------------------------------------- options
    @property
    def temperature_unit(self) -> str:
        """Display unit from options. The wire is always x10 Celsius. [P]"""
        unit = self.config_entry.options.get(
            "temperature_unit", const.DEFAULT_TEMPERATURE_UNIT
        )
        if str(unit).upper() == "F":
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    # -------------------------------------------------------------- polling
    async def _async_update_data(self) -> AquaTempData:
        """Exactly one 45-register FC03 read per cycle; no retry storm.

        Spec 014 FR-1: entries with mode == "push" run the query cycle —
        ONE vendor read per cycle (the TRIGGER the unit answers with its
        block burst, contract 014 §1), then the pushed-block store. The
        0.9.0 zero-TX cycle starved: the unit pushes nothing
        spontaneously and answers every request with its block burst
        (research 014 §0.2) — the store fills during the trigger window."""
        if str(self.config_entry.data.get("mode", "classic")) == "push":
            return await self._async_update_push()
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()
            try:
                regs = await self.client.read_registers(
                    registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
                )
            except AquaTempError as err:
                await self.client.disconnect()  # clean slate for next cycle
                raise UpdateFailed(f"read failed: {err}") from err
            return AquaTempData.from_regs(regs)

    # ------------------------------------------------ spec 013 push mode
    def push_freshness_window(self) -> float:
        """max(2 x poll interval, 60 s) — the freshness rule of contract
        013 §6.1: a store entry is used only while its last-seen age
        stays inside this window (the long-run push cadence is unproven,
        U-3 — the rule guards against a stale stream shown as current)."""
        interval = self.config_entry.options.get(
            "poll_interval", const.DEFAULT_POLL_INTERVAL
        )
        return max(2.0 * float(interval), 60.0)

    @property
    def push_temp_confirmed(self) -> bool:
        """The calibration gate (spec 013 FR-5, contract 013 §4.3): the
        per-entry `push_temp_confirmed` option OR the global registers
        switch (the one-line calibration fix, the WRITE_ECHO_EXPECTED
        precedent). False until the side-by-side run matched the app's
        T-family display (quickstart 013 §2) — no unconfirmed register
        is ever displayed under a certain name."""
        return bool(
            registers.PUSH_TEMP_CONFIRMED
            or self.config_entry.options.get("push_temp_confirmed", False)
        )

    @property
    def push_control_confirmed(self) -> bool:
        """The CONTROL confirmation gate (spec 015 FR-6, contract 015-A
        §4): the per-entry `push_control_confirmed` option OR the global
        registers switch (the push_temp_confirmed pattern). False until
        the owner re-verifies the pinned control homes (power 2011 /
        mode 2019, the 2026-10-12 diff-revert evidence) against the
        app's display (quickstart 015 §5) — unconfirmed homes never
        reach the entity plane: the control keys decode None and the
        climate is not created."""
        return bool(
            registers.PUSH_CONTROL_CONFIRMED
            or self.config_entry.options.get("push_control_confirmed", False)
        )

    @property
    def write_control_allowed(self) -> bool:
        """The write-control ACTIVATION gate (spec 017 FR-1, contract
        017 §2): the global registers switch (WRITE_CONTROL_PROVEN,
        016 T3 — the calibration-switch precedent; restart) OR the
        per-entry `write_control` option (the push_control_confirmed
        pattern — no restart; the climate reads this property live,
        so saving the option re-renders the entity without a reload).
        Never set by any code path: the owner's act alone opens it
        (the option's description names the required probe verdict);
        the shipped default is False on both surfaces — the
        integration stays byte-identical to 0.12.0 until then (AC-10)."""
        return bool(
            registers.WRITE_CONTROL_PROVEN
            or self.config_entry.options.get("write_control", False)
        )

    @property
    def last_control_write(self) -> dict[str, Any] | None:
        """Spec 017 FR-3 step 9: the last control write's stage dict
        (value, verdict, mirror raw + age relative to the write) — the
        climate's diagnostics attribute (FR-5); the entity state itself
        never reads it (never optimistic, FR-6)."""
        return self._last_control_write

    @property
    def write_setpoint_allowed(self) -> bool:
        """The setpoint-write ACTIVATION gate (spec 019 FR-9, contract
        019 §4): the THIRD gate, SEPARATE from write_control_allowed
        (the power proof authorizes 1011 only — the non-transference
        rule, contracts 016 §4/019 §4.3). The global registers switch
        (WRITE_SETPOINT_PROVEN — the calibration-switch precedent;
        restart) OR the per-entry `write_setpoint` option (the
        write_control pattern — no restart; the climate reads this
        property live, so saving the option re-renders the entity).
        Never set by any code path: the owner's act alone opens it,
        ONLY after the setpoint write probe's EXECUTED + REVERSIBLE
        verdict (quickstart 019 §1→§3); the shipped default is False
        on both surfaces — the setpoint WRITE surface stays absent
        (the read display still appears behind the existing control
        gate — the home is [P owner], FR-8)."""
        return bool(
            registers.WRITE_SETPOINT_PROVEN
            or self.config_entry.options.get("write_setpoint", False)
        )

    @property
    def last_setpoint_write(self) -> dict[str, Any] | None:
        """Spec 019 FR-10 step 6: the last setpoint write's stage dict
        (value_raw, verdict, mirror raw + age relative to the write) —
        the climate's diagnostics attribute (FR-11); the entity state
        itself never reads it (never optimistic, contract 019 §5.2)."""
        return self._last_setpoint_write

    def now(self) -> float:
        """The coordinator's monotonic clock (the _now seam, contract 007
        §4) — public so the entity platforms share the freshness gate's
        clock (spec 013 FR-6, the push_stream diagnostic)."""
        return _now()

    def _log_push_mode_base_anomaly(self) -> None:
        """Contract 013 §4.2: a base-plane capture in the store while in
        push mode is an ANOMALY — one WARNING per HA session, the data
        otherwise ignored (this spec never reverts the mode; the mode
        re-evaluation is a future spec's decision)."""
        if "base" in _logged_push_mode_base:
            return
        _logged_push_mode_base.add("base")
        _LOGGER.warning(
            "push mode: base-block frame captured in the pushed store "
            "(start %d) — the request path may have revived; treating it "
            "as an anomaly and ignoring it per contract 013 §4.2 (the "
            "mode re-evaluation is a future spec's decision)",
            registers.BASE_BLOCK_START,
        )

    def _log_trigger_answered_once(self) -> None:
        """Contract 014 §5.1: the trigger read ANSWERED — the request
        path revived (never observed; the post-pairing/E5 world). ONE
        WARNING per HA session (the module-set pattern of
        _logged_implausible) — the decisive evidence the future spec
        that re-evaluates the mode reads; the mode stays "push" (§5.3).
        """
        if "trigger" in _logged_trigger_answered:
            return
        _logged_trigger_answered.add("trigger")
        _LOGGER.warning(
            "push mode: trigger read answered — the request path "
            "revived; using base data (mode stays push; a future spec "
            "re-evaluates)"
        )

    async def _async_update_push(self) -> AquaTempData:
        """The push-mode cycle: TRIGGER + LISTEN (spec 014 FR-1,
        contract 014 §1/§2/§3/§4 — amends contract 013 §1's zero-TX
        poll invariant).

        The ladder: connect (the CCCD subscription connect() already
        performs — the only ATT operation besides the trigger, §1.1) ->
        TRIGGER: ONE vendor base read `63030007002d3c54`, the frame the
        unit demonstrably answers with its parameter-dataset block
        burst (research 014 §0.2/§6, six runs [P]); on this unit the
        call raises AquaTempTimeoutError after the 8.0 s window — the
        EXPECTED world, logged at INFO with the census, while the store
        fills on the notify path (contract 014 §2) -> REVIVAL check: an
        ANSWERED trigger is decisive evidence, logged once, snapshot
        from_merged (contract 014 §5) -> LISTEN: the presence-based
        harvest, instant when the burst already landed (contract 014
        §3) -> freshness gate (contract 013 §6.1, unchanged) -> fail
        honestly with UpdateFailed("push stream silent after trigger")
        and recycle the link (contract 014 §4.2/§4.3). One trigger per
        cycle, never re-sent, never re-triggered mid-cycle (§4.1)."""
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()
            # 1. TRIGGER (contract 014 §2): ONE vendor read — the
            #    frame the unit demonstrably answers with its block
            #    burst (research 014 §6). Single shot, no re-sends;
            #    write type = the client's resolved default
            #    (without-response, app parity). On this unit:
            #    AquaTempTimeoutError after 8.0 s — EXPECTED.
            try:
                regs = await self.client.read_registers(
                    registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
                )
            except AquaTempTimeoutError as exc:
                # Contract 014 §2.2: the expected world — never an
                # UpdateFailed by itself; the census (frames/bytes/
                # blocks/first/last) rides verbatim in the exception
                # message (contract 009 §3 format, unchanged).
                # Spec 021 (tasks.md C1): demoted to DEBUG — at the 30 s
                # poll interval this line fired ~2,880 times/day at INFO;
                # the world is proven, the census stays observable in debug
                # and in the WARNING verdicts that matter.
                _LOGGER.debug(
                    "push cycle: trigger read timed out as expected (census: %s)",
                    exc,
                )
                regs = None
            except AquaTempError as err:
                # Contract 014 §2.3: a transport failure during the
                # trigger (link lost, write refused) — an honest read
                # failure, never conflated with stream silence.
                await self.client.disconnect()
                raise UpdateFailed(f"trigger read failed: {err}") from err
            captures = dict(self.client.pushed_blocks)
            # 2. REVIVAL (contract 014 §5): the trigger ANSWERED — the
            #    base plane is back (never observed; the post-pairing/
            #    E5 world). Log once per session, use the base data; a
            #    completed trigger must never fail the cycle for
            #    lacking pushes (the base answer IS the data) — this
            #    branch runs BEFORE the freshness failure.
            if regs is not None:
                self._log_trigger_answered_once()
                return AquaTempData.from_merged(
                    regs, captures, self.push_temp_confirmed
                )
            # 3. LISTEN (contract 014 §3): presence-based harvest on
            #    the already-subscribed pipe — instant when the burst
            #    already landed during the trigger window (the typical
            #    case), otherwise a bounded zero-TX listen (observed:
            #    all four starts within ~16 s, research 013 §5.4 —
            #    wide margin).
            window = self.push_freshness_window()
            t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
            now = _now()
            if t_cap is None or now - t_cap.last_seen > window:
                caps = await self.client.harvest_pushes(
                    {registers.PUSH_TEMP_BLOCK_START},
                    const.PUSH_HARVEST_TIMEOUT,
                )
                if caps:
                    captures.update(caps)
                    t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
                now = _now()
            # 4. FRESHNESS (contract 013 §6.1, unchanged) + the new
            #    verdict (contract 014 §4.2): the proven trigger did
            #    not wake the unit — a strictly stronger statement
            #    than the 0.9.0 listen-only silence. The disconnect
            #    recycles the link for the next cycle's fresh trigger.
            if t_cap is None or now - t_cap.last_seen > window:
                await self.client.disconnect()
                raise UpdateFailed("push stream silent after trigger")
            if registers.BASE_BLOCK_START in captures:
                # Contract 013 §4.2: a base-plane frame in the store
                # while in push mode — anomaly, logged once, ignored.
                self._log_push_mode_base_anomaly()
            return AquaTempData.from_pushes(
                captures, self.push_temp_confirmed, self.push_control_confirmed
            )

    async def async_write_register(self, address: int, value: int) -> None:
        """Serialize a write against reads, then confirm with one refresh."""
        # FR-9 + spec 016 FR-3: the single write funnel guard — before any
        # lock or I/O, so no code path can ever write outside {8, 13, 14}
        # (+19-21 behind the WRITE_MODE_SETPOINT_TOO switch) or, behind
        # WRITE_CONTROL_PROVEN, the control allowlist (1011 with 0/1).
        # The VALUE threads through to the guard (spec 016 T3) so the
        # control plane's value discipline is enforced at the funnel.
        # Installer settings (H/R/P) and the high blocks 1001-2090 are
        # never writable (spec 003 §6).
        registers.ensure_writable(address, value)
        async with self._io_lock:
            await self.client.write_registers(address, [value])
        await self.async_request_refresh()  # debounced confirmation read

    # ---------------------------------------------------------------- probe
    async def async_probe_block(self, block: str = "base") -> dict[str, Any]:
        """Read a register block and log every register (calibration, §4).

        Spec 021 (tasks.md A1/A2): a push-mode entry's "base" probe is a
        TRIGGER + STORE DUMP — this unit never answers the classic FC03
        read with base data (law L1, spec 020: the burst blocks never
        match start=7), so the old direct base read was a guaranteed 8 s
        timeout on the "Probe registers" button. The probe now sends
        ONE vendor read (the burst trigger), harvests the pushed blocks,
        and dumps the whole store with labels — the useful calibration
        view. A classic-mode entry keeps the direct FC03 base read (the
        dormant other-firmware plane, spec 020 §5).
        """
        block = str(block)
        if block != "base" and block not in HIGH_BLOCKS:
            raise ValueError(f"unknown block {block!r}")
        push_mode = str(self.config_entry.data.get("mode", "classic")) == "push"
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()
            if block == "base":
                if push_mode:
                    return await self._probe_push_store()
                try:
                    regs = await self.client.read_registers(
                        registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
                    )
                except AquaTempError:
                    await self.client.disconnect()
                    raise
                return self._log_base_probe(regs)
            start = HIGH_BLOCKS[block]
            try:
                raw = await self.client.query_block(start, registers.BASE_BLOCK_COUNT)
            except AquaTempError:
                await self.client.disconnect()
                raise
            return self._log_raw_probe(block, start, raw)

    async def _probe_push_store(self) -> dict[str, Any]:
        """The push-mode "base" probe: trigger + dump the store (spec 021,
        tasks.md A1 — replaces the guaranteed-timeout direct base read).

        ONE vendor base read (the trigger the unit answers with its
        block burst — its 8 s timeout is the expected world, spec 014
        M-4), then a bounded harvest of the four burst blocks, then the
        per-register dump of every captured block with labels (the same
        line format the classic probe uses). A trigger that is ANSWERED
        (the never-observed revival) logs the decisive line once per
        session and its base data is dumped too (the poll cycle's
        evidence discipline).
        """
        address = self.config_entry.data["address"]
        regs: dict[int, int] | None = None
        try:
            regs = await self.client.read_registers(
                registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
            )
        except AquaTempTimeoutError:
            _LOGGER.debug(
                "probe [%s]: trigger timed out as expected (the answer is "
                "the block burst, spec 014 M-4)",
                address,
            )
        except AquaTempError:
            await self.client.disconnect()
            raise
        result: dict[str, Any] = {}
        if regs is not None:
            # The revival world (never observed): log the decisive line
            # once per session, exactly like the poll cycle, and dump the
            # base answer alongside the store.
            self._log_trigger_answered_once()
            result.update(self._log_base_probe(regs))
        # The store dump: harvest the four burst blocks (instant when the
        # burst already landed during the trigger window — the typical
        # case).
        await self.client.harvest_pushes(
            const.PUSH_HARVEST_STARTS, const.PUSH_HARVEST_TIMEOUT
        )
        captures = dict(self.client.pushed_blocks)
        if not captures:
            _LOGGER.warning(
                "AquaTemp probe: %s store empty after trigger — no block "
                "burst within the window (unit unreachable? check the "
                "vendor app is closed)",
                address,
            )
            result[address] = {"block": "store", "crc": "NONE", "registers": {}}
            return result
        result.update(self._log_store_dump(address, captures))
        return result

    def _log_store_dump(
        self, address: str, captures: dict[int, Any]
    ) -> dict[str, Any]:
        """Dump every pushed block in the per-register probe format
        (spec 021): one header line per block, one line per register
        with its label — the calibration view that replaces the base
        block this unit never serves."""
        out: dict[str, Any] = {"block": "store", "crc": "OK", "blocks": {}}
        for start in sorted(captures):
            cap = captures[start]
            _LOGGER.info(
                "AquaTemp probe: %s block=%d (pushed capture, start=%d qty=%d "
                "len=%d count=%d) CRC=OK",
                address,
                start,
                cap.start,
                cap.qty,
                cap.length,
                cap.count,
            )
            registers_out: dict[str, Any] = {}
            for addr in sorted(cap.registers):
                raw = cap.registers[addr]
                line = (
                    f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) "
                    f"| /10 = {raw / 10:7.1f}"
                )
                label = registers.KNOWN_LABELS.get(addr)
                if label:
                    line += f"  # {label}"
                _LOGGER.info("  %s", line)
                entry: dict[str, Any] = {
                    "raw": raw,
                    "hex": f"0x{raw & 0xFFFF:04X}",
                    "tenths": round(raw / 10, 1),
                }
                if label:
                    entry["label"] = label
                registers_out[str(addr)] = entry
            out["blocks"][str(start)] = registers_out
        return {address: out}

    def _log_base_probe(self, regs: dict[int, int]) -> dict[str, Any]:
        """Log the base block in the exact probe format (§4.3)."""
        address = self.config_entry.data["address"]
        length = 2 * registers.BASE_BLOCK_COUNT + 5
        _LOGGER.info(
            "AquaTemp probe: %s block=base (FC03 start=%d count=%d) len=%d CRC=OK",
            address,
            registers.BASE_BLOCK_START,
            registers.BASE_BLOCK_COUNT,
            length,
        )
        registers_out: dict[str, Any] = {}
        for offset in range(registers.BASE_BLOCK_COUNT):
            addr = registers.BASE_BLOCK_START + offset
            raw = regs.get(addr, 0)
            line = (
                f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) "
                f"| /10 = {raw / 10:7.1f}"
            )
            label = registers.KNOWN_LABELS.get(addr)
            if label:
                line += f"  # {label}"
            _LOGGER.info("  %s", line)
            entry: dict[str, Any] = {
                "raw": raw,
                "hex": f"0x{raw & 0xFFFF:04X}",
                "tenths": round(raw / 10, 1),
            }
            if label:
                entry["label"] = label
            registers_out[str(addr)] = entry
        return {address: {"block": "base", "crc": "OK", "registers": registers_out}}

    def _log_raw_probe(
        self, block: str, start: int, raw: bytes
    ) -> dict[str, Any]:
        """Log a high-block response (spec 008 FR-5: decode block frames).

        The unit's answer to a query block is the block frame proven in
        research 008 §2.1 — decode it into the per-register probe format
        (shared with _log_base_probe); a ModbusError falls back to the raw
        hex dump (plan 008 §3), so the bytes always stay visible. The
        result dict gains start/qty so the probe output is self-describing.
        """
        address = self.config_entry.data["address"]
        crc = "OK" if len(raw) >= 4 and modbus.validate_crc(raw) else "FAIL"
        _LOGGER.info(
            "AquaTemp probe: %s block=%s (query start=%d count=%d) len=%d CRC=%s",
            address,
            block,
            start,
            registers.BASE_BLOCK_COUNT,
            len(raw),
            crc,
        )
        response: dict[str, Any] = {
            "block": block,
            "crc": crc,
            "start": start,
            "qty": registers.BASE_BLOCK_COUNT,
        }
        try:
            regs = modbus.parse_block_response(
                bytes(raw),
                start,
                registers.BASE_BLOCK_COUNT,
                little_endian=registers.REG_DATA_LITTLE_ENDIAN,
            )
        except modbus.ModbusError as exc:
            _LOGGER.warning("could not parse %s response as a block frame: %s", block, exc)
            regs = {}
        if regs:
            out: dict[str, Any] = {}
            for addr, value in regs.items():
                line = (
                    f"reg {addr:3d} = {value:6d} (0x{value & 0xFFFF:04X}) "
                    f"| /10 = {value / 10:7.1f}"
                )
                label = registers.KNOWN_LABELS.get(addr)
                if label:
                    line += f"  # {label}"
                _LOGGER.info("  %s", line)
                item: dict[str, Any] = {
                    "raw": value,
                    "hex": f"0x{value & 0xFFFF:04X}",
                    "tenths": round(value / 10, 1),
                }
                if label:
                    item["label"] = label
                out[str(addr)] = item
            response["registers"] = out
        else:
            _LOGGER.info("  raw: %s", raw.hex())
            response["raw"] = raw.hex()
        return {address: response}

    # ------------------------------------------------ spec 016 power probe
    async def async_power_write_probe(self) -> dict[str, Any]:
        """The power-write probe — spec 016 FR-4's diff-revert proof
        instrument (contract 016 §3): baseline -> write the pinned ON
        frame -> the pushed mirror 2011 must follow to 1 -> write the
        pinned OFF frame -> the mirror must revert to 0 -> ONE verdict
        line at WARNING (the operator's log page keeps WARNING+,
        contract 012 §2).

        A MEASUREMENT INSTRUMENT, not a control surface (FR-6): it
        writes ONLY the two disassembly-pinned frames
        (registers.WRITE_FRAME_POWER_*), ONLY after the mandatory
        baseline gate confirms the unit is off (it never toggles an
        unverified state), and it always aims to leave the unit OFF —
        a failed or unconfirmed revert is never silent: the verdict
        line carries the remediation ("turn the unit OFF from the
        vendor app or the panel", contract 016 §7). The climate stays
        read-only regardless (FR-7): this method is the only code that
        may write 1011 before the live proof opens
        WRITE_CONTROL_PROVEN (contract 016 §4 — the funnel guard is
        NOT used by the probe; the frames go through the client's own
        pinned seam, contract 016 §5).

        Single-flight (the _io_lock, FR-4 step 9); disconnects on
        completion or failure (step 8, the push-mode convention).
        Non-timeout transport failures (connect, trigger) propagate
        to the caller — the service reports them; every stage
        observation and the verdict log at WARNING (the evidence
        discipline).

        Returns the stage dict (the mirror observations with raws +
        times, the frames sent, the resolved write UUID — FR-6).
        """
        address = self.config_entry.data["address"]
        write_uuid = getattr(self.client, "write_uuid", None) or const.UUID_WRITE
        mirror_addr = registers.READ_MIRROR_POWER
        result: dict[str, Any] = {
            "verdict": "no-report",
            "stage": "baseline",
            "write_uuid": write_uuid,
            "frames_sent": [],
            "mirror": {},
        }
        t_probe = _now()

        def _rel(ts: float) -> float:
            return round(ts - t_probe, 1)

        def _finish(verdict: str, detail: str) -> dict[str, Any]:
            """FR-4 step 6: set the verdict, log ONE line at WARNING —
            the resolved write UUID rides in it (FR-6) — and return
            the stage dict."""
            result["verdict"] = verdict
            line = f"power write probe: {detail} — write char {write_uuid}"
            result["verdict_line"] = line
            _LOGGER.warning("%s", line)
            return result

        async with self._io_lock:
            try:
                if not self.client.is_connected:
                    await self.client.connect()
                # -- step 1: the baseline — trigger + fresh harvest -----
                t_mark = _now()
                await self._trigger_report("power write probe")
                cap = await self._probe_fresh_control(t_mark)
                if cap is None:
                    return _finish("no-report", "no-report (no fresh 2001 block)")
                mirror0 = cap.registers.get(mirror_addr)
                if mirror0 is None:
                    return _finish("no-report", "no-report (no fresh 2001 block)")
                result["mirror"]["baseline"] = {
                    "raw": mirror0,
                    "t": _rel(cap.last_seen),
                }
                _LOGGER.warning(
                    "power write probe [%s]: baseline: mirror %d = %d (t+%.1fs)",
                    address,
                    mirror_addr,
                    mirror0,
                    result["mirror"]["baseline"]["t"],
                )
                # -- step 2: the mandatory baseline gate (FR-6) ---------
                if mirror0 != 0:
                    return _finish(
                        "refused-baseline-not-off",
                        f"refused-baseline-not-off (mirror {mirror_addr} = "
                        f"{mirror0}) — nothing was written",
                    )
                # -- step 3: the ON write (without-response parity) ---
                result["stage"] = "on-write"
                t_on = _now()
                _LOGGER.warning(
                    "power write probe [%s]: sending power-ON frame %s on %s",
                    address,
                    registers.WRITE_FRAME_POWER_ON,
                    write_uuid,
                )
                try:
                    await self.client.write_probe_frame(
                        bytes.fromhex(registers.WRITE_FRAME_POWER_ON)
                    )
                except Exception as exc:  # noqa: BLE001 — the chain IS the datum
                    return _finish("att-error", f"att-error: {exc_chain(exc)}")
                result["frames_sent"].append(registers.WRITE_FRAME_POWER_ON)
                # -- step 4: harvest after ON (the robust order) --------
                result["stage"] = "on-harvest"
                cap = await self._probe_fresh_control(t_on, allow_retry=True)
                if cap is None:
                    return _finish(
                        "no-report",
                        "no-report (no fresh 2001 block after ON) — the "
                        "unit's state is UNCONFIRMED: check the panel; if it "
                        "is on, turn the unit OFF from the vendor app or "
                        "the panel",
                    )
                mirror1 = cap.registers.get(mirror_addr)
                if mirror1 is None:
                    return _finish(
                        "no-report", "no-report (no fresh 2001 block after ON)"
                    )
                result["mirror"]["after_on"] = {
                    "raw": mirror1,
                    "t": _rel(cap.last_seen),
                }
                _LOGGER.warning(
                    "power write probe [%s]: after ON: mirror %d = %d (t+%.1fs)",
                    address,
                    mirror_addr,
                    mirror1,
                    result["mirror"]["after_on"]["t"],
                )
                if mirror1 != 1:
                    return _finish(
                        "not-executed",
                        f"not-executed (mirror {mirror_addr} stayed {mirror1} "
                        "after ON, expected 1)",
                    )
                # -- step 5: the OFF write + the revert check ----------
                result["stage"] = "off-write"
                t_off = _now()
                _LOGGER.warning(
                    "power write probe [%s]: sending power-OFF frame %s on %s",
                    address,
                    registers.WRITE_FRAME_POWER_OFF,
                    write_uuid,
                )
                try:
                    await self.client.write_probe_frame(
                        bytes.fromhex(registers.WRITE_FRAME_POWER_OFF)
                    )
                except Exception as exc:  # noqa: BLE001 — the chain IS the datum
                    return _finish(
                        "att-error",
                        f"att-error: {exc_chain(exc)} — the unit may still "
                        "be ON: turn the unit OFF from the vendor app or "
                        "the panel",
                    )
                result["frames_sent"].append(registers.WRITE_FRAME_POWER_OFF)
                result["stage"] = "off-harvest"
                cap = await self._probe_fresh_control(t_off, allow_retry=True)
                if cap is None:
                    return _finish(
                        "no-report",
                        "no-report (no fresh 2001 block after OFF) — the "
                        "unit's final state is UNCONFIRMED: check the "
                        "panel; if it is on, turn the unit OFF from the "
                        "vendor app or the panel",
                    )
                mirror2 = cap.registers.get(mirror_addr)
                if mirror2 is None:
                    return _finish(
                        "no-report", "no-report (no fresh 2001 block after OFF)"
                    )
                result["mirror"]["after_off"] = {
                    "raw": mirror2,
                    "t": _rel(cap.last_seen),
                }
                _LOGGER.warning(
                    "power write probe [%s]: after OFF: mirror %d = %d (t+%.1fs)",
                    address,
                    mirror_addr,
                    mirror2,
                    result["mirror"]["after_off"]["t"],
                )
                if mirror2 != 0:
                    return _finish(
                        "not-reversible",
                        f"not-reversible (mirror {mirror_addr} stayed "
                        f"{mirror2} after OFF, expected 0) — the unit is "
                        "still ON: turn the unit OFF from the vendor app "
                        "or the panel",
                    )
                # -- step 6: the verdict -------------------------------
                result["stage"] = "done"
                return _finish(
                    "executed+reversible",
                    f"EXECUTED + REVERSIBLE (mirror {mirror_addr} followed "
                    "0→1→0) — evidence for phase 2 (WRITE_CONTROL_PROVEN)",
                )
            finally:
                # step 8: disconnect on completion or failure (the
                # push-mode convention) — even when an AquaTempError
                # propagates to the caller.
                try:
                    await self.client.disconnect()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("probe disconnect failed", exc_info=True)

    async def _trigger_report(self, context: str) -> None:
        """ONE vendor base read — the proven trigger (spec 014 §6, the
        probe's FR-4 step 1): the frame the unit answers with its
        block burst. AquaTempTimeoutError is the EXPECTED world (the
        answer IS the burst — spec 014 M-4); any other AquaTempError
        is an honest transport failure: logged at WARNING (the stage
        evidence), re-raised for the caller (the service/button) to
        report.

        Spec 017 T3: the pure parameterization — ONE trigger
        implementation, two callers. The probe passes "power write
        probe" (its log lines keep their exact 016 strings so the
        016 suite passes unchanged); the confirmation cycle passes
        "power write confirm" (FR-3 step 4b — C-3's rule: the write
        does not wake the report, the trigger does)."""
        address = self.config_entry.data["address"]
        try:
            await self.client.read_registers(
                registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
            )
        except AquaTempTimeoutError:
            _LOGGER.debug(
                "%s [%s]: trigger timed out as expected "
                "(the answer is the block burst, spec 014 M-4)",
                context,
                address,
            )
        except AquaTempError as err:
            _LOGGER.warning(
                "%s [%s]: trigger read failed: %s", context, address, err
            )
            raise

    async def _probe_fresh_control(
        self, before: float, *, allow_retry: bool = False
    ) -> Any:
        """The fresh-mirror harvest (FR-4 step 4): wait for a 2001-block
        push FRESHER than `before` — the oracle must postdate the
        action it verifies (contract 016 §3's diff), so
        harvest_pushes's PRESENCE semantics (spec 013) are not enough:
        a capture from before the write would satisfy it instantly.
        The wait polls the same store view (client.pushed_blocks) on
        the harvest cadence (0.05 s).

        The robust order: with `allow_retry`, when the first window
        stays empty ONE base-read trigger goes out (the write itself
        may not have woken the report — research 016 §4 Q4) and one
        more wait follows."""
        cap = await self._wait_fresh_block(
            registers.PUSH_CONTROL_BLOCK_START, before
        )
        if cap is None and allow_retry:
            _LOGGER.warning(
                "power write probe [%s]: no fresh block after the write "
                "— sending ONE base-read trigger (FR-4 step 4)",
                self.config_entry.data["address"],
            )
            await self._trigger_report("power write probe")
            cap = await self._wait_fresh_block(
                registers.PUSH_CONTROL_BLOCK_START, before
            )
        return cap

    async def _wait_fresh_block(self, start: int, before: float) -> Any:
        """Poll the pushed-block store for a capture of the block
        `start` fresher than `before` (monotonic ts), bounded by
        const.PROBE_MIRROR_TIMEOUT. Returns the capture, or None when
        the window stayed empty.

        Spec 019 FR-12 (T5): the 016/017 `_wait_fresh_control`
        generalized to any pushed block — a PURE parameterization; the
        power paths pass PUSH_CONTROL_BLOCK_START, the setpoint paths
        pass the 1046 block (the write target and the mirror COINCIDE
        — contract 019 §1: the fresh block IS the oracle). The 016/017
        suites' log lines are unchanged (the refactor touches no
        strings)."""
        timeout = const.PROBE_MIRROR_TIMEOUT
        deadline = _now() + timeout
        while True:
            cap = self.client.pushed_blocks.get(start)
            if cap is not None and cap.last_seen > before:
                return cap
            if _now() >= deadline:
                return None
            await asyncio.sleep(0.05)

    # ---------------------------------------- spec 017 control writes
    async def async_control_write_power(self, value: int) -> dict[str, Any]:
        """The controlled climate's ONLY write path (spec 017 FR-3,
        contract 017 §3/§5): guard -> connect -> audit -> under the
        I/O lock: the fire-and-forget write -> ONE trigger -> the
        fresh-mirror wait -> the cross-check verdicts -> the mirror
        snapshot push.

        Never optimistic (contract 016 §6 / 017 §5.3): the only state
        mutation is the snapshot push, built by the SAME from_pushes
        the poll cycle uses — the entity plane reflects the MIRROR,
        never the request. The unconfirmed path pushes NO snapshot
        (the state stays; the next poll cycle retries naturally) and,
        for an OFF, raises the persistent notification instead
        (contract 016 §7's revert obligation made visible — a
        potentially-running unit is never a silent end state). Does
        NOT disconnect on completion (unlike the probe — the
        confirmation lives in the steady-state poll world; the next
        cycle reuses the link).

        Returns the stage dict (FR-3 step 9): the written value, the
        verdict ("confirmed" | "not-confirmed" | "unconfirmed"), the
        fresh mirror's raw and its age relative to the write — the
        tests' oracle and the climate's diagnostics attribute.
        """
        address = self.config_entry.data["address"]
        # 1. The funnel guard, FIRST — before any connect, lock or I/O
        #    (the funnel rule). The ACTIVATION gate rides the
        #    control_allowed thread (either owner surface — FR-1); the
        #    address AND value allowlists stay permanent. While the
        #    gate is closed this fails loudly with the 016 message and
        #    ZERO bytes move (AC-3).
        registers.ensure_writable(
            registers.WRITE_REG_POWER,
            value,
            control_allowed=self.write_control_allowed,
        )
        # 2. Connect if needed (the cycle's connect pattern); transport
        #    failures propagate to the caller (the entity surfaces the
        #    error).
        if not self.client.is_connected:
            await self.client.connect()
        # 3. The audit line (FR-4's exact string, verbatim) — one line
        #    per write, attributable in the owner's log page.
        _LOGGER.info(
            "power write via %d = %d — mirror should follow in the next report",
            registers.WRITE_REG_POWER,
            value,
        )
        mirror_addr = registers.READ_MIRROR_POWER
        result: dict[str, Any] = {
            "value": value,
            "verdict": "unconfirmed",
            "mirror_raw": None,
            "mirror_age_s": None,
        }
        async with self._io_lock:
            # 4a. The fire-and-forget write (FR-2) — the pinned frame on
            #     the _write_frame seam, NEVER write_registers (the
            #     echo trap, contract 017 §4). A write error propagates
            #     (AC-9): no trigger, no cross-check, no snapshot push,
            #     no notification.
            await self.client.write_control(registers.WRITE_REG_POWER, value)
            t_write = _now()
            # 4b. ONE trigger (C-3: the write does not wake the report;
            #     the trigger does — research 017 §2) ...
            await self._trigger_report("power write confirm")
            # 4c. ... then the fresh-mirror wait (the probe's helper,
            #     UNCHANGED: a 2001 capture with last_seen > t_write,
            #     bounded by PROBE_MIRROR_TIMEOUT — the oracle must
            #     postdate the write it verifies).
            cap = await self._wait_fresh_block(
                registers.PUSH_CONTROL_BLOCK_START, t_write
            )
            # 5. The cross-check verdicts (FR-4's exact strings).
            mirror_raw = (
                cap.registers.get(mirror_addr) if cap is not None else None
            )
            if cap is not None:
                result["mirror_raw"] = mirror_raw
                result["mirror_age_s"] = round(cap.last_seen - t_write, 1)
            if cap is None or mirror_raw is None:
                # No fresh report (or a capture whose mirror raw is
                # absent — the same honest verdict): the state is
                # UNKNOWN, never guessed.
                result["verdict"] = "unconfirmed"
                _LOGGER.warning(
                    "power write unconfirmed (no fresh report within "
                    "%.0f s) — the unit's state is UNKNOWN: check the "
                    "panel",
                    const.PROBE_MIRROR_TIMEOUT,
                )
                if value == 0:
                    self._notify_unconfirmed_off(address)
            elif mirror_raw == value:
                result["verdict"] = "confirmed"
                _LOGGER.info(
                    "power write confirmed by mirror (%d = %d)",
                    mirror_addr,
                    mirror_raw,
                )
                self._dismiss_unconfirmed_off(address)
            else:
                result["verdict"] = "not-confirmed"
                _LOGGER.warning(
                    "power write not confirmed by mirror (mirror %d = "
                    "%d, requested %d)",
                    mirror_addr,
                    mirror_raw,
                    value,
                )
                if value == 0:
                    self._notify_unconfirmed_off(address)
            # 6. The snapshot push (confirmed / not-confirmed ONLY —
            #    the MIRROR's truth, never the request; NO push on the
            #    unconfirmed path, AC-8).
            if result["verdict"] in ("confirmed", "not-confirmed"):
                data = AquaTempData.from_pushes(
                    dict(self.client.pushed_blocks),
                    self.push_temp_confirmed,
                    self.push_control_confirmed,
                )
                # async_set_updated_data is a PLAIN (sync) DataUpdateCoordinator
                # method — awaiting it raised "'NoneType' object can't be
                # awaited" after every control write (the frame had already
                # been sent, so the unit switched but the service call
                # reported a failure). Never await this one.
                self.async_set_updated_data(data)
        # FR-3 step 9: the stage dict — the tests' oracle + the
        # climate's diagnostics attribute (FR-5). The displayed state
        # itself never reads it (never optimistic, FR-6).
        self._last_control_write = result
        return result

    def _notify_unconfirmed_off(self, address: str) -> None:
        """FR-3 step 7 / contract 017 §5.5: the unconfirmed-OFF surface
        — a potentially-running unit is NEVER a silent end state.
        The persistent notification names the unit, the requested OFF
        and the remediation (contract 016 §7's revert obligation,
        made visible on the dashboard); a later CONFIRMED cycle for
        the same unit dismisses it."""
        notification_id = f"aqua_temp_ble_power_unconfirmed_{address}"
        message = (
            f"Aqua Temp unit {address}: an OFF command was sent but "
            "could not be verified — the unit's state is UNKNOWN: "
            "check the panel; the unit may still be ON — turn it OFF "
            "from the vendor app or the panel"
        )
        try:
            from homeassistant.components import persistent_notification
        except ImportError:  # pragma: no cover — ships with Home Assistant
            _LOGGER.warning(
                "power write unconfirmed OFF on %s: the persistent "
                "notification component is unavailable — the unit's "
                "state is UNKNOWN: check the panel",
                address,
            )
            return
        persistent_notification.async_create(
            self.hass,
            message,
            title="Aqua Temp: unconfirmed OFF",
            notification_id=notification_id,
        )

    def _dismiss_unconfirmed_off(self, address: str) -> None:
        """FR-3 step 7: a later CONFIRMED cycle for the same unit clears
        the notification (the unknown is resolved — the mirror
        decided). Idempotent: HA's dismiss on a missing id is a
        no-op, so this is safe on every confirmed cycle (a restart's
        leftover notification included)."""
        notification_id = f"aqua_temp_ble_power_unconfirmed_{address}"
        try:
            from homeassistant.components import persistent_notification
        except ImportError:  # pragma: no cover — ships with Home Assistant
            return
        persistent_notification.async_dismiss(self.hass, notification_id)

    # -------------------------------------- spec 019 setpoint write probe
    async def async_setpoint_write_probe(self) -> dict[str, Any]:
        """The setpoint write probe — spec 019 FR-5's TWO-PHASE proof
        instrument (contract 019 §3): baseline (trigger + fresh
        1046-block + the four sanity gates) -> phase A (the NO-OP:
        write the register's OWN current value — harmless by
        construction — then verify the WHOLE 45-register block is
        unchanged: the neighbor alarm catches a mis-aimed write
        before anything real is attempted) -> phase B (the execution
        diff-revert: a -0.5 °C dip toward the range interior, verify
        the mirror follows, revert, verify again) -> ONE verdict line
        at WARNING.

        A MEASUREMENT INSTRUMENT, not a control surface (the 016
        precedent): it writes ONLY build_write_control(1046, ·)
        frames, ONLY values it read from that same register first
        ({S, T}), only after the baseline gates (a sentinel / absent /
        out-of-range / off-step S aborts with refused-baseline and
        ZERO writes), and its frames go through the client's own
        validated seam (write_setpoint_probe_frame — FR-6), NEVER
        through the funnel (contract 019 §3: the probe is the ONLY
        code that may write 1046 before the proof; the funnel stays
        closed). Phase B never runs unless phase A held (the neighbor
        alarm is the hard stop). A failed revert is never silent: the
        not-reversible line states the CURRENT setpoint and the
        remediation. Epistemics: phase A proves HARMLESSNESS, not
        execution (a dropped write looks identical — quickstart 019
        §2 says so); phase B proves execution. The physical effect of
        the whole probe: the pool's heat target dips 0.5 °C for ~2
        minutes, then returns (quickstart 019 §0).

        Single-flight (the _io_lock); disconnects on completion or
        failure (the probe convention). Runs with BOTH setpoint gate
        surfaces closed — it is the gate's CAUSE, never its consumer
        (contract 019 §4.1).

        Returns the stage dict (the mirror observations with raws +
        times, the frames sent, the verdict, the resolved write UUID).
        """
        address = self.config_entry.data["address"]
        write_uuid = getattr(self.client, "write_uuid", None) or const.UUID_WRITE
        mirror_addr = registers.READ_MIRROR_SETPOINT
        block_start = registers.pushed_block_start(mirror_addr) or mirror_addr
        result: dict[str, Any] = {
            "verdict": "no-report",
            "stage": "baseline",
            "write_uuid": write_uuid,
            "mirror_addr": mirror_addr,
            "block": block_start,
            "baseline_raw": None,
            "test_raw": None,
            "frames_sent": [],
            "mirror": {},
        }
        t_probe = _now()

        def _rel(ts: float) -> float:
            return round(ts - t_probe, 1)

        def _finish(verdict: str, detail: str) -> dict[str, Any]:
            """FR-5 step 4: set the verdict, log ONE line at WARNING —
            the resolved write UUID rides in it (the 016 convention) —
            and return the stage dict."""
            result["verdict"] = verdict
            line = f"setpoint write probe: {detail} — write char {write_uuid}"
            result["verdict_line"] = line
            _LOGGER.warning("%s", line)
            return result

        async def _send(value_raw: int, label: str) -> str | None:
            """Write one probe value through the client's seam, record
            the frame, return its hex (None on an ATT failure — the
            chain is logged by the caller's _finish)."""
            frame_hex = modbus.build_write_control(mirror_addr, value_raw).hex()
            _LOGGER.warning(
                "setpoint write probe [%s]: sending %s frame %s on %s",
                address,
                label,
                frame_hex,
                write_uuid,
            )
            try:
                await self.client.write_setpoint_probe_frame(value_raw)
            except Exception as exc:  # noqa: BLE001 — the chain IS the datum
                _LOGGER.debug("setpoint probe write failed", exc_info=True)
                result["att_error"] = exc_chain(exc)
                return None
            result["frames_sent"].append(frame_hex)
            return frame_hex

        async def _fresh_block(before: float) -> Any:
            """Trigger + wait for a fresh 1046-block (the probe's per-write
            cadence: each write is followed by exactly ONE trigger — C-3's
            rule, the write does not wake the report)."""
            await self._trigger_report("setpoint write probe")
            return await self._wait_fresh_block(block_start, before)

        async with self._io_lock:
            try:
                if not self.client.is_connected:
                    await self.client.connect()
                # -- step 1: the baseline — trigger + fresh 1046-block --
                t_mark = _now()
                cap = await _fresh_block(t_mark)
                if cap is None:
                    return _finish(
                        "no-report",
                        "no-report (no fresh 1046 block) — check the "
                        "panel; the report never came back this window "
                        "(radio); nothing was written — just try again",
                    )
                s_raw = cap.registers.get(mirror_addr)
                # -- the four baseline gates (FR-5 step 1): absent,
                #    sentinel, out-of-range, off-step -> refused-baseline
                #    (the probe refuses to compute a safe test value from
                #    a weird register); ZERO writes.
                if (
                    s_raw is None
                    or registers.is_sentinel(s_raw)
                    or not (
                        registers.SETPOINT_MIN_RAW
                        <= s_raw
                        <= registers.SETPOINT_MAX_RAW
                    )
                    or s_raw % registers.SETPOINT_STEP_RAW != 0
                ):
                    return _finish(
                        "refused-baseline",
                        f"refused-baseline (mirror {mirror_addr} = {s_raw!r} "
                        "is not a sane setpoint: absent / sentinel / "
                        "outside 100..400 / off the 0.5 step) — check the "
                        "panel's setpoint display; nothing was written",
                    )
                baseline_regs = dict(cap.registers)
                result["baseline_raw"] = s_raw
                result["mirror"]["baseline"] = {
                    "raw": s_raw,
                    "t": _rel(cap.last_seen),
                }
                _LOGGER.warning(
                    "setpoint write probe [%s]: baseline: mirror %d = %d (t+%.1fs)",
                    address,
                    mirror_addr,
                    s_raw,
                    result["mirror"]["baseline"]["t"],
                )
                # -- step 2: phase A — the no-op (the register's OWN value) --
                result["stage"] = "no-op-write"
                t_noop = _now()
                if await _send(s_raw, "no-op") is None:
                    return _finish(
                        "att-error", f"att-error: {result.get('att_error')}"
                    )
                result["stage"] = "no-op-harvest"
                cap = await _fresh_block(t_noop)
                if cap is None:
                    return _finish(
                        "no-report",
                        "no-report (no fresh 1046 block after the no-op) — "
                        "check the panel; nothing could change (the no-op "
                        "wrote the register's own value); just try again",
                    )
                result["mirror"]["after_no_op"] = {
                    "raw": cap.registers.get(mirror_addr),
                    "t": _rel(cap.last_seen),
                }
                changed = [
                    f"{addr} {baseline_regs.get(addr)}->{cap.registers.get(addr)}"
                    for addr in sorted(set(baseline_regs) | set(cap.registers))
                    if baseline_regs.get(addr) != cap.registers.get(addr)
                ]
                if changed:
                    # AC-6: the neighbor alarm — a mis-aimed write (or an
                    # unexpected panel change) is caught HERE, before any
                    # real value is attempted; phase B never runs.
                    return _finish(
                        "no-op-neighbor-changed",
                        f"no-op-neighbor-changed (register(s) moved during "
                        f"the no-op: {'; '.join(changed)}) — the probe "
                        "ABORTED before any real change; check the panel's "
                        "setpoint is unchanged and report the line "
                        "(quickstart 019 §2)",
                    )
                _LOGGER.warning(
                    "setpoint write probe [%s]: after no-op: mirror %d = %d "
                    "(t+%.1fs) — block unchanged",
                    address,
                    mirror_addr,
                    result["mirror"]["after_no_op"]["raw"],
                    result["mirror"]["after_no_op"]["t"],
                )
                # -- step 3: phase B — the execution diff-revert. T dips
                #    toward the RANGE INTERIOR (S - 5, or S + 5 when S
                #    sits at the min) — the smallest step that is provably
                #    visible on the wire (plan 019 §2).
                t_raw = s_raw - registers.SETPOINT_STEP_RAW
                if t_raw < registers.SETPOINT_MIN_RAW:
                    t_raw = s_raw + registers.SETPOINT_STEP_RAW
                result["test_raw"] = t_raw
                result["stage"] = "test-write"
                t_test = _now()
                if await _send(t_raw, "test") is None:
                    return _finish(
                        "att-error", f"att-error: {result.get('att_error')}"
                    )
                result["stage"] = "test-harvest"
                cap = await _fresh_block(t_test)
                if cap is None:
                    return _finish(
                        "no-report",
                        "no-report (no fresh 1046 block after the test "
                        "write) — the setpoint's state is UNKNOWN: check "
                        "the panel; if it changed unexpectedly, restore "
                        "it from the panel or the vendor app",
                    )
                b_raw = cap.registers.get(mirror_addr)
                result["mirror"]["after_test"] = {
                    "raw": b_raw,
                    "t": _rel(cap.last_seen),
                }
                _LOGGER.warning(
                    "setpoint write probe [%s]: after test: mirror %d = %d (t+%.1fs)",
                    address,
                    mirror_addr,
                    b_raw,
                    result["mirror"]["after_test"]["t"],
                )
                if b_raw != t_raw:
                    return _finish(
                        "not-executed",
                        f"not-executed (mirror {mirror_addr} stayed {b_raw} "
                        f"after the test write, expected {t_raw}) — the "
                        "unit did not act on our frame; if the panel's "
                        "setpoint changed unexpectedly, restore it from "
                        "the panel or the vendor app; try once more "
                        "(radio), then report the line",
                    )
                # -- the revert: write S back, verify ---------------------
                result["stage"] = "revert-write"
                t_revert = _now()
                if await _send(s_raw, "revert") is None:
                    return _finish(
                        "att-error",
                        f"att-error: {result.get('att_error')} — the "
                        f"setpoint is now {t_raw / registers.TEMP_SCALE:.1f} "
                        "°C: restore it from the panel or the vendor app",
                    )
                result["stage"] = "revert-harvest"
                cap = await _fresh_block(t_revert)
                if cap is None:
                    return _finish(
                        "no-report",
                        "no-report (no fresh 1046 block after the revert) "
                        "— the setpoint's state is UNCONFIRMED: it may sit "
                        "at the test value; check the panel and restore the "
                        "setpoint from the panel or the vendor app if "
                        "needed",
                    )
                c_raw = cap.registers.get(mirror_addr)
                result["mirror"]["after_revert"] = {
                    "raw": c_raw,
                    "t": _rel(cap.last_seen),
                }
                _LOGGER.warning(
                    "setpoint write probe [%s]: after revert: mirror %d = %d (t+%.1fs)",
                    address,
                    mirror_addr,
                    c_raw,
                    result["mirror"]["after_revert"]["t"],
                )
                if c_raw != s_raw:
                    return _finish(
                        "not-reversible",
                        f"not-reversible (mirror {mirror_addr} stayed "
                        f"{c_raw} after the revert, expected {s_raw}) — the "
                        f"setpoint is now {c_raw / registers.TEMP_SCALE:.1f} "
                        "°C — restore it from the panel or the vendor app",
                    )
                # -- step 4: the verdict ---------------------------------
                result["stage"] = "done"
                return _finish(
                    "executed+reversible",
                    f"EXECUTED + REVERSIBLE (mirror {mirror_addr} followed "
                    f"{s_raw}→{t_raw}→{s_raw}) — evidence for "
                    "WRITE_SETPOINT_PROVEN",
                )
            finally:
                # Disconnect on completion or failure (the probe
                # convention) — even when an AquaTempError propagates
                # to the caller.
                try:
                    await self.client.disconnect()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("probe disconnect failed", exc_info=True)

    # -------------------------------- spec 019 setpoint control write
    async def async_control_write_setpoint(self, celsius: float) -> dict[str, Any]:
        """The guarded setpoint write path (spec 019 FR-10, contract
        019 §2/§5): validate -> the funnel -> connect -> audit -> under
        the I/O lock: the fire-and-forget write -> ONE trigger -> the
        fresh 1046-block wait -> the three cross-check verdicts -> the
        mirror snapshot push.

        Validation is FIRST and LOUD (contract 019 §2.1: never write a
        setpoint value that was not explicitly requested — no
        clamping, no rounding): a non-finite value, a non-0.5 multiple
        and an out-of-range value all raise ValueError BEFORE any byte
        moves. The funnel (ensure_writable with the setpoint_allowed
        thread — FR-9's live OR-property, the 017 pattern) is the
        second gate, still BEFORE any connect, lock or I/O. The write
        goes through the client's write_setpoint rail (FR-6's control
        successor — the same value discipline), NEVER through the
        classic funnel and NEVER write_registers (the echo trap,
        contract 017 §4/019 §2.4).

        Never optimistic (contract 019 §5.2): the only state mutation
        is the snapshot push, built by the SAME from_pushes the poll
        cycle uses — the tile reflects the MIRROR, never the request.
        The unconfirmed path pushes NO snapshot and raises NO
        persistent notification (contract 019 §5.3 — the deliberate
        divergence from the power cycle: a failed setpoint write
        leaves the machine at its old target, no hazard; the WARNING +
        the tile's mirror-truth suffice). Does NOT disconnect on
        completion (the confirmation lives in the steady-state poll
        world).

        The write is allowed REGARDLESS of power state (contract 019
        §5.4): the register is the machine's stored parameter — the
        panel changed the setpoint with the unit off [P owner].

        Returns the stage dict (FR-10 step 6): value_raw, verdict
        ("confirmed" | "not-confirmed" | "unconfirmed"), the fresh
        mirror's raw and its age relative to the write — the tests'
        oracle and the climate's diagnostics attribute
        (last_setpoint_write, FR-11).
        """
        # 1. Validation — never silently clamp a REGULATION value
        #    (contract 019 §2.2); the errors are loud and named.
        if not isinstance(celsius, (int, float)) or isinstance(celsius, bool):
            raise ValueError(
                f"setpoint {celsius!r} is not a number (contract 019 §2)"
            )
        if not math.isfinite(celsius):
            raise ValueError(
                f"setpoint {celsius!r} is not a finite value (contract 019 §2)"
            )
        raw = int(round(celsius * registers.TEMP_SCALE))
        if raw % registers.SETPOINT_STEP_RAW != 0:
            raise ValueError(
                f"setpoint {celsius} °C is not a multiple of the 0.5 °C "
                f"step (raw {raw}, step {registers.SETPOINT_STEP_RAW}) — "
                "a regulation value is never rounded (contract 019 §2.2)"
            )
        if not (
            registers.SETPOINT_MIN_RAW <= raw <= registers.SETPOINT_MAX_RAW
        ):
            raise ValueError(
                f"setpoint {celsius} °C is outside the guarded range "
                "10.0-40.0 °C (raw "
                f"{registers.SETPOINT_MIN_RAW}..{registers.SETPOINT_MAX_RAW}) "
                "— a regulation value is never clamped (contract 019 §2.2)"
            )
        # 2. The funnel guard, FIRST — before any connect, lock or I/O
        #    (the funnel rule). The setpoint gate rides the
        #    setpoint_allowed thread (either owner surface — FR-9); the
        #    address AND value allowlists stay permanent. While the gate
        #    is closed this fails loudly with the 019 message and ZERO
        #    bytes move (AC-2).
        registers.ensure_writable(
            registers.WRITE_REG_SETPOINT,
            raw,
            setpoint_allowed=self.write_setpoint_allowed,
        )
        # 3. Connect if needed (the cycle's connect pattern); transport
        #    failures propagate to the caller (the entity surfaces the
        #    error).
        if not self.client.is_connected:
            await self.client.connect()
        # 4. The audit line (contract 019 §8's audit obligation — the
        #    017 pattern, setpoint-flavored): one line per write,
        #    attributable in the owner's log page.
        _LOGGER.info(
            "setpoint write via %d = %d — mirror should follow in the next report",
            registers.WRITE_REG_SETPOINT,
            raw,
        )
        mirror_addr = registers.READ_MIRROR_SETPOINT
        block_start = registers.pushed_block_start(mirror_addr) or mirror_addr
        result: dict[str, Any] = {
            "value_raw": raw,
            "verdict": "unconfirmed",
            "mirror_raw": None,
            "mirror_age_s": None,
        }
        async with self._io_lock:
            # 5a. The fire-and-forget write — the client's own rail
            #     (address, range, step), on the _write_frame seam.
            #     A write error propagates: no trigger, no cross-check,
            #     no snapshot push, no notification.
            await self.client.write_setpoint(raw)
            t_write = _now()
            # 5b. ONE trigger (C-3: the write does not wake the report;
            #     the trigger does — research 017 §2, unchanged).
            await self._trigger_report("setpoint write confirm")
            # 5c. The fresh-block wait — a 1046 capture with last_seen >
            #     t_write (the oracle must postdate the write it
            #     verifies; contract 019 §1).
            cap = await self._wait_fresh_block(block_start, t_write)
            mirror_raw = cap.registers.get(mirror_addr) if cap is not None else None
            if cap is not None:
                result["mirror_raw"] = mirror_raw
                result["mirror_age_s"] = round(cap.last_seen - t_write, 1)
            # 6. The three verdicts (017 FR-4's semantics,
            #    setpoint-flavored; NO persistent notification —
            #    contract 019 §5.3).
            if cap is None or mirror_raw is None:
                # No fresh report (or a capture whose mirror raw is
                # absent — the same honest verdict): the state is
                # UNKNOWN, never guessed; the machine keeps its old
                # target — no hazard (the WARNING + the tile's
                # mirror-truth suffice).
                result["verdict"] = "unconfirmed"
                _LOGGER.warning(
                    "setpoint write unconfirmed (no fresh report within "
                    "%.0f s) — the setpoint's state is UNKNOWN: check the "
                    "panel",
                    const.PROBE_MIRROR_TIMEOUT,
                )
            elif mirror_raw == raw:
                result["verdict"] = "confirmed"
                _LOGGER.info(
                    "setpoint write confirmed by mirror (%d = %d)",
                    mirror_addr,
                    mirror_raw,
                )
            else:
                result["verdict"] = "not-confirmed"
                _LOGGER.warning(
                    "setpoint write not confirmed by mirror (mirror %d = "
                    "%d, requested %d)",
                    mirror_addr,
                    mirror_raw,
                    raw,
                )
            # 7. The snapshot push (confirmed / not-confirmed ONLY —
            #    the MIRROR's truth, never the request; NO push on the
            #    unconfirmed path).
            if result["verdict"] in ("confirmed", "not-confirmed"):
                data = AquaTempData.from_pushes(
                    dict(self.client.pushed_blocks),
                    self.push_temp_confirmed,
                    self.push_control_confirmed,
                )
                # async_set_updated_data is a PLAIN (sync)
                # DataUpdateCoordinator method — never await it (the
                # 017 production lesson).
                self.async_set_updated_data(data)
        # FR-10 step 6: the stage dict — the tests' oracle + the
        # climate's diagnostics attribute (FR-11). The displayed state
        # itself never reads it (never optimistic).
        self._last_setpoint_write = result
        return result