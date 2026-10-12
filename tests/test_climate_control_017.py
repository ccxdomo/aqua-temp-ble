#!/usr/bin/env python3
"""Spec 017 tests — the controlled climate: the activation gate, the
client rail, the confirmation cycle and the climate surface
(FR-1..FR-8, AC-1..AC-9, AC-11).

Part A (standalone modules): the AC-1 funnel parity (the classic
funnel, the control builder and registers' pinned strings produce the
SAME proven bytes — one source of truth), the const default
(DEFAULT_WRITE_CONTROL is False — the code ships inert), the guard's
`control_allowed` activation thread (the option surface honored while
the module switch stays the shipped gate) and the coordinator's
activation property (both surfaces OR'd, both-off locked).

Part B (the client seam — the 016 fake-bleak pattern): client.write_
control is fire-and-forget on the SAME _write_frame seam (the pinned
bytes, withoutResponse, app parity) with its own rail — only
(1011, 0|1) passes; (1011, 2), (1012, 1), a read home or a non-power
register raise ValueError BEFORE a byte goes out; not connected
raises AquaTempConnectionError; a GATT failure is wrapped, never
silent.

Part C (coordinator, synthetic-package import — the 016 scripted-
client pattern): the confirmation cycle's verdict matrix — gate
closed (the 016 message, ZERO I/O before the guard), the value
discipline through the funnel, confirmed (the FR-4 audit + verdict
INFO lines EXACT; the snapshot pushed reflects the MIRROR; ONE
trigger; no disconnect), the freshness gate (a STALE capture never
confirms), the mismatch (WARNING names both values; the tile shows
the MIRROR's truth; an OFF raises the persistent notification), the
no-report (WARNING; NO snapshot push; the unconfirmed-OFF persistent
notification created and dismissed by a later confirmed cycle), the
write error (propagates; ZERO post-write reads; no notification) and
the trigger failure (propagates, its WARNING names the confirm
context). The probe's own trigger lines keep their exact 016 strings
after the _trigger_report refactor.

Part D (the climate surface, AC-3/4/5): locked = the 015 read-only
surface byte-identical; the option path = [OFF, HEAT], TURN_ON|TURN_
OFF features (never TARGET_TEMPERATURE), the commands wired to the
cycle, COOL/AUTO refused (heat-only), set_temperature keeps the 015
refusal, the diagnostics attributes (write_control + the last
verdict); the switch path opens the same surface; the END-TO-END
command over the REAL client records EXACTLY the pinned frames on
the fake write char, withoutResponse (AC-4's byte-level demand);
write-path errors propagate (never optimistic).

Part E (the option's declared surface): strings.json + both
translations declare the write_control label + description naming
the required verdict; the options flow schema carries the checkbox
(default False, push-mode only, the save merge).

Red-first on d9d7632 (0.12.0): none of the 017 surfaces exist —
DEFAULT_WRITE_CONTROL, the guard's control_allowed thread,
client.write_control, the coordinator's activation property and
confirmation cycle, the climate's gate-aware surface and the
options-flow checkbox — so every test below fails until the
implementation lands.

Run standalone:

    python3 tests/test_climate_control_017.py

No real Bluetooth and no real Home Assistant required (fake modules
are installed when the real ones are absent).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import sys
import time
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

    class _FakeBleakClient:  # pragma: no cover — placeholder
        is_connected = False

    class _FakeBleakScanner:  # pragma: no cover — placeholder
        pass

    bleak.BleakClient = _FakeBleakClient
    bleak.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = bleak

import aqua_client  # noqa: E402
import const  # noqa: E402
import modbus  # noqa: E402
import registers  # noqa: E402

ADDRESS = "08:06:A6:C0:94:1C"
DOMAIN = "aqua_temp_ble"
PKG = "aqua_temp_ble_control_017"  # synthetic package (distinct from siblings)
UUID_WRITE = const.UUID_WRITE
POWER_ON_HEX = registers.WRITE_FRAME_POWER_ON
POWER_OFF_HEX = registers.WRITE_FRAME_POWER_OFF
NOTIFICATION_ID = f"aqua_temp_ble_power_unconfirmed_{ADDRESS}"

# The pushed-plane test vectors (the owner's 2026-10-12 diff captures —
# the 015 suite's baseline): the 2001 control block in both toggle
# states + the calibrated T-window.
CONTROL_BLOCK_ON = {2010: 2001, 2011: 1, 2012: 1, 2017: 13, 2018: 168, 2019: 4}
CONTROL_BLOCK_OFF = {2010: 2001, 2011: 0, 2012: 1, 2017: 13, 2018: 168, 2019: 0}
T_WINDOW_RAWS = {2046: 32765, 2047: 130, 2048: 125, 2049: 125, 2050: 125, 2051: 135}


@contextlib.contextmanager
def expect_raises(exc_type: type[BaseException], *substrs: str):
    """Assert the block raises exc_type (optionally containing substrs)."""
    try:
        yield
    except exc_type as err:
        for substr in substrs:
            assert substr in str(err), f"{substr!r} not in {err!r}"
    else:
        raise AssertionError(f"expected {exc_type.__name__} was not raised")


@contextlib.contextmanager
def _patch_attr(obj: object, name: str, value):
    """Patch obj.name for the duration of the block (restore after)."""
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


# ===========================================================================
# Part A — the funnel parity, the const default, the guard thread (AC-1/T1)
# ===========================================================================


def test_funnel_parity_pinned_frames() -> None:
    """AC-1/NFR-2: the classic funnel, the control builder and
    registers' pinned strings produce the SAME proven bytes — the
    frames that reach the wire are provably the owner-proven pair."""
    on = bytes.fromhex(registers.WRITE_FRAME_POWER_ON)
    off = bytes.fromhex(registers.WRITE_FRAME_POWER_OFF)
    assert modbus.build_write_registers(1011, [1]) == modbus.build_write_control(1011, 1) == on
    assert modbus.build_write_registers(1011, [0]) == modbus.build_write_control(1011, 0) == off
    assert len(on) == 11 and len(off) == 11
    assert modbus.validate_crc(on) and modbus.validate_crc(off)


def test_default_write_control_ships_inert() -> None:
    """FR-1/AC-10: the shipped default is False — the integration stays
    byte-identical to 0.12.0 until the owner's explicit act."""
    assert const.DEFAULT_WRITE_CONTROL is False


def test_guard_control_allowed_thread() -> None:
    """FR-3 step 1 (the activation's funnel rule): `control_allowed`
    threads the coordinator's OR-property into the guard — the OPTION
    surface honors (1011, 0|1) while the module switch stays the
    shipped default for every other caller; the value allowlist and the
    base scope are untouched."""
    assert registers.WRITE_CONTROL_PROVEN is False  # the shipped gate
    # the default (no thread) keeps the 016 behavior: the switch alone
    with expect_raises(ValueError, "WRITE_CONTROL_PROVEN"):
        registers.ensure_writable(1011, 1)
    # the activation (either surface) opens exactly (1011, 0|1)
    registers.ensure_writable(1011, 0, control_allowed=True)  # must not raise
    registers.ensure_writable(1011, 1, control_allowed=True)  # must not raise
    with expect_raises(ValueError, "accepts only values"):
        registers.ensure_writable(1011, 2, control_allowed=True)
    with expect_raises(ValueError, "WRITE_CONTROL_PROVEN"):
        registers.ensure_writable(1011, 1, control_allowed=False)
    # the base scope ignores the thread entirely (byte-identical rules)
    registers.ensure_writable(8, 1, control_allowed=False)  # must not raise
    registers.ensure_writable(14, 325, control_allowed=False)  # must not raise
    with expect_raises(ValueError, "spec 003"):
        registers.ensure_writable(15, 1, control_allowed=True)
    with expect_raises(ValueError, "spec 003"):
        registers.ensure_writable(1012, 1, control_allowed=True)
    # a read home is never writable — even with the activation on
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(2011, 0, control_allowed=True)


def test_write_control_gate_property_paths() -> None:
    """FR-1/AC-4/AC-5: the activation property — the per-entry option
    OR the global registers switch; both off = locked."""
    pkg_regs = sys.modules[f"{PKG}.registers"]
    closed = _make_coordinator(options={})
    option = _make_coordinator(options={"write_control": True})
    assert closed.write_control_allowed is False
    assert option.write_control_allowed is True
    with _patch_attr(pkg_regs, "WRITE_CONTROL_PROVEN", True):
        assert closed.write_control_allowed is True  # the switch ORs
        assert option.write_control_allowed is True  # both on
    assert pkg_regs.WRITE_CONTROL_PROVEN is False  # restored
    assert registers.WRITE_CONTROL_PROVEN is False  # the standalone module untouched
    assert coordinator_mod.registers is not registers  # separate instances


# ===========================================================================
# Parts B/C/D/E — synthetic-package imports (the 015/016 pattern)
# ===========================================================================

# The stub classes are defined HERE and attached to the stub modules so
# every imported platform binds the SAME objects (the exception/enum
# identity the tests rely on, the 015 file's isolation rationale).


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


class HomeAssistantError(Exception):
    pass


class UpdateFailed(Exception):
    pass


class HomeAssistant:  # pragma: no cover — placeholder
    pass


class UnitOfTemperature:
    CELSIUS = "°C"
    FAHRENHEIT = "°F"


class ConfigEntry:  # pragma: no cover — typing only
    pass


class AddEntitiesCallback:  # pragma: no cover — typing only
    pass


class TemperatureConverter:
    @staticmethod
    def convert(value, _from_unit, _to_unit):  # identity — tests are Celsius
        return value


class DataUpdateCoordinator:
    """The 016 stub + async_set_updated_data (the 017 cycle's snapshot
    push — recorded for the AC-6/AC-8 assertions)."""

    def __init__(self, hass, logger, name=None, update_interval=None,
                 config_entry=None, always_update=False, **_kwargs):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.always_update = always_update
        self.data = None
        self.pushed_snapshots = []

    def async_request_refresh(self):
        return asyncio.sleep(0)  # an awaitable no-op (the debounced read)

    async def async_set_updated_data(self, data):
        self.data = data
        self.pushed_snapshots.append(data)

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
    coordinator.py + climate.py + config_flow.py (the 016 pattern
    extended with the config-flow stubs the options-flow schema test
    needs: a marker-carrying voluptuous stand-in, the flow result
    helpers and the bluetooth/translation lazy imports)."""
    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_climate.HVACAction = HVACAction
    ha_climate.ClimateEntity = ClimateEntity
    ha_climate.ClimateEntityFeature = ClimateEntityFeature
    ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _bt_async_ble_device_from_address(hass, address, connectable=True):
        history = (
            getattr(hass, "connectable_history", None)
            if connectable
            else getattr(hass, "all_history", None)
        )
        return history.get(address) if history is not None else None

    def _bt_async_discovered_service_info(hass, connectable=True):
        for attr in ("bt_infos", "connectable_infos"):
            if hasattr(hass, attr):
                return list(getattr(hass, attr))
        return []

    def _bt_async_scanner_count(hass, connectable=True):
        return getattr(hass, "bt_scanner_count", 0)

    ha_bt.async_ble_device_from_address = _bt_async_ble_device_from_address
    ha_bt.async_discovered_service_info = _bt_async_discovered_service_info
    ha_bt.async_scanner_count = _bt_async_scanner_count
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.UnitOfTemperature = UnitOfTemperature
    ha_const.ATTR_TEMPERATURE = "temperature"
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = HomeAssistant

    class _StubConfigFlow:
        """Functional stand-in: accepts the domain class kwarg (the
        real ConfigFlow.__init_subclass__ shape) + the result helpers
        the flow's construction needs at import time."""

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()
            cls.handler = kwargs.get("domain")

    class _StubOptionsFlow:
        def async_show_form(self, *, step_id, data_schema=None, errors=None,
                            description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_create_entry(self, *, title=None, data=None):
            return {"type": "create_entry", "title": title, "data": data}

    ha_cfg = types.ModuleType("homeassistant.config_entries")
    ha_cfg.ConfigFlow = _StubConfigFlow
    ha_cfg.ConfigFlowResult = dict
    ha_cfg.OptionsFlow = _StubOptionsFlow
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
    ha_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_ep.AddEntitiesCallback = AddEntitiesCallback
    ha_util = types.ModuleType("homeassistant.util")
    ha_uc_conv = types.ModuleType("homeassistant.util.unit_conversion")
    ha_uc_conv.TemperatureConverter = TemperatureConverter
    ha_translation = types.ModuleType("homeassistant.helpers.translation")

    async def _tr_async_get_translations(hass, language, category, integrations,
                                         *, config_flow=False):
        return {}

    ha_translation.async_get_translations = _tr_async_get_translations

    ha.components = ha_components
    ha_components.climate = ha_climate
    ha_components.bluetooth = ha_bt
    ha.util = ha_util
    ha_helpers.update_coordinator = ha_uc
    ha_helpers.device_registry = ha_dr
    ha_helpers.entity_platform = ha_ep
    ha_helpers.translation = ha_translation

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.climate": ha_climate,
        "homeassistant.components.bluetooth": ha_bt,
        "homeassistant.const": ha_const,
        "homeassistant.core": ha_core,
        "homeassistant.config_entries": ha_cfg,
        "homeassistant.exceptions": ha_exc,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
        "homeassistant.helpers.device_registry": ha_dr,
        "homeassistant.helpers.entity_platform": ha_ep,
        "homeassistant.helpers.translation": ha_translation,
        "homeassistant.util": ha_util,
        "homeassistant.util.unit_conversion": ha_uc_conv,
    }.items():
        sys.modules[name] = module

    # voluptuous — the marker stand-in (the 011 options-flow pattern):
    # Required keeps .key/.kwargs so the schema is introspectable.
    vol_mod = types.ModuleType("voluptuous")

    class _VolMarker:
        def __init__(self, key, **kwargs):
            self.key = key
            self.kwargs = kwargs

    vol_mod.Required = lambda key, **kwargs: _VolMarker(key, **kwargs)
    vol_mod.Optional = lambda key, **kwargs: _VolMarker(key, **kwargs)
    vol_mod.Schema = lambda schema: schema
    vol_mod.In = lambda container: container
    vol_mod.All = lambda *args: args
    vol_mod.Coerce = lambda type_: type_
    vol_mod.Range = lambda **kwargs: kwargs
    sys.modules["voluptuous"] = vol_mod


def _import_pkg_module(name: str):
    """Import a component module under the synthetic package (the 016
    pattern — one stub set, every platform binding the same objects).
    A failed exec is removed from sys.modules so a later (green) run
    re-imports cleanly."""
    modname = f"{PKG}.{name}"
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, COMPONENT_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[modname]
        raise
    return module


_install_stubs()
if PKG not in sys.modules:
    _pkg = types.ModuleType(PKG)
    _pkg.__path__ = [str(COMPONENT_DIR)]  # supports `from . import x`
    sys.modules[PKG] = _pkg
coordinator_mod = _import_pkg_module("coordinator")
climate_mod = _import_pkg_module("climate")
# The config-flow import is guarded: in the RED state the 017 const
# surface (DEFAULT_WRITE_CONTROL) is missing and the ImportError must
# fail the schema TEST, not the whole file's collection.
_CONFIG_FLOW_IMPORT_ERROR: Exception | None = None
try:
    config_flow_mod = _import_pkg_module("config_flow")
except ImportError as _exc:
    config_flow_mod = None
    _CONFIG_FLOW_IMPORT_ERROR = _exc
pkg_aqua_client = sys.modules[f"{PKG}.aqua_client"]
pkg_const = sys.modules[f"{PKG}.const"]


def _make_coordinator(client=None, options=None):
    entry = SimpleNamespace(
        data={"address": ADDRESS, "mode": "push"},
        options=dict(options or {}),
    )
    return coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=client or ScriptedControlClient()
    )


def _open_options() -> dict:
    """The open-gate option set: the 015 control-confirmation gate +
    the 017 write-control activation."""
    return {
        "push_control_confirmed": True,
        "push_temp_confirmed": True,
        "write_control": True,
    }


def _ns_capture(raws: dict[int, int], *, last_seen: float | None = None) -> SimpleNamespace:
    """A duck-typed PushCapture for the 2001 block (the 016 pattern)."""
    ts = time.monotonic() if last_seen is None else last_seen
    return SimpleNamespace(
        start=2001, qty=45, length=99, registers=dict(raws),
        first_seen=ts, last_seen=ts, count=1,
    )


def _push_snapshot(*, power: bool):
    caps = {
        2001: _ns_capture(CONTROL_BLOCK_ON if power else CONTROL_BLOCK_OFF),
        2046: _ns_capture(T_WINDOW_RAWS),
    }
    return coordinator_mod.AquaTempData.from_pushes(caps, True, True)


class _LogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


@contextlib.contextmanager
def _control_logs():
    cap = _LogCap()
    logger = coordinator_mod._LOGGER
    old_level = logger.level
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    try:
        yield cap
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)


class _PNRecorder:
    """Records persistent_notification.create/dismiss calls."""

    def __init__(self) -> None:
        self.created: list[tuple[str | None, str]] = []
        self.dismissed: list[str | None] = []


def _attach_pn() -> _PNRecorder:
    """Install a recording persistent_notification stub into the LIVE
    sys.modules (run-time attach: the sibling suites replace the stub
    modules at their own collection, so the coordinator's lazy import
    must resolve against whatever is installed NOW)."""
    recorder = _PNRecorder()
    mod = types.ModuleType("homeassistant.components.persistent_notification")

    def _async_create(hass, message, title=None, notification_id=None):
        recorder.created.append((notification_id, message))

    def _async_dismiss(hass, notification_id):
        recorder.dismissed.append(notification_id)

    mod.async_create = _async_create
    mod.async_dismiss = _async_dismiss
    sys.modules["homeassistant.components.persistent_notification"] = mod
    sys.modules["homeassistant.components"].persistent_notification = mod
    return recorder


# ===========================================================================
# Part B — the client rail (AC-2, FR-2) — the real client over the fake
# ===========================================================================


class _FakeGattChar:
    """GATT characteristic stand-in (answers every UUID lookup)."""

    def __init__(self, props):
        self.properties = list(props)


class _FakeServices:
    def __init__(self, char=None):
        self._char = char

    def get_characteristic(self, _uuid):
        return self._char


class RecordingBleakClient:
    """Fake connected bleak client: records (uuid, data, response)."""

    def __init__(self) -> None:
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool]] = []
        self.services = _FakeServices(
            _FakeGattChar(["write-without-response", "write"])
        )

    async def write_gatt_char(self, uuid, data, response=True) -> None:
        self.writes.append((str(uuid), bytes(data), bool(response)))

    async def start_notify(self, _uuid, _handler) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        self.is_connected = False


def _make_connected_client(fake: RecordingBleakClient) -> aqua_client.AquaTempBleClient:
    client = aqua_client.AquaTempBleClient(ADDRESS)
    client._client = fake
    client._write_with_response = False  # the production ff03 mode
    return client


def test_control_send_records_pinned_frames() -> None:
    """FR-2/AC-4's byte level: write_control is fire-and-forget on the
    SAME _write_frame seam — the EXACT pinned bytes, withoutResponse
    (app parity), qty/bytecount per the disassembled shape."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    asyncio.run(client.write_control(1011, 1))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(POWER_ON_HEX), False)], fake.writes
    fake.writes.clear()
    asyncio.run(client.write_control(1011, 0))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(POWER_OFF_HEX), False)], fake.writes


def test_control_rail_refuses_before_any_byte() -> None:
    """AC-2: the client-side rail — (1011, 2), a non-power register and
    a read home raise ValueError with ZERO wire writes (defense in
    depth under the funnel guard; the pinned frames only)."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    for address, value in ((1011, 2), (1011, -1), (1012, 1), (2011, 0), (1076, 1), (1011, None)):
        with expect_raises(ValueError, "proven power frames"):
            asyncio.run(client.write_control(address, value))
    assert fake.writes == [], "a refused control write must never reach the wire"


def test_control_send_requires_connection() -> None:
    """FR-2: not connected raises AquaTempConnectionError (the
    fire-and-forget path's existing precondition) — zero writes."""
    client = aqua_client.AquaTempBleClient(ADDRESS)  # never connected
    with expect_raises(aqua_client.AquaTempConnectionError, "not connected"):
        asyncio.run(client.write_control(1011, 1))


def test_control_send_wraps_gatt_failure() -> None:
    """FR-2: a GATT failure is wrapped in AquaTempConnectionError with
    the cause chained (the write_registers fire-and-forget pattern) —
    never silent."""
    class _RaisingFake(RecordingBleakClient):
        async def write_gatt_char(self, uuid, data, response=True):
            raise RuntimeError("device disconnected mid-write")

    client = _make_connected_client(_RaisingFake())
    with expect_raises(aqua_client.AquaTempConnectionError, "GATT write failed"):
        asyncio.run(client.write_control(1011, 1))


# ===========================================================================
# Part C — the confirmation cycle (AC-6/7/8/9, FR-3/FR-4)
# ===========================================================================


class ScriptedControlClient:
    """Duck-typed client driving the confirmation cycle's verdict paths.

    mirrors: the NEXT mirror value is delivered as a FRESH 2001 capture
    on each trigger read (C-3's world: the write does not wake the
    report, the TRIGGER does); a None entry delivers nothing (a silent
    window). write_errors: per-write exceptions (a raising write never
    delivers — exactly like a refused GATT write). trigger_error: an
    AquaTempError the trigger read raises instead of the expected
    timeout (the honest transport failure that must propagate).
    """

    def __init__(self, mirrors=None, *, write_errors=None, trigger_error=None,
                 is_connected: bool = True) -> None:
        self.mirrors = list(mirrors or [])
        self.write_errors = list(write_errors or [])
        self.trigger_error = trigger_error
        self.is_connected = is_connected
        self.connect_kwargs: dict | None = None
        self.disconnected = False
        self.read_calls = 0
        self.control_writes: list[tuple[int, int]] = []
        self.probe_writes: list[bytes] = []
        self.funnel_writes: list[tuple[int, list[int]]] = []
        self._store: dict[int, SimpleNamespace] = {}

    def _deliver(self) -> None:
        if not self.mirrors:
            return
        value = self.mirrors.pop(0)
        if value is None:
            return  # a silent window — nothing fresh arrives
        self._store[2001] = _ns_capture(
            {2011: value, 2019: 4 if value else 0}
        )

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls += 1
        if self.trigger_error is not None:
            raise self.trigger_error
        self._deliver()
        raise pkg_aqua_client.AquaTempTimeoutError(
            "no matching response within 8.0s (received 4 frame(s) / 396 bytes)"
        )

    async def write_control(self, address, value):
        self.control_writes.append((address, value))
        if len(self.control_writes) <= len(self.write_errors):
            error = self.write_errors[len(self.control_writes) - 1]
            if error is not None:
                raise error

    async def write_probe_frame(self, frame: bytes) -> None:
        self.probe_writes.append(bytes(frame))

    async def write_registers(self, address, values):
        self.funnel_writes.append((address, list(values)))
        return None

    @property
    def pushed_blocks(self) -> dict[int, SimpleNamespace]:
        return dict(self._store)

    async def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        self.is_connected = True

    async def disconnect(self):
        self.disconnected = True


def _run_control(mirrors, *, value, write_errors=None, trigger_error=None,
                 is_connected=True, options=None, pre_stale=False):
    """Run ONE confirmation cycle over a scripted client; return
    (client, coordinator, cap, result). The fresh-mirror window is
    patched to 0.15 s: the scripted deliveries land inside the trigger
    call, so only the silent-window tests ever see the window elapse."""
    client = ScriptedControlClient(
        mirrors, write_errors=write_errors, trigger_error=trigger_error,
        is_connected=is_connected,
    )
    if pre_stale:
        ts = time.monotonic() - 1000.0
        client._store[2001] = _ns_capture({2011: 1, 2019: 4}, last_seen=ts)
    coordinator = _make_coordinator(client, options=options)
    coordinator.data = None
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _control_logs() as cap:
            result = asyncio.run(coordinator.async_control_write_power(value))
    return client, coordinator, cap, result


def test_cycle_gate_closed_raises_before_io() -> None:
    """AC-3/AC-10: with both activation surfaces off the cycle refuses
    with the 016 message BEFORE any connect, lock or I/O."""
    client = ScriptedControlClient()
    coordinator = _make_coordinator(client, options={})
    with expect_raises(ValueError, "WRITE_CONTROL_PROVEN"):
        asyncio.run(coordinator.async_control_write_power(1))
    assert client.read_calls == 0
    assert client.control_writes == []
    assert client.connect_kwargs is None, "the guard fires BEFORE the connect"


def test_cycle_value_discipline_zero_io() -> None:
    """FR-3 step 1's value half: with the activation on, value 2 raises
    the funnel's value-allowlist message with ZERO client I/O."""
    client = ScriptedControlClient()
    coordinator = _make_coordinator(client, options=_open_options())
    with expect_raises(ValueError, "accepts only values"):
        asyncio.run(coordinator.async_control_write_power(2))
    assert client.read_calls == 0
    assert client.control_writes == []


def test_cycle_confirmed_pushes_mirror_snapshot() -> None:
    """AC-6: a fresh capture with the mirror following -> the confirmed
    INFO verdict (FR-4's exact lines), the snapshot pushed reflects the
    MIRROR, ONE trigger, NO disconnect on completion."""
    pn = _attach_pn()
    client, coordinator, cap, result = _run_control([1], value=1, options=_open_options())
    assert result["verdict"] == "confirmed", result
    assert result["value"] == 1
    assert result["mirror_raw"] == 1
    assert result["mirror_age_s"] is not None and result["mirror_age_s"] >= 0.0
    infos = [m for lvl, m in cap.records if lvl == logging.INFO]
    assert "power write via 1011 = 1 — mirror should follow in the next report" in infos, infos
    assert "power write confirmed by mirror (2011 = 1)" in infos, infos
    # the snapshot push: the entity plane reflects the MIRROR
    assert len(coordinator.pushed_snapshots) == 1
    assert coordinator.data.power is True
    assert coordinator.data.hvac_mode == HVACMode.HEAT
    # the wire discipline: exactly ONE control write + ONE trigger
    assert client.control_writes == [(1011, 1)]
    assert client.read_calls == 1
    assert client.funnel_writes == []  # never the classic funnel
    assert not client.disconnected, "the confirmation must NOT disconnect (FR-3 step 8)"
    assert client.connect_kwargs is None  # already connected: no connect call
    # a confirmed cycle dismisses any stale notification (idempotent)
    assert pn.created == []
    assert pn.dismissed == [NOTIFICATION_ID]


def test_cycle_connects_when_disconnected() -> None:
    """FR-3 step 2: the cycle connects when needed, threading the
    pair_on_connect option exactly like the poll path."""
    client, _coord, _cap, result = _run_control(
        [1], value=1, is_connected=False, options=_open_options()
    )
    assert result["verdict"] == "confirmed"
    assert client.connect_kwargs == {"pair": False}, client.connect_kwargs


def test_cycle_mismatch_shows_mirror_and_notifies_off() -> None:
    """AC-7: the fresh mirror disagrees -> the WARNING verdict names
    BOTH values; the tile shows the MIRROR's truth (never the request);
    a contradicted OFF raises the persistent notification (§5.5)."""
    pn = _attach_pn()
    client, coordinator, cap, result = _run_control([1], value=0, options=_open_options())
    assert result["verdict"] == "not-confirmed", result
    assert result["mirror_raw"] == 1 and result["value"] == 0
    warns = [
        m for lvl, m in cap.records
        if lvl == logging.WARNING and m.startswith("power write not confirmed")
    ]
    assert len(warns) == 1, warns
    assert "mirror 2011 = 1" in warns[0] and "requested 0" in warns[0], warns[0]
    # the snapshot push carries the MIRROR's state: OFF was requested,
    # the unit reported ON — the tile must say ON
    assert len(coordinator.pushed_snapshots) == 1
    assert coordinator.data.power is True
    assert coordinator.data.hvac_mode == HVACMode.HEAT
    # the contradicted OFF is never silent
    assert len(pn.created) == 1, pn.created
    notification_id, message = pn.created[0]
    assert notification_id == NOTIFICATION_ID
    assert ADDRESS in message, message
    assert "OFF" in message, message
    assert (
        "check the panel; the unit may still be ON — turn it OFF from the "
        "vendor app or the panel" in message
    ), message
    assert pn.dismissed == []


def test_cycle_no_report_unconfirmed_off_notification() -> None:
    """AC-8: no fresh capture in the window -> the WARNING unconfirmed
    line, NO snapshot push (the state stays), the persistent
    notification for the OFF — and a later CONFIRMED cycle dismisses it."""
    pn = _attach_pn()
    client, coordinator, cap, result = _run_control([None], value=0, options=_open_options())
    assert result["verdict"] == "unconfirmed", result
    assert result["mirror_raw"] is None
    warns = [
        m for lvl, m in cap.records
        if lvl == logging.WARNING and m.startswith("power write unconfirmed")
    ]
    assert len(warns) == 1, warns
    assert "no fresh report within" in warns[0], warns[0]
    assert "the unit's state is UNKNOWN: check the panel" in warns[0], warns[0]
    # NO snapshot push: the coordinator data is untouched
    assert coordinator.pushed_snapshots == []
    assert coordinator.data is None
    # the unconfirmed-OFF surface (FR-3 step 7)
    assert len(pn.created) == 1, pn.created
    assert pn.created[0][0] == NOTIFICATION_ID
    assert "check the panel" in pn.created[0][1]
    # a later CONFIRMED cycle for the same unit dismisses it
    _client2, _coord2, _cap2, result2 = _run_control([0], value=0, options=_open_options())
    assert result2["verdict"] == "confirmed"
    assert pn.dismissed == [NOTIFICATION_ID], pn.dismissed


def test_cycle_stale_capture_never_confirms() -> None:
    """AC-6's freshness gate: a capture with last_seen <= t_write NEVER
    satisfies the wait — a stale mirror cannot confirm a write."""
    pn = _attach_pn()
    client, coordinator, cap, result = _run_control(
        [], value=1, pre_stale=True, options=_open_options()
    )
    assert result["verdict"] == "unconfirmed", result
    assert result["mirror_raw"] is None  # the stale capture was refused
    assert coordinator.pushed_snapshots == []
    assert pn.created == []  # value 1: no unconfirmed-OFF surface
    warns = [m for lvl, m in cap.records if lvl == logging.WARNING]
    assert any(
        m.startswith("power write unconfirmed (no fresh report within") for m in warns
    ), warns


def test_cycle_write_error_propagates_zero_reads() -> None:
    """AC-9: a raising write propagates to the caller — NO trigger, NO
    cross-check, NO snapshot push, NO notification."""
    pn = _attach_pn()
    client = ScriptedControlClient([], write_errors=[RuntimeError("link down")])
    coordinator = _make_coordinator(client, options=_open_options())
    coordinator.data = None
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _control_logs():
            with expect_raises(RuntimeError, "link down"):
                asyncio.run(coordinator.async_control_write_power(1))
    assert client.control_writes == [(1011, 1)]
    assert client.read_calls == 0, "a raising write must not trigger a read (AC-9)"
    assert coordinator.pushed_snapshots == []
    assert pn.created == []
    assert not client.disconnected


def test_cycle_trigger_failure_propagates() -> None:
    """FR-3/FR-4: a non-timeout trigger failure (an honest transport
    failure) logs its WARNING naming the confirm context and
    propagates — the write itself already went out."""
    trigger_error = pkg_aqua_client.AquaTempError("trigger transport failed")
    client = ScriptedControlClient([], trigger_error=trigger_error)
    coordinator = _make_coordinator(client, options=_open_options())
    with _control_logs() as cap:
        with expect_raises(pkg_aqua_client.AquaTempError, "trigger transport failed"):
            asyncio.run(coordinator.async_control_write_power(1))
    warns = [m for lvl, m in cap.records if lvl == logging.WARNING]
    assert any(
        m.startswith(f"power write confirm [{ADDRESS}]: trigger read failed") for m in warns
    ), warns
    assert client.control_writes == [(1011, 1)]


def test_probe_trigger_lines_unchanged_after_refactor() -> None:
    """T3's refactor pin: after _probe_trigger becomes _trigger_report
    (context), the PROBE's own trigger lines keep their exact 016
    strings — its DEBUG expected-timeout shape carries the probe's
    context and address, and the probe still recycles the link."""
    client = ScriptedControlClient([])  # silent baseline -> no-report
    coordinator = _make_coordinator(client, options={})
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _control_logs() as cap:
            result = asyncio.run(coordinator.async_power_write_probe())
    assert result["verdict"] == "no-report", result
    debug_lines = [m for _lvl, m in cap.records if "trigger timed out as expected" in m]
    assert debug_lines, cap.records
    assert debug_lines[0].startswith(f"power write probe [{ADDRESS}]"), debug_lines[0]
    assert "spec 014 M-4" in debug_lines[0], debug_lines[0]
    assert client.disconnected, "the probe still disconnects on completion"


# ===========================================================================
# Part D — the climate surface (AC-3/4/5, FR-5/FR-6/FR-8)
# ===========================================================================


def _make_platform(options, snapshot, client=None):
    entry = SimpleNamespace(
        entry_id="t17",
        title="Aqua Temp test",
        data={"address": ADDRESS, "mode": "push"},
        options=dict(options),
    )
    coordinator = coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=client or ScriptedControlClient()
    )
    coordinator.data = snapshot
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})
    return hass, entry, coordinator


def _added_entity(hass, entry):
    added: list = []

    def _collect(entities) -> None:
        added.extend(entities)

    asyncio.run(climate_mod.async_setup_entry(hass, entry, _collect))
    assert len(added) == 1, [type(e).__name__ for e in added]
    return added[0]


def test_climate_locked_surface_byte_identical() -> None:
    """AC-3: with both activation surfaces off the push-mode climate is
    the 015 read-only surface, byte-identical — no modes, no features,
    no diagnostics attributes, every write refused with the 015
    message, ZERO client I/O."""
    snapshot = _push_snapshot(power=True)
    client = ScriptedControlClient()
    hass, entry, _coord = _make_platform({"push_control_confirmed": True}, snapshot, client)
    entity = _added_entity(hass, entry)
    assert entity._attr_hvac_modes == []  # the 015 assertion still holds
    assert entity.hvac_modes == []
    assert entity.supported_features == ClimateEntityFeature(0)
    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    assert entity.extra_state_attributes is None
    for call in (
        lambda: entity.async_turn_on(),
        lambda: entity.async_turn_off(),
        lambda: entity.async_set_hvac_mode(HVACMode.HEAT),
        lambda: entity.async_set_temperature(temperature=20.0),
    ):
        with expect_raises(HomeAssistantError, "read-only"):
            asyncio.run(call())
    assert client.control_writes == []
    assert client.read_calls == 0
    assert client.funnel_writes == []


def test_climate_option_path_full_surface() -> None:
    """AC-4: the option path — hvac_modes [OFF, HEAT], the TURN_ON |
    TURN_OFF features (never TARGET_TEMPERATURE), the commands wired
    to the confirmation cycle, COOL/AUTO refused (heat-only),
    set_temperature keeps the 015 refusal, the diagnostics attributes
    carry the gate + the last verdict."""
    snapshot = _push_snapshot(power=False)
    client = ScriptedControlClient([1, 1, 0, 0])
    hass, entry, coordinator = _make_platform(
        {"push_control_confirmed": True, "write_control": True}, snapshot, client
    )
    entity = _added_entity(hass, entry)
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert entity.supported_features == (
        ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    attrs = entity.extra_state_attributes
    assert attrs is not None and attrs["write_control"] == "allowed", attrs
    # the commands (each consumes one scripted mirror)
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        asyncio.run(entity.async_turn_on())
        asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))
        asyncio.run(entity.async_turn_off())
        asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
    assert client.control_writes == [(1011, 1), (1011, 1), (1011, 0), (1011, 0)], (
        client.control_writes
    )
    assert client.funnel_writes == []  # never the classic funnel
    # COOL/AUTO are unsupported (heat-only — the pair-write exclusion)
    with expect_raises(HomeAssistantError, "heat-only"):
        asyncio.run(entity.async_set_hvac_mode(HVACMode.COOL))
    with expect_raises(HomeAssistantError, "heat-only"):
        asyncio.run(entity.async_set_hvac_mode(HVACMode.AUTO))
    # set_temperature keeps the 015 refusal (no setpoint surface, FR-8)
    with expect_raises(HomeAssistantError, "read-only"):
        asyncio.run(entity.async_set_temperature(temperature=20.0))
    # the diagnostics surface the last write verdict (FR-3 step 9's dict)
    attrs2 = entity.extra_state_attributes
    assert attrs2["last_power_write"]["verdict"] == "confirmed", attrs2
    assert attrs2["last_power_write"]["value"] == 0
    assert attrs2["last_power_write"]["mirror_raw"] == 0


def test_climate_switch_path_same_surface() -> None:
    """AC-5: the registers switch (test-patched WRITE_CONTROL_PROVEN)
    opens the SAME surface as the option — both activation paths are
    honored."""
    pkg_regs = sys.modules[f"{PKG}.registers"]
    snapshot = _push_snapshot(power=False)
    client = ScriptedControlClient([1])
    hass, entry, coordinator = _make_platform(
        {"push_control_confirmed": True}, snapshot, client
    )
    with _patch_attr(pkg_regs, "WRITE_CONTROL_PROVEN", True):
        entity = _added_entity(hass, entry)
        assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
        with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
            asyncio.run(entity.async_turn_on())
        assert client.control_writes == [(1011, 1)], client.control_writes
    assert pkg_regs.WRITE_CONTROL_PROVEN is False  # restored
    assert registers.WRITE_CONTROL_PROVEN is False  # the standalone module untouched


def test_climate_commands_write_pinned_frames_end_to_end() -> None:
    """AC-4's wire-level demand: over the REAL client, a climate
    command puts EXACTLY the pinned frame bytes on the fake write char
    (withoutResponse) — and the confirmed cycle pushes the mirror
    snapshot, so the tile follows the command."""
    fake = RecordingBleakClient()
    client = pkg_aqua_client.AquaTempBleClient(ADDRESS)
    client._client = fake
    client._write_with_response = False  # the production ff03 mode
    entry = SimpleNamespace(
        entry_id="e2e",
        title="Aqua Temp test",
        data={"address": ADDRESS, "mode": "push"},
        options=dict(_open_options()),
    )
    coordinator = coordinator_mod.AquaTempCoordinator(hass=None, entry=entry, client=client)
    hass = SimpleNamespace(data={DOMAIN: {"e2e": {"coordinator": coordinator}}})
    entity = _added_entity(hass, entry)
    # a FUTURE-stamped capture: fresher than any t_write the cycle
    # stamps, so the confirmation completes instantly on the real
    # client's store (the trigger read itself is bounded by the patched
    # REQUEST_TIMEOUT — the expected timeout world).
    client._pushed[2001] = _ns_capture(
        CONTROL_BLOCK_ON, last_seen=time.monotonic() + 10.0
    )
    with _patch_attr(pkg_const, "REQUEST_TIMEOUT", 0.05), _patch_attr(
        pkg_const, "PROBE_MIRROR_TIMEOUT", 1.0
    ):
        asyncio.run(entity.async_turn_on())
    control_frames = [w for w in fake.writes if len(w[1]) == 11]
    assert control_frames == [(UUID_WRITE, bytes.fromhex(POWER_ON_HEX), False)], fake.writes
    assert coordinator.data.power is True  # the mirror, via the snapshot push
    assert entity.hvac_mode == HVACMode.HEAT
    # the OFF twin: the store now mirrors the OFF state
    client._pushed[2001] = _ns_capture(
        CONTROL_BLOCK_OFF, last_seen=time.monotonic() + 10.0
    )
    fake.writes.clear()
    with _patch_attr(pkg_const, "REQUEST_TIMEOUT", 0.05), _patch_attr(
        pkg_const, "PROBE_MIRROR_TIMEOUT", 1.0
    ):
        asyncio.run(entity.async_turn_off())
    control_frames = [w for w in fake.writes if len(w[1]) == 11]
    assert control_frames == [(UUID_WRITE, bytes.fromhex(POWER_OFF_HEX), False)], fake.writes
    assert coordinator.data.power is False
    assert entity.hvac_mode == HVACMode.OFF


def test_climate_command_error_propagates() -> None:
    """FR-5: write-path exceptions propagate to HA (no optimistic
    fallback) — the entity surfaces the cycle's failure and the
    displayed state stays the last MIRROR's truth."""
    snapshot = _push_snapshot(power=False)
    client = ScriptedControlClient([], write_errors=[RuntimeError("link down")])
    hass, entry, _coord = _make_platform(
        {"push_control_confirmed": True, "write_control": True}, snapshot, client
    )
    entity = _added_entity(hass, entry)
    with expect_raises(RuntimeError, "link down"):
        asyncio.run(entity.async_turn_on())
    assert entity.hvac_mode == HVACMode.OFF  # never optimistic


# ===========================================================================
# Part E — the option's declared surface (T1)
# ===========================================================================


def test_write_control_option_declared_in_strings() -> None:
    """FR-1: the option is labeled + described in strings.json and BOTH
    translations — the description names the required probe verdict
    (the UI carries contract 016 §4's discipline)."""
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text())
    init = strings["options"]["step"]["init"]
    assert "write_control" in init["data"], init["data"].keys()
    assert "write_control" in init.get("data_description", {}), init.keys()
    assert "EXECUTED + REVERSIBLE" in init["data_description"]["write_control"]
    for lang in ("en", "fr"):
        data = json.loads((COMPONENT_DIR / "translations" / f"{lang}.json").read_text())
        init_lang = data["options"]["step"]["init"]
        assert "write_control" in init_lang["data"], (lang, init_lang["data"].keys())
        assert "write_control" in init_lang.get("data_description", {}), (
            lang,
            init_lang.keys(),
        )
        assert "EXECUTED + REVERSIBLE" in init_lang["data_description"]["write_control"], lang


def test_options_flow_schema_has_write_control() -> None:
    """FR-1/T1: the OptionsFlow carries the write_control checkbox —
    default False (the shipped inert default), push-mode only, its
    checked value kept as the default, and the save merge carries it."""
    assert config_flow_mod is not None, (
        f"config_flow import failed: {_CONFIG_FLOW_IMPORT_ERROR}"
    )
    flow = config_flow_mod.AquaTempOptionsFlowHandler()
    # push entry, fresh options -> the checkbox present, default False
    flow.config_entry = SimpleNamespace(options={}, data={"mode": "push"})
    result = asyncio.run(flow.async_step_init(None))
    assert result["type"] == "form" and result["step_id"] == "init", result
    marker = next(
        e for e in result["data_schema"] if getattr(e, "key", None) == "write_control"
    )
    assert marker.kwargs.get("default") is False
    assert config_flow_mod.DEFAULT_WRITE_CONTROL is False
    # an already-enabled entry keeps True as the default
    flow.config_entry = SimpleNamespace(
        options={"write_control": True}, data={"mode": "push"}
    )
    result = asyncio.run(flow.async_step_init(None))
    marker = next(
        e for e in result["data_schema"] if getattr(e, "key", None) == "write_control"
    )
    assert marker.kwargs.get("default") is True
    # the save merge carries the option into the entry's options
    flow.config_entry = SimpleNamespace(options={}, data={"mode": "push"})
    result = asyncio.run(flow.async_step_init({
        "poll_interval": 30,
        "temperature_unit": "C",
        "pair_on_connect": False,
        "push_temp_confirmed": False,
        "push_control_confirmed": False,
        "write_control": True,
    }))
    assert result["type"] == "create_entry", result
    assert result["data"]["write_control"] is True
    # classic entries do not get the checkbox (the push-only precedent)
    flow.config_entry = SimpleNamespace(options={}, data={})
    result = asyncio.run(flow.async_step_init(None))
    assert not any(
        getattr(e, "key", None) == "write_control" for e in result["data_schema"]
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Part A — parity, the const default, the guard thread, the gate
        test_funnel_parity_pinned_frames,
        test_default_write_control_ships_inert,
        test_guard_control_allowed_thread,
        test_write_control_gate_property_paths,
        # Part B — the client rail
        test_control_send_records_pinned_frames,
        test_control_rail_refuses_before_any_byte,
        test_control_send_requires_connection,
        test_control_send_wraps_gatt_failure,
        # Part C — the confirmation cycle
        test_cycle_gate_closed_raises_before_io,
        test_cycle_value_discipline_zero_io,
        test_cycle_confirmed_pushes_mirror_snapshot,
        test_cycle_connects_when_disconnected,
        test_cycle_mismatch_shows_mirror_and_notifies_off,
        test_cycle_no_report_unconfirmed_off_notification,
        test_cycle_stale_capture_never_confirms,
        test_cycle_write_error_propagates_zero_reads,
        test_cycle_trigger_failure_propagates,
        test_probe_trigger_lines_unchanged_after_refactor,
        # Part D — the climate surface
        test_climate_locked_surface_byte_identical,
        test_climate_option_path_full_surface,
        test_climate_switch_path_same_surface,
        test_climate_commands_write_pinned_frames_end_to_end,
        test_climate_command_error_propagates,
        # Part E — the option's declared surface
        test_write_control_option_declared_in_strings,
        test_options_flow_schema_has_write_control,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-017 climate-control tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())