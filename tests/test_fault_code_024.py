#!/usr/bin/env python3
"""Spec 024 tests — the fault-code sensor + the latch (AC-1..AC-10).

The owner's 2026-10-12 follow-up run (research 024 §1) proved the
second face of the fault word: register 2074 is a LATCHED memory —
it kept reading 256 while the machine sat healthy and logically OFF,
and only cutting the unit's power supply cleared it to 0. The same
run re-confirmed the code point (256 -> E03 — a second independent
observation) and surfaced the display ask: the panel CODE on the
tile, where a problem binary can only render its localized on/off
words ("Problematique"/"OK"). Spec 024 therefore ADDS one sensor —
"Fault code": `No fault` / `E03` / `unknown code (raw N)` — the
DISPLAY face of the SAME register the 023 Fault binary reads (one
register, one source, two faces; the binary stays byte-identical,
the automation face). No freshness exemption (contract 024 §5: the
shared cycle gate is already correct for a latched word), no timing
attribute (contract 024 §6: the store's timestamps date the
listening window, never the fault), no write path (contract 024
§7: clearing is a physical act — cut the supply).

Pinned by this file (spec 024 §2's ACs):
- AC-2/AC-3: the state + attributes table (0 -> "No fault" + no
  code attribute; 256 -> "E03" + code/code_label/set_bits [8]; 4 ->
  "unknown code (raw 4)" + [2]; absent register -> None + only the
  register attribute; no snapshot -> None + {}; NO timing key in
  any attributes dict — contract 024 §6);
- AC-4: both byte-exact evidence frames (99 bytes, CRCs 551d/54f0)
  through the REAL store (`client._on_notify`) -> the REAL
  coordinator cycle (`_async_update_data`: the trigger times out,
  the store answers) -> the sensor reads "E03" / "No fault" (and
  `registers.decode_fault_flag` -> True / False — the two faces
  decode the same raw);
- AC-1: a push entry creates exactly ONE
  AquaTempFaultCodeSensorEntity (name "Fault code", unique_id
  <ADDRESS>_pushed_fault_code — DISJOINT from the classic
  fault_code plane, icon mdi:alert-circle, NO device_class /
  entity_category / unit / state_class, the entry's device); the
  six temps + push_stream still created; a classic entry creates
  NONE (law L1 — register 47 unreachable, spec 020 §5);
- AC-5: registers.PUSHED_FAULT_CLEAR_STATE == "No fault";
  fault_code_state pure and total over {None, 0, 256, 4, 8192};
  PUSHED_FAULT_CODE_MAP exactly its ONE proven row (FR-4); the
  forbidden bit names absent from every component source
  (contract 023 §3, carried forward);
- AC-6: the freshness NON-change — no identifier anywhere couples
  the fault with freshness/staleness/age/window, and the fault
  entity path calls no gate directly (the fault entities ride the
  SHARED cycle gate, contract 024 §5);
- AC-7: binary_sensor.py references no fault_code_state (the
  addition is an ADDITION — the byte-identical proof rides the
  T7 git gate; the 023 suite stays green unmodified);
  PLATFORMS unchanged;
- AC-8: the docs state the latch — the README's features/entities/
  troubleshooting rows, CALIBRATION.md §3's rewritten bullet (the
  023 debt cleared); no "turning the unit off clears the fault"
  claim anywhere; the three translation JSONs keep their key
  parity;
- AC-9: the read-only guarantee — the options schema stays exactly
  poll_interval + temperature_unit; the funnel refuses 2074
  forever (even the value 0); no clear-fault surface anywhere;
- AC-10: the manifest reads 0.18.0.

Unavailability while the cycle fails is the shared CoordinatorEntity
behavior (the 013/014 freshness-gate suites pin it) — no
fault-specific code exists for it and none is added (FR-6/contract
024 §5: the gate tests the STREAM's recency, never VALUE movement —
a latched word rides fresh blocks forever).

Red-first on 7ee702b: no fault-code state helper, no sensor class,
no latch docs, manifest 0.17.0 — the new-behavior assertions below
fail until spec 024 lands.

Run standalone:

    python3 tests/test_fault_code_024.py

No real Bluetooth and no real Home Assistant required (the
test_fault_023 synthetic-package harness pattern).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
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
    # Fake bleak (the store-test pattern): the transport seam is
    # faked at the bleak-client level, never used for real.
    _bleak = types.ModuleType("bleak")

    class _FakeBleakClientStub:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScannerStub:  # pragma: no cover - placeholder
        pass

    _bleak.BleakClient = _FakeBleakClientStub
    _bleak.BleakScanner = _FakeBleakScannerStub
    sys.modules["bleak"] = _bleak

ADDRESS = "08:06:A6:C0:94:1C"
PKG = "aqua_temp_ble_fault_code_024"  # synthetic package (distinct from siblings)
DEVICE_TITLE = "Aqua Temp test"  # the fake entries' title (the device name)

# The vendor's ten-flag list (research 023 §2.1) — the candidate
# vocabulary whose ORDER is an [A] hypothesis. NEVER in entity code
# (contract 023 §3); pinned absent by test_fault_code_state_registers.
FORBIDDEN_BIT_NAMES = (
    "Sys1HighPressureSwitch",
    "Sys1LowPressureSwitch",
    "WaterFlowSwitch",
    "EmergencySwitch",
    "ModeSwitch",
    "Master/SlaveSwitch",
    "Sys1CompState",
    "InletWaterTempSensorFault",
    "OutletWaterTempSensorFault",
    "ATSensorFault",
)

# The owner's E03 capture of block 2046, byte-exact (research 023
# §1.2; re-served identically by the 2026-10-12 follow-up run,
# research 024 §1.1) — CRC 0x1D55, 2074 = 0x0100 = 256 (bit 8).
FRAME_2046_E03 = (
    "631007fe002d5a"
    + "7ffd"
    + "00910091009600960096"
    + "00" * 44
    + "0100"                  # 2074: THE FAULT (256 = bit 8)
    + "00" * 32
    + "551d"
)
# The healthy twin (2074 = 0) — also the post-power-cut clear state
# (the latch: only cutting the unit's supply returns this,
# owner-proven 2026-10-12).
FRAME_2046_E03_CLEAR = (
    "631007fe002d5a7ffd00910091009600960096"
    + "00" * 78 + "54f0"
)

# A minimal classic-plane register set (the coordinator-test pattern).
BASE_REGS = {
    7: 0, 8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
    22: 60, 23: 400, 24: 60, 25: 400, 26: 243, 27: -9990, 28: -125,
    47: 0, 48: 2, 49: 2, 50: 1, 51: 2040,
}

# AC-6's grep: an identifier (a contiguous [A-Za-z_0-9] run — prose
# spaces/hyphens deliberately break it) that couples the fault with
# freshness/staleness/age/window in either order — the shape a
# fault-specific gate would necessarily take. Empty today, empty
# after 024 (the NON-change, contract 024 §5).
_FAULTY_GATE_RE = re.compile(
    r"[A-Za-z_][A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*[Ff]resh[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*[Ss]tale[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*[Aa]ge[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*[Ww]indow[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Ff]resh[A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Ss]tale[A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Aa]ge[A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*"
    r"|[A-Za-z_][A-Za-z_0-9]*[Ww]indow[A-Za-z_0-9]*[Ff]ault[A-Za-z_0-9]*"
)


# ---------------------------------------------------------------------------
# The synthetic-package harness (the test_fault_023 pattern)
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — the superset imported by
    sensor.py + entity.py + coordinator.py."""

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

    class CoordinatorEntity:
        """Fake base: keeps the coordinator reference the entities read."""

        def __init__(self, coordinator):
            self.coordinator = coordinator

        def __class_getitem__(cls, _item):  # CoordinatorEntity[X]
            return cls

    class DeviceInfo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class SensorEntity:  # pragma: no cover - attribute container only
        pass

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"

    class ConfigEntry:  # pragma: no cover - typing only
        pass

    class AddEntitiesCallback:  # pragma: no cover - typing only
        pass

    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_sensor = types.ModuleType("homeassistant.components.sensor")
    ha_sensor.SensorEntity = SensorEntity
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.UnitOfTemperature = UnitOfTemperature
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = HomeAssistant
    ha_cfg = types.ModuleType("homeassistant.config_entries")
    ha_cfg.ConfigEntry = ConfigEntry
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

    ha.components = ha_components
    ha_components.climate = ha_climate
    ha_components.sensor = ha_sensor
    ha.helpers = ha_helpers
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
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
        "homeassistant.helpers.device_registry": ha_dr,
        "homeassistant.helpers.entity": ha_ent,
        "homeassistant.helpers.entity_platform": ha_ep,
    }.items():
        sys.modules[name] = module


def _import_pkg_module(name: str):
    """Import a component module under the synthetic package (the
    test_registers_push_013 pattern)."""
    _install_stubs()
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(COMPONENT_DIR)]  # supports `from . import x`
        sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.{name}", COMPONENT_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = module
    spec.loader.exec_module(module)
    return module


const_mod = _import_pkg_module("const")
modbus_mod = _import_pkg_module("modbus")
registers_mod = _import_pkg_module("registers")
aqua_client_mod = _import_pkg_module("aqua_client")
coordinator_mod = _import_pkg_module("coordinator")
sensor_mod = _import_pkg_module("sensor")


# ---------------------------------------------------------------------------
# Fakes: the step clock, the duck-typed capture, the no-IO client and
# the real-store feed (the 013/014/023 patterns)
# ---------------------------------------------------------------------------


class StepClock:
    """Scripted `_now` seam (contract 007 §4): returns .value."""

    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _ClockPatch:
    """Patch a module's `_now` seam for the duration (contract 007 §4)."""

    def __init__(self, module, clock: StepClock) -> None:
        self.module = module
        self.clock = clock
        self._real = module._now

    def __enter__(self) -> StepClock:
        self.module._now = self.clock
        return self.clock

    def __exit__(self, *_exc) -> None:
        self.module._now = self._real


class _ConnectedBleakClient:
    """Minimal stand-in for a connected bleak client (the store tests)."""

    is_connected = True

    async def write_gatt_char(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass


class _FakeClient:
    """Duck-typed client for the platform tests: connected, no I/O."""

    is_connected = True

    async def read_registers(self, start, count, **_kwargs):  # pragma: no cover
        return {}

    async def harvest_pushes(self, wanted=None, timeout=None):  # pragma: no cover
        return {}

    async def connect(self, **_kwargs):  # pragma: no cover
        return None

    async def disconnect(self):  # pragma: no cover
        return None


class FakePushClient:
    """Duck-typed AquaTempBleClient for the REAL push cycle (the
    test_coordinator_push_013 pattern): connected, a scripted store,
    a trigger read that always times out (the expected world)."""

    def __init__(self, captures: dict | None = None) -> None:
        self.is_connected = True
        self.pushed_blocks = dict(captures or {})
        self.read_calls: list[tuple] = []
        self.disconnect_calls: int = 0

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls.append((start, count))
        # The REAL exception class (the synthetic package's) — the
        # coordinator's except clause must catch it (contract 014 §2.2).
        raise aqua_client_mod.AquaTempTimeoutError(
            "no matching response within 8.0s (received 0 frame(s) / "
            "0 bytes; blocks seen: none; none for start 7)"
        )

    async def connect(self, **_kwargs) -> None:  # pragma: no cover
        self.is_connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.is_connected = False

    async def harvest_pushes(self, wanted_starts=None, timeout=None):  # pragma: no cover
        return {}


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


def _feed_real_store(frame_hex: str) -> aqua_client_mod.AquaTempBleClient:
    """Feed one block frame through the REAL client's idle notify path
    (the AC-4 evidence route: `_on_notify` -> the store)."""
    clock = StepClock(1000.0)
    with _ClockPatch(aqua_client_mod, clock):
        client = aqua_client_mod.AquaTempBleClient(ADDRESS)
        client._client = _ConnectedBleakClient()  # mark as connected
        client._t_subscribe = 1000.0  # the connect() stamp (plan 009 §2.2)
        client._on_notify(bytearray(bytes.fromhex(frame_hex)))
    return client


def _cycle(client) -> object:
    """Run ONE real coordinator push cycle (`_async_update_data`) over a
    duck-typed client carrying the real store's captures — the clock
    AT the capture stamp, so the freshness gate passes (age 0)."""
    entry = SimpleNamespace(
        data={"address": ADDRESS, "mode": "push"},
        options={"poll_interval": 30},
    )
    coord = coordinator_mod.AquaTempCoordinator(hass=None, entry=entry, client=client)
    with _ClockPatch(coordinator_mod, StepClock(1000.0)):
        return asyncio.run(coord._async_update_data())


def _push_snapshot(regs: dict[int, int] | None):
    """A push-mode snapshot from a synthetic 2046 capture (`None` = an
    empty store — no usable snapshot)."""
    caps: dict[int, SimpleNamespace] = {}
    if regs is not None:
        caps[2046] = _capture(2046, regs)
    return coordinator_mod.AquaTempData.from_pushes(caps)


def _coord_with(data) -> tuple:
    """A coordinator + entry pair carrying `data` as its snapshot."""
    entry = SimpleNamespace(
        entry_id="test_entry",
        title=DEVICE_TITLE,
        data={"address": ADDRESS, "mode": "push"},
        options={},
    )
    coordinator = coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=_FakeClient()
    )
    coordinator.data = data
    return coordinator, entry


def _fault_code_entity(data):
    """The Fault code sensor wired to a coordinator carrying `data`."""
    coordinator, entry = _coord_with(data)
    return sensor_mod.AquaTempFaultCodeSensorEntity(coordinator, entry)


def _added_sensors(hass, entry) -> list:
    """Run the sensor platform setup; collect the entities."""
    added: list = []

    def _collect(entities) -> None:
        # HA's AddEntitiesCallback is called synchronously by the
        # platform — a plain function, not a coroutine.
        added.extend(entities)

    asyncio.run(sensor_mod.async_setup_entry(hass, entry, _collect))
    return added


def _platform_world(mode: str) -> tuple:
    """A hass + entry pair wired for the platform setup (the
    test_registers_push_013 pattern)."""
    entry = SimpleNamespace(
        entry_id="test_entry",
        title=DEVICE_TITLE,
        data={"address": ADDRESS, "mode": mode},
        options={},
    )
    coordinator = coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=_FakeClient()
    )
    coordinator.data = _push_snapshot({2046: 32765, 2047: 145, 2074: 0})
    hass = SimpleNamespace(
        data={const_mod.DOMAIN: {entry.entry_id: {"coordinator": coordinator}}}
    )
    return hass, entry


# --------------------------------------------------------------------- tests
def test_state_and_attributes_table() -> None:
    """AC-2/AC-3: the sensor's state + attributes table — raw 0 ->
    "No fault" (no code attribute, set_bits []); 256 -> "E03" + code
    + code_label "water flow problem" + set_bits [8]; 4 -> "unknown
    code (raw 4)" + set_bits [2], no code_label; register absent ->
    None (the entity's `unknown`, never a guessed "No fault"); no
    snapshot -> None + no attributes. NO timing key in any
    attributes dict (contract 024 §6 — the store's timestamps date
    the listening window, never the fault)."""
    timing_keys = (
        "first_seen", "last_seen", "fault_since", "fault_age",
        "first_push_age_s", "last_push_age_s", "latched",
    )
    # raw 0 -> "No fault", no code/code_label attribute
    e = _fault_code_entity(_push_snapshot({2046: 32765, 2074: 0}))
    assert e.native_value == "No fault"
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 0, "hex": "0x0000", "set_bits": [],
    }
    # raw 256 -> "E03", the proven code point + the app-oracle label
    e = _fault_code_entity(_push_snapshot({2046: 32765, 2074: 256}))
    assert e.native_value == "E03"
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 256, "hex": "0x0100",
        "set_bits": [8], "code": "E03",
        "code_label": "water flow problem",
    }
    # raw 4 -> the honest unknown-code wording (never a guessed code)
    e = _fault_code_entity(_push_snapshot({2046: 32765, 2074: 4}))
    assert e.native_value == "unknown code (raw 4)"
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 4, "hex": "0x0004",
        "set_bits": [2], "code": "unknown code (raw 4)",
    }
    # register absent (empty store — no usable snapshot) -> unknown
    e = _fault_code_entity(_push_snapshot(None))
    assert e.native_value is None
    assert e.extra_state_attributes == {"register": 2074}
    # register absent within a partial capture -> unknown too
    e = _fault_code_entity(_push_snapshot({2046: 32765}))
    assert e.native_value is None
    assert e.extra_state_attributes == {"register": 2074}
    # no snapshot at all (a failed first cycle) -> unknown, no attrs
    e = _fault_code_entity(None)
    assert e.native_value is None
    assert e.extra_state_attributes == {}
    # a classic snapshot (the defense-in-depth mode guard) -> unknown
    classic = coordinator_mod.AquaTempData.from_regs(dict(BASE_REGS))
    e = _fault_code_entity(classic)
    assert e.native_value is None
    assert e.extra_state_attributes == {}
    # NO timing/latch key in ANY of the attributes dicts above
    for snapshot in (
        _push_snapshot({2046: 32765, 2074: 0}),
        _push_snapshot({2046: 32765, 2074: 256}),
        _push_snapshot({2046: 32765, 2074: 4}),
        _push_snapshot({2046: 32765}),
        _push_snapshot(None),
        None,
    ):
        attrs = _fault_code_entity(snapshot).extra_state_attributes
        assert not [k for k in attrs if k in timing_keys], attrs


def test_evidence_frames_through_real_store_and_cycle() -> None:
    """AC-4: both byte-exact evidence frames (99 bytes, CRC-valid; the
    E03 frame carries 2074 = 256, the twin 0) through the REAL store
    (`_on_notify`) -> the REAL coordinator cycle (`_async_update_data`:
    the trigger read times out — the expected world — and the store
    answers) -> the sensor reads "E03" / "No fault", and the BINARY's
    decode rule agrees on the same raw (one register, one source, two
    faces — data-model 024 §2)."""
    for frame_hex, fault_raw in (
        (FRAME_2046_E03, 256),
        (FRAME_2046_E03_CLEAR, 0),
    ):
        frame = bytes.fromhex(frame_hex)
        assert len(frame) == 99, len(frame)
        assert modbus_mod.validate_crc(frame), frame_hex
        regs = modbus_mod.parse_block_response(
            frame, 2046, 45, little_endian=False
        )
        assert regs[2074] == fault_raw
    # -- the E03 state: the real store capture -> the real cycle -----
    client = _feed_real_store(FRAME_2046_E03)
    caps = client.pushed_blocks
    assert set(caps) == {2046}, sorted(caps)
    assert caps[2046].registers[2074] == 256
    data = _cycle(FakePushClient(captures=caps))
    assert data.mode == "push"
    assert data.registers[2074] == 256
    e = _fault_code_entity(data)
    assert e.native_value == "E03"
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 256, "hex": "0x0100",
        "set_bits": [8], "code": "E03",
        "code_label": "water flow problem",
    }
    # the binary's face decodes the same raw -> ON (never a disagreement)
    assert registers_mod.decode_fault_flag(data.registers.get(2074)) is True
    # -- the healthy twin (also the post-power-cut clear state) ------
    client = _feed_real_store(FRAME_2046_E03_CLEAR)
    caps = client.pushed_blocks
    assert caps[2046].registers[2074] == 0
    data = _cycle(FakePushClient(captures=caps))
    assert data.registers[2074] == 0
    e = _fault_code_entity(data)
    assert e.native_value == "No fault"
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 0, "hex": "0x0000", "set_bits": [],
    }
    assert registers_mod.decode_fault_flag(data.registers.get(2074)) is False


def test_push_entry_creates_exactly_one_fault_code_entity() -> None:
    """AC-1: a push entry creates exactly ONE Fault code sensor —
    name "Fault code" (renders "<device> Fault code"), unique_id
    <ADDRESS>_pushed_fault_code (plane-prefixed, DISJOINT from the
    classic fault_code key — reg 47's entity can never collide),
    icon mdi:alert-circle, NO device_class (a free-form string; an
    enum class would demand a closed options list the unknown-code
    family violates), NO entity_category, NO unit, NO state_class,
    on the entry's device (identifiers + title). The six temps +
    push_stream still created (the 013/022 surface, unchanged)."""
    hass, entry = _platform_world("push")
    added = _added_sensors(hass, entry)
    fault_codes = [
        e for e in added if isinstance(e, sensor_mod.AquaTempFaultCodeSensorEntity)
    ]
    assert len(fault_codes) == 1, [type(x).__name__ for x in added]
    e = fault_codes[0]
    assert e._attr_name == "Fault code"
    assert e._attr_unique_id == f"{ADDRESS.upper()}_pushed_fault_code"
    assert e._attr_icon == "mdi:alert-circle"
    assert getattr(e, "_attr_device_class", None) is None
    assert getattr(e, "_attr_entity_category", None) is None
    assert getattr(e, "_attr_native_unit_of_measurement", None) is None
    assert getattr(e, "_attr_state_class", None) is None
    assert e._attr_has_entity_name is True
    # the entity's device: the entry's PHNIX device
    assert e._attr_device_info.identifiers == {(const_mod.DOMAIN, ADDRESS)}
    assert e._attr_device_info.name == DEVICE_TITLE
    assert e._attr_device_info.manufacturer == "PHNIX"
    # the entity reads the platform world's snapshot (2074 = 0)
    assert e.native_value == "No fault"
    # the non-regression: the six temps + push_stream still created;
    # the push-mode sensor platform gained exactly ONE entity
    assert len(added) == 8, [type(x).__name__ for x in added]
    assert len([
        x for x in added if isinstance(x, sensor_mod.AquaTempSensorEntity)
    ]) == 6
    assert len([
        x for x in added if isinstance(x, sensor_mod.AquaTempPushStreamEntity)
    ]) == 1


def test_classic_entry_creates_no_fault_code_entity() -> None:
    """AC-1: a classic entry creates NO Fault code sensor — the pushed
    fault word belongs to the push plane; the classic fault home
    (register 47) is unreachable on this firmware (law L1, spec 020
    §5). The classic fault_code RegDef surface stays as it is
    (its own plane, DISJOINT from the pushed sensor's key)."""
    hass, entry = _platform_world("classic")
    added = _added_sensors(hass, entry)
    assert not [
        e for e in added if isinstance(e, sensor_mod.AquaTempFaultCodeSensorEntity)
    ], [type(x).__name__ for x in added]
    keys = {
        e._regdef.key
        for e in added
        if isinstance(e, sensor_mod.AquaTempSensorEntity)
    }
    assert "fault_code" in keys  # the classic RegDef surface, unchanged
    assert "pushed_fault_code" not in keys


def test_fault_code_state_registers() -> None:
    """AC-5: PUSHED_FAULT_CLEAR_STATE, the pure total helper over
    {None, 0, 256, 4, 8192}, the UNTOUCHED one-row code map (FR-4),
    the sensor/binary parity (both faces decode the same raw —
    data-model 024 §2), and the bit-name ban over every component
    source (contract 023 §3, carried forward)."""
    r = registers_mod
    assert r.PUSHED_FAULT_CLEAR_STATE == "No fault"
    # fault_code_state: pure + total over the AC-5 domain
    assert r.fault_code_state(None) is None  # absent -> the entity's unknown
    assert r.fault_code_state(0) == "No fault"
    assert r.fault_code_state(256) == "E03"
    assert r.fault_code_state(4) == "unknown code (raw 4)"
    assert r.fault_code_state(8192) == "unknown code (raw 8192)"
    # the code map: exactly ONE proven row — UNTOUCHED (FR-4)
    assert r.PUSHED_FAULT_CODE_MAP == {256: "E03"}
    # the parity (T8): the sensor's state and the binary's flag decode
    # the same raw through the same module — never a disagreement
    for raw in (None, 0, 256, 4, 8192):
        flag = r.decode_fault_flag(raw)
        state = r.fault_code_state(raw)
        if raw is None:
            assert flag is None and state is None
        elif raw == 0:
            assert flag is False and state == r.PUSHED_FAULT_CLEAR_STATE
        else:
            assert flag is True
            assert state not in (None, r.PUSHED_FAULT_CLEAR_STATE)
    # the bit-name ban: absent from EVERY component source
    for py in sorted(COMPONENT_DIR.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for name in FORBIDDEN_BIT_NAMES:
            assert name not in text, (py.name, name)


def test_no_fault_specific_freshness_code() -> None:
    """AC-6: the freshness NON-change — no identifier anywhere in the
    component couples the fault with freshness/staleness/age/window
    (the shape a fault-specific gate would necessarily take), and
    the fault-code entity's own source references no gate and no
    clock: the fault entities ride the SHARED cycle gate exactly
    like the temperatures (contract 024 §5 — the gate tests the
    STREAM's recency, never VALUE movement)."""
    for py in sorted(COMPONENT_DIR.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        m = _FAULTY_GATE_RE.search(text)
        assert m is None, (py.name, m.group(0) if m else None)
    # the fault-code entity's class: no direct gate/clock reference —
    # it reads the snapshot's registers, nothing else
    src = (COMPONENT_DIR / "sensor.py").read_text(encoding="utf-8")
    seg = src[src.index("class AquaTempFaultCodeSensorEntity"):]
    for banned in ("push_freshness_window", "coordinator.now", "harvest"):
        assert banned not in seg, banned
    # the binary never grew a gate either (byte-identical, 023)
    bsrc = (COMPONENT_DIR / "binary_sensor.py").read_text(encoding="utf-8")
    assert "push_freshness_window" not in bsrc


def test_binary_untouched_no_fault_code_state() -> None:
    """AC-7: the addition is an ADDITION — binary_sensor.py decodes
    through its own 023 helpers only (no fault_code_state, no clear
    state constant; the byte-identical proof rides the T7 git gate,
    and the 023 suite stays green unmodified). PLATFORMS is
    unchanged."""
    src = (COMPONENT_DIR / "binary_sensor.py").read_text(encoding="utf-8")
    assert "fault_code_state" not in src
    assert "PUSHED_FAULT_CLEAR_STATE" not in src
    assert const_mod.PLATFORMS == ["climate", "sensor", "button", "binary_sensor"]


def test_docs_state_the_latch() -> None:
    """AC-8: the README states the latch + the new tile (the features
    row, both entity rows, both troubleshooting rows), CALIBRATION.md
    §3 carries the rewritten fault bullet (the 023 debt cleared —
    the fault word IS exposed, and it is latched), no sentence claims
    that turning the unit off clears a fault, and the three
    translation JSONs keep their exact options key parity."""
    readme = (REPO_DIR / "README.md").read_text(encoding="utf-8")
    low = " ".join(readme.lower().split())
    # the features row: the fault-code tile, the two-run proof, the latch
    assert (
        "| Fault code | **proven** (owner E03 diff-revert, two "
        "independent runs; the fault word is latched — see "
        "Troubleshooting) | 1 sensor |" in readme
    ), "the features-table Fault code row is missing"
    # the entities rows: BOTH faces of register 2074 (plan §2.4 verbatim)
    assert (
        "| Fault (binary) | pushed 2074 (the latched fault word) | "
        "`on` = a fault is on record (memorized, not necessarily "
        "active); attributes: raw / hex / set_bits / code / "
        "code_label |" in readme
    ), "the entities-table Fault binary row is missing"
    assert (
        "| Fault code | pushed 2074 (the latched fault word) | "
        "`No fault` / the panel code (`E03`) / `unknown code (raw N)`; "
        "a code stays displayed while the unit is powered — cleared "
        "only by cutting the unit's supply (latch, owner-proven) |"
        in readme
    ), "the entities-table Fault code row is missing"
    # the troubleshooting rows: the two-entity row + the latch row
    assert 'Fault: the "Fault" binary sensor + "Fault code" sensor' in readme
    assert "the fault word is latched (see the next row)" in readme
    assert (
        "| Fault code shows while the unit is off / healthy | "
        "**Normal — the fault word is latched**:" in readme
    ), "the latch troubleshooting row is missing"
    assert "turning the unit off does not clear it" in low
    assert "cut the unit's supply (unplug/replug) to clear the code" in readme
    # grep gate (AC-8): no sentence anywhere claims the off clears
    for banned in (
        "off clears the fault",
        "off clears it",
        "turning the unit off clears",
        "switching the unit off clears",
        "powering off clears",
    ):
        assert banned not in low, banned
    # CALIBRATION.md §3: the rewritten bullet (the 023 debt cleared)
    cal = (REPO_DIR / "docs" / "CALIBRATION.md").read_text(encoding="utf-8")
    cal_norm = " ".join(cal.split())
    assert "- **Fault code & firmware**" not in cal_norm
    assert "**Firmware**: no home was ever identified" in cal_norm
    assert "**The fault word** (pushed register 2074) IS exposed" in cal_norm
    assert (
        'a "Fault" binary sensor (spec 023) and a "Fault code" sensor '
        "(spec 024)" in cal_norm
    )
    assert "it is **latched**: the unit memorizes a fault until its supply is cut" in (
        cal_norm
    )
    assert "so a displayed code may outlive the fault itself" in cal_norm
    # the three translation JSONs: the exact options key parity, as-is
    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        data = json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))
        init = data["options"]["step"]["init"]
        assert set(init["data"]) == {"poll_interval", "temperature_unit"}, (
            name,
            init["data"].keys(),
        )
        assert "data_description" not in init, name


def test_read_only_guarantee() -> None:
    """AC-9: no option key, flow step, service or confirmation for the
    fault — the push options schema stays exactly poll_interval +
    temperature_unit (in the flow source AND the three JSON files),
    the funnel refuses the fault word forever (even the value 0 — a
    "clear" write is as forbidden as any other; contract 024 §7),
    and no clear-fault service exists anywhere."""
    flow = (COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
    options_region = flow[flow.index("class AquaTempOptionsFlowHandler"):]
    keys = set(re.findall(r'vol\.Required\(\s*"(\w+)"', options_region))
    assert keys == {"poll_interval", "temperature_unit"}, keys
    for value in (0, 1):
        try:
            registers_mod.ensure_writable(registers_mod.REG_PUSHED_FAULT, value)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"register 2074 must never be writable (value {value})"
            )
    # no clear-fault surface: no "clear" service, no fault option key
    services = (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    assert not re.search(r"(?<![A-Za-z])[Cc]lear", services), "a clear service exists"
    const_text = (COMPONENT_DIR / "const.py").read_text(encoding="utf-8")
    assert not re.search(
        r"^(?!DEFAULT_)[A-Z_0-9]*FAULT[A-Z_0-9]*\s*=", const_text, re.M
    ), "const.py carries a fault option constant"


def test_manifest_version() -> None:
    """AC-10: the manifest bumps to 0.18.0 (the 023->0.17.0
    precedent; HACS sees the change)."""
    manifest = json.loads(
        (COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.18.0", manifest.get("version")


def main() -> int:
    tests = [
        test_state_and_attributes_table,
        test_evidence_frames_through_real_store_and_cycle,
        test_push_entry_creates_exactly_one_fault_code_entity,
        test_classic_entry_creates_no_fault_code_entity,
        test_fault_code_state_registers,
        test_no_fault_specific_freshness_code,
        test_binary_untouched_no_fault_code_state,
        test_docs_state_the_latch,
        test_read_only_guarantee,
        test_manifest_version,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-024 fault-code-sensor tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())