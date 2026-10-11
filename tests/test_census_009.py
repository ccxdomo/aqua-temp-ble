#!/usr/bin/env python3
"""Census upgrade tests (spec 009 FR-1 / AC-1, contract 009 §3).

The spec-008 census named WHAT it saw; spec 009 adds the WHEN (research
009 §0.3/§1.1):

- per-request census lines gain a request-relative `t+%.2fs` offset
  (the push timeline inside the request window);
- the timeout message gains per-start counts (`1001x3, …`) and
  first/last census offsets — the 4-block-cycle model (research 009
  §0.3) becomes testable from one run;
- a NEW idle-window summary (INFO, exactly once per connection, at the
  first request, BEFORE the TX line) reports the pushes seen between
  subscribe and the first request — the E2 push-onset evidence
  (research 009 §1.1, quickstart 009 §0/§1).

Red-first: on ce41aa5 the timeout message prints sorted UNIQUE starts
("blocks seen: 1001, 1046, …"), census lines carry no `t+`, and no idle
summary exists — every assertion below fails until FR-1 lands.

The spec-008 invariants are re-pinned against the new grammar (NFR-6):
counters stay honest totals (bytes still counted for CRC-invalid
frames), nothing is dropped silently, the census is never fatal
(contract 008-B §5/§6, carried by contract 009 §3).

Run standalone:

    python3 tests/test_census_009.py

No real Bluetooth and no Home Assistant required (a fake bleak module is
installed if bleak is missing). The `_now` seam (contract 007 §4) is
replaced by a step clock so every t+ offset is deterministic.
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

# The proven production frame for block 1001-1045 (research 008 §2.2,
# CRC 0x3A56) — used as-is; the other three production starts are built
# synthetically (CRC-valid by construction).
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)

# Synthetic base-block values for the matching start-7 frame (research
# 008 §3 — the production first-read shape).
SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: -9990, 28: -125, 51: 2040}


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


def _build_fc03_response(values: dict[int, int], start: int = 7, count: int = 45) -> bytes:
    """Synthetic 95-byte FC03 response."""
    payload = bytearray()
    for addr in range(start, start + count):
        payload += (values.get(addr, 0) & 0xFFFF).to_bytes(2, "big")
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, len(payload)]) + bytes(payload)
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


# ---------------------------------------------------------------------------
# Fakes: step clock (the _now seam, contract 007 §4) + a scripted transport
# whose _write_frame advances the fake clock between scripted feeds, so
# every t+ offset in the census is deterministic.
# ---------------------------------------------------------------------------


class StepClock:
    """Scripted `_now` seam: returns .value; tests advance it explicitly."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeBleakClient:
    """Minimal stand-in for a connected bleak client."""

    is_connected = True

    async def write_gatt_char(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass


class ClockfedClient(aqua_client.AquaTempBleClient):
    """ScriptedClient twin with a deterministic clock.

    `_write_frame` records the frame, then feeds every scripted
    notification chunk, advancing the fake clock by `gap` before each
    feed — the census offsets become exact (t+gap, t+2*gap, …).
    """

    def __init__(self, clock: StepClock, feed: list[bytes] | None = None,
                 gap: float = 0.4) -> None:
        super().__init__(ADDRESS)
        self.clock = clock
        self.feed = list(feed or [])
        self.gap = gap
        self.sent: list[bytes] = []

    async def _write_frame(self, frame: bytes) -> None:
        self.sent.append(bytes(frame))
        for chunk in self.feed:
            self.clock.value += self.gap
            self._on_notify(bytearray(chunk))


def _connected_clocked(clock: StepClock, feed: list[bytes] | None = None,
                       gap: float = 0.4) -> ClockfedClient:
    client = ClockfedClient(clock, feed, gap)
    client._client = FakeBleakClient()  # mark as connected
    return client


class _FakeClockPatch:
    """Patch aqua_client._now with a StepClock (contract 007 §4 — the
    ONLY clock the instrumentation reads, plan 009 §2.2)."""

    def __init__(self, clock: StepClock) -> None:
        self.clock = clock
        self._real = aqua_client._now

    def __enter__(self) -> StepClock:
        aqua_client._now = self.clock
        return self.clock

    def __exit__(self, *_exc) -> None:
        aqua_client._now = self._real


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


def _production_cycle_pushes(cycles: int = 3) -> list[bytes]:
    """The production 4-block cycle [1001, 1046, 2001, 2046] repeated
    `cycles` times (research 009 §0.3 — the model FR-1 makes testable)."""
    frames = [
        bytes.fromhex(FRAME_BLOCK_1001),
        _build_block_frame(1046),
        _build_block_frame(2001),
        _build_block_frame(2046),
    ]
    return frames * cycles


# --------------------------------------------------------------------- tests
def test_timeout_message_per_start_counts_and_offsets() -> None:
    """AC-1 — THE vector run: 3×(1001, 1046, 2001, 2046) fake-notify →
    the timeout message reports per-start counts (`1001x3`, …), the
    first/last census offsets (`first t+…, last t+…`) and `none for
    start 7` (contract 009 §3).

    Red-first on ce41aa5: the message prints sorted unique starts with
    no per-start counts and no first/last offsets.
    """
    pushes = _production_cycle_pushes(3)
    assert len(pushes) == 12 and all(len(f) == 99 for f in pushes)
    assert all(modbus.validate_crc(f) for f in pushes)
    clock = StepClock(1000.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=pushes, gap=0.4)
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
        assert "blocks seen: 1001x3, 1046x3, 2001x3, 2046x3" in msg, msg
        assert "first t+0.4s" in msg, msg
        assert "last t+4.8s" in msg, msg
        assert "none for start 7" in msg, msg
        assert "got 0 bytes" not in msg, msg

    with _FakeClockPatch(clock):
        asyncio.run(run())


def test_census_lines_carry_request_relative_t() -> None:
    """AC-1: request-window census lines carry the t+%.2fs offset
    relative to the request start (contract 009 §3 grammar):

        block push observed: start=… qty=… len=… crc=OK (waiting for
        start 7; t+0.40s)

    Red-first on ce41aa5: the census line ends at `waiting for start 7)`.
    """
    pushes = _production_cycle_pushes(1)
    clock = StepClock(2000.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=pushes, gap=0.4)
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            with _capture_logs() as cap:
                await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass
        else:
            raise AssertionError("pushes only did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        census = [
            m for lvl, m in cap.records
            if lvl >= logging.INFO and "block push observed" in m
        ]
        assert census, f"no census INFO lines: {cap.records}"
        assert any(
            "block push observed: start=1001 qty=45 len=99 crc=OK" in m
            and "(waiting for start 7; t+0.40s)" in m
            for m in census
        ), census
        # every censused push carries a t+ offset (timing evidence, E7)
        assert all("; t+" in m for m in census), census

    with _FakeClockPatch(clock):
        asyncio.run(run())


def test_idle_summary_with_settle_pushes() -> None:
    """AC-1 idle-window summary: pushes arriving while NO request is
    pending (the settle window) are summarised in exactly ONE INFO line
    at the first request — count, sorted starts, first offset relative
    to the subscribe (contract 009 §3, research 009 §1.1 E2).

    Red-first on ce41aa5: no idle summary exists.
    """
    clock = StepClock(100.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=[], gap=0.1)
        client._t_subscribe = 100.0  # the connect() stamp (plan 009 §2.2)
        settle_pushes = _production_cycle_pushes(1)
        for frame in settle_pushes:  # arrive while idle (settle window)
            clock.value += 0.1
            client._on_notify(bytearray(frame))
        client.feed = [_build_block_frame(7, 45, SYNTHETIC_VALUES)]
        with _capture_logs() as cap:
            regs = await client.read_registers(7, 45)
        assert regs[26] == 243  # the request itself still completes
        summaries = [
            m for lvl, m in cap.records
            if lvl >= logging.INFO and "idle window before first request" in m
        ]
        assert summaries == [
            "idle window before first request: 4 block push(es), "
            "starts seen: 1001, 1046, 2001, 2046, "
            "first at t+0.10s after subscribe"
        ], cap.records

    with _FakeClockPatch(clock):
        asyncio.run(run())


def test_idle_summary_zero_pushes() -> None:
    """AC-1: no idle pushes → the summary line reports exactly
    `0 block push(es)` — the absence IS the E2 datum (quickstart 009
    §0: push onset later than the settle)."""
    clock = StepClock(100.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=[_build_block_frame(7, 45)])
        with _capture_logs() as cap:
            await client.read_registers(7, 45)
        summaries = [
            m for lvl, m in cap.records
            if "idle window before first request" in m
        ]
        assert summaries == [
            "idle window before first request: 0 block push(es)"
        ], cap.records
        assert summaries[0] in [m for lvl, m in cap.records if lvl >= logging.INFO]

    with _FakeClockPatch(clock):
        asyncio.run(run())


def test_idle_summary_once_per_connection_before_tx() -> None:
    """AC-1: the summary is logged EXACTLY ONCE per connection, at the
    FIRST request, BEFORE the TX debug line (plan 009 §2.3); a second
    request in the same connection does not re-log it."""
    clock = StepClock(100.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=[], gap=0.1)
        client._t_subscribe = 100.0
        clock.value += 0.1
        client._on_notify(bytearray(bytes.fromhex(FRAME_BLOCK_1001)))
        client.feed = [_build_block_frame(7, 45)]
        with _capture_logs() as cap:
            await client.read_registers(7, 45)
            client.feed = [_build_block_frame(7, 45)]
            await client.read_registers(7, 45)
        summary_idx = [
            i for i, (lvl, m) in enumerate(cap.records)
            if "idle window before first request" in m
        ]
        assert len(summary_idx) == 1, cap.records
        tx_idx = next(
            i for i, (lvl, m) in enumerate(cap.records)
            if m.startswith("TX (")
        )
        assert summary_idx[0] < tx_idx, cap.records[:tx_idx + 1]

    with _FakeClockPatch(clock):
        asyncio.run(run())


def test_bytes_counted_for_crc_invalid_frames() -> None:
    """NFR-6 re-pin (contract 008-B §5): a CRC-invalid frame still
    counts its bytes in the honest total (research 008 §5 Q3) and is
    census-logged at DEBUG with the new `; t+%.2fs` suffix — nothing is
    dropped silently (contract 009 §3)."""
    corrupt = bytearray(bytes.fromhex(FRAME_BLOCK_1001))
    corrupt[10] ^= 0xFF
    corrupt = bytes(corrupt)
    assert not modbus.validate_crc(corrupt) and len(corrupt) == 99
    clock = StepClock(3000.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=[corrupt], gap=0.4)
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            with _capture_logs() as cap:
                await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError as err:
            msg = str(err)
        else:
            raise AssertionError("corrupt-only feed did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        # bytes are an honest TOTAL: the 99 corrupt bytes are counted
        assert "received 0 frame(s) / 99 bytes" in msg, msg
        assert "blocks seen: none" in msg, msg
        # nothing dropped silently: the corrupt frame was census-logged
        assert any(
            lvl == logging.DEBUG
            and "block push observed: start=1001 qty=45 len=99 crc=FAIL" in m
            and "; t+0.40s" in m
            for lvl, m in cap.records
        ), cap.records

    with _FakeClockPatch(clock):
        asyncio.run(run())


def test_census_never_fatal_mixed_feed_completes() -> None:
    """NFR-6 re-pin (contract 008-B §5/§6): garbage chunks, a
    CRC-invalid frame and non-matching pushes never break the census or
    the request — a later matching start-7 block still completes it
    (the census is observation, never fatal)."""
    corrupt = bytearray(_build_block_frame(1046))
    corrupt[12] ^= 0xFF
    feed = [
        b"\xab\xcd\xef",  # non-Modbus garbage chunk (dropped, bytes counted)
        bytes(corrupt),  # CRC-invalid block frame
        bytes.fromhex(FRAME_BLOCK_1001),  # non-matching push (census INFO)
        _build_block_frame(7, 45, SYNTHETIC_VALUES),  # the matching answer
    ]
    clock = StepClock(4000.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=feed, gap=0.3)
        with _capture_logs() as cap:
            regs = await client.read_registers(7, 45)
        assert len(regs) == 45
        assert regs[26] == 243
        assert regs[27] == -9990
        # the census observed everything on the way (nothing dropped
        # silently): the non-matching push has its INFO line
        assert any(
            lvl >= logging.INFO
            and "block push observed: start=1001" in m
            and "; t+0.90s" in m
            for lvl, m in cap.records
        ), cap.records

    with _FakeClockPatch(clock):
        asyncio.run(run())


def main() -> int:
    tests = [
        test_timeout_message_per_start_counts_and_offsets,
        test_census_lines_carry_request_relative_t,
        test_idle_summary_with_settle_pushes,
        test_idle_summary_zero_pushes,
        test_idle_summary_once_per_connection_before_tx,
        test_bytes_counted_for_crc_invalid_frames,
        test_census_never_fatal_mixed_feed_completes,
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
    print(f"PASSED: {len(tests)}/{len(tests)} census 009 tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())