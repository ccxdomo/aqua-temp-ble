#!/usr/bin/env python3
"""Pushed-block store tests (spec 013 FR-1 / AC-1, contract 013 §2/§3).

The census counts the unit's pushes but DISCARDS the 90 data bytes
today (research 013 §5.2). Spec 013 adds a per-connect pushed-block
store: every CRC-valid block frame (idle or in-flight) is decoded via
parse_block_response (start/qty echoed BY the frame) and kept as
{start: PushCapture} — latest window wins, first/last-seen stamped,
per-start counts kept.

Pinned by this file:
- the four production frames (1001, 1046, 2001-mock, 2046×2) land in
  the store with the exact register tables (spot: 1021=1050, 1046=290,
  1056=-70, 2046=32765, 2047 140→145 latest-wins, count=2);
- the FIRST push per distinct start logs the complete 99-byte hex at
  WARNING (contract 013 §2.1: `block push captured: start=… qty=…
  len=… crc=OK hex=…`, ≤ 4 per connect, the U-1 capture mechanism);
- repeat pushes keep the 008/009 census grammar byte-identical (INFO);
- the idle-window summary moved INFO → WARNING (contract 013 §2.2,
  the only level amendment — message byte-identical);
- a 5×20-byte-chunked block frame arriving WHILE IDLE lands in the
  store too (FR-2 invariant: the idle path gains the accumulate/
  boundary loop — today it is per-chunk and loses fragmented frames).

Red-first on 1ac276d: no store, no capture line, the idle summary is
INFO, the idle path loses fragmented frames — every assertion below
fails until FR-1 lands.

Run standalone:

    python3 tests/test_push_store_013.py

No real Bluetooth and no real Home Assistant required (a fake bleak
module is installed if bleak is missing; the `_now` seam is a step
clock, the test_census_009 pattern).
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
    # Fake bleak (the census-test pattern): the transport seam is
    # faked at the bleak-client level, never used for real.
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

# The four proven production frames (research 013 §2, CRC-verified —
# the modbus self-test §10 vectors). Block 2001 is data-underdetermined
# [V] (research 008 §2.4); a CRC-valid mock frame carries the known
# value islands for the store test.
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)
FRAME_BLOCK_1046 = (
    "63100416002d5a01220005000500960190010e000500050064015effba00820028000800030000000000000000000000000000010e00000000000000000000000000000000000000000000000000000000000000000000000000000000000000008576"
)
FRAME_BLOCK_2046_PUSH1 = (
    "631007fe002d5a7ffd008c008c008c00870096" + "00" * 78 + "f49b"
)
FRAME_BLOCK_2046_PUSH2 = (
    "631007fe002d5a7ffd0091008c008c00870096" + "00" * 78 + "a565"
)


def _build_block_frame(
    start: int, count: int = 45, values: dict[int, int] | None = None
) -> bytes:
    """The unit's block frame [P] (research 008 §2.1)."""
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


# The 2001 mock: a CRC-valid block frame for the data-underdetermined
# block (the known value islands, research 008 §2.4 — content is [V]).
FRAME_BLOCK_2001_MOCK = _build_block_frame(2001, 45, {2001: 1, 2014: 13, 2017: 168})


# ---------------------------------------------------------------------------
# Fakes: step clock + clockfed client (the test_census_009 pattern)
# ---------------------------------------------------------------------------


class StepClock:
    """Scripted `_now` seam (contract 007 §4): returns .value."""

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
    """The test_census_009 scripted transport: `_write_frame` records
    the frame, then feeds every scripted notification chunk, advancing
    the fake clock by `gap` before each feed."""

    def __init__(self, clock: StepClock, feed: list[bytes] | None = None,
                 gap: float = 0.4) -> None:
        super().__init__(ADDRESS)
        self.clock = clock
        self.feed = list(feed or [])
        self.gap = gap
        self.sent: list[bytes] = []

    async def _write_frame(self, frame: bytes, *, force_response: bool = False) -> None:
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
    """Patch aqua_client._now with a StepClock (contract 007 §4)."""

    def __init__(self, clock: StepClock) -> None:
        self.clock = clock
        self._real = aqua_client._now

    def __enter__(self) -> StepClock:
        aqua_client._now = self.clock
        return self.clock

    def __exit__(self, *_exc) -> None:
        aqua_client._now = self._real


class _LogCap(logging.Handler):
    """Capture records emitted on the aqua_client logger."""

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
def test_four_pinned_pushes_land_in_store_with_capture_lines() -> None:
    """AC-1 — THE vector run: the four pinned frames (1001, 1046,
    2001-mock, 2046×2) fake-notified during a pending read land in the
    store with the exact register tables; exactly 4 WARNING capture
    lines (first per distinct start, full 198-char hex); repeats keep
    the 008/009 census grammar at INFO (byte-identical)."""
    clock = StepClock(1000.0)
    feed = [
        bytes.fromhex(FRAME_BLOCK_1001),
        bytes.fromhex(FRAME_BLOCK_1046),
        FRAME_BLOCK_2001_MOCK,
        bytes.fromhex(FRAME_BLOCK_2046_PUSH1),
        bytes.fromhex(FRAME_BLOCK_2046_PUSH2),
    ]
    assert all(len(f) == 99 and modbus.validate_crc(f) for f in feed)

    async def run() -> tuple[object, _LogCap]:
        client = _connected_clocked(clock, feed=feed, gap=0.4)
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            with _capture_logs() as cap:
                await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass  # the expected no-answer world (none for start 7)
        else:
            raise AssertionError("pushes only did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        return client, cap

    with _FakeClockPatch(clock):
        client, cap = asyncio.run(run())

    # --- the store: exact register tables (data-model 013 §2-§5) ---
    caps = client.pushed_blocks
    assert set(caps) == {1001, 1046, 2001, 2046}, sorted(caps)
    assert caps[1001].registers[1012] == 1
    assert caps[1001].registers[1021] == 1050     # H04 105.0 degC [P panel]
    assert caps[1001].count == 1
    assert caps[1046].registers[1046] == 290     # R ladder 29.0
    assert caps[1046].registers[1056] == -70      # -7.0
    assert caps[1046].count == 1
    assert caps[2001].registers[2017] == 168      # the known 16.8 island
    assert caps[2046].registers[2046] == 32765    # the T01 sentinel 0x7FFD
    assert caps[2046].registers[2047] == 145, (
        "latest-wins failed: 2047 must be 145 (push 2), not 140"
    )
    assert caps[2046].registers[2051] == 150
    assert caps[2046].count == 2, caps[2046].count
    assert caps[2046].first_seen < caps[2046].last_seen  # two pushes, two stamps
    # qty/length echo the frame (parse_block_response, contract 013 §3.1)
    assert caps[2046].qty == 45 and caps[2046].length == 99

    # --- the capture lines: exactly 4 WARNING, one per distinct start ---
    capture = [m for lvl, m in cap.records if m.startswith("block push captured:")]
    assert len(capture) == 4, capture
    assert all(
        lvl == logging.WARNING
        for lvl, m in cap.records
        if m.startswith("block push captured:")
    ), [(lvl, m) for lvl, m in cap.records if m.startswith("block push captured:")]
    starts_in_order = [line.split("start=")[1].split()[0] for line in capture]
    assert starts_in_order == ["1001", "1046", "2001", "2046"], capture
    for line in capture:
        # contract 013 §2.1 byte-exact: start/qty/len/crc=OK + the FULL hex
        assert " qty=45 len=99 crc=OK hex=" in line, line
        assert len(line.split("hex=")[1]) == 198, "full 99-byte hex expected"
    hexes = [line.split("hex=")[1] for line in capture]
    assert hexes[0] == FRAME_BLOCK_1001
    assert hexes[3] == FRAME_BLOCK_2046_PUSH1  # the FIRST push of 2046

    # --- repeat census lines: the 008/009 grammar byte-identical;
    #     level DEBUG per contract 018-B (spec 018 FR-4) ---
    assert any(
        lvl == logging.DEBUG
        and "block push observed: start=2046 qty=45 len=99 crc=OK" in m
        and "(waiting for start 7; t+2.00s)" in m
        for lvl, m in cap.records
    ), [m for lvl, m in cap.records if "block push observed" in m]
    # the 2046 repeat (push 2, t+2.00s) is INFO census — no second WARNING
    repeats = [m for m in capture if m.startswith("block push captured: start=2046")]
    assert len(repeats) == 1, capture

    # --- the idle summary is now WARNING (contract 013 §2.2, the only
    #     level amendment; message byte-identical to contract 009 §3) ---
    summaries = [
        (lvl, m) for lvl, m in cap.records
        if "idle window before first request" in m
    ]
    assert summaries == [
        (logging.WARNING, "idle window before first request: 0 block push(es)")
    ], summaries


def test_capture_line_is_first_per_start_not_first_per_frame() -> None:
    """AC-1: the WARNING hex line fires ONCE per distinct start per
    connect — a start seen twice logs its hex only on the first push
    (bounded: <= 4 lines per connect on the observed unit)."""
    clock = StepClock(2000.0)
    feed = [
        bytes.fromhex(FRAME_BLOCK_2046_PUSH1),
        bytes.fromhex(FRAME_BLOCK_2046_PUSH1),  # identical repeat
    ]

    async def run() -> None:
        client = _connected_clocked(clock, feed=feed, gap=0.4)
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass
        finally:
            const.REQUEST_TIMEOUT = old
        return client

    with _FakeClockPatch(clock):
        client = asyncio.run(run())
    caps = client.pushed_blocks
    assert set(caps) == {2046}
    assert caps[2046].count == 2
    assert caps[2046].registers[2047] == 140  # identical repeat -> same window


def test_chunked_idle_push_lands_in_store() -> None:
    """AC-1 (FR-2 invariant): a 5×20-byte-chunked block frame arriving
    WHILE IDLE (settle window, no request pending) lands in the store —
    today's idle path is per-chunk and would lose the fragmented frame;
    the accumulate/boundary loop moves into the idle path."""
    clock = StepClock(100.0)
    frame = bytes.fromhex(FRAME_BLOCK_2046_PUSH1)

    async def run() -> None:
        client = _connected_clocked(clock, feed=[], gap=0.1)
        client._t_subscribe = 100.0  # the connect() stamp (plan 009 §2.2)
        for i in range(0, len(frame), 20):  # 5 chunks: 4×20 + 19 bytes
            clock.value += 0.1
            client._on_notify(bytearray(frame[i : i + 20]))
        return client

    with _FakeClockPatch(clock):
        client = asyncio.run(run())
    caps = client.pushed_blocks
    assert 2046 in caps, "fragmented idle push lost — the store missed it"
    assert caps[2046].registers[2046] == 32765
    assert caps[2046].registers[2047] == 140
    assert caps[2046].count == 1


def test_store_resets_on_disconnect() -> None:
    """AC-1 (contract 013 §3.1): the store is per-connect state — it
    resets with the census state on disconnect (and connect)."""
    clock = StepClock(300.0)

    async def run() -> None:
        client = _connected_clocked(clock, feed=[], gap=0.1)
        client._on_notify(bytearray(bytes.fromhex(FRAME_BLOCK_2046_PUSH1)))
        assert 2046 in client.pushed_blocks
        await client.disconnect()
        return client

    with _FakeClockPatch(clock):
        client = asyncio.run(run())
    assert client.pushed_blocks == {}


def main() -> int:
    tests = [
        test_four_pinned_pushes_land_in_store_with_capture_lines,
        test_capture_line_is_first_per_start_not_first_per_frame,
        test_chunked_idle_push_lands_in_store,
        test_store_resets_on_disconnect,
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
    print(f"PASSED: {len(tests)}/{len(tests)} push store tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())