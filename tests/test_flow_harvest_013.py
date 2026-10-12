#!/usr/bin/env python3
"""Connection-test harvest-stage tests (spec 013 FR-3 / AC-3, contract
013 §2).

The flow's test gains a stage after the security probe: when the first
read timed out AND the probe returned None (the att-accepted-silent
world — the 0.8.0 production trace), a pure-listening push harvest
(zero TX, contract 013 §1) listens 30 s for the unit's spontaneous
block pushes. A CRC-valid push of the T-family block (start 2046) makes
the test SUCCEED with mode "push" (US-1 — the integration stops
dead-ending no_response against a unit that talks constantly); no
pushes keep the EXISTING no_response key + census message,
byte-identical (US-6 — classic units untouched).

Pinned by this file (the three worlds of AC-3 + the budget):
- world 1: probe None + harvest sees 2046 => test SUCCEEDS, the entry
  records mode "push", the success WARNING summary lists the T-window
  raws, the confirm step is the push variant (spec 022 FR-2/FR-7: the
  name-only form + the live captured values);
- world 2: no pushes at all => the no_response failure,
  byte-identical message + key, stage label unchanged;
- world 3: the probe ANSWERS => the harvest stage never runs (classic
  units untouched, US-6);
- the budget arithmetic (plan 013 §2.2: connect ~5 + 8 + 8 + 30 = 51 s
  inside the 70 s TEST_TIMEOUT) and the push-harvest stage label.

Red-first on 1ac276d: the stage, the mode field and the confirm_push
step do not exist — every test below fails until FR-3 lands.

Run standalone:

    python3 tests/test_flow_harvest_013.py

No real Bluetooth and no real Home Assistant required (the
test_security_probe_011 flow pattern: config_flow imported against
stubbed homeassistant/voluptuous modules under a synthetic package).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
)

try:
    import pytest

    pytestmark = pytest.mark.asyncio
except ImportError:  # pragma: no cover — pytest-less standalone host
    pass

# ---------------------------------------------------------------------------
# Fake bleak (if the library is absent — the transport is never used)
# ---------------------------------------------------------------------------

try:
    import bleak  # noqa: F401
except ImportError:
    bleak = types.ModuleType("bleak")

    class _FakeBleakClientPlaceholder:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScannerPlaceholder:  # pragma: no cover - placeholder
        pass

    bleak.BleakClient = _FakeBleakClientPlaceholder
    bleak.BleakScanner = _FakeBleakScannerPlaceholder
    sys.modules["bleak"] = bleak

sys.path.insert(0, str(COMPONENT_DIR))

ADDRESS = "08:06:A6:C0:94:1C"
FIRST_TIMEOUT = (
    "no matching response within 8.0s (received 0 frame(s) / 0 bytes; "
    "blocks seen: none; none for start 7)"
)

# The T-window raws the scripted harvest captures (the pinned second
# 2046 push, research 013 §2.4: 2047 changed live 140 -> 145 [P]).
T_WINDOW_RAWS = {2046: 32765, 2047: 145, 2048: 140, 2049: 140, 2050: 135, 2051: 150}
CAPTURE_2046 = SimpleNamespace(
    start=2046,
    qty=45,
    length=99,
    registers=dict(T_WINDOW_RAWS),
    first_seen=100.0,
    last_seen=102.0,
    count=2,
)

PKG = "_aqua_flow_013"  # synthetic package name (distinct from siblings)


class AlreadyConfigured(Exception):
    """Stand-in for HA's AbortFlow(already_configured)."""


def _install_stubs() -> None:
    """Stubbed homeassistant/voluptuous modules (the 011 flow pattern)."""
    vol_mod = types.ModuleType("voluptuous")

    class _VolMarker:
        def __init__(self, key, **kwargs):
            self.key = key
            self.kwargs = kwargs

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return isinstance(other, _VolMarker) and self.key == other.key

    class _VolRequired(_VolMarker):
        pass

    class _VolOptional(_VolMarker):
        pass

    class _Vol:
        @staticmethod
        def Schema(schema):
            return schema

        Required = _VolRequired

        @staticmethod
        def In(container):
            return container

        @staticmethod
        def All(*args):
            return args

        @staticmethod
        def Coerce(type_):
            return type_

        @staticmethod
        def Range(**kwargs):
            return kwargs

    for name in ("Schema", "In", "All", "Coerce", "Range"):
        setattr(vol_mod, name, getattr(_Vol, name))
    vol_mod.Required = _VolRequired
    vol_mod.Optional = _VolOptional
    sys.modules["voluptuous"] = vol_mod

    bleak_mod = types.ModuleType("bleak")
    bleak_mod.BleakClient = object
    bleak_mod.BleakScanner = object
    sys.modules["bleak"] = bleak_mod

    class _StubConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()
            cls.handler = kwargs.get("domain")

        def async_show_menu(self, *, step_id, menu_options=None, description_placeholders=None):
            return {
                "type": "menu",
                "step_id": step_id,
                "menu_options": dict(menu_options or {}),
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress(self, *, step_id, progress_action=None, progress_task=None, description_placeholders=None):
            return {
                "type": "progress",
                "step_id": step_id,
                "progress_action": progress_action,
                "progress_task": progress_task,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress_done(self, *, next_step_id):
            return {"type": "progress_done", "step_id": next_step_id, "handler": getattr(self, "handler", None)}

        def async_abort(self, *, reason):
            return {"type": "abort", "reason": reason}

        def async_create_entry(self, *, title=None, data=None, options=None):
            return {"type": "create_entry", "title": title, "data": data, "options": options}

        async def async_set_unique_id(self, unique_id, *, raise_on_progress=True):
            self._stub_unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            if getattr(self, "_stub_unique_id", None) in self.hass.current_ids:
                raise AlreadyConfigured(self._stub_unique_id)

        def _async_current_ids(self, include_ignore=True):
            return list(self.hass.current_ids)

    class _StubOptionsFlow:
        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_create_entry(self, *, title=None, data=None):
            return {"type": "create_entry", "title": title, "data": data}

    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_helpers = types.ModuleType("homeassistant.helpers")

    cfg_entries = types.ModuleType("homeassistant.config_entries")
    cfg_entries.ConfigFlow = _StubConfigFlow
    cfg_entries.ConfigFlowResult = dict
    cfg_entries.OptionsFlow = _StubOptionsFlow

    ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _bt_async_scanner_count(hass, connectable=True):
        return hass.bt_scanner_count

    def _bt_async_discovered_service_info(hass, connectable=True):
        for attr in ("bt_infos", "connectable_infos"):
            if hasattr(hass, attr):
                return list(getattr(hass, attr))
        return []

    def _bt_async_ble_device_from_address(hass, address, connectable=True):
        history = (
            getattr(hass, "connectable_history", None)
            if connectable
            else getattr(hass, "all_history", None)
        )
        if history is not None:
            return history.get(address)
        return None

    ha_bt.async_scanner_count = _bt_async_scanner_count
    ha_bt.async_discovered_service_info = _bt_async_discovered_service_info
    ha_bt.async_ble_device_from_address = _bt_async_ble_device_from_address

    ha_translation = types.ModuleType("homeassistant.helpers.translation")

    async def _tr_async_get_translations(hass, language, category, integrations, *, config_flow=False):
        return dict(hass.translations)

    ha_translation.async_get_translations = _tr_async_get_translations

    ha.config = types.SimpleNamespace(language="en")
    ha.components = ha_components
    ha.helpers = ha_helpers
    ha_components.bluetooth = ha_bt

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.bluetooth": ha_bt,
        "homeassistant.config_entries": cfg_entries,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.translation": ha_translation,
    }.items():
        sys.modules[name] = module


def _import_config_flow():
    _install_stubs()
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT_DIR)]
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.config_flow", COMPONENT_DIR / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.config_flow"] = module
    spec.loader.exec_module(module)
    return module


cf = _import_config_flow()
stub_const = sys.modules[f"{PKG}.const"]
# The REAL timeout class the flow catches (imported into config_flow's
# namespace from the synthetic package) — the scripted outcomes must be
# instances of exactly this class or the except clause will not fire.
AquaTempTimeoutOutcome = sys.modules[f"{PKG}.aqua_client"].AquaTempTimeoutError


class FlowInfo:
    def __init__(self, address, name, rssi, service_uuids=()):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.service_uuids = list(service_uuids)


class FlowHass:
    def __init__(self, infos=None):
        self.config = types.SimpleNamespace(language="en")
        self.bt_scanner_count = 1
        self.bt_infos = list(infos or [])
        self.current_ids = set()
        self.translations = {}

    def async_create_task(self, target):
        return asyncio.ensure_future(target)


class _FlowLogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []
        self.levels: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        self.levels.append(record.levelname)


class _flow_logs:
    """Capture BOTH the flow's and the stubbed aqua_client's loggers."""

    def __enter__(self) -> _FlowLogCap:
        self.cap = _FlowLogCap()
        self.loggers = [cf._LOGGER, sys.modules[f"{PKG}.aqua_client"]._LOGGER]
        self.old_levels = [lg.level for lg in self.loggers]
        for lg in self.loggers:
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self.cap)
        return self.cap

    def __exit__(self, *_exc) -> bool:
        for lg in self.loggers:
            lg.removeHandler(self.cap)
        for lg, level in zip(self.loggers, self.old_levels):
            lg.setLevel(level)
        return False


class FakeHarvestClient:
    """Duck-typed AquaTempBleClient for the flow's test task (the 011
    FakeProbeClient pattern + the spec 013 harvest seam): scriptable
    per-call read outcomes, a scriptable push-harvest result and a
    recorder for every call's kwargs."""

    instances: list["FakeHarvestClient"] = []
    script: list = []
    pair_result = "succeeded"
    pair_error: BaseException | None = None
    harvest_result: dict | None = {}
    harvest_hang = False

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.script = []
        cls.pair_result = "succeeded"
        cls.pair_error = None
        cls.harvest_result = {}
        cls.harvest_hang = False

    def __init__(self, address, name=None, hass=None):
        self.address = address
        self.name = name
        self.hass = hass
        self.reads: list[dict] = []
        self.pair_calls = 0
        self.harvest_calls: list[tuple] = []
        self.disconnected = False
        self.connect_kwargs = None
        type(self).instances.append(self)

    async def connect(self, max_attempts=None, pair=False):
        self.connect_kwargs = {"max_attempts": max_attempts, "pair": pair}
        return None

    async def read_registers(
        self, start, count, *, resend_every=None, resend_max=0, force_response=False
    ):
        self.reads.append(
            {
                "start": start,
                "count": count,
                "resend_every": resend_every,
                "resend_max": resend_max,
                "force_response": force_response,
            }
        )
        script = type(self).script
        outcome = script[len(self.reads) - 1] if len(self.reads) <= len(script) else None
        if outcome == "hang":
            await asyncio.sleep(30.0)
            return {}
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)

    async def harvest_pushes(self, wanted_starts=None, timeout=None):
        """The spec 013 seam: record the call, return the scripted
        captures ({} = no pushes — the world-2 failure)."""
        self.harvest_calls.append((wanted_starts, timeout))
        if type(self).harvest_hang:
            await asyncio.sleep(30.0)
            return {}
        return dict(type(self).harvest_result or {})

    async def pair(self):
        self.pair_calls += 1
        if type(self).pair_error is not None:
            raise type(self).pair_error
        return type(self).pair_result

    async def disconnect(self):
        self.disconnected = True


def _make_flow(hass=None, client_cls=FakeHarvestClient, script=None,
               harvest_result=None, harvest_hang=False):
    """Build a fresh flow bound to the fake hass (the 011 pattern)."""
    client_cls.reset()
    if script is not None:
        client_cls.script = list(script)
    if harvest_result is not None:
        client_cls.harvest_result = harvest_result
    client_cls.harvest_hang = harvest_hang
    cf.AquaTempBleClient = client_cls
    flow = cf.AquaTempConfigFlow()
    flow.hass = hass or FlowHass(infos=[FlowInfo(ADDRESS, "BlueNRG-C0941C", -50)])
    return flow


async def _drive_progress(flow, result):
    """Mirror HA's FlowManager for SHOW_PROGRESS results (spec 004)."""
    assert result["type"] == "progress", result
    await result["progress_task"]
    result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    assert result["type"] in ("progress", "progress_done"), result
    if result["type"] == "progress_done":
        result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    return result


async def _drive_to_confirm(hass=None, script=None, harvest_result=None,
                             harvest_hang=False):
    """Drive the device step through the test task; return (flow, client,
    result, cap)."""
    flow = _make_flow(hass=hass, script=script, harvest_result=harvest_result,
                      harvest_hang=harvest_hang)
    with _flow_logs() as cap:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    client = FakeHarvestClient.instances[-1]
    return flow, client, result, cap


# --------------------------------------------------------------------- tests
async def test_world1_push_harvest_succeeds_with_mode_push() -> None:
    """AC-3 world 1: first read times out, probe returns None, harvest
    sees the T-block push => the test SUCCEEDS with mode "push" — the
    success WARNING summary lists the T-window raws, the confirm step
    is the push variant, the entry records mode push."""
    flow, client, result, cap = await _drive_to_confirm(
        script=[AquaTempTimeoutOutcome(FIRST_TIMEOUT), AquaTempTimeoutOutcome(FIRST_TIMEOUT)],
        harvest_result={2046: CAPTURE_2046},
    )
    # the push-mode confirm step (NOT the classic one)
    assert result["type"] == "form" and result["step_id"] == "confirm_push", result
    # the probe ran before the harvest (the stage order of FR-3)
    assert len(client.reads) == 2, client.reads
    assert client.reads[0]["resend_every"] == 2.0
    assert client.reads[0]["resend_max"] == 3
    assert client.reads[1]["force_response"] is True
    # the harvest: the four observed starts, the 30 s window
    assert len(client.harvest_calls) == 1, client.harvest_calls
    wanted, timeout = client.harvest_calls[0]
    assert wanted == {1001, 1046, 2001, 2046}, wanted
    assert timeout == stub_const.PUSH_HARVEST_TIMEOUT == 30.0
    assert client.disconnected
    # the evidence lines (contract 013 §2.3, WARNING decisive-once)
    assert "push harvest: listening 30 s for block pushes (no frames sent)" in (
        cap.messages
    ), cap.messages
    summary = (
        "push harvest: T-block captured (start=2046 ×2); "
        "T-window raws: 2046=32765 2047=145 2048=140 2049=140 2050=135 2051=150"
    )
    assert summary in cap.messages, cap.messages
    assert cap.levels[cap.messages.index(summary)] == "WARNING"
    # spec 022 FR-7: the placeholders decode LIVE from the harvest
    # captures — the T-block values (inlet/outlet/ambient) and the
    # per-value fallbacks for the absent blocks (power/setpoint)
    placeholders = result["description_placeholders"]
    assert placeholders["inlet"] == "14.5 °C", placeholders
    assert placeholders["outlet"] == "14.0 °C", placeholders
    assert placeholders["ambient"] == "13.5 °C", placeholders
    assert placeholders["power"] == "unknown", placeholders  # no 2001 capture
    assert placeholders["setpoint"] == "unknown", placeholders  # no 1046 capture
    assert "mode" not in placeholders, placeholders  # no mode line (FR-7)
    # spec 022 FR-2/AC-2: the schema asks for a NAME only — the
    # confirmation checkboxes are gone
    schema = result["data_schema"]
    schema_items = _schema_keys(schema)
    assert schema_items == {"name"}, schema_items


def _schema_keys(schema) -> set[str]:
    """Extract the field keys from a stubbed schema (the vol markers'
    .key attributes — Required and Optional alike)."""
    if isinstance(schema, dict):
        return {getattr(k, "key", k) for k in schema}
    return {getattr(item, "key", item) for item in schema}


async def test_world1_entry_records_push_mode() -> None:
    """AC-3 + spec 022 AC-2: submitting the push confirm step creates
    the entry with mode "push" (data field) and options that carry
    ONLY the poll interval + the display unit (the gate keys are
    gone with the gates)."""
    flow, _client, result, _cap = await _drive_to_confirm(
        script=[
            AquaTempTimeoutOutcome(FIRST_TIMEOUT),
            AquaTempTimeoutOutcome(FIRST_TIMEOUT),
        ],
        harvest_result={2046: CAPTURE_2046},
    )
    assert result["step_id"] == "confirm_push", result
    created = await flow.async_step_confirm_push(user_input={"name": "Pool pump"})
    assert created["type"] == "create_entry", created
    assert created["data"] == {
        "address": ADDRESS,
        "name": "Pool pump",
        "mode": "push",
    }, created
    # Spec 022 AC-2: the options carry ONLY the poll interval + the
    # display unit — the four gate keys are gone
    assert created["options"] == {
        "poll_interval": 30,
        "temperature_unit": "C",
    }, created["options"]


async def test_world2_no_pushes_routes_no_response_byte_identical() -> None:
    """AC-3 world 2: no pushes at all => the EXISTING no_response key +
    census message, byte-identical (FR-3: no new failure UI keys), the
    stage label of the final failure line unchanged (first-read)."""
    flow, client, result, cap = await _drive_to_confirm(
        script=[AquaTempTimeoutOutcome(FIRST_TIMEOUT), AquaTempTimeoutOutcome(FIRST_TIMEOUT)],
        harvest_result={},  # the harvest heard NOTHING
    )
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    # the timeout WARNING line: the ORIGINAL census message, the
    # first-read stage literal (012-pinned), byte-identical
    failure_lines = [m for m in cap.messages if "no response:" in m]
    assert failure_lines, cap.messages
    assert any(
        f"connection test to {ADDRESS}: no response: "
        f"AquaTempTimeoutError: {FIRST_TIMEOUT} "
        "at stage first-read after" in m
        for m in failure_lines
    ), failure_lines
    # the harvest ran and honestly reported the empty window
    assert len(client.harvest_calls) == 1
    assert "push harvest: no block pushes within 30 s" in cap.messages, cap.messages


async def test_world3_probe_answered_harvest_never_runs() -> None:
    """AC-3 world 3 / US-6: the probe ANSWERS => the classic confirm
    step, the harvest stage never runs, the entry keeps today's shape
    (no mode field)."""
    flow_regs = {
        cf.registers.REG_POWER: 1,
        cf.registers.REG_MODE: 1,
        cf.registers.REG_INLET_WATER_TEMP: 245,
        cf.registers.REG_OUTLET_WATER_TEMP: 232,
        cf.registers.REG_AMBIENT_TEMP: cf.registers.SENTINEL_RAW,
    }
    flow, client, result, _cap = await _drive_to_confirm(
        script=[AquaTempTimeoutOutcome(FIRST_TIMEOUT), dict(flow_regs)],
        harvest_result={2046: CAPTURE_2046},  # scripted but must stay UNUSED
    )
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    assert client.harvest_calls == [], "the harvest must not run when the probe answers"
    # placeholders decoded from the real probe regs (classic path)
    assert result["description_placeholders"]["power"] == "on"
    created = await flow.async_step_confirm(user_input={"name": "Classic"})
    assert created["data"] == {"address": ADDRESS, "name": "Classic"}, created
    assert "mode" not in created["data"], created
    # the classic options shape is unchanged (and carries no gate keys)
    assert created["options"] == {
        "poll_interval": 30,
        "temperature_unit": "C",
    }, created["options"]


async def test_harvest_budget_arithmetic() -> None:
    """AC-3 / plan 013 §2.2 [P arithmetic]: connect ~5 s + first-read
    8 s + probe 8 s + harvest 30 s ≈ 51 s — inside the unchanged 70 s
    TEST_TIMEOUT budget."""
    assert stub_const.PUSH_HARVEST_TIMEOUT == 30.0
    assert cf.TEST_TIMEOUT == 70.0
    assert 5 + 8 + 8 + stub_const.PUSH_HARVEST_TIMEOUT < cf.TEST_TIMEOUT


async def test_harvest_stage_label_on_budget_expiry() -> None:
    """The push-harvest stage label (the 007/011 conventions): a budget
    expiry mid-harvest reports stage push-harvest in the failure line."""
    old_budget = cf.TEST_TIMEOUT
    cf.TEST_TIMEOUT = 0.05
    try:
        flow, _client, result, cap = await _drive_to_confirm(
            script=[AquaTempTimeoutOutcome(FIRST_TIMEOUT), AquaTempTimeoutOutcome(FIRST_TIMEOUT)],
            harvest_result={},
            harvest_hang=True,  # the harvest is still listening when the budget dies
        )
    finally:
        cf.TEST_TIMEOUT = old_budget
    assert result["type"] == "form", result  # the failure routed back
    assert any(
        "exceeded the" in m and "at stage push-harvest" in m for m in cap.messages
    ), [m for m in cap.messages if "exceeded" in m]


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_world1_push_harvest_succeeds_with_mode_push,
        test_world1_entry_records_push_mode,
        test_world2_no_pushes_routes_no_response_byte_identical,
        test_world3_probe_answered_harvest_never_runs,
        test_harvest_budget_arithmetic,
        test_harvest_stage_label_on_budget_expiry,
    ]
    failures = 0
    for test in tests:
        try:
            if asyncio.iscoroutinefunction(test):
                asyncio.run(test())
            else:
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
    print(f"PASSED: {len(tests)}/{len(tests)} flow harvest tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())