#!/usr/bin/env python3
"""Spec 011 tests — the security probe, the pairing ladder, the option.

Covers the three deliverables of specs/011-android-vs-linux-ble:

1. FR-1 — the acknowledged-write seam: `force_response=True` writes
   the SAME vendor frame with response=True (the ATT Write Request
   that makes the unit's refusal visible, research 011 §2.1/§12 X1);
   byte-equality is pinned (AC-2) and the acknowledged failure raises
   AquaTempConnectionError("acknowledged write failed: <chain>") so
   the ATT error text survives to the log (contract 011 §5.1).
2. FR-2/FR-4 — the probe/pair/retry sequence (contract 011 §2): the
   config-flow connection test, on a first-read timeout, probes with
   the acknowledged write, classifies the verdict (§5.1 taxonomy),
   runs the pairing ladder ONLY on att-error-security, and retries
   the base read exactly once after a successful/skipped pair.
3. FR-3 — the pairing ladder: rung A (bleak pair method), rung B
   (reconnect with pair=True), rung C (minimal NoInputNoOutput BlueZ
   agent), AlreadyExists → skipped, agent-signature failures → rung C,
   authentication failures → the bLE choreography guidance, and the
   bond-state logging (research 011 §10.2).
4. FR-6/FR-7 — the pair_on_connect option: default OFF, the toggle in
   the options flow, connect(pair=True) threading, and the AC-4
   non-regression (with the option off, no probe/pair ever runs on
   the success path).
5. Spec 012 (the log-visibility contract, Part 7): the probe/pair
   evidence lines are at WARNING — the operator's HA log page
   (Settings → System → Logs, the system_log ring buffer) stores
   WARNING+ only (research 012 §4) — and every connection test starts
   with a build-marker WARNING naming the running version (spec 012
   FR-2/FR-3, contract 012 §2/§3).

Red-first: on the 0b16e0a+010 code none of the seams exist —
const.PAIR_TIMEOUT, read_registers(force_response=...), pair(),
run_security_probe and the options toggle are all missing, so every
test below fails until the implementation lands.

The suite is dual-mode like its siblings: client-level tests import
aqua_client standalone over a fake bleak (the test_first_read_resend
pattern); config-flow tests import config_flow against stubbed
homeassistant/voluptuous modules under a synthetic package (the
test_config_flow_walkthrough pattern), driving the flow
manager-faithfully.

Run standalone:

    python3 tests/test_security_probe_011.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

try:
    import pytest

    pytestmark = pytest.mark.asyncio
except ImportError:  # pragma: no cover — pytest-less standalone host
    pass

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
)

# ---------------------------------------------------------------------------
# Standalone component imports (fake bleak when the library is absent —
# the transport seam is faked at the bleak-client level, never used for
# real; the injected module matches the other spec files' pattern).
# ---------------------------------------------------------------------------

try:
    import bleak  # noqa: F401
except ImportError:
    bleak = types.ModuleType("bleak")

    class _FakeBleakClientPlaceholder:  # pragma: no cover — placeholder
        is_connected = False

    class _FakeBleakScannerPlaceholder:  # pragma: no cover — placeholder
        pass

    bleak.BleakClient = _FakeBleakClientPlaceholder
    bleak.BleakScanner = _FakeBleakScannerPlaceholder
    sys.modules["bleak"] = bleak

sys.path.insert(0, str(COMPONENT_DIR))

import agent  # noqa: E402  (rung C module — imports clean without dbus_fast)
import aqua_client  # noqa: E402
import const  # noqa: E402
import modbus  # noqa: E402
import registers  # noqa: E402

ADDRESS = "08:06:A6:C0:94:1C"
BASE_READ_HEX = "63030007002d3c54"  # the vendor-proven base read [P]
UUID_WRITE = const.UUID_WRITE


# ---------------------------------------------------------------------------
# Frame helpers (the siblings' proven builders)
# ---------------------------------------------------------------------------


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


SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: 999, 28: -125, 51: 2040}
ANSWER_BLOCK = _build_block_frame(7, 45, SYNTHETIC_VALUES)


# ---------------------------------------------------------------------------
# Fakes: the bleak-client level (client tests) + the client wrapper
# ---------------------------------------------------------------------------


class FakeGattChar:
    """GATT characteristic stand-in (answers every UUID lookup)."""

    def __init__(self, props):
        self.properties = list(props)


class FakeServices:
    """Services stand-in: bucket lookups work, enumeration does not
    (the client's best-effort path — DEBUG, never fatal)."""

    def __init__(self, char=None):
        self._char = char

    def get_characteristic(self, _uuid):
        return self._char


class _RecordingBleakClientBase:
    """Fake connected bleak client: records (uuid, data, response) writes.

    The AC-2 seam delta is asserted HERE: force_response is visible on
    the wire only as response=True (the frame bytes must be identical
    either way — contract 011 §1.2).

    Optional behaviors (scriptable per test):
    - ack_error: exception raised on acknowledged writes (response=True)
      — the ATT refusal the probe surfaces;
    - answer_frames/answer_on_write: notification frame(s) fed into the
      wrapped client's _on_notify on the Nth write (a scripted answer);
    - backend: a fake BlueZ backend exposing _device_path/_bus for the
      bond-state logging (contract 011 §3) — read through the client's
      `_backend` attribute, the name the introspection looks for.
    """

    def __init__(
        self,
        *,
        ack_error: BaseException | None = None,
        answer_frames: list[bytes] | None = None,
        answer_on_write: int = 1,
        backend: object | None = None,
    ) -> None:
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool]] = []
        self.write_count = 0
        self.pair_calls = 0
        self.ack_error = ack_error
        self.answer_frames = list(answer_frames or [])
        self.answer_on_write = answer_on_write
        self._backend = backend
        self.client_ref: aqua_client.AquaTempBleClient | None = None
        self.disconnected = False
        # the app-parity ff03 layout: bucket lookups resolve, the table
        # itself is not enumerable (the client logs at DEBUG and falls
        # back to the direct UUID lookups — spec 010's path).
        self.services = FakeServices(
            FakeGattChar(["write-without-response", "write"])
        )

    async def write_gatt_char(self, uuid, data, response=True) -> None:
        self.write_count += 1
        self.writes.append((str(uuid), bytes(data), bool(response)))
        if response and self.ack_error is not None:
            raise self.ack_error
        if self.answer_frames and self.write_count >= self.answer_on_write:
            frames, self.answer_frames = self.answer_frames, []
            for frame in frames:
                if self.client_ref is not None:
                    self.client_ref._on_notify(bytearray(frame))

    async def start_notify(self, _uuid, _handler) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


class RecordingBleakClient(_RecordingBleakClientBase):
    """The rung-A shape: the bleak client exposes a pair() method."""

    def __init__(self, *, pair_script: list | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.pair_script = pair_script

    async def pair(self) -> None:
        self.pair_calls += 1
        script = self.pair_script
        if script is not None:
            outcome = script[min(self.pair_calls - 1, len(script) - 1)]
            if isinstance(outcome, BaseException):
                raise outcome


class FakeBluezBackend:
    """Fake bleak BlueZ backend for the bond-state read (contract §3.6)."""

    def __init__(self, bus: object) -> None:
        self._device_path = "/org/bluez/hci0/dev_0806_A6_C0_94_1C"
        self._bus = bus


class FakeVariant:
    """dbus-fast Variant shape (body[0].value)."""

    def __init__(self, value: bool) -> None:
        self.value = value


class FakeDbusBus:
    """Fake dbus_fast MessageBus: answers Properties.Get(Paired)."""

    def __init__(self, paired: bool, *, variant: bool = False) -> None:
        self.paired = paired
        self.variant = variant
        self.calls: list[object] = []

    async def call(self, message) -> object:
        self.calls.append(message)
        body = [FakeVariant(self.paired)] if self.variant else [self.paired]
        return SimpleNamespace(body=body)


class _FakeMessage:
    """Stand-in for dbus_fast.Message (kwarg-constructed)."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def _make_probe_client(fake: _RecordingBleakClientBase) -> aqua_client.AquaTempBleClient:
    """A real AquaTempBleClient over a fake bleak client (connected).

    The write mode is the production ff03 mode (write-without-response,
    the vendor parity of spec 010) so the AC-2 seam assertions see the
    session's real default; force_response=True is the only override.
    """
    client = aqua_client.AquaTempBleClient(ADDRESS)
    client._client = fake
    client._write_with_response = False
    fake.client_ref = client
    return client


class _LogCap(logging.Handler):
    """Capture records emitted on the aqua_client logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []
        # Spec 012 FR-3: the caps also record each record's levelname —
        # the suite now asserts LEVELS, not just message strings (the
        # coverage hole that let an operator-invisible INFO grammar pass).
        self.levels: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        self.levels.append(record.levelname)


class _capture_logs:
    """Attach a capture handler to aqua_client._LOGGER for the duration."""

    def __enter__(self) -> _LogCap:
        self.cap = _LogCap()
        self.logger = aqua_client._LOGGER
        self.old_level = self.logger.level
        self.logger.addHandler(self.cap)
        self.logger.setLevel(logging.DEBUG)
        return self.cap

    def __exit__(self, *_exc) -> bool:
        self.logger.removeHandler(self.cap)
        self.logger.setLevel(self.old_level)
        return False


class _patch_const:
    """Patch a const attribute for the duration of a block."""

    def __init__(self, name: str, value) -> None:
        self.name = name
        self.value = value

    def __enter__(self) -> None:
        self.old = getattr(const, self.name)
        setattr(const, self.name, self.value)

    def __exit__(self, *_exc) -> bool:
        setattr(const, self.name, self.old)
        return False


class _patch_agent_module:
    """Patch the rung-C module's registration seams for a test.

    The ladder looks the functions up on the module at call time, so a
    plain attribute patch is the test seam (no bus is ever opened).
    """

    def __init__(self, *, available: bool = True, register_error=None) -> None:
        self.available = available
        self.register_error = register_error
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    async def _fake_register(self) -> object:
        if self.register_error is not None:
            raise self.register_error
        self.registered.append(agent.AGENT_PATH)
        return object()  # the fake bus token

    async def _fake_unregister(self, bus) -> None:
        self.unregistered.append(bus)

    def __enter__(self) -> "_patch_agent_module":
        self._old_available = agent.DBUS_FAST_AVAILABLE
        self._old_register = agent.register_agent
        self._old_unregister = agent.unregister_agent
        agent.DBUS_FAST_AVAILABLE = self.available
        agent.register_agent = self._fake_register  # type: ignore[method-assign]
        agent.unregister_agent = self._fake_unregister  # type: ignore[method-assign]
        return self

    def __exit__(self, *_exc) -> bool:
        agent.DBUS_FAST_AVAILABLE = self._old_available
        agent.register_agent = self._old_register  # type: ignore[method-assign]
        agent.unregister_agent = self._old_unregister  # type: ignore[method-assign]
        return False


def _leaked_tasks() -> list[asyncio.Task]:
    """Running tasks from this suite's machinery (leak guard)."""
    return [
        t
        for t in asyncio.all_tasks()
        if "_resend_loop" in getattr(t.get_coro(), "__qualname__", "")
    ]


# ---------------------------------------------------------------------------
# Part 1 — the pure verdict classifier (contract 011 §5.1)
# ---------------------------------------------------------------------------


async def test_classifier_table_every_contract_row() -> None:
    """AC-1: every §5.1 row classifies to its verdict, case-insensitively,
    including the BlueZ 'Not paired' status string and the short
    'insufficient auth' form older stacks emit (research 011 §10.1).
    Unknown strings fall to local-failure — never guessed."""
    classify = aqua_client._classify_ack_write_failure
    table = [
        # (chain text, verdict)
        ("BleakGATTProtocolError: write failed: Insufficient Authentication", "att-error-security"),
        ("insufficient encryption", "att-error-security"),
        ("Not paired: Operation requires an existing bond", "att-error-security"),
        ("org.bluez.Error.NotPaired: ...", "att-error-security"),
        ("insufficient auth", "att-error-security"),
        ("Write Not Permitted", "att-error-permission"),
        ("org.bluez.Error.NotPermitted: Not permitted", "att-error-permission"),
        ("write not allowed", "att-error-permission"),
        ("Insufficient Authorization", "att-error-permission"),  # NOT security
        ("invalid handle", "att-error-handle"),
        ("Invalid Offset", "att-error-handle"),
        ("something completely different", "local-failure"),
        ("", "local-failure"),
    ]
    for text, expected in table:
        got = classify(text)
        assert got == expected, f"{text!r}: expected {expected}, got {got}"
    # The classifier must also see through the wrapper chain the seam
    # produces (acknowledged write failed: <chain verbatim>).
    wrapped = "acknowledged write failed: BleakGATTProtocolError: ATT error: Insufficient Authentication"
    assert classify(wrapped) == "att-error-security", wrapped


# ---------------------------------------------------------------------------
# Part 2 — FR-1: the acknowledged-write seam (AC-2)
# ---------------------------------------------------------------------------


async def test_probe_bytes_equal_and_response_only_delta() -> None:
    """AC-2: the probe writes EXACTLY the vendor's base frame
    (build_read_holding_registers(7, 45) == 63030007002d3c54) and the
    ONLY seam delta is response=True; the same request without the flag
    writes the same bytes with the session's write mode (False here)."""
    fake = _RecordingBleakClientBase()
    client = _make_probe_client(fake)
    client._write_with_response = False

    with _patch_const("REQUEST_TIMEOUT", 0.2):
        # normal read: without-response, the vendor parity of spec 010
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass
        # probe read: the acknowledged twin of the SAME request
        try:
            await client.read_registers(7, 45, force_response=True)
        except aqua_client.AquaTempTimeoutError:
            pass

    assert len(fake.writes) == 2, fake.writes
    expected = modbus.build_read_holding_registers(7, 45)
    assert expected.hex() == BASE_READ_HEX  # the constant pin
    for uuid, data, response in fake.writes:
        assert data == expected, (data.hex(), expected.hex())
        assert uuid == UUID_WRITE
    # the ONLY delta: response True on the acknowledged write
    assert [r for _u, _d, r in fake.writes] == [False, True], fake.writes


async def test_acknowledged_write_failure_chain_survives() -> None:
    """FR-1: an acknowledged-write failure raises AquaTempConnectionError
    whose message starts with 'acknowledged write failed:' and carries
    the chain verbatim (contract 011 §5.1 reads the ATT text from it);
    the without-response path keeps today's 'GATT write failed:' wrapper."""
    fake = _RecordingBleakClientBase(
        ack_error=RuntimeError("BleakGATTProtocolError: Insufficient Authentication")
    )
    client = _make_probe_client(fake)
    client._write_with_response = False

    # acknowledged path (the probe)
    exc = None
    try:
        await client.read_registers(7, 45, force_response=True)
    except aqua_client.AquaTempConnectionError as err:
        exc = err
    assert exc is not None, "acknowledged write failure was swallowed"
    assert str(exc).startswith("acknowledged write failed: "), str(exc)
    assert "Insufficient Authentication" in str(exc), str(exc)

    # without-response path: today's wrapper, byte-identical (spec 005).
    # The fake raises only on acknowledged bleak writes, so the session
    # mode is flipped to with-response to make the PLAIN write fail —
    # the wrapper under test is chosen by force_response (False here),
    # not by the session mode.
    fake.ack_error = RuntimeError("link went down")
    client._write_with_response = True
    exc = None
    try:
        await client.read_registers(7, 45)
    except aqua_client.AquaTempConnectionError as err:
        exc = err
    assert exc is not None
    assert str(exc).startswith("GATT write failed: "), str(exc)


async def test_last_request_write_count_tracks_resends() -> None:
    """The helper's 'answered after pairing (<n> attempt of the window)'
    line needs the request's write count (contract 011 §3.4) — the
    counter covers the initial write plus every re-send."""
    fake = _RecordingBleakClientBase(
        answer_frames=[ANSWER_BLOCK], answer_on_write=3
    )
    client = _make_probe_client(fake)
    await client.read_registers(7, 45, resend_every=0.05, resend_max=5)
    assert client.last_request_write_count == 3, client.last_request_write_count
    await asyncio.sleep(0.01)
    assert not _leaked_tasks(), _leaked_tasks()


# ---------------------------------------------------------------------------
# Part 3 — FR-2/FR-4: the probe/pair/retry sequence (run_security_probe)
# ---------------------------------------------------------------------------


async def test_probe_security_verdict_pair_then_retry_answers() -> None:
    """The case-closed path, client level: probe → att-error-security
    verdict → pair (rung A) → ONE retry read → answered → the
    'answered after pairing' line + the regs returned (contract §2/§3.4)."""
    fake = RecordingBleakClient(
        ack_error=RuntimeError(
            "BleakGATTProtocolError: Insufficient Authentication"
        ),
        answer_frames=[ANSWER_BLOCK],
        answer_on_write=2,  # write 1 = probe (refused), write 2 = retry
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(
            client, retry_resend_every=2.0, retry_resend_max=3
        )
    assert regs is not None and regs[26] == 243, regs
    assert fake.pair_calls == 1, "the ladder must run exactly once"
    infos = [m for m in cap.messages if m.startswith("security probe:") or m.startswith("pairing:")]
    assert any(
        m == f"security probe: re-sending base read as acknowledged write (response=True) on {UUID_WRITE} to {ADDRESS}"
        for m in infos
    ), infos
    assert any(
        m.startswith("security probe: verdict att-error-security: ")
        and "Insufficient Authentication" in m
        for m in infos
    ), infos
    assert any(m.startswith("pairing: attempting via rung A") for m in infos), infos
    assert any(
        m.startswith("security probe: answered after pairing (") for m in infos
    ), infos
    # the retry is the SAME base read, re-send bounds passed through
    assert fake.write_count == 2, fake.write_count
    await asyncio.sleep(0.01)
    assert not _leaked_tasks(), _leaked_tasks()


async def test_probe_security_verdict_pair_fails_no_retry() -> None:
    """A failed pair means NO retry read (FR-4: the retry follows
    success/skipped only) — the original timeout routing stays."""
    fake = RecordingBleakClient(
        ack_error=RuntimeError("BleakGATTProtocolError: Insufficient Encryption"),
        pair_script=[RuntimeError("org.bluez.Error.AuthenticationFailed: failed")],
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert fake.pair_calls == 1
    assert fake.write_count == 1, "no retry read may follow a failed pair"
    infos = [m for m in cap.messages if m.startswith("security probe:") or m.startswith("pairing:")]
    assert any(
        m.startswith("security probe: verdict att-error-security: ") for m in infos
    ), infos
    assert any(m.startswith("pairing: failed: ") for m in infos), infos
    # the bLE choreography guidance rides on the authentication failure
    assert any(
        m.startswith("pairing: failed: ") and "bLE" in m for m in infos
    ), infos


async def test_probe_non_security_verdict_skips_pair() -> None:
    """Contract §2: only att-error-security runs the ladder — a
    permission verdict logs and stops (the resolver/cache column of
    the decision matrix)."""
    fake = RecordingBleakClient(
        ack_error=RuntimeError("BleakGATTProtocolError: Write Not Permitted")
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert fake.pair_calls == 0, "no pair attempt may follow a non-security verdict"
    infos = [m for m in cap.messages if m.startswith("security probe:")]
    assert any(
        m.startswith("security probe: verdict att-error-permission: ") for m in infos
    ), infos


async def test_probe_accepted_silent_verdict() -> None:
    """The ack-accepted-but-silent case: the write succeeded at the ATT
    layer and the engine ignored it — att-accepted-silent with the
    census in the line (contract §5.1 row 4; the H-APP-GATE branch)."""
    fake = RecordingBleakClient()  # acknowledged write accepted, no answer
    client = _make_probe_client(fake)
    with _patch_const("REQUEST_TIMEOUT", 0.2), _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert fake.pair_calls == 0
    infos = [m for m in cap.messages if m.startswith("security probe:")]
    assert any(
        m.startswith(
            "security probe: verdict att-accepted-silent "
            "(ack ok, no Modbus answer within 0.2s; census: "
        )
        for m in infos
    ), infos


async def test_probe_answers_directly() -> None:
    """Contract §2 row 3: the acknowledged write itself WAKES the
    engine — the probe's read answers, which is decisive evidence and
    a success (the helper returns the regs)."""
    fake = RecordingBleakClient(
        answer_frames=[ANSWER_BLOCK], answer_on_write=1
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is not None and regs[26] == 243, regs
    assert fake.pair_calls == 0, "an answering probe needs no pair"
    assert any(
        "security probe: acknowledged write answered" in m for m in cap.messages
    ), cap.messages


async def test_probe_modbus_exception_frame_logged() -> None:
    """Edge: the acknowledged write reaches the engine and the unit
    answers with a Modbus exception frame — the write pipe is OPEN at
    the ATT layer; the helper logs the datum and returns None (no
    regs to route)."""
    exception_frame = (
        bytes([modbus.SLAVE_ID, 0x83, 0x02])
        + modbus.crc16_modbus(bytes([modbus.SLAVE_ID, 0x83, 0x02])).to_bytes(2, "little")
    )
    fake = RecordingBleakClient(
        answer_frames=[exception_frame], answer_on_write=1
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert any(
        "security probe: acknowledged write answered" in m for m in cap.messages
    ), cap.messages


async def test_probe_pair_stage_bounded_no_retry_after_wedge() -> None:
    """Contract §4 / tasks 011 §5: the rung-A pair() call is bounded by
    PAIR_TIMEOUT — a wedged D-Bus Pair logs 'pairing: failed:' and the
    sequence ends WITHOUT the retry (FR-4), never a hang. (The helper's
    own whole-ladder bound — 'pairing budget' — is covered at the flow
    level by test_flow_pair_stage_wedge_is_bounded_and_skips_retry.)"""

    async def wedged_pair() -> None:
        await asyncio.sleep(5.0)

    client = _make_probe_client(
        RecordingBleakClient(
            ack_error=RuntimeError(
                "BleakGATTProtocolError: Insufficient Authentication"
            )
        )
    )
    client._client.pair = wedged_pair  # type: ignore[method-assign]
    with _patch_const("PAIR_TIMEOUT", 0.2):
        with _capture_logs() as cap:
            regs = await aqua_client.run_security_probe(client)
    assert regs is None
    # The whole-ladder bound fired (the helper's wait_for starts before
    # the rung's, so with equal budgets the OUTER one cancels first —
    # contract 011 §4's inner bound, the wedged Pair never hangs).
    assert any("pairing budget" in m for m in cap.messages), cap.messages
    assert not any("pairing: succeeded" in m for m in cap.messages)
    assert client._client.write_count == 1, "no retry read after a wedged pair"


# ---------------------------------------------------------------------------
# Part 4 — FR-3: the pairing ladder
# ---------------------------------------------------------------------------


async def test_pair_rung_a_success_and_bond_lines() -> None:
    """Rung A: the bleak pair method exists → one bounded call → the
    succeeded line plus the bond-state before/after lines (contract §3)."""
    fake = RecordingBleakClient()
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        result = await client.pair()
    assert result == "succeeded", result
    infos = [m for m in cap.messages if m.startswith("pairing:")]
    assert any(m.startswith("pairing: attempting via rung A") for m in infos), infos
    assert any(m == "pairing: bond state before: unknown" for m in infos), infos
    assert any(m == "pairing: succeeded" for m in infos), infos
    assert any(m == "pairing: bond state after: unknown" for m in infos), infos


async def test_pair_rung_a_already_exists_skipped() -> None:
    """BlueZ AlreadyExists → 'pairing: skipped: already bonded' (the X3
    fast no-op verification, contract §5.2) — status skipped."""
    fake = RecordingBleakClient(
        pair_script=[RuntimeError("org.bluez.Error.AlreadyExists: Already exists")]
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        result = await client.pair()
    assert result == "skipped", result
    assert any(
        m == "pairing: skipped: already bonded"
        for m in cap.messages
        if m.startswith("pairing:")
    ), cap.messages


async def test_pair_rung_a_authentication_failure_gets_ble_guidance() -> None:
    """AuthenticationFailed chains log the bLE choreography guidance
    (contract §5.2) and do NOT trigger rung C."""
    fake = RecordingBleakClient(
        pair_script=[RuntimeError("org.bluez.Error.AuthenticationFailed: refused")]
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        result = await client.pair()
    assert result == "failed", result
    infos = [m for m in cap.messages if m.startswith("pairing:")]
    assert any(
        m.startswith("pairing: failed: ") and "bLE" in m for m in infos
    ), infos
    assert not any("rung C" in m for m in infos), infos


async def test_pair_rung_c_registers_agent_and_retries() -> None:
    """The agent-signature failure (contract §5.2 row 1: the documented
    no-agent Pair() failure, research 011 §10.2) triggers rung C: the
    minimal agent registers, rung A retries ONCE, the agent is released
    in the finally."""
    fake = RecordingBleakClient(
        pair_script=[
            RuntimeError("org.bluez.Error.Failed: No agent is registered"),
            None,  # the retry succeeds
        ]
    )
    client = _make_probe_client(fake)
    with _patch_agent_module() as patched:
        with _capture_logs() as cap:
            result = await client.pair()
    assert result == "succeeded", result
    assert fake.pair_calls == 2, "rung C retries rung A exactly once"
    assert patched.registered == [agent.AGENT_PATH], patched.registered
    assert patched.unregistered, "the agent must always be released"
    infos = [m for m in cap.messages if m.startswith("pairing:")]
    assert any(m.startswith("pairing: attempting via rung C") for m in infos), infos


async def test_pair_rung_c_unavailable_without_dbus_fast() -> None:
    """AC-8: the dbus_fast import is guarded — when the library is
    absent (non-Linux / stripped hosts) rung C reports unavailable, the
    ladder never crashes (ImportError is a verdict, not an exception)."""
    fake = RecordingBleakClient(
        pair_script=[RuntimeError("org.bluez.Error.Failed: No agent is registered")]
    )
    client = _make_probe_client(fake)
    with _patch_agent_module(available=False):
        with _capture_logs() as cap:
            result = await client.pair()
    assert result == "unavailable", result
    assert fake.pair_calls == 1, "no retry without the agent"
    infos = [m for m in cap.messages if m.startswith("pairing:")]
    assert any(m.startswith("pairing: unavailable: ") for m in infos), infos


async def test_pair_rung_b_reconnects_with_pair_true() -> None:
    """Rung B (bleak >= 1.0 shape: no pair method): the connection is
    torn down and re-established with pairing requested, then the
    normal post-connect setup re-runs (research 011 §10.1)."""
    establish_calls: list[bool] = []

    class RungBClient(aqua_client.AquaTempBleClient):
        async def _resolve_device(self) -> object:
            return object()  # a fake BLEDevice

        async def _establish_connection(self, device, *, max_attempts, pair=False):
            establish_calls.append(bool(pair))
            return RecordingBleakClient()

        async def _post_connect_setup(self, client, device, *, t0, t_resolve, t_connect):
            self._client = client  # the setup "ran"

    pairless = _RecordingBleakClientBase()
    client = RungBClient(ADDRESS)
    client._client = pairless
    # capability: the raw BleakClient constructor accepts pair=
    old_cls = aqua_client.BleakClient

    class _PairCapableBleakClient:  # the bleak >= 1.0 constructor shape
        def __init__(self, device, *, disconnected_callback=None, timeout=None, pair=False):
            pass

    aqua_client.BleakClient = _PairCapableBleakClient
    try:
        with _capture_logs() as cap:
            result = await client.pair()
    finally:
        aqua_client.BleakClient = old_cls
    assert result == "succeeded", result
    assert establish_calls == [True], establish_calls
    assert pairless.disconnected, "rung B must tear the old link down first"
    infos = [m for m in cap.messages if m.startswith("pairing:")]
    assert any(m.startswith("pairing: attempting via rung B") for m in infos), infos


async def test_pair_unavailable_when_backend_cannot_pair() -> None:
    """No rung at all: no pair method and no pair kwarg anywhere →
    'pairing: unavailable: <reason>' — the connection is NOT touched
    (contract §3.3, research 011 §10.1)."""
    pairless = _RecordingBleakClientBase()
    client = _make_probe_client(pairless)
    with _capture_logs() as cap:
        result = await client.pair()
    assert result == "unavailable", result
    assert pairless.disconnected is False, "unavailable must not tear the link"
    assert not hasattr(pairless, "establish_called")
    infos = [m for m in cap.messages if m.startswith("pairing:")]
    assert any(m.startswith("pairing: unavailable: ") for m in infos), infos


async def test_pair_bond_state_read_via_backend() -> None:
    """The best-effort Paired property read (contract §3.6): both the
    plain-bool and the dbus-fast Variant reply shapes render the
    paired/not-paired state; failures degrade to unknown."""
    old_message = aqua_client._dbus_fast_message

    def _fake_message():
        return _FakeMessage

    aqua_client._dbus_fast_message = _fake_message
    try:
        for paired in (True, False):
            for variant in (False, True):
                bus = FakeDbusBus(paired, variant=variant)
                # RecordingBleakClient (not the pairless base): the ladder
                # needs its pair method so rung A succeeds and the bond
                # state is read around it.
                fake = RecordingBleakClient(backend=FakeBluezBackend(bus))
                client = _make_probe_client(fake)
                with _capture_logs() as cap:
                    result = await client.pair()
                assert result == "succeeded", result
                state = (
                    "pairing: bond state after: paired"
                    if paired
                    else "pairing: bond state after: not paired"
                )
                assert state in cap.messages, (paired, variant, cap.messages)
                assert bus.calls, "the Properties.Get message was never sent"
    finally:
        aqua_client._dbus_fast_message = old_message


async def test_pair_bond_state_unknown_without_dbus_fast() -> None:
    """dbus_fast absent → the guarded read degrades to unknown (DEBUG),
    never an exception (AC-8)."""
    fake = RecordingBleakClient()
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        result = await client.pair()
    assert result == "succeeded", result
    assert "pairing: bond state before: unknown" in cap.messages


# ---------------------------------------------------------------------------
# Part 5 — FR-6/FR-7: connect(pair=...) threading + the option
# ---------------------------------------------------------------------------


class FakeBrc:
    """Fake bleak_retry_connector module object (establish_connection
    with **kwargs — the VAR_KEYWORD capability path)."""

    def __init__(self, *, connected_fake: _RecordingBleakClientBase) -> None:
        self.connected_fake = connected_fake
        self.calls: list[dict] = []

    async def establish_connection(self, client_class, device, **kwargs):
        self.calls.append(kwargs)
        return self.connected_fake


class _ConnectClient(aqua_client.AquaTempBleClient):
    """Client over the fakes: the real connect() body runs."""

    def __init__(self, brc) -> None:
        super().__init__(ADDRESS)
        self._brc = brc

    async def _resolve_device(self) -> object:
        return SimpleNamespace(address=ADDRESS, name="BlueNRG-C0941C")

    def _load_retry_connector(self):
        return self._brc


def _record_sleeps(events: list) -> object:
    """Patch asyncio.sleep to record the delay instead of waiting."""
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *_args, **_kwargs):
        events.append(("sleep", delay))

    asyncio.sleep = recording_sleep
    return real_sleep


async def test_connect_threads_pair_into_establish() -> None:
    """FR-6: connect(pair=True) requests pairing at the establish call
    (brc pair=True — the capability-checked kwarg, research 011 §10.1)
    and logs the contract §3.5 DEBUG marker; connect() bare passes no
    pair at all (FR-7: today's behavior byte-identical)."""
    events: list = []
    fake = _RecordingBleakClientBase()
    brc = FakeBrc(connected_fake=fake)
    client = _ConnectClient(brc)
    real_sleep = _record_sleeps(events)
    try:
        with _capture_logs() as cap:
            cap_cap = cap
            await client.connect(max_attempts=2, pair=True)
            pair_calls = [c for c in brc.calls]
            await client.disconnect()
            brc.calls.clear()
            await client.connect(max_attempts=2)
            plain_calls = [c for c in brc.calls]
    finally:
        asyncio.sleep = real_sleep
    assert any(c.get("pair") is True for c in pair_calls), pair_calls
    assert all("pair" not in c for c in plain_calls), plain_calls
    assert any(
        m == "connect with pairing requested (option pair_on_connect)"
        for m in cap_cap.messages
    ), cap_cap.messages


async def test_connect_pair_unsupported_degrades_with_warning() -> None:
    """The option must never break the poll path: a backend that cannot
    pair logs a WARNING and connects WITHOUT pairing (FR-6/FR-7)."""

    class _PairlessBrc:
        def __init__(self, connected_fake):
            self.connected_fake = connected_fake
            self.calls = []

        async def establish_connection(
            self, client_class, device, *, name, max_attempts, timeout,
            disconnected_callback=None,
        ):
            self.calls.append(name)
            return self.connected_fake

    fake = _RecordingBleakClientBase()
    brc = _PairlessBrc(fake)
    client = _ConnectClient(brc)
    events: list = []
    real_sleep = _record_sleeps(events)
    try:
        with _capture_logs() as cap:
            await client.connect(max_attempts=2, pair=True)
    finally:
        asyncio.sleep = real_sleep
    assert brc.calls, "the connect must proceed despite the unsupported pair"
    assert any(
        m.startswith("pairing was requested") for m in cap.messages
    ), cap.messages


async def test_const_pair_values() -> None:
    """Block 1 constants: PAIR_TIMEOUT bounds one D-Bus Pair attempt
    (contract 011 §4), the option ships OFF (FR-6) and the plumbing
    constant names the establish kwarg."""
    assert const.PAIR_TIMEOUT == 25.0, const.PAIR_TIMEOUT
    assert const.DEFAULT_PAIR_ON_CONNECT is False
    assert const.PAIR_ENABLE_PROP == "pair"


async def test_option_off_no_probe_no_pair_on_normal_reads() -> None:
    """AC-4 (client half): a full normal read cycle with the option OFF
    sends no acknowledged writes and never pairs — the poll path is
    today's behavior (FR-7)."""
    fake = RecordingBleakClient(answer_frames=[ANSWER_BLOCK], answer_on_write=1)
    client = _make_probe_client(fake)
    regs = await client.read_registers(7, 45)
    assert regs[26] == 243
    assert fake.pair_calls == 0
    assert [r for _u, _d, r in fake.writes] == [False], fake.writes


# ---------------------------------------------------------------------------
# Part 6 — the config-flow stages (FR-2, contract 011 §2) — stubbed HA
# ---------------------------------------------------------------------------

PKG = "_aqua_flow_011"  # synthetic package name (distinct from siblings)


class AlreadyConfigured(Exception):
    """Stand-in for HA's AbortFlow(already_configured)."""


def _install_stubs() -> None:
    vol_mod = types.ModuleType("voluptuous")

    class _VolRequired:
        """Stub vol.Required that RECORDS its kwargs — the options-flow
        test asserts the pair_on_connect default through it."""

        def __init__(self, key, **kwargs):
            self.key = key
            self.kwargs = kwargs

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return isinstance(other, _VolRequired) and self.key == other.key

    class _Vol:
        @staticmethod
        def Schema(schema):
            return schema

        Required = _VolRequired

        @staticmethod
        def In(container):
            return container

        @staticmethod
        def All(*args):
            return args

        @staticmethod
        def Coerce(type_):
            return type_

        @staticmethod
        def Range(**kwargs):
            return kwargs

    for name in ("Schema", "In", "All", "Coerce", "Range"):
        setattr(vol_mod, name, getattr(_Vol, name))
    vol_mod.Required = _VolRequired
    sys.modules["voluptuous"] = vol_mod

    bleak_mod = types.ModuleType("bleak")
    bleak_mod.BleakClient = object
    bleak_mod.BleakScanner = object
    sys.modules["bleak"] = bleak_mod

    class _StubConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()
            cls.handler = kwargs.get("domain")

        def async_show_menu(self, *, step_id, menu_options=None, description_placeholders=None):
            return {
                "type": "menu",
                "step_id": step_id,
                "menu_options": dict(menu_options or {}),
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress(self, *, step_id, progress_action=None, progress_task=None, description_placeholders=None):
            return {
                "type": "progress",
                "step_id": step_id,
                "progress_action": progress_action,
                "progress_task": progress_task,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress_done(self, *, next_step_id):
            return {"type": "progress_done", "step_id": next_step_id, "handler": getattr(self, "handler", None)}

        def async_abort(self, *, reason):
            return {"type": "abort", "reason": reason}

        def async_create_entry(self, *, title=None, data=None, options=None):
            return {"type": "create_entry", "title": title, "data": data, "options": options}

        async def async_set_unique_id(self, unique_id, *, raise_on_progress=True):
            self._stub_unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            if getattr(self, "_stub_unique_id", None) in self.hass.current_ids:
                raise AlreadyConfigured(self._stub_unique_id)

        def _async_current_ids(self, include_ignore=True):
            return list(self.hass.current_ids)

    class _StubOptionsFlow:
        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_create_entry(self, *, title=None, data=None):
            return {"type": "create_entry", "title": title, "data": data}

    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_helpers = types.ModuleType("homeassistant.helpers")

    cfg_entries = types.ModuleType("homeassistant.config_entries")
    cfg_entries.ConfigFlow = _StubConfigFlow
    cfg_entries.ConfigFlowResult = dict
    cfg_entries.OptionsFlow = _StubOptionsFlow

    ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _bt_async_scanner_count(hass, connectable=True):
        return hass.bt_scanner_count

    def _bt_async_discovered_service_info(hass, connectable=True):
        # Test isolation (this install REPLACES the module the lazy
        # imports resolve): the flow's fakes carry bt_infos, the
        # resolve-device fakes carry connectable_infos — support both.
        for attr in ("bt_infos", "connectable_infos"):
            if hasattr(hass, attr):
                return list(getattr(hass, attr))
        return []

    def _bt_async_ble_device_from_address(hass, address, connectable=True):
        # The resolve-device stub's semantics (the fake hass carries the
        # history tiers directly): this install REPLACES that module in
        # sys.modules, so the lazy import inside _resolve_device must
        # keep finding the function — otherwise the 005 tier tests break
        # under the full suite (test isolation).
        history = (
            getattr(hass, "connectable_history", None)
            if connectable
            else getattr(hass, "all_history", None)
        )
        if history is not None:
            return history.get(address)
        return None

    ha_bt.async_scanner_count = _bt_async_scanner_count
    ha_bt.async_discovered_service_info = _bt_async_discovered_service_info
    ha_bt.async_ble_device_from_address = _bt_async_ble_device_from_address

    ha_translation = types.ModuleType("homeassistant.helpers.translation")

    async def _tr_async_get_translations(hass, language, category, integrations, *, config_flow=False):
        return dict(hass.translations)

    ha_translation.async_get_translations = _tr_async_get_translations

    ha.config = types.SimpleNamespace(language="en")
    ha.components = ha_components
    ha.helpers = ha_helpers
    ha_components.bluetooth = ha_bt

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.bluetooth": ha_bt,
        "homeassistant.config_entries": cfg_entries,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.translation": ha_translation,
    }.items():
        sys.modules[name] = module


def _import_config_flow():
    _install_stubs()
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT_DIR)]
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.config_flow", COMPONENT_DIR / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.config_flow"] = module
    spec.loader.exec_module(module)
    return module


cf = _import_config_flow()


class FlowInfo:
    def __init__(self, address, name, rssi, service_uuids=()):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.service_uuids = list(service_uuids)


class FlowHass:
    def __init__(self, infos=None):
        self.config = types.SimpleNamespace(language="en")
        self.bt_scanner_count = 1
        self.bt_infos = list(infos or [])
        self.current_ids = set()
        self.translations = {}

    def async_create_task(self, target):
        return asyncio.ensure_future(target)


class _FlowLogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []
        # Spec 012 FR-3: levels recorded alongside messages — the level
        # contract of contract 012 §2 is asserted through this cap.
        self.levels: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        self.levels.append(record.levelname)


class _flow_logs:
    """Capture BOTH the flow's and the stubbed aqua_client's loggers."""

    def __enter__(self) -> _FlowLogCap:
        self.cap = _FlowLogCap()
        self.loggers = [cf._LOGGER, sys.modules[f"{PKG}.aqua_client"]._LOGGER]
        self.old_levels = [lg.level for lg in self.loggers]
        for lg in self.loggers:
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self.cap)
        return self.cap

    def __exit__(self, *_exc) -> bool:
        for lg in self.loggers:
            lg.removeHandler(self.cap)
        for lg, level in zip(self.loggers, self.old_levels):
            lg.setLevel(level)
        return False


FLOW_REGS = {
    cf.registers.REG_POWER: 1,
    cf.registers.REG_MODE: 1,
    cf.registers.REG_INLET_WATER_TEMP: 245,
    cf.registers.REG_OUTLET_WATER_TEMP: 232,
    cf.registers.REG_AMBIENT_TEMP: cf.registers.SENTINEL_RAW,
}


class FakeProbeClient:
    """Duck-typed AquaTempBleClient for the flow's test task.

    Scriptable per-call read outcomes (dict = success, Exception =
    raise, "hang" = never returns), scriptable pair result, and a
    recorder for every read's kwargs — the probe's force_response and
    the re-send bounds are asserted through it.
    """

    instances: list["FakeProbeClient"] = []
    script: list = []
    pair_result = "succeeded"
    pair_error: BaseException | None = None

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.script = []
        cls.pair_result = "succeeded"
        cls.pair_error = None

    def __init__(self, address, name=None, hass=None):
        self.address = address
        self.name = name
        self.hass = hass
        self.reads: list[dict] = []
        self.pair_calls = 0
        self.harvest_calls = 0  # spec 013: the push-harvest stage seam
        self.disconnected = False
        self.connect_kwargs = None
        type(self).instances.append(self)

    async def connect(self, max_attempts=None, pair=False):
        self.connect_kwargs = {"max_attempts": max_attempts, "pair": pair}
        return None

    async def read_registers(
        self, start, count, *, resend_every=None, resend_max=0, force_response=False
    ):
        self.reads.append(
            {
                "start": start,
                "count": count,
                "resend_every": resend_every,
                "resend_max": resend_max,
                "force_response": force_response,
            }
        )
        script = type(self).script
        outcome = script[len(self.reads) - 1] if len(self.reads) <= len(script) else None
        if outcome == "hang":
            await asyncio.sleep(30.0)
            return {}
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)

    async def pair(self):
        self.pair_calls += 1
        if type(self).pair_error is not None:
            raise type(self).pair_error
        return type(self).pair_result

    async def harvest_pushes(self, wanted_starts=None, timeout=None):
        # Spec 013 (contract 013 §2): the push-harvest stage after a
        # probe-None verdict — none of the 011 worlds script pushes, so
        # the fake returns partials (empty) and the ORIGINAL
        # no_response path stays exercised byte-identically.
        self.harvest_calls += 1
        return {}

    async def disconnect(self):
        self.disconnected = True


def _make_flow(hass=None, client_cls=FakeProbeClient, script=None,
               pair_result="succeeded", pair_error=None):
    """Build a fresh flow bound to the fake hass. The fake's class state
    is reset FIRST, then the script is applied — reset() clears it, so
    the ordering here is the contract (the walkthrough's fault-after-
    make_flow pattern)."""
    client_cls.reset()
    if script is not None:
        client_cls.script = list(script)
    client_cls.pair_result = pair_result
    client_cls.pair_error = pair_error
    cf.AquaTempBleClient = client_cls
    flow = cf.AquaTempConfigFlow()
    flow.hass = hass or FlowHass(infos=[FlowInfo(ADDRESS, "BlueNRG-C0941C", -50)])
    return flow


async def _drive_progress(flow, result):
    """Mirror HA's FlowManager for SHOW_PROGRESS results (spec 004)."""
    assert result["type"] == "progress", result
    await result["progress_task"]
    result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    assert result["type"] in ("progress", "progress_done"), result
    if result["type"] == "progress_done":
        result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    return result


FIRST_TIMEOUT = "no matching response within 8.0s (received 0 frame(s) / 0 bytes; blocks seen: none; none for start 7)"
SECURITY_CHAIN = (
    "acknowledged write failed: BleakGATTProtocolError: "
    "ATT error: Insufficient Authentication"
)


async def test_flow_success_path_runs_no_probe_no_pair() -> None:
    """AC-4: a first read that SUCCEEDS runs NO probe, NO pair attempt,
    no extra read calls — zero new INFO lines on the success path."""
    flow = _make_flow(script=[dict(FLOW_REGS)])
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    client = FakeProbeClient.instances[-1]
    assert len(client.reads) == 1, client.reads
    assert client.reads[0]["force_response"] is False
    assert client.reads[0]["resend_every"] == 2.0
    assert client.reads[0]["resend_max"] == 3
    assert client.pair_calls == 0
    assert client.disconnected
    assert not any(
        m.startswith("security probe:") or m.startswith("pairing:")
        for m in log.messages
    ), log.messages


async def test_flow_case_closed_probe_pair_retry_answered() -> None:
    """The case-closed walkthrough: first-read timeout → probe
    (acknowledged) → security verdict → pair success → ONE retry with
    the re-send bounds → answered → confirm form. The log carries the
    contract §3 lines verbatim."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(SECURITY_CHAIN),
            dict(FLOW_REGS),
        ],
        pair_result="succeeded",
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    placeholders = result["description_placeholders"]
    assert placeholders["inlet"] == "24.5 °C", placeholders
    client = FakeProbeClient.instances[-1]
    # the three reads, exactly: first (re-send bounds) / probe
    # (acknowledged, no re-send) / retry (re-send bounds unchanged)
    assert len(client.reads) == 3, client.reads
    assert client.reads[0] == {
        "start": 7, "count": 45, "resend_every": 2.0, "resend_max": 3,
        "force_response": False,
    }, client.reads[0]
    assert client.reads[1] == {
        "start": 7, "count": 45, "resend_every": None, "resend_max": 0,
        "force_response": True,
    }, client.reads[1]
    assert client.reads[2] == {
        "start": 7, "count": 45, "resend_every": 2.0, "resend_max": 3,
        "force_response": False,
    }, client.reads[2]
    assert client.pair_calls == 1
    assert client.disconnected
    assert any(
        m.startswith("security probe: verdict att-error-security: ")
        for m in log.messages
    ), log.messages
    assert any(
        m.startswith("security probe: answered after pairing (")
        for m in log.messages
    ), log.messages
    # UI keys unchanged: a success is a success — no new error key
    assert flow._test_error is None


async def test_flow_still_silent_after_pairing_routes_no_response() -> None:
    """FR-4's still-silent outcome: pair succeeded, the retry timed out
    → 'still silent after pairing (census: ...)' and the ORIGINAL
    no_response routing (UI keys unchanged)."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(SECURITY_CHAIN),
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
        ],
        pair_result="succeeded",
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert client.pair_calls == 1
    assert len(client.reads) == 3, client.reads
    assert any(
        m.startswith("security probe: still silent after pairing (census: ")
        for m in log.messages
    ), log.messages


async def test_flow_non_security_verdict_skips_pair_routes_no_response() -> None:
    """A permission verdict logs and routes; NO pair attempt (contract
    §2) — the UI key stays no_response (the original timeout)."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(
                "acknowledged write failed: BleakGATTProtocolError: Write Not Permitted"
            ),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert client.pair_calls == 0
    assert len(client.reads) == 2, client.reads
    assert any(
        m.startswith("security probe: verdict att-error-permission: ")
        for m in log.messages
    ), log.messages


async def test_flow_accepted_silent_verdict_routes_no_response() -> None:
    """att-accepted-silent: the write was accepted, the engine ignored
    it — the verdict line with the census, no pair, no_response."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert client.pair_calls == 0
    assert any(
        m.startswith(
            "security probe: verdict att-accepted-silent "
            "(ack ok, no Modbus answer within 8.0s; census: "
        )
        for m in log.messages
    ), log.messages


async def test_flow_probe_answers_directly_reaches_confirm() -> None:
    """Contract §2 row 3: the acknowledged probe itself answers → the
    test SUCCEEDS (decisive evidence: the write type is the gate)."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            dict(FLOW_REGS),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    client = FakeProbeClient.instances[-1]
    assert client.pair_calls == 0
    assert any(
        "security probe: acknowledged write answered" in m for m in log.messages
    ), log.messages


async def test_flow_budget_expiry_mid_probe_routes_cannot_connect() -> None:
    """AC-5 (contract §4 pathological path): a probe that hangs inside
    the flow's UNCHANGED 70 s envelope expires the budget MID-PROBE —
    the failure line reports the running stage (security-probe) and
    routes to the existing cannot_connect key."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            "hang",
        ],
    )
    original_timeout = cf.TEST_TIMEOUT
    cf.TEST_TIMEOUT = 1.0  # %.0f renders "1" — the pasted line stays greppable
    try:
        with _flow_logs() as log:
            result = await flow.async_step_device(user_input={"address": ADDRESS})
            result = await _drive_progress(flow, result)
    finally:
        cf.TEST_TIMEOUT = original_timeout
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "cannot_connect"}, result
    assert any(
        "exceeded the 1 s budget" in m and "at stage security-probe" in m
        for m in log.messages
    ), log.messages


async def test_flow_pair_stage_wedge_is_bounded_and_skips_retry() -> None:
    """Contract §4's inner bound at the flow level: a wedged pair
    hits the PAIR_TIMEOUT bound (patched small), logs it, skips the
    retry and routes the ORIGINAL no_response — never a hang."""
    FakeProbeClient.script = [
        cf.AquaTempTimeoutError(FIRST_TIMEOUT),
        cf.AquaTempConnectionError(SECURITY_CHAIN),
    ]

    class HangingPairClient(FakeProbeClient):
        async def pair(self):
            self.pair_calls += 1
            await asyncio.sleep(30.0)
            return "succeeded"

    HangingPairClient.reset()
    flow = _make_flow(
        client_cls=HangingPairClient,
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(SECURITY_CHAIN),
        ],
    )
    stub_const = sys.modules[f"{PKG}.const"]
    original_pair_timeout = stub_const.PAIR_TIMEOUT
    stub_const.PAIR_TIMEOUT = 0.2
    try:
        with _flow_logs() as log:
            result = await flow.async_step_device(user_input={"address": ADDRESS})
            result = await _drive_progress(flow, result)
    finally:
        stub_const.PAIR_TIMEOUT = original_pair_timeout
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    client = HangingPairClient.instances[-1]
    assert client.pair_calls == 1
    assert len(client.reads) == 2, "no retry read may follow the wedged pair"
    assert any("pairing budget" in m for m in log.messages), log.messages


async def test_flow_pair_failure_routes_no_response_never_crashes() -> None:
    """A pair that fails (or even raises) never breaks the test: no
    retry, the original no_response routing, the chain in the log."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(SECURITY_CHAIN),
        ],
        pair_error=RuntimeError("org.bluez.Error.AuthenticationFailed"),
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert client.pair_calls == 1
    assert len(client.reads) == 2
    assert any("pairing: failed:" in m for m in log.messages), log.messages


async def test_options_flow_pair_on_connect_toggle() -> None:
    """FR-6: the options flow gains the boolean toggle, default
    DEFAULT_PAIR_ON_CONNECT (OFF), and submits it into the options."""
    flow = cf.AquaTempOptionsFlowHandler()
    # data={} — the real ConfigEntry always carries .data; the options
    # flow reads the entry mode from it (spec 013 FR-5 push gate).
    flow.config_entry = types.SimpleNamespace(options={}, data={})
    result = await flow.async_step_init(None)
    assert result["type"] == "form" and result["step_id"] == "init", result
    schema = result["data_schema"]
    pair_entry = next(
        entry for entry in schema if getattr(entry, "key", None) == "pair_on_connect"
    )
    assert pair_entry.kwargs.get("default") is cf.DEFAULT_PAIR_ON_CONNECT
    assert cf.DEFAULT_PAIR_ON_CONNECT is False  # ships OFF (FR-6)
    # an already-ON entry keeps its value as the default
    flow.config_entry = types.SimpleNamespace(
        options={"pair_on_connect": True}, data={}
    )
    result = await flow.async_step_init(None)
    pair_entry = next(
        entry for entry in result["data_schema"]
        if getattr(entry, "key", None) == "pair_on_connect"
    )
    assert pair_entry.kwargs.get("default") is True
    # submitting stores it
    result = await flow.async_step_init(
        {"poll_interval": 30, "temperature_unit": "C", "pair_on_connect": True}
    )
    assert result["type"] == "create_entry", result
    assert result["data"]["pair_on_connect"] is True, result


# ---------------------------------------------------------------------------
# Part 7 — spec 012: the log-level visibility contract (contract 012 §2/§3)
# ---------------------------------------------------------------------------


async def _drive_probe_flow(script, *, pair_result="succeeded", pair_error=None):
    """One flow walkthrough over a scripted probe outcome, returning the
    routing result and the captured (message, levelname) pairs (spec 012
    FR-3: the caps record levels since this part — the suite asserts the
    operator-visible LEVEL, not just the message string)."""
    flow = _make_flow(script=script, pair_result=pair_result, pair_error=pair_error)
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    return result, list(zip(log.messages, log.levels))


async def test_flow_probe_evidence_lines_are_warning() -> None:
    """Spec 012 FR-1/AC-2 (contract 012 §2): the att-accepted-silent
    walkthrough — the probe entry line and the verdict line are at
    WARNING, and ZERO `security probe:`/`pairing:` records exist at any
    other level. The operator's system_log panel stores WARNING+ only
    (research 012 §4): below WARNING the evidence is operator-invisible."""
    result, records = await _drive_probe_flow(
        [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempTimeoutError(FIRST_TIMEOUT)]
    )
    assert result["errors"] == {"base": "no_response"}, result
    evidence = [
        (m, lvl)
        for m, lvl in records
        if m.startswith("security probe:") or m.startswith("pairing:")
    ]
    assert evidence, records
    assert all(lvl == "WARNING" for _m, lvl in evidence), evidence
    # both decisive lines of this walkthrough are captured at WARNING
    assert any(
        m.startswith("security probe: re-sending base read as acknowledged write")
        for m, _lvl in evidence
    ), evidence
    assert any(
        m.startswith("security probe: verdict att-accepted-silent")
        for m, _lvl in evidence
    ), evidence


async def test_flow_all_probe_evidence_lines_are_warning() -> None:
    """Spec 012 FR-1 (contract 012 §2): EVERY probe evidence line the
    flow can emit — the entry line, the classified verdict, the
    accepted-silent verdict, the Modbus-exception frame, the answered
    write, the probe pair-catch failure, still-silent-after-pairing and
    answered-after-pairing — is at WARNING in every walkthrough that
    exercises it (zero probe/pair records at any other level)."""
    scenarios = [
        ("accepted-silent", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempTimeoutError(FIRST_TIMEOUT)], {}),
        ("case-closed", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempConnectionError(SECURITY_CHAIN), dict(FLOW_REGS)], {}),
        ("modbus-exception", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempResponseError("Modbus exception frame")], {}),
        ("pair-catch-failure", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempConnectionError(SECURITY_CHAIN)], {"pair_error": RuntimeError("org.bluez.Error.Failed: boom")}),
        ("still-silent", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempConnectionError(SECURITY_CHAIN), cf.AquaTempTimeoutError(FIRST_TIMEOUT)], {}),
        ("answers-directly", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), dict(FLOW_REGS)], {}),
    ]
    seen: list[str] = []
    for name, script, kwargs in scenarios:
        result, records = await _drive_probe_flow(script, **kwargs)
        evidence = [
            (m, lvl)
            for m, lvl in records
            if m.startswith("security probe:") or m.startswith("pairing:")
        ]
        assert evidence, (name, records)
        assert all(lvl == "WARNING" for _m, lvl in evidence), (name, evidence)
        seen.extend(m for m, _lvl in evidence)
    # every contract 012 §2 evidence shape was exercised at WARNING
    prefixes = (
        "security probe: re-sending base read as acknowledged write",
        "security probe: verdict att-error-security: ",
        "security probe: verdict att-accepted-silent",
        "security probe: acknowledged write answered by a Modbus exception frame",
        "security probe: acknowledged write answered — the Write",
        "pairing: failed: ",
        "security probe: still silent after pairing",
        "security probe: answered after pairing",
    )
    for prefix in prefixes:
        assert any(m.startswith(prefix) for m in seen), prefix


async def test_pairing_ladder_lines_are_warning() -> None:
    """Spec 012 FR-1 (contract 012 §2): every pairing-ladder evidence
    line — bond states before/after, the rung A/B/C attempt lines,
    succeeded / skipped / failed / unavailable — is at WARNING. The
    ladder runs on the REAL client (the flow's FakeProbeClient.pair
    replaces it, so these lines are client-level only)."""
    evidence: list[tuple[str, str]] = []

    async def _run_pair(client) -> str:
        with _capture_logs() as cap:
            result = await client.pair()
        evidence.extend(
            (m, lvl)
            for m, lvl in zip(cap.messages, cap.levels)
            if m.startswith("pairing:")
        )
        return result

    # Rung A: success / AlreadyExists-skip / authentication failure.
    assert await _run_pair(_make_probe_client(RecordingBleakClient())) == "succeeded"
    assert await _run_pair(
        _make_probe_client(
            RecordingBleakClient(
                pair_script=[RuntimeError("org.bluez.Error.AlreadyExists: exists")]
            )
        )
    ) == "skipped"
    assert await _run_pair(
        _make_probe_client(
            RecordingBleakClient(
                pair_script=[
                    RuntimeError("org.bluez.Error.AuthenticationFailed: refused")
                ]
            )
        )
    ) == "failed"
    # No rung at all (no pair method, no pair kwarg): unavailable without
    # touching the link (contract 011 §3.3).
    assert (
        await _run_pair(_make_probe_client(_RecordingBleakClientBase()))
        == "unavailable"
    )
    # Rung C, dbus_fast absent: the guarded import reports unavailable.
    with _patch_agent_module(available=False):
        rung_c_client = _make_probe_client(
            RecordingBleakClient(
                pair_script=[
                    RuntimeError("org.bluez.Error.Failed: No agent is registered")
                ]
            )
        )
        assert await _run_pair(rung_c_client) == "unavailable"
    # Rung C, registration refused (register_agent -> None): unavailable.
    with _patch_agent_module():

        async def _register_none():
            return None

        agent.register_agent = _register_none  # type: ignore[method-assign]
        rung_c_client = _make_probe_client(
            RecordingBleakClient(
                pair_script=[
                    RuntimeError("org.bluez.Error.Failed: No agent is registered")
                ]
            )
        )
        assert await _run_pair(rung_c_client) == "unavailable"
    # Rung B (the bleak >= 1.0 shape): success / AlreadyExists-skip /
    # generic failure during the re-establish.
    def _rung_b_client(establish_error: BaseException | None):
        class RungBClient(aqua_client.AquaTempBleClient):
            async def _resolve_device(self) -> object:
                return object()  # a fake BLEDevice

            async def _establish_connection(self, device, *, max_attempts, pair=False):
                if establish_error is not None:
                    raise establish_error
                return RecordingBleakClient()

            async def _post_connect_setup(self, client, device, *, t0, t_resolve, t_connect):
                self._client = client  # the setup "ran"

        client = RungBClient(ADDRESS)
        client._client = _RecordingBleakClientBase()  # the pairless old link
        return client

    old_cls = aqua_client.BleakClient

    class _PairCapableBleakClient:  # the bleak >= 1.0 constructor shape
        def __init__(self, device, *, disconnected_callback=None, timeout=None, pair=False):
            pass

    aqua_client.BleakClient = _PairCapableBleakClient
    try:
        assert await _run_pair(_rung_b_client(None)) == "succeeded"
        assert (
            await _run_pair(
                _rung_b_client(RuntimeError("org.bluez.Error.AlreadyExists: exists"))
            )
            == "skipped"
        )
        assert (
            await _run_pair(
                _rung_b_client(RuntimeError("org.bluez.Error.NetworkUnreachable: gone"))
            )
            == "failed"
        )
    finally:
        aqua_client.BleakClient = old_cls

    assert evidence, "no pairing lines were captured"
    bad = [(m, lvl) for m, lvl in evidence if lvl != "WARNING"]
    assert not bad, bad
    # every contract 012 §2 ladder line shape was exercised at WARNING
    prefixes = (
        "pairing: bond state before: ",
        "pairing: attempting via rung A",
        "pairing: attempting via rung B",
        "pairing: attempting via rung C",
        "pairing: skipped: already bonded",
        "pairing: succeeded",
        "pairing: failed: ",
        "pairing: bond state after: ",
    )
    for prefix in prefixes:
        assert any(m.startswith(prefix) for m, _lvl in evidence), prefix
    # the three distinct 'unavailable' verdicts (capability / dbus_fast
    # absent / registration refused) all ran at WARNING
    unavailable = [m for m, _lvl in evidence if m.startswith("pairing: unavailable: ")]
    assert len(unavailable) >= 3, unavailable
    assert any(
        m.startswith("pairing: unavailable: no pairing agent could be registered")
        for m in unavailable
    ), unavailable
    assert any(
        m.startswith("pairing: unavailable: agent registration failed")
        for m in unavailable
    ), unavailable


async def test_census_lines_stay_debug_counterexample() -> None:
    """Spec 012 counter-example (contract 012 §1.2, amended by
    contract 018-B for the block line): the high-volume census
    diagnostics stay at DEBUG — a 16-push-per-window stream must not
    become WARNING noise (the counter-example discipline stands;
    grep-stable formats unchanged; spec 018 FR-4 moved the per-frame
    block line INFO -> DEBUG — re-pinned here)."""
    push = _build_block_frame(1001)  # a CRC-valid non-matching push
    fake = _RecordingBleakClientBase(answer_frames=[push], answer_on_write=1)
    client = _make_probe_client(fake)
    with _patch_const("REQUEST_TIMEOUT", 0.2), _capture_logs() as cap:
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass
    census = [
        (m, lvl)
        for m, lvl in zip(cap.messages, cap.levels)
        if m.startswith("block push observed")
    ]
    assert census, cap.messages
    assert all(lvl == "DEBUG" for _m, lvl in census), census


async def test_flow_build_marker_logged_once_first() -> None:
    """Spec 012 FR-2/AC-3 (contract 012 §3): every connection test
    starts with EXACTLY ONE build-marker WARNING — the FIRST captured
    record, naming the running build so a stale-module install (the
    files never loaded) is always detectable from the panel."""
    result, records = await _drive_probe_flow([dict(FLOW_REGS)])
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    marker = f"connection test to {ADDRESS}: build {cf.BUILD_VERSION}"
    matches = [(m, lvl) for m, lvl in records if m == marker]
    assert matches == [(marker, "WARNING")], [r for r in records if "build" in r[0]]
    assert records[0] == (marker, "WARNING"), records[:3]


async def test_build_version_matches_manifest() -> None:
    """Spec 012 FR-3c (contract 012 §3.3 — the drift pin): the marker's
    version is read from manifest.json at module import — the marker can
    never lie about the running build, and never breaks the flow
    ('unknown' only on a guarded read failure)."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert cf.BUILD_VERSION == manifest["version"], (
        cf.BUILD_VERSION,
        manifest["version"],
    )
    assert cf.BUILD_VERSION != "unknown"


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_classifier_table_every_contract_row,
        test_probe_bytes_equal_and_response_only_delta,
        test_acknowledged_write_failure_chain_survives,
        test_last_request_write_count_tracks_resends,
        test_probe_security_verdict_pair_then_retry_answers,
        test_probe_security_verdict_pair_fails_no_retry,
        test_probe_non_security_verdict_skips_pair,
        test_probe_accepted_silent_verdict,
        test_probe_answers_directly,
        test_probe_modbus_exception_frame_logged,
        test_probe_pair_stage_bounded_no_retry_after_wedge,
        test_pair_rung_a_success_and_bond_lines,
        test_pair_rung_a_already_exists_skipped,
        test_pair_rung_a_authentication_failure_gets_ble_guidance,
        test_pair_rung_c_registers_agent_and_retries,
        test_pair_rung_c_unavailable_without_dbus_fast,
        test_pair_rung_b_reconnects_with_pair_true,
        test_pair_unavailable_when_backend_cannot_pair,
        test_pair_bond_state_read_via_backend,
        test_pair_bond_state_unknown_without_dbus_fast,
        test_connect_threads_pair_into_establish,
        test_connect_pair_unsupported_degrades_with_warning,
        test_const_pair_values,
        test_option_off_no_probe_no_pair_on_normal_reads,
        test_flow_success_path_runs_no_probe_no_pair,
        test_flow_case_closed_probe_pair_retry_answered,
        test_flow_still_silent_after_pairing_routes_no_response,
        test_flow_non_security_verdict_skips_pair_routes_no_response,
        test_flow_accepted_silent_verdict_routes_no_response,
        test_flow_probe_answers_directly_reaches_confirm,
        test_flow_budget_expiry_mid_probe_routes_cannot_connect,
        test_flow_pair_stage_wedge_is_bounded_and_skips_retry,
        test_flow_pair_failure_routes_no_response_never_crashes,
        test_options_flow_pair_on_connect_toggle,
        test_flow_probe_evidence_lines_are_warning,
        test_flow_all_probe_evidence_lines_are_warning,
        test_pairing_ladder_lines_are_warning,
        test_census_lines_stay_debug_counterexample,
        test_flow_build_marker_logged_once_first,
        test_build_version_matches_manifest,
    ]
    failures = 0
    for test in tests:
        try:
            asyncio.run(test())
        except (AssertionError, AttributeError, TypeError) as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
            continue
        print(f"PASS  {test.__name__}")
    print()
    if failures:
        print(f"FAILED: {failures}/{len(tests)} spec 011 tests")
        return 1
    print(f"PASSED: {len(tests)}/{len(tests)} spec 011 tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())