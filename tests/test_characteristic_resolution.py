#!/usr/bin/env python3
"""App-parity characteristic resolution tests (spec 010, AC-2/AC-3).

The vendor app's disassembled discovery classifier (research 010 §1,
closure 0xa146dc) stores the session characteristics BY UUID BUCKET,
property-blind:

- notify bucket {ff02, 2216, 2a00} -> subscribed (state field 0x2b);
- write bucket  {ff03, 2215, 2aa6} -> every frame written there
  (state field 0x2f), always without response (research 010 §2.3).

These tests pin the resolver's truth table (contract
resolve-characteristics.md, plan 010 §4) with fake GATT tables — no
I/O, no connect(): the resolvers run over the real enumeration +
resolution code path only:

- T1: the owner unit (service ff01 hosting ff02+ff03) resolves
  notify=ff02 / write=ff03, with the census write line (AC-4) and no
  advisories;
- T2: ff03 stripped of every write property is STILL the write char,
  written without response + the app-parity WARNING;
- T3: ff03 advertising only 'write' is written WITH response (ATT
  constraint) + WARNING;
- T4: the gen-2 layout (service 2214 hosting 2216+2215) resolves
  2216/2215 — both announced (neither is the classic ff02);
- T5: the GAP-only fallback (1800 hosting 2a00+2aa6) resolves
  2a00/2aa6 — the app's own bucket vocabulary — with BOTH advisories;
- T6: an unknown vendor layout falls back to the unchanged adaptive
  scan (the c8d8260 safety net) with the INFO fallback line, while a
  notify-less table keeps the loud notify error;
- T7: a standard-only table (1801/2a05) resolves nothing — both loud
  errors, message listing the real table;
- T8: bucket order is first-wins (ff03 beats a 2215 living elsewhere);
  and a writable characteristic NAMED ff01 never wins over the
  bucket — the historic ff01-first preference is the misreading this
  spec fixes (research 010 §1.3).

Red-first: on the pre-010 code the bucket constants do not exist
(AttributeError at import) and the write resolution prefers ff01 —
every expectation below fails.

Run standalone:

    python3 tests/test_characteristic_resolution.py

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
    # Inject a minimal fake bleak: the resolvers never construct the
    # real classes, they only walk the fake services object.
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

ADDRESS = "08:06:A6:C0:94:1C"  # the production unit's address

# The app's bucket heads (spec 010 FR-1, research 010 §1).
FF02 = const.UUID_NOTIFY
FF03 = const.UUID_WRITE

# Full bucket vocabulary (research 010 §1.1/§1.3).
SVC_2214 = "00002214-0000-1000-8000-00805f9b34fb"  # gen-2 vendor service
C2216 = "00002216-0000-1000-8000-00805f9b34fb"    # gen-2 notify char
C2215 = "00002215-0000-1000-8000-00805f9b34fb"    # gen-2 write char
GAP_SVC = "00001800-0000-1000-8000-00805f9b34fb"  # standard GAP service
C2A00 = "00002a00-0000-1000-8000-00805f9b34fb"    # GAP device name
C2AA6 = "00002aa6-0000-1000-8000-00805f9b34fb"    # central addr resolution
SVC_1801 = "00001801-0000-1000-8000-00805f9b34fb"  # standard GATT service
C2A05 = "00002a05-0000-1000-8000-00805f9b34fb"    # service changed (indicate)
FF01_SVC = "0000ff01-0000-1000-8000-00805f9b34fb"  # the owner unit's SERVICE
FF01_CHAR = "0000ff01-0000-1000-8000-00805f9b34fb"  # a characteristic NAMED ff01
DEV_NAME = "00002a00-0000-1000-8000-00805f9b34fb"

# An unknown vendor layout (no bucket UUID anywhere) for T6.
FF9E_SVC = "0000ff9e-0000-1000-8000-00805f9b34fb"
FF9F_WRITE = "0000ff9f-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# Fakes: a realistic GATT table (iterable services, faithful
# get_characteristic) — the same stand-in style as test_gatt_discovery.
# ---------------------------------------------------------------------------


class FakeChar:
    def __init__(self, uuid, handle, props):
        self.uuid = uuid
        self.handle = handle
        self.properties = list(props)


class FakeService:
    def __init__(self, uuid, handle, chars):
        self.uuid = uuid
        self.handle = handle
        self.chars = list(chars)
        self.characteristics = {c.handle: c for c in self.chars}


class FakeServices:
    def __init__(self, services):
        self._services = list(services)

    def __iter__(self):
        return iter(self._services)

    def get_characteristic(self, uuid):
        wanted = str(uuid).lower()
        for service in self._services:
            for char in service.chars:
                if str(char.uuid).lower() == wanted:
                    return char
        return None


class FakeBleakClient:
    """Connected bleak stand-in: .services only — the resolvers and the
    enumerator never touch anything else."""

    is_connected = True

    def __init__(self, services):
        self.services = services


def _svc(uuid, handle, *chars):
    return FakeService(uuid, handle, chars)


def _char(uuid, handle, props):
    return FakeChar(uuid, handle, props)


class _LogRecorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _capture():
    """Capture every aqua_client log line at DEBUG (INFO+WARNING incl.)."""
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


def _client_over(*services):
    """AquaTempBleClient with the REAL enumeration run over a fake table
    (the same order connect() uses), returning (client, fake_client)."""
    fake = FakeBleakClient(FakeServices(list(services)))
    client = aqua_client.AquaTempBleClient(ADDRESS)
    client._gatt_chars = client._enumerate_gatt(fake)
    return client, fake


def _resolve_both(client, fake):
    """Run both resolvers (notify first, then write — the connect()
    order) under log capture; returns the captured messages."""
    with _capture() as logs:
        client._resolve_notify_characteristic(fake)
        client._resolve_write_characteristic(fake)
    return logs


# --------------------------------------------------------------------- tests
def test_t1_owner_unit_resolves_ff02_ff03() -> None:
    """T1: service ff01 hosting ff02 (notify) + ff03 (write,
    write-without-response) — the production layout (research 010 §0).
    notify=ff02 (silent), write=ff03 without response, the census write
    line byte-identical (AC-4), zero advisories.
    """

    client, fake = _client_over(
        _svc(
            FF01_SVC,
            10,
            _char(FF02, 2216, ["notify"]),
            _char(FF03, 2215, ["write", "write-without-response"]),
        )
    )
    logs = _resolve_both(client, fake)
    assert client._notify_uuid == FF02, client._notify_uuid
    assert client._write_uuid == FF03, client._write_uuid
    assert client._write_with_response is False, client._write_with_response
    # AC-4: the census line format — the E4' grep target, byte-identical
    # to the ce41aa5 production line.
    assert (
        f"using write characteristic {FF03} "
        f"(properties=['write', 'write-without-response']) on {ADDRESS}" in logs
    ), logs
    # The classic ff02 pick is never announced (historic behaviour kept).
    assert not [m for m in logs if m.startswith("using notify characteristic")], logs
    # No advisories: every property is present.
    assert not [m for m in logs if "app parity" in m], logs


def test_t2_ff03_without_write_property_written_anyway() -> None:
    """T2: ff03 stripped of EVERY write property is still the write
    char — property-blind, like the app — written WITHOUT response with
    the app-parity WARNING (FR-2 item 2 / FR-3).
    """

    client, fake = _client_over(
        _svc(
            FF01_SVC,
            10,
            _char(FF02, 2216, ["notify"]),
            _char(FF03, 2215, ["read"]),
        )
    )
    logs = _resolve_both(client, fake)
    assert client._notify_uuid == FF02, client._notify_uuid
    assert client._write_uuid == FF03, client._write_uuid
    assert client._write_with_response is False, client._write_with_response
    warns = [m for m in logs if "app parity: writing anyway" in m]
    assert warns, logs
    assert (
        f"write characteristic {FF03} advertises no write property "
        f"(properties=['read']) — app parity: writing anyway" == warns[0]
    ), warns
    # The choice is still announced with the census format.
    assert (
        f"using write characteristic {FF03} (properties=['read']) "
        f"on {ADDRESS}" in logs
    ), logs


def test_t3_ff03_write_only_switches_with_response() -> None:
    """T3: ff03 advertising only 'write' forbids Write Commands at the
    ATT layer — the one legal divergence: write WITH response + WARNING
    (FR-3). The bucket pick itself is unchanged (never re-picked).
    """

    client, fake = _client_over(
        _svc(
            FF01_SVC,
            10,
            _char(FF02, 2216, ["notify"]),
            _char(FF03, 2215, ["write"]),
        )
    )
    logs = _resolve_both(client, fake)
    assert client._write_uuid == FF03, client._write_uuid
    assert client._write_with_response is True, client._write_with_response
    warns = [m for m in logs if "forbids write-without-response" in m]
    assert warns, logs
    assert (
        f"write characteristic {FF03} forbids write-without-response "
        f"(properties=['write']); using write-with-response" == warns[0]
    ), warns


def test_t4_gen2_layout_resolves_2216_2215() -> None:
    """T4: the gen-2 vendor layout (service 2214 hosting 2216+2215,
    research 010 §1.3) resolves through the SAME buckets: notify=2216,
    write=2215 — both announced at INFO (neither is the classic ff02).
    """

    client, fake = _client_over(
        _svc(
            SVC_2214,
            10,
            _char(C2216, 2216, ["notify"]),
            _char(C2215, 2215, ["write-without-response"]),
        )
    )
    logs = _resolve_both(client, fake)
    assert client._notify_uuid == C2216, client._notify_uuid
    assert client._write_uuid == C2215, client._write_uuid
    assert client._write_with_response is False, client._write_with_response
    assert (
        f"using notify characteristic {C2216} (properties=['notify']) "
        f"on {ADDRESS}" in logs
    ), logs
    assert (
        f"using write characteristic {C2215} "
        f"(properties=['write-without-response']) on {ADDRESS}" in logs
    ), logs
    assert not [m for m in logs if "app parity" in m], logs


def test_t5_gap_only_layout_resolves_2a00_2aa6() -> None:
    """T5: a GAP-only table (1800 hosting 2a00+2aa6) resolves through
    the buckets too — the app classifies 2a00/2aa6 the same way
    (research 010 §1.3, last-wins in its loop; first-wins in ours).
    Both advisories fire: neither char advertises the role property,
    and the app — property-blind — would use them anyway.
    """

    client, fake = _client_over(
        _svc(
            GAP_SVC,
            1,
            _char(C2A00, 2, ["read"]),
            _char(C2AA6, 3, ["read"]),
        )
    )
    logs = _resolve_both(client, fake)
    assert client._notify_uuid == C2A00, client._notify_uuid
    assert client._write_uuid == C2AA6, client._write_uuid
    assert client._write_with_response is False, client._write_with_response
    assert (
        f"notify characteristic {C2A00} advertises no notify property "
        f"(properties=['read']) — app parity: subscribing anyway" in logs
    ), logs
    assert (
        f"write characteristic {C2AA6} advertises no write property "
        f"(properties=['read']) — app parity: writing anyway" in logs
    ), logs
    # Both non-classic picks are announced.
    assert (
        f"using notify characteristic {C2A00} (properties=['read']) "
        f"on {ADDRESS}" in logs
    ), logs
    assert (
        f"using write characteristic {C2AA6} (properties=['read']) "
        f"on {ADDRESS}" in logs
    ), logs


def test_t6_unknown_vendor_falls_back_to_adaptive_scan() -> None:
    """T6: no bucket UUID anywhere — the unchanged adaptive scan (the
    c8d8260 safety net) picks the vendor writable char with the INFO
    fallback line; a notify-less table keeps the loud notify error with
    the table in the message.
    """

    client, fake = _client_over(
        _svc(FF9E_SVC, 80, _char(FF9F_WRITE, 81, ["write-without-response"]))
    )
    raised = None
    with _capture():
        try:
            client._resolve_notify_characteristic(fake)
        except aqua_client.AquaTempConnectionError as exc:
            raised = exc
    assert raised is not None, "a notify-less table must raise"
    assert "no notify-capable characteristic" in str(raised), str(raised)
    assert FF02 in str(raised), str(raised)  # known UUID absence named
    assert FF9F_WRITE in str(raised), str(raised)  # the table is listed
    with _capture() as logs:
        client._resolve_write_characteristic(fake)
    assert client._write_uuid == FF9F_WRITE, client._write_uuid
    assert client._write_with_response is False, client._write_with_response
    assert (
        f"using write characteristic {FF9F_WRITE} "
        f"(properties=['write-without-response']) on {ADDRESS}" in logs
    ), logs


def test_t7_standard_only_table_fails_loudly() -> None:
    """T7: a standard-only table (1801/2a05-indicate) resolves nothing —
    both loud errors, unchanged messages. The indicate char lives in a
    STANDARD service: never a notify candidate.
    """

    client, fake = _client_over(_svc(SVC_1801, 1, _char(C2A05, 2, ["indicate"])))
    raised_notify = None
    raised_write = None
    with _capture():
        try:
            client._resolve_notify_characteristic(fake)
        except aqua_client.AquaTempConnectionError as exc:
            raised_notify = exc
        try:
            client._resolve_write_characteristic(fake)
        except aqua_client.AquaTempConnectionError as exc:
            raised_write = exc
    assert raised_notify is not None, "notify resolution must raise"
    assert "no notify-capable characteristic" in str(raised_notify), str(
        raised_notify
    )
    assert raised_write is not None, "write resolution must raise"
    assert "no usable write characteristic" in str(raised_write), str(raised_write)
    # The known (bucket-head) UUID absence is named in the message.
    assert FF03 in str(raised_write), str(raised_write)
    assert C2A05 in str(raised_write), str(raised_write)  # table listed


def test_t8_bucket_order_first_wins() -> None:
    """T8: when two bucket UUIDs exist in different services, the FIRST
    bucket UUID wins (ff03 over a 2215 living in a gen-2 service) —
    our tuple walk is first-wins by design (contract T8 note).
    """

    client, fake = _client_over(
        _svc(
            FF01_SVC,
            10,
            _char(FF02, 2216, ["notify"]),
            _char(FF03, 2215, ["write", "write-without-response"]),
        ),
        _svc(SVC_2214, 20, _char(C2215, 21, ["write", "write-without-response"])),
    )
    logs = _resolve_both(client, fake)
    assert client._notify_uuid == FF02, client._notify_uuid
    assert client._write_uuid == FF03, client._write_uuid
    assert C2215 not in (client._write_uuid, client._notify_uuid)


def test_ff01_characteristic_never_wins_over_bucket() -> None:
    """A writable characteristic NAMED ff01 loses to the bucket's ff03:
    the app never selects a characteristic ff01 (research 010 §1.3) —
    the historic ff01-first preference is the misreading this spec
    fixes. On this layout the old code picked ff01; the new code picks
    ff03 even though ff03 is read-only and ff01 is writable.
    """

    client, fake = _client_over(
        _svc(
            FF01_SVC,
            10,
            _char(FF01_CHAR, 2214, ["write", "write-without-response"]),
            _char(FF02, 2216, ["notify"]),
            _char(FF03, 2215, ["read"]),
        )
    )
    logs = _resolve_both(client, fake)
    assert client._write_uuid == FF03, client._write_uuid
    assert client._write_with_response is False, client._write_with_response
    # ff03 without write property -> the app-parity WARNING fires.
    assert [m for m in logs if "app parity: writing anyway" in m], logs
    # ff01 is never announced as the write char.
    assert not [
        m for m in logs if m.startswith("using write characteristic") and FF01_CHAR in m
    ], logs


def main() -> int:
    tests = [
        test_t1_owner_unit_resolves_ff02_ff03,
        test_t2_ff03_without_write_property_written_anyway,
        test_t3_ff03_write_only_switches_with_response,
        test_t4_gen2_layout_resolves_2216_2215,
        test_t5_gap_only_layout_resolves_2a00_2aa6,
        test_t6_unknown_vendor_falls_back_to_adaptive_scan,
        test_t7_standard_only_table_fails_loudly,
        test_t8_bucket_order_first_wins,
        test_ff01_characteristic_never_wins_over_bucket,
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
    print(f"PASSED: {len(tests)}/{len(tests)} characteristic resolution tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())