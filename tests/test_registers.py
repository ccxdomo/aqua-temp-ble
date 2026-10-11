#!/usr/bin/env python3
"""Unit tests for registers.py — the decode layer & registries (spec 003).

Covers every vector of
specs/003-sensor-coverage/contracts/decode-contract.md §6 plus the
shipped-registry assertions of contracts/entity-contract.md §2/§3/§6:
sentinel set (both conventions), plausibility guard, binary polarity
(per-RegDef override), write allowlist, import-time integrity, and the
placeholder table — new keys present, all new addresses None, existing
keys/icons/addresses unchanged.

Run standalone:

    python3 tests/test_registers.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"))

import registers  # noqa: E402
from registers import RegDef  # noqa: E402


def _raises_value_error(fn, *args, **kwargs) -> bool:
    """True if fn(*args, **kwargs) raises exactly ValueError."""
    try:
        fn(*args, **kwargs)
    except ValueError:
        return True
    except BaseException:
        return False
    return False


# ------------------------------------------------------------- decode layer
def test_sentinel_set() -> None:
    """FR-1/FR-14: both sentinel conventions + the legacy alias."""
    assert registers.SENTINEL_RAWS == frozenset({-9990, 32765})
    assert registers.SENTINEL_RAW == -9990  # legacy alias (FR-14)
    assert registers.SENTINEL_RAW in registers.SENTINEL_RAWS
    assert registers.is_sentinel(-9990)   # [P apk] -999.0 degC convention
    assert registers.is_sentinel(32765)   # [P panel] 0x7FFD convention
    assert not registers.is_sentinel(0)
    assert not registers.is_sentinel(None)
    assert not registers.is_sentinel(32767)  # unknown future sentinel is NOT a member


def test_decode_temp_vectors() -> None:
    """decode-contract.md §6 vectors (AC-1/AC-2)."""
    assert registers.decode_temp(None) is None
    assert registers.decode_temp(-9990) is None   # sentinel, apk convention
    assert registers.decode_temp(32765) is None   # sentinel, panel convention
    assert registers.decode_temp(170) == 17.0     # T02 Inlet, panel-observed
    assert registers.decode_temp(195) == 19.5     # T03 Outlet / T04 Coil
    assert registers.decode_temp(175) == 17.5     # T06 Exhaust
    assert registers.decode_temp(180) == 18.0     # T05 Ambient
    assert registers.decode_temp(-100) == -10.0  # winter ambient passes (E-6)
    assert registers.decode_temp(1050) == 105.0  # H04-scale value passes
    assert registers.decode_temp(-450) == -45.0  # window bounds are inclusive
    assert registers.decode_temp(1500) == 150.0
    assert registers.decode_temp(2000) is None    # implausible ceiling
    assert registers.decode_temp(-5000) is None  # implausible floor
    assert registers.decode_temp(32767) is None  # unknown sentinel -> guard (E-7)
    assert registers.decode_temp(43520) is None  # endianness garbage 0xAA00
    assert registers.is_plausible_temp(0)
    assert not registers.is_plausible_temp(None)
    assert not registers.is_plausible_temp(32765)  # sentinel is out of window too


def test_decode_flag_matrix() -> None:
    """decode-contract.md §4/§6 polarity matrix (AC-8)."""
    assert registers.decode_flag(None, True) is None
    assert registers.decode_flag(None, False) is None
    assert registers.decode_flag(0, True) is False
    assert registers.decode_flag(1, True) is True
    assert registers.decode_flag(5, True) is True     # any nonzero means ON [A]
    assert registers.decode_flag(0, False) is True    # inverted polarity
    assert registers.decode_flag(1, False) is False  # inverted polarity


def test_polarity_resolution() -> None:
    """FR-4/AC-8: a RegDef-level active_when_nonzero=False overrides the global."""
    d_default = RegDef("x", None, "X", is_binary=True)
    d_inverted = RegDef("y", None, "Y", is_binary=True, active_when_nonzero=False)
    d_direct = RegDef("z", None, "Z", is_binary=True, active_when_nonzero=True)
    assert registers.resolve_polarity(d_default) is registers.BINARY_ACTIVE_WHEN_NONZERO
    assert registers.resolve_polarity(d_inverted) is False
    assert registers.resolve_polarity(d_direct) is True
    # End-to-end: an inverted RegDef reads raw 0 as ON (S03-style, E-4).
    assert registers.decode_flag(0, registers.resolve_polarity(d_inverted)) is True


# ------------------------------------------------------------ write security
def test_ensure_writable_matrix() -> None:
    """decode-contract.md §5 write guard (AC-6)."""
    for address in (registers.REG_POWER, registers.REG_MODE, registers.REG_SET_CURRENT_TEMP):
        registers.ensure_writable(address)  # must not raise
    # Installer/high-block registers are never writable — the guard raises.
    for address in (15, 1004, 2001, 2090):
        assert _raises_value_error(registers.ensure_writable, address), (
            f"ensure_writable({address}) must raise"
        )
    # The error message names the policy (FR-9).
    try:
        registers.ensure_writable(1004)
    except ValueError as err:
        assert "not writable" in str(err)
        assert "spec 003" in str(err)
    # {19, 20, 21} are conditional on the default-off mirror switch.
    for address in (19, 20, 21):
        assert _raises_value_error(registers.ensure_writable, address), (
            f"ensure_writable({address}) must raise while "
            "WRITE_MODE_SETPOINT_TOO is False"
        )
    saved = registers.WRITE_MODE_SETPOINT_TOO
    try:
        registers.WRITE_MODE_SETPOINT_TOO = True
        for address in (8, 13, 14, 19, 20, 21):
            registers.ensure_writable(address)  # must not raise
        # Even with the mirror switch on, high blocks stay unwritable (AC-6).
        assert _raises_value_error(registers.ensure_writable, 1004)
    finally:
        registers.WRITE_MODE_SETPOINT_TOO = saved
    assert registers.ALLOWED_WRITE_BASE == frozenset({8, 13, 14})


# ------------------------------------------------------ registries & gating
def test_in_base_block() -> None:
    """FR-7: the polled base block window is 7..51 inclusive."""
    assert registers.BASE_BLOCK_START == 7 and registers.BASE_BLOCK_COUNT == 45
    assert registers.in_base_block(7)
    assert registers.in_base_block(51)
    assert not registers.in_base_block(6)
    assert not registers.in_base_block(52)
    assert not registers.in_base_block(1001)


def test_registry_integrity() -> None:
    """FR-11/AC-9: broken registries raise; the shipped ones pass."""
    # The shipped registries already passed at import; re-assert explicitly.
    registers.check_registry_integrity()
    good = (
        RegDef("t1", 30, "T1", is_binary=True),
        RegDef("t2", 31, "T2", is_binary=True),
    )
    registers.check_registry_integrity(good, ())  # sanity: a clean pair passes
    # Duplicate key within one registry.
    dup_key = (RegDef("t1", 30, "T1", is_binary=True),
               RegDef("t1", 31, "T1b", is_binary=True))
    assert _raises_value_error(registers.check_registry_integrity, dup_key, ())
    # Duplicate key across the two registries (keys are globally unique).
    cross_key = (RegDef("t1", 30, "T1"),)
    cross_bin = (RegDef("t1", 31, "T1", is_binary=True),)
    assert _raises_value_error(registers.check_registry_integrity, cross_key, cross_bin)
    # Duplicate address within one registry.
    dup_addr = (RegDef("a", 30, "A"), RegDef("b", 30, "B"))
    assert _raises_value_error(registers.check_registry_integrity, dup_addr, ())
    # Duplicate address across the two registries (shadowing, E-8).
    cross_addr_s = (RegDef("a", 30, "A"),)
    cross_addr_b = (RegDef("b", 30, "B", is_binary=True),)
    assert _raises_value_error(
        registers.check_registry_integrity, cross_addr_s, cross_addr_b
    )
    # Out-of-base-block pin -> loud ValueError mentioning the high-block phase.
    high = (RegDef("h", 1001, "H"),)
    assert _raises_value_error(registers.check_registry_integrity, high, ())
    try:
        registers.check_registry_integrity(high, ())
    except ValueError as err:
        assert "outside the polled base block" in str(err)
        assert "high-block" in str(err)
    # Temperature/binary class mismatch.
    mixed = (RegDef("m", 30, "M", is_binary=True, device_class="temperature"),)
    assert _raises_value_error(registers.check_registry_integrity, mixed, ())


def test_shipped_sensor_registry() -> None:
    """entity-contract.md §2: shipped table, key-for-key (AC-3/AC-4)."""
    sensors = {d.key: d for d in registers.SENSORS}
    # Existing temperature sensors: unchanged, family-tagged (T02/T03/T05).
    for key, address, family in (
        ("inlet_water_temp", 26, "T02"),
        ("outlet_water_temp", 27, "T03"),
        ("ambient_temp", 28, "T05"),
    ):
        d = sensors[key]
        assert d.address == address
        assert d.family == family
        assert d.device_class == "temperature"
        assert d.unit == "°C"
        assert d.state_class == "measurement"
        assert d.scale == registers.TEMP_SCALE and d.signed is True
        assert d.icon is None and d.diagnostic is False
    # New T-family placeholders: present, [V] (address None -> no entity, AC-3).
    for key, family in (("suction_temp", "T01"),
                        ("coil_temp", "T04"),
                        ("exhaust_temp", "T06")):
        d = sensors[key]
        assert d.address is None, f"{key} must ship unpinned"
        assert d.family == family
        assert d.device_class == "temperature"
        assert d.unit == "°C"
        assert d.state_class == "measurement"
        assert d.scale == registers.TEMP_SCALE and d.signed is True
        assert d.icon is None  # the temperature device class supplies one
    # Fault code / firmware: existing entries unchanged.
    assert sensors["fault_code"].address == registers.REG_FAULT1 == 47
    assert sensors["fault_code"].icon == "mdi:alert-circle"
    assert sensors["firmware"].address == registers.REG_FIRMWARE_FIRST == 48
    assert sensors["firmware"].icon == "mdi:information"
    assert sensors["firmware"].diagnostic is True
    # No entity exists for any H/R/P/D family member (spec §6 / AC-12).
    assert not any(d.family and d.family[0] in ("H", "R", "P", "D")
                   for d in registers.SENSORS + registers.BINARIES)


def test_shipped_binary_registry() -> None:
    """entity-contract.md §3: shipped table, key-for-key (AC-3)."""
    binaries = {d.key: d for d in registers.BINARIES}
    # Existing O-family placeholders: keys/icons/addresses unchanged,
    # labels panel-confirmed, now family-tagged.
    for key, icon, family in (
        ("compressor", "mdi:engine", "O01"),
        ("circulate_pump", "mdi:pump", "O02"),
        ("four_way_valve", "mdi:swap-horizontal", "O04"),
    ):
        d = binaries[key]
        assert d.address is None
        assert d.icon == icon
        assert d.family == family
        assert d.diagnostic is False
        assert d.active_when_nonzero is None  # global default applies
    # New placeholders: high_fan operational, S family DIAGNOSTIC.
    d = binaries["high_fan"]
    assert d.address is None and d.icon == "mdi:fan" and d.family == "O03"
    assert d.diagnostic is False and d.active_when_nonzero is None
    for key, icon, family in (
        ("hp_switch", "mdi:alert-circle", "S01"),
        ("lp_switch", "mdi:alert-circle", "S02"),
        ("flow_switch", "mdi:water", "S03"),
    ):
        d = binaries[key]
        assert d.address is None, f"{key} must ship unpinned"
        assert d.icon == icon and d.family == family
        assert d.diagnostic is True  # entity category DIAGNOSTIC
        assert d.active_when_nonzero is None  # calibrate polarity live (E-4)
    # No device_class on ANY binary sensor until polarity is proven (AC-8 note).
    assert all(d.device_class is None for d in registers.BINARIES)


def test_known_labels() -> None:
    """FR-13/T-8: probe labels gain family tags; placeholders add nothing yet."""
    assert registers.KNOWN_LABELS[26] == "InletWaterTemp [P] (T02)"
    assert registers.KNOWN_LABELS[27] == "OutletWaterTemp [P] (T03)"
    assert registers.KNOWN_LABELS[28] == "AT [P] (T05)"
    # Every probe label addresses a register inside the base block.
    for addr in registers.KNOWN_LABELS:
        assert registers.in_base_block(addr)
    # While the placeholders are unpinned, no [V] family label exists yet —
    # new labels appear via this mechanism only after pinning.
    assert not any("T01" in label or "S01" in label or "O03" in label
                   for label in registers.KNOWN_LABELS.values())


def test_pinned_fixture_shape() -> None:
    """AC-4/AC-5: pinning inside 7..51 keeps the contract shape and passes
    the integrity check; pinning outside (1001+) fails loudly."""
    pinned = tuple(
        dataclasses.replace(d, address=33) if d.key == "exhaust_temp" else d
        for d in registers.SENSORS
    )
    exhaust = {d.key: d for d in pinned}["exhaust_temp"]
    assert exhaust.address == 33
    assert registers.in_base_block(exhaust.address)
    assert exhaust.device_class == "temperature"
    assert exhaust.unit == "°C"
    assert exhaust.state_class == "measurement"
    assert exhaust.family == "T06"
    registers.check_registry_integrity(pinned, registers.BINARIES)  # must not raise
    # AC-5: a high-block pin (e.g. 1001) violates the integrity check.
    high = tuple(
        dataclasses.replace(d, address=1001) if d.key == "exhaust_temp" else d
        for d in registers.SENSORS
    )
    assert _raises_value_error(
        registers.check_registry_integrity, high, registers.BINARIES
    )


# --------------------------------------------------------------------- main
def main() -> int:
    tests = [
        test_sentinel_set,
        test_decode_temp_vectors,
        test_decode_flag_matrix,
        test_polarity_resolution,
        test_ensure_writable_matrix,
        test_in_base_block,
        test_registry_integrity,
        test_shipped_sensor_registry,
        test_shipped_binary_registry,
        test_known_labels,
        test_pinned_fixture_shape,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as err:
            failures += 1
            print(f"FAIL  {test.__name__}: {err}")
        else:
            print(f"PASS  {test.__name__}")
    print()
    if failures:
        print(f"FAILED: {failures} test(s)")
        return 1
    print(f"PASSED: {len(tests)}/{len(tests)} tests "
          "(decode-contract.md §6 vectors + entity-contract.md registries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())