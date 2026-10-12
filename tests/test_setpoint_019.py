#!/usr/bin/env python3
"""Spec 019 tests — the setpoint control: the read home, the write-plane
data, the guard, the two-phase probe and the gated climate surface
(FR-1..FR-12, AC-1..AC-11).

Part A (standalone modules — the test_registers pattern): the read home
(PUSHED_CONTROL["setpoint"] = 1046 — [P owner diff 2026-10-12]), the
write-plane constants (WRITE_REG_SETPOINT, the family map, the range/step
raws, ALLOWED_WRITE_SETPOINT, WRITE_SETPOINT_PROVEN=False), the parity
vectors (build_write_control(1046, 300/295/310) == research 019 §6's
pinned frames — CRC re-verified independently in this repo — plus the
build_write_registers cross-check, AC-1), the guard matrix (AC-2: both
setpoint gate surfaces False → 1046 refused exactly as 0.13.2 refused
it; AC-3: the value discipline 100..400 step 5, the address allowlist,
the power/base rules byte-identical), the integrity check (1046
in-window, collision-free) and the corrected ladder labels.

Part B (the client seams — the 016 fake-bleak pattern): write_setpoint_
probe_frame + write_setpoint put the EXACT pinned bytes on the write
bucket WITHOUT response (app parity — research 010 §2.3), and the rail
refuses out-of-range / off-step / non-int values BEFORE a byte goes out;
not connected raises AquaTempConnectionError.

Part C (coordinator, synthetic-package import — the 015/016 pattern):
the FR-1 block-source decode (a store with ONLY a fresh 1046 block →
the target decodes; the 2001 block only → target None — AC-4; the gate
closed → None), then the two-phase probe over the scripted client:
AC-5 (S → no-op → S → T → revert → S → executed+reversible + ONE
WARNING verdict line with the <S>→<T>→<S> raws), AC-6 (a scripted
NEIGHBOR change during the no-op → no-op-neighbor-changed naming the
moved register(s), exactly ONE write, NO phase-B frame), AC-7
(not-executed / not-reversible with the remediation + the CURRENT
value / refused-baseline sentinel + out-of-range + off-step + absent
with ZERO writes / no-report at every stage / att-error with the chain
verbatim), AC-8 (only values from {S, T} reach the seam; the classic
funnel is never called; the probe runs with BOTH gate surfaces closed
— it is the proof instrument, not a control surface).

Part D (the confirmation cycle, AC-9): the gate closed → the guard's
locked message with ZERO client I/O; the validation errors (30.3 → the
step, 41.0/45.0/9.5 → the range — NEVER clamped, contract 019 §2);
confirmed (the audit INFO + the verdict INFO lines EXACT, the snapshot
pushed reflects the MIRROR, ONE trigger, NO disconnect); not-confirmed
(the WARNING names BOTH values; the tile shows the MIRROR's truth);
unconfirmed (NO snapshot push, NO persistent notification — contract
019 §5.3's deliberate divergence); a STALE capture never confirms; a
write error propagates with zero post-write reads; the write is allowed
with the power OFF in the snapshot [P owner]; the END-TO-END frame over
the real client is byte-equal to the pinned vector.

Part E (the surfaces, AC-2/AC-10): the climate gate matrix (setpoint
gate closed: NO TARGET_TEMPERATURE feature, the 015 refusal
byte-identical, ZERO I/O; gate open: TARGET_TEMPERATURE + the write
path; the gates COMPOSE — setpoint open with power closed advertises
the target alone; the read display is independent of the write gate),
the bounds from the guard's own constants (10.0/40.0/0.5), the
last_setpoint_write diagnostics attribute, the activation property's
OR matrix + the no-setter source scan, the diagnostic "Setpoint write
probe" button (routes to the probe), the options-flow checkbox (default
False) and the strings/translations declaring the service + the option
with the required verdict line.

Red-first on a7305ba (0.13.2): none of the setpoint surfaces exist —
the read home is None (no target displayed), the write constants, the
guard branch, the seams, the probe, the activation property, the
climate write surface, the button and the option are all absent — so
every test below fails until the implementation lands.

Run standalone:

    python3 tests/test_setpoint_019.py

No real Bluetooth and no real Home Assistant required (fake modules
are installed when the real ones are absent).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import math
import re
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
PKG = "aqua_temp_ble_setpoint_019"  # synthetic package (distinct from siblings)
UUID_WRITE = const.UUID_WRITE

# The pinned setpoint frames [P — research 019 §6]: CRC16-Modbus computed
# through the app's own builder pipeline and re-verified independently
# against the modbus module in this repo (the crc16 reproduction of
# research §6).
SETPOINT_300_HEX = "63100416000102012c5049"  # 30.0 °C -> register 1046
SETPOINT_295_HEX = "631004160001020127118e"  # 29.5 °C -> register 1046
# 31.0 °C -> 310 = 0x0136 by the PROVEN value formula (temp x 10,
# research 019 §2 [P disassembly]). Research §6's third row prints the
# body ...0132 (= 306 = 30.6 °C) — an internal arithmetic slip: 306 is
# off the vendor's own 0.5-grained value space (306 % 5 != 0) and off
# the proven formula. The two mandated rows (300/295) are byte-exact
# and pinned as printed; this third vector is pinned from the verified
# pipeline, not from the slip.
SETPOINT_310_HEX = "631004160001020136d182"  # 31.0 °C -> register 1046

# The owner's 1046-block ladder capture (the 015 suite's LADDER_RAWS —
# the production block; 1046 = 290 = 29.0 °C, the pre-diff value
# [P owner diff 2026-10-12: 290 -> 300 across the panel's +1.0 °C]).
LADDER_RAWS = {
    1046: 290, 1047: 5, 1048: 5, 1049: 150, 1050: 400, 1051: 270,
    1052: 5, 1053: 5, 1054: 100, 1055: 350, 1056: -70, 1057: 130,
    1058: 40, 1059: 8, 1060: 3, 1068: 270,
}
S_RAW = 290  # the probe's baseline (29.0 °C)
T_RAW = 285  # the probe's test value (S - 5 -> 28.5 °C, toward the interior)

# The pushed-plane companions (the 015/017 suites' vectors).
CONTROL_BLOCK_OFF = {2010: 2001, 2012: 1, 2017: 13, 2018: 168, 2011: 0, 2019: 0}
CONTROL_BLOCK_ON = {2010: 2001, 2012: 1, 2017: 13, 2018: 168, 2011: 1, 2019: 4}
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
# Part A — the read home, the write-plane data, the guard (standalone)
# ===========================================================================


def test_read_home_pinned() -> None:
    """FR-1: the setpoint read home is register 1046 — the owner's panel
    diff (290 -> 300 across +1.0 °C, research 019 §1 [P owner]); the
    proof oracle aliases the home (write target == read mirror)."""
    assert registers.PUSHED_CONTROL["setpoint"] == 1046
    assert registers.READ_MIRROR_SETPOINT == 1046
    assert registers.READ_MIRROR_SETPOINT == registers.PUSHED_CONTROL["setpoint"]
    # the pushed block that carries the home
    assert registers.pushed_block_start(1046) == 1046


def test_write_plane_constants_transcribed() -> None:
    """FR-2: the write-plane constants — the heat write target adjudicated
    from the family map [A-strong — research 019 §5], the guard's range
    and step [D — research 019 §7], the family map as DATA [P — §4.1]."""
    assert registers.WRITE_REG_SETPOINT == 1046
    assert registers.SETPOINT_MIN_RAW == 100   # 10.0 °C
    assert registers.SETPOINT_MAX_RAW == 400   # 40.0 °C
    assert registers.SETPOINT_STEP_RAW == 5    # the 0.5 °C granularity
    assert registers.WRITE_SETPOINT_FAMILY_MAP == {
        1: (1135, 1136, 1137),
        2: (1051, 1046, 1068),
        3: (1073, 1074, 1075),
    }
    # the family-2 heat slot IS the pinned read home (the adjudication)
    assert registers.WRITE_SETPOINT_FAMILY_MAP[2][1] == registers.READ_MIRROR_SETPOINT


def test_setpoint_parity_vectors() -> None:
    """FR-3/AC-1: build_write_control(1046, ·) reproduces research 019
    §6's pinned frames byte-exact — 11 bytes, qty 1, bytecount 02, CRC
    valid — and the generic funnel agrees (one source of truth)."""
    vectors = [
        (300, SETPOINT_300_HEX),  # 30.0 °C
        (295, SETPOINT_295_HEX),  # 29.5 °C
        (310, SETPOINT_310_HEX),  # 31.0 °C
    ]
    for raw, hexstr in vectors:
        frame = modbus.build_write_control(1046, raw)
        expected = bytes.fromhex(hexstr)
        assert frame == expected, (raw, frame.hex(), hexstr)
        assert len(frame) == 11, (raw, len(frame))
        assert modbus.validate_crc(frame), raw
        # the single-register control shape: qty 1, bytecount 02
        assert frame[4:6] == b"\x00\x01" and frame[6] == 0x02, raw
    # AC-1 cross-check: the classic funnel and the control builder agree
    assert modbus.build_write_registers(1046, [300]) == modbus.build_write_control(
        1046, 300
    )


def test_allowed_write_setpoint_data() -> None:
    """FR-4: the address AND value allowlist — 1046 accepts ONLY the raw
    range 100..400 in steps of 5 (10.0-40.0 °C, 0.5 granularity); the
    allowlist is PERMANENT (contract 019 §2.2)."""
    assert registers.ALLOWED_WRITE_SETPOINT == {
        registers.WRITE_REG_SETPOINT: frozenset(
            range(registers.SETPOINT_MIN_RAW, registers.SETPOINT_MAX_RAW + 1,
                  registers.SETPOINT_STEP_RAW)
        ),
    }
    allowed = registers.ALLOWED_WRITE_SETPOINT[1046]
    assert 100 in allowed and 400 in allowed and 295 in allowed
    assert 99 not in allowed and 401 not in allowed
    assert 297 not in allowed and 292 not in allowed  # off the 0.5 step


def test_guard_shipped_default_locked() -> None:
    """AC-2: with both setpoint gate surfaces False (the shipped default),
    ensure_writable refuses 1046 for every value — the setpoint plane
    stays locked exactly as 0.13.2 refused it, and the error names the
    evidence gate."""
    assert registers.WRITE_SETPOINT_PROVEN is False
    for value in (100, 300, 400):
        with expect_raises(ValueError, "not writable", "WRITE_SETPOINT_PROVEN"):
            registers.ensure_writable(1046, value)
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(1046)  # the valueless form refuses too
    # the message names the required probe verdict (the gate's cause)
    try:
        registers.ensure_writable(1046, 300)
    except ValueError as err:
        assert "EXECUTED + REVERSIBLE" in str(err), str(err)
    else:
        raise AssertionError("the locked message did not raise")


def test_guard_value_discipline_when_open() -> None:
    """AC-3: with the gate open (the owner's post-verdict flip), 1046
    accepts ONLY the allowlist — the range AND the step are enforced,
    and the value-discipline message names both."""
    with _patch_attr(registers, "WRITE_SETPOINT_PROVEN", True):
        for good in (100, 105, 295, 300, 400):
            registers.ensure_writable(1046, good)  # must not raise
        for bad in (99, 401, 297, 292, 1000, -5):
            with expect_raises(ValueError, "accepts only values"):
                registers.ensure_writable(1046, bad)
        with expect_raises(ValueError, "accepts only values"):
            registers.ensure_writable(1046, None)
        try:
            registers.ensure_writable(1046, 297)
        except ValueError as err:
            # the range and the step ride in the text (T3)
            assert "100" in str(err) and "400" in str(err), str(err)
            assert "5" in str(err), str(err)  # the step
        else:
            raise AssertionError("the value-discipline message did not raise")
    # restored: the gate is closed again
    assert registers.WRITE_SETPOINT_PROVEN is False
    with expect_raises(ValueError):
        registers.ensure_writable(1046, 300)


def test_guard_setpoint_allowed_thread() -> None:
    """FR-4/AC-3: the `setpoint_allowed` thread — the OPTION surface
    honors the write while the module switch stays the shipped gate for
    every other caller; the base scope ignores the thread entirely."""
    registers.ensure_writable(1046, 300, setpoint_allowed=True)  # must not raise
    with expect_raises(ValueError, "WRITE_SETPOINT_PROVEN"):
        registers.ensure_writable(1046, 300, setpoint_allowed=False)
    with expect_raises(ValueError, "accepts only values"):
        registers.ensure_writable(1046, 297, setpoint_allowed=True)
    # the address allowlist: the ladder neighbours are NEVER writable —
    # even with the gate open (contract 019 §7.2)
    for addr in (1045, 1047, 1049, 1051, 1068):
        with expect_raises(ValueError, "not writable"):
            registers.ensure_writable(addr, 300, setpoint_allowed=True)


def test_guard_power_and_base_rules_unchanged() -> None:
    """AC-3: the power rules and the base rules are byte-identical —
    1011 stays behind WRITE_CONTROL_PROVEN/{0,1}, the base allowlist
    {8, 13, 14} passes with and without a value, the read homes stay
    unwritable, and the setpoint thread never leaks into them."""
    with expect_raises(ValueError, "WRITE_CONTROL_PROVEN"):
        registers.ensure_writable(1011, 1)
    registers.ensure_writable(1011, 1, control_allowed=True)  # must not raise
    with expect_raises(ValueError, "accepts only values"):
        registers.ensure_writable(1011, 2, control_allowed=True)
    for address in (8, 13, 14):
        registers.ensure_writable(address)  # the one-arg form: unchanged
        registers.ensure_writable(address, 0)  # the value threads harmlessly
    for address in (15, 2001, 2090):
        with expect_raises(ValueError, "spec 003"):
            registers.ensure_writable(address)
    # a read home is never writable — even with the setpoint gate open
    with expect_raises(ValueError, "not writable"):
        registers.ensure_writable(2011, 0, setpoint_allowed=True)
    # 19-21 stay behind WRITE_MODE_SETPOINT_TOO (spec 003 §6 verbatim)
    with expect_raises(ValueError):
        registers.ensure_writable(20, 300, setpoint_allowed=True)


def test_integrity_passes_with_setpoint_home() -> None:
    """FR-1: the shipped registries + the pinned setpoint home pass the
    integrity check (1046 is inside the control plane 1001-2090 and
    collides with nothing — FR-11d of 015 already allowed it)."""
    registers.check_registry_integrity()  # must not raise


def test_labels_corrected() -> None:
    """FR-1: the corrected ladder labels (research 019 §1.1) — 1046 is
    the PROVEN heat home, 1051/1068 the per-mode candidates, the bound
    candidates stay [A-weak]; the 015 'setpoint' substring contract
    still holds for the ladder five."""
    assert "setpoint home (heat)" in registers.KNOWN_LABELS[1046]
    assert "[P owner" in registers.KNOWN_LABELS[1046]
    assert "cool setpoint" in registers.KNOWN_LABELS[1051]
    assert "auto setpoint" in registers.KNOWN_LABELS[1068]
    assert "[A-strong" in registers.KNOWN_LABELS[1051]
    assert "[A-strong" in registers.KNOWN_LABELS[1068]
    # the bound candidates keep their [A-weak] tags (never promoted)
    assert "[A-weak" in registers.KNOWN_LABELS[1050]
    assert "[A-weak" in registers.KNOWN_LABELS[1054]
    # the 015 suite's substring contract (the ladder five)
    for reg in (1046, 1049, 1051, 1055, 1068):
        assert "setpoint" in registers.KNOWN_LABELS[reg].lower(), reg
    # the FR-10 candidate tag updated to the proven wording
    assert "[P owner" in registers.PUSH_CONTROL_CANDIDATES[1046]
    # the candidate key set is unchanged (gains nothing new)
    assert set(registers.PUSH_CONTROL_CANDIDATES) == {
        2011, 2019, 2012, 1046, 1049, 1051, 1055, 1068,
    }


def test_no_code_path_sets_the_setpoint_gate() -> None:
    """AC-10 (contract 019 §4): no code path, update, or default may
    ever SET either activation surface — a source scan: the only
    assignment to WRITE_SETPOINT_PROVEN is the False definition, and
    no component file ever writes the write_setpoint option key."""
    for py in sorted(COMPONENT_DIR.glob("*.py")):
        text = py.read_text()
        for match in re.finditer(r"WRITE_SETPOINT_PROVEN\s*=", text):
            assigned = text[match.start():match.start() + 60]
            assert "= False" in assigned, (py.name, assigned)
        assert not re.search(r"options\[[\"']write_setpoint[\"']\]\s*=", text), py.name
        assert '"write_setpoint": True' not in text, py.name
    assert registers.WRITE_SETPOINT_PROVEN is False  # the shipped default


# ===========================================================================
# Parts B/C/D/E — synthetic-package imports (the 015/016/017 pattern)
# ===========================================================================

# The stub classes are defined HERE and attached to the stub modules so
# every imported platform binds the SAME objects (the exception/enum
# identity the tests rely on — the 015 file's isolation rationale).


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
    """The 017 stub: records the snapshot pushes (async_set_updated_data
    is SYNC — the production TypeError's fix is pinned by keeping the
    stub honest)."""

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

    def async_set_updated_data(self, data):
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
    coordinator.py + climate.py + button.py + config_flow.py (the 017
    pattern extended with the button platform; the bluetooth/translation
    helpers of the 011 install)."""
    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_climate = types.ModuleType("homeassistant.components.climate")
    ha_climate.HVACMode = HVACMode
    ha_climate.HVACAction = HVACAction
    ha_climate.ClimateEntity = ClimateEntity
    ha_climate.ClimateEntityFeature = ClimateEntityFeature
    ha_button = types.ModuleType("homeassistant.components.button")
    ha_button.ButtonEntity = ButtonEntity
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
    ha_ent = types.ModuleType("homeassistant.helpers.entity")
    ha_ent.EntityCategory = EntityCategory
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
    ha_components.button = ha_button
    ha_components.bluetooth = ha_bt
    ha.util = ha_util
    ha_helpers.update_coordinator = ha_uc
    ha_helpers.device_registry = ha_dr
    ha_helpers.entity = ha_ent
    ha_helpers.entity_platform = ha_ep
    ha_helpers.translation = ha_translation

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.climate": ha_climate,
        "homeassistant.components.button": ha_button,
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
        "homeassistant.helpers.translation": ha_translation,
        "homeassistant.util": ha_util,
        "homeassistant.util.unit_conversion": ha_uc_conv,
    }.items():
        sys.modules[name] = module

    # voluptuous — the marker stand-in (the 011/017 pattern)
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
    """Import a component module under the synthetic package (the 017
    pattern — one stub set, every platform binding the same objects)."""
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
button_mod = _import_pkg_module("button")
# The config-flow import is guarded: in the RED state the 019 const
# surface (DEFAULT_WRITE_SETPOINT) is missing and the ImportError must
# fail the schema TEST, not the whole file's collection.
_CONFIG_FLOW_IMPORT_ERROR: Exception | None = None
try:
    config_flow_mod = _import_pkg_module("config_flow")
except ImportError as _exc:
    config_flow_mod = None
    _CONFIG_FLOW_IMPORT_ERROR = _exc
pkg_aqua_client = sys.modules[f"{PKG}.aqua_client"]
pkg_const = sys.modules[f"{PKG}.const"]


# ---------------------------------------------------------------------------
# The scripted setpoint client (Parts C/D/E) + the shared helpers
# ---------------------------------------------------------------------------


def _ns_capture(start: int, raws: dict[int, int], *,
                last_seen: float | None = None) -> SimpleNamespace:
    """A duck-typed PushCapture, stamped NOW (the probe's freshness gate
    compares last_seen against its own stamps — same monotonic clock,
    delivered strictly after the stamp)."""
    ts = time.monotonic() if last_seen is None else last_seen
    return SimpleNamespace(
        start=start, qty=45, length=99, registers=dict(raws),
        first_seen=ts, last_seen=ts, count=1,
    )


class ScriptedSetpointClient:
    """Duck-typed client driving the setpoint probe and cycle.

    blocks: the next TRIGGER read delivers the next entry as a FRESH
    1046-block capture (a full register window — the probe's whole-block
    diff needs it); a None entry delivers nothing (a silent window).
    seed: extra store entries installed at construction (e.g. a 2001
    block for the snapshot's power decode). write_errors: per-write
    exceptions on the probe seam (a raising write never delivers).
    Records: trigger reads, probe-seam values, control writes, funnel
    writes (must stay EMPTY — the probe never uses the classic funnel),
    connect kwargs, disconnects.
    """

    def __init__(self, blocks=None, *, write_errors=None, is_connected=True,
                 seed=None) -> None:
        self.blocks = list(blocks or [])
        self.write_errors = list(write_errors or [])
        self.is_connected = is_connected
        self.connect_kwargs: dict | None = None
        self.disconnected = False
        self.read_calls = 0
        self.probe_writes: list[int] = []       # write_setpoint_probe_frame values
        self.control_writes: list[int] = []     # write_setpoint values
        self.funnel_writes: list[tuple[int, list[int]]] = []  # write_registers
        self.power_probe_writes: list[bytes] = []  # the power seam — never used here
        self._store: dict[int, SimpleNamespace] = {}
        for start, raws in (seed or {}).items():
            self._store[start] = _ns_capture(start, raws)

    def _deliver(self) -> None:
        if not self.blocks:
            return
        raws = self.blocks.pop(0)
        if raws is None:
            return  # a silent window — nothing fresh arrives
        self._store[1046] = _ns_capture(1046, raws)

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls += 1
        self._deliver()
        raise pkg_aqua_client.AquaTempTimeoutError(
            "no matching response within 8.0s (received 4 frame(s) / 396 bytes)"
        )

    async def write_setpoint_probe_frame(self, value_raw: int) -> None:
        self.probe_writes.append(value_raw)
        if len(self.probe_writes) <= len(self.write_errors):
            error = self.write_errors[len(self.probe_writes) - 1]
            if error is not None:
                raise error  # a refused write never delivers a burst

    async def write_setpoint(self, value_raw: int) -> None:
        self.control_writes.append(value_raw)
        if len(self.control_writes) <= len(self.write_errors):
            error = self.write_errors[len(self.control_writes) - 1]
            if error is not None:
                raise error  # a raising write never delivers a burst

    async def write_probe_frame(self, frame: bytes) -> None:
        self.power_probe_writes.append(bytes(frame))  # never called here

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


def _make_coordinator(client=None, options=None):
    entry = SimpleNamespace(
        data={"address": ADDRESS, "mode": "push"},
        options=dict(options or {}),
    )
    return coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=client or ScriptedSetpointClient()
    )


def _open_options() -> dict:
    """The open-gate option set: the 015 control-confirmation gate +
    the 019 setpoint activation (the THIRD gate — separate by design)."""
    return {
        "push_control_confirmed": True,
        "push_temp_confirmed": True,
        "write_setpoint": True,
    }


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
        msg for _lvl, msg in cap.records if msg.startswith("setpoint write probe: ")
    ]


def _run_probe(blocks, *, write_errors=None, is_connected=True, options=None):
    """Run the setpoint probe over a scripted client; return
    (client, result, cap). The fresh-block window is patched to 0.15 s
    (the _now seam's module-attribute pattern — the coordinator reads
    it at call time)."""
    client = ScriptedSetpointClient(
        blocks, write_errors=write_errors, is_connected=is_connected
    )
    coordinator = _make_coordinator(client, options=options)
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _probe_logs() as cap:
            result = asyncio.run(coordinator.async_setpoint_write_probe())
    return client, result, cap


class _PNRecorder:
    """Records persistent_notification.create/dismiss calls."""

    def __init__(self) -> None:
        self.created: list[tuple[str | None, str]] = []
        self.dismissed: list[str | None] = []


def _attach_pn() -> _PNRecorder:
    """Install a recording persistent_notification stub into the LIVE
    sys.modules (run-time attach — the 017 pattern)."""
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
# Part B — the client seams (FR-6/FR-10, AC-2's zero-byte discipline)
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
    """FR-6: the probe's send path writes the EXACT pinned bytes on the
    write bucket WITHOUT response (app parity — the frame is BUILT, never
    string-pinned: the probe's values are dynamic)."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    asyncio.run(client.write_setpoint_probe_frame(300))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(SETPOINT_300_HEX), False)], (
        fake.writes
    )
    fake.writes.clear()
    asyncio.run(client.write_setpoint_probe_frame(295))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(SETPOINT_295_HEX), False)], (
        fake.writes
    )


def test_probe_seam_rail_refuses_before_any_byte() -> None:
    """FR-6/AC-2: the client rail — out-of-range, off-step and non-int
    values raise ValueError BEFORE a byte goes out (defense in depth
    under the funnel guard)."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    for bad in (99, 401, 297, 292, 1000, 0, None, 300.0, "300"):
        with expect_raises(ValueError):
            asyncio.run(client.write_setpoint_probe_frame(bad))
    assert fake.writes == [], "a refused frame must never reach the wire"


def test_probe_seam_requires_connection() -> None:
    """FR-6: not connected raises AquaTempConnectionError (the
    fire-and-forget path's existing precondition) — zero writes."""
    client = aqua_client.AquaTempBleClient(ADDRESS)  # never connected
    with expect_raises(aqua_client.AquaTempConnectionError, "not connected"):
        asyncio.run(client.write_setpoint_probe_frame(300))


def test_control_seam_wire_parity_and_rail() -> None:
    """FR-10/T9: write_setpoint (the control twin) — the same rail, the
    same pinned bytes on the wire, withoutResponse; a GATT failure is
    wrapped, never silent."""
    fake = RecordingBleakClient()
    client = _make_connected_client(fake)
    asyncio.run(client.write_setpoint(300))
    assert fake.writes == [(UUID_WRITE, bytes.fromhex(SETPOINT_300_HEX), False)], (
        fake.writes
    )
    fake.writes.clear()
    for bad in (99, 401, 297):
        with expect_raises(ValueError):
            asyncio.run(client.write_setpoint(bad))
    assert fake.writes == []

    class _RaisingFake(RecordingBleakClient):
        async def write_gatt_char(self, uuid, data, response=True):
            raise RuntimeError("device disconnected mid-write")

    client2 = _make_connected_client(_RaisingFake())
    with expect_raises(aqua_client.AquaTempConnectionError, "GATT write failed"):
        asyncio.run(client2.write_setpoint(300))


# ===========================================================================
# Part C — the read decode (AC-4) + the two-phase probe (AC-5..AC-8)
# ===========================================================================


def _push_snapshot(caps=None, confirmed=True, control_confirmed=False):
    return coordinator_mod.AquaTempData.from_pushes(
        caps or {}, confirmed, control_confirmed
    )


def test_decode_target_from_own_block() -> None:
    """FR-1/AC-4: the setpoint decodes from the capture of the block
    that CONTAINS the home (the 1046 block) — a store with ONLY that
    block fresh decodes the target; the 2001 block alone decodes
    NOTHING (the block-source fix)."""
    caps = {1046: _ns_capture(1046, LADDER_RAWS)}
    data = _push_snapshot(caps, True, True)
    assert data.target_temp == 29.0  # 290 / 10
    # the 2001 block only: the target stays None
    caps = {2001: _ns_capture(2001, CONTROL_BLOCK_ON)}
    data = _push_snapshot(caps, True, True)
    assert data.target_temp is None
    # both blocks: the target comes from the 1046 window
    caps = {
        1046: _ns_capture(1046, LADDER_RAWS),
        2001: _ns_capture(2001, CONTROL_BLOCK_ON),
    }
    data = _push_snapshot(caps, True, True)
    assert data.target_temp == 29.0
    # the power/mode decode still needs the 2001 capture (unchanged)
    assert data.power is True and data.hvac_mode == HVACMode.HEAT


def test_decode_target_gate_closed_none() -> None:
    """FR-1: the decode rides the EXISTING push_control_confirmed gate —
    unconfirmed, the target stays None exactly as today (no
    unconfirmed register under a certain name)."""
    caps = {1046: _ns_capture(1046, LADDER_RAWS)}
    data = _push_snapshot(caps, True, False)
    assert data.target_temp is None
    # the two-argument call sites keep the 0.10.0 behaviour
    data = coordinator_mod.AquaTempData.from_pushes(caps, True)
    assert data.target_temp is None


def test_decode_target_sentinel_and_implausible() -> None:
    """FR-1: the shared decode rules — a sentinel decodes None (sensor
    absent is never a temperature), an implausible raw decodes None."""
    coordinator_mod._logged_implausible.clear()
    sent = dict(LADDER_RAWS)
    sent[1046] = 32765  # the 0x7FFD sentinel
    data = _push_snapshot({1046: _ns_capture(1046, sent)}, True, True)
    assert data.target_temp is None
    impl = dict(LADDER_RAWS)
    impl[1046] = 5000  # outside the plausibility window
    data = _push_snapshot({1046: _ns_capture(1046, impl)}, True, True)
    assert data.target_temp is None


def test_probe_executed_reversible() -> None:
    """AC-5/AC-8: the full S → no-op → S → T → revert → S script yields
    executed+reversible; exactly THREE writes go out — S, T, S — and
    the ONE verdict line (WARNING) carries the <S>→<T>→<S> raws; the
    probe disconnects on completion; the classic funnel is never
    called; the probe runs with BOTH gate surfaces closed (it is the
    proof instrument — the gate's cause, not its consumer)."""
    ladder = dict(LADDER_RAWS)
    blocks = [ladder, dict(ladder), {**ladder, 1046: T_RAW}, dict(ladder)]
    client, result, cap = _run_probe(blocks)
    assert result["verdict"] == "executed+reversible", result
    assert client.probe_writes == [S_RAW, T_RAW, S_RAW], client.probe_writes
    assert client.funnel_writes == [], "the probe never uses the classic funnel"
    assert client.power_probe_writes == [], "the probe never uses the power seam"
    # the recorded frames are the builder's own output for {S, T}
    assert result["frames_sent"] == [
        modbus.build_write_control(1046, S_RAW).hex(),
        modbus.build_write_control(1046, T_RAW).hex(),
        modbus.build_write_control(1046, S_RAW).hex(),
    ], result["frames_sent"]
    assert result["mirror"]["baseline"]["raw"] == S_RAW
    assert result["mirror"]["after_no_op"]["raw"] == S_RAW
    assert result["mirror"]["after_test"]["raw"] == T_RAW
    assert result["mirror"]["after_revert"]["raw"] == S_RAW
    assert client.disconnected, "the probe must disconnect on completion"
    # baseline + one trigger after each write = 4 reads, no retries
    assert client.read_calls == 4, client.read_calls
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        "setpoint write probe: EXECUTED + REVERSIBLE "
        f"(mirror 1046 followed {S_RAW}→{T_RAW}→{S_RAW})"
        " — evidence for WRITE_SETPOINT_PROVEN"
    ), lines[0]
    assert UUID_WRITE in lines[0], lines[0]
    # every stage observation logs at WARNING (the operator's panel
    # keeps WARNING+ — contract 012 §2); the trigger's timed-out
    # plumbing line is the one allowed exception (the expected world).
    probe_lines = [(lvl, m) for lvl, m in cap.records if m.startswith("setpoint write probe")]
    assert all(
        lvl == logging.WARNING for lvl, m in probe_lines if "timed out" not in m
    ), probe_lines


def test_probe_connects_when_disconnected() -> None:
    """FR-5 step 1: the probe connects (threading the pair_on_connect
    option like the poll path) when the client is not connected."""
    ladder = dict(LADDER_RAWS)
    blocks = [ladder, dict(ladder), {**ladder, 1046: T_RAW}, dict(ladder)]
    client, result, _cap = _run_probe(blocks, is_connected=False)
    assert client.connect_kwargs == {"pair": False}, client.connect_kwargs
    assert result["verdict"] == "executed+reversible", result


def test_probe_no_op_neighbor_changed() -> None:
    """AC-6: a scripted NEIGHBOR change during the no-op → the verdict
    no-op-neighbor-changed, the line LISTS the changed register(s), and
    NO phase-B frame was sent (exactly ONE write — the no-op)."""
    ladder = dict(LADDER_RAWS)
    moved = {**ladder, 1049: 155}
    client, result, cap = _run_probe([ladder, moved])
    assert result["verdict"] == "no-op-neighbor-changed", result
    assert client.probe_writes == [S_RAW], client.probe_writes
    assert client.read_calls == 2, "baseline + the post-no-op trigger only"
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "no-op-neighbor-changed" in lines[0], lines[0]
    assert "1049" in lines[0], lines[0]  # the moved register is named
    assert "150->155" in lines[0], lines[0]
    assert "ABORTED" in lines[0], lines[0]
    assert client.disconnected


def test_probe_no_op_register_itself_changed() -> None:
    """AC-6's hard case: the register ITSELF moved during the 'no-op'
    — the whole-block diff catches it the same way (the neighbor alarm
    exists precisely to catch a mis-aimed write before phase B)."""
    ladder = dict(LADDER_RAWS)
    moved = {**ladder, 1046: 295}
    client, result, cap = _run_probe([ladder, moved])
    assert result["verdict"] == "no-op-neighbor-changed", result
    assert client.probe_writes == [S_RAW], "phase B never ran"
    lines = _verdict_lines(cap)
    assert "1046" in lines[0] and "290->295" in lines[0], lines[0]
    assert client.disconnected


def test_probe_not_executed() -> None:
    """AC-7: the mirror staying S after the T write → not-executed; the
    line carries the remediation (restore the setpoint if the panel
    changed unexpectedly)."""
    ladder = dict(LADDER_RAWS)
    blocks = [ladder, dict(ladder), {**ladder, 1046: S_RAW}]  # stays 290
    client, result, cap = _run_probe(blocks)
    assert result["verdict"] == "not-executed", result
    assert client.probe_writes == [S_RAW, T_RAW], client.probe_writes
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "not-executed" in lines[0], lines[0]
    assert f"stayed {S_RAW}" in lines[0], lines[0]
    assert "restore it from the panel or the vendor app" in lines[0], lines[0]
    assert client.disconnected


def test_probe_not_reversible() -> None:
    """AC-7: the mirror staying T after the revert → not-reversible;
    the line states the CURRENT setpoint (28.5 °C) and the remediation
    (restore it from the panel or the vendor app) — never silent."""
    ladder = dict(LADDER_RAWS)
    blocks = [ladder, dict(ladder), {**ladder, 1046: T_RAW}, {**ladder, 1046: T_RAW}]
    client, result, cap = _run_probe(blocks)
    assert result["verdict"] == "not-reversible", result
    assert client.probe_writes == [S_RAW, T_RAW, S_RAW], client.probe_writes
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "not-reversible" in lines[0], lines[0]
    assert "the setpoint is now 28.5 °C" in lines[0], lines[0]
    assert "restore it from the panel or the vendor app" in lines[0], lines[0]
    assert client.disconnected


def test_probe_refused_baseline_bad_s() -> None:
    """AC-7: the baseline gates — a sentinel, out-of-range, off-step or
    absent S aborts with refused-baseline and sends ZERO writes (the
    probe refuses to compute a safe test value from a weird register)."""
    ladder = dict(LADDER_RAWS)
    for name, bad in (
        ("sentinel", {**ladder, 1046: 32765}),
        ("below range", {**ladder, 1046: 95}),
        ("above range", {**ladder, 1046: 405}),
        ("off step", {**ladder, 1046: 292}),
        ("absent", {k: v for k, v in ladder.items() if k != 1046}),
    ):
        client, result, cap = _run_probe([bad])
        assert result["verdict"] == "refused-baseline", (name, result)
        assert client.probe_writes == [], (name, client.probe_writes)
        lines = _verdict_lines(cap)
        assert len(lines) == 1, (name, lines)
        assert "refused-baseline" in lines[0], (name, lines[0])
        assert "nothing was written" in lines[0], (name, lines[0])
        assert client.disconnected, name


def test_probe_no_report_baseline() -> None:
    """AC-7: a silent baseline window (no fresh 1046 block) → no-report
    — nothing was written; the remediation names the panel check."""
    client, result, cap = _run_probe([])
    assert result["verdict"] == "no-report", result
    assert client.probe_writes == []
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert lines[0].startswith("setpoint write probe: no-report (no fresh 1046 block)"), (
        lines[0]
    )
    assert "check the panel" in lines[0], lines[0]
    assert client.read_calls == 1, "no retry trigger at the baseline stage"
    assert client.disconnected


def test_probe_no_report_after_noop() -> None:
    """AC-7: a silent post-no-op window → no-report with exactly ONE
    write recorded (the no-op — harmless by construction)."""
    ladder = dict(LADDER_RAWS)
    client, result, cap = _run_probe([ladder, None])
    assert result["verdict"] == "no-report", result
    assert client.probe_writes == [S_RAW], client.probe_writes
    lines = _verdict_lines(cap)
    assert "no fresh 1046 block after the no-op" in lines[0], lines[0]
    assert client.disconnected


def test_probe_no_report_after_revert_names_remediation() -> None:
    """AC-7/contract 019 §3: a silent post-REVERT window leaves the
    setpoint possibly at the TEST value — the verdict line must say so
    (never a silent end state)."""
    ladder = dict(LADDER_RAWS)
    blocks = [ladder, dict(ladder), {**ladder, 1046: T_RAW}, None]
    client, result, cap = _run_probe(blocks)
    assert result["verdict"] == "no-report", result
    assert client.probe_writes == [S_RAW, T_RAW, S_RAW], client.probe_writes
    lines = _verdict_lines(cap)
    assert "no fresh 1046 block after the revert" in lines[0], lines[0]
    assert "check the panel" in lines[0], lines[0]
    assert "restore" in lines[0], lines[0]
    assert client.disconnected


def test_probe_att_error() -> None:
    """AC-7: a raising write → att-error with the failure chain VERBATIM
    (the ATT error text is the diagnosis) and the write char named."""
    chain_exc = RuntimeError(
        "BleakGATTProtocolError: ATT error: Insufficient Authentication"
    )
    ladder = dict(LADDER_RAWS)
    client, result, cap = _run_probe([ladder, dict(ladder)],
                                     write_errors=[chain_exc])
    assert result["verdict"] == "att-error", result
    lines = _verdict_lines(cap)
    assert len(lines) == 1, lines
    assert "att-error: " in lines[0], lines[0]
    assert "Insufficient Authentication" in lines[0], lines[0]
    assert UUID_WRITE in lines[0], lines[0]
    assert client.disconnected


def test_probe_frame_discipline() -> None:
    """AC-8: every value that reaches the seam is from {S, T} and
    in-range/on-step (the rail holds on every path); the probe never
    calls ensure_writable (the scripted funnel stays empty) — its own
    validated seam IS the guard (contract 016 §5's pattern)."""
    ladder = dict(LADDER_RAWS)
    # the happy path
    blocks = [ladder, dict(ladder), {**ladder, 1046: T_RAW}, dict(ladder)]
    client, result, _cap = _run_probe(blocks)
    assert result["verdict"] == "executed+reversible"
    assert all(v in (S_RAW, T_RAW) for v in client.probe_writes)
    assert all(
        registers.SETPOINT_MIN_RAW <= v <= registers.SETPOINT_MAX_RAW
        and v % registers.SETPOINT_STEP_RAW == 0
        for v in client.probe_writes
    )
    # the failure paths keep the discipline too
    client, result, _cap = _run_probe(
        [ladder, dict(ladder), {**ladder, 1046: T_RAW}, {**ladder, 1046: T_RAW}]
    )
    assert result["verdict"] == "not-reversible"
    assert all(v in (S_RAW, T_RAW) for v in client.probe_writes)
    # the refused baseline sends NOTHING (the rail test's AC-8 clause)
    client, result, _cap = _run_probe([{**ladder, 1046: 32765}])
    assert result["verdict"] == "refused-baseline"
    assert client.probe_writes == []


# ===========================================================================
# Part D — the confirmation cycle (AC-9, FR-10)
# ===========================================================================


def _run_cycle(blocks, *, celsius, options=None, is_connected=True,
               write_error=None, pre_stale=False, seed=None):
    """Run ONE setpoint confirmation cycle; return (client, coordinator,
    cap, result). The fresh-block window is patched to 0.15 s."""
    client = ScriptedSetpointClient(
        blocks, is_connected=is_connected, seed=seed,
        write_errors=[write_error] if write_error is not None else None,
    )
    if pre_stale:
        ts = time.monotonic() - 1000.0
        client._store[1046] = _ns_capture(1046, LADDER_RAWS, last_seen=ts)
    coordinator = _make_coordinator(client, options=options)
    coordinator.data = None
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _probe_logs() as cap:
            result = asyncio.run(coordinator.async_control_write_setpoint(celsius))
    return client, coordinator, cap, result


def test_cycle_gate_closed_zero_io() -> None:
    """AC-9: with both activation surfaces off the cycle refuses with
    the guard's locked message BEFORE any connect, lock or I/O."""
    client = ScriptedSetpointClient()
    coordinator = _make_coordinator(client, options={})
    with expect_raises(ValueError, "WRITE_SETPOINT_PROVEN"):
        asyncio.run(coordinator.async_control_write_setpoint(30.0))
    assert client.read_calls == 0
    assert client.control_writes == []
    assert client.probe_writes == []
    assert client.connect_kwargs is None, "the guard fires BEFORE the connect"


def test_cycle_validation_never_clamps() -> None:
    """FR-10 step 1: a regulation value is never reinterpreted — 30.3 °C
    (off the 0.5 step), 41.0/45.0 °C (above), 9.5 °C (below) and a
    non-finite value all raise ValueError with ZERO client I/O; a valid
    30.0 does not."""
    for celsius, needle in (
        (30.3, "0.5"),
        (41.0, "10.0-40.0"),
        (45.0, "10.0-40.0"),
        (9.5, "10.0-40.0"),
        (float("nan"), "finite"),
        (float("inf"), "finite"),
    ):
        client = ScriptedSetpointClient()
        coordinator = _make_coordinator(client, options=_open_options())
        with expect_raises(ValueError, needle):
            asyncio.run(coordinator.async_control_write_setpoint(celsius))
        assert client.read_calls == 0, celsius
        assert client.control_writes == [], celsius


def test_cycle_confirmed() -> None:
    """AC-9: the happy path — the funnel passes, the audit INFO and the
    verdict INFO lines are EXACT, ONE trigger follows the write, the
    snapshot push reflects the MIRROR, and the cycle does NOT
    disconnect (the steady-state poll world)."""
    ladder = dict(LADDER_RAWS)
    client, coordinator, cap, result = _run_cycle(
        [{**ladder, 1046: 300}], celsius=30.0, options=_open_options()
    )
    assert result["verdict"] == "confirmed", result
    assert result["value_raw"] == 300
    assert result["mirror_raw"] == 300
    assert result["mirror_age_s"] is not None and result["mirror_age_s"] >= 0.0
    infos = [m for lvl, m in cap.records if lvl == logging.INFO]
    assert "setpoint write via 1046 = 300 — mirror should follow in the next report" in (
        infos
    ), infos
    assert "setpoint write confirmed by mirror (1046 = 300)" in infos, infos
    assert client.control_writes == [300], client.control_writes
    assert client.read_calls == 1, "exactly ONE trigger after the write"
    assert client.funnel_writes == []  # never the classic funnel
    assert not client.disconnected, "the confirmation must NOT disconnect"
    # the snapshot push: the entity plane reflects the MIRROR
    assert len(coordinator.pushed_snapshots) == 1
    assert coordinator.data.target_temp == 30.0
    # the stage dict lands in the diagnostics attribute
    assert coordinator.last_setpoint_write == result


def test_cycle_connects_when_disconnected() -> None:
    """FR-10 step 3: the cycle connects when needed, threading the
    pair_on_connect option exactly like the poll path."""
    ladder = dict(LADDER_RAWS)
    client, _coord, _cap, result = _run_cycle(
        [{**ladder, 1046: 300}], celsius=30.0, options=_open_options(),
        is_connected=False,
    )
    assert result["verdict"] == "confirmed", result
    assert client.connect_kwargs == {"pair": False}, client.connect_kwargs


def test_cycle_not_confirmed_shows_mirror() -> None:
    """AC-9: the fresh mirror disagrees → the WARNING names BOTH values;
    the tile shows the MIRROR's truth, never the request."""
    ladder = dict(LADDER_RAWS)
    client, coordinator, cap, result = _run_cycle(
        [dict(ladder)], celsius=30.0, options=_open_options()
    )
    assert result["verdict"] == "not-confirmed", result
    assert result["mirror_raw"] == 290 and result["value_raw"] == 300
    warns = [
        m for lvl, m in cap.records
        if lvl == logging.WARNING and m.startswith("setpoint write not confirmed")
    ]
    assert len(warns) == 1, warns
    assert "mirror 1046 = 290" in warns[0] and "requested 300" in warns[0], warns[0]
    # the snapshot push carries the MIRROR's state: 30.0 was requested,
    # the unit reported 29.0 — the tile must say 29.0
    assert len(coordinator.pushed_snapshots) == 1
    assert coordinator.data.target_temp == 29.0


def test_cycle_unconfirmed_no_snapshot_no_notification() -> None:
    """AC-9/contract 019 §5.3: no fresh block → the WARNING unconfirmed
    line, NO snapshot push (the state stays), and NO persistent
    notification (the deliberate divergence: a failed setpoint write
    leaves the machine at its old target — no hazard)."""
    pn = _attach_pn()
    client, coordinator, cap, result = _run_cycle(
        [None], celsius=30.0, options=_open_options()
    )
    assert result["verdict"] == "unconfirmed", result
    assert result["mirror_raw"] is None
    warns = [
        m for lvl, m in cap.records
        if lvl == logging.WARNING and m.startswith("setpoint write unconfirmed")
    ]
    assert len(warns) == 1, warns
    assert "no fresh report within" in warns[0], warns[0]
    assert "UNKNOWN" in warns[0], warns[0]
    assert coordinator.pushed_snapshots == []
    assert coordinator.data is None
    assert pn.created == [], "NO persistent notification on setpoint failures"
    assert pn.dismissed == []


def test_cycle_stale_capture_never_confirms() -> None:
    """AC-9's freshness gate: a capture with last_seen <= t_write NEVER
    satisfies the wait — a stale mirror cannot confirm a write."""
    client, coordinator, _cap, result = _run_cycle(
        [], celsius=30.0, options=_open_options(), pre_stale=True
    )
    assert result["verdict"] == "unconfirmed", result
    assert result["mirror_raw"] is None  # the stale capture was refused
    assert coordinator.pushed_snapshots == []


def test_cycle_write_error_propagates() -> None:
    """FR-10: a raising write propagates to the caller — NO trigger, NO
    cross-check, NO snapshot push, NO notification."""
    pn = _attach_pn()
    client = ScriptedSetpointClient([], write_errors=[RuntimeError("link down")])
    coordinator = _make_coordinator(client, options=_open_options())
    coordinator.data = None
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        with _probe_logs():
            with expect_raises(RuntimeError, "link down"):
                asyncio.run(coordinator.async_control_write_setpoint(30.0))
    assert client.control_writes == [300]
    assert client.read_calls == 0, "a raising write must not trigger a read"
    assert coordinator.pushed_snapshots == []
    assert pn.created == []
    assert not client.disconnected


def test_cycle_works_with_power_off() -> None:
    """AC-9/FR-11: the write is allowed REGARDLESS of power state — the
    register is the machine's stored parameter [P owner: the panel
    changed the setpoint with the unit off]."""
    ladder = dict(LADDER_RAWS)
    client, coordinator, _cap, result = _run_cycle(
        [{**ladder, 1046: 300}], celsius=30.0, options=_open_options(),
        seed={2001: CONTROL_BLOCK_OFF},
    )
    assert result["verdict"] == "confirmed", result
    assert coordinator.data.power is False
    assert coordinator.data.hvac_mode == HVACMode.OFF
    assert coordinator.data.target_temp == 30.0


def test_cycle_end_to_end_real_client_frame() -> None:
    """AC-9's wire-level demand: over the REAL client, the cycle puts
    EXACTLY the pinned frame bytes on the fake write char
    (withoutResponse) — and the pushed snapshot makes the tile follow
    the mirror."""
    fake = RecordingBleakClient()
    client = pkg_aqua_client.AquaTempBleClient(ADDRESS)
    client._client = fake
    client._write_with_response = False  # the production ff03 mode
    entry = SimpleNamespace(
        entry_id="e2e019",
        title="Aqua Temp test",
        data={"address": ADDRESS, "mode": "push"},
        options=dict(_open_options()),
    )
    coordinator = coordinator_mod.AquaTempCoordinator(hass=None, entry=entry, client=client)
    coordinator.data = None
    # a FUTURE-stamped capture: fresher than any t_write the cycle
    # stamps, so the confirmation completes instantly (the trigger read
    # itself is bounded by the patched REQUEST_TIMEOUT — the expected
    # timeout world).
    client._pushed[1046] = _ns_capture(
        1046, {**LADDER_RAWS, 1046: 300}, last_seen=time.monotonic() + 10.0
    )
    with _patch_attr(pkg_const, "REQUEST_TIMEOUT", 0.05), _patch_attr(
        pkg_const, "PROBE_MIRROR_TIMEOUT", 1.0
    ):
        result = asyncio.run(coordinator.async_control_write_setpoint(30.0))
    assert result["verdict"] == "confirmed", result
    control_frames = [w for w in fake.writes if len(w[1]) == 11]
    assert control_frames == [(UUID_WRITE, bytes.fromhex(SETPOINT_300_HEX), False)], (
        fake.writes
    )
    assert coordinator.data.target_temp == 30.0


# ===========================================================================
# Part E — the surfaces: the climate gate matrix, the button, the option
# ===========================================================================


def _make_platform(options, snapshot, client=None):
    entry = SimpleNamespace(
        entry_id="t19",
        title="Aqua Temp test",
        data={"address": ADDRESS, "mode": "push"},
        options=dict(options),
    )
    coordinator = coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=client or ScriptedSetpointClient()
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


def _target_snapshot(target=29.0):
    """A push snapshot whose 1046 block carries the given target."""
    raw = int(round(target * 10))
    caps = {
        1046: _ns_capture(1046, {**LADDER_RAWS, 1046: raw}),
        2001: _ns_capture(2001, CONTROL_BLOCK_ON),
        2046: _ns_capture(2046, T_WINDOW_RAWS),
    }
    return coordinator_mod.AquaTempData.from_pushes(caps, True, True)


def test_climate_setpoint_gate_closed_no_target_feature() -> None:
    """AC-2: with both setpoint gate surfaces closed the push climate
    advertises NO TARGET_TEMPERATURE — even with the POWER gate open
    (the 017 surface stands unchanged) — and set_temperature refuses
    with the 015 message, byte-identical, with ZERO client I/O."""
    snapshot = _target_snapshot()
    client = ScriptedSetpointClient()
    hass, entry, _coord = _make_platform(
        {"push_control_confirmed": True, "write_control": True}, snapshot, client
    )
    entity = _added_entity(hass, entry)
    assert entity.supported_features == (
        ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    with expect_raises(HomeAssistantError, "read-only"):
        asyncio.run(entity.async_set_temperature(temperature=30.0))
    assert client.control_writes == []
    assert client.probe_writes == []
    assert client.read_calls == 0
    # the read display is live (the home is [P owner] — no write needed)
    assert entity.target_temperature == 29.0


def test_climate_setpoint_gate_open_composes() -> None:
    """AC-9/FR-9: the gates COMPOSE — with the setpoint gate open and
    the POWER gate closed the climate advertises TARGET_TEMPERATURE
    alone (no mode buttons), the bounds come from the guard's own
    constants, and the write path runs the confirmation cycle."""
    snapshot = _target_snapshot()
    ladder = dict(LADDER_RAWS)
    client = ScriptedSetpointClient([{**ladder, 1046: 300}])
    hass, entry, coordinator = _make_platform(
        {"push_control_confirmed": True, "write_setpoint": True}, snapshot, client
    )
    entity = _added_entity(hass, entry)
    assert entity.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.hvac_modes == []  # the power gate is still closed
    assert entity.min_temp == 10.0
    assert entity.max_temp == 40.0
    assert entity._attr_target_temperature_step == 0.5
    assert entity.target_temperature == 29.0  # the read display
    with _patch_attr(coordinator_mod.const, "PROBE_MIRROR_TIMEOUT", 0.15):
        asyncio.run(entity.async_set_temperature(temperature=30.0))
    assert client.control_writes == [300], client.control_writes
    assert client.funnel_writes == []
    # the tile follows the MIRROR (30.0 came from the pushed block)
    assert coordinator.data.target_temp == 30.0
    # the diagnostics attribute carries the stage dict
    attrs = entity.extra_state_attributes
    assert attrs is not None
    assert attrs["write_setpoint"] == "allowed"
    assert attrs["last_setpoint_write"]["verdict"] == "confirmed", attrs


def test_climate_step_range_and_gate_refusals() -> None:
    """AC-2/AC-9 (the mission's mandatory cases): gate open — 30.3 °C
    (not a 0.5 multiple) raises; 45.0 °C (out of range) raises; gate
    closed — set_temperature refuses with the 015 message."""
    snapshot = _target_snapshot()
    # gate open: the validation errors surface through the entity
    client = ScriptedSetpointClient()
    hass, entry, _coord = _make_platform(
        {"push_control_confirmed": True, "write_setpoint": True}, snapshot, client
    )
    entity = _added_entity(hass, entry)
    with expect_raises(ValueError, "0.5"):
        asyncio.run(entity.async_set_temperature(temperature=30.3))
    with expect_raises(ValueError, "10.0-40.0"):
        asyncio.run(entity.async_set_temperature(temperature=45.0))
    with expect_raises(ValueError, "10.0-40.0"):
        asyncio.run(entity.async_set_temperature(temperature=41.0))
    with expect_raises(ValueError, "10.0-40.0"):
        asyncio.run(entity.async_set_temperature(temperature=9.5))
    assert client.control_writes == []  # nothing reached the wire
    # gate closed: the 015 refusal, byte-identical
    hass2, entry2, _coord2 = _make_platform(
        {"push_control_confirmed": True}, snapshot, ScriptedSetpointClient()
    )
    entity2 = _added_entity(hass2, entry2)
    with expect_raises(HomeAssistantError, "read-only"):
        asyncio.run(entity2.async_set_temperature(temperature=30.0))


def test_climate_read_display_independent_of_write_gate() -> None:
    """FR-8: target_temperature is displayed behind the EXISTING
    push_control_confirmed gate, independent of the write gate — the
    honest display of an owner-proven register (the 015 read-only
    pattern); unconfirmed decodes None."""
    snapshot = _target_snapshot()
    hass, entry, _coord = _make_platform({"push_control_confirmed": True},
                                         snapshot, ScriptedSetpointClient())
    entity = _added_entity(hass, entry)
    assert entity.target_temperature == 29.0
    # no 1046 block in the store -> the target is unknown (never guessed)
    caps = {
        2001: _ns_capture(2001, CONTROL_BLOCK_ON),
        2046: _ns_capture(2046, T_WINDOW_RAWS),
    }
    empty = coordinator_mod.AquaTempData.from_pushes(caps, True, True)
    hass2, entry2, _coord2 = _make_platform(
        {"push_control_confirmed": True}, empty, ScriptedSetpointClient()
    )
    entity2 = _added_entity(hass2, entry2)
    assert entity2.target_temperature is None


def test_climate_locked_attrs_absent() -> None:
    """AC-2: with both write gates closed the diagnostics attributes
    stay absent (the 015/017 locked surface, byte-identical)."""
    snapshot = _target_snapshot()
    hass, entry, _coord = _make_platform(
        {"push_control_confirmed": True}, snapshot, ScriptedSetpointClient()
    )
    entity = _added_entity(hass, entry)
    assert entity.extra_state_attributes is None


def test_button_platform_adds_setpoint_probe() -> None:
    """FR-7: the diagnostic button — the platform adds THREE buttons,
    the new one named 'Setpoint write probe', DIAGNOSTIC category,
    unique_id suffix setpoint_write_probe; pressing it RUNS the probe
    (the writes go out, the verdict lands, the link recycles)."""
    ladder = dict(LADDER_RAWS)
    client = ScriptedSetpointClient(
        [ladder, dict(ladder), {**ladder, 1046: T_RAW}, dict(ladder)]
    )
    hass, entry, _coord = _make_platform({}, None, client)
    added: list = []

    def _collect(entities) -> None:
        added.extend(entities)

    asyncio.run(button_mod.async_setup_entry(hass, entry, _collect))
    assert len(added) == 3, [type(e).__name__ for e in added]
    probe_btn = next(e for e in added if "Setpoint" in str(e._attr_name))
    assert probe_btn._attr_name == "Setpoint write probe"
    assert probe_btn._attr_entity_category == EntityCategory.DIAGNOSTIC
    assert "setpoint_write_probe" in probe_btn._attr_unique_id
    # the siblings are unchanged (non-regression)
    assert added[0]._attr_name == "Probe registers"
    assert added[1]._attr_name == "Power write probe"
    # pressing runs the probe end-to-end on the scripted client
    with _probe_logs():
        asyncio.run(probe_btn.async_press())
    assert client.probe_writes == [S_RAW, T_RAW, S_RAW], client.probe_writes
    assert client.disconnected


def test_write_setpoint_gate_property_paths() -> None:
    """FR-9/AC-10: the activation property — the per-entry option OR the
    global registers switch, read LIVE (no restart); both off = locked;
    the power gate is untouched (the gates are separate)."""
    pkg_regs = sys.modules[f"{PKG}.registers"]
    closed = _make_coordinator(options={})
    option = _make_coordinator(options={"write_setpoint": True})
    assert closed.write_setpoint_allowed is False
    assert option.write_setpoint_allowed is True
    with _patch_attr(pkg_regs, "WRITE_SETPOINT_PROVEN", True):
        assert closed.write_setpoint_allowed is True  # the switch ORs
        assert option.write_setpoint_allowed is True  # both on
    assert pkg_regs.WRITE_SETPOINT_PROVEN is False  # restored
    assert registers.WRITE_SETPOINT_PROVEN is False  # the standalone module untouched
    assert coordinator_mod.registers is not registers  # separate instances
    # the power gate reads its own surfaces only
    assert closed.write_control_allowed is False


def test_options_flow_schema_has_write_setpoint() -> None:
    """FR-9/T8: the OptionsFlow carries the write_setpoint checkbox —
    default False (the shipped inert default), push-mode only, its
    checked value kept as the default, and the save merge carries it."""
    assert config_flow_mod is not None, (
        f"config_flow import failed: {_CONFIG_FLOW_IMPORT_ERROR}"
    )
    assert config_flow_mod.DEFAULT_WRITE_SETPOINT is False
    flow = config_flow_mod.AquaTempOptionsFlowHandler()
    flow.config_entry = SimpleNamespace(options={}, data={"mode": "push"})
    result = asyncio.run(flow.async_step_init(None))
    assert result["type"] == "form" and result["step_id"] == "init", result
    marker = next(
        e for e in result["data_schema"] if getattr(e, "key", None) == "write_setpoint"
    )
    assert marker.kwargs.get("default") is False
    # an already-enabled entry keeps True as the default
    flow.config_entry = SimpleNamespace(
        options={"write_setpoint": True}, data={"mode": "push"}
    )
    result = asyncio.run(flow.async_step_init(None))
    marker = next(
        e for e in result["data_schema"] if getattr(e, "key", None) == "write_setpoint"
    )
    assert marker.kwargs.get("default") is True
    # the save merge carries the option into the entry's options
    flow.config_entry = SimpleNamespace(options={}, data={"mode": "push"})
    result = asyncio.run(flow.async_step_init({"write_setpoint": True}))
    assert result["type"] == "create_entry", result
    assert result["data"]["write_setpoint"] is True
    # classic entries do not get the checkbox (the push-only precedent)
    flow.config_entry = SimpleNamespace(options={}, data={})
    result = asyncio.run(flow.async_step_init(None))
    assert not any(
        getattr(e, "key", None) == "write_setpoint" for e in result["data_schema"]
    )


def test_service_and_option_declared_in_strings() -> None:
    """FR-7/FR-9: the setpoint_write_probe service is declared in
    services.yaml, strings.json and BOTH translations; the write_setpoint
    option is labeled + described with the required probe verdict (the
    UI carries contract 019 §4's discipline)."""
    services_yaml = (COMPONENT_DIR / "services.yaml").read_text()
    assert "setpoint_write_probe:" in services_yaml, "services.yaml misses the service"
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text())
    assert "setpoint_write_probe" in strings["services"], strings["services"].keys()
    assert "fields" in strings["services"]["setpoint_write_probe"]
    # the service description names the physical effect (FR-7)
    assert "0.5" in strings["services"]["setpoint_write_probe"]["description"]
    # the option + its description name the required verdict line
    init = strings["options"]["step"]["init"]
    assert "write_setpoint" in init["data"], init["data"].keys()
    assert "write_setpoint" in init.get("data_description", {}), init.keys()
    assert "EXECUTED + REVERSIBLE" in init["data_description"]["write_setpoint"]
    for lang in ("en", "fr"):
        translations = json.loads(
            (COMPONENT_DIR / "translations" / f"{lang}.json").read_text()
        )
        assert "setpoint_write_probe" in translations.get("services", {}), (
            lang,
            translations.get("services", {}).keys(),
        )
        init_lang = translations["options"]["step"]["init"]
        assert "write_setpoint" in init_lang["data"], (lang, init_lang["data"].keys())
        assert "write_setpoint" in init_lang.get("data_description", {}), (
            lang,
            init_lang.keys(),
        )
        assert "EXECUTED + REVERSIBLE" in (
            init_lang["data_description"]["write_setpoint"]
        ), lang


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Part A — the data + the builder + the guard
        test_read_home_pinned,
        test_write_plane_constants_transcribed,
        test_setpoint_parity_vectors,
        test_allowed_write_setpoint_data,
        test_guard_shipped_default_locked,
        test_guard_value_discipline_when_open,
        test_guard_setpoint_allowed_thread,
        test_guard_power_and_base_rules_unchanged,
        test_integrity_passes_with_setpoint_home,
        test_labels_corrected,
        test_no_code_path_sets_the_setpoint_gate,
        # Part B — the client seams
        test_probe_seam_wire_parity,
        test_probe_seam_rail_refuses_before_any_byte,
        test_probe_seam_requires_connection,
        test_control_seam_wire_parity_and_rail,
        # Part C — the decode + the probe
        test_decode_target_from_own_block,
        test_decode_target_gate_closed_none,
        test_decode_target_sentinel_and_implausible,
        test_probe_executed_reversible,
        test_probe_connects_when_disconnected,
        test_probe_no_op_neighbor_changed,
        test_probe_no_op_register_itself_changed,
        test_probe_not_executed,
        test_probe_not_reversible,
        test_probe_refused_baseline_bad_s,
        test_probe_no_report_baseline,
        test_probe_no_report_after_noop,
        test_probe_no_report_after_revert_names_remediation,
        test_probe_att_error,
        test_probe_frame_discipline,
        # Part D — the confirmation cycle
        test_cycle_gate_closed_zero_io,
        test_cycle_validation_never_clamps,
        test_cycle_confirmed,
        test_cycle_connects_when_disconnected,
        test_cycle_not_confirmed_shows_mirror,
        test_cycle_unconfirmed_no_snapshot_no_notification,
        test_cycle_stale_capture_never_confirms,
        test_cycle_write_error_propagates,
        test_cycle_works_with_power_off,
        test_cycle_end_to_end_real_client_frame,
        # Part E — the surfaces
        test_climate_setpoint_gate_closed_no_target_feature,
        test_climate_setpoint_gate_open_composes,
        test_climate_step_range_and_gate_refusals,
        test_climate_read_display_independent_of_write_gate,
        test_climate_locked_attrs_absent,
        test_button_platform_adds_setpoint_probe,
        test_write_setpoint_gate_property_paths,
        test_options_flow_schema_has_write_setpoint,
        test_service_and_option_declared_in_strings,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-019 setpoint-control tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())