#!/usr/bin/env python3
"""Spec 015 control-data tests — the pushed control homes, the
read-only climate and the confirmation gate (FR-5..FR-11, AC-2/AC-3).

Part A (registers-level, standalone import): the pushed-control
constants transcribed from the owner's 2026-10-12 diff-revert evidence
(research 015 §8.1 — power home 2011, mode home 2019, no setpoint home:
PUSH_CONTROL_BLOCK_START, PUSHED_CONTROL,
PUSHED_MODE_VALUE_TO_NAME, PUSH_CONTROL_CANDIDATES) and the
check_registry_integrity extension (FR-11d): keys limited to
power/mode/setpoint, addresses inside 1001-2090, no duplicates within
the control plane, no cross-plane collisions with the pushed
temperatures — every rule with a negative test. The BASE mode enum
stays untouched (the classic write path's reverse map can never emit
the pushed enum's 4).

Part B (coordinator, synthetic-package import — the 013 pattern):
from_pushes decodes the control keys (power flag, mode value,
hvac_mode follows POWER alone — contract 018-A §2 amending contract
015-A §5: power False renders OFF, power True renders HEAT on this
heat-only unit, and an unmapped NONZERO mode value logs ONE INFO per
session per value; the setpoint path when a home is ever pinned) —
UNCONDITIONALLY since spec 022 (the confirmation gates are gone; the
gate-closed withholding world is inverted); the push cycle decodes the
control keys with no options set; the FR-10 diagnostics raws
(push_control_raws) ride the snapshot unconditionally — the owner
re-runs the diff without a terminal.

Part C (climate platform, amended by spec 022): the push-mode climate
is created UNCONDITIONALLY with the FULL surface — [OFF, HEAT]
hvac_modes, TURN_ON/TURN_OFF + TARGET_TEMPERATURE features, every
write routed through the confirmation cycles; hvac_action OFF when
power is False, unknown when on (the compressor is uncalibrated — no
optimistic activity); bounds from the setpoint guard's own constants;
the current temperature reads the pushed INLET mirror (the FR-12 pin,
spec 022 AC-16). The classic entity keeps today's exact surface
(byte-identical, US-6).

Part D (sensor platform): the push_stream diagnostic exposes the
control-candidate raws with their evidence tags (FR-10).

Test vectors: the owner's production diff captures (2026-10-12: the
2001 block in the two toggle states — power register 0/1, mode
register 0/4, quickstart 015 §1), the pinned frames.txt baseline
(2011=0, 2019=4 — the unit's state was unknown at that capture) and
the 1046-ladder setpoint candidates.

Spec 022 note: the confirmation-gate surfaces this file used to pin
(the gate property, the gate-closed decode, the read-only climate)
are INVERTED — the gates are gone; the living behaviours (the decode
paths, the vectors, the diagnostics, the classic surface) keep their
assertions.

Run standalone:

    python3 tests/test_control_015.py

No real Bluetooth and no real Home Assistant required (fake modules
are installed when the real ones are absent).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO_DIR = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_DIR / "custom_components" / "aqua_temp_ble"
sys.path.insert(0, str(COMPONENT_DIR))

try:
    import bleak  # noqa: F401
except ImportError:
    bleak = types.ModuleType("bleak")

    class _FakeBleakClient:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScanner:  # pragma: no cover - placeholder
        pass

    bleak.BleakClient = _FakeBleakClient
    bleak.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = bleak

import registers  # noqa: E402  (part A: standalone, the test_registers pattern)

DOMAIN = "aqua_temp_ble"
ADDRESS = "08:06:A6:C0:94:1C"
PKG = "aqua_temp_ble_control_015"  # synthetic package (distinct from siblings)

# --- the test vectors -------------------------------------------------------
# The owner's production diff captures (2026-10-12, research 015 §8.1):
# the 2001 block in the two toggle states. Power home 2011 flips
# 0 -> 1 -> 0 with the app's power slide + revert; mode home 2019 flips
# 0 -> 4 -> 0 across the same toggle. 2010 is the block's self-reference
# (0x07D1), 2012 the static flag, 2017/2018 the two unknowns (13, 168).
CONTROL_BLOCK_OFF = {
    2010: 2001, 2012: 1, 2017: 13, 2018: 168,
    2011: 0, 2019: 0,
}
CONTROL_BLOCK_ON = {
    2010: 2001, 2012: 1, 2017: 13, 2018: 168,
    2011: 1, 2019: 4,
}
# The frames.txt baseline capture (research 015 §1/§2.3) — the unit's
# power state at capture time was unknown (U-8): 2011 reads 0, the mode
# candidate 2019 reads 4. A decoded block frame carries ALL 45
# registers (zeros included) — hence the explicit 2011: 0.
CONTROL_BLOCK_BASELINE = {
    2010: 2001, 2012: 1, 2017: 13, 2018: 168, 2011: 0, 2019: 4,
}
# The calibrated T-window (the 0.10.0 frames.txt capture).
T_WINDOW_RAWS = {2046: 32765, 2047: 130, 2048: 125, 2049: 125, 2050: 125, 2051: 135}
# The 1046-ladder setpoint candidates (research 015 §2.2).
LADDER_RAWS = {
    1046: 290, 1047: 5, 1048: 5, 1049: 150, 1050: 400, 1051: 270,
    1052: 5, 1053: 5, 1054: 100, 1055: 350, 1056: -70, 1057: 130,
    1058: 40, 1059: 8, 1060: 3, 1068: 270,
}
# The classic base-block vector (the 013 coordinator test's BASE_REGS).
BASE_REGS = {
    7: 0, 8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
    22: 60, 23: 400, 24: 60, 25: 400, 26: 243, 27: -9990, 28: -125,
    47: 0, 48: 2, 49: 2, 50: 1, 51: 2040,
}


def _capture(start: int, raws: dict[int, int], count: int = 1,
             first_seen: float = 990.0, last_seen: float = 1000.0) -> SimpleNamespace:
    """A duck-typed PushCapture for the store (the 013 test pattern)."""
    return SimpleNamespace(
        start=start,
        qty=45,
        length=99,
        registers=dict(raws),
        first_seen=first_seen,
        last_seen=last_seen,
        count=count,
    )


# ===========================================================================
# Part A — registers constants + integrity (spec 015 FR-5/FR-11d, AC-5)
# ===========================================================================


def test_pushed_control_constants_transcribed() -> None:
    """AC-5: the control constants of data-model 015 §7, transcribed
    verbatim — the single source of truth (a corrected diff outcome is
    a one-line edit in PUSHED_CONTROL)."""
    assert registers.PUSH_CONTROL_BLOCK_START == 2001
    assert registers.PUSHED_CONTROL == {
        "power": 2011,     # [P owner diff-revert 2026-10-12]
        "mode": 2019,      # [P owner diff-revert 2026-10-12]
        # Spec 019 T1 (research 019 §1, [P owner diff 2026-10-12]): the
        # setpoint home is pinned — the panel's +1.0 °C moved it 290→300.
        "setpoint": 1046,
    }
    # the pushed mode VALUE mapping — [A] on this heat-only unit (D-3);
    # the observed enum: 0 (off-state) / 4 (on-state).
    assert registers.PUSHED_MODE_VALUE_TO_NAME == {4: "heat"}
    # FR-10 + spec 022 FR-8: the diagnostics candidates (the homes +
    # the old candidates + the ladder five + the 2017/2018 fault-hunt
    # wildcards), each with its evidence tag. Spec 023 FR-5 adds the
    # pinned fault word 2074 (the owner's E03 diff-revert).
    assert set(registers.PUSH_CONTROL_CANDIDATES) == {
        2011, 2019, 2012, 2017, 2018, 1046, 1049, 1051, 1055, 1068, 2074,
    }
    # the control plane window (FR-11d): the two pushed series.
    assert registers.PUSH_CONTROL_FIRST == 1001
    assert registers.PUSH_CONTROL_LAST == 2090


def test_base_mode_mapping_untouched() -> None:
    """AC-6 / the write guard: the BASE mode enum stays exactly
    {0: cool, 1: heat, 2: auto} — the pushed 4->heat mapping lives ONLY
    in PUSHED_MODE_VALUE_TO_NAME, so the classic write path's reverse
    map (HVAC_TO_MODE_VALUE) can never emit the pushed enum's values."""
    assert registers.MODE_VALUE_TO_NAME == {0: "cool", 1: "heat", 2: "auto"}


def test_control_labels_annotated() -> None:
    """FR-10/AC-5: the probe output annotates the 2001-block homes and
    the ladder candidates (KNOWN_LABELS — the probe-log surface); the
    base labels are untouched."""
    assert "Power" in registers.KNOWN_LABELS[2011]
    assert "Mode" in registers.KNOWN_LABELS[2019]
    for reg in (2010, 2012, 2017, 2018):
        assert reg in registers.KNOWN_LABELS
    for reg in (1046, 1049, 1051, 1055, 1068):
        assert "setpoint" in registers.KNOWN_LABELS[reg].lower()
    # the base labels unchanged (the 003 surface)
    assert registers.KNOWN_LABELS[8] == "Power [P]"
    assert registers.KNOWN_LABELS[13] == "Mode [P]"


def test_pushed_block_start() -> None:
    """The block-lookup helper for the FR-10 diagnostics raws: the four
    observed pushed blocks (1001/1046/2001/2046, 45 registers each)."""
    assert registers.pushed_block_start(1001) == 1001
    assert registers.pushed_block_start(1046) == 1046
    assert registers.pushed_block_start(1068) == 1046
    assert registers.pushed_block_start(2001) == 2001
    assert registers.pushed_block_start(2011) == 2001
    assert registers.pushed_block_start(2019) == 2001
    assert registers.pushed_block_start(2046) == 2046
    assert registers.pushed_block_start(2047) == 2046
    # outside every observed block (the never-pushed 1091 included)
    assert registers.pushed_block_start(7) is None
    assert registers.pushed_block_start(2000) is None
    assert registers.pushed_block_start(2091) is None
    assert registers.pushed_block_start(1091) is None


def test_control_integrity_shipped_map_passes() -> None:
    """FR-11d: the shipped registries + the shipped control map pass at
    import (the module bottom already ran this — pin it explicitly)."""
    registers.check_registry_integrity()  # must not raise


def test_control_integrity_unknown_key_raises() -> None:
    """FR-11d: the control keys are power/mode/setpoint ONLY."""
    bad = {"power": 2011, "mode": 2019, "setpoint": None, "fan_speed": 2030}
    try:
        registers.check_registry_integrity(pushed_control=bad)
    except ValueError as err:
        assert "fan_speed" in str(err), err
    else:
        raise AssertionError("unknown control key did not raise")


def test_control_integrity_outside_window_raises() -> None:
    """FR-11d: a control home outside the pushed plane 1001-2090 raises
    (base registers and beyond-plane addresses are illegal homes)."""
    for bad in (8, 1000, 2091, 3000):
        ctrl = dict(registers.PUSHED_CONTROL)
        ctrl["power"] = bad
        try:
            registers.check_registry_integrity(pushed_control=ctrl)
        except ValueError as err:
            assert str(bad) in str(err), err
        else:
            raise AssertionError(f"control address {bad} outside 1001-2090 did not raise")


def test_control_integrity_duplicate_within_raises() -> None:
    """FR-11d: two control keys pinning the same register raise."""
    ctrl = {"power": 2019, "mode": 2019, "setpoint": None}
    try:
        registers.check_registry_integrity(pushed_control=ctrl)
    except ValueError as err:
        assert "2019" in str(err), err
    else:
        raise AssertionError("duplicate control address did not raise")


def test_control_integrity_duplicate_vs_pushed_temps_raises() -> None:
    """FR-11d: no cross-plane duplicates — a control home colliding
    with a pushed TEMPERATURE register (2047 is the calibrated inlet)
    raises."""
    ctrl = dict(registers.PUSHED_CONTROL)
    ctrl["power"] = 2047
    try:
        registers.check_registry_integrity(pushed_control=ctrl)
    except ValueError as err:
        assert "2047" in str(err), err
    else:
        raise AssertionError("cross-plane collision did not raise")


def test_control_integrity_setpoint_none_is_legal() -> None:
    """FR-11d: a None home (setpoint today) is legal — no setpoint home
    is identified, so no target may ever be displayed."""
    ctrl = {"power": None, "mode": None, "setpoint": None}
    registers.check_registry_integrity(pushed_control=ctrl)  # must not raise


# ===========================================================================
# Parts B/C/D — synthetic-package imports (the 013 pattern, one stub set)
# ===========================================================================

# The stub classes are defined HERE and attached to the stub modules, so
# every imported platform binds the SAME objects (HVACMode strings,
# ClimateEntityFeature ints, and the HomeAssistantError the read-only
# refusal raises must be catchable regardless of import order).


class HVACMode:
    OFF = "off"
    COOL = "cool"
    HEAT = "heat"
    AUTO = "auto"


class HVACAction:
    OFF = "off"
    HEATING = "heating"
    COOLING = "cooling"
    IDLE = "idle"
    DRYING = "drying"
    FAN = "fan"


class ClimateEntityFeature(int):
    TARGET_TEMPERATURE = 1
    TURN_ON = 2
    TURN_OFF = 4


class ClimateEntity:
    def __init__(self) -> None:  # the real base takes no args
        pass


class SensorEntity:  # pragma: no cover - attribute container only
    pass


class HomeAssistantError(Exception):
    pass


class UpdateFailed(Exception):
    pass


class HomeAssistant:  # pragma: no cover - placeholder
    pass


class UnitOfTemperature:
    CELSIUS = "°C"
    FAHRENHEIT = "°F"


class ConfigEntry:  # pragma: no cover - typing only
    pass


class AddEntitiesCallback:  # pragma: no cover - typing only
    pass


class EntityCategory:
    DIAGNOSTIC = "diagnostic"


class TemperatureConverter:
    @staticmethod
    def convert(value, _from_unit, _to_unit):  # identity — tests are Celsius
        return value


class DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None,
                 config_entry=None, always_update=False, **_kwargs):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.always_update = always_update

    def __class_getitem__(cls, _item):  # DataUpdateCoordinator[X]
        return cls


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, _item):  # CoordinatorEntity[X]
        return cls


class DeviceInfo:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — the union imported by
    coordinator.py + entity.py + climate.py + sensor.py (the 013 test
    pattern extended with the climate surface)."""
    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_climate.HVACAction = HVACAction
    ha_climate.ClimateEntity = ClimateEntity
    ha_climate.ClimateEntityFeature = ClimateEntityFeature
    ha_sensor = types.ModuleType("homeassistant.components.sensor")
    ha_sensor.SensorEntity = SensorEntity
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.UnitOfTemperature = UnitOfTemperature
    ha_const.ATTR_TEMPERATURE = "temperature"
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = HomeAssistant
    ha_cfg = types.ModuleType("homeassistant.config_entries")
    ha_cfg.ConfigEntry = ConfigEntry
    ha_exc = types.ModuleType("homeassistant.exceptions")
    ha_exc.HomeAssistantError = HomeAssistantError
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_uc = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_uc.DataUpdateCoordinator = DataUpdateCoordinator
    ha_uc.UpdateFailed = UpdateFailed
    ha_uc.CoordinatorEntity = CoordinatorEntity
    ha_dr = types.ModuleType("homeassistant.helpers.device_registry")
    ha_dr.DeviceInfo = DeviceInfo
    ha_ent = types.ModuleType("homeassistant.helpers.entity")
    ha_ent.EntityCategory = EntityCategory
    ha_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_ep.AddEntitiesCallback = AddEntitiesCallback
    ha_util = types.ModuleType("homeassistant.util")
    ha_uc_conv = types.ModuleType("homeassistant.util.unit_conversion")
    ha_uc_conv.TemperatureConverter = TemperatureConverter

    ha.components = ha_components
    ha_components.climate = ha_climate
    ha_components.sensor = ha_sensor
    ha.util = ha_util
    ha_helpers.update_coordinator = ha_uc
    ha_helpers.device_registry = ha_dr
    ha_helpers.entity = ha_ent
    ha_helpers.entity_platform = ha_ep

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.climate": ha_climate,
        "homeassistant.components.sensor": ha_sensor,
        "homeassistant.const": ha_const,
        "homeassistant.core": ha_core,
        "homeassistant.config_entries": ha_cfg,
        "homeassistant.exceptions": ha_exc,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
        "homeassistant.helpers.device_registry": ha_dr,
        "homeassistant.helpers.entity": ha_ent,
        "homeassistant.helpers.entity_platform": ha_ep,
        "homeassistant.util": ha_util,
        "homeassistant.util.unit_conversion": ha_uc_conv,
    }.items():
        sys.modules[name] = module


def _import_pkg_module(name: str):
    """Import a component module under the synthetic package — the stub
    set is installed exactly once so every platform binds the SAME
    stub objects (the exception/enum identity the tests rely on)."""
    modname = f"{PKG}.{name}"
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, COMPONENT_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


_install_stubs()
if PKG not in sys.modules:
    _pkg = types.ModuleType(PKG)
    _pkg.__path__ = [str(COMPONENT_DIR)]  # supports `from . import x`
    sys.modules[PKG] = _pkg
coordinator_mod = _import_pkg_module("coordinator")
sensor_mod = _import_pkg_module("sensor")
climate_mod = _import_pkg_module("climate")
aqua_client_mod = sys.modules[f"{PKG}.aqua_client"]


class _FakeClient:
    """Duck-typed client: connected, a scripted store, write recorder."""

    is_connected = True

    def __init__(self) -> None:
        self.read_calls: list[tuple] = []
        self.write_calls: list[tuple] = []
        self.control_writes: list[tuple] = []
        self.setpoint_writes: list[int] = []
        self._pushed: dict = {}

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls.append((start, count))
        raise aqua_client_mod.AquaTempTimeoutError("no matching response within 8.0s")

    async def write_registers(self, address, values):
        self.write_calls.append((address, list(values)))
        return None

    async def write_control(self, address, value):
        self.control_writes.append((address, value))

    async def write_setpoint(self, value_raw):
        self.setpoint_writes.append(value_raw)

    async def harvest_pushes(self, wanted=None, timeout=None):
        return {}

    @property
    def pushed_blocks(self) -> dict:
        return dict(self._pushed)

    async def connect(self, **_kwargs):  # pragma: no cover - not exercised
        return None

    async def disconnect(self):  # pragma: no cover - not exercised
        return None


def _make_coordinator(client=None, data=None, options=None):
    entry = SimpleNamespace(
        data={"address": ADDRESS, **(data or {})},
        options=dict(options or {}),
    )
    return coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=client or _FakeClient()
    )


def _snapshot(caps=None):
    return coordinator_mod.AquaTempData.from_pushes(caps or {})


class _LogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


def _capture_logs():
    cap = _LogCap()
    logger = coordinator_mod._LOGGER
    old_level = logger.level
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    return cap, logger, old_level


# ===========================================================================
# Part B — the coordinator: from_pushes control decode + the gate
# ===========================================================================


def test_unconditional_control_decode_from_pushes() -> None:
    """Spec 022 inversion of the FR-11c gate-closed world: from_pushes
decodes the control keys UNCONDITIONALLY — no gate arguments, no
options; the registers dict carries the T-block raws (the entity
plane's temperature source); the FR-10 diagnostics raws ride along
(they are diagnostics, never entity states)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_ON),
        1046: _capture(1046, LADDER_RAWS),
    }
    data = _snapshot(caps)
    assert data.power is True
    assert data.mode_value == 4
    assert data.hvac_mode == HVACMode.HEAT
    assert data.target_temp == 29.0
    assert data.registers == dict(T_WINDOW_RAWS)  # T-block raws
    assert data.mode == "push"
    assert data.push_counts == {2046: 1, 2001: 1, 1046: 1}
    # FR-10: the diagnostics raws are visible unconditionally
    assert data.push_control_raws[2011] == 1
    assert data.push_control_raws[2019] == 4
    assert data.push_control_raws[2012] == 1
    assert data.push_control_raws[1046] == 290
    assert data.push_control_raws[1068] == 270


def test_from_pushes_single_argument_form() -> None:
    """Spec 022: from_pushes takes the captures ALONE — the gate
    arguments are gone; the decode is unconditional (the old
    two-argument call form no longer exists)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    data = coordinator_mod.AquaTempData.from_pushes(caps)
    assert data.power is True
    assert data.hvac_mode == HVACMode.HEAT


def test_gate_open_off_vector_decodes() -> None:
    """FR-7/AC-3: the owner's OFF capture (07:47/08:37) — power home
    2011 reads 0 -> power False -> hvac_mode OFF (power wins, the
    from_regs pattern); no setpoint home -> target None."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_OFF),
    }
    data = _snapshot(caps)
    assert data.power is False
    assert data.mode_value == 0
    assert data.hvac_mode == HVACMode.OFF
    assert data.target_temp is None


def test_gate_open_on_vector_decodes() -> None:
    """FR-7/AC-3: the owner's ON capture (08:31) — power 2011 reads 1 ->
    power True; mode 2019 reads 4 -> heat ([A] mapping, D-3)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    data = _snapshot(caps)
    assert data.power is True
    assert data.mode_value == 4
    assert data.hvac_mode == HVACMode.HEAT
    assert data.target_temp is None


def test_baseline_capture_decodes_power_off() -> None:
    """The pinned frames.txt baseline (unit state unknown at capture,
    U-8): 2011 reads 0 -> power False -> hvac_mode OFF even though the
    mode candidate 2019 reads 4 — power wins (contract 015-A §5)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_BASELINE),
    }
    data = _snapshot(caps)
    assert data.power is False
    assert data.mode_value == 4
    assert data.hvac_mode == HVACMode.OFF


def test_unmapped_push_mode_heat_and_info_once() -> None:
    """Spec 018 (contract 018-A §2/§3, amending the 015 FR-7 decode):
    an unmapped NONZERO mode VALUE no longer degrades the display —
    power True renders HEAT (the mode mirror never gates the display
    on this heat-only unit), and the value logs exactly ONE INFO per
    session per value (the research/calibration signal; a second
    decode logs nothing). The old 'Unknown pushed Mode' WARNING is
    GONE at ANY level (contract 018-A §3)."""
    coordinator_mod._logged_unmapped_push_modes.clear()
    raws = dict(CONTROL_BLOCK_ON)
    raws[2019] = 7  # not in PUSHED_MODE_VALUE_TO_NAME
    caps = {2001: _capture(2001, raws), 2046: _capture(2046, T_WINDOW_RAWS)}
    cap, logger, old_level = _capture_logs()
    try:
        data1 = _snapshot(caps)
        data2 = _snapshot(caps)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    assert data1.hvac_mode == HVACMode.HEAT and data2.hvac_mode == HVACMode.HEAT
    infos = [
        m for lvl, m in cap.records
        if lvl == logging.INFO and "pushed mode value" in m and "unmapped" in m
    ]
    assert len(infos) == 1, cap.records  # once per session per value
    assert "2019" in infos[0] and "7" in infos[0], infos[0]
    assert not [m for lvl, m in cap.records if "Unknown pushed Mode" in m], cap.records


def test_missing_control_capture_stays_none() -> None:
    """Gate open but no 2001 block in the store => the control keys
    decode None (no optimistic state, spec 001)."""
    caps = {2046: _capture(2046, T_WINDOW_RAWS)}
    data = _snapshot(caps)
    assert data.power is None
    assert data.mode_value is None
    assert data.hvac_mode is None
    assert data.push_control_raws[2011] is None  # absent, not zero


def test_setpoint_decode_path_when_pinned() -> None:
    """FR-7: the setpoint path — with a home pinned (a one-line edit),
    the target decodes /10 plausibility-guarded through the shared
    decoder; a sentinel decodes None; an implausible raw decodes None."""
    pkg_regs = sys.modules[f"{PKG}.registers"]
    original = dict(pkg_regs.PUSHED_CONTROL)
    try:
        pkg_regs.PUSHED_CONTROL = dict(original)
        pkg_regs.PUSHED_CONTROL["setpoint"] = 2026
        raws = dict(CONTROL_BLOCK_ON)
        raws[2026] = 300  # 30.0 degC
        caps = {2001: _capture(2001, raws)}
        data = _snapshot(caps)
        assert data.target_temp == 30.0
        # sentinel -> None (sensor absent is never a temperature)
        sent = dict(CONTROL_BLOCK_ON)
        sent[2026] = 32765
        data2 = _snapshot({2001: _capture(2001, sent)})
        assert data2.target_temp is None
        # implausible raw -> None (the plausibility window)
        impl = dict(CONTROL_BLOCK_ON)
        impl[2026] = 5000
        data3 = _snapshot({2001: _capture(2001, impl)})
        assert data3.target_temp is None
    finally:
        pkg_regs.PUSHED_CONTROL = original


def test_push_cycle_decodes_control_keys() -> None:
    """Spec 022 inversion of the FR-6 gate-threading world: the push
    cycle decodes the control keys with NO options set (active by
    default); the registers dict stays T-block-only (the control
    values ride the typed fields)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS, count=2),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    client = _FakeClient()
    client._pushed = dict(caps)
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30},
    )
    real_now = coordinator_mod._now
    coordinator_mod._now = lambda: 1000.0
    try:
        data = asyncio.run(coord._async_update_data())
    finally:
        coordinator_mod._now = real_now
    assert data.mode == "push"
    assert data.power is True
    assert data.hvac_mode == HVACMode.HEAT
    assert 2011 not in data.registers  # the T-block only
    assert data.registers[2047] == 130
    assert data.push_control_raws[2011] == 1
    # the trigger fired exactly once (the 014 cycle, unchanged)
    assert client.read_calls == [(7, 45)], client.read_calls


def test_climate_current_temperature_is_inlet_022() -> None:
    """Spec 022 FR-12 (AC-16): the climate's current temperature is
    the INLET water temperature (push register 2047 through the shared
    decoder) — a canned 2047 raw decodes it; a sentinel or absent 2047
    decodes None; a canned ambient (2050) value NEVER appears as
    current_temperature. Replaces the dead gate-property test (the
    property is gone with the gates)."""
    raws = dict(T_WINDOW_RAWS)
    raws[2047] = 145
    caps = {
        2046: _capture(2046, raws),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    snap = _snapshot(caps)
    entity, _coord, _client = _push_entity(snap)
    assert entity.current_temperature == 14.5  # 2047 = 145 raw
    # the ambient is in the snapshot too — it must NEVER feed the climate
    raws_ambient = dict(raws)
    raws_ambient[2050] = 999  # a distinct ambient raw (99.9 degC)
    snap_ambient = _snapshot({2046: _capture(2046, raws_ambient)})
    entity2, _coord2, _client2 = _push_entity(snap_ambient)
    assert entity2.current_temperature == 14.5  # still the INLET, not 99.9
    # the sentinel inlet: 2047 = 0x7FFD -> None (sensor absent)
    raws_sent = dict(raws)
    raws_sent[2047] = 32765
    snap_sent = _snapshot({2046: _capture(2046, raws_sent)})
    entity3, _coord3, _client3 = _push_entity(snap_sent)
    assert entity3.current_temperature is None
    # the absent inlet: no 2046 capture -> the registers dict is empty
    snap_absent = _snapshot({2001: _capture(2001, CONTROL_BLOCK_ON)})
    entity4, _coord4, _client4 = _push_entity(snap_absent)
    assert entity4.current_temperature is None


# ===========================================================================
# Part C — the climate platform: the gate + the read-only surface
# ===========================================================================


def _make_platform(data: dict, options: dict, snapshot) -> tuple:
    entry = SimpleNamespace(
        entry_id="test_entry",
        title="Aqua Temp test",
        data={"address": ADDRESS, **data},
        options=dict(options),
    )
    client = _FakeClient()
    coordinator = coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=client
    )
    coordinator.data = snapshot
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})
    return hass, entry, coordinator, client


def _added_entities(hass, entry) -> list:
    added: list = []

    def _collect(entities) -> None:
        added.extend(entities)

    asyncio.run(climate_mod.async_setup_entry(hass, entry, _collect))
    return added


def _push_entity(snapshot, options=None):
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    snap = snapshot if snapshot is not None else _snapshot(caps)
    hass, entry, coordinator, client = _make_platform(
        {"mode": "push"}, options or {}, snap
    )
    added = _added_entities(hass, entry)
    assert len(added) == 1, [type(e).__name__ for e in added]
    return added[0], coordinator, client


def test_climate_push_created_unconditionally() -> None:
    """Spec 022 FR-1/FR-3 (the inversion of the 015 closed-gate world):
    the push-mode climate is created with NO options set — active by
    default (the gate-closed absence test is dead with the gate)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    snap = _snapshot(caps)
    hass, entry, _coord, _client = _make_platform({"mode": "push"}, {}, snap)
    added = _added_entities(hass, entry)
    assert len(added) == 1, [type(e).__name__ for e in added]


def test_climate_push_full_surface() -> None:
    """Spec 022 (the inversion of the 015 read-only surface): the
    push-mode climate advertises the FULL surface unconditionally —
    [OFF, HEAT] hvac_modes, TURN_ON/TURN_OFF + TARGET_TEMPERATURE
    features; hvac_mode from power (heat-only rule); hvac_action
    unknown when on (the compressor is uncalibrated — no optimistic
    activity); bounds from the setpoint guard's own constants; the
    target decodes the 1046 mirror when the block is present (the
    default snapshot has no 1046 capture -> None)."""
    entity, _coord, _client = _push_entity(None)
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert entity.supported_features == (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TARGET_TEMPERATURE
    )
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.hvac_action is None
    assert entity.target_temperature is None
    # Spec 019 FR-8 (the 015 suite's scripted-store review, T11): the
    # push-mode bounds read the setpoint guard's own constants
    # (10.0/40.0 — one source of truth with ALLOWED_WRITE_SETPOINT).
    assert entity.min_temp == 10.0
    assert entity.max_temp == 40.0


def test_climate_push_off_state() -> None:
    """FR-8: power False => hvac_mode OFF and hvac_action OFF."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_OFF),
    }
    snap = _snapshot(caps)
    entity, _coord, _client = _push_entity(snap)
    assert entity.hvac_mode == HVACMode.OFF
    assert entity.hvac_action == HVACAction.OFF


def test_climate_push_writes_route_to_cycles() -> None:
    """Spec 022 (the inversion of the 015 read-only refusal world): the
    push entity's writes route to the confirmation cycles — turn_on/
    turn_off/set_hvac_mode(HEAT) reach the client's write_control rail,
    set_temperature reaches the write_setpoint rail; the heat-only rule
    still refuses COOL/AUTO; the confirmation windows are silent (no
    fresh mirrors scripted) so the cycles end unconfirmed — honest, and
    the WRITES happened."""
    entity, _coord, client = _push_entity(None)

    async def _drive() -> None:
        await entity.async_turn_on()
        await entity.async_turn_off()
        await entity.async_set_hvac_mode(HVACMode.HEAT)
        await entity.async_set_temperature(temperature=20.0)

    old_window = coordinator_mod.const.PROBE_MIRROR_TIMEOUT
    coordinator_mod.const.PROBE_MIRROR_TIMEOUT = 0.05
    try:
        asyncio.run(_drive())
    finally:
        coordinator_mod.const.PROBE_MIRROR_TIMEOUT = old_window
    assert client.control_writes == [(1011, 1), (1011, 0), (1011, 1)], (
        client.control_writes
    )
    assert client.setpoint_writes == [200], client.setpoint_writes
    assert client.write_calls == []  # never the classic funnel
    assert client.read_calls == [(7, 45)] * 4, client.read_calls  # one trigger each
    # the heat-only rule stands (the vendor app locks the mode; the
    # mode write would clobber an unpinned pair register)
    try:
        asyncio.run(entity.async_set_hvac_mode(HVACMode.COOL))
    except HomeAssistantError as err:
        assert "heat-only" in str(err), str(err)
    else:
        raise AssertionError("COOL did not raise on the heat-only unit")


def test_climate_push_current_temperature_reads_inlet() -> None:
    """The current temperature reads the pushed inlet mirror (contract
    013 §4.1 — one quantity, one source): 2047 = 130 raw -> 13.0 with
    NO options set (spec 022: active by default; the unconfirmed
    withholding variant is dead with the gate — the FR-12 pin test
    above covers the sentinel/absent/ambient cases)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS),
        2001: _capture(2001, CONTROL_BLOCK_ON),
    }
    snap = _snapshot(caps)
    entity, _coord, _client = _push_entity(snap)
    assert entity.current_temperature == 13.0  # 2047 = 130 raw


def test_climate_classic_surface_unchanged() -> None:
    """US-6: the classic entity keeps today's exact surface — created
    unconditionally (no gate), TARGET_TEMPERATURE feature, the four
    hvac_modes, values from the base snapshot."""
    snap = coordinator_mod.AquaTempData.from_regs(dict(BASE_REGS))
    hass, entry, _coord, _client = _make_platform({}, {}, snap)  # no mode => classic
    added = _added_entities(hass, entry)
    assert len(added) == 1
    entity = added[0]
    assert entity._attr_supported_features == ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity._attr_hvac_modes == [
        HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO,
    ]
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.target_temperature == 32.5
    assert entity.current_temperature == 24.3
    assert entity.hvac_action is None  # power on, activity unknown


# ===========================================================================
# Part D — the sensor platform: the FR-10 diagnostics raws
# ===========================================================================


def _sensor_added(hass, entry) -> list:
    added: list = []

    def _collect(entities) -> None:
        added.extend(entities)

    asyncio.run(sensor_mod.async_setup_entry(hass, entry, _collect))
    return added


def test_push_stream_exposes_control_candidates() -> None:
    """FR-10: the push_stream diagnostic exposes the control-candidate
    raws with their evidence tags — the owner re-runs the diff without
    a terminal (the raws ride the snapshot gate-independently)."""
    caps = {
        2046: _capture(2046, T_WINDOW_RAWS, count=2),
        2001: _capture(2001, CONTROL_BLOCK_ON),
        1046: _capture(1046, LADDER_RAWS),
    }
    snap = _snapshot(caps)
    hass, entry, _coord, _client = _make_platform({"mode": "push"}, {}, snap)
    added = _sensor_added(hass, entry)
    stream = [e for e in added if isinstance(e, sensor_mod.AquaTempPushStreamEntity)]
    assert len(stream) == 1, [type(e).__name__ for e in added]
    attrs = stream[0].extra_state_attributes
    cands = attrs["control_candidates"]
    pkg_regs = sys.modules[f"{PKG}.registers"]
    assert cands["2011"] == {"raw": 1, "evidence": pkg_regs.PUSH_CONTROL_CANDIDATES[2011]}
    assert cands["2019"] == {"raw": 4, "evidence": pkg_regs.PUSH_CONTROL_CANDIDATES[2019]}
    assert cands["2012"]["raw"] == 1
    # Spec 022 FR-8 (AC-11): the 2017/2018 fault-hunt wildcards ride the
    # candidate attributes in EVERY push snapshot, with their evidence tags
    assert cands["2017"] == {"raw": 13, "evidence": pkg_regs.PUSH_CONTROL_CANDIDATES[2017]}
    assert cands["2018"] == {"raw": 168, "evidence": pkg_regs.PUSH_CONTROL_CANDIDATES[2018]}
    assert cands["1046"]["raw"] == 290
    assert cands["1049"]["raw"] == 150
    assert cands["1051"]["raw"] == 270
    assert cands["1055"]["raw"] == 350
    assert cands["1068"]["raw"] == 270
    assert "power" in cands["2011"]["evidence"]
    assert "mode" in cands["2019"]["evidence"]
    assert "spec 022 fault hunt" in cands["2017"]["evidence"]
    # the 013 attributes stay (non-regression)
    assert attrs["mode"] == "push"
    assert attrs["per_start_counts"] == {"1046": 1, "2001": 1, "2046": 2}


def test_push_stream_no_candidates_attribute_in_classic() -> None:
    """The diagnostics attribute is push-only (classic snapshots carry
    no push_control_raws)."""
    snap = coordinator_mod.AquaTempData.from_regs(dict(BASE_REGS))
    hass, entry, _coord, _client = _make_platform({}, {}, snap)
    added = _sensor_added(hass, entry)
    # classic mode creates no push_stream at all (the 013 rule)
    assert not [
        e for e in added if isinstance(e, sensor_mod.AquaTempPushStreamEntity)
    ]


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Part A — registers + integrity
        test_pushed_control_constants_transcribed,
        test_base_mode_mapping_untouched,
        test_control_labels_annotated,
        test_pushed_block_start,
        test_control_integrity_shipped_map_passes,
        test_control_integrity_unknown_key_raises,
        test_control_integrity_outside_window_raises,
        test_control_integrity_duplicate_within_raises,
        test_control_integrity_duplicate_vs_pushed_temps_raises,
        test_control_integrity_setpoint_none_is_legal,
        # Part B — coordinator decode (spec 022: unconditional)
        test_unconditional_control_decode_from_pushes,
        test_from_pushes_single_argument_form,
        test_gate_open_off_vector_decodes,
        test_gate_open_on_vector_decodes,
        test_baseline_capture_decodes_power_off,
        test_unmapped_push_mode_heat_and_info_once,
        test_missing_control_capture_stays_none,
        test_setpoint_decode_path_when_pinned,
        test_push_cycle_decodes_control_keys,
        # Part C — the climate surface (spec 022: full + the FR-12 pin)
        test_climate_push_created_unconditionally,
        test_climate_push_full_surface,
        test_climate_push_off_state,
        test_climate_push_writes_route_to_cycles,
        test_climate_push_current_temperature_reads_inlet,
        test_climate_current_temperature_is_inlet_022,
        test_climate_classic_surface_unchanged,
        # Part D — the diagnostics
        test_push_stream_exposes_control_candidates,
        test_push_stream_no_candidates_attribute_in_classic,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as err:
            failures += 1
            print(f"FAIL  {test.__name__}: {err}")
        except Exception as err:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {test.__name__}: unexpected {type(err).__name__}: {err}")
        else:
            print(f"PASS  {test.__name__}")
    print()
    if failures:
        print(f"FAILED: {failures} test(s)")
        return 1
    print(f"PASSED: {len(tests)}/{len(tests)} spec-015 control tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())