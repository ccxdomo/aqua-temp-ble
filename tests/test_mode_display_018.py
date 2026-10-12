#!/usr/bin/env python3
"""Spec 018 tests — the power-dominant display + the census level.

Part 1 (coordinator, synthetic-package import — the 015 pattern):
the contract 018-A truth table for the pushed climate's hvac_mode —
the display follows POWER alone on this heat-only unit:
    (2011=1, 2019=0)  -> HEAT  (the owner's production case: our
                                power-only write moves 2011, never
                                2019 — research 018 §3, M-1)
    (2011=1, 2019=4)  -> HEAT  (the 08:31 app-flow on-state)
    (2011=1, absent)  -> HEAT
    (2011=1, unmapped)-> HEAT + ONE INFO per value per session
    (2011=0, any)     -> OFF   (the OFF branch never reads mode)
    gate closed / no capture -> None (honest unknown, unchanged)
The mode mirror NEVER gates the display; the old WARNING "Unknown
pushed Mode value …" is GONE (zero records at ANY level — AC-2); an
unmapped NONZERO value logs exactly ONE INFO per session per value
(AC-3) — a research signal, never a display defect.

Part 2 (aqua_client, standalone import — the census_009 pattern):
the contract 018-B census level — a CRC-valid block frame received
while a request is pending produces NO "block push observed" record
at INFO or above; the DEBUG record still exists; the timeout
message's census summary still counts the frame (the request
counters are unchanged, AC-4).

Red-first on b81b604: every Part-1 truth-table assertion fails
(the decode renders None + the old WARNING for (1, 0), (1, None)
and every unmapped value), and the Part-2 level assertion fails
(the block census line logs at INFO).

Run standalone:

    python3 tests/test_mode_display_018.py

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

import aqua_client  # noqa: E402  (standalone, the census_009 pattern)
import const  # noqa: E402
import modbus  # noqa: E402

DOMAIN = "aqua_temp_ble"
PKG = "aqua_temp_ble_mode_display_018"  # synthetic package (distinct from siblings)

# The proven production frame for block 1001-1045 (research 008 §2.2,
# CRC 0x3A56) — used as-is for the census-level test.
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)


# ===========================================================================
# The synthetic-package stubs (the 015 pattern — the coordinator imports
# homeassistant.* at module load; the tests must bind the SAME objects).
# ===========================================================================


class HVACMode:
    OFF = "off"
    COOL = "cool"
    HEAT = "heat"
    AUTO = "auto"


class HVACAction:  # pragma: no cover - not exercised here
    OFF = "off"
    HEATING = "heating"
    COOLING = "cooling"
    IDLE = "idle"
    DRYING = "drying"
    FAN = "fan"


class ClimateEntityFeature(int):  # pragma: no cover - not exercised here
    TARGET_TEMPERATURE = 1
    TURN_ON = 2
    TURN_OFF = 4


class ClimateEntity:  # pragma: no cover - not exercised here
    def __init__(self) -> None:
        pass


class SensorEntity:  # pragma: no cover - placeholder
    pass


class HomeAssistantError(Exception):  # pragma: no cover - placeholder
    pass


class UpdateFailed(Exception):
    pass


class HomeAssistant:  # pragma: no cover - placeholder
    pass


class UnitOfTemperature:  # pragma: no cover - placeholder
    CELSIUS = "°C"
    FAHRENHEIT = "°F"


class ConfigEntry:  # pragma: no cover - typing only
    pass


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


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — the union imported by
    coordinator.py (the 015 test pattern)."""
    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_climate.HVACAction = HVACAction
    ha_climate.ClimateEntity = ClimateEntity
    ha_climate.ClimateEntityFeature = ClimateEntityFeature
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.UnitOfTemperature = UnitOfTemperature
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

    ha.components = ha_components
    ha_components.climate = ha_climate
    ha_helpers.update_coordinator = ha_uc

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.climate": ha_climate,
        "homeassistant.const": ha_const,
        "homeassistant.core": ha_core,
        "homeassistant.config_entries": ha_cfg,
        "homeassistant.exceptions": ha_exc,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
    }.items():
        sys.modules[name] = module


def _import_pkg_module(name: str):
    """Import a component module under the synthetic package."""
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

# --- the test vectors (the 015 suite's owner-production captures) ----------
# The 2001 block in the two toggle states plus the 018 production case:
# a fresh post-write frame reports 2011 = 1 with 2019 STILL 0 — the
# normal on-state after OUR power-only writes (research 018 §1/§3).
CONTROL_BLOCK_ON_MODE0 = {
    2010: 2001, 2012: 1, 2017: 13, 2018: 168,
    2011: 1, 2019: 0,
}
CONTROL_BLOCK_ON_MODE4 = {  # the 08:31 app-flow on-state [P owner]
    2010: 2001, 2012: 1, 2017: 13, 2018: 168,
    2011: 1, 2019: 4,
}
CONTROL_BLOCK_OFF = {
    2010: 2001, 2012: 1, 2017: 13, 2018: 168,
    2011: 0, 2019: 0,
}
# The calibrated T-window (the 0.10.0 frames.txt capture).
T_WINDOW_RAWS = {2046: 32765, 2047: 130, 2048: 125, 2049: 125, 2050: 125, 2051: 135}


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


def _snapshot(caps):
    return coordinator_mod.AquaTempData.from_pushes(caps)


class _LogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


class _capture_logs:
    """Attach a capture handler to coordinator_mod._LOGGER."""

    def __enter__(self) -> _LogCap:
        self.cap = _LogCap()
        self.logger = coordinator_mod._LOGGER
        self.old_level = self.logger.level
        self.logger.addHandler(self.cap)
        self.logger.setLevel(logging.DEBUG)
        return self.cap

    def __exit__(self, *_exc) -> None:
        self.logger.removeHandler(self.cap)
        self.logger.setLevel(self.old_level)


def _clear_mode_ledgers() -> None:
    """Start a fresh session: clear the once-per-session mode ledgers
    (the 018 name AND the pre-018 name it replaced, so the red run is
    deterministic against the shipped code)."""
    for name in ("_logged_unmapped_push_modes", "_logged_unknown_push_modes"):
        ledger = getattr(coordinator_mod, name, None)
        if ledger is not None:
            ledger.clear()


# ===========================================================================
# Part 1 — the contract 018-A truth table (AC-1/AC-2/AC-3)
# ===========================================================================


def test_truth_table_power_on_mode_zero_renders_heat() -> None:
    """AC-1/AC-2 — THE owner's production case: a fresh post-write 2001
    frame carries 2011 = 1 with 2019 = 0 (our power-only write moves
    the power mirror and never the mode mirror — research 018 §3,
    M-1) -> HEAT, with ZERO records containing 'Unknown pushed Mode'
    at ANY level (the old WARNING fired on every healthy control
    session — noise, now gone)."""
    _clear_mode_ledgers()
    caps = {
        2001: _capture(2001, CONTROL_BLOCK_ON_MODE0),
        2046: _capture(2046, T_WINDOW_RAWS),
    }
    with _capture_logs() as cap:
        data = _snapshot(caps)
    assert data.power is True
    assert data.mode_value == 0
    assert data.hvac_mode == HVACMode.HEAT
    assert not [m for _lvl, m in cap.records if "Unknown pushed Mode" in m], cap.records


def test_truth_table_power_on_mode_four_renders_heat() -> None:
    """AC-1: the 08:31 app-flow on-state (2011=1, 2019=4) still renders
    HEAT — the 015-era verdict survives the amendment unchanged."""
    _clear_mode_ledgers()
    caps = {
        2001: _capture(2001, CONTROL_BLOCK_ON_MODE4),
        2046: _capture(2046, T_WINDOW_RAWS),
    }
    with _capture_logs() as cap:
        data = _snapshot(caps)
    assert data.power is True
    assert data.mode_value == 4
    assert data.hvac_mode == HVACMode.HEAT
    assert not [m for _lvl, m in cap.records if "Unknown pushed Mode" in m], cap.records


def test_truth_table_power_on_mode_absent_renders_heat() -> None:
    """AC-2: 2019 ABSENT from the capture (the defensive case) — power
    True renders HEAT with ZERO 'Unknown pushed Mode' records at ANY
    level (the shipped code warned 'Unknown pushed Mode value None')."""
    _clear_mode_ledgers()
    raws = {k: v for k, v in CONTROL_BLOCK_ON_MODE0.items() if k != 2019}
    caps = {2001: _capture(2001, raws), 2046: _capture(2046, T_WINDOW_RAWS)}
    with _capture_logs() as cap:
        data = _snapshot(caps)
    assert data.power is True
    assert data.mode_value is None
    assert data.hvac_mode == HVACMode.HEAT
    assert not [m for _lvl, m in cap.records if "Unknown pushed Mode" in m], cap.records


def test_truth_table_unmapped_mode_renders_heat_info_once() -> None:
    """AC-3: an unmapped NONZERO mode value while power is on never
    degrades the display — (1, 7) renders HEAT + exactly ONE INFO
    'pushed mode value 7 (reg 2019) unmapped …' per session per value;
    a second decode of 7 logs NOTHING; (1, 8) and (1, 99) each log
    their own single INFO (a distinct value is a distinct ledger
    entry). The INFO is the research/calibration signal (contract
    018-A §3) — no WARNING anywhere."""
    _clear_mode_ledgers()
    raws7 = dict(CONTROL_BLOCK_ON_MODE0)
    raws7[2019] = 7
    caps7 = {2001: _capture(2001, raws7), 2046: _capture(2046, T_WINDOW_RAWS)}
    with _capture_logs() as cap:
        data1 = _snapshot(caps7)
        data2 = _snapshot(caps7)  # second decode of 7 — must log nothing
        raws8 = dict(CONTROL_BLOCK_ON_MODE0)
        raws8[2019] = 8
        data8 = _snapshot({2001: _capture(2001, raws8)})
        raws99 = dict(CONTROL_BLOCK_ON_MODE0)
        raws99[2019] = 99
        data99 = _snapshot({2001: _capture(2001, raws99)})
    assert data1.hvac_mode == HVACMode.HEAT and data1.mode_value == 7
    assert data2.hvac_mode == HVACMode.HEAT
    assert data8.hvac_mode == HVACMode.HEAT and data8.mode_value == 8
    assert data99.hvac_mode == HVACMode.HEAT and data99.mode_value == 99
    infos = [
        m for lvl, m in cap.records
        if lvl == logging.INFO and "pushed mode value" in m and "unmapped" in m
    ]
    # 7 once (the second decode silent), 8 once, 99 once
    assert len(infos) == 3, cap.records
    assert sum("pushed mode value 7 (reg 2019) unmapped" in m for m in infos) == 1
    assert sum("pushed mode value 8 (reg 2019) unmapped" in m for m in infos) == 1
    assert sum("pushed mode value 99 (reg 2019) unmapped" in m for m in infos) == 1
    assert not [m for _lvl, m in cap.records if "Unknown pushed Mode" in m], cap.records
    assert not [m for lvl, m in cap.records
                if lvl >= logging.WARNING and "mode" in m.lower()], cap.records


def test_truth_table_power_off_never_reads_mode() -> None:
    """AC-1: power False renders OFF for ANY mode value — the OFF
    branch never reads the mode mirror: (0, 0), (0, 4) and (0, 7) all
    decode OFF with ZERO mode records at any level (no INFO, no
    WARNING — the unmapped 7 stays silent when the unit is off)."""
    _clear_mode_ledgers()
    for mode_raw in (0, 4, 7):
        raws = dict(CONTROL_BLOCK_OFF)
        raws[2019] = mode_raw
        caps = {2001: _capture(2001, raws), 2046: _capture(2046, T_WINDOW_RAWS)}
        with _capture_logs() as cap:
            data = _snapshot(caps)
        assert data.power is False
        assert data.hvac_mode == HVACMode.OFF, (mode_raw, data.hvac_mode)
        mode_records = [
            m for _lvl, m in cap.records
            if "pushed mode value" in m or "Unknown pushed Mode" in m
        ]
        assert not mode_records, (mode_raw, mode_records)


def test_missing_capture_renders_unknown() -> None:
    """AC-1 (honesty, unchanged from 015): NO 2001 capture in the store
    -> hvac_mode None (the tile's honest 'unknown' — the power mirror is
    absent, nothing is invented). Spec 022: the gate-closed variant is
    dead with the gate — the decode is UNCONDITIONAL; with a capture
    the display follows power (the unconditional decode)."""
    no_capture = _snapshot({2046: _capture(2046, T_WINDOW_RAWS)})
    assert no_capture.power is None
    assert no_capture.hvac_mode is None
    # spec 022: the same capture now decodes the display (the gate is gone)
    caps = {
        2001: _capture(2001, CONTROL_BLOCK_ON_MODE0),
        2046: _capture(2046, T_WINDOW_RAWS),
    }
    decoded = _snapshot(caps)
    assert decoded.power is True
    assert decoded.hvac_mode == HVACMode.HEAT


# ===========================================================================
# Part 2 — the contract 018-B census level (AC-4)
# ===========================================================================


class StepClock:
    """Scripted `_now` seam: returns .value; tests advance it explicitly."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeBleakClient:
    """Minimal stand-in for a connected bleak client."""

    is_connected = True

    async def write_gatt_char(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass


class ClockfedClient(aqua_client.AquaTempBleClient):
    """`_write_frame` records the frame, then feeds every scripted
    notification, advancing the fake clock by `gap` before each feed
    (the census_009 pattern — deterministic t+ offsets)."""

    def __init__(self, clock: StepClock, feed: list[bytes] | None = None,
                 gap: float = 0.4) -> None:
        super().__init__("AA:BB:CC:DD:EE:FF")
        self.clock = clock
        self.feed = list(feed or [])
        self.gap = gap
        self.sent: list[bytes] = []

    async def _write_frame(self, frame: bytes, *, force_response: bool = False) -> None:
        self.sent.append(bytes(frame))
        for chunk in self.feed:
            self.clock.value += self.gap
            self._on_notify(bytearray(chunk))


class _FakeClockPatch:
    """Patch aqua_client._now with a StepClock (contract 007 §4)."""

    def __init__(self, clock: StepClock) -> None:
        self.clock = clock
        self._real = aqua_client._now

    def __enter__(self) -> StepClock:
        aqua_client._now = self.clock
        return self.clock

    def __exit__(self, *_exc) -> None:
        aqua_client._now = self._real


class _capture_client_logs:
    """Attach a capture handler to aqua_client._LOGGER (DEBUG+)."""

    def __enter__(self) -> _LogCap:
        self.cap = _LogCap()
        self.logger = aqua_client._LOGGER
        self.old_level = self.logger.level
        self.logger.addHandler(self.cap)
        self.logger.setLevel(logging.DEBUG)
        return self.cap

    def __exit__(self, *_exc) -> None:
        self.logger.removeHandler(self.cap)
        self.logger.setLevel(self.old_level)


def test_block_census_logs_at_debug_only() -> None:
    """AC-4 (contract 018-B §1/§4): a CRC-valid block frame received
    while a request is pending produces NO 'block push observed'
    record at INFO or above (the per-frame firehose that drove HA's
    rate limiter); the DEBUG record still exists (nothing dropped
    silently); the timeout message's census summary still counts the
    frame — the request counters are unchanged."""
    push = bytes.fromhex(FRAME_BLOCK_1001)
    assert modbus.validate_crc(push) and len(push) == 99
    clock = StepClock(100.0)

    async def run() -> None:
        client = ClockfedClient(clock, feed=[push], gap=0.4)
        client._client = FakeBleakClient()  # mark as connected
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            with _capture_client_logs() as cap:
                try:
                    await client.read_registers(7, 45)
                except aqua_client.AquaTempTimeoutError as err:
                    msg = str(err)
                else:
                    raise AssertionError("push-only feed did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        # NO block-census record at INFO or above (contract 018-B)
        loud = [
            m for lvl, m in cap.records
            if lvl >= logging.INFO and "block push observed" in m
        ]
        assert not loud, loud
        # the DEBUG record still exists (the census survives at DEBUG)
        assert any(
            lvl == logging.DEBUG
            and "block push observed: start=1001 qty=45 len=99 crc=OK" in m
            and "; t+" in m
            for lvl, m in cap.records
        ), cap.records
        # the timeout census summary still counts the frame (counters
        # unchanged — the timeout message is computed from them)
        assert "received 1 frame(s) / 99 bytes" in msg, msg
        assert "blocks seen: 1001x1" in msg, msg
        assert "none for start 7" in msg, msg

    with _FakeClockPatch(clock):
        asyncio.run(run())


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_truth_table_power_on_mode_zero_renders_heat,
        test_truth_table_power_on_mode_four_renders_heat,
        test_truth_table_power_on_mode_absent_renders_heat,
        test_truth_table_unmapped_mode_renders_heat_info_once,
        test_truth_table_power_off_never_reads_mode,
        test_missing_capture_renders_unknown,
        test_block_census_logs_at_debug_only,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-018 mode-display tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())