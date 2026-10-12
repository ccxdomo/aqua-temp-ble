#!/usr/bin/env python3
"""Coordinator push-mode tests (spec 013 FR-4/FR-6 / AC-4, contract 013
§1/§4/§6 — re-pinned by spec 014 FR-3, contract 014 §1/§4).

The coordinator's mode branch, re-pinned to the 014 query cycle: a
"push" entry sends exactly ONE base read per cycle — the TRIGGER
(contract 014 §1.1, amending contract 013 §1's zero-TX poll
invariant) — then listens with the harvest and applies the unchanged
freshness rule (contract 013 §6.1): a store entry is used only while
its last-seen age stays inside max(2 x poll interval, 60 s); stale =>
UpdateFailed("push stream silent after trigger") (the 014 literal,
contract 014 §4.2) and the link is recycled on the next cycle (U-3's
defensive stance — the long-run push cadence is unproven). On this
unit the trigger read always times out (research 014 §0.2 — the store
carries the burst), which the fake models by default.

Pinned by this file:
- a fresh store => a snapshot with exactly ONE read_registers(7, 45)
  call per cycle — the trigger (contract 014 §1.1 re-pin), values from
  the T-block raws (only when confirmed — the honesty gate, contract
  013 §4.3);
- a stale store => the re-harvest rung (zero-TX listen), then
  UpdateFailed("push stream silent after trigger") + disconnect
  (never a request beyond the single trigger — contract 013 §6.2 as
  amended by contract 014 §4.1);
- the re-harvest recovering a fresh T-block => the cycle succeeds;
- classic mode: the single-read cycle byte-identical (US-6);
- a base-plane capture in push mode => ONE WARNING anomaly line, the
  data otherwise ignored (contract 013 §4.2).

Red-first on 1ac276d: no mode branch, no from_pushes, no freshness
gate — every test below failed until FR-4 landed. Spec 014 re-pins the
two AC-4 assertions above (contract 014 §4, a cited deviation).

Run standalone:

    python3 tests/test_coordinator_push_013.py

No real Bluetooth and no real Home Assistant required (the
test_coordinator_probe stub pattern).
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
    _bleak = types.ModuleType("bleak")

    class _FakeBleakClient:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScanner:  # pragma: no cover - placeholder
        pass

    _bleak.BleakClient = _FakeBleakClient
    _bleak.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = _bleak

ADDRESS = "08:06:A6:C0:94:1C"
PKGC = "aqua_temp_ble_coord_push_013"  # synthetic package (distinct)

# The pinned T-window raws (research 013 §2.4, the second 2046 push).
T_WINDOW_RAWS = {2046: 32765, 2047: 145, 2048: 140, 2049: 140, 2050: 135, 2051: 150}

BASE_REGS = {
    7: 0, 8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
    22: 60, 23: 400, 24: 60, 25: 400, 26: 243, 27: -9990, 28: -125,
    47: 0, 48: 2, 49: 2, 50: 1, 51: 2040,
}


def _capture(start: int, raws: dict[int, int], count: int = 1,
             first_seen: float = 990.0, last_seen: float = 1000.0) -> SimpleNamespace:
    """A duck-typed PushCapture for the store."""
    return SimpleNamespace(
        start=start,
        qty=45,
        length=99,
        registers=dict(raws),
        first_seen=first_seen,
        last_seen=last_seen,
        count=count,
    )


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — what coordinator.py imports
    (the test_coordinator_probe pattern)."""

    class HVACMode:
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
    _install_stubs()
    pkg = types.ModuleType(PKGC)
    pkg.__path__ = [str(COMPONENT_DIR)]
    sys.modules[PKGC] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKGC}.coordinator", COMPONENT_DIR / "coordinator.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKGC}.coordinator"] = module
    spec.loader.exec_module(module)
    return module


coordinator = _import_coordinator()
UpdateFailed = sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed
# The REAL exception classes from the synthetic package (the 013
# lessons): the fake must raise the AquaTempTimeoutError the
# coordinator's except clause actually catches (contract 014 §2.2).
aqua_client = sys.modules[f"{PKGC}.aqua_client"]


class _ClockPatch:
    """Patch coordinator._now (the contract 007 §4 seam — the ONLY clock
    the freshness gate reads)."""

    def __init__(self, value: float) -> None:
        self.value = value
        self._real = coordinator._now

    def __enter__(self) -> float:
        coordinator._now = lambda: self.value
        return self.value

    def __exit__(self, *_exc) -> None:
        coordinator._now = self._real


class FakePushClient:
    """Duck-typed AquaTempBleClient for the push-mode cycle: connected,
    a scripted pushed-block store, a scripted (instant) harvest, and a
    recorder for every read/connect/harvest call — the ONE-trigger and
    zero-write gates assert through it (the spec 014 re-pin)."""

    def __init__(self, captures: dict | None = None,
                 harvest_result: dict | None = None,
                 connected: bool = True,
                 read_result: dict | None = None) -> None:
        self.is_connected = connected
        self.pushed_blocks = dict(captures or {})
        self.harvest_result = dict(harvest_result or {})
        # Spec 014 (contract 014 §2.2): the cycle's TRIGGER read — on
        # this unit it always times out (the expected world; the store
        # fills during the window). read_result scripts an ANSWERED
        # trigger (the classic vector's world).
        self.read_result = read_result
        self.read_calls: list[tuple] = []
        self.harvest_calls: list[tuple] = []
        self.connect_kwargs: list[dict] = []
        self.disconnect_calls = 0

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls.append((start, count))
        if self.read_result is None:
            # The production push-mode world (contract 014 §2.2): the
            # trigger read times out with the census in the message.
            raise aqua_client.AquaTempTimeoutError(
                "no matching response within 8.0s (received 0 frame(s) / "
                "0 bytes; blocks seen: none; none for start 7)"
            )
        return dict(self.read_result)

    async def connect(self, **kwargs):
        self.connect_kwargs.append(dict(kwargs))
        self.is_connected = True
        return None

    async def disconnect(self):
        self.disconnect_calls += 1
        self.is_connected = False
        return None

    async def harvest_pushes(self, wanted_starts=None, timeout=None):
        self.harvest_calls.append((wanted_starts, timeout))
        return dict(self.harvest_result)


def _make_coordinator(client, data=None, options=None):
    entry = SimpleNamespace(
        data={"address": ADDRESS, **(data or {})},
        options=dict(options or {}),
    )
    return coordinator.AquaTempCoordinator(hass=None, entry=entry, client=client)


class _LogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


def _capture_logs():
    cap = _LogCap()
    logger = coordinator._LOGGER
    old_level = logger.level
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    return cap, logger, old_level


# --------------------------------------------------------------------- tests
def test_push_cycle_single_trigger_builds_snapshot() -> None:
    """AC-4: a fresh store => a snapshot built from the
    pushes with exactly ONE read_registers(7, 45) call per cycle — the
    TRIGGER (contract 014 §1.1 re-pin of the 013 zero-read vector) —
    and NO harvest."""
    client = FakePushClient(captures={2046: _capture(2046, T_WINDOW_RAWS, count=2)})
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30},
    )
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    # Spec 014 re-pin (contract 014 §1.1/§4): the cycle now TRIGGERS —
    # exactly ONE read_registers(7, 45) per cycle, single-shot.
    assert client.read_calls == [(7, 45)], client.read_calls
    assert client.harvest_calls == [], client.harvest_calls  # fresh: no harvest
    assert data.mode == "push"
    assert data.registers[2047] == 145
    assert data.registers[2046] == 32765
    assert data.push_counts == {2046: 2}
    assert data.push_last_seen == 1000.0
    assert data.push_first_seen == 990.0
    # no 2001 capture in the store: the control keys decode None (the
    # absent-block honesty rule; spec 022: the decode is unconditional)
    assert data.power is None
    assert data.hvac_mode is None
    assert data.target_temp is None
    assert data.fault_code is None
    assert data.firmware is None


def test_push_cycle_decodes_raws_unconditionally() -> None:
    """AC-4/AC-5 (spec 022, the inversion of the withholding world): an
    entry with NO options still cycles and the raws ALWAYS enter the
    entity plane — the mirrored keys decode live (active by default)."""
    client = FakePushClient(captures={2046: _capture(2046, T_WINDOW_RAWS)})
    coord = _make_coordinator(
        client, data={"mode": "push"}, options={"poll_interval": 30}
    )
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    assert client.read_calls == [(7, 45)]  # the one trigger (contract 014 §1.1)
    assert data.mode == "push"
    assert data.registers == dict(T_WINDOW_RAWS), data.registers  # unconditional
    assert data.push_counts == {2046: 1}  # the diagnostic still counts


def test_push_cycle_stale_store_update_failed() -> None:
    """AC-4: a stale store (age 61 s > the max(2x30, 60) window) with a
    silent re-harvest => UpdateFailed("push stream silent") + the link
    recycled — and NEVER a request fallback (contract 013 §6.2)."""
    stale = _capture(2046, T_WINDOW_RAWS, last_seen=939.0)  # age 61 at t=1000
    client = FakePushClient(captures={2046: stale}, harvest_result={})
    coord = _make_coordinator(
        client, data={"mode": "push"}, options={"poll_interval": 30}
    )
    with _ClockPatch(1000.0):
        try:
            asyncio.run(coord._async_update_data())
        except UpdateFailed as err:
            # Spec 014 re-pin (contract 014 §4.2): the new verdict —
            # silence AFTER the proven trigger, never 0.9.0's
            # listen-only silence.
            assert str(err) == "push stream silent after trigger", str(err)
        else:
            raise AssertionError("stale store did not raise UpdateFailed")
    # the re-harvest rung ran (zero-TX), then the honest failure
    assert len(client.harvest_calls) == 1, client.harvest_calls
    assert client.harvest_calls[0][0] == {2046}
    # Spec 014 re-pin (contract 014 §4.1): one trigger per cycle, never
    # re-sent, never a request fallback beyond the single trigger.
    assert client.read_calls == [(7, 45)], client.read_calls
    assert client.disconnect_calls == 1  # the link is recycled (U-3 guard)


def test_push_cycle_reharvest_recovers() -> None:
    """AC-4: a stale store but the re-harvest returns a fresh T-block =>
    the cycle succeeds from the harvested capture."""
    stale = _capture(2046, T_WINDOW_RAWS, last_seen=900.0)
    fresh = _capture(2046, T_WINDOW_RAWS, count=3, last_seen=1000.0)
    client = FakePushClient(captures={2046: stale}, harvest_result={2046: fresh})
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30},
    )
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    assert len(client.harvest_calls) == 1
    # Spec 014 re-pin (contract 014 §1.1): the trigger fired once.
    assert client.read_calls == [(7, 45)], client.read_calls
    assert data.registers[2047] == 145
    assert data.push_counts == {2046: 3}


def test_push_cycle_connects_bare() -> None:
    """AC-4: a disconnected client connects first (the CCCD subscription
    connect() already performs — contract 013 §1.1's only allowed ATT
    operation). Spec 021: the connect call carries NO pair kwarg — the
    pair_on_connect option was pruned with the pairing ladder (tasks.md
    D3; the verdict att-accepted-silent proved no bond is needed)."""
    client = FakePushClient(
        captures={2046: _capture(2046, T_WINDOW_RAWS)}, connected=False
    )
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30},
    )
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    assert client.connect_kwargs == [{}], client.connect_kwargs
    # Spec 014 re-pin (contract 014 §2.1): the trigger follows connect.
    assert client.read_calls == [(7, 45)], client.read_calls
    assert data.mode == "push"


def test_classic_mode_single_read_cycle_unchanged() -> None:
    """AC-4 / US-6: without the push mode the cycle is byte-identical —
    exactly ONE FC03 base read, the classic snapshot, mode classic."""
    client = FakePushClient(read_result=BASE_REGS)  # an ANSWERED read (classic world)
    coord = _make_coordinator(client, options={"poll_interval": 30})
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    assert client.read_calls == [(7, 45)], client.read_calls
    assert client.harvest_calls == []
    assert data.mode == "classic"
    assert data.inlet_temp == 24.3      # from_regs unchanged
    assert data.outlet_temp is None      # sentinel -9990 -> unknown
    assert data.ambient_temp == -12.5
    assert data.power is True
    assert data.fault_code == 0
    assert data.firmware == "2.2.1.2040"
    assert data.push_counts == {} and data.push_last_seen is None


def test_push_mode_base_capture_is_anomaly_logged_once_and_ignored() -> None:
    """AC-4 / contract 013 §4.2: a base-plane capture (start 7) in the
    store while in push mode is an ANOMALY — exactly ONE WARNING line
    per session, the pushed snapshot otherwise unaffected (the base
    values do NOT override; the mode re-evaluation is a future spec's
    decision)."""
    coordinator._logged_push_mode_base.clear()  # module state isolation
    captures = {
        2046: _capture(2046, T_WINDOW_RAWS, count=2),
        7: _capture(7, BASE_REGS, count=1),  # the unit pushed a start-7 frame
    }
    client = FakePushClient(captures=captures)
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30},
    )
    cap, logger, old_level = _capture_logs()
    try:
        with _ClockPatch(1000.0):
            data1 = asyncio.run(coord._async_update_data())
            data2 = asyncio.run(coord._async_update_data())
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    anomaly = [m for lvl, m in cap.records if "push mode: base-block" in m]
    assert len(anomaly) == 1, cap.records  # once per session, not per cycle
    assert logging.WARNING in [lvl for lvl, m in cap.records if "base-block" in m]
    # the pushed snapshot is otherwise unaffected: control keys stay None
    assert data1.power is None and data2.power is None
    assert data1.registers[2047] == 145  # the T-block is still the source


def main() -> int:
    tests = [
        test_push_cycle_single_trigger_builds_snapshot,
        test_push_cycle_decodes_raws_unconditionally,
        test_push_cycle_stale_store_update_failed,
        test_push_cycle_reharvest_recovers,
        test_push_cycle_connects_bare,
        test_classic_mode_single_read_cycle_unchanged,
        test_push_mode_base_capture_is_anomaly_logged_once_and_ignored,
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
    print(f"PASSED: {len(tests)}/{len(tests)} coordinator push-mode tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())