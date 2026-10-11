#!/usr/bin/env python3
"""Coordinator probe tests (spec 008 FR-5 / AC-8) — high-block decode.

`_log_raw_probe` must decode the unit's block frame (the shape proven in
research 008 §2.1 — `63 10 start qty bytecount data crc`) into
per-register probe lines — the same format as `_log_base_probe` — and
expose `start`/`qty` in its result dict; a ModbusError falls back to the
raw-hex branch (WARNING + "raw" key, plan 008 §3) so the bytes always
stay visible.

Red-first: on c8d8260 the probe tried the FC03 parse, which never fits a
block frame, and silently fell back to the raw hex — no per-register
lines, no start/qty (the failures this file pins).

Run standalone:

    python3 tests/test_coordinator_probe.py

No real Bluetooth and no real Home Assistant required (minimal fake
homeassistant modules are installed, the config-flow-test pattern).
"""

from __future__ import annotations

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
    # Fake bleak (same pattern as the other transport tests): only the
    # import itself is needed — no I/O happens in this test file.
    _bleak = types.ModuleType("bleak")

    class _FakeBleakClient:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScanner:  # pragma: no cover - placeholder
        pass

    _bleak.BleakClient = _FakeBleakClient
    _bleak.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = _bleak

import modbus  # noqa: E402  (standalone import: frame helpers only)

ADDRESS = "08:06:A6:C0:94:1C"

# The proven production frame for block 1001-1045 (research 008 §2.2,
# CRC 0x3A56) — the probe's answer on this unit.
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)

# Synthetic package name for the component import (the walkthrough-test
# pattern; a distinct name so the fakes never collide with other files).
PKG = "aqua_temp_ble_probe_test"


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — just what coordinator.py
    imports (the pattern of tests/test_config_flow_walkthrough.py)."""

    class HVACMode:  # enough of the real enum for the coordinator
        OFF = "off"
        COOL = "cool"
        HEAT = "heat"
        AUTO = "auto"

    class UnitOfTemperature:
        CELSIUS = "°C"
        FAHRENHEIT = "°F"

    class HomeAssistant:  # pragma: no cover - placeholder
        pass

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        """Fake base: stores what the real constructor stores. Supports
        subscripting — the coordinator subclasses
        DataUpdateCoordinator[AquaTempData] at import time."""

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

    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.UnitOfTemperature = UnitOfTemperature
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = HomeAssistant
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
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
    }.items():
        sys.modules[name] = module


def _import_coordinator():
    """Import coordinator.py as part of a synthetic package."""
    _install_stubs()
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT_DIR)]  # supports `from . import x`
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.coordinator", COMPONENT_DIR / "coordinator.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.coordinator"] = module
    spec.loader.exec_module(module)
    return module


coordinator = _import_coordinator()


class _LogCap(logging.Handler):
    """Capture records emitted on the coordinator module's logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


def _make_coordinator():
    """AquaTempCoordinator over the fake base — only _log_raw_probe runs."""
    entry = SimpleNamespace(data={"address": ADDRESS}, options={})
    return coordinator.AquaTempCoordinator(
        hass=None, entry=entry, client=SimpleNamespace()
    )


def _capture():
    cap = _LogCap()
    logger = coordinator._LOGGER
    old_level = logger.level
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    return cap, logger, old_level


# --------------------------------------------------------------------- tests
def test_probe_decodes_block_1001() -> None:
    """AC-8: a block-1001 response is decoded into per-register probe
    lines and the result dict gains start/qty + the registers."""
    frame = bytes.fromhex(FRAME_BLOCK_1001)
    assert modbus.validate_crc(frame) and len(frame) == 99
    coord = _make_coordinator()
    cap, logger, old_level = _capture()
    try:
        result = coord._log_raw_probe("1001", 1001, frame)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)

    info_msgs = [m for lvl, m in cap.records if lvl >= logging.INFO]
    # the probe header line (unchanged format)
    assert any(
        "block=1001" in m and "start=1001" in m and "len=99" in m and "CRC=OK" in m
        for m in info_msgs
    ), info_msgs
    # per-register probe lines, the shared _log_base_probe format
    line_1021 = [m for m in info_msgs if "reg 1021" in m]
    assert line_1021, f"no per-register line for reg 1021: {info_msgs}"
    assert "1050" in line_1021[0] and "0x041A" in line_1021[0] and "105.0" in (
        line_1021[0]
    ), line_1021[0]
    line_1001 = [m for m in info_msgs if "reg 1001" in m]
    assert line_1001 and "0x0000" in line_1001[0], info_msgs
    # the raw-hex fallback branch must NOT have run
    assert not any(m.strip().startswith("raw:") for m in info_msgs), info_msgs

    out = result[ADDRESS]
    assert out["block"] == "1001"
    assert out["crc"] == "OK"
    assert out["start"] == 1001            # spec 008 FR-5: start/qty exposed
    assert out["qty"] == 45
    assert "raw" not in out
    regs = out["registers"]
    assert regs["1021"]["raw"] == 1050
    assert regs["1021"]["tenths"] == 105.0
    assert regs["1021"]["hex"] == "0x041A"
    assert regs["1001"]["raw"] == 0
    assert len(regs) == 45


def test_probe_falls_back_to_raw_hex_on_modbus_error() -> None:
    """A ModbusError (corrupt CRC here) falls back to the raw-hex branch:
    WARNING + "raw" key — the bytes stay visible (plan 008 §3). start/qty
    are still exposed."""
    frame = bytearray(bytes.fromhex(FRAME_BLOCK_1001))
    frame[10] ^= 0xFF  # corrupt one data byte -> ModbusCRCError
    corrupt = bytes(frame)
    assert not modbus.validate_crc(corrupt)
    coord = _make_coordinator()
    cap, logger, old_level = _capture()
    try:
        result = coord._log_raw_probe("1001", 1001, corrupt)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)

    warn_msgs = [m for lvl, m in cap.records if lvl >= logging.WARNING]
    assert any("could not parse" in m and "1001" in m for m in warn_msgs), warn_msgs
    info_msgs = [m for lvl, m in cap.records if lvl >= logging.INFO]
    assert any(m.strip().startswith("raw:") and corrupt.hex() in m for m in info_msgs), (
        info_msgs
    )

    out = result[ADDRESS]
    assert out["block"] == "1001"
    assert out["crc"] == "FAIL"
    assert out["start"] == 1001
    assert out["qty"] == 45
    assert out["raw"] == corrupt.hex()
    assert "registers" not in out


def main() -> int:
    tests = [
        test_probe_decodes_block_1001,
        test_probe_falls_back_to_raw_hex_on_modbus_error,
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
    print(f"PASSED: {len(tests)}/{len(tests)} coordinator probe tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())