#!/usr/bin/env python3
"""Coordinator query-cycle tests (spec 014 FR-1/FR-2 / AC-1..AC-4,
contract 014 §2/§3/§4/§5).

The 0.9.0 production run (research 014 §0) killed the zero-TX push
cycle: the unit pushes NOTHING spontaneously (0 frames in the idle
window and in the 30 s zero-TX listen) and ANSWERS every request with
a burst of its parameter-dataset block frames (1001/1046/2001/2046).
The push-mode poll cycle therefore becomes TRIGGER + LISTEN (contract
014 §1, amending contract 013 §1's zero-TX invariant): ONE vendor base
read `63030007002d3c54` per cycle — the frame the unit has answered
six times [P] — then the harvest machinery spec 013 already built,
then the unchanged freshness gate and entity mapping.

Pinned by this file:
- AC-1: the trigger read TIMES OUT as expected (the INFO line with the
  census verbatim, contract 014 §2.2) and the store carries the burst's
  T-block => the from_pushes snapshot, exactly ONE read_registers(7, 45)
  call with single-shot kwargs (no re-send, no acknowledged-write
  override — contract 014 §1.1/§2.1), no other write, no harvest;
- AC-2: nothing arrives (empty store, empty harvest) =>
  UpdateFailed("push stream silent after trigger") — the new verdict,
  a strictly stronger statement than 0.9.0's listen-only silence —
  + disconnect (contract 014 §4.2/§4.3), the trigger fired exactly once;
- AC-3: a non-timeout AquaTempError during the trigger is a TRANSPORT
  failure => UpdateFailed("trigger read failed: …") + disconnect, never
  conflated with stream silence (contract 014 §2.3);
- AC-4: an ANSWERED trigger (the revival world, never observed) =>
  the from_merged snapshot: typed fields from the base answer, the
  confirmed T-block raws merged into `registers` (unconfirmed =>
  withheld — the honesty gate unchanged, contract 013 §4.3), mode
  "push" preserved, the revival WARNING logged ONCE per HA session
  (contract 014 §5.1/§7.2);
- the cycle-order guard: the revival branch runs BEFORE the freshness
  failure — an answered trigger with an EMPTY store still succeeds
  (plan 014 §2.1).

Red-first on 9d752a7: no trigger, no from_merged, no revival log —
every test below fails until FR-1/FR-2 land.

Run standalone:

    python3 tests/test_coordinator_query_014.py

No real Bluetooth and no real Home Assistant required (the
test_coordinator_push_013 stub pattern).
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
PKGC = "aqua_temp_ble_coord_query_014"  # synthetic package (distinct)

# The pinned T-window raws — the SECOND independent capture (the
# 2026-10-11 22:41 production log, research 014 §8.1), distinct from
# the 013 suite's first-capture values so the two captures cannot alias.
T_WINDOW_RAWS = {2046: 32765, 2047: 140, 2048: 135, 2049: 135, 2050: 135, 2051: 145}

# The pinned base-block vector (the trigger's answer in the revival
# world — from_regs semantics, contract 013 §4.2; the 013 suite's
# vector, reused so the two suites decode identically).
BASE_REGS = {
    7: 0, 8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
    22: 60, 23: 400, 24: 60, 25: 400, 26: 243, 27: -9990, 28: -125,
    47: 0, 48: 2, 49: 2, 50: 1, 51: 2040,
}

# The census the trigger's AquaTempTimeoutError carries in its message
# (the contract 009 §3 format — the per-cycle burst census, contract
# 014 §2.2/§7.1: the U-7 latency/composition evidence).
TRIGGER_CENSUS = (
    "no matching response within 8.0s (received 16 frame(s) / 1584 "
    "bytes; blocks seen: 1001x12, 1046x4, 2001x5, 2046x3; first "
    "t+0.20s, last t+4.80s; none for start 7)"
)


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
    (the test_coordinator_push_013 pattern)."""

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
HVACMode = sys.modules["homeassistant.components.climate"].HVACMode
# The REAL exception classes from the synthetic package (the 013
# lessons): the fakes must raise instances the coordinator's except
# clauses actually catch.
aqua_client = sys.modules[f"{PKGC}.aqua_client"]
AquaTempTimeoutError = aqua_client.AquaTempTimeoutError
AquaTempConnectionError = aqua_client.AquaTempConnectionError


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


class FakeQueryClient:
    """Duck-typed AquaTempBleClient for the query cycle: a scripted
    trigger outcome (timeout / answered / transport error), a scripted
    pushed-block store and harvest, and a recorder for every call —
    the exactly-one-trigger and no-other-writes gates assert through
    it (contract 014 §1.1/§2.1)."""

    def __init__(self, captures: dict | None = None,
                 harvest_result: dict | None = None,
                 connected: bool = True,
                 trigger_error: Exception | None = None,
                 trigger_regs: dict | None = None) -> None:
        self.is_connected = connected
        self.pushed_blocks = dict(captures or {})
        self.harvest_result = dict(harvest_result or {})
        self.trigger_error = trigger_error  # raised by read_registers
        self.trigger_regs = trigger_regs    # a dict => the trigger ANSWERS
        self.read_calls: list[tuple] = []   # (start, count, kwargs)
        self.harvest_calls: list[tuple] = []
        self.connect_kwargs: list[dict] = []
        self.disconnect_calls = 0
        self.write_calls: list[tuple] = []

    async def read_registers(self, start, count, **kwargs):
        self.read_calls.append((start, count, dict(kwargs)))
        if self.trigger_error is not None:
            raise self.trigger_error
        if self.trigger_regs is not None:
            return dict(self.trigger_regs)
        raise AssertionError("the fake trigger has no scripted outcome")

    async def write_registers(self, start, values):
        # AC-1's "no other writes" gate: the query cycle must never
        # write (contract 014 §1.4 — the trigger is a READ frame).
        self.write_calls.append((start, list(values)))
        return None

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
def test_query_cycle_timeout_trigger_harvests_snapshot() -> None:
    """AC-1 (FR-1, contract 014 §2): the trigger read times out as
    expected — the INFO line carries the census verbatim — the store
    already holds the burst's T-block, and the cycle builds the
    from_pushes snapshot: exactly ONE read_registers(7, 45) call with
    single-shot kwargs, no other write, no harvest needed, NO
    UpdateFailed."""
    client = FakeQueryClient(
        captures={2046: _capture(2046, T_WINDOW_RAWS, count=3)},
        trigger_error=AquaTempTimeoutError(TRIGGER_CENSUS),
    )
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30, "push_temp_confirmed": True},
    )
    cap, logger, old_level = _capture_logs()
    try:
        with _ClockPatch(1000.0):
            data = asyncio.run(coord._async_update_data())
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    # exactly ONE trigger per cycle, single-shot (no re-send bounds, no
    # acknowledged-write override — contract 014 §1.1/§2.1)
    assert client.read_calls == [(7, 45, {})], client.read_calls
    assert client.write_calls == [], client.write_calls  # no other writes
    assert client.harvest_calls == [], client.harvest_calls  # burst already landed
    assert data.mode == "push"
    assert data.registers[2047] == 140     # the second capture's raws
    assert data.registers[2046] == 32765   # the T01 sentinel raw stays raw
    assert data.push_confirmed is True
    assert data.push_counts == {2046: 3}
    assert data.push_last_seen == 1000.0
    assert data.push_first_seen == 990.0
    # no pushed home [P/V] for control keys: the listen snapshot is
    # from_pushes, unchanged (contract 013 §4.4)
    assert data.power is None
    assert data.hvac_mode is None
    assert data.target_temp is None
    assert data.fault_code is None
    assert data.firmware is None
    assert data.binaries == {}
    # the INFO expected-timeout line, census verbatim (contract 014 §2.2)
    expected = (
        "push cycle: trigger read timed out as expected "
        f"(census: {TRIGGER_CENSUS})"
    )
    assert (logging.INFO, expected) in cap.records, cap.records


def test_query_cycle_silence_after_trigger_fails_honestly() -> None:
    """AC-2 (FR-1, contract 014 §4.2): the trigger fired (exactly once)
    and the unit did not wake — empty store, empty harvest => the NEW
    verdict `push stream silent after trigger` + disconnect (the link
    recycles, §4.3), never 0.9.0's listen-only silence."""
    client = FakeQueryClient(
        captures={},
        harvest_result={},
        trigger_error=AquaTempTimeoutError(TRIGGER_CENSUS),
    )
    coord = _make_coordinator(
        client, data={"mode": "push"}, options={"poll_interval": 30}
    )
    with _ClockPatch(1000.0):
        try:
            asyncio.run(coord._async_update_data())
        except UpdateFailed as err:
            assert str(err) == "push stream silent after trigger", str(err)
        else:
            raise AssertionError("silent unit did not raise UpdateFailed")
    assert client.read_calls == [(7, 45, {})], client.read_calls  # trigger exactly once
    assert len(client.harvest_calls) == 1, client.harvest_calls
    assert client.harvest_calls[0] == ({2046}, 30.0), client.harvest_calls
    assert client.disconnect_calls == 1  # the link recycles (contract 014 §4.3)


def test_query_cycle_trigger_transport_failure() -> None:
    """AC-3 (FR-1, contract 014 §2.3): a non-timeout AquaTempError
    during the trigger is a TRANSPORT failure — UpdateFailed("trigger
    read failed: …") + disconnect — never conflated with stream silence
    (the stream literal must NOT appear) and the harvest never runs."""
    client = FakeQueryClient(
        trigger_error=AquaTempConnectionError(
            "GATT write failed: BleakError: Not connected"
        ),
    )
    coord = _make_coordinator(
        client, data={"mode": "push"}, options={"poll_interval": 30}
    )
    with _ClockPatch(1000.0):
        try:
            asyncio.run(coord._async_update_data())
        except UpdateFailed as err:
            assert str(err).startswith("trigger read failed:"), str(err)
            assert "push stream silent" not in str(err)  # never conflated
        else:
            raise AssertionError("transport failure did not raise UpdateFailed")
    assert client.read_calls == [(7, 45, {})], client.read_calls
    assert client.disconnect_calls == 1
    assert client.harvest_calls == [], client.harvest_calls  # no listen on a dead link


def test_query_cycle_answered_trigger_merges_base_data() -> None:
    """AC-4 (FR-2, contract 014 §5): the trigger read COMPLETES (the
    revival world, never observed) => the from_merged snapshot: typed
    fields from the base answer (from_regs semantics — base wins,
    contract 013 §4.2), the confirmed T-block raws merged into
    `registers` (disjoint windows, collision-free [P]), mode "push"
    preserved, and the revival WARNING logged ONCE per HA session —
    the second cycle does not re-log."""
    coordinator._logged_trigger_answered.clear()  # module state isolation
    client = FakeQueryClient(
        captures={2046: _capture(2046, T_WINDOW_RAWS, count=2)},
        trigger_regs=dict(BASE_REGS),
    )
    coord = _make_coordinator(
        client,
        data={"mode": "push"},
        options={"poll_interval": 30, "push_temp_confirmed": True},
    )
    cap, logger, old_level = _capture_logs()
    try:
        with _ClockPatch(1000.0):
            data1 = asyncio.run(coord._async_update_data())
            data2 = asyncio.run(coord._async_update_data())
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    # typed fields from the base answer (field-by-field, the base vector)
    assert data1.power is True
    assert data1.boost is False
    assert data1.mode_value == 1
    assert data1.hvac_mode == HVACMode.HEAT  # reg 13 = 1 -> "heat"
    assert data1.target_temp == 32.5
    assert data1.min_target == 6.0
    assert data1.max_target == 40.0
    assert data1.inlet_temp == 24.3
    assert data1.outlet_temp is None      # sentinel -9990 -> unknown
    assert data1.ambient_temp == -12.5
    assert data1.fault_code == 0
    assert data1.firmware == "2.2.1.2040"
    assert data1.binaries == {}
    # the disjoint-window merge: base regs + confirmed T-block raws [P]
    assert data1.registers == {**BASE_REGS, **T_WINDOW_RAWS}, data1.registers
    assert data1.mode == "push"          # the mode stays (contract 014 §5.3)
    assert data1.push_confirmed is True
    assert data1.push_counts == {2046: 2}
    assert data1.push_first_seen == 990.0
    assert data1.push_last_seen == 1000.0
    # the revival WARNING, once per session (contract 014 §5.1/§7.2)
    revival = [m for lvl, m in cap.records if "trigger read answered" in m]
    assert len(revival) == 1, cap.records
    assert logging.WARNING in [
        lvl for lvl, m in cap.records if "trigger read answered" in m
    ], cap.records
    # the second cycle does not re-log and stays merged
    assert data2.registers == {**BASE_REGS, **T_WINDOW_RAWS}
    assert data2.mode == "push"


def test_query_cycle_answered_trigger_unconfirmed_withholds_raws() -> None:
    """AC-4 (FR-2, contract 013 §4.3 unchanged): an UNCONFIRMED entry
    still merges, but the pushed raws are WITHHELD from `registers` —
    the honesty gate applies to pushed raws only; the base values are
    base-pinned registers, not unconfirmed pushes."""
    coordinator._logged_trigger_answered.clear()  # module state isolation
    client = FakeQueryClient(
        captures={2046: _capture(2046, T_WINDOW_RAWS)},
        trigger_regs=dict(BASE_REGS),
    )
    coord = _make_coordinator(
        client, data={"mode": "push"}, options={"poll_interval": 30}
    )
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    assert data.registers == dict(BASE_REGS), data.registers  # the gate
    assert 2047 not in data.registers
    assert data.push_confirmed is False
    assert data.push_counts == {2046: 1}   # the diagnostic still counts
    assert data.power is True              # base values unaffected by the gate
    assert data.mode == "push"


def test_query_cycle_revival_precedes_freshness_failure() -> None:
    """The cycle-order guard (plan 014 §2.1): the revival branch runs
    BEFORE the freshness failure — an ANSWERED trigger with an EMPTY
    store still succeeds (the base answer IS the data; the classical
    world must not wait 30 s for pushes that may never come —
    contract 014 §5.2)."""
    coordinator._logged_trigger_answered.clear()  # module state isolation
    client = FakeQueryClient(trigger_regs=dict(BASE_REGS))  # empty store
    coord = _make_coordinator(
        client, data={"mode": "push"}, options={"poll_interval": 30}
    )
    with _ClockPatch(1000.0):
        data = asyncio.run(coord._async_update_data())
    assert data.mode == "push"
    assert data.power is True
    assert data.registers == dict(BASE_REGS)
    assert data.push_counts == {}          # no pushes — and no failure
    assert data.push_last_seen is None
    assert client.harvest_calls == [], client.harvest_calls  # no 30 s wait (§5.2)


def main() -> int:
    tests = [
        test_query_cycle_timeout_trigger_harvests_snapshot,
        test_query_cycle_silence_after_trigger_fails_honestly,
        test_query_cycle_trigger_transport_failure,
        test_query_cycle_answered_trigger_merges_base_data,
        test_query_cycle_answered_trigger_unconfirmed_withholds_raws,
        test_query_cycle_revival_precedes_freshness_failure,
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
    print(f"PASSED: {len(tests)}/{len(tests)} coordinator query-cycle tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())