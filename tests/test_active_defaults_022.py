#!/usr/bin/env python3
"""Spec 022 tests — active by default: the symbol-absence grep, the
strings parity and the gate-free copy (AC-5, AC-8; FR-3/FR-6).

Part A (the symbol grep, AC-5): every removed symbol is GONE from
custom_components/aqua_temp_ble/*.py — the four global switches, the
two shipped defaults, the four option keys (quoted — the transport
methods keep their names), the four coordinator gate properties and
the vestigial dataclass field. The transport write RAILS remain
(client.write_control / client.write_setpoint / modbus.build_write_
control — the proven write paths, never gates).

Part B (the strings, AC-8): strings.json, translations/en.json and
translations/fr.json keep exact key parity; no text mentions gates,
checkboxes or probe-verdict requirements; the confirm_push description
carries exactly the live placeholders {address}/{power}/{setpoint}/
{inlet}/{outlet}/{ambient} and no mode line (FR-2/FR-7); the options
step carries exactly poll_interval + temperature_unit.

Part C (the docs, AC-9): the README states the honest fault/firmware
reason and no gate-opening instruction survives; every remaining
gate/checkbox mention in the docs declares the ABSENCE (spec 022).

Run standalone:

    python3 tests/test_active_defaults_022.py

No real Bluetooth and no real Home Assistant required (pure file
analysis).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_DIR / "custom_components" / "aqua_temp_ble"

# AC-5: the removed symbols. The QUOTED forms of the two write option
# keys are grep-precise: the transport methods (write_control /
# write_setpoint) and the builder (build_write_control) legitimately
# keep their unquoted names — they are the write RAILS, not gates.
# `push_confirmed` is a distinct token (not a substring of
# push_temp_confirmed) — the vestigial dataclass field.
REMOVED_SYMBOLS = (
    "PUSH_TEMP_CONFIRMED",
    "PUSH_CONTROL_CONFIRMED",
    "WRITE_CONTROL_PROVEN",
    "WRITE_SETPOINT_PROVEN",
    "DEFAULT_WRITE_CONTROL",
    "DEFAULT_WRITE_SETPOINT",
    "push_temp_confirmed",
    "push_control_confirmed",
    '"write_control"',
    '"write_setpoint"',
    "write_control_allowed",
    "write_setpoint_allowed",
    "push_confirmed",
)

# The three files whose JSON trees must stay in exact key parity.
STRINGS_FILES = (
    "strings.json",
    "translations/en.json",
    "translations/fr.json",
)

# The live placeholders the confirm_push copy carries (FR-7) — the
# exact set config_flow._push_placeholders provides.
CONFIRM_PUSH_PLACEHOLDERS = {
    "address",
    "power",
    "setpoint",
    "inlet",
    "outlet",
    "ambient",
}


def test_removed_symbols_absent_from_components() -> None:
    """AC-5: grep over custom_components finds NONE of the removed
    symbols — the four switches, the two shipped defaults, the four
    option keys (quoted), the four coordinator gate properties and
    the vestigial dataclass field are all gone."""
    for py in sorted(COMPONENT_DIR.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for symbol in REMOVED_SYMBOLS:
            assert symbol not in text, (py.name, symbol)


def test_transport_rails_retained() -> None:
    """AC-5's carve-out: the write RAILS keep their names — the
    client's write_control/write_setpoint methods and
    modbus.build_write_control (the proven write paths, not gates)."""
    aqua_client = (COMPONENT_DIR / "aqua_client.py").read_text(encoding="utf-8")
    modbus = (COMPONENT_DIR / "modbus.py").read_text(encoding="utf-8")
    assert "async def write_control(" in aqua_client
    assert "async def write_setpoint(" in aqua_client
    assert "def build_write_control(" in modbus


def _flat_keys(obj, prefix: str = "") -> set[str]:
    """The recursive key set of a JSON tree (node and leaf keys)."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(prefix + key)
            keys |= _flat_keys(value, prefix + key + ".")
    return keys


def test_strings_translations_exact_key_parity() -> None:
    """AC-8: the three files keep EXACT key parity — every key in
    strings.json exists in en.json and fr.json and vice versa."""
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text(encoding="utf-8"))
    keys = _flat_keys(strings)
    for name in ("translations/en.json", "translations/fr.json"):
        data = json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))
        assert _flat_keys(data) == keys, name


def test_confirm_push_copy_gate_free_with_live_placeholders() -> None:
    """FR-2/FR-6/FR-7/AC-8: in every file the confirm_push description
    carries exactly the live placeholders, no gate/checkbox/
    calibration sentence, no fault/firmware line and no mode line;
    the schema data key is exactly `name`."""
    for name in STRINGS_FILES:
        data = json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))
        step = data["config"]["step"]["confirm_push"]
        description = step["description"]
        placeholders = set(re.findall(r"\{(\w+)\}", description))
        assert placeholders == CONFIRM_PUSH_PLACEHOLDERS, (name, placeholders)
        # FR-2: the schema asks for a NAME only
        assert set(step["data"]) == {"name"}, (name, step["data"].keys())
        low = description.lower()
        for banned in (
            "gate",
            "checkbox",
            "calibration",
            "confirmed",
            "read-only",
            "fault",
            "firmware",
            "mode",
        ):
            assert banned not in low, (name, banned)


def test_options_step_only_interval_and_unit() -> None:
    """FR-6/AC-8: the options step carries ONLY the poll interval and
    the display unit labels — the four gate labels and BOTH
    data_description entries are gone, in every file."""
    for name in STRINGS_FILES:
        data = json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))
        init = data["options"]["step"]["init"]
        assert set(init["data"]) == {
            "poll_interval",
            "temperature_unit",
        }, (name, init["data"].keys())
        assert "data_description" not in init, (name, init.keys())


def test_no_gate_vocabulary_in_strings_files() -> None:
    """AC-8: none of the three files' texts mentions gates, checkboxes
    or probe-verdict requirements; the removed option keys appear
    nowhere. (The word 'calibration' survives ONLY in the probe
    service descriptions — the probes remain calibration instruments;
    the ADD/OPTIONS copy never mentions it.)"""
    for name in STRINGS_FILES:
        data = json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))
        flat = json.dumps(data, ensure_ascii=False).lower()
        assert "gate" not in flat, (name, "gate")
        assert "checkbox" not in flat, (name, "checkbox")
        assert "executed + reversible" not in flat, (name, "verdict requirement")
        for key in (
            "push_temp_confirmed",
            "push_control_confirmed",
            '"write_control"',
            '"write_setpoint"',
        ):
            assert key.strip('"') not in flat, (name, key)


def test_readme_honest_and_gate_free() -> None:
    """AC-8/AC-9: the README states the REAL fault/firmware reason
    ('no reliable source … identified'), and no gate-opening
    instruction survives — the old gate section, the closed-gates
    advice and the gate-qualified features are gone."""
    readme = (REPO_DIR / "README.md").read_text(encoding="utf-8")
    low = " ".join(readme.lower().split())
    # the honest one-liner (AC-8's fault/firmware sentence)
    assert "no reliable source" in low, "the honest fault reason is missing"
    # the gate-opening world is gone
    assert "the gates (the honesty system)" not in low
    assert "keep the write gates closed" not in low
    assert "four gates" not in low
    assert "a gate is closed" not in low
    # the active-by-default status is stated
    assert "active by default" in low


def test_calibration_doc_optional_not_gates() -> None:
    """AC-9: docs/CALIBRATION.md presents the verifications as
    OPTIONAL confidence checks — 'no gates to open, no options to flip'
    — and points to spec 022 for the removal rationale."""
    doc = (REPO_DIR / "docs" / "CALIBRATION.md").read_text(encoding="utf-8")
    low = " ".join(doc.lower().split())
    assert "no gates to open, no options to flip" in low
    assert "spec 022" in low
    assert "verifying a new unit (optional)" in low


def main() -> int:
    tests = [
        test_removed_symbols_absent_from_components,
        test_transport_rails_retained,
        test_strings_translations_exact_key_parity,
        test_confirm_push_copy_gate_free_with_live_placeholders,
        test_options_step_only_interval_and_unit,
        test_no_gate_vocabulary_in_strings_files,
        test_readme_honest_and_gate_free,
        test_calibration_doc_optional_not_gates,
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
    print(f"PASSED: {len(tests)}/{len(tests)} spec-022 active-defaults tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())