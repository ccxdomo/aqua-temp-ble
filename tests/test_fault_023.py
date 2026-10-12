#!/usr/bin/env python3
"""Spec 023 tests — the fault exposure: register 2074 (AC-1..AC-8).

The owner's E03 diff-revert (2026-10-12) pinned the fault word:
healthy capture 2074 = 0 -> fault capture 256 (0x0100, bit 8) while
the panel displayed E03 -> 0 after the clear. Spec 023 exposes it as
ONE binary_sensor "Fault" (device_class problem) on push-mode
entries, with the evidence as attributes (raw/hex/set_bits/code) and
exactly ONE proven code point (256 -> "E03"; every other non-zero
raw reads "unknown code (raw N)" — no guessed tables, and bit names
are FORBIDDEN in entity code, contract 023 §3).

The app-oracle record (owner relay, 2026-10-12): while the fault
lived, the vendor's own app displayed "E03 probleme de debit d'eau"
— the CODE label "water flow problem" is PROVEN by the app itself
and rides the entity as the `code_label` attribute (the parent's
authorized second attribute; registers.PUSHED_FAULT_CODE_APP_LABELS).
Which protection BIT 8 names stays unproven: the app's label ties
E03 to a water-flow fault while the disassembly's ten-flag list
order would make bit 8 the outlet-water-temp-sensor fault [A,
research 023 §2.3] — the conflict is documented, never adjudicated
in code (no bit names anywhere, contract 023 §3).

Pinned by this file (spec 023 §2's ACs):
- AC-4's vectors first: the byte-exact E03 frame (99 bytes, computed
  CRC 0x1D55 = the relayed tail `55 1d`) and its healthy twin (CRC
  0xF054) are CRC-valid block frames with 2074 = 256 / 0;
- AC-2/AC-3: the decode + attributes table (0 -> off, no code; 256 ->
  on + "E03" + set_bits [8]; 4 -> on + unknown-code + [2]; absent ->
  unknown, never a guessed OFF; no snapshot -> unknown + no attrs);
- AC-4: both evidence frames through the REAL store
  (`client._on_notify`) -> the REAL coordinator cycle
  (`_async_update_data`: the trigger times out, the store answers)
  -> the entity reads ON (code E03, raw 256) / OFF;
- AC-1: a push entry creates exactly ONE binary entity (name
  "Fault", problem class, unique_id <ADDRESS>_fault, the entry's
  device, NO entity category); a classic entry creates NONE
  (register 47 is unreachable — law L1, spec 020 §5);
- AC-5/AC-8: PLATFORMS gains binary_sensor; the platform file
  exists; NO other binary surface anywhere (the 021 placeholders
  stay dead); the options schema stays exactly poll_interval +
  temperature_unit and no fault surface exists in config_flow.py,
  services.yaml or const.py (no option, no gate — FR-8);
- AC-6: 2074 rides the diagnostics (PUSH_CONTROL_CANDIDATES -> the
  push_stream control_candidates attributes; KNOWN_LABELS -> the
  probe dump labels) with the verbatim evidence tags;
- AC-7: the README's three spots state the new truth;
- the write plane: 2074 is refused by the funnel forever (contract
  023 §5) and the code map carries exactly the ONE proven row.

Unavailability while the cycle fails is the shared CoordinatorEntity
behavior (the 013/014 freshness-gate suites pin it) — no fault-specific
code exists for it (FR-2).

Red-first on 84494aa: no fault plane, no binary_sensor platform, no
fault row in the README — every assertion below fails until spec 023
lands.

Run standalone:

    python3 tests/test_fault_023.py

No real Bluetooth and no real Home Assistant required (the
test_registers_push_013 stub pattern + the test_push_store_013
store-feed pattern + the test_coordinator_push_013 cycle pattern).
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
PKG = "aqua_temp_ble_fault_023"  # synthetic package (distinct from siblings)
DEVICE_TITLE = "Aqua Temp test"  # the fake entries' title (the device name)

# The vendor's ten-flag list (research 023 §2.1) — the candidate
# vocabulary whose ORDER is an [A] hypothesis. NEVER in entity code
# (contract 023 §3); pinned absent by test_fault_plane_registers.
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
# §1.2): the reconstruction's computed CRC-16/Modbus over the first 97
# bytes is 0x1D55, transmitted low byte first as `55 1d` — exactly the
# relayed frame tail (a coincidental match has probability 2^-16).
# 2074 = 0x0100 = 256 at frame bytes 63-64 (research 023 §1.3).
FRAME_2046_E03 = (
    "631007fe002d5a"          # fn, 0x10, start 2046, qty 45, 90 bytes
    + "7ffd"                  # 2046: the T01-absent sentinel
    + "00910091009600960096"  # 2047-2051: the fault-capture temps
    + "00" * 44               # 2052-2073: the zero run
    + "0100"                  # 2074: THE FAULT (256 = bit 8)
    + "00" * 32               # 2075-2090: the zero tail
    + "551d"                  # CRC-16/Modbus (verified)
)
# The healthy twin: the same capture with 2074 = 0 (synthetic, valid —
# the baseline semantics are the owner's diff record; research 023 §1.2).
FRAME_2046_E03_CLEAR = (
    "631007fe002d5a7ffd00910091009600960096"
    + "00" * 78 + "54f0"      # CRC recomputed for 2074 = 0
)

# The candidates/labels evidence tags (registers.py — pinned verbatim).
FAULT_CANDIDATE_TAG = "fault word [P owner diff-revert 2026-10-12 — spec 023]"
FAULT_LABEL_TAG = "Fault word (bitfield) [P owner diff-revert 2026-10-12]"

# A minimal classic-plane register set (the coordinator-test pattern).
BASE_REGS = {
    7: 0, 8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
    22: 60, 23: 400, 24: 60, 25: 400, 26: 243, 27: -9990, 28: -125,
    47: 0, 48: 2, 49: 2, 50: 1, 51: 2040,
}


# ---------------------------------------------------------------------------
# The synthetic-package harness (the test_registers_push_013 pattern,
# extended with the binary_sensor platform stub)
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — the superset imported by
    binary_sensor.py + sensor.py + entity.py + coordinator.py."""

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

    class BinarySensorEntity:  # pragma: no cover - attribute container
        pass

    class BinarySensorDeviceClass:
        PROBLEM = "problem"

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
    ha_binary = types.ModuleType("homeassistant.components.binary_sensor")
    ha_binary.BinarySensorEntity = BinarySensorEntity
    ha_binary.BinarySensorDeviceClass = BinarySensorDeviceClass
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
    ha_components.binary_sensor = ha_binary
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
        "homeassistant.components.binary_sensor": ha_binary,
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
binary_sensor_mod = _import_pkg_module("binary_sensor")


# ---------------------------------------------------------------------------
# Fakes: the step clock, the duck-typed capture, the no-IO client and
# the real-store feed (the 013/014 patterns)
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
        self.disconnect_calls = 0

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


def _fault_entity(data):
    """The Fault binary sensor wired to a coordinator carrying `data`."""
    coordinator, entry = _coord_with(data)
    return binary_sensor_mod.AquaTempFaultBinarySensor(coordinator, entry)


def _added_binary(hass, entry) -> list:
    """Run the binary_sensor platform setup; collect the entities."""
    added: list = []

    def _collect(entities) -> None:
        # HA's AddEntitiesCallback is called synchronously by the
        # platform — a plain function, not a coroutine.
        added.extend(entities)

    asyncio.run(binary_sensor_mod.async_setup_entry(hass, entry, _collect))
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


_FAULT_WORD_RE = re.compile(r"(?<![A-Za-z])[Ff]ault")


def _mentions_fault(text: str) -> bool:
    """True when `text` carries a fault surface word — `default`
    (de-FAULT) deliberately does NOT match (the lookbehind)."""
    return bool(_FAULT_WORD_RE.search(text))


# --------------------------------------------------------------------- tests
def test_evidence_fixtures_byte_exact() -> None:
    """AC-4's vectors: both frames are 99-byte CRC-valid block frames;
    the E03 frame carries 2074 = 256 (0x0100, big-endian) at frame
    bytes 63-64, the twin 0 — the owner's evidence, byte-pinned."""
    for frame_hex in (FRAME_2046_E03, FRAME_2046_E03_CLEAR):
        frame = bytes.fromhex(frame_hex)
        assert len(frame) == 99, len(frame)
        assert modbus_mod.validate_crc(frame), frame_hex
    e03 = bytes.fromhex(FRAME_2046_E03)
    assert e03[63:65] == b"\x01\x00"  # 2074 = 0x0100, big-endian
    regs = modbus_mod.parse_block_response(e03, 2046, 45, little_endian=False)
    assert len(regs) == 45
    assert regs[2074] == 256
    assert regs[2046] == 32765  # the T01 sentinel anchor, unchanged
    assert regs[2047] == 145 and regs[2051] == 150  # the drift temps
    clear = modbus_mod.parse_block_response(
        bytes.fromhex(FRAME_2046_E03_CLEAR), 2046, 45, little_endian=False
    )
    assert len(clear) == 45
    assert clear[2074] == 0


def test_fault_plane_registers() -> None:
    """FR-5/AC-3/AC-6: the register constant, the ONE-row code map,
    the three pure helpers, the diagnostics candidate + label entries
    (verbatim tags), the permanent write refusal (contract 023 §5),
    the untouched sibling registries, and the bit-name ban (contract
    023 §3 — the ten-flag list order is [A])."""
    r = registers_mod
    assert r.REG_PUSHED_FAULT == 2074
    # exactly ONE proven code row (contract 023 §3)
    assert r.PUSHED_FAULT_CODE_MAP == {256: "E03"}
    # decode_fault_flag: the diff-revert rule — 0 clear, non-zero fault
    assert r.decode_fault_flag(None) is None
    assert r.decode_fault_flag(0) is False
    assert r.decode_fault_flag(256) is True
    assert r.decode_fault_flag(4) is True
    # fault_code: the proven point maps; others read unknown; clear -> None
    assert r.fault_code(None) is None
    assert r.fault_code(0) is None
    assert r.fault_code(256) == "E03"
    assert r.fault_code(4) == "unknown code (raw 4)"
    assert r.fault_code(258) == "unknown code (raw 258)"
    # fault_code_app_label: the APP-oracle record — the vendor's own
    # app NAMES the codes (E03 = "water flow problem", read live by
    # the owner 2026-10-12; the app is the vendor's documentation
    # for CODE meanings — research 023 Part 8's [V] "what E03 means"
    # upgraded to [P] at the code level). A CODE label, never a bit
    # name; an unknown or unrecorded code carries NO label, never
    # a guess.
    assert r.PUSHED_FAULT_CODE_APP_LABELS == {"E03": "water flow problem"}
    assert r.fault_code_app_label(None) is None
    assert r.fault_code_app_label(0) is None
    assert r.fault_code_app_label(256) == "water flow problem"
    assert r.fault_code_app_label(4) is None  # unknown code: no label
    assert r.fault_code_app_label(258) is None
    # fault_set_bits: arithmetic only, no names
    assert r.fault_set_bits(None) == []
    assert r.fault_set_bits(0) == []
    assert r.fault_set_bits(256) == [8]
    assert r.fault_set_bits(4) == [2]
    assert r.fault_set_bits(258) == [1, 8]
    assert r.fault_set_bits(0x0104) == [2, 8]
    # the diagnostics homes (AC-6): the candidate + the probe label,
    # pinned verbatim (the plan §2.1 shapes)
    assert r.PUSH_CONTROL_CANDIDATES[2074] == FAULT_CANDIDATE_TAG
    assert r.KNOWN_LABELS[2074] == FAULT_LABEL_TAG
    # the write plane refuses the fault register forever (contract 023 §5)
    try:
        r.ensure_writable(r.REG_PUSHED_FAULT, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("register 2074 must never be writable")
    # the untouchable registries stay untouched (FR-5's DO-NOT list)
    assert r.PUSHED_TEMPS == {
        "suction_temp": 2046, "inlet_water_temp": 2047,
        "outlet_water_temp": 2048, "coil_temp": 2049,
        "ambient_temp": 2050, "exhaust_temp": 2051,
    }
    assert set(r.PUSHED_CONTROL) == {"power", "mode", "setpoint"}
    assert 2074 not in r.PUSHED_CONTROL.values()
    # bit names are FORBIDDEN in entity code (contract 023 §3)
    for py in (COMPONENT_DIR / "registers.py", COMPONENT_DIR / "binary_sensor.py"):
        text = py.read_text(encoding="utf-8")
        for name in FORBIDDEN_BIT_NAMES:
            assert name not in text, (py.name, name)


def test_decode_and_attributes_table() -> None:
    """AC-2/AC-3: the entity's decode + attributes table — raw 0 ->
    off (no code attribute); 256 -> on + "E03" + set_bits [8]; 4 ->
    on + "unknown code (raw 4)" + [2]; absent register -> unknown,
    never a guessed OFF; no snapshot -> unknown + no attributes."""
    # raw 0 -> OFF, no code attribute
    e = _fault_entity(_push_snapshot({2046: 32765, 2074: 0}))
    assert e.is_on is False
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 0, "hex": "0x0000", "set_bits": [],
    }
    # raw 256 -> ON, the proven code point + the app-oracle label
    e = _fault_entity(_push_snapshot({2046: 32765, 2074: 256}))
    assert e.is_on is True
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 256, "hex": "0x0100",
        "set_bits": [8], "code": "E03",
        "code_label": "water flow problem",
    }
    # raw 4 -> ON, the honest unknown-code wording
    e = _fault_entity(_push_snapshot({2046: 32765, 2074: 4}))
    assert e.is_on is True
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 4, "hex": "0x0004",
        "set_bits": [2], "code": "unknown code (raw 4)",
    }
    # register absent (empty store — no usable snapshot) -> unknown
    e = _fault_entity(_push_snapshot(None))
    assert e.is_on is None
    assert e.extra_state_attributes == {"register": 2074}
    # register absent within a partial capture -> unknown too
    e = _fault_entity(_push_snapshot({2046: 32765}))
    assert e.is_on is None
    assert e.extra_state_attributes == {"register": 2074}
    # no snapshot at all (a failed first cycle) -> unknown, no attrs
    e = _fault_entity(None)
    assert e.is_on is None
    assert e.extra_state_attributes == {}
    # a classic snapshot (the defense-in-depth mode guard) -> unknown
    classic = coordinator_mod.AquaTempData.from_regs(dict(BASE_REGS))
    e = _fault_entity(classic)
    assert e.is_on is None
    assert e.extra_state_attributes == {}


def test_evidence_frames_through_real_store_and_cycle() -> None:
    """AC-4: both byte-exact evidence frames through the REAL store
    (`_on_notify`) -> the REAL coordinator cycle (`_async_update_data`:
    the trigger read times out — the expected world — and the store
    answers) -> the entity reads ON (code E03, raw 256, set_bits [8])
    and OFF. The snapshot's diagnostics raws carry 2074 too (AC-6's
    feed, the FR-9 continuity)."""
    # -- the E03 state: the real store capture --------------------------
    client = _feed_real_store(FRAME_2046_E03)
    caps = client.pushed_blocks
    assert set(caps) == {2046}, sorted(caps)
    assert caps[2046].registers[2074] == 256
    assert caps[2046].registers[2046] == 32765
    assert caps[2046].length == 99 and caps[2046].qty == 45
    # -> the real cycle over the real captures
    data = _cycle(FakePushClient(captures=caps))
    assert data.mode == "push"
    assert data.registers[2074] == 256
    assert data.push_control_raws[2074] == 256  # the diagnostics wiring
    e = _fault_entity(data)
    assert e.is_on is True
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 256, "hex": "0x0100",
        "set_bits": [8], "code": "E03",
        "code_label": "water flow problem",
    }
    # -- the healthy twin: OFF, no code attribute ------------------------
    client = _feed_real_store(FRAME_2046_E03_CLEAR)
    caps = client.pushed_blocks
    assert caps[2046].registers[2074] == 0
    data = _cycle(FakePushClient(captures=caps))
    assert data.registers[2074] == 0
    assert data.push_control_raws[2074] == 0
    e = _fault_entity(data)
    assert e.is_on is False
    assert e.extra_state_attributes == {
        "register": 2074, "raw": 0, "hex": "0x0000", "set_bits": [],
    }


def test_push_entry_creates_exactly_one_fault_entity() -> None:
    """AC-1: a push entry creates exactly ONE binary entity — name
    "Fault", device_class problem, unique_id <ADDRESS>_fault, on the
    entry's device (identifiers + title), NO entity category (an
    operational signal, not plumbing), has_entity_name (renders
    "<device> Fault")."""
    hass, entry = _platform_world("push")
    added = _added_binary(hass, entry)
    assert len(added) == 1, [type(x).__name__ for x in added]
    e = added[0]
    assert isinstance(e, binary_sensor_mod.AquaTempFaultBinarySensor)
    assert e._attr_name == "Fault"
    assert e._attr_device_class == "problem"
    assert e._attr_unique_id == f"{ADDRESS.upper()}_fault"
    assert getattr(e, "_attr_entity_category", None) is None
    assert e._attr_has_entity_name is True
    # the entity's device: the entry's PHNIX device
    assert e._attr_device_info.identifiers == {(const_mod.DOMAIN, ADDRESS)}
    assert e._attr_device_info.name == DEVICE_TITLE
    assert e._attr_device_info.manufacturer == "PHNIX"
    # the entity reads the platform world's snapshot (2074 = 0 -> off)
    assert e.is_on is False


def test_classic_entry_creates_no_binary_entity() -> None:
    """AC-1: a classic entry creates NO binary entity — the classic
    fault home (register 47) is unreachable (law L1, spec 020 §5)."""
    hass, entry = _platform_world("classic")
    assert _added_binary(hass, entry) == []


def test_push_stream_diagnostics_carry_the_fault_word() -> None:
    """AC-6: the Push stream diagnostic's control_candidates attributes
    include 2074 with its verbatim evidence tag — the diff instrument
    keeps working for the fault word (the 2011/2019/1046 precedent)."""
    client = _feed_real_store(FRAME_2046_E03)
    snap = coordinator_mod.AquaTempData.from_pushes(dict(client.pushed_blocks))
    coordinator, entry = _coord_with(snap)
    stream = sensor_mod.AquaTempPushStreamEntity(coordinator, entry)
    attrs = stream.extra_state_attributes
    cands = attrs["control_candidates"]
    assert cands["2074"] == {"raw": 256, "evidence": FAULT_CANDIDATE_TAG}


def test_platform_list_and_no_dead_placeholders() -> None:
    """AC-5: PLATFORMS gains binary_sensor; the platform file exists;
    NO other binary surface anywhere — the "binary_sensor" token lives
    ONLY in const.py (the platform list) and the platform file; no
    registry, no RegDef loop, no placeholder drives creation (the 021
    dead-weight pattern cannot return)."""
    assert const_mod.PLATFORMS == ["climate", "sensor", "button", "binary_sensor"]
    assert (COMPONENT_DIR / "binary_sensor.py").is_file()
    for py in sorted(COMPONENT_DIR.glob("*.py")):
        if py.name in ("const.py", "binary_sensor.py"):
            continue
        assert "binary_sensor" not in py.read_text(encoding="utf-8"), py.name
    # the platform file: ONE literal entity, no registry-driven creation
    src = (COMPONENT_DIR / "binary_sensor.py").read_text(encoding="utf-8")
    assert (
        "async_add_entities([AquaTempFaultBinarySensor(coordinator, entry)])"
        in src
    ), "the platform must add exactly one literal entity"
    assert "RegDef" not in src
    assert "registers.SENSORS" not in src
    assert "PUSHED_TEMPS" not in src
    setup_seg = src[src.index("async def async_setup_entry"):]
    setup_seg = setup_seg[: setup_seg.index("async_add_entities")]
    assert "for " not in setup_seg, "creation must not be loop-driven"


def test_no_fault_gate_anywhere() -> None:
    """AC-8/FR-8: no option, no flow step, no service, no confirmation
    for the fault — config_flow.py and services.yaml carry NO fault
    surface word at all; const.py gains no fault option constant (its
    platform-list comment's "Fault word 2074" documentation is legal,
    plan §2.2); the options schema stays exactly poll_interval +
    temperature_unit, in the flow source AND the three JSON files."""
    flow = (COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
    assert not _mentions_fault(flow), "config_flow.py carries a fault surface"
    services = (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    assert not _mentions_fault(services), "services.yaml carries a fault service"
    const_text = (COMPONENT_DIR / "const.py").read_text(encoding="utf-8")
    # The pre-existing DEFAULT_* constants contain "DE-FAULT" — the
    # negative lookahead keeps them legal; only a NEW fault option
    # constant (e.g. FAULT_CONFIRMED, SHOW_FAULT) fails this pin.
    assert not re.search(
        r"^(?!DEFAULT_)[A-Z_0-9]*FAULT[A-Z_0-9]*\s*=", const_text, re.M
    ), "const.py carries a fault option constant"
    assert '"fault"' not in const_text
    # the options flow: exactly the two keys, in the source region
    options_region = flow[flow.index("class AquaTempOptionsFlowHandler"):]
    keys = set(re.findall(r'vol\.Required\(\s*"(\w+)"', options_region))
    assert keys == {"poll_interval", "temperature_unit"}, keys
    # ... and in the three shipped JSON files (the 022 pattern)
    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        data = json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))
        init = data["options"]["step"]["init"]
        assert set(init["data"]) == {"poll_interval", "temperature_unit"}, (
            name,
            init["data"].keys(),
        )
        assert "data_description" not in init, name


def test_readme_states_the_new_fault_truth() -> None:
    """AC-7: the features row, the reworded not-available paragraph and
    the rewritten troubleshooting row; no 'fault not displayed' claim
    remains; the firmware honesty stays ('no reliable source' — the
    022 README pin)."""
    readme = (REPO_DIR / "README.md").read_text(encoding="utf-8")
    low = " ".join(readme.lower().split())
    # the features row (plan §2.4 verbatim)
    assert (
        "| Fault indicator (code when proven) | **proven** (owner E03 "
        "diff-revert) | 1 binary sensor |" in readme
    ), "the features-table Fault row is missing"
    # the old claim is gone
    assert "Fault code / firmware not displayed" not in readme
    # the not-available paragraph: the fault IS exposed since 023
    assert "the fault IS exposed since spec 023" in readme
    # the troubleshooting row: the Fault binary sensor truth
    assert 'Fault: the "Fault" binary sensor' in readme
    assert "e03 = raw 256" in low
    # the app-oracle label sentence (parent brief 2026-10-12): the
    # vendor's OWN app names E03 "water flow problem" while the
    # fault lives — a CODE label [P app display], never a bit name.
    # The bit-8 name stays [V]/[A] open (research 023 §2/Part 8):
    # the app is the vendor's documentation for CODE meanings; the
    # honest-limit rule keeps BIT names out of the docs.
    assert "water flow problem" in low
    assert 'else "unknown code (raw n)"' in low
    # the firmware honesty stays (still no home identified — 022's pin)
    assert "Firmware: still not displayed" in readme
    assert "no reliable source" in low


def main() -> int:
    tests = [
        test_evidence_fixtures_byte_exact,
        test_fault_plane_registers,
        test_decode_and_attributes_table,
        test_evidence_frames_through_real_store_and_cycle,
        test_push_entry_creates_exactly_one_fault_entity,
        test_classic_entry_creates_no_binary_entity,
        test_push_stream_diagnostics_carry_the_fault_word,
        test_platform_list_and_no_dead_placeholders,
        test_no_fault_gate_anywhere,
        test_readme_states_the_new_fault_truth,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-023 fault-exposure tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())