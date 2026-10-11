#!/usr/bin/env python3
"""Reassembly tests for AquaTempBleClient with a mocked BLE transport.

Gate (BLUEPRINT §7 step 4): a 95-byte FC03 response split into 20-byte
chunks, into 2 chunks, and into 1 chunk must all complete correctly; garbage
prefix and CRC-corrupt paths must raise; silence/delay fallbacks must work.

Spec 008 revised the reassembly rules: silence/deadline complete ONLY
frames that MATCH the pending request (FR-4), and a CRC-invalid or
non-matching frame never fails a request (FR-2) — the four updated tests
below pin that contract (contracts/reassembly-matching.md §4).

Run standalone:

    python3 tests/test_reassembly.py

No real Bluetooth and no Home Assistant required (a fake bleak module is
installed if bleak is missing).
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
import registers  # noqa: E402

ADDRESS = "AA:BB:CC:DD:EE:FF"

SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: -9990, 28: -125, 47: 0}


def _build_response(values: dict[int, int], start: int = 7, count: int = 45) -> bytes:
    """Synthetic 95-byte FC03 response."""
    payload = bytearray()
    for addr in range(start, start + count):
        payload += (values.get(addr, 0) & 0xFFFF).to_bytes(2, "big")
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, len(payload)]) + bytes(payload)
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


def _build_echo(start: int, qty: int = 1) -> bytes:
    head = (
        bytes([modbus.SLAVE_ID, modbus.FC_WRITE_MULTI])
        + start.to_bytes(2, "big")
        + qty.to_bytes(2, "big")
    )
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


def _chunks(response: bytes, size: int) -> list[bytes]:
    return [response[i : i + size] for i in range(0, len(response), size)]


class FakeBleakClient:
    """Minimal stand-in for a connected bleak client."""

    is_connected = True

    async def write_gatt_char(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass


class ScriptedClient(aqua_client.AquaTempBleClient):
    """Overrides the transport seam: replies with scripted notification chunks."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        super().__init__(ADDRESS)
        self.chunks = list(chunks or [])
        self.sent: list[bytes] = []

    async def _write_frame(self, frame: bytes, *, force_response: bool = False) -> None:
        self.sent.append(bytes(frame))
        for chunk in self.chunks:
            # feed chunks synchronously as if the device just notified us
            self._on_notify(bytearray(chunk))


def _connected(chunks: list[bytes] | None = None) -> ScriptedClient:
    client = ScriptedClient(chunks)
    client._client = FakeBleakClient()  # mark as connected
    return client


class FakeBLEDevice:
    """Stand-in for a bleak BLEDevice (address + name only)."""

    def __init__(self, address, name):
        self.address = address
        self.name = name


class _FakeChar:
    """GATT characteristic stand-in with scriptable properties."""

    def __init__(self, props):
        self.properties = list(props)


class _FakeServices:
    """Services collection stand-in exposing one characteristic."""

    def __init__(self, props):
        self._char = _FakeChar(props)

    def get_characteristic(self, _uuid):
        return self._char


class ConnectableFakeBleakClient:
    """Fake connected bleak client for driving the REAL connect() body.

    Spec 006 A4/plan §4.1: connect() now sleeps SETTLE_AFTER_SUBSCRIBE
    after start_notify (FR-1); this fake implements every method the
    connect path touches (start_notify stores the handler, write_gatt_char
    replays the scripted chunks as notifications), so the shipped connect()
    can run over it while the fixture zeroes the settle constant — no
    wall-clock cost.
    """

    is_connected = True

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = list(chunks or [])
        self._handler = None
        self.services = _FakeServices(["write-without-response", "write"])

    async def start_notify(self, _uuid, handler) -> None:
        self._handler = handler

    async def write_gatt_char(self, _uuid, _data, response=True) -> None:
        for chunk in self.chunks:
            self._handler(bytearray(chunk))

    async def disconnect(self) -> None:
        pass


class ConnectableScriptedClient(aqua_client.AquaTempBleClient):
    """ScriptedClient + patched connect() seams, so the REAL connect()
    body (start_notify, write-char resolution, the settle sleep) runs over
    ConnectableFakeBleakClient (spec 006 A4)."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        super().__init__(ADDRESS)
        self.bleak = ConnectableFakeBleakClient(chunks)
        self.sent: list[bytes] = []

    async def _resolve_device(self):
        return FakeBLEDevice(ADDRESS, "BlueNRG-C0941C")

    def _load_retry_connector(self):
        return None  # force the standalone raw-client path

    async def _connect_with_raw_client(self, _device, pair: bool = False):
        return self.bleak

    async def _write_frame(self, frame: bytes, *, force_response: bool = False) -> None:
        self.sent.append(bytes(frame))
        await self.bleak.write_gatt_char(None, frame, response=self._write_with_response)


# --------------------------------------------------------------------- tests
def test_reassembly_20_byte_chunks() -> None:
    """95-byte response in 5 chunks of <=20 bytes (default-MTU case)."""

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        assert len(_chunks(resp, 20)) == 5  # 20,20,20,20,15
        client = _connected(_chunks(resp, 20))
        regs = await client.read_registers(7, 45)
        assert len(regs) == 45
        assert regs[26] == 243
        assert regs[27] == -9990

    asyncio.run(run())


def test_reassembly_two_chunks() -> None:
    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        client = _connected(_chunks(resp, 48))
        assert len(client.chunks) == 2
        regs = await client.read_registers(7, 45)
        assert regs[14] == 325

    asyncio.run(run())


def test_reassembly_single_chunk() -> None:
    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        client = _connected([resp])
        regs = await client.read_registers(7, 45)
        assert regs[28] == -125

    asyncio.run(run())


def test_garbage_prefix_dropped() -> None:
    """Non-Modbus leading chunk is dropped; the good response still parses.
    Also exercises the old-bleak two-argument callback form."""

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        client = _connected([b"\xab\xcd\xef", *_chunks(resp, 20)])
        # feed the garbage chunk with the legacy (sender, data) callback shape
        client.chunks = _chunks(resp, 20)
        regs = await client.read_registers(7, 45)
        assert regs[26] == 243

    asyncio.run(run())


def test_crc_corrupt_response_censused_request_keeps_waiting() -> None:
    """Spec 008 FR-2: a CRC-corrupt response is census-logged at DEBUG and
    discarded WITHOUT failing the request — it keeps waiting for its own
    frame, so the deadline raises AquaTempTimeoutError with the honest
    counters ('received 0 frame(s) / 95 bytes': the bytes arrived, no
    complete CRC-valid frame was seen).

    History: c8d8260 failed fast with AquaTempResponseError("bad CRC").
    The spec-008 contract (reassembly-matching.md §2 step 9) removed the
    fast-fail: a corrupt frame may be a corrupted instance of the expected
    answer, so the request waits instead of dying.
    """

    async def run() -> None:
        resp = bytearray(_build_response(SYNTHETIC_VALUES))
        resp[10] ^= 0xFF
        client = _connected([bytes(resp)])
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25  # speed the wait up
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError as err:
            msg = str(err)
        else:
            raise AssertionError("corrupt-only response did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        assert "received 0 frame(s) / 95 bytes" in msg, msg

    asyncio.run(run())


def test_no_response_times_out() -> None:
    async def run() -> None:
        client = _connected([])
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25  # speed the test up
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            ok = True
        else:
            ok = False
        finally:
            const.REQUEST_TIMEOUT = old
        assert ok, "missing response did not time out"

    asyncio.run(run())


def test_silence_never_completes_unknown_shape() -> None:
    """Spec 008 FR-4: the silence timer completes ONLY a CRC-valid frame
    that MATCHES the pending request — an unknown-shape frame is left to
    the request deadline, which raises the census AquaTempTimeoutError.

    History: c8d8260 completed any CRC-valid buffer at silence (the
    fallback for unknown response shapes, written when the high-block
    answer was [V]). Spec 008 closed that hole: all real shapes are now
    known [P — research 008 §2.1] and a non-matching buffer must never
    resolve a request (contract reassembly-matching.md §4.2). The
    silence self-heal that REMAINS (the pathological write echo) is
    pinned in test_reassembly_matching.py.
    """

    async def run() -> None:
        head = bytes([modbus.SLAVE_ID, 0x63, 5, 1, 2, 3, 4])  # unknown fc 0x63
        frame = head + modbus.crc16_modbus(head).to_bytes(2, "little")
        client = _connected(_chunks(frame, 4))
        old_silence, old_request = const.SILENCE_TIMEOUT, const.REQUEST_TIMEOUT
        const.SILENCE_TIMEOUT = 0.05
        const.REQUEST_TIMEOUT = 0.25
        try:
            await client.query_block(1001, 45)
        except aqua_client.AquaTempTimeoutError as err:
            assert "received 0 frame(s) / 9 bytes" in str(err)
            ok = True
        else:
            ok = False
        finally:
            const.SILENCE_TIMEOUT, const.REQUEST_TIMEOUT = old_silence, old_request
        assert ok, "unknown-shape frame completed a request at silence"

    asyncio.run(run())


def test_deadline_requires_matching_frame() -> None:
    """Spec 008 FR-4 / contract 008-B §4.3: the deadline accepts ONLY a
    complete CRC-valid frame that MATCHES the request — an unknown-shape
    CRC-valid buffer is refused and raises the census timeout.

    History: c8d8260 accepted ANY CRC-valid buffer at the deadline. The
    matching self-heal that REMAINS at the deadline (the pathological
    echo) is pinned in test_reassembly_matching.py.
    """

    async def run() -> None:
        head = bytes([modbus.SLAVE_ID, 0x63, 5, 1, 2, 3, 4])  # unknown fc 0x63
        frame = head + modbus.crc16_modbus(head).to_bytes(2, "little")
        client = _connected(_chunks(frame, 4))
        old_silence, old_request = const.SILENCE_TIMEOUT, const.REQUEST_TIMEOUT
        const.SILENCE_TIMEOUT = 5.0  # silence must NOT fire before the deadline
        const.REQUEST_TIMEOUT = 0.25
        try:
            await client.query_block(1001, 45)
        except aqua_client.AquaTempTimeoutError as err:
            assert "received 0 frame(s) / 9 bytes" in str(err)
            ok = True
        else:
            ok = False
        finally:
            const.SILENCE_TIMEOUT, const.REQUEST_TIMEOUT = old_silence, old_request
        assert ok, "non-matching CRC-valid buffer was accepted at the deadline"

    asyncio.run(run())


def test_write_with_echo() -> None:
    """H1 write vector goes out on the wire and the echo validates."""

    async def run() -> None:
        client = _connected([_build_echo(8, 1)])
        await client.write_registers(8, [1])
        assert client.sent[0].hex() == "631000080001020001d7ba"

    asyncio.run(run())


def test_write_echo_mismatch_censused() -> None:
    """Spec 008 FR-2: an echo for a DIFFERENT register does not match the
    write request — it is census-logged (1 frame / 8 bytes) and discarded
    without failing the request; the echo wait ends at its deadline with
    AquaTempTimeoutError.

    History: c8d8260 completed on any FC10 frame, then parse_write_echo
    raised AquaTempResponseError("echo mismatch"). Spec 008 moved the
    start/qty check INTO the match (contract 008-B §3) — a wrong echo
    can no longer resolve the request.
    """

    async def run() -> None:
        client = _connected([_build_echo(9, 1)])  # echo for a different register
        old = const.WRITE_ECHO_TIMEOUT
        const.WRITE_ECHO_TIMEOUT = 0.25
        try:
            await client.write_registers(8, [1])
        except aqua_client.AquaTempTimeoutError as err:
            assert "received 1 frame(s) / 8 bytes" in str(err)
            ok = True
        else:
            ok = False
        finally:
            const.WRITE_ECHO_TIMEOUT = old
        assert ok, "mismatched echo completed the request"

    asyncio.run(run())


def test_write_fire_and_forget() -> None:
    """WRITE_ECHO_EXPECTED=False -> no echo wait, no error."""

    async def run() -> None:
        old = registers.WRITE_ECHO_EXPECTED
        registers.WRITE_ECHO_EXPECTED = False
        try:
            client = _connected()  # no echo will ever arrive
            await client.write_registers(14, [325])
            assert client.sent[0].hex() == "6310000e0001020145d67f"
        finally:
            registers.WRITE_ECHO_EXPECTED = old

    asyncio.run(run())


def test_write_echo_timeout_raises() -> None:
    async def run() -> None:
        client = _connected()  # echo expected but never arrives
        old = const.WRITE_ECHO_TIMEOUT
        const.WRITE_ECHO_TIMEOUT = 0.25
        try:
            await client.write_registers(8, [1])
        except aqua_client.AquaTempTimeoutError:
            ok = True
        else:
            ok = False
        finally:
            const.WRITE_ECHO_TIMEOUT = old
        assert ok, "missing echo did not raise"

    asyncio.run(run())


def test_exception_response_raises() -> None:
    """A Modbus exception frame (fc|0x80) surfaces as AquaTempResponseError
    carrying the exception code."""

    async def run() -> None:
        head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING | 0x80, 0x02])
        exc = head + modbus.crc16_modbus(head).to_bytes(2, "little")
        assert len(exc) == 5
        client = _connected([exc])
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempResponseError as err:
            assert err.modbus_error is not None
            assert err.modbus_error.code == 2
        else:
            raise AssertionError("exception response did not raise")

    asyncio.run(run())


def test_stale_notification_dropped() -> None:
    """A notification with no request in flight is dropped without crash."""

    async def run() -> None:
        client = _connected()
        client._on_notify(bytearray(b"\x63\x03\x5a\x01\x02"))
        client._on_notify(None, bytearray(b"\x63\x03\x5a\x01\x02"))  # legacy 2-arg
        assert not client._rx

    asyncio.run(run())


def test_connect_settle_zeroed_for_fakes() -> None:
    """Spec 006 A4/plan §4.1: fixtures zero the post-subscribe settle.

    connect() sleeps SETTLE_AFTER_SUBSCRIBE after start_notify (spec 006
    FR-1); this fixture monkeypatches the constant to 0 so driving the
    real connect() body costs no wall-clock. Regression guard: proves the
    reassembly fakes keep working once the sleep is part of connect().
    """

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        client = ConnectableScriptedClient(_chunks(resp, 20))
        had_settle = hasattr(const, "SETTLE_AFTER_SUBSCRIBE")
        settle = getattr(const, "SETTLE_AFTER_SUBSCRIBE", 2.0)
        const.SETTLE_AFTER_SUBSCRIBE = 0  # zeroed in the fixture (A4)
        try:
            await client.connect()
            assert client.is_connected
            regs = await client.read_registers(7, 45)
            assert regs[26] == 243
        finally:
            const.SETTLE_AFTER_SUBSCRIBE = settle
            if not had_settle:
                # leave the pre-006 module exactly as we found it
                del const.SETTLE_AFTER_SUBSCRIBE

    asyncio.run(run())


def test_single_flight_guard() -> None:
    """A second concurrent request is refused (PAC protection)."""

    async def run() -> None:
        client = _connected()

        async def slow_write(frame: bytes, *, force_response: bool = False) -> None:
            await asyncio.sleep(0.2)

        client._write_frame = slow_write  # type: ignore[method-assign]
        task = asyncio.create_task(client.read_registers(7, 45))
        await asyncio.sleep(0.05)
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempError:
            ok = True
        else:
            ok = False
        task.cancel()
        assert ok, "concurrent second request was not refused"

    asyncio.run(run())


def main() -> int:
    tests = [
        test_reassembly_20_byte_chunks,
        test_reassembly_two_chunks,
        test_reassembly_single_chunk,
        test_garbage_prefix_dropped,
        test_crc_corrupt_response_censused_request_keeps_waiting,
        test_no_response_times_out,
        test_silence_never_completes_unknown_shape,
        test_deadline_requires_matching_frame,
        test_write_with_echo,
        test_write_echo_mismatch_censused,
        test_write_fire_and_forget,
        test_write_echo_timeout_raises,
        test_exception_response_raises,
        test_stale_notification_dropped,
        test_connect_settle_zeroed_for_fakes,
        test_single_flight_guard,
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
    print(f"PASSED: {len(tests)}/{len(tests)} reassembly tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())