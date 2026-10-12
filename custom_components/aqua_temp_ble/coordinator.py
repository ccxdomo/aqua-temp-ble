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
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import const, modbus, registers
from .aqua_client import AquaTempBleClient, AquaTempError, AquaTempTimeoutError

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
HIGH_BLOCKS: dict[str, int] = {
    "1001": 1001,
    "1046": 1046,
    "1091": 1091,
    "1136": 1136,
    "2001": 2001,
    "2046": 2046,
}

# Log each unknown Mode value only once per HA session.
_logged_unknown_modes: set[int | None] = set()

# Spec 015 (FR-7/D-3): the same once-per-session rule for the PUSHED
# mode enum — a separate set, because the two planes warn for different
# registers with different calibration surfaces (PUSHED_MODE_VALUE_TO_NAME
# vs the base MODE_VALUE_TO_NAME); a value warned on one plane never
# silences the other.
_logged_unknown_push_modes: set[int | None] = set()

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
    ambient_temp: float | None       # reg 28 / 10
    fault_code: int | None           # reg 47
    firmware: str | None             # regs 48..51 joined
    binaries: dict[str, bool | None]  # verified binary regs only
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

        binaries: dict[str, bool | None] = {}
        for d in registers.BINARIES:
            if d.address is None:
                continue  # not calibrated -> entity not created, no data shown
            # Per-RegDef polarity, else the global default (FR-4).
            binaries[d.key] = registers.decode_flag(
                regs.get(d.address), registers.resolve_polarity(d)
            )

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
            binaries=binaries,
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
        (raw != 0), mode_value as the raw enum, hvac_mode via PUSHED_MODE_
        VALUE_TO_NAME ([A], D-3 — an unmapped value renders None + ONE
        warning per session; power False wins and renders OFF), and the
        setpoint decodes only when a home is ever pinned (None today —
        no target is displayed, FR-5). Control-unconfirmed => every
        control key is None — the exact 0.10.0 behaviour (FR-11c). The
        FR-10 diagnostics raws (push_control_raws) ride the snapshot
        GATE-INDEPENDENTLY: they are diagnostics with evidence tags,
        never entity states. Binaries stay empty (their pushed homes are
        unknown [V]); fault/firmware have no pushed home [P] — None by
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
        # FR-7 (contract 015-A §5): the confirmed control decode.
        power: bool | None = None
        mode_value: int | None = None
        hvac_mode: HVACMode | None = None
        target_temp: float | None = None
        c_cap = captures.get(registers.PUSH_CONTROL_BLOCK_START)
        if c_cap is not None and control_confirmed:
            cregs = c_cap.registers
            p_addr = registers.PUSHED_CONTROL.get("power")
            raw = cregs.get(p_addr) if p_addr is not None else None
            power = registers.decode_flag(raw, active_when_nonzero=True)
            m_addr = registers.PUSHED_CONTROL.get("mode")
            mode_value = cregs.get(m_addr) if m_addr is not None else None
            if power is False:
                hvac_mode = HVACMode.OFF
            elif power is True:
                name = (
                    registers.PUSHED_MODE_VALUE_TO_NAME.get(mode_value)
                    if mode_value is not None
                    else None
                )
                hvac_mode = MODE_NAME_TO_HVAC.get(name) if name is not None else None
                if hvac_mode is None and mode_value not in _logged_unknown_push_modes:
                    _logged_unknown_push_modes.add(mode_value)
                    _LOGGER.warning(
                        "Unknown pushed Mode value %r (reg %s) — update "
                        "registers.PUSHED_MODE_VALUE_TO_NAME after "
                        "calibration (quickstart 015 §5)",
                        mode_value,
                        m_addr,
                    )
            s_addr = registers.PUSHED_CONTROL.get("setpoint")
            if s_addr is not None:
                target_temp = decode_temp_logged(s_addr, cregs.get(s_addr))
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
            binaries={},
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
            binaries=base.binaries,
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
                # Spec 011 FR-6: the evidence-gated pairing lever. Default
                # OFF keeps this byte-identical to the pre-011 behaviour;
                # only an explicit opt-in after the security probe answered
                # ("answered after pairing") turns it on.
                await self.client.connect(
                    pair=self.config_entry.options.get(
                        "pair_on_connect", const.DEFAULT_PAIR_ON_CONNECT
                    )
                )
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
                await self.client.connect(
                    pair=self.config_entry.options.get(
                        "pair_on_connect", const.DEFAULT_PAIR_ON_CONNECT
                    )
                )
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
                _LOGGER.info(
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
        # FR-9: the single write funnel guard — before any lock or I/O, so no
        # code path can ever write outside {8, 13, 14} (+19-21 behind the
        # WRITE_MODE_SETPOINT_TOO switch). Installer settings (H/R/P) and the
        # high blocks 1001-2090 are never writable (spec 003 §6).
        registers.ensure_writable(address)
        async with self._io_lock:
            await self.client.write_registers(address, [value])
        await self.async_request_refresh()  # debounced confirmation read

    # ---------------------------------------------------------------- probe
    async def async_probe_block(self, block: str = "base") -> dict[str, Any]:
        """Read a register block and log every register (calibration, §4)."""
        block = str(block)
        if block != "base" and block not in HIGH_BLOCKS:
            raise ValueError(f"unknown block {block!r}")
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()
            if block == "base":
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