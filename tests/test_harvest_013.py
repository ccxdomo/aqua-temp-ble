#!/usr/bin/env python3
"""Listen-only harvest tests (spec 013 FR-2 / AC-2, contract 013 §1).

The harvest is the zero-TX read plane: `harvest_pushes(wanted_starts,
timeout)` awaits the unit's spontaneous block pushes on the ALREADY
SUBSCRIBED notify characteristic and sends NOTHING — no frame for the
firmware's application-layer gate (H-APP-GATE, research 013 §0.4) to
drop. The core invariant (contract 013 §1.3): the fake write
characteristic receives ZERO writes.

Pinned by this file:
- harvest_pushes({2046}, 30) with a scripted 2046 push at t+5
  completes at t+5 (not at the timeout) and the fake WRITE
  characteristic received ZERO writes — the AC-2 core;
- no pushes at all → the harvest returns the (empty) partial result at
  the timeout and NEVER raises (the caller decides);
- a partial arrival (one of two wanted starts) returns the partial
  capture at the timeout;
- the default wanted set {1001, 1046, 2001, 2046} (const
  PUSH_HARVEST_STARTS, the four observed starts [P]) completes on the
  pinned production frames.

Red-first on 1ac276d: harvest_pushes does not exist — every test below
fails until FR-2 lands.

Run standalone:

    python3 tests/test_harvest_013.py

No real Bluetooth and no real Home Assistant required (a fake bleak
module is installed if bleak is missing).
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
    # Fake bleak (the census-test pattern): the transport seam is never
    # used for real here — the harvest must not transmit at all.
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

ADDRESS = "AA:BB:CC:DD:EE:FF"

# The pinned production frames (research 013 §2 — the modbus self-test
# §10 vectors).
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)
FRAME_BLOCK_2046_PUSH1 = (
    "631007fe002d5a7ffd008c008c008c00870096" + "00" * 78 + "f49b"
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


class StepClock:
    """Scripted `_now` seam (contract 007 §4): returns .value."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingBleakClient:
    """Connected bleak stand-in that RECORDS every write (the zero-TX
    gate: the harvest must leave this list empty — contract 013 §1.3)."""

    is_connected = True

    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes, bool]] = []

    async def write_gatt_char(self, uuid, data, response=False) -> None:
        self.writes.append((uuid, bytes(data), bool(response)))

    async def disconnect(self) -> None:  # pragma: no cover - placeholder
        pass


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


def _connected(bleak_client: RecordingBleakClient | None = None) -> aqua_client.AquaTempBleClient:
    """A connected real client over the recording bleak stand-in — the
    real _write_frame is in play, so any transmission would be recorded
    (the AC-2 zero-TX gate)."""
    client = aqua_client.AquaTempBleClient(ADDRESS)
    client._client = bleak_client or RecordingBleakClient()
    return client


# --------------------------------------------------------------------- tests
def test_harvest_zero_tx_completes_on_wanted_push() -> None:
    """AC-2 core: harvest_pushes({2046}, 30) with a scripted 2046 push
    at t+5 completes at t+5 — not at the timeout — and the fake WRITE
    characteristic received ZERO writes (contract 013 §1.3)."""
    clock = StepClock(1000.0)

    async def run() -> tuple[aqua_client.AquaTempBleClient, dict, RecordingBleakClient]:
        bleak_client = RecordingBleakClient()
        client = _connected(bleak_client)

        async def push_at_t_plus_5() -> None:
            await asyncio.sleep(0.05)  # let the harvest enter its listen loop
            clock.value = 1005.0  # t+5 (the fake clock seam)
            client._on_notify(bytearray(bytes.fromhex(FRAME_BLOCK_2046_PUSH1)))

        task = asyncio.create_task(push_at_t_plus_5())
        caps = await client.harvest_pushes({2046}, 30.0)
        await task
        return client, caps, bleak_client

    with _FakeClockPatch(clock):
        client, caps, bleak_client = asyncio.run(run())

    assert 2046 in caps, caps
    assert caps[2046].registers[2046] == 32765
    assert caps[2046].registers[2047] == 140
    # completed AT t+5 (the clock seam), not at the t+30 deadline
    assert clock.value == 1005.0
    # THE zero-TX gate: nothing was ever transmitted
    assert bleak_client.writes == [], bleak_client.writes


def test_harvest_no_pushes_returns_partials_never_raises() -> None:
    """AC-2: with no notifications at all, the harvest returns the
    (empty) partial result at the timeout — it NEVER raises (the caller
    decides what a partial means)."""
    async def run() -> dict:
        client = _connected()
        return await client.harvest_pushes({2046}, 0.1)  # real clock, short window

    caps = asyncio.run(run())
    assert caps == {}, caps


def test_harvest_partial_results_at_timeout() -> None:
    """AC-2: one of two wanted starts arrives → the harvest returns the
    partial capture (the one start) at the timeout, still never raises."""
    async def run() -> dict:
        bleak_client = RecordingBleakClient()
        client = _connected(bleak_client)

        async def push_one() -> None:
            await asyncio.sleep(0.02)
            client._on_notify(bytearray(bytes.fromhex(FRAME_BLOCK_2046_PUSH1)))

        task = asyncio.create_task(push_one())
        caps = await client.harvest_pushes({2046, 1001}, 0.15)
        await task
        assert bleak_client.writes == []  # zero TX on the partial path too
        return caps

    caps = asyncio.run(run())
    assert set(caps) == {2046}, caps  # 1001 never arrived -> partial


def test_harvest_default_wanted_set_all_four_starts() -> None:
    """AC-2: the default call (no args) wants {1001, 1046, 2001, 2046}
    (const.PUSH_HARVEST_STARTS — the four observed starts [P]) and
    completes on the pinned frames without transmitting anything."""
    assert const.PUSH_HARVEST_STARTS == frozenset({1001, 1046, 2001, 2046})
    assert const.PUSH_HARVEST_TIMEOUT == 30.0

    async def run() -> tuple[dict, RecordingBleakClient]:
        bleak_client = RecordingBleakClient()
        client = _connected(bleak_client)
        # the pushes arrive spontaneously (idle path) BEFORE the harvest
        # is even called — the store already holds them; the harvest
        # returns as soon as every wanted start is present.
        for frame in (
            bytes.fromhex(FRAME_BLOCK_1001),
            _build_block_frame(1046),
            _build_block_frame(2001),
            bytes.fromhex(FRAME_BLOCK_2046_PUSH1),
        ):
            client._on_notify(bytearray(frame))
        caps = await client.harvest_pushes()  # defaults
        return caps, bleak_client

    caps, bleak_client = asyncio.run(run())
    assert set(caps) == {1001, 1046, 2001, 2046}, sorted(caps)
    assert caps[1001].registers[1021] == 1050  # H04, the panel-match anchor
    assert bleak_client.writes == [], bleak_client.writes


def main() -> int:
    tests = [
        test_harvest_zero_tx_completes_on_wanted_push,
        test_harvest_no_pushes_returns_partials_never_raises,
        test_harvest_partial_results_at_timeout,
        test_harvest_default_wanted_set_all_four_starts,
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
    print(f"PASSED: {len(tests)}/{len(tests)} harvest tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())