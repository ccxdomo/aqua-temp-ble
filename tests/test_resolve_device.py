#!/usr/bin/env python3
"""Unit tests for AquaTempBleClient._resolve_device (spec 005).

Resolution inside Home Assistant is an explicit 3-tier HA-API walk —
connectable cache -> non-connectable cache -> name-prefix match over the
connectable discovery history (plan 005 §1.1, contracts/
device-resolution.md §2) — and raw bleak lookups run ONLY in standalone
mode (hass is None), byte-identical to 5aa887b (NFR-4). A resolution miss
raises the new AquaTempDeviceNotFoundError from connect() (FR-9).

Home Assistant is faked the same way the walkthrough suite fakes it: a
stub `homeassistant.components.bluetooth` module with the two API
functions is injected into sys.modules (the import inside _resolve_device
is lazy and resolved at call time), and bleak is stubbed so the component
imports with nothing installed. The raw standalone path is exercised by
patching the module-level BleakScanner name that aqua_client bound at
its import.

Red-first: every HA-tier case and the not-found cases FAIL on the 5aa887b
code (no AquaTempDeviceNotFoundError, no tier-2/3 HA walk, no provenance
logs); the two standalone passthrough cases are regression guards for
NFR-4 and may already pass.

Run standalone:

    python3 tests/test_resolve_device.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import types
from pathlib import Path

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
)
ADDRESS = "08:06:A6:C0:94:1C"  # the production unit's address (spec 005 §1)


# ---------------------------------------------------------------------------
# Stubs: fake bleak + fake homeassistant.components.bluetooth, then import
# aqua_client standalone (same technique as the walkthrough suite).
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    bleak_mod = types.ModuleType("bleak")
    bleak_mod.BleakClient = object
    bleak_mod.BleakScanner = object
    sys.modules["bleak"] = bleak_mod

    # A FRESH bluetooth module object every time (never mutate an existing
    # one): under pytest the walkthrough suite binds its own bluetooth stub
    # at its import; replacing the sys.modules entry leaves those bound
    # references intact while the call-time lazy import here picks ours.
    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _async_ble_device_from_address(hass, address, connectable=True):
        # Manager-history dict lookup (research 005 §2.1): the fake hass
        # carries the two history tiers directly, keyed by address.
        history = hass.connectable_history if connectable else hass.all_history
        return history.get(address)

    def _async_discovered_service_info(hass, connectable=True):
        return list(hass.connectable_infos)

    ha_bt.async_ble_device_from_address = _async_ble_device_from_address
    ha_bt.async_discovered_service_info = _async_discovered_service_info

    ha.components = ha_components
    ha_components.bluetooth = ha_bt
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.components"] = ha_components
    sys.modules["homeassistant.components.bluetooth"] = ha_bt


def _import_aqua_client():
    sys.path.insert(0, str(COMPONENT_DIR))
    _install_stubs()
    import aqua_client  # noqa: E402

    return aqua_client


ac = _import_aqua_client()


# ---------------------------------------------------------------------------
# Fakes: hass, devices, service infos, raw scanner
# ---------------------------------------------------------------------------


class FakeHass:
    """The two manager-history tiers + the discovery list, scriptable."""

    def __init__(self, connectable=None, non_connectable=None, infos=()):
        self.connectable_history = dict(connectable or {})  # tier 1
        self.all_history = dict(non_connectable or {})      # tier 2
        self.connectable_infos = list(infos)               # tier 3 walk


class FakeBLEDevice:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class FakeServiceInfo:
    """Stand-in for BluetoothServiceInfoBleak (tier-3 walk inputs)."""

    def __init__(self, name, address, device):
        self.name = name
        self.address = address
        self.device = device


class _CaptureHandler(logging.Handler):
    """Collect formatted log messages — caplog without pytest."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class _LogCapture:
    """Attach a capturing handler to a logger inside a `with`.

    The logger level is lowered to DEBUG on entry and restored on exit so
    resolution-provenance DEBUG lines are assertable (FR-3).
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


class FakeBleakScanner:
    """Stand-in for bleak.BleakScanner on the standalone path (FR-8).

    aqua_client binds BleakScanner at its import; the tests patch the
    module-level name (see _fake_raw_scanner) the same way the walkthrough
    suite swaps the client class.
    """

    by_address_result = None
    discover_result: list = []

    @classmethod
    async def find_device_by_address(cls, address, timeout=5.0):
        return cls.by_address_result

    @classmethod
    async def discover(cls, timeout=5.0):
        return list(cls.discover_result)


@contextlib.contextmanager
def _fake_raw_scanner(by_address_result=None, discover_result=()):
    """Patch aqua_client.BleakScanner for a standalone-mode test."""
    FakeBleakScanner.by_address_result = by_address_result
    FakeBleakScanner.discover_result = list(discover_result)
    original = ac.BleakScanner
    ac.BleakScanner = FakeBleakScanner
    try:
        yield FakeBleakScanner
    finally:
        ac.BleakScanner = original


def make_client(address=ADDRESS, name=None, hass=None):
    return ac.AquaTempBleClient(address, name=name, hass=hass)


# ---------------------------------------------------------------------------
# Tests — HA mode (plan 005 §1.1, contract §2)
# ---------------------------------------------------------------------------


def test_tier1_connectable_cache_hit():
    """E-1/FR-7: connectable-history hit resolves + DEBUG provenance log."""
    dev = FakeBLEDevice(ADDRESS, "BlueNRG-C0941C")
    hass = FakeHass(connectable={ADDRESS: dev})
    client = make_client(hass=hass)

    async def scenario():
        with _LogCapture(ac._LOGGER) as log:
            resolved = await client._resolve_device()
        assert resolved is dev, resolved
        # FR-3: provenance is logged, never claiming a scan inside HA.
        assert any(
            ADDRESS in m and "resolved from HA connectable cache" in m
            for m in log.messages
        ), log.messages

    asyncio.run(scenario())
    print("PASS  test_tier1_connectable_cache_hit")


def test_tier2_non_connectable_fallback():
    """E-3/FR-7: only visible to non-connectable scanners -> tier-2 hit +
    WARNING that the connection may fail (AC-5)."""
    dev = FakeBLEDevice(ADDRESS, "BlueNRG-C0941C")
    hass = FakeHass(non_connectable={ADDRESS: dev})
    client = make_client(hass=hass)

    async def scenario():
        with _LogCapture(ac._LOGGER) as log:
            resolved = await client._resolve_device()
        assert resolved is dev, resolved
        assert any(
            ADDRESS in m
            and "non-connectable scanner" in m
            and "connection may fail" in m
            for m in log.messages
        ), log.messages

    asyncio.run(scenario())
    print("PASS  test_tier2_non_connectable_fallback")


def test_tier3_name_prefix_match_and_different_address():
    """E-5/FR-7: the MAC-change case — tier-3 prefix match is returned,
    with the tier-3 entry WARNING and the existing DIFFERENT-address
    WARNING (kept verbatim)."""
    new_address = "08:06:A6:C0:94:99"
    dev = FakeBLEDevice(new_address, "BlueNRG-2")
    hass = FakeHass(infos=[FakeServiceInfo("BlueNRG-2", new_address, dev)])
    client = make_client(hass=hass)  # configured address in no history tier

    async def scenario():
        with _LogCapture(ac._LOGGER) as log:
            resolved = await client._resolve_device()
        assert resolved is dev, resolved
        # Tier-3 entry warning — truthful text, no "scanning" wording.
        assert any(
            ADDRESS in m and "name-prefix match" in m for m in log.messages
        ), log.messages
        # The existing MAC-change warning, verbatim.
        assert any(
            "DIFFERENT address" in m and new_address in m
            for m in log.messages
        ), log.messages

    asyncio.run(scenario())
    print("PASS  test_tier3_name_prefix_match_and_different_address")


def test_tier3_multiple_matches_warn_and_first_wins():
    """E-11/FR-7: several BlueNRG candidates -> the existing multi-match
    WARNING; the first match (insertion order) wins."""
    dev1 = FakeBLEDevice("08:06:A6:C0:94:91", "BlueNRG-A")
    dev2 = FakeBLEDevice("08:06:A6:C0:94:92", "BlueNRG-B")
    infos = [
        FakeServiceInfo("BlueNRG-A", "08:06:A6:C0:94:91", dev1),
        FakeServiceInfo("BlueNRG-B", "08:06:A6:C0:94:92", dev2),
    ]
    hass = FakeHass(infos=infos)
    client = make_client(hass=hass)

    async def scenario():
        with _LogCapture(ac._LOGGER) as log:
            resolved = await client._resolve_device()
        assert resolved is dev1, resolved  # first in insertion order
        assert any(
            "multiple Aqua Temp candidates" in m for m in log.messages
        ), log.messages

    asyncio.run(scenario())
    print("PASS  test_tier3_multiple_matches_warn_and_first_wins")


def test_all_tiers_miss_raises_device_not_found():
    """E-4/FR-9: no tier finds the address -> _resolve_device() is None and
    connect() raises AquaTempDeviceNotFoundError (not the generic
    AquaTempConnectionError)."""
    hass = FakeHass()  # every history tier empty, no infos
    client = make_client(hass=hass)

    async def scenario():
        with _LogCapture(ac._LOGGER) as log:
            resolved = await client._resolve_device()
            assert resolved is None, resolved
            try:
                await client.connect()
            except ac.AquaTempDeviceNotFoundError as exc:
                assert "no Aqua Temp device found" in str(exc), str(exc)
                assert ADDRESS in str(exc), str(exc)
            else:
                raise AssertionError(
                    "connect() must raise AquaTempDeviceNotFoundError"
                )
        # Provenance: the tier-3 entry WARNING fired before the miss.
        assert any(
            ADDRESS in m and "name-prefix match" in m for m in log.messages
        ), log.messages

    asyncio.run(scenario())
    # Type contract (plan 005 §1.3): an AquaTempError, NOT an
    # AquaTempConnectionError — the found/could-not-connect split is the
    # point of FR-9 (coordinator's `except AquaTempError` still catches it).
    assert issubclass(ac.AquaTempDeviceNotFoundError, ac.AquaTempError)
    assert not issubclass(
        ac.AquaTempDeviceNotFoundError, ac.AquaTempConnectionError
    )
    print("PASS  test_all_tiers_miss_raises_device_not_found")


# ---------------------------------------------------------------------------
# Tests — standalone mode (FR-8/NFR-4: byte-identical to 5aa887b)
# ---------------------------------------------------------------------------


def test_standalone_find_by_address_passthrough():
    """hass=None keeps the raw bleak path: the direct address lookup wins
    and NO scan warning fires."""
    dev = FakeBLEDevice(ADDRESS, "BlueNRG-C0941C")

    async def scenario():
        with _fake_raw_scanner(by_address_result=dev):
            with _LogCapture(ac._LOGGER) as log:
                client = make_client()  # hass=None
                resolved = await client._resolve_device()
        assert resolved is dev, resolved
        # The 5 s direct lookup hit: the scan-by-name-prefix warning must
        # NOT fire on this path.
        assert not any(
            "scanning by name prefix" in m for m in log.messages
        ), log.messages

    asyncio.run(scenario())
    print("PASS  test_standalone_find_by_address_passthrough")


def test_standalone_both_scans_empty_returns_none():
    """hass=None, both raw scans miss -> None (same standalone log texts)
    and connect() raises AquaTempDeviceNotFoundError."""
    other = FakeBLEDevice("11:22:33:44:55:66", "Living room TV")

    async def scenario():
        with _fake_raw_scanner(discover_result=[other]):
            with _LogCapture(ac._LOGGER) as log:
                client = make_client()
                resolved = await client._resolve_device()
        assert resolved is None, resolved
        # NFR-4: the standalone scan texts are unchanged from 5aa887b.
        assert any(
            ADDRESS in m and "scanning by name prefix" in m
            for m in log.messages
        ), log.messages

    asyncio.run(scenario())

    async def connect_scenario():
        with _fake_raw_scanner():
            try:
                await make_client().connect()
            except ac.AquaTempDeviceNotFoundError:
                pass
            else:
                raise AssertionError(
                    "connect() must raise AquaTempDeviceNotFoundError"
                )

    asyncio.run(connect_scenario())
    print("PASS  test_standalone_both_scans_empty_returns_none")


# ---------------------------------------------------------------------------
# FR-1: the public exception-chain renderer
# ---------------------------------------------------------------------------


def test_exc_chain_public_with_alias():
    """FR-1: exc_chain is public and renders the cause chain; the private
    _exc_chain name stays as an alias (no behaviour change)."""
    root = RuntimeError("root cause")
    exc = ac.AquaTempConnectionError("outer failure")
    exc.__cause__ = root
    rendered = ac.exc_chain(exc)
    assert "AquaTempConnectionError: outer failure" in rendered, rendered
    assert "<- RuntimeError: root cause" in rendered, rendered
    assert ac._exc_chain is ac.exc_chain  # FR-1: private alias kept
    print("PASS  test_exc_chain_public_with_alias")


# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        test_tier1_connectable_cache_hit,
        test_tier2_non_connectable_fallback,
        test_tier3_name_prefix_match_and_different_address,
        test_tier3_multiple_matches_warn_and_first_wins,
        test_all_tiers_miss_raises_device_not_found,
        test_standalone_find_by_address_passthrough,
        test_standalone_both_scans_empty_returns_none,
        test_exc_chain_public_with_alias,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 — report, don't crash
            failures += 1
            print(f"FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
            continue
    if failures:
        print(f"\nFAILED: {failures}/{len(tests)} device resolution tests")
        sys.exit(1)
    print(f"\nPASSED: {len(tests)}/{len(tests)} device resolution tests")


if __name__ == "__main__":
    main()