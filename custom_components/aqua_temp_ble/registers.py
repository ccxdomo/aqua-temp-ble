"""Single source of truth for the Aqua Temp register mapping and calibration.

Every label<->register and value<->meaning decision lives HERE and nowhere
else. The static APK analysis (FINDINGS.md §11.4 / §12) established this map
at ~80% confidence; the live probe tool (the "Probe registers" button and
the `aqua_temp_ble.probe_registers` service, see docs/CALIBRATION.md) closes
the remaining gap. After a live calibration session, correcting a label =
editing one entry below + restarting Home Assistant.

Spec 003 (specs/003-sensor-coverage/) extends this surface with the seven
parameter families observed in the vendor app's parameter view
(T/H/R/P/S/O/D — see FAMILIES below). The decode rules (sentinel set,
plausibility guard, binary polarity) and the write allowlist
(ensure_writable) are normative in the spec's contracts; the H/R/P installer
families are deliberately NOT exposed as entities and never writable.

This module must stay Home-Assistant-free (stdlib only) so the standalone
tools (tools/smoke_client.py) and the unit tests can import it.

Status legend used throughout this repo:
  [P] PROVEN — recovered from the APK and CRC-verified ([P apk]), or observed
      in the vendor app's parameter view ([P panel], owner's photographs,
      2026-10)
  [A] ASSUMED — reasonable default, cheap to correct after calibration
  [V] TO-VERIFY — cannot be known without one live session with the unit
"""

from __future__ import annotations

from dataclasses import dataclass

# --- base read block (proven frame 63030007002d3c54) ---
BASE_BLOCK_START = 7   # [P]
BASE_BLOCK_COUNT = 45  # [P] -> registers 7..51

# --- [P] verified base-block registers ---
REG_POWER = 8                 # 0=off, 1=on
REG_BOOST = 11                # future use, read-only here
REG_MODE = 13                 # cool/heat/auto, values below [A]
REG_SET_CURRENT_TEMP = 14     # current setpoint, x10
REG_SET_COOL_TARGET = 19      # x10
REG_SET_HEAT_TARGET = 20      # x10
REG_SET_AUTO_TARGET = 21      # x10
REG_COOL_MIN_TARGET = 22      # x10
REG_COOL_MAX_TARGET = 23      # x10
REG_HEAT_MIN_TARGET = 24      # x10
REG_HEAT_MAX_TARGET = 25      # x10
REG_INLET_WATER_TEMP = 26     # x10, signed
REG_OUTLET_WATER_TEMP = 27    # x10, signed
REG_AMBIENT_TEMP = 28         # x10, signed
REG_FAULT1 = 47
REG_FIRMWARE_FIRST = 48       # 48..51 [V] semantics
REG_FIRMWARE_LAST = 51

# --- [V] TO-VERIFY placeholders: set the register number after calibration
# (docs/CALIBRATION.md §8, spec 003 calibration-contract.md). None = entity
# not created yet, so no unverified data can ever be displayed under a name.
# Family codes refer to the vendor app's parameter view (FAMILIES below).
REG_COMPRESSOR: int | None = None       # O01 Compressor, expected in 29..46
REG_CIRCULATE_PUMP: int | None = None   # O02 Circulate pump, expected in 29..46
REG_FOUR_WAY_VALVE: int | None = None   # O04 4-way valve, expected in 29..46
REG_HIGH_FAN: int | None = None         # O03 High fan, expected in 29..46
REG_HP_SWITCH: int | None = None        # S01 HP switch (diagnostic entity)
REG_LP_SWITCH: int | None = None        # S02 LP switch (diagnostic entity)
REG_FLOW_SWITCH: int | None = None      # S03 Flow switch (diagnostic entity)
REG_SUCTION_TEMP: int | None = None     # T01 Suction, x10, signed
REG_COIL_TEMP: int | None = None        # T04 Coil, x10, signed
REG_EXHAUST_TEMP: int | None = None     # T06 Exhaust, x10, signed

# --- [A] value mappings (verify via runbook §4.4 step M) ---
# Rationale for this order: the sequential register allocation follows
# Cool->Heat->Auto (regs 19/20/21), so the Mode enum likely shares that order.
MODE_VALUE_TO_NAME: dict[int, str] = {0: "cool", 1: "heat", 2: "auto"}

TEMP_SCALE = 10.0         # [P] value = int(degrees_C * 10)
FALLBACK_MIN_TARGET = 5.0   # [A] if bound registers are missing/sentinel
FALLBACK_MAX_TARGET = 40.0  # [A]

# --- sentinels & plausibility (spec 003, decode-contract.md §2) ---
# Two "sensor absent" conventions exist on the wire; both decode to None so
# the state shows "unknown" and the vendor's own display bug (3276.5 degC for
# an absent sensor) can never happen here:
#   -9990  [P apk]   the app's Status page defines -999.0 degC as
#                    "sensor absent" (FINDINGS.md §12);
#   32765  [P panel] 0x7FFD, two below the int16 maximum — observed for the
#                    absent T01 Suction sensor on the owner's unit (2026-10),
#                    which the vendor app renders as 3276.5 degC.
SENTINEL_RAWS: frozenset[int] = frozenset({-9990, 32765})
SENTINEL_RAW = -9990  # [P] legacy alias, the -9990 member of the set (FR-14)

# Plausibility window (FR-2): a temperature raw outside these bounds decodes
# to None -> state "unknown" + one warning per address. Real values always
# pass: the highest legitimate system temperature observed is the H04
# exhaust-high safety threshold at 105.0 degC [P panel] (raw 1050), and
# winter ambients stay far above -45.0 degC (raw -450). The window also
# catches unknown sentinel conventions (e.g. 0x7FFF = 32767) and mis-decoded
# garbage (e.g. byte-swapped 0xAA00 = 43520).
TEMP_PLAUSIBLE_MIN_RAW = -450   # -> -45.0 degC
TEMP_PLAUSIBLE_MAX_RAW = 1500   # -> +150.0 degC

# --- calibration switches (flip after live verification, then restart HA) ---
WRITE_ECHO_EXPECTED = True         # False if the device sends no FC10 echo (runbook step W)
WRITE_MODE_SETPOINT_TOO = False    # True if the app also writes reg 19/20/21 on setpoint change (step S)
BINARY_ACTIVE_WHEN_NONZERO = True  # [A] polarity default of the binary sensors
REG_DATA_LITTLE_ENDIAN = False     # [A] register data byte order; flip if the probe
# shows byte-swapped values (e.g. 0xF300 instead of 0x00F3 for 24.3 degC).


def is_sentinel(raw: int | None) -> bool:
    """True when `raw` is a "sensor absent" sentinel (False for None)."""
    return raw is not None and raw in SENTINEL_RAWS


def is_plausible_temp(raw: int | None) -> bool:
    """True when `raw` lies inside the temperature plausibility window."""
    return raw is not None and TEMP_PLAUSIBLE_MIN_RAW <= raw <= TEMP_PLAUSIBLE_MAX_RAW


def decode_temp(raw: int | None) -> float | None:
    """Raw register -> degrees Celsius; None when absent, sentinel or implausible.

    Priority (decode-contract.md §2): None -> None; sentinel -> None (sensor
    absent — a normal state, never rendered as a temperature); implausible
    -> None (the consumer logs one warning per address per session);
    otherwise round(raw / TEMP_SCALE, 1). Pure: no logging here (NFR-1).
    """
    if raw is None or is_sentinel(raw):
        return None
    if not is_plausible_temp(raw):
        return None
    return round(raw / TEMP_SCALE, 1)


def decode_flag(raw: int | None, active_when_nonzero: bool) -> bool | None:
    """Raw register -> ON/OFF flag; None when the register is absent.

    Polarity per decode-contract.md §4: nonzero means ON when
    `active_when_nonzero` is True; inverted (zero means ON) when False. The
    S-family may use the opposite convention of the O-family [V] — hence the
    per-RegDef polarity (research.md §9).
    """
    if raw is None:
        return None
    return (raw != 0) if active_when_nonzero else (raw == 0)


# --- write security (spec 003 §6 / FR-9) ---
# The ONLY registers the integration may ever write: power, mode and the
# current setpoint — all proven user-operational writes inside the base
# block. Regs 19/20/21 join only behind the default-off
# WRITE_MODE_SETPOINT_TOO mirror switch. The installer families (H/R/P) and
# the high blocks 1001-2090 are never writable; there is deliberately NO
# generic write surface (research.md §7) — an erroneous H04/R-limit write can
# damage the heat pump or prevent it from starting.
ALLOWED_WRITE_BASE: frozenset[int] = frozenset(
    {REG_POWER, REG_MODE, REG_SET_CURRENT_TEMP}
)


def ensure_writable(address: int) -> None:
    """Guard the single write funnel: raise ValueError unless allowed (FR-9).

    coordinator.async_write_register calls this as its first statement, so
    no code path — present or future — can write outside the allowlist.
    """
    allowed = ALLOWED_WRITE_BASE
    if WRITE_MODE_SETPOINT_TOO:
        allowed = allowed | {
            REG_SET_COOL_TARGET,
            REG_SET_HEAT_TARGET,
            REG_SET_AUTO_TARGET,
        }
    if address not in allowed:
        raise ValueError(f"register {address} is not writable (spec 003 §6)")


@dataclass(frozen=True)
class RegDef:
    """One entity definition, driven by the registries below."""

    key: str                     # entity key + unique_id suffix
    address: int | None          # None = unverified -> entity not added
    label: str                   # probe-log annotation
    scale: float = 1.0
    signed: bool = False
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    icon: str | None = None
    diagnostic: bool = False
    is_binary: bool = False
    # --- spec 003 fields (appended; existing positional uses keep working) ---
    active_when_nonzero: bool | None = None  # None -> BINARY_ACTIVE_WHEN_NONZERO
    family: str | None = None                # "T01", "S03", ... doc/probe tag


def resolve_polarity(regdef: RegDef) -> bool:
    """Per-RegDef binary polarity (FR-4/AC-8).

    The entry's `active_when_nonzero` when set, else the global
    BINARY_ACTIVE_WHEN_NONZERO default — so calibration can flip one S/O
    entry without touching code or the global switch.
    """
    if regdef.active_when_nonzero is not None:
        return regdef.active_when_nonzero
    return BINARY_ACTIVE_WHEN_NONZERO


SENSORS: tuple[RegDef, ...] = (
    RegDef("inlet_water_temp", REG_INLET_WATER_TEMP, "InletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T02"),
    RegDef("outlet_water_temp", REG_OUTLET_WATER_TEMP, "OutletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T03"),
    RegDef("ambient_temp", REG_AMBIENT_TEMP, "AT", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T05"),
    # spec 003 T-family placeholders — [V] indices; entities appear only
    # after calibration pins the registers (calibration-contract.md §3).
    RegDef("suction_temp", REG_SUCTION_TEMP, "SuctionTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T01"),
    RegDef("coil_temp", REG_COIL_TEMP, "Coil", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T04"),
    RegDef("exhaust_temp", REG_EXHAUST_TEMP, "Exhaust", TEMP_SCALE, True,
           "temperature", "°C", "measurement", family="T06"),
    # entity-contract.md §2 lists this as `diagnostic`; shipped behaviour is
    # kept as-is (non-regression, "existing, unchanged"). No category change.
    RegDef("fault_code", REG_FAULT1, "Fault1", icon="mdi:alert-circle"),
    RegDef("firmware", REG_FIRMWARE_FIRST, "Firmware", icon="mdi:information",
           diagnostic=True),  # special-case: joins regs 48..51
)

BINARIES: tuple[RegDef, ...] = (
    # O family (outputs) — labels panel-confirmed [P panel]; keys/icons are
    # stable ids and stay unchanged.
    RegDef("compressor", REG_COMPRESSOR, "Compressor", is_binary=True,
           icon="mdi:engine", family="O01"),
    RegDef("circulate_pump", REG_CIRCULATE_PUMP, "Circulate pump", is_binary=True,
           icon="mdi:pump", family="O02"),
    RegDef("high_fan", REG_HIGH_FAN, "High fan", is_binary=True,
           icon="mdi:fan", family="O03"),
    RegDef("four_way_valve", REG_FOUR_WAY_VALVE, "4-way valve", is_binary=True,
           icon="mdi:swap-horizontal", family="O04"),
    # S family (safety inputs) — DIAGNOSTIC entities; polarity stays None
    # (global default) until the live calibration proves it (research.md §9).
    RegDef("hp_switch", REG_HP_SWITCH, "HP switch", is_binary=True,
           icon="mdi:alert-circle", diagnostic=True, family="S01"),
    RegDef("lp_switch", REG_LP_SWITCH, "LP switch", is_binary=True,
           icon="mdi:alert-circle", diagnostic=True, family="S02"),
    RegDef("flow_switch", REG_FLOW_SWITCH, "Flow switch", is_binary=True,
           icon="mdi:water", diagnostic=True, family="S03"),
)

# Reverse map used by the probe logs ("reg N = ... # Label"). The [P]/[V] tag
# records the confidence level of each label; the family code documents the
# panel-observed group (spec 003 data-model.md).
KNOWN_LABELS: dict[int, str] = {
    REG_POWER: "Power [P]",
    REG_BOOST: "EnBoostMode [P]",
    REG_MODE: "Mode [P]",
    REG_SET_CURRENT_TEMP: "SetCurrentTemp [P]",
    REG_SET_COOL_TARGET: "SetCoolTargetTemp [P]",
    REG_SET_HEAT_TARGET: "SetHeatTargetTemp [P]",
    REG_SET_AUTO_TARGET: "SetAutoTargetTemp [P]",
    REG_COOL_MIN_TARGET: "SetCoolMinTargetTemp [P]",
    REG_COOL_MAX_TARGET: "SetCoolMaxTargetTemp [P]",
    REG_HEAT_MIN_TARGET: "SetHeatMinTargetTemp [P]",
    REG_HEAT_MAX_TARGET: "SetHeatMaxTargetTemp [P]",
    REG_INLET_WATER_TEMP: "InletWaterTemp [P] (T02)",
    REG_OUTLET_WATER_TEMP: "OutletWaterTemp [P] (T03)",
    REG_AMBIENT_TEMP: "AT [P] (T05)",
    REG_FAULT1: "Fault1 [P]",
    REG_FIRMWARE_FIRST: "Firmware [V]",  # marks the 48..51 group
}

# Placeholder labels ([V]) are only meaningful once their register is pinned
# down; the family tag documents the panel-observed group (entity-contract.md
# §2/§3).
for _reg, _label in (
    (REG_COMPRESSOR, "Compressor [V] (O01)"),
    (REG_CIRCULATE_PUMP, "Circulate pump [V] (O02)"),
    (REG_HIGH_FAN, "High fan [V] (O03)"),
    (REG_FOUR_WAY_VALVE, "4-way valve [V] (O04)"),
    (REG_HP_SWITCH, "HP switch [V] (S01)"),
    (REG_LP_SWITCH, "LP switch [V] (S02)"),
    (REG_FLOW_SWITCH, "Flow switch [V] (S03)"),
    (REG_SUCTION_TEMP, "SuctionTemp [V] (T01)"),
    (REG_COIL_TEMP, "Coil [V] (T04)"),
    (REG_EXHAUST_TEMP, "Exhaust [V] (T06)"),
):
    if _reg is not None:
        KNOWN_LABELS[_reg] = _label
del _reg, _label

# Documentation-only mirror of the panel observation (2026-10,
# specs/003-sensor-coverage/data-model.md). NO consumer — do not build logic
# on it (plan.md §5); it exists so the seven families and their HA exposure
# policy are readable next to the registries. H/R/P/D have NO entity of any
# platform and are never writable (spec 003 §6).
FAMILIES: dict[str, dict[str, str]] = {
    "T": {"meaning": "live temperatures (T01-T06)", "exposure": "temperature sensors"},
    "H": {"meaning": "machine settings (H01-H06, H70-H72)",
          "exposure": "NONE — never writable from HA (spec 003 §6)"},
    "R": {"meaning": "regulation thresholds, installer (R01-R11)",
          "exposure": "NONE — never writable from HA (spec 003 §6)"},
    "P": {"meaning": "filtration program (P01-P04)",
          "exposure": "NONE — never writable from HA (spec 003 §6)"},
    "S": {"meaning": "safety inputs / switches (S01-S06)",
          "exposure": "binary sensors, DIAGNOSTIC"},
    "O": {"meaning": "outputs (O01-O04)", "exposure": "binary sensors"},
    "D": {"meaning": "digital states (D01-D06)",
          "exposure": "NONE — known but unmapped, no speculation"},
}


def in_base_block(address: int) -> bool:
    """True when `address` lies inside the polled base block (FR-7 gating)."""
    return BASE_BLOCK_START <= address <= BASE_BLOCK_START + BASE_BLOCK_COUNT - 1


def check_registry_integrity(
    sensors: tuple[RegDef, ...] = SENSORS,
    binaries: tuple[RegDef, ...] = BINARIES,
) -> None:
    """Validate the entity registries (FR-11 / entity-contract.md §6).

    A mis-calibration must fail loudly at import instead of silently
    shadowing one label with another's register. Raises ValueError on:
    - duplicate entity keys (across both registries);
    - duplicate non-None addresses (across both registries);
    - a pinned address outside the polled base block 7..51 (high-block
      polling is not implemented — spec 003 §8; leave such a register
      unpinned until phase B);
    - a binary RegDef carrying a temperature device_class (or vice versa).
    The shipped registries must pass — asserted in tests/test_registers.py.
    """
    seen_keys: set[str] = set()
    seen_addresses: dict[int, str] = {}
    for registry_name, registry in (("SENSORS", sensors), ("BINARIES", binaries)):
        for regdef in registry:
            if regdef.key in seen_keys:
                raise ValueError(
                    f"duplicate entity key {regdef.key!r} in the registries (FR-11)"
                )
            seen_keys.add(regdef.key)
            if regdef.is_binary and regdef.device_class == "temperature":
                raise ValueError(
                    f"binary RegDef {regdef.key!r} must not carry a temperature "
                    "device_class (FR-11)"
                )
            if regdef.address is None:
                continue
            if not in_base_block(regdef.address):
                raise ValueError(
                    f"{registry_name} entry {regdef.key!r} pins register "
                    f"{regdef.address}, outside the polled base block "
                    f"{BASE_BLOCK_START}..{BASE_BLOCK_START + BASE_BLOCK_COUNT - 1}; "
                    "high-block polling is not implemented (spec 003 §8) — "
                    "leave the register unpinned (docs/CALIBRATION.md §8.4)"
                )
            if regdef.address in seen_addresses:
                raise ValueError(
                    f"register {regdef.address} pinned for both "
                    f"{seen_addresses[regdef.address]!r} and {regdef.key!r} (FR-11)"
                )
            seen_addresses[regdef.address] = regdef.key


check_registry_integrity()