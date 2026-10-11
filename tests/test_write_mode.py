#!/usr/bin/env python3
"""Write-mode rule tests (spec 010 FR-3 / AC-2).

The vendor app's write wrapper is the ONLY write path in the whole Dart
binary and it uses exactly ONE WriteType instance: withoutResponse
(research 010 §2.3 — no withResponse, no noResponse string exists
anywhere in the object pool). FR-3 pins our rule to that:

- 'write-without-response' advertised -> write WITHOUT response
  (app parity, the default);
- only 'write' advertised -> write WITH response — an ATT-layer
  constraint, not a preference: a write-only characteristic forbids
  Write Commands — + WARNING;
- no write property at all -> write WITHOUT response anyway — the app,
  property-blind (research 010 §1), would do exactly that — + WARNING
  naming the anomaly. The pre-010 code hard-refused this case
  (spec 006 AC-2); FR-3 supersedes it because the refusal was only
  ever reachable through the deleted ff01-first branch.

Red-first: on the pre-010 code the no-property rows raise
AquaTempConnectionError and the write-only row switches silently (no
WARNING) — both expectations below fail until FR-3 lands.

Run standalone:

    python3 tests/test_write_mode.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import types
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
sys.path.insert(0, str(COMPONENT_DIR))

try:
    import bleak  # noqa: F401
except ImportError:
    # Inject a minimal fake bleak: the setter never touches the classes.
    bleak = types.ModuleType("bleak")

    class _FakeBleakClient:  # pragma: no cover - placeholder
        is_connected = False

    class _FakeBleakScanner:  # pragma: no cover - placeholder
        pass

    bleak.BleakClient = _FakeBleakClient
    bleak.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = bleak

import aqua_client  # noqa: E402
import const  # noqa: E402

ADDRESS = "08:06:A6:C0:94:1C"
FF03 = const.UUID_WRITE  # the app's write bucket head (spec 010 FR-1)


class _LogRecorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _capture():
    """Capture every aqua_client log line at DEBUG."""
    handler = _LogRecorder()
    logger = aqua_client._LOGGER
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield handler.messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _set_mode(props: list[str]) -> tuple[bool, list[str]]:
    """Run the FR-3 rule on a fresh client; returns (mode, logs)."""
    client = aqua_client.AquaTempBleClient(ADDRESS)
    with _capture() as logs:
        client._set_write_mode_from_properties(FF03, list(props))
    return client._write_with_response, logs


# --------------------------------------------------------------------- tests
def test_without_response_advertised_wins() -> None:
    """'write-without-response' in the property set -> WITHOUT response
    (app parity — the app's only WriteType, research 010 §2.3), with or
    without 'write' alongside, and no WARNING.
    """

    for props in (["write-without-response", "write"], ["write-without-response"]):
        mode, logs = _set_mode(props)
        assert mode is False, (props, mode)
        assert not [m for m in logs if "WARNING" in m or "app parity" in m], (props, logs)
        assert not [m for m in logs if "forbids" in m], (props, logs)


def test_write_only_switches_with_response_and_warns() -> None:
    """Only 'write' advertised -> WITH response (the ATT constraint: a
    write-only characteristic cannot take Write Commands) + the exact
    WARNING (FR-3).
    """

    mode, logs = _set_mode(["write"])
    assert mode is True, mode
    warns = [m for m in logs if "forbids write-without-response" in m]
    assert warns, logs
    assert (
        f"write characteristic {FF03} forbids write-without-response "
        f"(properties=['write']); using write-with-response" == warns[0]
    ), warns


def test_no_write_property_writes_anyway_and_warns() -> None:
    """No write property at all -> WITHOUT response anyway (app parity:
    the app never reads properties before writing, research 010 §1) +
    the exact WARNING. The pre-010 hard error is gone — FR-3 supersedes
    spec 006 AC-2's refusal row.
    """

    for props in ([], ["read"], ["notify"]):
        mode, logs = _set_mode(props)
        assert mode is False, (props, mode)
        warns = [m for m in logs if "app parity: writing anyway" in m]
        assert warns, (props, logs)
        assert (
            f"write characteristic {FF03} advertises no write property "
            f"(properties={list(props)}) — app parity: writing anyway" == warns[0]
        ), (props, warns)


def test_mode_never_refuses() -> None:
    """FR-2 item 2: properties are advisory — the setter NEVER raises,
    whatever the property set says (the app never re-picks on property
    grounds; a GATT refusal belongs to the write itself, on the wire).
    """

    for props in ([], ["read"], ["notify"], ["indicate"], ["broadcast"]):
        client = aqua_client.AquaTempBleClient(ADDRESS)
        # Must not raise for ANY property set.
        client._set_write_mode_from_properties(FF03, list(props))
        assert client._write_with_response is False, (props,)


def main() -> int:
    tests = [
        test_without_response_advertised_wins,
        test_write_only_switches_with_response_and_warns,
        test_no_write_property_writes_anyway_and_warns,
        test_mode_never_refuses,
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
    print(f"PASSED: {len(tests)}/{len(tests)} write mode tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())