#!/usr/bin/env python3
"""Adaptive GATT discovery tests for AquaTempBleClient.

Production evidence (mission context): the user's unit establishes the
GATT link in ~2.9 s but does NOT expose ff01 — the hard-coded lookup
died with "write characteristic 0000ff01-... not found on the device".
The real table of the PAC is unknown, so these tests pin the adaptive
discovery contract:

- AC-1 (enumeration): every successful connect walks the device's
  services and logs the full GATT table (service + characteristic
  UUIDs, handles, properties) at INFO via "GATT enumeration" lines —
  one connect must be enough to see the real layout.
- AC-2 (write fallback): ff01 stays the FIRST choice; when absent the
  resolution falls back to (b) a writable characteristic of a
  vendor-specific service (preferring services that also notify),
  (c) ff03 as a last-resort candidate, (d) an explicit
  AquaTempConnectionError whose MESSAGE lists the real table.
- AC-3 (notify fallback): ff02 stays the FIRST choice; when absent the
  client subscribes to a notify/indicate characteristic of the same
  service as the write candidate, else of a vendor service, else the
  same explicit listing error — never from a standard SIG service.
- AC-4 (alternative logging): whenever an ALTERNATIVE uuid is used, ONE
  INFO line "using write/notify characteristic <uuid> (properties=...)"
  names the choice.
- AC-5 (regression): units that expose ff01/ff02 behave exactly as
  before — same uuids on the wire, same vendor session order (spec 006
  FR-1), same write-mode property gate (spec 006 FR-2), and no
  "using ..." announcement.

Red-first: on the pre-discovery code every test below fails — the write
fallback dies with the hard "not found" error, no enumeration lines
exist, no alternative is ever chosen, and error messages carry no GATT
table.

Only the outer I/O seams (_resolve_device, _load_retry_connector,
_connect_with_raw_client) are patched, so connect() runs its REAL body.
asyncio.sleep is recorded (never waited) so the tests cost no
wall-clock time.

Run standalone:

    python3 tests/test_gatt_discovery.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import asyncio
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
    # Inject a minimal fake bleak: the transport seam is overridden in the
    # tests, so the classes are never actually used.
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
import modbus  # noqa: E402

ADDRESS = "08:06:A6:C0:94:1C"  # the production unit's address
DEVICE_NAME = "BlueNRG-C0941C"

# Known UUIDs (first choice, FINDINGS.md §3, §11.3)
FF01 = const.UUID_WRITE
FF02 = const.UUID_NOTIFY

# Candidate UUIDs for the adaptive discovery
FF03 = "0000ff03-0000-1000-8000-00805f9b34fb"      # handle 2215, role unknown
FF00 = "0000ff00-0000-1000-8000-00805f9b34fb"      # plausible vendor service
GAP = "00001800-0000-1000-8000-00805f9b34fb"       # standard service
DEV_NAME = "00002a00-0000-1000-8000-00805f9b34fb"  # GAP device name char
BATTERY = "0000180f-0000-1000-8000-00805f9b34fb"   # standard service
BATT_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
CUSTOM_SVC = "a5e80001-3c3c-4b4b-9d9d-1a2b3c4d5e6f"  # full 128-bit vendor
CUSTOM_WRITE = "a5e80002-3c3c-4b4b-9d9d-1a2b3c4d5e6f"
CUSTOM_NOTIFY = "a5e80003-3c3c-4b4b-9d9d-1a2b3c4d5e6f"

SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: -9990, 28: -125, 47: 0}


def _build_response(values: dict[int, int], start: int = 7, count: int = 45) -> bytes:
    """Synthetic 95-byte FC03 response."""
    payload = bytearray()
    for addr in range(start, start + count):
        payload += (values.get(addr, 0) & 0xFFFF).to_bytes(2, "big")
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, len(payload)]) + bytes(payload)
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


# ---------------------------------------------------------------------------
# Fakes: a REALISTIC GATT table (iterable services, get_characteristic that
# returns None for absent UUIDs) — the whole point of the mission.
# ---------------------------------------------------------------------------


class FakeBLEDevice:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class FakeChar:
    """GATT characteristic stand-in: uuid, handle, scriptable properties."""

    def __init__(self, uuid, handle, props):
        self.uuid = uuid
        self.handle = handle
        self.properties = list(props)


class FakeService:
    """GATT service stand-in; characteristics exposed dict-like (bleak
    >= 0.19 style, keyed by handle) or as a plain list."""

    def __init__(self, uuid, handle, chars, chars_style="dict"):
        self.uuid = uuid
        self.handle = handle
        self.chars = list(chars)
        if chars_style == "dict":
            self.characteristics = {c.handle: c for c in self.chars}
        else:
            self.characteristics = list(self.chars)


def _svc(uuid, handle, *chars, chars_style="dict"):
    return FakeService(uuid, handle, chars, chars_style)


class FakeServices:
    """Iterable services collection with a faithful get_characteristic:
    it returns None when the characteristic is absent (the production
    behaviour the old code hit on the user's unit)."""

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


class RecordingBleakClient:
    """Connected bleak stand-in over the realistic services collection.

    start_notify records the requested uuid; write_gatt_char records
    (uuid, data, response) and replays the scripted response chunks
    through the stored notify handler — connect() runs its real body,
    then read_registers answers like a PAC."""

    is_connected = True

    def __init__(self, services, response=None):
        self.services = services
        self.events: list = []
        self.notified_uuids: list = []
        self.writes: list = []
        self._response = response
        self._handler = None

    async def start_notify(self, uuid, handler):
        self.events.append("start_notify")
        self.notified_uuids.append(uuid)
        self._handler = handler

    async def write_gatt_char(self, uuid, data, response=True):
        self.events.append("write")
        self.writes.append((str(uuid), bytes(data), response))
        if self._handler is not None and self._response is not None:
            self._handler(bytearray(self._response))

    async def disconnect(self):
        self.events.append("disconnect")


class DiscoveryClient(aqua_client.AquaTempBleClient):
    """AquaTempBleClient over the realistic fakes.

    Only the outer I/O seams are patched; the connect() BODY — GATT
    enumeration, adaptive notify/write resolution, start_notify, the
    settle sleep — is the shipped code under test.
    """

    def __init__(self, bleak_client: RecordingBleakClient):
        super().__init__(ADDRESS)
        self._fake = bleak_client

    async def _resolve_device(self):
        return FakeBLEDevice(ADDRESS, DEVICE_NAME)

    def _load_retry_connector(self):
        return None  # force the standalone raw-client path

    async def _connect_with_raw_client(self, _device):
        self._fake.events.append("connect")
        return self._fake


# ------------------------------------------------------------- test helpers


@contextlib.contextmanager
def _record_sleeps(events: list):
    """Patch asyncio.sleep to record the delay instead of waiting."""
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *_args, **_kwargs):
        events.append(("sleep", delay))

    asyncio.sleep = recording_sleep
    try:
        yield
    finally:
        asyncio.sleep = real_sleep


class _LogRecorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _capture():
    """Capture every aqua_client log line at DEBUG (INFO included)."""
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


def classic_unit(response=None):
    """The documented unit: ff01/ff02 present (handles FINDINGS §11.3)."""
    services = FakeServices(
        [
            _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read"])),
            _svc(
                FF00,
                2212,
                FakeChar(FF01, 2214, ["write", "write-without-response"]),
                FakeChar(FF03, 2215, ["read"]),
                FakeChar(FF02, 2216, ["notify"]),
            ),
        ]
    )
    return RecordingBleakClient(services, response)


# --------------------------------------------------------------------- tests
def test_enumeration_logs_full_gatt_table() -> None:
    """AC-1: one connect reveals the whole real table at INFO.

    Red-first: the pre-discovery code logs no GATT enumeration line.
    """

    async def run() -> None:
        events: list = []
        fake = classic_unit()
        client = DiscoveryClient(fake)
        with _record_sleeps(events), _capture() as logs:
            await client.connect()
        gatt = [m for m in logs if m.startswith("GATT enumeration")]
        assert gatt, "no GATT enumeration lines were logged"
        # Summary first: how much was walked.
        assert gatt[0] == (
            f"GATT enumeration {ADDRESS}: 2 service(s), 4 characteristic(s)"
        ), gatt[0]
        # One line per service with its handle and characteristic count.
        assert (
            f"GATT enumeration {ADDRESS}: service {GAP} (handle 1): 1 char(s)" in gatt
        ), gatt
        assert (
            f"GATT enumeration {ADDRESS}: service {FF00} (handle 2212): 3 char(s)" in gatt
        ), gatt
        # One line per characteristic: UUID, handle, properties.
        assert (
            f"GATT enumeration {ADDRESS}:   char {FF01} (handle 2214) "
            "properties ['write', 'write-without-response']" in gatt
        ), gatt
        assert (
            f"GATT enumeration {ADDRESS}:   char {FF03} (handle 2215) properties ['read']"
            in gatt
        ), gatt
        assert (
            f"GATT enumeration {ADDRESS}:   char {FF02} (handle 2216) properties ['notify']"
            in gatt
        ), gatt
        # Regression: the spec-007 summary and the parity line still appear.
        assert any(m.startswith("connect summary") for m in logs), logs
        assert f"Connected to {ADDRESS} ({DEVICE_NAME})" in logs, logs

    asyncio.run(run())


def test_known_uuids_still_win() -> None:
    """AC-5 regression: ff01/ff02 units keep the exact historic behaviour.

    Red-first: _write_uuid/_notify_uuid do not exist on the
    pre-discovery code.
    """

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        fake = classic_unit(resp)
        client = DiscoveryClient(fake)
        # Sleeps are recorded into the FAKE's event list so the session
        # order (transport calls interleaved with the settle) is one
        # observable timeline — same technique as test_connect_parity.
        with _record_sleeps(fake.events), _capture() as logs:
            await client.connect()
            regs = await client.read_registers(7, 45)
        assert regs[26] == 243, regs  # the scripted session answered
        assert client._write_uuid == FF01
        assert client._notify_uuid == FF02
        assert fake.notified_uuids == [FF02], fake.notified_uuids
        assert all(uuid == FF01 for uuid, _f, _r in fake.writes), fake.writes
        # Property gate (spec 006 FR-2): without-response preferred.
        assert all(r is False for _u, _f, r in fake.writes), fake.writes
        # Vendor session order (spec 006 FR-1) is untouched.
        assert fake.events == [
            "connect",
            "start_notify",
            ("sleep", const.SETTLE_AFTER_SUBSCRIBE),
            "write",
        ], fake.events
        # Known UUIDs are never announced as alternatives.
        using = [m for m in logs if m.startswith("using ")]
        assert not using, using

    asyncio.run(run())


def test_write_fallback_finds_writable_char_when_ff01_absent() -> None:
    """AC-2(b)/AC-3/AC-4: no ff01 and no ff02 — a full 128-bit vendor
    service exposes write + notify. connect() must USE them end to end
    and announce both choices at INFO.

    Red-first: the pre-discovery code dies with "write characteristic
    ... not found on the device".
    """

    async def run() -> None:
        events: list = []
        resp = _build_response(SYNTHETIC_VALUES)
        services = FakeServices(
            [
                _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read"])),
                _svc(
                    CUSTOM_SVC,
                    64,
                    FakeChar(CUSTOM_WRITE, 65, ["write"]),
                    FakeChar(CUSTOM_NOTIFY, 66, ["notify"]),
                ),
            ]
        )
        fake = RecordingBleakClient(services, resp)
        client = DiscoveryClient(fake)
        with _record_sleeps(events), _capture() as logs:
            await client.connect()
            regs = await client.read_registers(7, 45)
        assert regs[26] == 243, regs  # the full session works end to end
        assert client._write_uuid == CUSTOM_WRITE
        assert client._notify_uuid == CUSTOM_NOTIFY
        assert fake.notified_uuids == [CUSTOM_NOTIFY], fake.notified_uuids
        assert all(
            uuid == CUSTOM_WRITE for uuid, _f, _r in fake.writes
        ), fake.writes
        assert (
            f"using write characteristic {CUSTOM_WRITE} (properties=['write']) "
            f"on {ADDRESS}" in logs
        ), logs
        assert (
            f"using notify characteristic {CUSTOM_NOTIFY} (properties=['notify']) "
            f"on {ADDRESS}" in logs
        ), logs
        # Each alternative is announced exactly once.
        assert (
            len([m for m in logs if m.startswith("using write characteristic")]) == 1
        ), logs
        assert (
            len([m for m in logs if m.startswith("using notify characteristic")]) == 1
        ), logs

    asyncio.run(run())


def test_write_fallback_uses_ff03_when_writable() -> None:
    """AC-2(b): the documented sibling ff03 (handle 2215, role unknown,
    FINDINGS §11.3) advertises write — it must be chosen when ff01 is
    absent, while ff02 keeps the notify role.

    Red-first: the pre-discovery code dies "not found".
    """

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        services = FakeServices(
            [
                _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read"])),
                _svc(
                    FF00,
                    2212,
                    FakeChar(FF03, 2215, ["write"]),
                    FakeChar(FF02, 2216, ["notify"]),
                ),
            ]
        )
        fake = RecordingBleakClient(services, resp)
        client = DiscoveryClient(fake)
        with _record_sleeps([]), _capture() as logs:
            await client.connect()
        assert client._write_uuid == FF03
        assert client._notify_uuid == FF02  # known notify uuid still first
        assert fake.notified_uuids == [FF02], fake.notified_uuids
        assert (
            f"using write characteristic {FF03} (properties=['write']) "
            f"on {ADDRESS}" in logs
        ), logs

    asyncio.run(run())


def test_ff03_last_resort_without_write_property() -> None:
    """AC-2(c): ff03 exists but advertises NO write property — the
    last-resort candidate is still tried (its role was never confirmed),
    with a WARNING naming the missing property and the vendor-parity
    write mode (without response).

    Red-first: the pre-discovery code dies "not found".
    """

    async def run() -> None:
        services = FakeServices(
            [
                _svc(
                    FF00,
                    2212,
                    FakeChar(FF03, 2215, ["read"]),
                    FakeChar(FF02, 2216, ["notify"]),
                ),
            ]
        )
        fake = RecordingBleakClient(services)
        client = DiscoveryClient(fake)
        with _record_sleeps([]), _capture() as logs:
            await client.connect()
        assert client._write_uuid == FF03
        # No property advertises a mode -> vendor parity: without response.
        assert client._write_with_response is False
        warns = [m for m in logs if "does not advertise a write property" in m]
        assert warns and FF03 in warns[0], logs
        assert (
            f"using write characteristic {FF03} (properties=['read']) "
            f"on {ADDRESS}" in logs
        ), logs

    asyncio.run(run())


def test_no_writable_characteristic_error_lists_gatt_table() -> None:
    """AC-2(d) / mission part 2: NOTHING writable anywhere — the error
    MESSAGE must list the real services and characteristics (UUID +
    properties) so the next iteration can hard-code the right UUID
    without a new probe round.

    Red-first: the pre-discovery message is a bare "not found" with no
    table.
    """

    async def run() -> None:
        services = FakeServices(
            [
                _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read"])),
                _svc(BATTERY, 10, FakeChar(BATT_LEVEL, 11, ["read", "notify"])),
                _svc(FF00, 2212, FakeChar(FF02, 2216, ["notify"])),
            ]
        )
        fake = RecordingBleakClient(services)
        client = DiscoveryClient(fake)
        raised = None
        with _record_sleeps([]), _capture() as logs:
            try:
                await client.connect()
            except aqua_client.AquaTempConnectionError as exc:
                raised = exc
        assert raised is not None, "connect() must raise AquaTempConnectionError"
        msg = str(raised)
        assert "no usable write characteristic" in msg, msg
        # The known UUID's absence is named...
        assert FF01 in msg, msg
        # ...and the message lists what the device REALLY exposes.
        assert DEV_NAME in msg and "['read']" in msg, msg
        assert BATT_LEVEL in msg and "['read', 'notify']" in msg, msg
        assert FF02 in msg and "['notify']" in msg, msg
        assert msg.count("service ") == 3, msg
        # Teardown happened: the half-open link is closed.
        assert "disconnect" in fake.events, fake.events
        # The failure is logged at stage write-char (spec 007 §3.2).
        joined = "\n".join(logs)
        assert (
            f"post-connect setup (ff02 notify / ff01 lookup) on {ADDRESS} "
            f"failed at stage write-char" in joined
        ), logs

    asyncio.run(run())


def test_no_notify_characteristic_error_lists_gatt_table() -> None:
    """AC-3 error twin: no notify/indicate anywhere — explicit error
    BEFORE any subscription, listing the real table (the write
    characteristic exists but nothing can deliver responses)."""

    async def run() -> None:
        services = FakeServices(
            [
                _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read"])),
                _svc(FF00, 2212, FakeChar(FF01, 2214, ["write", "write-without-response"])),
            ]
        )
        fake = RecordingBleakClient(services)
        client = DiscoveryClient(fake)
        raised = None
        with _record_sleeps([]), _capture() as logs:
            try:
                await client.connect()
            except aqua_client.AquaTempConnectionError as exc:
                raised = exc
        assert raised is not None, "connect() must raise AquaTempConnectionError"
        msg = str(raised)
        assert "no notify-capable characteristic" in msg, msg
        assert FF02 in msg, msg  # known uuid absence named
        assert FF01 in msg and "['write', 'write-without-response']" in msg, msg
        # The failure happened BEFORE any subscription.
        assert fake.notified_uuids == [], fake.notified_uuids
        assert "start_notify" not in fake.events, fake.events
        assert "disconnect" in fake.events, fake.events
        # Stage notify (spec 007 §3.2): nothing was subscribed yet.
        joined = "\n".join(logs)
        assert (
            f"post-connect setup (ff02 notify / ff01 lookup) on {ADDRESS} "
            f"failed at stage notify" in joined
        ), logs

    asyncio.run(run())


def test_fallback_write_mode_is_property_gated() -> None:
    """AC-2: the write-mode property gate (spec 006 FR-2) applies to
    fallback candidates too — write-without-response writes without
    response, write writes with response."""

    async def run() -> None:
        for props, expected in ((["write-without-response"], False), (["write"], True)):
            services = FakeServices(
                [
                    _svc(
                        CUSTOM_SVC,
                        64,
                        FakeChar(CUSTOM_WRITE, 65, props),
                        FakeChar(CUSTOM_NOTIFY, 66, ["notify"]),
                    ),
                ]
            )
            fake = RecordingBleakClient(services)
            client = DiscoveryClient(fake)
            with _record_sleeps([]):
                await client.connect()
            assert client._write_uuid == CUSTOM_WRITE
            assert client._write_with_response is expected, (props, expected)

    asyncio.run(run())


def test_standard_service_writable_char_not_chosen() -> None:
    """AC-2(b): the fallback never grabs writable characteristics from
    standard SIG services (GAP/GATT/battery...) — vendor services only,
    mission step (b). A writable standard char is not a candidate; the
    explicit error wins and the listing shows what exists."""

    async def run() -> None:
        services = FakeServices(
            [
                _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read", "write"])),
                _svc(FF00, 2212, FakeChar(FF02, 2216, ["notify"])),
            ]
        )
        fake = RecordingBleakClient(services)
        client = DiscoveryClient(fake)
        raised = None
        with _record_sleeps([]):
            try:
                await client.connect()
            except aqua_client.AquaTempConnectionError as exc:
                raised = exc
        assert raised is not None, "connect() must raise AquaTempConnectionError"
        msg = str(raised)
        assert "no usable write characteristic" in msg, msg
        # The standard-service writable char is listed but NOT used.
        assert client._write_uuid is None

    asyncio.run(run())


def test_enumeration_survives_list_characteristics() -> None:
    """Robustness: services exposing characteristics as a plain LIST
    (not bleak's handle-keyed collection) are still fully enumerated."""

    async def run() -> None:
        services = FakeServices(
            [
                _svc(
                    FF00,
                    2212,
                    FakeChar(FF01, 2214, ["write-without-response"]),
                    FakeChar(FF02, 2216, ["notify"]),
                    chars_style="list",
                ),
            ]
        )
        fake = RecordingBleakClient(services)
        client = DiscoveryClient(fake)
        with _record_sleeps([]), _capture() as logs:
            await client.connect()
        gatt = [m for m in logs if m.startswith("GATT enumeration")]
        assert gatt, "no GATT enumeration lines were logged"
        assert gatt[0] == (
            f"GATT enumeration {ADDRESS}: 1 service(s), 2 characteristic(s)"
        ), gatt[0]
        assert client._write_uuid == FF01
        assert client._notify_uuid == FF02

    asyncio.run(run())


def main() -> int:
    tests = [
        test_enumeration_logs_full_gatt_table,
        test_known_uuids_still_win,
        test_write_fallback_finds_writable_char_when_ff01_absent,
        test_write_fallback_uses_ff03_when_writable,
        test_ff03_last_resort_without_write_property,
        test_no_writable_characteristic_error_lists_gatt_table,
        test_no_notify_characteristic_error_lists_gatt_table,
        test_fallback_write_mode_is_property_gated,
        test_standard_service_writable_char_not_chosen,
        test_enumeration_survives_list_characteristics,
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
    print(f"PASSED: {len(tests)}/{len(tests)} GATT discovery tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())