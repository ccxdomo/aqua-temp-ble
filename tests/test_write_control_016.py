#!/usr/bin/env python3
"""Spec 016 tests — the write-control path: pinned frames, the locked
guard, the power-write probe and its surfaces (FR-1..FR-7, AC-1..AC-10).

Part A (standalone modules — the test_registers pattern): the
write-plane constants transcribed from the app's own disassembly
(research 016 §2/§5: power 1011, mode 1012-1013, mute 1076; the pinned
ON/OFF frames CRC-verified through the app's builder pipeline), the
modbus.build_write_control builder reproducing the four pinned vectors
of research 016 §2.1 byte-exact (AC-1, NFR-2's two-sources cross-check
against registers.WRITE_FRAME_*), the probe-log annotations
(KNOWN_LABELS), the untouched read map (PUSHED_CONTROL/ALLOWED_WRITE_
BASE) and the guard matrix (spec 022: the PROVEN switch is gone —
1011 is writable BY DEFAULT within the {0,1} value allowlist;
1012/1076/None-value refused), AC-4
(the base-block rules byte-identical: one-arg calls still work, 15/
1004/2001/2090 still raise — a read home is never writable, contract
016 §2 — and 19-21 stay behind WRITE_MODE_SETPOINT_TOO).

Part B (the client seam — the 011-suite fake-bleak pattern): the probe
send path writes the EXACT pinned bytes on the write bucket WITHOUT
response (app parity, research 010 §2.3) and the FR-6 safety rail
refuses any non-pinned frame before a byte goes out.

Part C (coordinator, synthetic-package import — the 015 pattern): the
diff-revert protocol's verdict paths driven against a scripted client:
AC-5 (mirror 0→1→0 -> executed+reversible + the WARNING verdict line),
AC-6 (stuck-0 -> not-executed; stuck-1 -> not-reversible with the
remediation text), AC-7 (baseline≠0 -> refused-baseline-not-off with
ZERO writes recorded), the att-error chain, the no-report silence, the
FR-4 step-4 robust order (ONE retry trigger after a silent write
window), the disconnect-on-completion and the T3 funnel (async_write_
register threads the value through the guard).

Part D (the surfaces, spec 022): the diagnostic "Power write probe"
button routes to the probe; the push-mode climate is created with the
FULL surface (active by default — the read-only world is dead with
the gates); the service is declared in
services.yaml/strings.json/translations.

Spec 022 note: the 016 gate surfaces (the PROVEN switch, the
gate-closed refusals, the read-only climate) are INVERTED — the gates
are gone; the living behaviours (the pinned frames, the builder
parity, the probe's verdict paths, the button, the service) keep
their assertions.

Red-first on d8f6385 (0.11.0): none of the write-plane constants, the
builder, the guard extension, the probe seam or the probe itself
exist, so every test below fails until the implementation lands.

Run standalone:

    python3 tests/test_write_control_016.py

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
PKG = "aqua_temp_ble_write_016"  # synthetic package (distinct from siblings)
UUID_WRITE = const.UUID_WRITE

# The four pinned control vectors [P — research 016 §2.1]: the power
# pair from the disassembled home-screen power handler (reg 1011 =
# 0x03F3), the mute pair from the mute handler (reg 1076 = 0x0434).
POWER_ON_HEX = "631003f30001020001f131"
POWER_OFF_HEX = "631003f3000102000030f1"
MUTE_ON_HEX = "6310043400010200019746"
MUTE_OFF_HEX = "6310043400010200005686"


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
# Part A — the data + the builder + the guard (standalone modules)
# ===========================================================================


def test_write_plane_constants_transcribed() -> None:
    """FR-1: the write-plane map, transcribed from research 016 §2/§5 —
    the disassembly-pinned power register, the mode pair and the mute
    register; READ_MIRROR_POWER aliases the pushed read home."""
    assert registers.WRITE_REG_POWER == 1011      # [P disassembly]
    assert registers.WRITE_REG_MODE == 1012       # [P shape / A semantics]
    assert registers.WRITE_REG_MODE_PAIR == 1013  # [P shape / A semantics]
    assert registers.WRITE_REG_MUTE == 1076       # [P frame / V semantics]
    # the proof oracle — the existing pinned read home (spec 015)
    assert registers.READ_MIRROR_POWER == registers.PUSHED_CONTROL["power"]
    assert registers.READ_MIRROR_POWER == 2011
    # the pinned frames, byte-for-byte research 016 §2.1
    assert registers.WRITE_FRAME_POWER_ON == POWER_ON_HEX
    assert registers.WRITE_FRAME_POWER_OFF == POWER_OFF_HEX
    # the control allowlist: the address AND the value allowlist only
    # (spec 022: the value rail is the PERMANENT protection — the
    # human gate is gone)
    assert registers.ALLOWED_WRITE_CONTROL == {
        registers.WRITE_REG_POWER: frozenset({0, 1}),
    }


def test_read_map_untouched() -> None:
    """FR-1/NFR-3: the READ plane is untouched — PUSHED_CONTROL keeps
    the 015 pinning for power/mode (spec 019 T1 later pinned the
    setpoint home to 1046 — the owner's panel diff, research 019 §1)
    and the base allowlist keeps spec 003 §6."""
    assert registers.PUSHED_CONTROL == {
        "power": 2011, "mode": 2019, "setpoint": 1046,
    }
    assert registers.ALLOWED_WRITE_BASE == frozenset({8, 13, 14})


def test_pinned_control_frame_vectors() -> None:
    """AC-1: build_write_control reproduces the four pinned vectors of
    research 016 §2.1 byte-exact — 11 bytes each, CRC valid, the vendor
    command shape (qty 1, bytecount 02)."""
    vectors = [
        ("power ON", registers.WRITE_REG_POWER, 1, POWER_ON_HEX),
        ("power OFF", registers.WRITE_REG_POWER, 0, POWER_OFF_HEX),
        ("mute ON", registers.WRITE_REG_MUTE, 1, MUTE_ON_HEX),
        ("mute OFF", registers.WRITE_REG_MUTE, 0, MUTE_OFF_HEX),
    ]
    for name, addr, value, hexstr in vectors:
        frame = modbus.build_write_control(addr, value)
        expected = bytes.fromhex(hexstr)
        assert frame == expected, (name, frame.hex(), hexstr)
        assert len(frame) == 11, (name, len(frame))
        assert modbus.validate_crc(frame), name


def test_builder_cross_checks_registers_pinned_strings() -> None:
    """NFR-2: the two sources of truth agree — the builder's output is
    byte-equal to registers' pinned strings (a mismatch would fail the
    modbus self-test AND this suite)."""
    on = modbus.build_write_control(registers.WRITE_REG_POWER, 1)
    off = modbus.build_write_control(registers.WRITE_REG_POWER, 0)
    assert on.hex() == registers.WRITE_FRAME_POWER_ON
    assert off.hex() == registers.WRITE_FRAME_POWER_OFF
    # the builder never produces a non-pinned power value through the
    # constants' own address (the guard owns the value discipline)
    assert modbus.build_write_control(registers.WRITE_REG_POWER, 2) != on


def test_write_plane_labels_annotated() -> None:
    """FR-1 (the probe-log annotation convention): KNOWN_LABELS tags
    the write-plane registers with their evidence state; the pushed
    control homes keep their spec-015 tags."""
    assert "Power (WRITE plane)" in registers.KNOWN_LABELS[1011]
    assert "Mode (WRITE plane)" in registers.KNOWN_LABELS[1012]
    assert "Mode pair (WRITE plane)" in registers.KNOWN_LABELS[1013]
    assert "Mute (WRITE plane)" in registers.KNOWN_LABELS[1076]
    # the read-plane labels are unchanged (the 015 surface)
    assert "Power" in registers.KNOWN_LABELS[2011]
    assert "Mode" in registers.KNOWN_LABELS[2019]
    assert registers.KNOWN_LABELS[8] == "Power [P]"


def test_guard_control_value_rail_by_default() -> None:
    """AC-4 (spec 022, the inversion of the shipped-locked world):
    ensure_writable admits 1011 BY DEFAULT within its PERMANENT value
    rail — 0/1 pass; any other value (and the valueless form) is
    refused with the value-discipline message, ZERO bytes moving."""
    for good in (0, 1):
        registers.ensure_writable(registers.WRITE_REG_POWER, good)  # must not raise
    for bad in (2, None):
        with expect_raises(ValueError, "accepts only values"):
            registers.ensure_writable(registers.WRITE_REG_POWER, bad)
    # the one-argument form (the historic callers' signature) refuses
    with expect_raises(ValueError, "accepts only values"):
        registers.ensure_writable(registers.WRITE_REG_POWER)


def test_guard_power_value_allowlist_permanent() -> None:
    """AC-3/AC-4 (spec 022): 1011 accepts ONLY 0/1 — the value rail is
    PERMANENT (no gate to open); any other value, any other control
    register (mode 1012/1013, mute 1076) and a valueless control write
    all raise. The base allowlist never leaks into the control plane."""
    registers.ensure_writable(registers.WRITE_REG_POWER, 0)  # must not raise
    registers.ensure_writable(registers.WRITE_REG_POWER, 1)  # must not raise
    with expect_raises(ValueError, "accepts only values"):
        registers.ensure_writable(registers.WRITE_REG_POWER, 2)
    with expect_raises(ValueError, "accepts only values"):
        registers.ensure_writable(registers.WRITE_REG_POWER, None)
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(registers.WRITE_REG_MODE, 1)
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(registers.WRITE_REG_MODE_PAIR, 0)
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(registers.WRITE_REG_MUTE, 1)


def test_guard_base_block_rules_unchanged() -> None:
    """AC-4: the base-block rules are byte-identical to 0.11.0 —
    {8,13,14} pass with AND without a value (the backward-compatible
    signature), 19-21 stay behind WRITE_MODE_SETPOINT_TOO, and the
    never-writable set still raises: the installer families, the high
    blocks and the READ home 2011 (never written — contract 016 §2)."""
    for address in (registers.REG_POWER, registers.REG_MODE, registers.REG_SET_CURRENT_TEMP):
        registers.ensure_writable(address)  # one-arg form: unchanged
        registers.ensure_writable(address, 0)  # value threads through: unchanged
    for address in (15, 1004, 2001, 2090):
        with expect_raises(ValueError, "not writable", "spec 003"):
            registers.ensure_writable(address)
    # a read home is never writable — permanently (spec 022: the value
    # rails are the only guards left, and 2011 is in none of them)
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(registers.READ_MIRROR_POWER, 0)
    # 19-21 behind the default-off mirror switch (spec 003 §6 verbatim)
    for address in (19, 20, 21):
        with expect_raises(ValueError):
            registers.ensure_writable(address)
    with _patch_attr(registers, "WRITE_MODE_SETPOINT_TOO", True):
        for address in (19, 20, 21):
            registers.ensure_writable(address)  # must not raise
        # the mirror switch never touches the control plane's value
        # rail: 1011 accepts 1 (the default world) and still refuses 2
        registers.ensure_writable(registers.WRITE_REG_POWER, 1)  # {0, 1}
        with expect_raises(ValueError, "accepts only values"):
            registers.ensure_writable(registers.WRITE_REG_POWER, 2)


# ===========================================================================
# Part B — the probe seam (the real client over the recording bleak fake)
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


def test_probe_seam_wire_parity() -> None:
    """FR-4 step 3 / FR-6: the probe's send path writes the EXACT
    pinned bytes on the write bucket WITHOUT response (app parity —
    research 010 §2.3; the acknowledged path is never used)."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    asyncio.run(client.write_probe_frame(bytes.fromhex(POWER_ON_HEX)))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(POWER_ON_HEX), False)], fake.writes
    fake.writes.clear()
    asyncio.run(client.write_probe_frame(bytes.fromhex(POWER_OFF_HEX)))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(POWER_OFF_HEX), False)], fake.writes


def test_probe_seam_pinned_frame_rail() -> None:
    """FR-6: the seam accepts ONLY the two pinned power frames — any
    other frame raises ValueError BEFORE a byte goes out (the probe
    can never send an unpinned register or value by mistake)."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    for junk in (
        b"",  # empty
        bytes.fromhex("631003f30001020002" + "0000"),  # value 2 at 1011
        bytes.fromhex(MUTE_ON_HEX),  # a pinned frame of ANOTHER register
        bytes.fromhex(POWER_ON_HEX)[:-1],  # truncated
        b"\x63\x10",  # fragment
    ):
        with expect_raises(ValueError, "pinned"):
            asyncio.run(client.write_probe_frame(junk))
    assert fake.writes == [], "a refused frame must never reach the wire"


# ===========================================================================
# Parts C/D — synthetic-package imports (the 015 pattern, one stub set)
# ===========================================================================

# The stub classes are defined HERE and attached to the stub modules so
# every imported platform binds the SAME objects (the 015 file's
# isolation rationale: the HomeAssistantError the read-only refusal
# raises must be catchable regardless of import order).


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


class ButtonEntity:
    def __init__(self) -> None:  # the real base takes no args
        pass


class SensorEntity:  # pragma: no cover — attribute container only
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

    def async_request_refresh(self):
        return asyncio.sleep(0)  # an awaitable no-op (the debounced read)

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
    coordinator.py + entity.py + climate.py + button.py (the 015 test
    pattern extended with the button platform), PLUS the bluetooth/
    translation helpers of the 011 install: this file is the LAST
    stub installer of the suite (alphabetical collection), so the
    call-time lazy imports of the sibling files' fakes must keep
    resolving (the 011 file's own last-installer rationale)."""
    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_climate.HVACAction = HVACAction
    ha_climate.ClimateEntity = ClimateEntity
    ha_climate.ClimateEntityFeature = ClimateEntityFeature
    ha_button = types.ModuleType("homeassistant.components.button")
    ha_button.ButtonEntity = ButtonEntity
    ha_sensor = types.ModuleType("homeassistant.components.sensor")
    ha_sensor.SensorEntity = SensorEntity
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
    ha_components.button = ha_button
    ha_components.sensor = ha_sensor
    ha_components.bluetooth = ha_bt
    ha.util = ha_util
    ha_helpers.update_coordinator = ha_uc
    ha_helpers.device_registry = ha_dr
    ha_helpers.entity = ha_ent
    ha_helpers.entity_platform = ha_ep

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.climate": ha_climate,
        "homeassistant.components.button": ha_button,
        "homeassistant.components.sensor": ha_sensor,
        "homeassistant.components.bluetooth": ha_bt,
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
    """Import a component module under the synthetic package (the 015
    pattern — one stub set, every platform binding the same objects)."""
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
coordinator_mod = _import_pkg_module("coordinator")  # idempotent
climate_mod = _import_pkg_module("climate")
button_mod = _import_pkg_module("button")
pkg_aqua_client = sys.modules[f"{PKG}.aqua_client"]
pkg_const = sys.modules[f"{PKG}.const"]


# ---------------------------------------------------------------------------
# The scripted probe client (Parts C/D)
# ---------------------------------------------------------------------------


def _capture(raws: dict[int, int]) -> SimpleNamespace:
    """A duck-typed PushCapture for the 2001 block, stamped NOW (the
    probe's freshness gate compares last_seen against its own stamps —
    same monotonic clock, delivered strictly after the stamp)."""
    ts = time.monotonic()
    return SimpleNamespace(
        start=2001,
        qty=45,
        length=99,
        registers=dict(raws),
        first_seen=ts,
        last_seen=ts,
        count=1,
    )


class ScriptedProbeClient:
    """Duck-typed client driving the probe's verdict paths.

    Every TX (the trigger read or a probe write) delivers the next
    scripted mirror value as a FRESH 2001 capture; a None entry delivers
    nothing (a silent window). write_errors scripts PER-WRITE failures
    (the Nth entry raises if it is an exception — a raising write never
    delivers, exactly like a refused GATT write). Records: trigger
    reads, probe writes (raw bytes), funnel writes, connect kwargs,
    disconnects.
    """

    def __init__(
        self,
        mirrors: list[int | None] | None = None,
        *,
        write_errors: list[BaseException | None] | None = None,
        is_connected: bool = True,
    ) -> None:
        self.mirrors = list(mirrors or [])
        self.write_errors = list(write_errors or [])
        self.is_connected = is_connected
        self.connect_kwargs: dict | None = None
        self.disconnected = False
        self.read_calls = 0
        self.probe_writes: list[bytes] = []
        self.funnel_writes: list[tuple[int, list[int]]] = []
        self.control_writes: list[tuple[int, int]] = []
        self.setpoint_writes: list[int] = []
        self._store: dict[int, SimpleNamespace] = {}

    def _deliver(self) -> None:
        if not self.mirrors:
            return
        value = self.mirrors.pop(0)
        if value is None:
            return  # a silent window — nothing fresh arrives
        self._store[2001] = _capture({2011: value})

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls += 1
        self._deliver()
        raise pkg_aqua_client.AquaTempTimeoutError(
            "no matching response within 8.0s (received 4 frame(s) / 396 bytes)"
        )

    async def write_probe_frame(self, frame: bytes) -> None:
        self.probe_writes.append(bytes(frame))
        if len(self.probe_writes) <= len(self.write_errors):
            error = self.write_errors[len(self.probe_writes) - 1]
            if error is not None:
                raise error  # a refused write never delivers a burst
        self._deliver()

    async def write_control(self, address, value):
        self.control_writes.append((address, value))

    async def write_setpoint(self, value_raw: int) -> None:
        self.setpoint_writes.append(value_raw)

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


def _make_probe_coordinator(client: ScriptedProbeClient, *, options=None):
    entry = SimpleNamespace(
        data={"address": ADDRESS, "mode": "push"},
        options=dict(options or {}),
    )
    return coordinator_mod.AquaTempCoordinator(hass=None, entry=entry, client=client)


class _LogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


@contextlib.contextmanager
def _probe_logs():
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


def _verdict_lines(cap: _LogCap) -> list[str]:
    """The verdict lines (the no-address shape) — the stage lines carry
    the [address] bracket, so the two shapes never collide."""
    return [
        msg
        for _lvl, msg in cap.records
        if msg.startswith("power write probe: ")
    ]


def _run_probe(mirrors, *, write_errors=None, is_connected=True, options=None):
    """Run the probe over a scripted client; return (client, result, cap).

    The fresh-mirror window is patched to 0.15 s: the scripted deliveries
    land inside the TX calls (instant), so only the silent-window tests
    ever see the window elapse — the production 20 s constant would
    make them crawl (the coordinator reads it at call time — the _now
    seam's module-attribute pattern)."""
    client = ScriptedProbeClient(
        mirrors, write_errors=write_errors, is_connected=is_connected
    )
    coordinator = _make_probe_coordinator(client, options=options)
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _probe_logs() as cap:
            result = asyncio.run(coordinator.async_power_write_probe())
    return client, result, cap


# ===========================================================================
# Part C — the probe's verdict paths (AC-5..AC-8, FR-4/FR-6)
# ===========================================================================


def test_probe_executed_reversible() -> None:
    """AC-5/AC-8: the mirror following 0→1→0 yields executed+reversible;
    exactly TWO writes go out — ON then OFF, byte-equal to the pinned
    vectors — the verdict line (WARNING) matches the spec string and
    names the write char; every stage line is at WARNING; the client
    disconnects on completion."""
    client, result, cap = _run_probe([0, 1, 0])
    assert result["verdict"] == "executed+reversible", result
    assert client.probe_writes == [
        bytes.fromhex(POWER_ON_HEX),
        bytes.fromhex(POWER_OFF_HEX),
    ], client.probe_writes
    assert result["frames_sent"] == [POWER_ON_HEX, POWER_OFF_HEX]
    assert result["mirror"]["baseline"]["raw"] == 0
    assert result["mirror"]["after_on"]["raw"] == 1
    assert result["mirror"]["after_off"]["raw"] == 0
    assert result["write_uuid"] == UUID_WRITE
    assert client.disconnected, "the probe must disconnect on completion"
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        "power write probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0→1→0)"
        " — the write is proven (spec 022: the control is active by default)"
    ), lines[0]
    assert UUID_WRITE in lines[0], lines[0]
    # every stage observation + the verdict log at WARNING (the
    # operator's panel keeps WARNING+, contract 012 §2); the trigger's
    # timed-out-as-expected plumbing line is the one allowed exception
    # (the poll cycle's own precedent logs the same event below WARNING
    # — it is the expected world, not evidence, spec 014 M-4).
    probe_lines = [(lvl, m) for lvl, m in cap.records if m.startswith("power write probe")]
    assert all(
        lvl == logging.WARNING for lvl, m in probe_lines if "timed out" not in m
    ), probe_lines


def test_probe_connects_when_disconnected() -> None:
    """FR-4 step 1: the probe connects (a bare connect call — spec 021:
    the pair_on_connect option was pruned with the pairing ladder) when
    the client is not already connected."""
    client, result, _cap = _run_probe([0, 1, 0], is_connected=False)
    assert client.connect_kwargs == {}, client.connect_kwargs
    assert result["verdict"] == "executed+reversible"


def test_probe_not_executed() -> None:
    """AC-6a: the mirror staying 0 after ON yields not-executed — the
    unit did not act on our frame (the bond-gate residual); only ONE
    write goes out (no OFF write on a confirmed-off unit)."""
    client, result, cap = _run_probe([0, 0])
    assert result["verdict"] == "not-executed", result
    assert client.probe_writes == [bytes.fromhex(POWER_ON_HEX)]
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "not-executed" in lines[0]
    assert "stayed 0 after ON" in lines[0], lines[0]
    assert client.disconnected


def test_probe_not_executed_via_retry_trigger() -> None:
    """FR-4 step 4 (the robust order): a silent post-ON window sends
    ONE base-read trigger and waits once more — the retried harvest
    reads the still-0 mirror and the verdict is not-executed."""
    client, result, cap = _run_probe([0, None, 0])
    assert result["verdict"] == "not-executed", result
    assert client.probe_writes == [bytes.fromhex(POWER_ON_HEX)]
    assert client.read_calls == 2, (
        "the baseline trigger + exactly ONE retry trigger",
        client.read_calls,
    )
    assert client.disconnected
    assert len(_verdict_lines(cap)) == 1


def test_probe_not_reversible() -> None:
    """AC-6b: the mirror staying 1 after OFF yields not-reversible and
    the verdict line carries the remediation (contract 016 §7: a failed
    revert is never silent — the owner is told how to turn it off)."""
    client, result, cap = _run_probe([0, 1, 1])
    assert result["verdict"] == "not-reversible", result
    assert client.probe_writes == [
        bytes.fromhex(POWER_ON_HEX),
        bytes.fromhex(POWER_OFF_HEX),
    ]
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "not-reversible" in lines[0]
    assert "turn the unit OFF from the vendor app or the panel" in lines[0], lines[0]
    assert "stayed 1 after OFF" in lines[0], lines[0]
    assert client.disconnected


def test_probe_refused_baseline_not_off() -> None:
    """AC-7: a baseline mirror != 0 aborts with refused-baseline-not-off
    and sends ZERO frames — the probe never toggles an unverified
    state (the mandatory baseline gate, FR-6)."""
    client, result, cap = _run_probe([1])
    assert result["verdict"] == "refused-baseline-not-off", result
    assert client.probe_writes == [], "nothing may be written on a refused baseline"
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        "power write probe: refused-baseline-not-off (mirror 2011 = 1)"
    ), lines[0]
    assert "nothing was written" in lines[0], lines[0]
    assert client.disconnected


def test_probe_att_error() -> None:
    """FR-4 step 6: a raising write yields att-error with the failure
    chain VERBATIM (the ATT error text is the diagnosis) and the write
    char named in the line."""
    chain_exc = RuntimeError(
        "BleakGATTProtocolError: ATT error: Insufficient Authentication"
    )
    client, result, cap = _run_probe([0], write_errors=[chain_exc])
    assert result["verdict"] == "att-error", result
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "att-error: " in lines[0], lines[0]
    assert "Insufficient Authentication" in lines[0], lines[0]
    assert UUID_WRITE in lines[0], lines[0]
    assert client.disconnected


def test_probe_no_report() -> None:
    """FR-4 step 6: a silent baseline window (no fresh 2001 block)
    yields no-report — nothing was written, the radio/link lost the
    report (quickstart: just try again)."""
    client, result, cap = _run_probe([])
    assert result["verdict"] == "no-report", result
    assert client.probe_writes == []
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert lines[0].startswith("power write probe: no-report (no fresh 2001 block)"), lines[0]
    assert client.read_calls == 1, "no retry trigger at the baseline stage"
    assert client.disconnected


def test_probe_no_report_after_off_names_remediation() -> None:
    """Contract 016 §7 (the revert obligation): when the post-OFF
    window stays silent the unit's final state is UNCONFIRMED — the
    verdict line must tell the owner to check the panel and how to turn
    the unit off (never silently leave a possibly-on unit)."""
    client, result, cap = _run_probe([0, 1, None, None])
    assert result["verdict"] == "no-report", result
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "no fresh 2001 block after OFF" in lines[0], lines[0]
    assert "UNCONFIRMED" in lines[0], lines[0]
    assert "turn the unit OFF from the vendor app or the panel" in lines[0], lines[0]
    # both writes went out (the revert was attempted)
    assert client.probe_writes == [
        bytes.fromhex(POWER_ON_HEX),
        bytes.fromhex(POWER_OFF_HEX),
    ]
    # reads: the baseline trigger + the post-OFF retry (the ON/OFF TXs
    # are writes, not reads)
    assert client.read_calls == 2, client.read_calls
    assert client.disconnected


def test_probe_att_error_on_off_names_remediation() -> None:
    """Contract 016 §7: when the OFF write itself raises after the ON
    was confirmed, the att-error line carries the remediation — the
    unit is likely still on."""
    client, result, cap = _run_probe(
        [0, 1], write_errors=[None, RuntimeError("link down")]
    )
    assert result["verdict"] == "att-error", result
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "att-error: " in lines[0] and "link down" in lines[0], lines[0]
    assert "the unit may still be ON" in lines[0], lines[0]
    assert "turn the unit OFF from the vendor app or the panel" in lines[0], lines[0]
    assert client.disconnected


def test_probe_uses_pinned_frames_only() -> None:
    """FR-6: the frames that reach the seam are EXACTLY the registers'
    pinned strings — byte-equal, 11 bytes each (AC-8's byte-equality on
    the successful path)."""
    client, result, _cap = _run_probe([0, 1, 0])
    assert result["verdict"] == "executed+reversible"
    for frame in client.probe_writes:
        assert len(frame) == 11
        assert frame.hex() in (
            registers.WRITE_FRAME_POWER_ON,
            registers.WRITE_FRAME_POWER_OFF,
        )


def test_write_funnel_delivers_control_by_default() -> None:
    """T3 (spec 022, the inversion of the gate-threaded funnel):
    async_write_register passes the value to the guard — the funnel
    delivers (1011, [1]) to the client BY DEFAULT (the PERMANENT value
    rail is the only guard); an off-rail value raises before ANY
    client I/O; the base-block path is unchanged."""
    client = ScriptedProbeClient()
    coordinator = _make_probe_coordinator(client)
    asyncio.run(coordinator.async_write_register(1011, 1))
    assert client.funnel_writes == [(1011, [1])], client.funnel_writes
    # the base path is unchanged (the value threads through harmlessly)
    asyncio.run(coordinator.async_write_register(8, 1))
    assert client.funnel_writes == [(1011, [1]), (8, [1])], client.funnel_writes
    # value discipline through the funnel — the PERMANENT rail, no gate
    with expect_raises(ValueError, "accepts only values"):
        asyncio.run(coordinator.async_write_register(1011, 2))
    assert client.funnel_writes == [(1011, [1]), (8, [1])], client.funnel_writes


# ===========================================================================
# Part D — the surfaces (AC-9/AC-10)
# ===========================================================================


def _make_surface_coordinator(client, snapshot, options=None):
    entry = SimpleNamespace(
        entry_id="test_entry",
        title="Aqua Temp test",
        data={"address": ADDRESS, "mode": "push"},
        options=dict(options or {}),
    )
    coordinator = coordinator_mod.AquaTempCoordinator(hass=None, entry=entry, client=client)
    coordinator.data = snapshot
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}})
    return hass, entry, coordinator


def test_button_platform_adds_power_write_probe() -> None:
    """AC-9: the diagnostic button — the platform adds TWO buttons, the
    new one named 'Power write probe', DIAGNOSTIC category, unique_id
    suffix power_write_probe; pressing it RUNS the probe (the frames go
    out, the verdict lands, the link recycles)."""
    client = ScriptedProbeClient([0, 1, 0])
    hass, entry, _coordinator = _make_surface_coordinator(client, None)
    added: list = []

    def _collect(entities) -> None:
        added.extend(entities)

    asyncio.run(button_mod.async_setup_entry(hass, entry, _collect))
    # Spec 019 T7 added a THIRD button (the setpoint probe); the 016
    # power probe keeps its exact assertions — selected BY NAME now.
    assert len(added) == 3, [type(e).__name__ for e in added]
    probe_btn = next(e for e in added if e._attr_name == "Power write probe")
    assert probe_btn._attr_entity_category == EntityCategory.DIAGNOSTIC
    assert "power_write_probe" in probe_btn._attr_unique_id
    # the probe-registers button is unchanged (non-regression)
    assert added[0]._attr_name == "Probe registers"
    # the spec-019 sibling routes to the setpoint probe (its own suite)
    assert added[2]._attr_name == "Setpoint write probe"
    # pressing runs the probe end-to-end on the scripted client
    with _probe_logs():
        asyncio.run(probe_btn.async_press())
    assert client.probe_writes == [
        bytes.fromhex(POWER_ON_HEX),
        bytes.fromhex(POWER_OFF_HEX),
    ], client.probe_writes
    assert client.disconnected


def test_climate_full_surface_while_active() -> None:
    """Spec 022 (the inversion of the AC-10 read-only world): the
    push-mode climate is created with NO options and its commands
    WRITE — turn_on reaches the client's control rail; the heat-only
    rule (COOL refused) stands; the probe seam stays untouched (the
    climate uses the confirmation cycle, never the probe frames)."""
    caps = {
        2001: _capture({2010: 2001, 2011: 1, 2012: 1, 2017: 13, 2018: 168, 2019: 4}),
    }
    snapshot = coordinator_mod.AquaTempData.from_pushes(caps)
    client = ScriptedProbeClient()
    hass, entry, _coordinator = _make_surface_coordinator(client, snapshot)
    added: list = []

    def _collect(entities) -> None:
        added.extend(entities)

    asyncio.run(climate_mod.async_setup_entry(hass, entry, _collect))
    assert len(added) == 1, [type(e).__name__ for e in added]
    entity = added[0]
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.05):
        asyncio.run(entity.async_turn_on())
    assert client.control_writes == [(1011, 1)], client.control_writes
    # the heat-only rule stands (the mode write is refused — spec 018)
    with expect_raises(HomeAssistantError, "heat-only"):
        asyncio.run(entity.async_set_hvac_mode(HVACMode.COOL))
    # the probe seam is never touched by the climate (the cycle's rail)
    assert client.probe_writes == []


def test_service_surfaces_declared() -> None:
    """AC-9 (the service half): the power_write_probe service is
    declared in services.yaml, strings.json and both translations."""
    services_yaml = (COMPONENT_DIR / "services.yaml").read_text()
    assert "power_write_probe:" in services_yaml, "services.yaml misses the service"
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text())
    assert "power_write_probe" in strings["services"], strings["services"].keys()
    assert "fields" in strings["services"]["power_write_probe"]
    for lang in ("en", "fr"):
        translations = json.loads((COMPONENT_DIR / "translations" / f"{lang}.json").read_text())
        assert "power_write_probe" in translations.get("services", {}), (
            lang,
            translations.get("services", {}).keys(),
        )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Part A — data + builder + guard
        test_write_plane_constants_transcribed,
        test_read_map_untouched,
        test_pinned_control_frame_vectors,
        test_builder_cross_checks_registers_pinned_strings,
        test_write_plane_labels_annotated,
        test_guard_control_value_rail_by_default,
        test_guard_power_value_allowlist_permanent,
        test_guard_base_block_rules_unchanged,
        # Part B — the seam
        test_probe_seam_wire_parity,
        test_probe_seam_pinned_frame_rail,
        # Part C — the probe's verdict paths
        test_probe_executed_reversible,
        test_probe_connects_when_disconnected,
        test_probe_not_executed,
        test_probe_not_executed_via_retry_trigger,
        test_probe_not_reversible,
        test_probe_refused_baseline_not_off,
        test_probe_att_error,
        test_probe_no_report,
        test_probe_no_report_after_off_names_remediation,
        test_probe_att_error_on_off_names_remediation,
        test_probe_uses_pinned_frames_only,
        test_write_funnel_delivers_control_by_default,
        # Part D — the surfaces
        test_button_platform_adds_power_write_probe,
        test_climate_full_surface_while_active,
        test_service_surfaces_declared,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-016 write-control tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())