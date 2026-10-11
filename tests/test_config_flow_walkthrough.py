#!/usr/bin/env python3
"""Walkthrough tests for the spec 002 config flow state machine, extended
by spec 004: the progress steps are driven manager-faithfully and the
progress-step contract is asserted on every re-entry — a progress step
may only yield progress or progress_done; anything else is exactly
what HA punishes with the ValueError that crashed the production
instance (research 004 §3.2).

Extended by spec 005: every failure branch of the connection test must
log a diagnosable line (address + real exception) before mapping its UI
key — no failure may be silent (AC-1) — plus the two new routing cases:
not_found (device resolution miss, FR-12) and budget expiry (still keyed
cannot_connect, but logged, FR-5/AC-3).

Extended by spec 006: the connection test gets the vendor app's own
retry persistence — a 70 s budget (two 20 s connect attempts + settle +
read, research 006 §2.2) and max_attempts=2 (FR-3/AC-3).

Unlike test_config_flow_list.py (the pure list builder), this file drives
the ACTUAL flow steps end to end — user menu -> device list -> live test
-> confirm -> entry — plus every branch: rescan, no-devices menu,
adapter-less abort, manual path, test-failure routing back to the origin
form, and the dormant bluetooth path (contracts/config-flow-states.md §2
is the normative transition table; labels per plan.md §6).

Extended by spec 007: every failure branch of the connection test
appends its stage + elapsed + budget suffix to the log line (FR-3,
contract 007 §3.3) — log-only, the UI keys are untouched (NFR-2), and
the stage values follow the contract's branch table (resolve for the
resolution miss, connect for the connect phase, first-read for the
read phase, unknown for the unexpected branch).

Home Assistant is fully stubbed (no real HA install needed): config_flow
is imported against minimal fake homeassistant/voluptuous/bleak modules,
and the stubbed result helpers record every step result for inspection.
The goal: a flow path that becomes unrunnable — a forgotten required
argument, a broken transition, a progress step whose progress_action
does not match its step_id — fails HERE, not on a live instance.

Run standalone:

    python3 tests/test_config_flow_walkthrough.py

No pytest API is used beyond an optional module-level asyncio mark
(below), so plain python works.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

# Pytest glue (optional): this suite's cases are bare async functions
# driven by main() below; under plain pytest, pytest-asyncio's strict
# mode only runs coroutine tests that carry the asyncio marker. The
# module-level mark makes `pytest tests/ -q` green too; the standalone
# runner never reads it, and the guarded import keeps pytest-less hosts
# working (spec 006 gate D1).
try:
    import pytest

    pytestmark = pytest.mark.asyncio
except ImportError:  # pragma: no cover — pytest-less standalone host
    pass

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
PKG = "_aqua_flow_e2e"  # synthetic package name (distinct from the list test)


class AlreadyConfigured(Exception):
    """Stand-in for HA's AbortFlow(already_configured) from the guard."""


# ---------------------------------------------------------------------------
# Minimal but FUNCTIONAL stubs so config_flow.py imports and runs outside
# Home Assistant. These are installed unconditionally: the walkthrough
# must be deterministic regardless of what is (or is not) installed.
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    vol_mod = types.ModuleType("voluptuous")

    class _Vol:  # tiny stand-in: no validation, full introspection
        @staticmethod
        def Schema(schema):
            return schema

        @staticmethod
        def Required(key, **kwargs):
            return key

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

    for name in ("Schema", "Required", "In", "All", "Coerce", "Range"):
        setattr(vol_mod, name, getattr(_Vol, name))
    sys.modules["voluptuous"] = vol_mod

    bleak_mod = types.ModuleType("bleak")
    bleak_mod.BleakClient = object
    bleak_mod.BleakScanner = object
    sys.modules["bleak"] = bleak_mod

    class _StubConfigFlow:
        """Functional stand-in for homeassistant.config_entries.ConfigFlow."""

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()
            # HA's real ConfigFlow stores the domain passed via the class
            # kwarg; result helpers expose it as `handler` (research 004 §3.3).
            cls.handler = kwargs.get("domain")

        # -- results (recorded for inspection) --------------------------
        def async_show_menu(
            self,
            *,
            step_id,
            menu_options=None,
            description_placeholders=None,
        ):
            return {
                "type": "menu",
                "step_id": step_id,
                "menu_options": dict(menu_options or {}),
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_form(
            self,
            *,
            step_id,
            data_schema=None,
            errors=None,
            description_placeholders=None,
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress(
            self,
            *,
            step_id,
            progress_action=None,
            progress_task=None,
            description_placeholders=None,
        ):
            return {
                "type": "progress",
                "step_id": step_id,
                "progress_action": progress_action,
                "progress_task": progress_task,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress_done(self, *, next_step_id):
            # HA result shape (research 004 §3.3): SHOW_PROGRESS_DONE whose
            # step_id is the NEXT step the manager will call (kwarg is
            # next_step_id, not step_id).
            return {
                "type": "progress_done",
                "step_id": next_step_id,
                "handler": getattr(self, "handler", None),
            }

        def async_abort(self, *, reason):
            return {"type": "abort", "reason": reason}

        def async_create_entry(self, *, title=None, data=None, options=None):
            return {
                "type": "create_entry",
                "title": title,
                "data": data,
                "options": options,
            }

        # -- unique-id machinery -----------------------------------------
        async def async_set_unique_id(self, unique_id, *, raise_on_progress=True):
            self._stub_unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            if getattr(self, "_stub_unique_id", None) in self.hass.current_ids:
                raise AlreadyConfigured(self._stub_unique_id)

        def _async_current_ids(self, include_ignore=True):
            return list(self.hass.current_ids)

    class _StubOptionsFlow:
        pass

    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_helpers = types.ModuleType("homeassistant.helpers")

    cfg_entries = types.ModuleType("homeassistant.config_entries")
    cfg_entries.ConfigFlow = _StubConfigFlow
    cfg_entries.ConfigFlowResult = dict  # annotations only (stringified)
    cfg_entries.OptionsFlow = _StubOptionsFlow

    ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _bt_async_scanner_count(hass, connectable=True):
        return hass.bt_scanner_count

    def _bt_async_discovered_service_info(hass, connectable=True):
        return list(hass.bt_infos)

    ha_bt.async_scanner_count = _bt_async_scanner_count
    ha_bt.async_discovered_service_info = _bt_async_discovered_service_info

    ha_translation = types.ModuleType("homeassistant.helpers.translation")

    async def _tr_async_get_translations(
        hass, language, category, integrations, *, config_flow=False
    ):
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
    """Import config_flow.py as part of a synthetic package."""
    _install_stubs()
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT_DIR)]  # supports `from . import x`
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.config_flow", COMPONENT_DIR / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.config_flow"] = module
    spec.loader.exec_module(module)
    return module


cf = _import_config_flow()
_build_device_list = cf._build_device_list  # noqa: SLF001 — test seam, see list tests
UUID_WRITE = cf.UUID_WRITE


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeInfo:
    """Stand-in for a discovered BluetoothServiceInfoBleak entry."""

    def __init__(self, address, name, rssi, service_uuids=()):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.service_uuids = list(service_uuids)


class FakeHass:
    """Hass stub: everything the config flow touches, nothing else."""

    def __init__(
        self,
        scanner_count=1,
        infos=None,
        current_ids=None,
        language="en",
        translations=None,
    ):
        self.config = types.SimpleNamespace(language=language)
        self.bt_scanner_count = scanner_count
        self.bt_infos = list(infos or [])
        self.current_ids = set(current_ids or ())
        # Empty -> the flow's EN fallback labels; a flattened strings
        # dict -> the runtime translation path (probed key format).
        self.translations = dict(translations or {})

    def async_create_task(self, target):
        return asyncio.ensure_future(target)


class FakeClient:
    """Stand-in for AquaTempBleClient, with per-test fault injection."""

    instances = []
    fault = None  # exception class/instance raised by connect(), if any
    regs = {}  # register block returned by read_registers()

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.fault = None
        cls.regs = {
            cf.registers.REG_POWER: 1,          # on
            cf.registers.REG_MODE: 1,           # heat
            cf.registers.REG_INLET_WATER_TEMP: 245,    # 24.5 degC
            cf.registers.REG_OUTLET_WATER_TEMP: 232,   # 23.2 degC
            cf.registers.REG_AMBIENT_TEMP: cf.registers.SENTINEL_RAW,  # absent
        }

    def __init__(self, address, name=None, hass=None):
        self.address = address
        self.name = name
        self.hass = hass
        self.connected = False
        self.disconnected = False
        self.connect_kwargs = None  # recorded by connect() (spec 006 AC-3)
        self.read_kwargs = None  # recorded by read_registers() (spec 009 FR-2)
        type(self).instances.append(self)

    async def connect(self, max_attempts=None):
        # max_attempts kwarg (spec 005): the flow test calls
        # connect(max_attempts=2) since spec 006 FR-3; the pre-005 flow
        # called connect() bare — both shapes must work.
        self.connect_kwargs = {"max_attempts": max_attempts}
        if type(self).fault is not None:
            raise type(self).fault
        self.connected = True

    async def read_registers(self, start, count, *, resend_every=None, resend_max=0):
        assert (start, count) == (
            cf.registers.BASE_BLOCK_START,
            cf.registers.BASE_BLOCK_COUNT,
        )
        # Spec 009 FR-2: the flow's first read passes the bounded re-send
        # parameters (contract 009 §5) — recorded so the walkthrough pins
        # the call site.
        self.read_kwargs = {"resend_every": resend_every, "resend_max": resend_max}
        return dict(type(self).regs)

    async def disconnect(self):
        self.connected = False
        self.disconnected = True


class SlowFakeClient(FakeClient):
    """FakeClient whose read hangs briefly, to observe a re-entry while
    the progress task is still running (E-7: mid-progress refresh)."""

    async def read_registers(self, start, count, **kwargs):
        await asyncio.sleep(0.05)
        return await super().read_registers(start, count, **kwargs)


class HangingFakeClient(FakeClient):
    """FakeClient whose connect() outlives the (patched) test budget —
    the pre-005 silent cancellation path: the connect hangs, the flow's
    asyncio.timeout cancels it in flight. Spec 005 expects the budget
    expiry to be logged now (AC-3)."""

    async def connect(self, max_attempts=None):
        await asyncio.sleep(0.5)  # always longer than the patched budget
        self.connected = True


class _CaptureHandler(logging.Handler):
    """Collect formatted log messages — caplog without pytest."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class _LogCapture:
    """Attach a capturing handler to a logger for the duration of a `with`.

    The logger level is lowered to DEBUG on entry and restored on exit so
    provenance DEBUG lines are assertable too (spec 005 FR-3).
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._handler = _CaptureHandler()
        self._previous_level: int | None = None

    def __enter__(self) -> _CaptureHandler:
        self._previous_level = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self._logger.addHandler(self._handler)
        return self._handler

    def __exit__(self, *exc_info: object) -> bool:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous_level)
        return False


def make_flow(hass):
    """Build a fresh flow bound to the fake hass (client fakes reset)."""
    FakeClient.reset()
    # Seam: async_step_test builds its client through this module-level
    # name — swap the real transport for the recording fake.
    cf.AquaTempBleClient = FakeClient
    flow = cf.AquaTempConfigFlow()
    flow.hass = hass
    return flow


def _flatten_translations(domain, data):
    """Flatten a strings-style dict the way the HA translation helper does:
    nested dicts become dotted component.<domain>.<path> keys."""
    out = {}

    def rec(prefix, obj):
        for key, value in obj.items():
            path = f"{prefix}.{key}"
            if isinstance(value, dict):
                rec(path, value)
            else:
                out[path] = value

    rec(f"component.{domain}", data)
    return out


async def _drive_progress(flow, result):
    """Mirror HA's FlowManager for SHOW_PROGRESS results (plan 004 §5).

    The manager registers a done-callback on the progress task and, when
    the task completes, re-enters the step with user_input=None. THE
    contract (research 004 §3.2): that re-entry may only yield progress
    (same or new task) or progress_done — any other result type is what
    HA punishes with "ValueError: Show progress can only transition to
    show progress or show progress done." On progress_done the
    manager's configure loop advances to async_step_<next_step_id>(None).
    """
    assert result["type"] == "progress", result
    await result["progress_task"]  # the manager's done-callback fires
    # Re-entry: HA calls the step again with user_input=None.
    result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    assert result["type"] in ("progress", "progress_done"), (
        "progress re-entry must yield progress or progress_done "
        f"(data_entry_flow contract) — got {result['type']!r}: {result}"
    )
    if result["type"] == "progress_done":
        # Manager loop: advance to the next step with user_input=None.
        result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    return result


# ---------------------------------------------------------------------------
# Walkthrough tests (contracts/config-flow-states.md §2 + 004 contract)
# ---------------------------------------------------------------------------


async def test_happy_path_menu_device_test_confirm_entry():
    """START -> user menu -> device form -> test progress -> confirm -> entry."""
    hass = FakeHass(
        infos=[
            FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -62),
            FakeInfo("AA:BB:CC:DD:EE:11", "TV Salon", -75),
        ]
    )
    flow = make_flow(hass)

    # 1. First step is a MENU with 3 options (AC-11: live counts shown).
    result = await flow.async_step_user()
    assert result["type"] == "menu", result
    assert result["step_id"] == "user", result
    assert list(result["menu_options"]) == ["device", "scan", "manual"], result
    assert result["menu_options"]["device"] == "Select a discovered device"
    assert result["menu_options"]["scan"] == "Scan again"
    assert result["menu_options"]["manual"] == "Enter the Bluetooth address manually"
    assert result["description_placeholders"] == {"recognized": 1, "total": 2}, result

    # 2. Menu pick "device": HA calls async_step_device() with NO input.
    #    The form lists real addresses only — no sentinel pseudo-entries.
    result = await flow.async_step_device()
    assert result["type"] == "form" and result["step_id"] == "device", result
    options = result["data_schema"]["address"]
    assert list(options) == ["AA:BB:CC:DD:EE:10", "AA:BB:CC:DD:EE:11"], options
    assert "__rescan__" not in options and "__manual__" not in options
    assert options["AA:BB:CC:DD:EE:10"] == "BlueNRG-7A — AA:BB:CC:DD:EE:10 — RSSI -62"

    # 3. Submit the pick: unique-id guard, then the test progress step.
    result = await flow.async_step_device(
        user_input={"address": "AA:BB:CC:DD:EE:10"}
    )
    assert result["type"] == "progress" and result["step_id"] == "test", result
    assert result["progress_action"] == "test"  # must match the step_id
    assert result["progress_task"] is not None
    assert flow._stub_unique_id == "AA:BB:CC:DD:EE:10"  # upper-cased unique id

    # 4. Manager-faithful drive: task completes -> HA re-enters the step,
    #    which must yield progress_done(confirm) -> the manager advances to
    #    the confirm form (004 FR-1; the pre-fix code returned the form
    #    directly and HA raised the ValueError).
    result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    client = FakeClient.instances[-1]
    # FR-9: the client is built with the flow's hass; name does not leak.
    assert client.hass is hass
    assert client.address == "AA:BB:CC:DD:EE:10"
    assert client.name is None
    assert client.disconnected  # the test always closes the link
    placeholders = result["description_placeholders"]
    assert placeholders["address"] == "AA:BB:CC:DD:EE:10"
    assert placeholders["power"] == "on", placeholders
    assert placeholders["mode"] == "heat", placeholders
    assert placeholders["inlet"] == "24.5 °C", placeholders
    assert placeholders["outlet"] == "23.2 °C", placeholders
    assert placeholders["ambient"] == "sensor absent", placeholders

    # 5. Confirm -> entry created with address + default options.
    result = await flow.async_step_confirm(user_input={"name": "Pool heat pump"})
    assert result["type"] == "create_entry", result
    assert result["data"] == {"address": "AA:BB:CC:DD:EE:10", "name": "Pool heat pump"}
    assert result["options"] == {"poll_interval": 30, "temperature_unit": "C"}


async def test_failed_test_returns_to_device_form_with_error():
    """P3: a failed test returns to the DEVICE LIST with the error (AC-2/AC-7)."""
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    await flow.async_step_user()  # menu
    await flow.async_step_device()  # device form

    FakeClient.fault = cf.AquaTempConnectionError("vendor app holds the link")
    with _LogCapture(cf._LOGGER) as log:
        result = await flow.async_step_device(
            user_input={"address": "AA:BB:CC:DD:EE:10"}
        )
        assert result["type"] == "progress" and result["step_id"] == "test", result
        # Re-entry must yield progress_done(next_step_id="device"); the manager
        # then re-renders the device form with the stashed error (004 FR-4).
        result = await _drive_progress(flow, result)
    assert result["type"] == "form", result
    assert result["step_id"] == "device", result
    assert result["errors"] == {"base": "cannot_connect"}, result
    assert "AA:BB:CC:DD:EE:10" in result["data_schema"]["address"]
    # Spec 005 FR-2/AC-6: the cannot_connect branch logs the address and
    # the full exception chain (red-first: silent on the pre-005 code).
    # Spec 007 FR-3/AC-4 (red-first: no suffix on the pre-007 code): the
    # same line now carries the stage, the elapsed and the budget
    # (contract 007 §3.3) — the fake raises from connect(), so the
    # stage is "connect".
    assert any(
        "AA:BB:CC:DD:EE:10" in m
        and "failed" in m
        and "vendor app holds the link" in m
        and "at stage connect" in m
        and "after " in m
        and "budget" in m
        for m in log.messages
    ), log.messages

    # The stash is consumed by that render: a second render of the same
    # step shows no stale error (004 FR-4 read-and-clear).
    result = await flow.async_step_device()
    assert result["type"] == "form" and result["errors"] is None, result

    # Recovery: a retry with a working unit completes the flow. Exactly
    # one new test task starts (AC-7 rapid retry).
    FakeClient.fault = None
    result = await flow.async_step_device(
        user_input={"address": "AA:BB:CC:DD:EE:10"}
    )
    result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result


async def test_manual_path_and_failure_symmetry():
    """Manual: invalid MAC -> field error; failure -> back to manual (AC-7/AC-8)."""
    hass = FakeHass(infos=[])  # manual stays reachable with no devices (FR-3)
    flow = make_flow(hass)
    await flow.async_step_user()  # no_devices menu
    result = await flow.async_step_manual()  # menu pick "manual"
    assert result["type"] == "form" and result["step_id"] == "manual", result

    # Malformed MAC -> invalid_address on the same form.
    result = await flow.async_step_manual(user_input={"address": "not a mac"})
    assert result["step_id"] == "manual", result
    assert result["errors"] == {"address": "invalid_address"}, result

    # Valid MAC -> unique-id guard (upper-cased) -> test step.
    result = await flow.async_step_manual(
        user_input={"address": "aa:bb:cc:dd:ee:ff"}
    )
    assert result["type"] == "progress" and result["step_id"] == "test", result
    assert flow._stub_unique_id == "AA:BB:CC:DD:EE:FF"
    FakeClient.fault = cf.AquaTempTimeoutError("no complete response")
    # Re-entry must yield progress_done(next_step_id="manual"); the
    # manager re-renders the manual form with the stashed error.
    with _LogCapture(cf._LOGGER) as log:
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "manual", result
    assert result["errors"] == {"base": "no_response"}, result
    # Spec 005 FR-2: the no_response branch logs the address + chain.
    # Spec 007 FR-3/AC-4 (red-first): the suffix's stage is "first-read"
    # (contract 007 §3.3 — the request deadline only expires during the
    # read).
    assert any(
        "aa:bb:cc:dd:ee:ff" in m
        and "no response" in m
        and "at stage first-read" in m
        and "after " in m
        and "budget" in m
        for m in log.messages
    ), log.messages

    # The stash is consumed: a second render shows no stale error.
    result = await flow.async_step_manual()
    assert result["type"] == "form" and result["errors"] is None, result

    # An already-configured address aborts BEFORE any test runs.
    FakeClient.fault = None
    hass.current_ids.add("AA:BB:CC:DD:EE:FF")
    try:
        await flow.async_step_manual(user_input={"address": "AA:BB:CC:DD:EE:FF"})
    except AlreadyConfigured:
        pass
    else:
        raise AssertionError("a configured address must abort the flow")
    assert len(FakeClient.instances) == 1  # no new test started


async def test_reentry_while_running_returns_same_task():
    """E-7/FR-3: a re-entry while the task still runs returns the SAME task.

    The manager no-ops on an unchanged progress task (research 004 §3.1);
    re-creating a task per entry (the pre-004 behaviour) would duplicate
    connection attempts against a unit that accepts a single BLE link.
    """
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    cf.AquaTempBleClient = SlowFakeClient  # read hangs briefly

    result = await flow.async_step_device(
        user_input={"address": "AA:BB:CC:DD:EE:10"}
    )
    assert result["type"] == "progress" and result["step_id"] == "test", result
    first_task = result["progress_task"]

    # Mid-progress re-entry (frontend refresh): progress again, with the
    # SAME task object — never a second attempt.
    again = await flow.async_step_test()
    assert again["type"] == "progress" and again["step_id"] == "test", again
    assert again["progress_task"] is first_task, (
        "a re-entry while the task runs must return the SAME task object"
    )

    # The single run then completes: progress_done -> confirm form.
    result = await _drive_progress(flow, again)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    assert len(FakeClient.instances) == 1  # exactly one connection attempt


async def test_crc_error_failure_routes_to_device_form():
    """crc_error (invalid response frame) routes to the origin form too.

    Failure-routing matrix (004 routing table): cannot_connect from the
    device origin and the dormant bluetooth path, no_response from the
    manual origin, crc_error here — every key reaches its origin form.
    """
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    await flow.async_step_user()  # menu
    await flow.async_step_device()  # device form

    FakeClient.fault = cf.AquaTempResponseError("bad CRC")
    with _LogCapture(cf._LOGGER) as log:
        result = await flow.async_step_device(
            user_input={"address": "AA:BB:CC:DD:EE:10"}
        )
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "crc_error"}, result
    # Spec 005 FR-2: the crc_error branch logs the address + chain.
    # Spec 007 FR-3/AC-4 (red-first): stage "first-read" — an invalid
    # frame only surfaces during the read (contract 007 §3.3).
    assert any(
        "AA:BB:CC:DD:EE:10" in m
        and "bad response" in m
        and "at stage first-read" in m
        and "after " in m
        and "budget" in m
        for m in log.messages
    ), log.messages
    # Stash consumed: a second render shows no stale error.
    result = await flow.async_step_device()
    assert result["errors"] is None, result


async def test_not_found_routes_to_device_form_with_log():
    """Spec 005 FR-12/AC-2: a resolution miss routes not_found + logs it.

    Red-first: on the 5aa887b code this test cannot even construct the
    fault (no AquaTempDeviceNotFoundError) and no branch logged.
    """
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    await flow.async_step_user()  # menu
    await flow.async_step_device()  # device form

    FakeClient.fault = cf.AquaTempDeviceNotFoundError(
        "no Aqua Temp device found for address AA:BB:CC:DD:EE:10"
    )
    with _LogCapture(cf._LOGGER) as log:
        result = await flow.async_step_device(
            user_input={"address": "AA:BB:CC:DD:EE:10"}
        )
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "not_found"}, result
    # AC-1: the not_found branch logs the address and the cause.
    # Spec 007 FR-3/AC-4 (red-first): stage "resolve" — this exception
    # can only come from the resolution tiers (contract 007 §3.3).
    assert any(
        "AA:BB:CC:DD:EE:10" in m
        and "not found" in m
        and "at stage resolve" in m
        and "after " in m
        and "budget" in m
        for m in log.messages
    ), log.messages
    # Stash consumed: a second render shows no stale error.
    result = await flow.async_step_device()
    assert result["errors"] is None, result


async def test_budget_expiry_routes_cannot_connect_with_log():
    """Spec 005 FR-5/FR-12/AC-3: budget expiry stays keyed cannot_connect
    but is no longer silent — the log names the budget and the address.

    Red-first note: the ROUTING assertion also passes on the pre-005
    code (the key was already cannot_connect); the LOG assertion is the
    red part — the old TimeoutError branch mapped the key without any
    log line (research 005 §4.2, the production incident's signature).
    """
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    cf.AquaTempBleClient = HangingFakeClient  # connect outlives the budget

    original_timeout = cf.TEST_TIMEOUT
    cf.TEST_TIMEOUT = 0.2  # patched budget: the 0.5 s hang always expires it
    try:
        with _LogCapture(cf._LOGGER) as log:
            result = await flow.async_step_device(
                user_input={"address": "AA:BB:CC:DD:EE:10"}
            )
            result = await _drive_progress(flow, result)
    finally:
        cf.TEST_TIMEOUT = original_timeout

    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "cannot_connect"}, result
    # AC-3: the budget-expiry branch names the budget value explicitly.
    # Spec 007 FR-3/AC-4 (red-first): the stage is where the cancellation
    # hit — the fake hangs inside connect(), so "connect" (contract 007
    # §3.3, research 007 §8).
    assert any(
        "AA:BB:CC:DD:EE:10" in m
        and "budget" in m
        and "at stage connect" in m
        and "after " in m
        for m in log.messages
    ), log.messages


async def test_unexpected_failure_logs_stage_unknown():
    """Spec 007 FR-3/AC-4: the unexpected-exception branch logs the
    stage suffix too (contract 007 §3.3: stage "unknown" — an unexpected
    exception has no reliable site; the label says so instead of
    guessing). UI key stays cannot_connect (spec 005 mapping).

    Red-first: the pre-007 line carries no stage/elapsed suffix.
    """
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    FakeClient.fault = ValueError("bleak exploded")
    with _LogCapture(cf._LOGGER) as log:
        result = await flow.async_step_device(
            user_input={"address": "AA:BB:CC:DD:EE:10"}
        )
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "cannot_connect"}, result
    assert any(
        "unexpected error during connection test" in m
        and "at stage unknown" in m
        and "after " in m
        and "budget" in m
        for m in log.messages
    ), log.messages


async def test_connection_test_budget_and_attempts():
    """Spec 006 FR-3/AC-3: 70 s budget, two connect attempts.

    Red-first: the spec-005 flow passed max_attempts=1 inside a 40 s
    budget (a second 20 s attempt never fitted); spec 006 gives the probe
    the vendor app's own persistence — two attempts inside 70 s
    (2 x 20 s connects + backoff + 2 s settle + 8 s read + margin,
    plan 006 §3.1; the vendor app retries connects, research 006 §2.2).

    Spec 009 FR-2: the first read also carries the bounded app-parity
    re-send (resend_every=2.0, resend_max=3 — contract 009 §5), inside
    the unchanged budget and window.
    """
    assert cf.TEST_TIMEOUT == 70.0, cf.TEST_TIMEOUT
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    result = await flow.async_step_device(
        user_input={"address": "AA:BB:CC:DD:EE:10"}
    )
    assert result["type"] == "progress" and result["step_id"] == "test", result
    result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    client = FakeClient.instances[-1]
    # The fake client records the kwargs _async_test passed (spec 006 FR-3).
    assert client.connect_kwargs == {"max_attempts": 2}, client.connect_kwargs
    # Spec 009 FR-2 (contract 009 §5): the first read re-sends the
    # identical vendor frame on the 2 s cadence, 3 extra sends, inside
    # the unchanged 8 s window.
    assert client.read_kwargs == {"resend_every": 2.0, "resend_max": 3}, (
        client.read_kwargs
    )


async def test_scan_branch_returns_to_user_menu():
    """Menu pick "scan": bounded progress, then the user menu with fresh counts."""
    hass = FakeHass(infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)])
    flow = make_flow(hass)
    await flow.async_step_user()

    # Speed up the bounded wait for the test run (value itself asserted
    # against const in test_flow_state_constants).
    original_wait = cf.SCAN_WAIT_SECONDS
    cf.SCAN_WAIT_SECONDS = 0.01
    try:
        result = await flow.async_step_scan()
        assert result["type"] == "progress" and result["step_id"] == "scan", result
        assert result["progress_action"] == "scan"  # must match the step_id
        assert result["description_placeholders"]["seconds"] == 0.01, result
        # A new advertiser appears while the wait runs: the menu driven
        # after progress_done re-renders with the fresh history.
        hass.bt_infos.append(FakeInfo("AA:BB:CC:DD:EE:99", "BlueNRG-2", -40))

        # Re-entry must yield progress_done(next_step_id="user"); the
        # manager then re-renders the USER menu with fresh counts.
        result = await _drive_progress(flow, result)
        assert result["type"] == "menu" and result["step_id"] == "user", result
        assert list(result["menu_options"]) == ["device", "scan", "manual"]
        assert result["description_placeholders"] == {"recognized": 2, "total": 2}

        # E-11: an immediate second scan is harmless (one fresh wait).
        result = await flow.async_step_scan()
        result = await _drive_progress(flow, result)
        assert result["type"] == "menu" and result["step_id"] == "user", result
    finally:
        cf.SCAN_WAIT_SECONDS = original_wait


async def test_no_devices_menu_and_configured_exclusion():
    """E-5: only configured devices nearby -> no-devices menu with 2 options."""
    hass = FakeHass(
        infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)],
        current_ids={"AA:BB:CC:DD:EE:10"},
    )
    flow = make_flow(hass)
    result = await flow.async_step_user()
    assert result["type"] == "menu" and result["step_id"] == "no_devices", result
    assert list(result["menu_options"]) == ["scan", "manual"], result  # exactly 2

    # From the no-devices menu both actions stay reachable.
    result = await flow.async_step_manual()
    assert result["type"] == "form" and result["step_id"] == "manual", result
    result = await flow.async_step_device()  # defensive: empty history
    assert result["type"] == "menu" and result["step_id"] == "no_devices", result


async def test_no_bluetooth_adapter_aborts():
    """FR-2/AC-5: no scanner at all -> abort no_bluetooth_adapter."""
    hass = FakeHass(scanner_count=0)
    flow = make_flow(hass)
    result = await flow.async_step_user()
    assert result["type"] == "abort", result
    assert result["reason"] == "no_bluetooth_adapter", result


async def test_menu_labels_follow_real_translations():
    """FR-13/AC-9: menu labels resolve from the real strings/*.json keys.

    This locks the contract between the probed key format
    (component.<domain>.config.step.<menu>.menu_options.<key>) and the
    shipped translation files: if either side drifts, labels fall back
    to English and this test fails.
    """
    fr = json.loads(
        (COMPONENT_DIR / "translations" / "fr.json").read_text(encoding="utf-8")
    )
    translations = _flatten_translations(cf.DOMAIN, fr)
    hass = FakeHass(
        infos=[FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50)],
        language="fr",
        translations=translations,
    )
    flow = make_flow(hass)
    result = await flow.async_step_user()
    fr_user = fr["config"]["step"]["user"]["menu_options"]
    assert list(result["menu_options"]) == ["device", "scan", "manual"]
    assert result["menu_options"] == {
        "device": fr_user["device"],
        "scan": fr_user["scan"],
        "manual": fr_user["manual"],
    }, result

    # Same probe against the no-devices menu.
    hass_empty = FakeHass(infos=[], language="fr", translations=translations)
    flow_empty = make_flow(hass_empty)
    result = await flow_empty.async_step_user()
    fr_no_devices = fr["config"]["step"]["no_devices"]["menu_options"]
    assert result["menu_options"] == {
        "scan": fr_no_devices["scan"],
        "manual": fr_no_devices["manual"],
    }, result


async def test_dormant_bluetooth_path():
    """FR-12: async_step_bluetooth stays dormant-but-intact.

    Wrong name -> not_supported abort; matching name -> straight to the
    test step; a failed test routes to the device list with the error
    (origin="user": menus cannot display errors).
    """
    hass = FakeHass(
        infos=[
            FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -50),
            FakeInfo("AA:BB:CC:DD:EE:11", "Living room TV", -60),
        ]
    )
    flow = make_flow(hass)

    result = await flow.async_step_bluetooth(
        types.SimpleNamespace(name="Living room TV", address="AA:BB:CC:DD:EE:11")
    )
    assert result["type"] == "abort" and result["reason"] == "not_supported", result

    FakeClient.fault = cf.AquaTempConnectionError("link lost")
    result = await flow.async_step_bluetooth(
        types.SimpleNamespace(name="BlueNRG-7A", address="AA:BB:CC:DD:EE:10")
    )
    assert result["type"] == "progress" and result["step_id"] == "test", result

    # origin="user": the failure routes to the DEVICE list (menus cannot
    # display errors) via progress_done(next_step_id="device"). The client
    # is instantiated by the task, so inspect it after the drive.
    result = await _drive_progress(flow, result)
    client = FakeClient.instances[-1]
    assert client.name == "BlueNRG-7A"  # discovery name reaches the client
    assert client.hass is hass
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "cannot_connect"}, result
    # Stash consumed: a second render shows no stale error.
    result = await flow.async_step_device()
    assert result["type"] == "form" and result["errors"] is None, result


async def test_bluetooth_failure_drained_history_clears_stash():
    """E-6: routed failure + drained discovery history -> the device step
    falls back to the user menu and DROPS the stashed error (menus cannot
    display it) — it must not leak into a later form render.
    """
    hass = FakeHass(infos=[])  # history drains before the form re-renders
    flow = make_flow(hass)
    FakeClient.fault = cf.AquaTempConnectionError("link lost")
    result = await flow.async_step_bluetooth(
        types.SimpleNamespace(name="BlueNRG-7A", address="AA:BB:CC:DD:EE:10")
    )
    assert result["type"] == "progress" and result["step_id"] == "test", result
    result = await _drive_progress(flow, result)
    # No devices left: the device step falls back to the no-devices menu.
    assert result["type"] == "menu" and result["step_id"] == "no_devices", result
    # The stash was dropped by the fallback: a later manual form render
    # shows NO stale error (004 FR-4, contract §5).
    assert flow._test_error is None
    result = await flow.async_step_manual()
    assert result["type"] == "form" and result["step_id"] == "manual", result
    assert result["errors"] is None, result


async def test_flow_state_constants_and_no_sentinels():
    """P5: constants live in const.py under contract names; no sentinels."""
    assert cf.SCAN_WAIT_SECONDS == 8.0
    assert cf.MAX_OTHER_DEVICES == 30
    assert cf.UNNAMED_LABEL == "Unnamed device"
    assert not hasattr(cf, "RESCAN_KEY")
    assert not hasattr(cf, "MANUAL_KEY")
    assert not hasattr(cf, "SCAN_WAIT")
    assert not hasattr(cf, "OTHER_DEVICES_MAX")
    const_mod = sys.modules[f"{PKG}.const"]
    assert const_mod.SCAN_WAIT_SECONDS == 8.0
    assert const_mod.MAX_OTHER_DEVICES == 30
    # 004 data model: the boolean done-flags are gone; the task references
    # start clean on a fresh flow.
    flow = make_flow(FakeHass())
    assert flow._test_task is None and flow._scan_task is None
    assert flow._test_error is None
    assert "_test_done" not in vars(flow)
    assert "_scan_done" not in vars(flow)


async def test_device_list_in_form_matches_contract_builder():
    """The device form's options ARE the contract list builder's output."""
    infos = [
        FakeInfo("AA:BB:CC:DD:EE:10", "BlueNRG-7A", -62),
        FakeInfo("AA:BB:CC:DD:EE:11", "AA:BB:CC:DD:EE:11", -58, [UUID_WRITE.upper()]),
        FakeInfo("AA:BB:CC:DD:EE:12", "TV Salon", -75),
    ]
    hass = FakeHass(infos=infos, current_ids={"AA:BB:CC:DD:EE:99"})
    flow = make_flow(hass)
    await flow.async_step_user()
    result = await flow.async_step_device()
    options = result["data_schema"]["address"]
    expected, counts = _build_device_list(infos, {"AA:BB:CC:DD:EE:99"}, cf.UNNAMED_LABEL)
    assert list(options) == list(expected)
    assert options == expected
    assert counts == {"recognized": 2, "total": 3}


# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        test_happy_path_menu_device_test_confirm_entry,
        test_failed_test_returns_to_device_form_with_error,
        test_manual_path_and_failure_symmetry,
        test_reentry_while_running_returns_same_task,
        test_crc_error_failure_routes_to_device_form,
        test_not_found_routes_to_device_form_with_log,
        test_budget_expiry_routes_cannot_connect_with_log,
        test_unexpected_failure_logs_stage_unknown,
        test_connection_test_budget_and_attempts,
        test_scan_branch_returns_to_user_menu,
        test_no_devices_menu_and_configured_exclusion,
        test_no_bluetooth_adapter_aborts,
        test_menu_labels_follow_real_translations,
        test_dormant_bluetooth_path,
        test_bluetooth_failure_drained_history_clears_stash,
        test_flow_state_constants_and_no_sentinels,
        test_device_list_in_form_matches_contract_builder,
    ]
    failures = 0
    for test in tests:
        try:
            asyncio.run(test())
        except (AssertionError, AttributeError) as exc:
            # AssertionError: contract/stage violation. AttributeError:
            # data-model violation (expected attribute missing).
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
            continue
        print(f"PASS  {test.__name__}")
    if failures:
        print(f"\nFAILED: {failures}/{len(tests)} config flow walkthrough tests")
        sys.exit(1)
    print(f"\nPASSED: {len(tests)}/{len(tests)} config flow walkthrough tests")


if __name__ == "__main__":
    main()