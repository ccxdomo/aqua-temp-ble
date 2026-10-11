#!/usr/bin/env python3
"""Stage-timing instrumentation tests for connect() (spec 007).

Pin the connect-stage instrumentation acceptance criteria (contract:
specs/007-connect-speed-gap/contracts/connect-stage-timing.md):

- AC-1: a successful connect emits exactly ONE "connect summary" INFO
  line whose five stage durations (resolve, connect, notify, write-char,
  settle) are non-negative and sum to the total (contract §2.2), emitted
  just BEFORE the existing "Connected to <address> (<name>)" parity line
  (kept verbatim).
- AC-2: a failing establish_connection — BleakNotFoundError raised with
  a TimeoutError cause, the production signature (research 007 §4/§8) —
  logs "connect to <address> failed at stage connect after <e.1f> s:
  <exc_chain>" (contract §3.1) with the chain intact and NO summary
  line.
- AC-3: the attempt-window INFO line (contract §2.1) is emitted BEFORE
  the connect call and names the max_attempts value passed — plus its
  standalone twin on the raw-bleak path (const.CONNECT_TIMEOUT).

Also pins the post-connect failure line's stage selection (contract
§3.2): a start_notify failure reports stage "notify"; a characteristic
resolution failure AFTER a completed notify reports "write-char" — the
stage is derived from the completed boundaries only, never a guessed
duration for an incomplete stage (plan 007 §1.2).

Red-first: on the pre-007 code none of these lines exist (no _now seam,
no summary, no window line, no stage in the failure lines), so every
test below fails until spec 007 FR-1/FR-2 land.

Only the outer I/O seams (_resolve_device, _load_retry_connector,
_connect_with_raw_client, _resolve_write_characteristic) are patched, so
connect() runs its REAL body. asyncio.sleep is recorded (never waited)
so the tests cost no wall-clock time; aqua_client._now is replaced by a
scripted clock (contract 007 §4 — the ONLY clock the instrumentation
reads).

Run standalone:

    python3 tests/test_connect_timing.py

No pytest API is used, so plain python works.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
import types
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
sys.path.insert(0, str(COMPONENT_DIR))

try:
    import bleak  # noqa: F401
except ImportError:
    # Inject a minimal fake bleak: the transport seam is overridden in
    # the tests, so the classes are never actually used.
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

ADDRESS = "08:06:A6:C0:94:1C"  # the production unit's address (spec 007 §0)
DEVICE_NAME = "BlueNRG-C0941C"

# Contract 007 §2.2 — the summary line for the AC-1 scripted clock
# below (t0=10.0 resolve=10.5 connect=51.0 notify=51.25 write-char=51.5
# settle=53.5): resolve 0.50, connect 40.5, notify 0.25, write-char
# 0.25, settle 2.0 — total 43.5, the sum of the five stages.
SUMMARY_LINE = (
    f"connect summary {ADDRESS}: total 43.5 s "
    f"(resolve 0.50, connect 40.5, notify 0.25, write-char 0.25, settle 2.0)"
)

# Contract 007 §2.1 — the attempt-window lines.
BRC_WINDOW_LINE = (
    f"connecting to {ADDRESS}: up to 2 connect attempt(s) of 20 s each "
    "(bleak-retry-connector BLEAK_TIMEOUT, research 007 §4)"
)
RAW_WINDOW_LINE = (
    f"connecting to {ADDRESS}: up to 2 connect attempt(s) of "
    f"{const.CONNECT_TIMEOUT:.0f} s each (raw bleak, const.CONNECT_TIMEOUT)"
)

SUMMARY_RE = re.compile(
    r"connect summary (?P<address>[0-9A-F:]+): total (?P<total>[0-9.]+) s "
    r"\(resolve (?P<resolve>[0-9.]+), connect (?P<connect>[0-9.]+), "
    r"notify (?P<notify>[0-9.]+), write-char (?P<write_char>[0-9.]+), "
    r"settle (?P<settle>[0-9.]+)\)"
)


# ---------------------------------------------------------------------------
# Fakes: transport seams + scripted clock — connect()'s body is the code
# under test (same technique as test_connect_parity.py).
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


class FakeBleakClient:
    """Connected bleak client stand-in; start_notify can be scripted to
    fail so the post-connect failure line is observable (contract §3.2)."""

    is_connected = True

    def __init__(self, notify_exc=None):
        self.services = FakeServices(FakeChar(("write-without-response", "write")))
        self._notify_exc = notify_exc

    async def start_notify(self, _uuid, _handler):
        if self._notify_exc is not None:
            raise self._notify_exc

    async def disconnect(self):
        pass


class BleakNotFoundError(Exception):
    """Stand-in for brc's exhausted-timeout exception (research 007 §4):
    bleak-retry-connector raises BleakNotFoundError("Failed to connect
    after N attempt(s)") when the hardcoded connect windows expire —
    nothing was "not found". Only the class NAME is load-bearing here:
    exc_chain renders type(exc).__name__, identical to the production
    chain in the research 007 §8 signature table."""


class FakeClock:
    """Scripted time seam (contract 007 §4): every _now() call pops the
    next scripted value. Extras repeat the last value so a drifted call
    count never crashes a test — the count is asserted separately."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0
        self._last = self.values[0] if self.values else 0.0

    def __call__(self):
        self.calls += 1
        if self.values:
            self._last = self.values.pop(0)
        return self._last


class Recorder:
    """One timeline interleaving log emissions and transport calls, so
    ordering assertions ("the window line precedes the connect call",
    AC-3) are direct."""

    def __init__(self):
        self.events: list = []

    def logs(self) -> list:
        return [msg for kind, msg in self.events if kind == "log"]


class _RecorderHandler(logging.Handler):
    def __init__(self, recorder: Recorder):
        super().__init__(level=logging.DEBUG)
        self._recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        self._recorder.events.append(("log", record.getMessage()))


@contextlib.contextmanager
def _capture(recorder: Recorder):
    """Attach the recorder to aqua_client's logger at DEBUG level."""
    logger = aqua_client._LOGGER
    handler = _RecorderHandler(recorder)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@contextlib.contextmanager
def _fake_clock(values):
    """Patch the module time seam (contract 007 §4). Absent on the
    pre-007 code (red-first): the patched clock then simply never runs."""
    real_now = getattr(aqua_client, "_now", None)
    clock = FakeClock(values)
    aqua_client._now = clock
    try:
        yield clock
    finally:
        if real_now is not None:
            aqua_client._now = real_now
        else:
            del aqua_client._now  # restore the pre-007 module state


@contextlib.contextmanager
def _record_sleeps(recorder: Recorder):
    """Patch asyncio.sleep to record the delay instead of waiting: the
    settle sleep stays observable without costing wall-clock time."""
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *_args, **_kwargs):
        recorder.events.append(("sleep", delay))

    asyncio.sleep = recording_sleep
    try:
        yield
    finally:
        asyncio.sleep = real_sleep


class FakeBRC:
    """Scripted bleak_retry_connector: records its call in the timeline,
    then raises or returns the scripted outcome (kwargs kept for the
    AC-3 pass-through assertions — connect() must not change them)."""

    def __init__(self, recorder: Recorder, outcome):
        self._recorder = recorder
        self._outcome = outcome
        self.kwargs: dict | None = None

    async def establish_connection(self, _client_class, _device, **kwargs):
        self._recorder.events.append(("establish_connection", ""))
        self.kwargs = kwargs
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class TimingClient(aqua_client.AquaTempBleClient):
    """AquaTempBleClient over the recording fakes.

    Only the I/O seams are patched; the connect() BODY — the window
    lines, the stage timestamps, the failure lines, the summary — is the
    shipped code under test.
    """

    def __init__(self, recorder, raw_client=None, brc=None, write_char_exc=None):
        super().__init__(ADDRESS)
        self._recorder = recorder
        self._raw_client = raw_client
        self._brc = brc
        self._write_char_exc = write_char_exc

    async def _resolve_device(self):
        return FakeBLEDevice(ADDRESS, DEVICE_NAME)

    def _load_retry_connector(self):
        return self._brc  # None -> raw standalone path

    async def _connect_with_raw_client(self, _device, pair: bool = False):
        self._recorder.events.append(("raw_connect", ""))
        return self._raw_client

    def _resolve_write_characteristic(self, client):
        if self._write_char_exc is not None:
            raise self._write_char_exc
        super()._resolve_write_characteristic(client)


# --------------------------------------------------------------------- tests
def test_success_summary_line_sums_to_total() -> None:
    """AC-1: ONE summary INFO line; five non-negative stage durations
    that sum to the total; ordered just before the Connected parity
    line (contract 007 §2.2, research 007 §8).

    Red-first: the pre-007 code emits no summary line at all.
    """
    # Scripted clock (s): connect() reads _now() exactly six times on
    # the success path — t0, resolve, connect, notify, write-char, settle.
    clock_values = [10.0, 10.5, 51.0, 51.25, 51.5, 53.5]
    recorder = Recorder()
    client = TimingClient(recorder, raw_client=FakeBleakClient())
    with _fake_clock(clock_values) as clock, _record_sleeps(recorder), _capture(recorder):
        asyncio.run(client.connect())
    logs = recorder.logs()
    # Exactly one summary line, byte-identical to the contract format.
    summaries = [m for m in logs if m.startswith("connect summary")]
    assert summaries == [SUMMARY_LINE], logs
    # The five parsed durations are non-negative and sum to the total.
    match = SUMMARY_RE.match(summaries[0])
    assert match, summaries[0]
    stages = {
        key: float(match.group(key))
        for key in ("resolve", "connect", "notify", "write_char", "settle")
    }
    assert all(value >= 0.0 for value in stages.values()), stages
    total = float(match.group("total"))
    assert abs(sum(stages.values()) - total) < 0.05, (stages, total)
    # The settle stage reports the configured sleep (spec 007 FR-1:
    # "logged as its configured value").
    assert stages["settle"] == const.SETTLE_AFTER_SUBSCRIBE, stages
    # The summary precedes the existing parity line (kept verbatim).
    connected_line = f"Connected to {ADDRESS} ({DEVICE_NAME})"
    assert connected_line in logs, logs
    assert logs.index(SUMMARY_LINE) < logs.index(connected_line), logs
    # Exactly one settle sleep happened — the spec 006 FR-1 sequence is
    # unchanged (spec 007 NFR-2).
    sleeps = [e for e in recorder.events if e[0] == "sleep"]
    assert sleeps == [("sleep", const.SETTLE_AFTER_SUBSCRIBE)], recorder.events
    # The clock was read exactly once per stage boundary — no drift.
    assert clock.calls == 6, clock.calls


def test_connect_failure_names_stage_and_elapsed() -> None:
    """AC-2: establish_connection raising BleakNotFoundError with a
    TimeoutError cause (the production signature, research 007 §4/§8)
    logs "failed at stage connect after <e.1f> s" with the full chain
    (contract 007 §3.1) and NO summary line.

    Red-first: the pre-007 failure line carries no stage/elapsed.
    """
    exc = BleakNotFoundError(
        f"{DEVICE_NAME} - {ADDRESS}: Failed to connect after 2 attempt(s) "
        f"(timeout of 20 seconds) while trying to connect to {DEVICE_NAME} - {ADDRESS}"
    )
    exc.__cause__ = TimeoutError()  # the <- chain of the research §8 signature
    # Scripted clock (s): t0, resolve, then the failure elapsed read.
    # elapsed = 40.5 s — the 2 x 20 s connect-window signature itself.
    clock_values = [10.0, 10.5, 50.5]
    recorder = Recorder()
    brc = FakeBRC(recorder, exc)
    client = TimingClient(recorder, brc=brc)
    raised = None
    with _fake_clock(clock_values) as clock, _capture(recorder):
        try:
            asyncio.run(client.connect(max_attempts=2))
        except aqua_client.AquaTempConnectionError as err:
            raised = err
    assert raised is not None, "connect() must raise AquaTempConnectionError"
    logs = recorder.logs()
    errors = [m for m in logs if m.startswith(f"connect to {ADDRESS}")]
    assert len(errors) == 1, logs
    expected_prefix = f"connect to {ADDRESS} failed at stage connect after 40.5 s: "
    assert errors[0].startswith(expected_prefix), errors[0]
    # The chain is intact — the stage proof (research 007 §8).
    assert "BleakNotFoundError" in errors[0], errors[0]
    assert "<- TimeoutError" in errors[0], errors[0]
    assert "Failed to connect after 2 attempt(s)" in errors[0], errors[0]
    # The raised exception keeps its message shape and cause (NFR-2).
    assert "could not connect to" in str(raised), str(raised)
    assert isinstance(raised.__cause__, BleakNotFoundError), raised.__cause__
    # No summary on a failed connect (contract 007 §2.2: one line per
    # SUCCESSFUL connect).
    assert not [m for m in logs if m.startswith("connect summary")], logs
    # The window line still preceded the failing call (AC-3 on failure).
    assert BRC_WINDOW_LINE in logs, logs
    assert recorder.events.index(("log", BRC_WINDOW_LINE)) < (
        recorder.events.index(("establish_connection", ""))
    ), recorder.events
    # Elapsed read exactly once at failure — t0, resolve, failure.
    assert clock.calls == 3, clock.calls


def test_attempt_window_line_precedes_connect_call() -> None:
    """AC-3: the attempt-window INFO line is emitted BEFORE the connect
    call and names the max_attempts value passed (contract 007 §2.1,
    research 007 §4 — the attempt arithmetic made visible at INFO).

    Red-first: the pre-007 code logs nothing before establish_connection.
    """
    clock_values = [10.0, 10.5, 11.5, 11.75, 12.0, 14.0]
    recorder = Recorder()
    brc = FakeBRC(recorder, FakeBleakClient())
    client = TimingClient(recorder, brc=brc)
    with (
        _fake_clock(clock_values),
        _record_sleeps(recorder),
        _capture(recorder),
    ):
        asyncio.run(client.connect(max_attempts=3))
    events = recorder.events
    connect_calls = [i for i, e in enumerate(events) if e[0] == "establish_connection"]
    assert len(connect_calls) == 1, events
    expected_window = BRC_WINDOW_LINE.replace(
        "up to 2 connect attempt(s)", "up to 3 connect attempt(s)"
    )
    window_idx = next(
        (
            i
            for i, e in enumerate(events)
            if e[0] == "log" and e[1] == expected_window
        ),
        None,
    )
    assert window_idx is not None, recorder.logs()
    assert window_idx < connect_calls[0], events[window_idx : connect_calls[0] + 1]
    # The connect call's arguments are unchanged (NFR-2): the window
    # line is the ONLY addition on this path.
    assert brc.kwargs is not None
    assert brc.kwargs["max_attempts"] == 3, brc.kwargs
    assert brc.kwargs["timeout"] == const.CONNECT_TIMEOUT, brc.kwargs
    assert brc.kwargs["name"] == DEVICE_NAME, brc.kwargs
    assert callable(brc.kwargs["disconnected_callback"]), brc.kwargs
    # The brc path emits the summary too (AC-1 on this path).
    summaries = [m for m in recorder.logs() if m.startswith("connect summary")]
    assert len(summaries) == 1, recorder.logs()


def test_attempt_window_line_raw_path_twin() -> None:
    """FR-2 standalone twin: the raw-bleak fallback logs its own window
    line (2 hardcoded attempts, const.CONNECT_TIMEOUT each) BEFORE the
    raw connect (contract 007 §2.1 standalone variant).

    Red-first: the pre-007 code logs nothing before the raw loop.
    """
    clock_values = [10.0, 10.5, 51.0, 51.25, 51.5, 53.5]
    recorder = Recorder()
    client = TimingClient(recorder, raw_client=FakeBleakClient())
    with (
        _fake_clock(clock_values),
        _record_sleeps(recorder),
        _capture(recorder),
    ):
        asyncio.run(client.connect())
    events = recorder.events
    raw_calls = [i for i, e in enumerate(events) if e[0] == "raw_connect"]
    assert len(raw_calls) == 1, events
    window_idx = next(
        (
            i
            for i, e in enumerate(events)
            if e[0] == "log" and e[1] == RAW_WINDOW_LINE
        ),
        None,
    )
    assert window_idx is not None, recorder.logs()
    assert window_idx < raw_calls[0], events[window_idx : raw_calls[0] + 1]


def test_post_connect_failure_at_stage_notify() -> None:
    """Contract 007 §3.2: a start_notify failure logs the post-connect
    ERROR with stage "notify" and the elapsed from connect() entry; the
    chain is intact; NO summary line (research 007 §8 — the
    notify/write-char class pivots the diagnosis away from the connect
    stall).

    Red-first: the pre-007 line carries no stage/elapsed.
    """
    clock_values = [10.0, 10.5, 12.0, 13.0]  # elapsed = 3.0 s
    recorder = Recorder()
    client = TimingClient(
        recorder, raw_client=FakeBleakClient(notify_exc=OSError("CCCD write refused"))
    )
    raised = None
    with _fake_clock(clock_values), _capture(recorder):
        try:
            asyncio.run(client.connect())
        except aqua_client.AquaTempConnectionError as err:
            raised = err
    assert raised is not None, "connect() must raise AquaTempConnectionError"
    logs = recorder.logs()
    expected = (
        f"post-connect setup (ff02 notify / ff01 lookup) on {ADDRESS} "
        f"failed at stage notify after 3.0 s: OSError: CCCD write refused"
    )
    assert expected in logs, logs
    assert not [m for m in logs if m.startswith("connect summary")], logs


def test_post_connect_failure_at_stage_write_char() -> None:
    """Contract 007 §3.2: a characteristic-resolution failure AFTER a
    completed notify logs stage "write-char" — the stage selection is
    driven by the completed boundaries, never guessed (plan 007 §1.2).

    Red-first: the pre-007 line carries no stage/elapsed.
    """
    clock_values = [10.0, 10.5, 12.0, 12.5, 13.5]  # elapsed = 3.5 s
    recorder = Recorder()
    client = TimingClient(
        recorder,
        raw_client=FakeBleakClient(),
        write_char_exc=RuntimeError("GATT table walk exploded"),
    )
    raised = None
    with _fake_clock(clock_values), _capture(recorder):
        try:
            asyncio.run(client.connect())
        except aqua_client.AquaTempConnectionError as err:
            raised = err
    assert raised is not None, "connect() must raise AquaTempConnectionError"
    logs = recorder.logs()
    expected = (
        f"post-connect setup (ff02 notify / ff01 lookup) on {ADDRESS} "
        f"failed at stage write-char after 3.5 s: "
        f"RuntimeError: GATT table walk exploded"
    )
    assert expected in logs, logs
    assert not [m for m in logs if m.startswith("connect summary")], logs


def main() -> int:
    tests = [
        test_success_summary_line_sums_to_total,
        test_connect_failure_names_stage_and_elapsed,
        test_attempt_window_line_precedes_connect_call,
        test_attempt_window_line_raw_path_twin,
        test_post_connect_failure_at_stage_notify,
        test_post_connect_failure_at_stage_write_char,
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
    print(f"PASSED: {len(tests)}/{len(tests)} connect timing tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())