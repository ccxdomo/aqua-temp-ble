#!/usr/bin/env python3
"""Reassembly-matching tests (spec 008) — request-aware frame matching.

The production case (research 008 §1/§3): with a pending
read_registers(7, 45) the unit pushes `63 10 00 07 00 2d 5a …` block
frames; the c8d8260 FC drop-gate deleted each one
("dropping unexpected function code 0x10") and the request died with
"got 0 bytes". These tests pin the spec-008 contract
(contracts/reassembly-matching.md):

- a block frame echoing the requested start/qty COMPLETES the request
  (AC-2, AC-7), the FC03 answer still completes too (AC-3);
- pushes for other blocks are census-logged at INFO and never complete
  or fail the request (AC-4);
- the timeout message reports the census — frames received, bytes,
  block starts — never a bare "got 0 bytes" (AC-5). Pinned to the
  spec-009 grammar (NFR-6): per-start counts (`1001x2, …`) plus the
  first/last census offsets — the 008 semantic properties stay pinned;
- the write-echo path is unchanged, including the pathological echo
  whose CRC low byte equals 2*qty: the silence/deadline paths self-heal
  it (AC-6, contract 008-B §4.2/§4.3);
- exception frames still fail fast;
- idle pushes census once per connect at INFO, then DEBUG (FR-4).

Red-first: AC-2/AC-4/AC-5/AC-7 reproduce the production failure
verbatim on c8d8260 (AquaTempTimeoutError "… got 0 bytes" after the
WARNING drop) — kept in the history comments.

Run standalone:

    python3 tests/test_reassembly_matching.py

No real Bluetooth and no Home Assistant required (a fake bleak module is
installed if bleak is missing).
"""

from __future__ import annotations

import asyncio
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
import registers  # noqa: E402

ADDRESS = "AA:BB:CC:DD:EE:FF"

# Synthetic base-block values for the start-7 block frame (the production
# first-read shape, research 008 §3).
SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: -9990, 28: -125, 51: 2040}

# The proven production frames (research 008 §2) — block 1001/1046 pushes.
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)
FRAME_BLOCK_1046 = (
    "63100416002d5a01220005000500960190010e000500050064015effba00820028000800030000000000000000000000000000010e00000000000000000000000000000000000000000000000000000000000000000000000000000000000000008576"
)

# The pathological write echo (contract 008-B §4.3): qty=1, CRC low byte
# 0x02 == 2*qty — the block length rule misreads it as an 11-byte frame.
PATHO_ECHO_START = 8192
PATHO_ECHO = bytes.fromhex("631020000001024b")


def _build_response(values: dict[int, int], start: int = 7, count: int = 45) -> bytes:
    """Synthetic 95-byte FC03 response."""
    payload = bytearray()
    for addr in range(start, start + count):
        payload += (values.get(addr, 0) & 0xFFFF).to_bytes(2, "big")
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, len(payload)]) + bytes(payload)
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


def _build_block_frame(
    start: int, count: int = 45, values: dict[int, int] | None = None
) -> bytes:
    """Build the unit's block frame [P] (research 008 §2.1):

        63 10 start:2 qty:2 bytecount(=2*qty) data:bytecount crc:2 LE
    """
    values = values or {}
    payload = bytearray()
    for i in range(count):
        payload += (values.get(start + i, 0) & 0xFFFF).to_bytes(2, "big")
    head = (
        bytes([modbus.SLAVE_ID, 0x10])
        + start.to_bytes(2, "big")
        + count.to_bytes(2, "big")
        + bytes([len(payload)])
    )
    body = head + bytes(payload)
    return body + modbus.crc16_modbus(body).to_bytes(2, "little")


def _build_echo(start: int, qty: int = 1) -> bytes:
    head = (
        bytes([modbus.SLAVE_ID, modbus.FC_WRITE_MULTI])
        + start.to_bytes(2, "big")
        + qty.to_bytes(2, "big")
    )
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


def _chunks(frame: bytes, size: int = 20) -> list[bytes]:
    return [frame[i : i + size] for i in range(0, len(frame), size)]


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


class _LogCap(logging.Handler):
    """Capture records emitted on the aqua_client logger (census lines)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


class _capture_logs:
    """Attach a capture handler to aqua_client._LOGGER for the duration."""

    def __enter__(self) -> _LogCap:
        self.cap = _LogCap()
        self.logger = aqua_client._LOGGER
        self.old_level = self.logger.level
        self.logger.addHandler(self.cap)
        self.logger.setLevel(logging.DEBUG)
        return self.cap

    def __exit__(self, *_exc) -> None:
        self.logger.removeHandler(self.cap)
        self.logger.setLevel(self.old_level)


# --------------------------------------------------------------------- tests
def test_read_completes_on_pushed_start7_block() -> None:
    """AC-2 — THE production case (research 008 §1/§3): a pending
    read_registers(7, 45) completes on the unit's pushed start-7 block
    frame `63 10 00 07 00 2d 5a …`.

    History (c8d8260, the red-first run): the FC drop-gate read rx[1] ==
    0x10, logged 'dropping unexpected function code 0x10: 63100007002d…'
    and cleared the 99 bytes; the request then died with
    AquaTempTimeoutError: no complete response within 8.0s (got 0 bytes)
    — the verbatim production failure.
    """

    async def run() -> None:
        frame = _build_block_frame(7, 45, SYNTHETIC_VALUES)
        assert frame[:7].hex() == "63100007002d5a"
        assert len(frame) == 99
        client = _connected([frame])
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25  # speed a failure up (red-first run)
        try:
            regs = await client.read_registers(7, 45)
        finally:
            const.REQUEST_TIMEOUT = old
        assert len(regs) == 45
        assert regs[14] == 325
        assert regs[26] == 243
        assert regs[27] == -9990   # sentinel preserved through the block parse
        assert regs[28] == -125

    asyncio.run(run())


def test_read_completes_on_real_block_1001_frame() -> None:
    """The proven production frame `631003e9002d5a…563a` (CRC 0x3A56,
    research 008 §2.2) is ACCEPTED, not dropped, when the request's
    start/qty match it."""

    async def run() -> None:
        frame = bytes.fromhex(FRAME_BLOCK_1001)
        assert modbus.validate_crc(frame)
        client = _connected([frame])
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            regs = await client.read_registers(1001, 45)
        finally:
            const.REQUEST_TIMEOUT = old
        assert len(regs) == 45
        assert regs[1021] == 1050     # the H04 exhaust-high threshold [P]

    asyncio.run(run())


def test_fc03_response_still_completes() -> None:
    """AC-3 regression: the standard FC03 answer still completes the read."""

    async def run() -> None:
        resp = _build_response(SYNTHETIC_VALUES)
        client = _connected(_chunks(resp, 20))
        regs = await client.read_registers(7, 45)
        assert len(regs) == 45
        assert regs[26] == 243

    asyncio.run(run())


def test_push_for_other_block_censused_request_unaffected() -> None:
    """AC-4: a block-1001 push during a pending base read is census-logged
    at INFO, does NOT complete or fail the request; a later start-7 block
    still completes it.

    History (c8d8260): the push was dropped by the FC gate and the later
    matching frame was dropped too — AquaTempTimeoutError 'got 0 bytes'.
    """

    async def run() -> None:
        push = bytes.fromhex(FRAME_BLOCK_1001)
        mine = _build_block_frame(7, 45, SYNTHETIC_VALUES)
        client = _connected([push, mine])  # two notifications
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            with _capture_logs() as cap:
                regs = await client.read_registers(7, 45)
        finally:
            const.REQUEST_TIMEOUT = old
        assert len(regs) == 45
        assert regs[26] == 243
        info_msgs = [msg for lvl, msg in cap.records if lvl >= logging.INFO]
        census = [m for m in info_msgs if "block push observed" in m]
        assert census, f"no census INFO line logged; got {info_msgs}"
        assert any(
            "block push observed: start=1001 qty=45 len=99 crc=OK" in m
            and "waiting for start 7" in m
            and "; t+" in m  # spec 009 FR-1 census timing (contract 009 §3)
            for m in census
        ), census

    asyncio.run(run())


def test_timeout_message_names_received_frames() -> None:
    """AC-5: pushes only — the timeout message reports the census
    (12 frames / 1188 bytes, per-start block counts, first/last t+
    offsets, 'none for start 7'), never a bare 'got 0 bytes' (research
    008 §5 Q3). Grammar updated by spec 009 FR-1 (NFR-6, contract 009
    §3): counts go from sorted-unique starts to `startxN`, and the
    first/last offsets are added — the semantic properties stay."""

    async def run() -> None:
        blocks = [1001, 1046, 1091, 1136, 2001, 2046]
        frames = [
            bytes.fromhex(FRAME_BLOCK_1001),
            bytes.fromhex(FRAME_BLOCK_1046),
            _build_block_frame(1091),
            _build_block_frame(1136),
            _build_block_frame(2001),
            _build_block_frame(2046),
        ]
        pushes = frames + frames  # 2 full cycles = the 12 production drops
        assert len(pushes) == 12 and all(len(f) == 99 for f in pushes)
        client = _connected(pushes)
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError as err:
            msg = str(err)
        else:
            raise AssertionError("pushes only did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        assert "received 12 frame(s) / 1188 bytes" in msg, msg
        # Spec 009 FR-1 grammar (contract 009 §3): per-start counts —
        # 12 pushes = 2 full cycles of the app's six canonical blocks.
        assert "blocks seen: 1001x2, 1046x2, 1091x2, 1136x2, 2001x2, 2046x2" in msg, msg
        assert "first t+" in msg and "last t+" in msg, msg
        assert "none for start 7" in msg, msg
        assert "got 0 bytes" not in msg, msg

    asyncio.run(run())


def test_fragmented_start7_block_reassembles() -> None:
    """AC-7: a start-7 block frame fragmented into 20-byte chunks (MTU-23
    case, research 008 §5 Q4) reassembles to 99 bytes and completes.

    History (c8d8260): the first chunk already tripped the FC gate; every
    later chunk was dropped as non-Modbus — 'got 0 bytes'.
    """

    async def run() -> None:
        frame = _build_block_frame(7, 45, SYNTHETIC_VALUES)
        parts = _chunks(frame, 20)
        assert [len(p) for p in parts] == [20, 20, 20, 20, 19]
        client = _connected(parts)
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            regs = await client.read_registers(7, 45)
        finally:
            const.REQUEST_TIMEOUT = old
        assert len(regs) == 45
        assert regs[26] == 243
        assert regs[27] == -9990

    asyncio.run(run())


def test_write_echo_completes() -> None:
    """AC-6: the 8-byte write echo still completes write_registers."""

    async def run() -> None:
        client = _connected([_build_echo(8, 1)])
        await client.write_registers(8, [1])
        assert client.sent[0].hex() == "631000080001020001d7ba"

    asyncio.run(run())


def test_push_before_echo_discarded_echo_completes() -> None:
    """AC-6: a block push arriving during the echo wait is discarded
    (census-logged) without breaking the wait; the echo still completes."""

    async def run() -> None:
        push = bytes.fromhex(FRAME_BLOCK_1001)
        echo = _build_echo(8, 1)
        client = _connected([push, echo])
        with _capture_logs() as cap:
            await client.write_registers(8, [1])
        info_msgs = [msg for lvl, msg in cap.records if lvl >= logging.INFO]
        assert any("block push observed: start=1001" in m for m in info_msgs), (
            "the push must be census-logged while the echo is pending"
        )

    asyncio.run(run())


def test_pathological_echo_self_heals_at_silence() -> None:
    """AC-6 / contract 008-B §4.2: an 8-byte echo whose CRC low byte equals
    2*qty is misread by the block boundary rule (declared length 11), but
    the silence timer completes it — the buffer is CRC-valid and matches
    the echo spec."""

    async def run() -> None:
        assert PATHO_ECHO[6] == 0x02 == 2 * 1
        client = _connected([PATHO_ECHO])
        old_silence = const.SILENCE_TIMEOUT
        const.SILENCE_TIMEOUT = 0.05
        try:
            await client.write_registers(PATHO_ECHO_START, [1])
        finally:
            const.SILENCE_TIMEOUT = old_silence

    asyncio.run(run())


def test_pathological_echo_self_heals_at_deadline() -> None:
    """Contract 008-B §4.3: same pathological echo, but with the silence
    timer effectively disabled — the request deadline accepts the
    CRC-valid matching buffer."""

    async def run() -> None:
        client = _connected([PATHO_ECHO])
        old_silence, old_echo = const.SILENCE_TIMEOUT, const.WRITE_ECHO_TIMEOUT
        const.SILENCE_TIMEOUT = 5.0    # must NOT fire before the deadline
        const.WRITE_ECHO_TIMEOUT = 0.25
        try:
            await client.write_registers(PATHO_ECHO_START, [1])
        finally:
            const.SILENCE_TIMEOUT, const.WRITE_ECHO_TIMEOUT = old_silence, old_echo

    asyncio.run(run())


def test_exception_fails_fast() -> None:
    """An exception frame (fc|0x80) still fails the request immediately
    with AquaTempResponseError carrying the code (unchanged semantics)."""

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


def test_idle_census_one_info_per_connect() -> None:
    """FR-4 / contract 008-B §5: idle pushes census at INFO once per
    connect (the periodic-vs-triggered evidence, research 008 §7 E2),
    then at DEBUG; a later request still works."""

    async def run() -> None:
        client = _connected()
        push1 = bytes.fromhex(FRAME_BLOCK_1001)
        push2 = bytes.fromhex(FRAME_BLOCK_1046)
        with _capture_logs() as cap:
            client._on_notify(bytearray(push1))
            client._on_notify(bytearray(push2))
        by_msg = cap.records
        first = [m for lvl, m in by_msg if "block push observed while idle" in m]
        assert first, "no idle census line logged"
        levels = [lvl for lvl, m in by_msg if "block push observed while idle" in m]
        assert levels[0] == logging.INFO, "first idle push must census at INFO"
        assert logging.INFO not in levels[1:], "only ONE idle census INFO per connect"
        assert any(lvl == logging.DEBUG for lvl in levels[1:]), (
            "subsequent idle pushes census at DEBUG"
        )
        # the idle pushes must not poison the buffer or the next request
        assert not client._rx
        client.chunks = [_build_response(SYNTHETIC_VALUES)]
        regs = await client.read_registers(7, 45)
        assert regs[26] == 243

    asyncio.run(run())


def main() -> int:
    tests = [
        test_read_completes_on_pushed_start7_block,
        test_read_completes_on_real_block_1001_frame,
        test_fc03_response_still_completes,
        test_push_for_other_block_censused_request_unaffected,
        test_timeout_message_names_received_frames,
        test_fragmented_start7_block_reassembles,
        test_write_echo_completes,
        test_push_before_echo_discarded_echo_completes,
        test_pathological_echo_self_heals_at_silence,
        test_pathological_echo_self_heals_at_deadline,
        test_exception_fails_fast,
        test_idle_census_one_info_per_connect,
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
    print(f"PASSED: {len(tests)}/{len(tests)} reassembly-matching tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())