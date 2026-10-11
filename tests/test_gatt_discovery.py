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
- AC-2 (write resolution): the app's write bucket {ff03, 2215,
  2aa6} (research 010 §1, spec 010 FR-2) resolves FIRST,
  property-blind; when no bucket UUID exists the resolution falls
  back to (b) a writable characteristic of a vendor-specific service
  (preferring services that also notify), (c) an explicit
  AquaTempConnectionError whose MESSAGE lists the real table. The
  historic ff01-first preference is GONE — the app never selects a
  characteristic ff01 (research 010 §1.3); a unit whose only writable
  char IS ff01 still gets it via the adaptive fallback (b).
- AC-3 (notify resolution): the app's notify bucket {ff02, 2216,
  2a00} resolves FIRST, property-blind; the classic ff02 pick stays
  silent, anything else is announced at INFO. When no bucket UUID
  exists the client subscribes to a notify/indicate characteristic of
  the same service as the write candidate, else of a vendor service,
  else the same explicit listing error — never from a standard SIG
  service.
- AC-4 (choice logging): the write choice is ALWAYS announced with
  ONE INFO line "using write characteristic <uuid> (properties=...)"
  (the E4' evidence line, quickstart 010 §0); notify alternatives
  keep the "using notify characteristic ..." line.
- AC-5 (regression): the production layout (service ff01 hosting
  ff02+ff03) keeps the exact historic behaviour — same uuids on the
  wire, same vendor session order (spec 006 FR-1), without-response
  writes (spec 006 FR-2 / research 010 §2.3).

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

# The app's bucket heads (spec 010 FR-1, research 010 §1): ff02 is the
# notify char, ff03 the write char — the pair the production census
# log already showed. The full buckets live in
# const.UUID_NOTIFY_BUCKETS / const.UUID_WRITE_BUCKETS.
FF02 = const.UUID_NOTIFY
FF03 = const.UUID_WRITE

# The historic DEX "write" UUID (FINDINGS.md §3 + the §11.3 spec-010
# correction): NOT in the app's buckets — the app never selects a
# characteristic ff01 (research 010 §1.3). Fixture-only from spec 010
# on: a unit whose ONLY writable char is ff01 still gets it via the
# adaptive vendor-service scan (the c8d8260 safety net).
FF01 = "0000ff01-0000-1000-8000-00805f9b34fb"
FF01_SVC = "0000ff01-0000-1000-8000-00805f9b34fb"  # the owner unit's SERVICE uuid (research 010 §0)
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

    async def _connect_with_raw_client(self, _device, pair: bool = False):
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
    """The historic "documented" layout (ff01 writable + ff03 read-only
    + ff02 notify, handles FINDINGS §11.3): from spec 010 the write
    bucket picks ff03 even though ff01 is the only writable char — the
    app never writes a characteristic ff01 (research 010 §1.3)."""
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


def owner_unit(response=None):
    """The PRODUCTION unit's layout (research 010 §0/§6.2): service ff01
    hosting ff02 (notify) + ff03 (write, write-without-response) — the
    pair the ce41aa5 census run already resolved at runtime."""
    services = FakeServices(
        [
            _svc(GAP, 1, FakeChar(DEV_NAME, 2, ["read"])),
            _svc(
                FF01_SVC,
                2212,
                FakeChar(FF02, 2216, ["notify"]),
                FakeChar(FF03, 2215, ["write", "write-without-response"]),
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
    """AC-5 regression, spec-010 edition: the production layout (service
    ff01 hosting ff02+ff03, research 010 §0) resolves notify=ff02 /
    write=ff03 — the pair the ce41aa5 census already used at runtime —
    with the byte-identical observable session: same uuids on the
    wire, same vendor session order (spec 006 FR-1), without-response
    writes (spec 006 FR-2 / research 010 §2.3), the census write line
    present, and NO notify announcement (the classic ff02 pick stays
    silent).

    Red-first: on the pre-010 code the write resolution prefers the
    ff01-first branch, so the ff03 bucket pick never happens.
    """

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        fake = owner_unit(resp)
        client = DiscoveryClient(fake)
        # Sleeps are recorded into the FAKE's event list so the session
        # order (transport calls interleaved with the settle) is one
        # observable timeline — same technique as test_connect_parity.
        with _record_sleeps(fake.events), _capture() as logs:
            await client.connect()
            regs = await client.read_registers(7, 45)
        assert regs[26] == 243, regs  # the scripted session answered
        assert client._write_uuid == FF03
        assert client._notify_uuid == FF02
        assert fake.notified_uuids == [FF02], fake.notified_uuids
        assert all(uuid == FF03 for uuid, _f, _r in fake.writes), fake.writes
        # Property gate (spec 006 FR-2 / research 010 §2.3):
        # without-response preferred — the app's only WriteType.
        assert all(r is False for _u, _f, r in fake.writes), fake.writes
        # Vendor session order (spec 006 FR-1) is untouched.
        assert fake.events == [
            "connect",
            "start_notify",
            ("sleep", const.SETTLE_AFTER_SUBSCRIBE),
            "write",
        ], fake.events
        # AC-4: the census write line, byte-identical to the production
        # format (the E4' grep target, quickstart 010 §0).
        assert (
            f"using write characteristic {FF03} "
            f"(properties=['write', 'write-without-response']) "
            f"on {ADDRESS}" in logs
        ), logs
        # The classic ff02 pick is never announced.
        assert not [m for m in logs if m.startswith("using notify characteristic")], logs
        # No property advisories on the clean layout.
        assert not [m for m in logs if "app parity" in m], logs

    asyncio.run(run())


def test_write_fallback_finds_writable_char_when_bucket_uuids_absent() -> None:
    """AC-2(b)/AC-3/AC-4: no bucket UUID anywhere — a full 128-bit vendor
    service exposes write + notify. connect() must USE them end to end
    and announce both choices at INFO (the unchanged c8d8260 adaptive
    safety net).

    Red-first: the pre-discovery code dies with "write characteristic
    ... not found on the device".
    """

    async def run() -> None:
        events: list = []
        resp = _build_response(SYNTHETIC_VALUES)
        # No bucket UUID anywhere: the GAP device-name char 2a00 is
        # itself a notify-bucket UUID (research 010 §1.3 — a table
        # exposing it resolves 2a00 property-blind, see T5 in
        # test_characteristic_resolution.py), so the fixture uses a
        # battery service instead: only the full 128-bit vendor service
        # exposes write + notify.
        services = FakeServices(
            [
                _svc(BATTERY, 10, FakeChar(BATT_LEVEL, 11, ["read"])),
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


def test_write_bucket_picks_ff03_when_writable() -> None:
    """AC-2/plan-010 T1 end to end: the write bucket head ff03
    advertising only 'write' is picked FIRST (property-blind), with
    the with-response ATT divergence + WARNING (FR-3), while ff02
    keeps the notify role.

    Red-first: the pre-010 code prefers the absent ff01 and only
    reaches ff03 through the last-resort branch.
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


def test_bucket_ff03_without_write_property_is_used_anyway() -> None:
    """AC-2 / plan-010 T2: ff03 exists but advertises NO write property —
    the bucket pick is property-BLIND, so ff03 is still the write char
    with the app-parity WARNING naming the missing property and the
    vendor-parity write mode (without response, research 010 §1/§2.3).

    Red-first: the pre-010 code reaches ff03 only through the
    last-resort branch with the old wording.
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
        # No property advertises a mode -> app parity: without response.
        assert client._write_with_response is False
        warns = [m for m in logs if "app parity: writing anyway" in m]
        assert warns and FF03 in warns[0], logs
        assert "advertises no write property" in warns[0], warns
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
        # No write-bucket UUID (ff03/2215/2aa6) and no vendor writable
        # char anywhere — the known UUID absence is named...
        assert FF03 in msg, msg
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
    """AC-3 error twin: NO notify bucket UUID (ff02/2216/2a00 all
    absent) and no vendor notify/indicate char — explicit error BEFORE
    any subscription, listing the real table. The notifying battery
    char (2a19, a STANDARD service) is listed but never a candidate.

    Spec-010 note: a table exposing 2a00 would NOT fail here — 2a00 is
    in the app's notify bucket (research 010 §1.3), so the bucket walk
    would pick it (T5 in test_characteristic_resolution.py).
    """

    async def run() -> None:
        services = FakeServices(
            [
                _svc(FF00, 2212, FakeChar(FF01, 2214, ["write", "write-without-response"])),
                _svc(BATTERY, 10, FakeChar(BATT_LEVEL, 11, ["read", "notify"])),
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
        # The standard-service notify char is listed but NOT chosen.
        assert BATT_LEVEL in msg and "['read', 'notify']" in msg, msg
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
        test_write_fallback_finds_writable_char_when_bucket_uuids_absent,
        test_write_bucket_picks_ff03_when_writable,
        test_bucket_ff03_without_write_property_is_used_anyway,
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