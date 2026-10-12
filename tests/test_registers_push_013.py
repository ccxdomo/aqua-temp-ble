#!/usr/bin/env python3
"""Pushed-plane registers tests (spec 013 FR-5/FR-7, AC-5/AC-6).

Part A (registers-level, standalone import): the pushed-plane
constants transcribed VERBATIM from data-model 013 §6 (PUSH_TEMP_BLOCK_
START, PUSHED_TEMPS, the PUSHED_MIRRORS pairs and
the PUSHED_SENSORS push-only registry) and the check_registry_integrity
extension (FR-7, contract 013 §7.1): a pushed address must lie in
2001-2090, duplicates within the pushed plane raise, a pushed key must
be a shipped temperature entity (H/R/P are never entities — spec 003
§6), and the shipped registries pass. (Spec 022: the confirmation
switches are DELETED — active by default.)

Part B (sensor-platform surface, synthetic-package import — the
test_coordinator_probe pattern extended): the entity creation rules of
FR-5, amended by spec 022 — ALL six T-family keys are created
UNCONDITIONALLY in push mode (active by default; the confirmation
gates are gone), 2047 -> 14.5 from the pinned
vector; the T01 sentinel at 2046 decodes `unknown` (the vendor's
3276.5 degC display bug must never replicate); a permuted mapping edit
(swapping 2047/2048 in PUSHED_TEMPS) swaps the sources — the
single-source-of-truth test.

Red-first on 1ac276d: none of the pushed-plane constants, registries,
creation rules or the push branch of native_value exist.

Run standalone:

    python3 tests/test_registers_push_013.py

No real Bluetooth and no real Home Assistant required (fake modules
are installed when the real ones are absent).
"""

from __future__ import annotations

import asyncio
import importlib.util
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
    bleak = types.ModuleType("bleak")

    class _FakeBleakClient:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScanner:  # pragma: no cover - placeholder
        pass

    bleak.BleakClient = _FakeBleakClient
    bleak.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = bleak

import registers  # noqa: E402  (part A: standalone, the test_registers pattern)

DOMAIN = "aqua_temp_ble"
ADDRESS = "08:06:A6:C0:94:1C"

# The pinned T-window raws (research 013 §2.4, the second 2046 push:
# 2047 changed live 140 -> 145 [P]).
T_WINDOW_RAWS = {2046: 32765, 2047: 145, 2048: 140, 2049: 140, 2050: 135, 2051: 150}


def _capture(start: int, raws: dict[int, int], count: int = 1) -> SimpleNamespace:
    """A duck-typed PushCapture for the snapshot tests."""
    return SimpleNamespace(
        start=start,
        qty=45,
        length=99,
        registers=dict(raws),
        first_seen=100.0,
        last_seen=102.0,
        count=count,
    )


# ===========================================================================
# Part A — registers constants + integrity (spec 013 FR-5/FR-7)
# ===========================================================================


def test_pushed_plane_constants_transcribed() -> None:
    """AC-5: the constants of data-model 013 §6, transcribed verbatim —
    the single source of truth (a permuted calibration outcome is a
    one-line edit in PUSHED_TEMPS)."""
    assert registers.PUSH_TEMP_BLOCK_START == 2046
    assert registers.PUSHED_TEMPS == {
        "suction_temp": 2046,      # T01 — sentinel on this unit -> unknown
        "inlet_water_temp": 2047,  # T02 — the live-proven register [P]
        "outlet_water_temp": 2048,  # T03
        "coil_temp": 2049,         # T04
        "ambient_temp": 2050,      # T05
        "exhaust_temp": 2051,      # T06
    }
    # the mirror pairs: base-pinned keys with their pushed registers
    assert registers.PUSHED_MIRRORS == {
        "inlet_water_temp": 2047,
        "outlet_water_temp": 2048,
        "ambient_temp": 2050,
    }
    # the push-only registry: suction/coil/exhaust, temperatures only
    assert [d.key for d in registers.PUSHED_SENSORS] == [
        "suction_temp",
        "coil_temp",
        "exhaust_temp",
    ]
    assert all(d.device_class == "temperature" for d in registers.PUSHED_SENSORS)
    assert all(d.address == registers.PUSHED_TEMPS[d.key] for d in registers.PUSHED_SENSORS)
    # the pushed plane window (FR-7): 2001-2090
    assert registers.PUSH_PLANE_FIRST == 2001
    assert registers.PUSH_PLANE_LAST == 2090


def test_pushed_integrity_shipped_registries_pass() -> None:
    """AC-6: the shipped registries (base + pushed) pass at import."""
    registers.check_registry_integrity()  # must not raise


def test_pushed_integrity_duplicate_raises() -> None:
    """AC-6: a duplicate pushed address within the pushed plane raises."""
    dup = {
        "inlet_water_temp": 2047,
        "outlet_water_temp": 2047,  # same pushed address, other key
        "ambient_temp": 2050,
    }
    try:
        registers.check_registry_integrity(pushed_temps=dup)
    except ValueError as err:
        assert "2047" in str(err), err
    else:
        raise AssertionError("duplicate pushed address did not raise")


def test_pushed_integrity_outside_window_raises() -> None:
    """AC-6: a pushed address outside 2001-2090 raises (the same
    loud-failure spirit as the spec 003 base-block window)."""
    for bad in (2000, 2091, 1050, 1001, 7):
        pushed = dict(registers.PUSHED_TEMPS)
        pushed["inlet_water_temp"] = bad
        try:
            registers.check_registry_integrity(pushed_temps=pushed)
        except ValueError as err:
            assert str(bad) in str(err), err
        else:
            raise AssertionError(f"pushed address {bad} outside 2001-2090 did not raise")


def test_pushed_integrity_non_temperature_key_raises() -> None:
    """AC-6/contract 013 §7.1: a pushed key must be a shipped temperature
    entity — fault_code is shipped but not a temperature; an unknown key
    is not shipped at all; the H/R/P families are never entities
    (spec 003 §6 — the pushed plane maps sensor keys only)."""
    for bad_map in (
        {"fault_code": 2047},       # shipped, not a temperature
        {"h04_threshold": 2055},    # not shipped at all
        {"compressor": 2050},       # shipped binary, not a temperature
    ):
        try:
            registers.check_registry_integrity(pushed_temps=bad_map)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad pushed key map {bad_map} did not raise")


def test_pushed_integrity_permuted_mapping_is_legal() -> None:
    """AC-5: a permuted calibration outcome is a LEGAL one-line edit —
    swapping two members of PUSHED_TEMPS keeps the registries valid
    (window + no duplicates still hold)."""
    permuted = dict(registers.PUSHED_TEMPS)
    permuted["inlet_water_temp"], permuted["outlet_water_temp"] = (
        permuted["outlet_water_temp"],
        permuted["inlet_water_temp"],
    )
    registers.check_registry_integrity(pushed_temps=permuted)  # must not raise


# ===========================================================================
# Part B — the sensor-platform pushed surface (spec 013 FR-5/FR-6)
# ===========================================================================

PKG = "aqua_temp_ble_push_013"  # synthetic package (distinct from siblings)


def _install_stubs() -> None:
    """Minimal fake homeassistant modules — the superset imported by
    sensor.py + entity.py + coordinator.py (the test_coordinator_probe
    pattern extended with the sensor/entity helpers)."""

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
        """Fake base: stores what the real constructor stores."""

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
    test_config_flow_walkthrough pattern)."""
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


coordinator_mod = _import_pkg_module("coordinator")
sensor_mod = _import_pkg_module("sensor")


class _FakeClient:
    """Duck-typed client for the platform tests: connected, a scripted
    store, no I/O at all (the zero-TX surface)."""

    is_connected = True

    def __init__(self) -> None:
        self.read_calls: list[tuple] = []

    async def read_registers(self, start, count, **_kwargs):
        self.read_calls.append((start, count))
        return {}

    async def harvest_pushes(self, wanted=None, timeout=None):
        return {}

    async def connect(self, **_kwargs):  # pragma: no cover - not exercised
        return None

    async def disconnect(self):  # pragma: no cover - not exercised
        return None


def _make_platform(
    data: dict, options: dict
):
    """A coordinator + entry + hass triple wired for the platform."""
    entry = SimpleNamespace(
        entry_id="test_entry",
        title="Aqua Temp test",
        data={"address": ADDRESS, **data},
        options=dict(options),
    )
    coordinator = coordinator_mod.AquaTempCoordinator(
        hass=None, entry=entry, client=_FakeClient()
    )
    caps = {registers.PUSH_TEMP_BLOCK_START: _capture(2046, T_WINDOW_RAWS, count=2)}
    coordinator.data = coordinator_mod.AquaTempData.from_pushes(caps)
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: {"coordinator": coordinator}}}
    )
    return hass, entry, coordinator


def _added_entities(hass, entry) -> list:
    added: list = []

    def _collect(entities) -> None:
        # HA's AddEntitiesCallback is called synchronously by the
        # platform — a plain function, not a coroutine.
        added.extend(entities)

    asyncio.run(sensor_mod.async_setup_entry(hass, entry, _collect))
    return added


def _sensor_keys(added: list) -> set[str]:
    return {
        e._regdef.key
        for e in added
        if isinstance(e, sensor_mod.AquaTempSensorEntity)
    }


def test_push_mode_creation_unconditional() -> None:
    """AC-5 (spec 022): ALL six T-family keys are created
    UNCONDITIONALLY in push mode — active by default, no options, no
    gates; fault_code/firmware NOT created (no pushed home); the
    push_stream diagnostic IS created. Inverts the 013 closed-gate
    world (the withholding behaviour is gone with the gate)."""
    hass, entry, _coord = _make_platform(data={"mode": "push"}, options={})
    added = _added_entities(hass, entry)
    keys = _sensor_keys(added)
    assert keys == {
        "inlet_water_temp",
        "outlet_water_temp",
        "ambient_temp",
        "suction_temp",
        "coil_temp",
        "exhaust_temp",
    }, keys
    assert any(
        isinstance(e, sensor_mod.AquaTempPushStreamEntity) for e in added
    ), [type(e).__name__ for e in added]


def test_push_mode_decode_live_raws_unconditional() -> None:
    """AC-5 (spec 022, inverted): the pushed raws ALWAYS enter the
    entity plane — the inlet decodes the live value with NO options set
    (active by default; the withholding behaviour is gone with the
    gate)."""
    hass, entry, coordinator = _make_platform(
        data={"mode": "push"}, options={}
    )
    added = _added_entities(hass, entry)
    for entity in added:
        if isinstance(entity, sensor_mod.AquaTempSensorEntity) and (
            entity._regdef.key == "inlet_water_temp"
        ):
            assert entity.native_value == 14.5  # 2047 = 145 raw
            break
    else:
        raise AssertionError("inlet entity not found")


def test_push_mode_decode_pinned_vector() -> None:
    """AC-5: 2047 -> 14.5 degC from the pinned vector (the
    live-proven register [P]); the T01 sentinel at 2046 decodes
    `unknown` — the vendor's 3276.5 degC display bug must never
    replicate (data-model 013 §5). No options needed (spec 022 —
    active by default)."""
    hass, entry, coordinator = _make_platform(
        data={"mode": "push"}, options={}
    )
    added = _added_entities(hass, entry)
    values: dict[str, object] = {}
    for entity in added:
        if isinstance(entity, sensor_mod.AquaTempSensorEntity):
            values[entity._regdef.key] = entity.native_value
    assert values["inlet_water_temp"] == 14.5, values   # 2047 = 145 raw
    assert values["outlet_water_temp"] == 14.0, values   # 2048 = 140
    assert values["ambient_temp"] == 13.5, values        # 2050 = 135
    assert values["coil_temp"] == 14.0, values           # 2049 = 140
    assert values["exhaust_temp"] == 15.0, values        # 2051 = 150
    assert values["suction_temp"] is None, values        # 2046 = sentinel


def test_push_mode_permuted_mapping_swaps_sources() -> None:
    """AC-5 — the single-source-of-truth test: swapping 2047/2048 in
    PUSHED_TEMPS swaps the sources the entities read (the runtime
    lookup goes through PUSHED_TEMPS, so a permuted calibration
    outcome is exactly one edit)."""
    pkg_registers = sys.modules[f"{PKG}.registers"]
    original = dict(pkg_registers.PUSHED_TEMPS)
    pkg_registers.PUSHED_TEMPS = dict(original)
    pkg_registers.PUSHED_TEMPS["inlet_water_temp"], (
        pkg_registers.PUSHED_TEMPS["outlet_water_temp"]
    ) = (
        original["outlet_water_temp"],
        original["inlet_water_temp"],
    )
    try:
        hass, entry, _coord = _make_platform(
            data={"mode": "push"}, options={}
        )
        added = _added_entities(hass, entry)
        values: dict[str, object] = {}
        for entity in added:
            if isinstance(entity, sensor_mod.AquaTempSensorEntity):
                values[entity._regdef.key] = entity.native_value
        assert values["inlet_water_temp"] == 14.0, values   # now reads 2048
        assert values["outlet_water_temp"] == 14.5, values  # now reads 2047
    finally:
        pkg_registers.PUSHED_TEMPS = original


def test_push_stream_diagnostic_attributes() -> None:
    """FR-6: the push_stream diagnostic reports the mode, the per-start
    counts and the first/last push ages — the owner watches stream
    health without a terminal."""
    hass, entry, coordinator = _make_platform(
        data={"mode": "push"}, options={}
    )
    added = _added_entities(hass, entry)
    stream = [
        e for e in added if isinstance(e, sensor_mod.AquaTempPushStreamEntity)
    ]
    assert len(stream) == 1, [type(e).__name__ for e in added]
    entity = stream[0]
    attrs = entity.extra_state_attributes
    assert attrs["mode"] == "push"
    assert attrs["per_start_counts"] == {"2046": 2}
    assert isinstance(attrs["last_push_age_s"], float)
    assert isinstance(attrs["first_push_age_s"], float)


def test_classic_mode_creation_unchanged() -> None:
    """US-6 non-regression: without the push mode the platform keeps
    today's exact surface — fault_code + firmware created (addresses
    pinned), placeholders skipped (None), and NO push_stream."""
    hass, entry, _coord = _make_platform(data={}, options={})
    added = _added_entities(hass, entry)
    keys = _sensor_keys(added)
    assert keys == {"inlet_water_temp", "outlet_water_temp", "ambient_temp",
                    "fault_code", "firmware"}, keys
    assert not any(
        isinstance(e, sensor_mod.AquaTempPushStreamEntity) for e in added
    ), [type(e).__name__ for e in added]


def main() -> int:
    tests = [
        test_pushed_plane_constants_transcribed,
        test_pushed_integrity_shipped_registries_pass,
        test_pushed_integrity_duplicate_raises,
        test_pushed_integrity_outside_window_raises,
        test_pushed_integrity_non_temperature_key_raises,
        test_pushed_integrity_permuted_mapping_is_legal,
        test_push_mode_creation_unconditional,
        test_push_mode_decode_live_raws_unconditional,
        test_push_mode_decode_pinned_vector,
        test_push_mode_permuted_mapping_swaps_sources,
        test_push_stream_diagnostic_attributes,
        test_classic_mode_creation_unchanged,
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
    print(f"PASSED: {len(tests)}/{len(tests)} pushed-registers tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())