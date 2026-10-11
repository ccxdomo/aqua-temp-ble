#!/usr/bin/env python3
"""Pure tests for the spec 002 device-list builder (_build_device_list).

No Home Assistant and no real bleak required: the HA-facing modules are
stubbed just enough to import config_flow.py, then the list builder is
exercised as a pure function (contract:
specs/002-config-flow-scanning/contracts/device-list-format.md; edge
cases E-1/E-2/E-4/E-5 from spec.md §6).

Run standalone:

    python3 tests/test_config_flow_list.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"


# ---------------------------------------------------------------------------
# Minimal stubs so config_flow.py imports outside Home Assistant. Only the
# list builder is exercised here; nothing HA-related is ever called.
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    if "voluptuous" not in sys.modules:
        vol_mod = types.ModuleType("voluptuous")

        class _Vol:  # noqa: D401 - tiny stand-in, no validation semantics
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

    if "bleak" not in sys.modules:
        bleak_mod = types.ModuleType("bleak")
        bleak_mod.BleakClient = object  # transport seam, never instantiated here
        bleak_mod.BleakScanner = object
        sys.modules["bleak"] = bleak_mod

    if "homeassistant" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        ha_components = types.ModuleType("homeassistant.components")
        ha_helpers = types.ModuleType("homeassistant.helpers")
        ha.config = types.SimpleNamespace(language="en")

        class _StubConfigFlow:
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__()

        class _StubOptionsFlow:
            pass

        cfg_entries = types.ModuleType("homeassistant.config_entries")
        cfg_entries.ConfigFlow = _StubConfigFlow
        cfg_entries.ConfigFlowResult = dict  # annotations only (stringified)
        cfg_entries.OptionsFlow = _StubOptionsFlow
        ha_bt = types.ModuleType("homeassistant.components.bluetooth")
        ha_translation = types.ModuleType("homeassistant.helpers.translation")

        for name, module in {
            "homeassistant": ha,
            "homeassistant.components": ha_components,
            "homeassistant.components.bluetooth": ha_bt,
            "homeassistant.config_entries": cfg_entries,
            "homeassistant.helpers": ha_helpers,
            "homeassistant.helpers.translation": ha_translation,
        }.items():
            sys.modules[name] = module
        ha.components = ha_components
        ha.helpers = ha_helpers


def _import_config_flow():
    """Import config_flow.py as part of a synthetic package."""
    _install_stubs()
    pkg = types.ModuleType("_aqua_flow_pkg")
    pkg.__path__ = [str(COMPONENT_DIR)]  # supports `from . import x`
    sys.modules["_aqua_flow_pkg"] = pkg
    spec = importlib.util.spec_from_file_location(
        "_aqua_flow_pkg.config_flow", COMPONENT_DIR / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["_aqua_flow_pkg.config_flow"] = module
    spec.loader.exec_module(module)
    return module


cf = _import_config_flow()
_build_device_list = cf._build_device_list
UUID_WRITE = cf.UUID_WRITE
UUID_NOTIFY = cf.UUID_NOTIFY


class FakeInfo:
    """Stand-in for a discovered BluetoothServiceInfoBleak entry."""

    def __init__(self, address, name, rssi, service_uuids=()):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.service_uuids = list(service_uuids)


# --- tests -----------------------------------------------------------------


def test_contract_worked_example():
    """Contract §6 worked inputs: exclusion, tiering, UUID signal, ordering, counts.

    Note: the contract's §6 example originally listed BB (-62) before
    CC (-58), contradicting its own §4 rule ("sorted by rssi_key
    descending", i.e. nearest first — also stated in spec FR-4, plan §3 and
    quickstart V4). The example was corrected in the contract to match
    the rule. Expected order below follows §4: CC (-58) before BB (-62),
    EE (-75) before DD (-90).
    """
    infos = [
        FakeInfo("AA:BB:CC:DD:EE:01", "BlueNRG-1", -50, [UUID_WRITE, UUID_NOTIFY]),
        FakeInfo("AA:BB:CC:DD:EE:02", "BlueNRG-7A", -62),
        FakeInfo("AA:BB:CC:DD:EE:03", "AA:BB:CC:DD:EE:03", -58, [UUID_WRITE.upper()]),
        FakeInfo("AA:BB:CC:DD:EE:04", "AA:BB:CC:DD:EE:04", -90),
        FakeInfo("AA:BB:CC:DD:EE:05", "TV Salon", -75),
    ]
    labels, counts = _build_device_list(
        infos, {"AA:BB:CC:DD:EE:01"}, cf.UNNAMED_LABEL
    )
    assert list(labels) == [
        "AA:BB:CC:DD:EE:03",  # tier 1 via UUID signal, RSSI -58 (nearest)
        "AA:BB:CC:DD:EE:02",  # tier 1 via name prefix, RSSI -62
        "AA:BB:CC:DD:EE:05",  # tier 2, RSSI -75
        "AA:BB:CC:DD:EE:04",  # tier 2, RSSI -90 (last)
    ], list(labels)
    assert "AA:BB:CC:DD:EE:01" not in labels  # configured -> excluded
    assert labels["AA:BB:CC:DD:EE:02"] == (
        "BlueNRG-7A — AA:BB:CC:DD:EE:02 — RSSI -62"
    )
    assert (
        labels["AA:BB:CC:DD:EE:03"]
        == "Unnamed device — AA:BB:CC:DD:EE:03 — RSSI -58"
    )
    assert labels["AA:BB:CC:DD:EE:05"] == "TV Salon — AA:BB:CC:DD:EE:05 — RSSI -75"
    assert counts == {"recognized": 2, "total": 4}, counts
    print("PASS  test_contract_worked_example")


def test_unnamed_and_missing_rssi():
    """E-1/E-4: unnamed device labeled, missing RSSI sorts worst, shown verbatim."""
    infos = [
        FakeInfo("11:22:33:44:55:10", "11:22:33:44:55:10", None),
        FakeInfo("11:22:33:44:55:11", "Watch", -100),
    ]
    labels, counts = _build_device_list(infos, set(), cf.UNNAMED_LABEL)
    assert list(labels) == ["11:22:33:44:55:11", "11:22:33:44:55:10"], list(labels)
    assert (
        labels["11:22:33:44:55:10"]
        == "Unnamed device — 11:22:33:44:55:10 — RSSI None"
    )
    assert counts == {"recognized": 0, "total": 2}, counts
    print("PASS  test_unnamed_and_missing_rssi")


def test_prefix_matching_rules():
    """E-2: exact startswith (case-insensitive) on all NAME_PREFIXES; near-miss -> tier 2.

    Spec 006 FR-4/AC-4: "wt-" is part of the vendor's operating-channel
    filter (research 006 §3) — a WT-AB12 advertiser must be recognized.
    Red-first: on the pre-006 NAME_PREFIXES the WT- entry lands in tier 2.
    """
    infos = [
        FakeInfo("AA:00:00:00:00:01", "blueNRg-7", -50),        # case variant
        FakeInfo("AA:00:00:00:00:02", "HIFLYING-2", -60),
        FakeInfo("AA:00:00:00:00:06", "WT-AB12", -65),          # spec 006 WT- prefix
        FakeInfo("AA:00:00:00:00:03", "prov_x", -70),
        FakeInfo("AA:00:00:00:00:04", "bluenrg_", -80),         # near-miss, no UUIDs
        FakeInfo("AA:00:00:00:00:05", "XBlueNRG-9", -90),       # prefix not at start
    ]
    labels, counts = _build_device_list(infos, set(), cf.UNNAMED_LABEL)
    assert counts == {"recognized": 4, "total": 6}, counts
    assert list(labels)[:4] == [
        "AA:00:00:00:00:01",
        "AA:00:00:00:00:02",
        "AA:00:00:00:00:06",
        "AA:00:00:00:00:03",
    ], list(labels)
    print("PASS  test_prefix_matching_rules")


def test_other_tier_cap_and_recognized_never_capped():
    """MAX_OTHER_DEVICES caps the other tier (nearest kept); recognized never capped."""
    others = [
        FakeInfo(f"22:22:22:22:22:{i:02d}", f"Phone {i}", -40 - i) for i in range(35)
    ]
    labels, counts = _build_device_list(
        others, {"22:22:22:22:22:00"}, cf.UNNAMED_LABEL  # strongest, but configured
    )
    assert counts == {"recognized": 0, "total": 30}, counts  # 34 kept -> 30
    assert list(labels)[0] == "22:22:22:22:22:01"  # nearest non-configured first
    assert list(labels)[-1] == "22:22:22:22:22:30"

    recognized = [
        FakeInfo(f"33:33:33:33:33:{i:02d}", f"BlueNRG-{i}", -30 - i) for i in range(35)
    ]
    labels, counts = _build_device_list(recognized, set(), cf.UNNAMED_LABEL)
    assert counts == {"recognized": 35, "total": 35}, counts
    print("PASS  test_other_tier_cap_and_recognized_never_capped")


def test_exclusion_case_insensitive_and_empty_input():
    """Exclusion compares info.address.upper() against the configured set.

    Unique ids are stored upper-cased (contract config-flow-states §4:
    unique_id = address.upper() on every path), and the flow additionally
    upper-cases the ids defensively before calling the builder. Empty
    input yields an empty list and zeroed counts.
    """
    infos = [FakeInfo("aa:bb:cc:dd:ee:99", "BlueNRG-99", -50)]  # lowercase address
    labels, counts = _build_device_list(
        infos, {"AA:BB:CC:DD:EE:99"}, cf.UNNAMED_LABEL
    )
    assert labels == {} and counts == {"recognized": 0, "total": 0}, (labels, counts)
    labels, counts = _build_device_list([], set(), cf.UNNAMED_LABEL)
    assert labels == {} and counts == {"recognized": 0, "total": 0}, (labels, counts)
    print("PASS  test_exclusion_case_insensitive_and_empty_input")


def test_flow_constants():
    """Spec 002 flow constants: values from const.py (tasks.md block 1).

    The scan delay and the other-tier cap live in const.py under their
    contract names; no dropdown sentinel keys may reappear (the flow is a
    menu — FR-1), and the unnamed label is the contract text (§5).
    """
    assert cf.SCAN_WAIT_SECONDS == 8.0
    assert cf.MAX_OTHER_DEVICES == 30
    # No sentinel values: the flow uses a menu, not dropdown pseudo-entries.
    assert not hasattr(cf, "RESCAN_KEY")
    assert not hasattr(cf, "MANUAL_KEY")
    assert not hasattr(cf, "SCAN_WAIT")
    assert not hasattr(cf, "OTHER_DEVICES_MAX")
    # English fallback labels exist for every menu option.
    assert cf.MENU_LABELS_EN == {
        "device": "Select a discovered device",
        "scan": "Scan again",
        "manual": "Enter the Bluetooth address manually",
    }
    assert cf.UNNAMED_LABEL == "Unnamed device"
    print("PASS  test_flow_constants")


def main() -> None:
    tests = [
        test_contract_worked_example,
        test_unnamed_and_missing_rssi,
        test_prefix_matching_rules,
        test_other_tier_cap_and_recognized_never_capped,
        test_exclusion_case_insensitive_and_empty_input,
        test_flow_constants,
    ]
    for test in tests:
        test()
    print(f"\nPASSED: {len(tests)}/{len(tests)} config flow list-builder tests")


if __name__ == "__main__":
    main()