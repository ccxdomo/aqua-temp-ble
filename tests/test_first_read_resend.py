#!/usr/bin/env python3
"""Bounded re-send tests (spec 009 FR-2 / AC-2, contract 009 §5).

The config-flow connection test's first read re-sends the IDENTICAL
vendor-proven frame `63030007002d3c54` on the app's 2 s cadence
(resend_every=2.0, resend_max=3 → sends at t+0/2/4/6 s, all inside the
UNCHANGED 8.0 s request window — no timeout constant changes). The
re-send is the cheapest production experiment for C2/C5 ("answers only
after repeats / request-during-push collision", research 009 §1.3/§7)
and a plausible fix for free if the unit answers the Nth request.

Red-first: on ce41aa5 `read_registers` takes no re-send parameters
(TypeError on the kwargs) and exactly one write goes out — every
re-send assertion below fails until FR-2 lands.

Pinned boundaries (contract 009 §5):
- 4 writes at the 2.0 s cadence with the parameters, each logged at
  INFO as `first-read re-send N/3: 63030007002d3c54`;
- exactly ONE write without the parameters (coordinator parity, NFR-3);
- an answer arriving after any re-send completes the request ONCE and
  stops the loop (single-flight untouched, no writes past completion);
- the timeout message and the request budget are unchanged (the re-send
  never extends the window);
- a disconnect mid-window cancels the loop — no leaked re-send task.

The 2.0 s cadence is asserted from RECORDED sleeps (the connect-parity
test pattern: asyncio.sleep is recorded, never waited) so the test costs
no wall-clock; the completion/leak tests use real fast timers.

Run standalone:

    python3 tests/test_first_read_resend.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
import time
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
BASE_READ_HEX = "63030007002d3c54"  # the vendor-proven base read [P]

# Synthetic base-block values for the matching start-7 block frame (the
# production first-read shape, research 008 §3).
SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: -9990, 28: -125, 51: 2040}


def _build_block_frame(
    start: int, count: int = 45, values: dict[int, int] | None = None
) -> bytes:
    """Build the unit's block frame [P] (research 008 §2.1)."""
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


class FakeBleakClient:
    """Minimal stand-in for a connected bleak client."""

    is_connected = True

    async def write_gatt_char(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass


class ResendTestClient(aqua_client.AquaTempBleClient):
    """Transport seam override: records writes; optionally feeds an
    answer frame at the Nth write (the "unit answers the Nth request"
    case, research 009 §1.3)."""

    def __init__(self, answer: bytes | None = None, answer_on_write: int = 1) -> None:
        super().__init__(ADDRESS)
        self.sent: list[bytes] = []
        self.answer = answer
        self.answer_on_write = answer_on_write

    async def _write_frame(self, frame: bytes) -> None:
        self.sent.append(bytes(frame))
        if self.answer is not None and len(self.sent) == self.answer_on_write:
            self._on_notify(bytearray(self.answer))


def _connected(answer: bytes | None = None,
                answer_on_write: int = 1) -> ResendTestClient:
    client = ResendTestClient(answer, answer_on_write)
    client._client = FakeBleakClient()  # mark as connected
    return client


class _RecordSleeps:
    """Patch asyncio.sleep to record delays without waiting (the
    connect-parity pattern) — the 2 s cadence is asserted from records."""

    def __enter__(self) -> "_RecordSleeps":
        self.delays: list[float] = []
        self._real = asyncio.sleep

        async def recording_sleep(delay, *_args, **_kwargs):
            self.delays.append(delay)

        asyncio.sleep = recording_sleep
        return self

    def __exit__(self, *_exc) -> None:
        asyncio.sleep = self._real


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


def _leaked_resend_tasks() -> list[asyncio.Task]:
    """Running tasks whose coroutine is the re-send loop (leak guard)."""
    return [
        t for t in asyncio.all_tasks()
        if "_resend_loop" in getattr(t.get_coro(), "__qualname__", "")
    ]


# --------------------------------------------------------------------- tests
def test_resend_cadence_payload_and_logging() -> None:
    """AC-2 — THE vector: with resend_every=2.0, resend_max=3 the fake
    write characteristic receives EXACTLY 4 writes (t+0/2/4/6 s: the
    initial send + 3 recorded 2.0 s sleeps), all payloads identical to
    the vendor frame, each re-send logged at INFO (contract 009 §5).

    Red-first on ce41aa5: read_registers takes no re-send kwargs.
    """
    answer = _build_block_frame(7, 45, SYNTHETIC_VALUES)

    async def run() -> None:
        client = _connected()  # nothing answers -> deadline path
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        try:
            with _capture_logs() as cap, _RecordSleeps() as sleeps:
                await client.read_registers(7, 45, resend_every=2.0, resend_max=3)
        except aqua_client.AquaTempTimeoutError as err:
            msg = str(err)
        else:
            raise AssertionError("no answer did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        assert len(client.sent) == 4, [f.hex() for f in client.sent]
        assert all(f.hex() == BASE_READ_HEX for f in client.sent), client.sent
        # sends at t+0/2/4/6: three 2.0 s sleeps precede the re-sends
        assert sleeps.delays == [2.0, 2.0, 2.0], sleeps.delays
        infos = [m for lvl, m in cap.records if lvl >= logging.INFO]
        assert "first-read re-send 1/3: " + BASE_READ_HEX in infos, infos
        assert "first-read re-send 2/3: " + BASE_READ_HEX in infos, infos
        assert "first-read re-send 3/3: " + BASE_READ_HEX in infos, infos
        # the timeout message keeps its contract-009 shape under re-send
        assert "no matching response within 0.2s" in msg, msg
        assert "none for start 7" in msg, msg

    asyncio.run(run())


def test_no_params_exactly_one_write() -> None:
    """AC-2 / NFR-3 coordinator parity: WITHOUT the parameters exactly
    ONE write goes out (the polling path stays single-shot; the re-send
    is opt-in, contract 009 §5) and no re-send INFO line is logged."""
    answer = _build_block_frame(7, 45, SYNTHETIC_VALUES)

    async def run() -> None:
        client = _connected(answer, answer_on_write=1)
        with _capture_logs() as cap:
            regs = await client.read_registers(7, 45)
        assert len(client.sent) == 1, [f.hex() for f in client.sent]
        assert client.sent[0].hex() == BASE_READ_HEX
        assert regs[26] == 243
        assert not any(
            "first-read re-send" in m for lvl, m in cap.records
        ), cap.records

    asyncio.run(run())


def test_answer_after_second_send_completes_once() -> None:
    """AC-2: an answer arriving after the 2nd send completes the request
    ONCE and the loop stops — no writes past completion, no leaked
    task (contract 009 §5: the loop is cancelled in the request's
    finally)."""
    answer = _build_block_frame(7, 45, SYNTHETIC_VALUES)

    async def run() -> None:
        client = _connected(answer, answer_on_write=2)
        regs = await client.read_registers(7, 45, resend_every=0.05, resend_max=3)
        assert len(regs) == 45
        assert regs[26] == 243
        await asyncio.sleep(0.01)  # let any cancelled loop finalize
        assert len(client.sent) == 2, [f.hex() for f in client.sent]
        assert not _leaked_resend_tasks(), _leaked_resend_tasks()

    asyncio.run(run())


def test_timeout_budget_unchanged_with_resend() -> None:
    """AC-2: when nothing arrives the request dies at the SAME deadline
    — the re-send loop is cancelled inside the window and never extends
    the wait (NFR-2: no timeout constant changes); the timeout message
    keeps its shape."""
    async def run() -> None:
        client = _connected()
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 0.25
        t0 = time.monotonic()
        try:
            with _capture_logs() as cap:
                await client.read_registers(7, 45, resend_every=2.0, resend_max=3)
        except aqua_client.AquaTempTimeoutError as err:
            msg = str(err)
        else:
            raise AssertionError("no answer did not time out")
        finally:
            const.REQUEST_TIMEOUT = old
        elapsed = time.monotonic() - t0
        # the window bounded the wait (0.25 s), not the 6 s of re-sends
        assert elapsed < 1.0, elapsed
        assert "no matching response within 0.2s" in msg, msg
        assert "none for start 7" in msg, msg
        await asyncio.sleep(0.01)
        assert not _leaked_resend_tasks(), _leaked_resend_tasks()

    asyncio.run(run())


def test_disconnect_mid_window_cancels_resend() -> None:
    """AC-2 / contract 009 §5: a disconnect mid-window fails the pending
    request, the finally cancels the re-send loop — no leaked task, no
    writes past the disconnect."""
    async def run() -> None:
        client = _connected()
        old = const.REQUEST_TIMEOUT
        const.REQUEST_TIMEOUT = 5.0  # long window: disconnect lands mid-flight
        try:
            task = asyncio.create_task(
                client.read_registers(7, 45, resend_every=0.5, resend_max=10)
            )
            await asyncio.sleep(0.1)
            await client.disconnect()
            try:
                await task
            except aqua_client.AquaTempError:
                pass
            else:
                raise AssertionError("disconnect did not fail the request")
            await asyncio.sleep(0.05)  # let the cancelled loop finalize
            assert not _leaked_resend_tasks(), _leaked_resend_tasks()
            # the loop was cancelled inside its 0.5 s sleep — only the
            # initial write ever went out
            assert len(client.sent) == 1, [f.hex() for f in client.sent]
        finally:
            const.REQUEST_TIMEOUT = old

    asyncio.run(run())


def main() -> int:
    tests = [
        test_resend_cadence_payload_and_logging,
        test_no_params_exactly_one_write,
        test_answer_after_second_send_completes_once,
        test_timeout_budget_unchanged_with_resend,
        test_disconnect_mid_window_cancels_resend,
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
    print(f"PASSED: {len(tests)}/{len(tests)} first-read re-send tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())