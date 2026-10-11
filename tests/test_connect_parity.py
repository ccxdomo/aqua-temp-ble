#!/usr/bin/env python3
"""Vendor-parity tests for the connect sequence (spec 006, AC-1/AC-2).

The vendor app's proven BLE session (research 006 §2, contract
ble-session-sequence.md §2) is:

    connect -> discover -> subscribe ff02 -> settle 2 s -> first read

and its write path uses WRITE WITHOUT RESPONSE (research 006 §2.1).
These tests pin BOTH parity behaviours on the shipped client:

- AC-1: connect() awaits exactly one SETTLE_AFTER_SUBSCRIBE sleep,
  ordered AFTER start_notify and BEFORE the first request frame.
- AC-2: _resolve_write_characteristic prefers write-without-response
  (response=False), falls back to write (response=True), and (spec 010
  FR-3) a characteristic advertising NO write property at all is used
  ANYWAY — without response, with an app-parity WARNING — never a
  connect-time refusal. The pre-010 code hard-refused that case
  (spec 006 AC-2); the refusal was only ever reachable through the
  deleted ff01-first branch, and the app — property-blind (research
  010 §1) — writes regardless.

Red-first: every AC-1/AC-2 assertion below reproduces the gap with the
vendor app — on the pre-006 code there is no settle sleep and the write
mode is picked WITH response, so these tests fail until FR-1/FR-2 land.
The write-only rows are regression guards and may already pass.

Only the outer I/O seams (_resolve_device, _load_retry_connector,
_connect_with_raw_client) are patched, so connect() runs its REAL body;
asyncio.sleep is recorded (never waited) so the tests cost no
wall-clock time.

Run standalone:

    python3 tests/test_connect_parity.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import asyncio
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

ADDRESS = "08:06:A6:C0:94:1C"

SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: -9990, 28: -125, 47: 0}


def _build_response(values: dict[int, int], start: int = 7, count: int = 45) -> bytes:
    """Synthetic 95-byte FC03 response."""
    payload = bytearray()
    for addr in range(start, start + count):
        payload += (values.get(addr, 0) & 0xFFFF).to_bytes(2, "big")
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, len(payload)]) + bytes(payload)
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


# ---------------------------------------------------------------------------
# Fakes: the transport seams only — connect() runs its real body over them.
# ---------------------------------------------------------------------------


class FakeBLEDevice:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class FakeChar:
    """GATT characteristic stand-in with scriptable properties."""

    def __init__(self, props):
        self.properties = list(props)


class FakeServices:
    def __init__(self, char):
        self._char = char

    def get_characteristic(self, _uuid):
        return self._char


class RecordingBleakClient:
    """Connected bleak client stand-in that records the session order.

    start_notify stores the client's real _on_notify handler;
    write_gatt_char records the frame + the `response` kwarg, then
    answers with one scripted notification (the single-chunk regime —
    chunked reassembly is covered by test_reassembly.py).
    """

    is_connected = True

    def __init__(self, props, events, response):
        self.events = events
        self.writes: list[tuple[bytes, bool]] = []
        self.services = FakeServices(FakeChar(props))
        self._response = response
        self._handler = None

    async def start_notify(self, _uuid, handler):
        self.events.append("start_notify")
        self._handler = handler

    async def write_gatt_char(self, _uuid, data, response=True):
        self.events.append("write")
        self.writes.append((bytes(data), response))
        if self._handler is not None and self._response is not None:
            self._handler(bytearray(self._response))

    async def disconnect(self):
        self.events.append("disconnect")


class ParityClient(aqua_client.AquaTempBleClient):
    """AquaTempBleClient over the recording fakes.

    Only the I/O seams are patched (resolve / retry-connector / raw
    connect); the connect() BODY — start_notify, write-characteristic
    resolution, the settle sleep — is the shipped code under test.
    """

    def __init__(self, bleak_client: RecordingBleakClient):
        super().__init__(ADDRESS)
        self._fake = bleak_client

    async def _resolve_device(self):
        return FakeBLEDevice(ADDRESS, "BlueNRG-C0941C")

    def _load_retry_connector(self):
        return None  # force the standalone raw-client path

    async def _connect_with_raw_client(self, _device, pair: bool = False):
        self._fake.events.append("connect")
        return self._fake


def _record_sleeps(events: list):
    """Patch asyncio.sleep to record the delay instead of waiting.

    aqua_client awaits asyncio.sleep(SETTLE_AFTER_SUBSCRIBE) inside
    connect(); recording (never waiting) keeps the test instant while the
    ordering stays observable. The real sleep is returned for the caller
    to restore in a finally block.
    """
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *_args, **_kwargs):
        events.append(("sleep", delay))

    asyncio.sleep = recording_sleep
    return real_sleep


# --------------------------------------------------------------------- tests
def test_settle_constant_value() -> None:
    """AC-1: the settle is the vendor's 2 s Duration (research 006 §7).

    Red-first: the constant does not exist on the pre-006 code.
    """
    assert const.SETTLE_AFTER_SUBSCRIBE == 2.0, (
        "SETTLE_AFTER_SUBSCRIBE must be the vendor's 2 s settle "
        "(research 006 §2 step 4, §7)"
    )


def test_settle_after_subscribe_before_first_request() -> None:
    """AC-1: exactly one settle sleep, ordered start_notify -> sleep -> read.

    Red-first: on the pre-006 code connect() returns straight after the
    notify setup — no sleep exists between the subscribe and the first
    request, which is the gap with the vendor app's session.
    """

    async def run() -> None:
        events: list = []
        fake = RecordingBleakClient(
            ("write-without-response", "write"), events, _build_response(SYNTHETIC_VALUES)
        )
        client = ParityClient(fake)
        real_sleep = _record_sleeps(events)
        try:
            await client.connect()
        finally:
            asyncio.sleep = real_sleep
        await client.read_registers(7, 45)

        sleeps = [e for e in events if isinstance(e, tuple)]
        assert len(sleeps) == 1, f"expected exactly one settle sleep, got: {events}"
        assert sleeps[0] == ("sleep", const.SETTLE_AFTER_SUBSCRIBE), sleeps
        # Full session order (contract 006 §2): connect -> start_notify ->
        # settle -> first request. The sleep precedes ANY Modbus frame.
        assert events == [
            "connect",
            "start_notify",
            ("sleep", const.SETTLE_AFTER_SUBSCRIBE),
            "write",
        ], events

    asyncio.run(run())


def test_write_mode_property_matrix() -> None:
    """AC-2: without-response preferred, write fallback, no-property ->
    used anyway (spec 010 FR-3).

    Red-first rows: ['write-without-response', 'write'] currently maps to
    WITH response (the vendor writes without — research 006 §2.1), and
    ['notify'] currently raises AquaTempConnectionError instead of the
    FR-3 without-response-anyway + WARNING (spec 010 supersedes the
    spec-006 refusal row — the app, property-blind, writes regardless,
    research 010 §1/§2.3).
    """

    async def run() -> None:
        for props, expected in (
            (("write-without-response", "write"), False),
            (("write",), True),
        ):
            fake = RecordingBleakClient(props, [], None)
            client = ParityClient(fake)
            client._resolve_write_characteristic(fake)
            assert client._write_with_response is expected, (props, expected)

        # Spec 010 FR-3: no write property advertised — the bucket pick
        # (the fake answers every UUID lookup) is used WITHOUT response
        # anyway, never a connect-time refusal. The exact WARNING is
        # pinned in tests/test_write_mode.py.
        fake = RecordingBleakClient(("notify",), [], None)
        client = ParityClient(fake)
        client._resolve_write_characteristic(fake)
        assert client._write_with_response is False, client._write_with_response
        assert client._write_uuid == const.UUID_WRITE, client._write_uuid

    asyncio.run(run())


def test_write_mode_reaches_the_wire() -> None:
    """AC-2 end-to-end: _write_frame passes the resolved mode to bleak.

    Without-response ff03 (the app's write bucket head — the fake
    answers every UUID lookup with the same char) -> every GATT write
    carries response=False (vendor parity, research 006 §2.1, research
    010 §2.3); write-only -> response=True (the FR-3 ATT divergence).
    Red-first: the without-response case writes response=True on the
    pre-006 code.
    """

    async def run() -> None:
        for props, expected in (
            (("write-without-response", "write"), False),
            (("write",), True),
        ):
            events: list = []
            fake = RecordingBleakClient(props, events, _build_response(SYNTHETIC_VALUES))
            client = ParityClient(fake)
            real_sleep = _record_sleeps(events)
            try:
                await client.connect()
            finally:
                asyncio.sleep = real_sleep
            regs = await client.read_registers(7, 45)
            assert regs[26] == 243, regs  # the scripted session answered
            assert fake.writes, "no frame reached the wire"
            assert all(
                got is expected for _frame, got in fake.writes
            ), (props, fake.writes)

    asyncio.run(run())


def main() -> int:
    tests = [
        test_settle_constant_value,
        test_settle_after_subscribe_before_first_request,
        test_write_mode_property_matrix,
        test_write_mode_reaches_the_wire,
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
    print(f"PASSED: {len(tests)}/{len(tests)} connect parity tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())