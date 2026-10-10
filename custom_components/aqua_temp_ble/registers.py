"""Single source of truth for the Aqua Temp register mapping and calibration.

Every label<->register and value<->meaning decision lives HERE and nowhere
else. The static APK analysis (FINDINGS.md §11.4 / §12) established this map
at ~80% confidence; the live probe tool (the "Probe registers" button and
the `aqua_temp_ble.probe_registers` service, see docs/CALIBRATION.md) closes
the remaining gap. After a live calibration session, correcting a label =
editing one entry below + restarting Home Assistant.

This module must stay Home-Assistant-free (stdlib only) so the standalone
tools (tools/smoke_client.py) and the unit tests can import it.

Status legend used throughout this repo:
  [P] PROVEN — recovered from the APK and CRC-verified
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
# (docs/CALIBRATION.md §4.4 step 4). None = entity not created yet, so no
# unverified data can ever be displayed under a name.
REG_COMPRESSOR: int | None = None       # expected somewhere in 29..46
REG_CIRCULATE_PUMP: int | None = None   # expected somewhere in 29..46
REG_FOUR_WAY_VALVE: int | None = None   # expected somewhere in 29..46

# --- [A] value mappings (verify via runbook §4.4 step M) ---
# Rationale for this order: the sequential register allocation follows
# Cool->Heat->Auto (regs 19/20/21), so the Mode enum likely shares that order.
MODE_VALUE_TO_NAME: dict[int, str] = {0: "cool", 1: "heat", 2: "auto"}

SENTINEL_RAW = -9990      # [P] -999.0 degC x 10 = sensor absent
TEMP_SCALE = 10.0         # [P] value = int(degrees_C * 10)
FALLBACK_MIN_TARGET = 5.0   # [A] if bound registers are missing/sentinel
FALLBACK_MAX_TARGET = 40.0  # [A]

# --- calibration switches (flip after live verification, then restart HA) ---
WRITE_ECHO_EXPECTED = True         # False if the device sends no FC10 echo (runbook step W)
WRITE_MODE_SETPOINT_TOO = False    # True if the app also writes reg 19/20/21 on setpoint change (step S)
BINARY_ACTIVE_WHEN_NONZERO = True  # [A] polarity of the binary sensors
REG_DATA_LITTLE_ENDIAN = False     # [A] register data byte order; flip if the probe
# shows byte-swapped values (e.g. 0xF300 instead of 0x00F3 for 24.3 degC).


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


SENSORS: tuple[RegDef, ...] = (
    RegDef("inlet_water_temp", REG_INLET_WATER_TEMP, "InletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement"),
    RegDef("outlet_water_temp", REG_OUTLET_WATER_TEMP, "OutletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement"),
    RegDef("ambient_temp", REG_AMBIENT_TEMP, "AT", TEMP_SCALE, True,
           "temperature", "°C", "measurement"),
    RegDef("fault_code", REG_FAULT1, "Fault1", icon="mdi:alert-circle"),
    RegDef("firmware", REG_FIRMWARE_FIRST, "Firmware", icon="mdi:information",
           diagnostic=True),  # special-case: joins regs 48..51
)

BINARIES: tuple[RegDef, ...] = (
    RegDef("compressor", REG_COMPRESSOR, "Compressor", is_binary=True, icon="mdi:engine"),
    RegDef("circulate_pump", REG_CIRCULATE_PUMP, "Circulate pump", is_binary=True, icon="mdi:pump"),
    RegDef("four_way_valve", REG_FOUR_WAY_VALVE, "4-way valve", is_binary=True,
           icon="mdi:swap-horizontal"),
)

# Reverse map used by the probe logs ("reg N = ... # Label"). The [P]/[V] tag
# records the confidence level of each label.
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
    REG_INLET_WATER_TEMP: "InletWaterTemp [P]",
    REG_OUTLET_WATER_TEMP: "OutletWaterTemp [P]",
    REG_AMBIENT_TEMP: "AT [P]",
    REG_FAULT1: "Fault1 [P]",
    REG_FIRMWARE_FIRST: "Firmware [V]",  # marks the 48..51 group
}

# Binary labels are only meaningful once their register is pinned down.
for _reg, _label in (
    (REG_COMPRESSOR, "Compressor [V]"),
    (REG_CIRCULATE_PUMP, "Circulate pump [V]"),
    (REG_FOUR_WAY_VALVE, "4-way valve [V]"),
):
    if _reg is not None:
        KNOWN_LABELS[_reg] = _label
del _reg, _label