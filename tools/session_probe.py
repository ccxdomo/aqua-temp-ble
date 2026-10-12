#!/usr/bin/env python3
"""Standalone BLE session probe for the Aqua Temp pool heat pump (spec 009 FR-3).

A raw-bleak MEASUREMENT INSTRUMENT: no Home Assistant, and no
aqua_client import for the RAW phases (plan 009 §4.2 — the probe owns
its bleak client so it can do what the integration forbids:
subscribe-all, write-with-response, phase timing). Phase S (spec 011
FR-9) is the one deliberate exception: it imports the integration's
client so a developer with a terminal reproduces the HA connection
test EXACTLY — no logic duplication (tasks 011 §14, contract 011 §1).
It exists to separate the surviving causes of the silent
first read (research 009 §7: C1 write-never-delivered, C2/C5
answers-after-repeats, C3 push-only firmware, C6 wrong notify char) in
ONE run at the panel — the decision matrix of research 009 §11 is
binding for the follow-up spec.

Phases (plan 009 §4.3 + spec 011 FR-9; run in canonical order
L,B,P,Q,W,E,S):

    L  passive listen --listen s after subscribe — NO TX: push onset,
       cadence, per-start counts (E2/E7)
    B  ONE base read (63030007002d3c54), then listen --b-window s: any
       FC03 answer / exception / late answer (E1/E3)
    P  re-send the base read every --poll-every s, --poll-count sends:
       answers-the-Nth-request (E3/C2/C5)
    Q  ONE block query 1001 (631003e9002dd9e6, app-proven [P]), then
       listen: an IMMEDIATE out-of-cycle 1001 push proves our writes
       reach the engine (E4/C1 vs C3) — the verdict is the gap from the
       previous frame vs the Phase-L baseline median, never "did a
       1001 push occur" (one arrives every cycle anyway)
    W  the base read again but write-WITH-response: ATT errors surface
       — the unobservable write-command path made observable (E4/C1a)
    E  experimental frames (research 009 §8) — ONLY with
       --experimental AND Phase Q in the same run (contract 009 §4)
    S  the security twin (spec 011 FR-9): after the raw session
       closes, reconnect through the integration's OWN client and run
       the HA connection test exactly — first read, acknowledged
       probe, printing the same `security probe:` verdict lines
       (contract 011 §3; spec 021: the pairing ladder and its retry
       were pruned from the integration)

Safety invariants (contract 009 §7): the probe NEVER writes registers
(FC10 frames in the allowlist are the 8-byte query shape only), never
reconnects in a loop, stops at the first decisive phase with
--stop-on-answer, prints the enumerated GATT table at connect (the E4
capture) and tags every RX event with its characteristic under
--subscribe-all. Every TX frame is asserted against the module-level
allowlist (research 009 §8) before it goes out.

Usage:

    python3 tools/session_probe.py --address 08:06:A6:C0:94:1C
    python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --dry-run
    python3 tools/session_probe.py --address ... --phases L,B,P,Q,W,E \\
        --experimental --debug

Requirements: `pip install bleak` (nothing else, no Home Assistant).
Run next to the heat pump with the vendor app CLOSED (single-connection
rule, quickstart 009 §2).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Standalone import of the component's protocol layer (the smoke_client
# pattern, plan 009 §4.1): modbus/const only — NO Home Assistant, NO
# aqua_client (NFR-5, contract 009 §7).
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"),
)

import const  # noqa: E402
import modbus  # noqa: E402

DEFAULT_ADDRESS = "08:06:A6:C0:94:1C"  # the owner's unit (spec 009 FR-3)

# The TX frames the probe may EVER send (research 009 §8, contract 009
# §2). All CRC-correct by construction from the proven builders; the
# experimental pair exists ONLY here, never in the integration.
BASE_READ = modbus.build_read_holding_registers(7, 45)  # 63030007002d3c54 [P]
QUERY_1001 = modbus.build_query_block(1001, 45)  # 631003e9002dd9e6 [P]
QUERY_BLOCKS = frozenset(
    modbus.build_query_block(start, 45)
    for start in (1001, 1046, 1091, 1136, 2001, 2046)  # the app's six [P]
)
EXPERIMENT_QUERY_BASE = modbus.build_query_block(7, 45)  # 63100007002db997 (§8)
EXPERIMENT_FC03_1001 = modbus.build_read_holding_registers(1001, 45)  # 630303e9002d5c25
FRAME_ALLOWLIST = frozenset(
    {BASE_READ}
    | set(QUERY_BLOCKS)
    | {EXPERIMENT_QUERY_BASE, EXPERIMENT_FC03_1001}
)

DEFAULT_PHASES = "L,B,P,Q,W"
PHASE_ORDER = "LBPQWES"  # S — the security twin (spec 011 FR-9) — is always last

E_RISK_STATEMENT = """\
EXPERIMENTAL — these frames were NEVER sent by the vendor app (research 009 §8):
  - 63100007002db997 asks the BASE block in the query-block shape; a sloppy
    parser could read it as a truncated FC10 write (start 7, qty 45,
    bytecount missing) — Modbus would discard it awaiting more bytes [A].
  - 630303e9002d5c25 is an FC03 read at start 1001 — the control isolating
    'FC03 handler off' from 'register 7 absent'.
Neither frame can ever enter the integration (contract 009 §2.4/§4)."""


class ProbePlanError(ValueError):
    """Invalid phase plan (experimental gates, unknown phase letters)."""


@dataclass(frozen=True)
class PhaseSpec:
    """One planned phase (plan 009 §4.1)."""

    name: str                      # L | B | P | Q | W | E
    frames: tuple[bytes, ...]      # distinct TX frames, send order
    repeat: int = 1                # sends per frame (P: poll_count)
    listen_s: float = 15.0         # answer window after the last send
    poll_every: float = 0.0        # P: the cadence between sends
    force_response: bool = False   # W: write-with-response forced


def build_phase_plan(
    phases: str,
    *,
    experimental: bool,
    listen: float,
    b_window: float,
    poll_every: float,
    poll_count: int,
) -> list[PhaseSpec]:
    """Validate + canonicalise the phase plan (plan 009 §4.1, AC-3).

    Raises ProbePlanError for unknown letters, Phase E without
    --experimental, or Phase E without Phase Q in the same run
    (contract 009 §4) — a hard, testable error, never a silent skip.
    """
    letters = [p.strip().upper() for p in phases.split(",") if p.strip()]
    unknown = [p for p in letters if p not in PHASE_ORDER]
    if unknown:
        raise ProbePlanError(
            f"unknown phase(s) {unknown}; valid phases: {','.join(PHASE_ORDER)}"
        )
    ordered = [p for p in PHASE_ORDER if p in set(letters)]
    if "E" in ordered:
        if not experimental:
            raise ProbePlanError(
                "Phase E requires --experimental (contract 009 §4): the "
                "experimental frames are never sent without the explicit flag"
            )
        if "Q" not in ordered:
            raise ProbePlanError(
                "Phase E must follow Phase Q in the same run (contract 009 "
                "§4): the query-shaped base frame is gated on Q's verdict"
            )
    specs: list[PhaseSpec] = []
    for phase in ordered:
        if phase == "L":
            specs.append(PhaseSpec("L", (), 1, listen))
        elif phase == "B":
            specs.append(PhaseSpec("B", (BASE_READ,), 1, b_window))
        elif phase == "P":
            specs.append(
                PhaseSpec(
                    "P", (BASE_READ,), poll_count,
                    (poll_count - 1) * poll_every + b_window,
                    poll_every=poll_every,
                )
            )
        elif phase == "Q":
            specs.append(PhaseSpec("Q", (QUERY_1001,), 1, b_window))
        elif phase == "W":
            specs.append(PhaseSpec("W", (BASE_READ,), 1, b_window, force_response=True))
        elif phase == "E":
            specs.append(
                PhaseSpec("E", (EXPERIMENT_QUERY_BASE, EXPERIMENT_FC03_1001), 1, b_window)
            )
        elif phase == "S":
            # Spec 011 FR-9 (tasks 011 §14): the security twin — NO raw
            # frames; run_security_phase builds its own client session
            # after the raw probe closes (contract 011 §1: import the
            # client, never copy it).
            specs.append(PhaseSpec("S", ()))
    return specs


# ---------------------------------------------------------------------------
# Pure frame helpers (unit-tested in tests/test_session_probe.py)
# ---------------------------------------------------------------------------


def probe_boundary(buf: bytes) -> int | None:
    """Total length of the frame at the head of buf, or None (unknown).

    The probe's own copy of contract 008-A §3's rule (plan 009 §4.2: it
    deliberately does NOT import aqua_client):

    1. wrong slave / too short -> None;
    2. fc & 0x80 -> 5 (exception);
    3. fc == FC03 -> bytecount+5 (needs len >= 3);
    4. fc == FC10 -> block shape (bytecount+9) wins, else the 8-byte
       write echo; below 7 bytes None (never guess — the bytecount byte
       is not visible yet, research 008 §3);
    5. anything else -> None.
    """
    if len(buf) < 2 or buf[0] != modbus.SLAVE_ID:
        return None
    fc = buf[1]
    if fc & 0x80:
        return 5
    if fc == modbus.FC_READ_HOLDING:
        return buf[2] + 5 if len(buf) >= 3 else None
    if fc == modbus.FC_BLOCK:
        if len(buf) < 7:
            return None
        block_len = modbus.block_response_length(buf)
        if block_len is not None:
            return block_len
        return 8
    return None


def _is_block_frame(frame: bytes) -> bool:
    """True for the unit's 99-byte block frame shape (research 008 §2.1)."""
    return (
        len(frame) >= 7
        and frame[0] == modbus.SLAVE_ID
        and frame[1] == modbus.FC_BLOCK
        and modbus.block_response_length(frame) == len(frame)
    )


def _block_start(frame: bytes) -> int:
    return struct.unpack(">H", frame[2:6])[0]


def classify_frame(frame: bytes) -> str:
    """One-line description of a completed frame (probe verdict data)."""
    if len(frame) < 2 or frame[0] != modbus.SLAVE_ID:
        return f"foreign {len(frame)}B"
    fc = frame[1]
    if fc & 0x80:
        code = frame[2] if len(frame) > 2 else 0
        return f"exception fc=0x{fc:02X} code={code}"
    if fc == modbus.FC_READ_HOLDING and len(frame) >= 3:
        return f"FC03 answer {len(frame)}B ({frame[2] // 2} register(s))"
    if _is_block_frame(frame):
        start, qty = struct.unpack(">HH", frame[2:6])
        return f"block start={start} qty={qty} {len(frame)}B"
    if fc == modbus.FC_BLOCK and len(frame) == 8:
        start, qty = struct.unpack(">HH", frame[2:6])
        return f"echo start={start} qty={qty} 8B"
    return f"unknown fc=0x{fc:02X} {len(frame)}B"


def register_dump_lines(frame: bytes) -> list[str]:
    """Per-register probe lines for a block frame — the 008 probe format
    (plan 009 §4.1: 'reg NNN = raw (0x..) | /10 = ..')."""
    if not _is_block_frame(frame):
        return []
    start, qty = struct.unpack(">HH", frame[2:6])
    lines = []
    for i in range(qty):
        raw = struct.unpack(">h", frame[7 + 2 * i : 9 + 2 * i])[0]
        addr = start + i
        lines.append(
            f"    reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) "
            f"| /10 = {raw / 10:7.1f}"
        )
    return lines


def cadence_stats(times: list[float]) -> tuple[float, float, float] | None:
    """(min, median, max) of the consecutive gaps; None below 2 stamps."""
    if len(times) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(times, times[1:]))
    return gaps[0], statistics.median(gaps), gaps[-1]


def query_reaction(
    frames: list[tuple[float, str]], tx_t: float
) -> tuple[float | None, str] | None:
    """First post-TX block-1001 frame's inter-frame gap (plan 009 §4.3).

    frames: (t, classify_frame(frame)) in arrival order — the probe's
    whole timeline, any frame kind. Returns None when no 1001 block
    frame followed the TX; (gap, detail) otherwise — gap None when the
    1001 had no preceding frame (nothing to measure against).
    """
    prev_t: float | None = None
    for t, desc in frames:
        if t > tx_t and "block start=1001" in desc:
            if prev_t is None:
                return (
                    None,
                    f"first frame of the timeline at t={t:.3f}s — no "
                    "pre-TX frame to measure the gap against",
                )
            gap = t - prev_t
            return (
                gap,
                f"first post-TX 1001 at t={t:.3f}s, gap {gap:.3f}s "
                f"from the previous frame (t={prev_t:.3f}s)",
            )
        prev_t = t
    return None


def query_verdict(
    baseline_median: float | None, reaction: tuple[float | None, str] | None
) -> str:
    """Phase Q verdict per plan 009 §4.3: POSITIVE only for an immediate
    out-of-cycle reaction (gap below 50% of the Phase-L baseline median)
    — an in-cycle 1001 arrival proves nothing (one arrives every
    ~cycle anyway), and no baseline is reported as INCONCLUSIVE, never
    guessed."""
    if reaction is None:
        return (
            "NEGATIVE — no 1001 block frame followed the query within the "
            "window: the write pipe is the suspect (C1 alive, research 009 §11)"
        )
    gap, detail = reaction
    if gap is None:
        return f"INCONCLUSIVE — {detail}"
    if baseline_median is None:
        return (
            "INCONCLUSIVE — a reaction arrived but there is no Phase-L "
            "cadence baseline to compare (run Phase L)"
        )
    if gap < 0.5 * baseline_median:
        return (
            f"POSITIVE — out-of-cycle reaction: {detail} "
            f"(below 50% of the baseline median {baseline_median:.2f}s) — "
            "writes DO reach the engine (C1 dead for queries, C3 dead)"
        )
    return (
        f"NEGATIVE — in-cycle arrival: {detail} "
        f"(>= 50% of the baseline median {baseline_median:.2f}s) — the "
        "cycle's next 1001 slot, not a reaction"
    )


def phase_e_frames(q_positive: bool) -> tuple[bytes, ...]:
    """Contract 009 §4: the query-shaped base frame may be sent ONLY
    after Phase Q proved the engine reacts to query-shaped frames on
    the current write pipe (Q negative -> refused: it would test
    nothing and adds an unproven frame to the link); the FC03-at-1001
    control stays allowed either way."""
    if q_positive:
        return (EXPERIMENT_QUERY_BASE, EXPERIMENT_FC03_1001)
    return (EXPERIMENT_FC03_1001,)


def analyze_answers(
    frames: list[tuple[float, bytes]], start: int | None
) -> tuple[str | None, str]:
    """First answer-shaped frame in a window (probe verdict data):
    exception, FC03 answer, or a block frame echoing `start` (the
    spec-008 matching shape for base reads). start=None: only
    FC03/exception count — in-cycle block pushes are noise for that
    request."""
    for t, frame in frames:
        if len(frame) < 2 or frame[0] != modbus.SLAVE_ID:
            continue
        fc = frame[1]
        if fc & 0x80:
            code = frame[2] if len(frame) > 2 else 0
            return "exception", f"exception code {code} at t={t:.3f}s"
        if fc == modbus.FC_READ_HOLDING and len(frame) >= 3:
            return (
                "fc03",
                f"FC03 answer {len(frame)}B ({frame[2] // 2} register(s)) at t={t:.3f}s",
            )
        if (
            start is not None
            and _is_block_frame(frame)
            and _block_start(frame) == start
        ):
            return (
                "block",
                f"start-{start} block frame {len(frame)}B (the spec-008 "
                f"matching shape) at t={t:.3f}s",
            )
    return None, ""


def _crc_note(frame: bytes) -> str:
    """CRC verdict for the dry-run print (every frame CRC-verified)."""
    if len(frame) >= 4 and modbus.validate_crc(frame):
        crc = struct.unpack("<H", frame[-2:])[0]
        return f"CRC OK (0x{crc:04X})"
    return "CRC FAIL"


def build_dry_run_text(
    address: str,
    plan: list[PhaseSpec],
    *,
    write_with_response: bool,
    subscribe_all: bool,
) -> str:
    """The --dry-run print: the phase plan and the EXACT frames it would
    send with their CRC verdicts — never connects (AC-3, tasks 009 3.1)."""
    lines = [
        "session_probe DRY RUN — no connection will be made",
        f"address: {address}",
        f"phases: {','.join(spec.name for spec in plan)}",
    ]
    if subscribe_all:
        lines.append("subscribe: ALL notify/indicate characteristics (C6)")
    else:
        lines.append("subscribe: ff02 (vendor notify), fallback first vendor char")
    lines.append(
        "write mode: "
        + (
            "with-response (flag: every phase)"
            if write_with_response
            else "without-response (vendor parity; Phase W forces with-response)"
        )
    )
    for spec in plan:
        if spec.name == "L":
            lines.append(
                f"=== PHASE L: passive listen {spec.listen_s:.0f} s — NO TX "
                "(push onset / cadence / per-start counts, E2/E7) ==="
            )
        elif spec.name == "B":
            lines.append(
                f"=== PHASE B: TX {BASE_READ.hex()} ({_crc_note(BASE_READ)}) "
                f"base read start=7 count=45 — listen {spec.listen_s:.0f} s "
                "(any answer / exception / late answer, E1/E3) ==="
            )
        elif spec.name == "P":
            lines.append(
                f"=== PHASE P: TX {BASE_READ.hex()} ({_crc_note(BASE_READ)}) "
                f"every {spec.poll_every:.1f} s × {spec.repeat} — "
                f"late-answer window {spec.listen_s:.0f} s "
                "(answers-the-Nth-request, E3/C2/C5) ==="
            )
        elif spec.name == "Q":
            lines.append(
                f"=== PHASE Q: TX {QUERY_1001.hex()} ({_crc_note(QUERY_1001)}) "
                f"query block start=1001 count=45 — listen {spec.listen_s:.0f} s "
                "(immediate out-of-cycle reaction? E4/C1 vs C3) ==="
            )
        elif spec.name == "W":
            lines.append(
                f"=== PHASE W: TX {BASE_READ.hex()} ({_crc_note(BASE_READ)}) "
                f"write-WITH-response — listen {spec.listen_s:.0f} s "
                "(ATT errors surface, E4/C1a) ==="
            )
        elif spec.name == "E":
            lines.append(
                "=== PHASE E: EXPERIMENTAL — gated frames (contract 009 §4) ==="
            )
            for risk_line in E_RISK_STATEMENT.splitlines():
                lines.append(f"    {risk_line}")
            for frame in spec.frames:
                gate = (
                    "sent ONLY if Phase Q is POSITIVE"
                    if frame == EXPERIMENT_QUERY_BASE
                    else "the control experiment (allowed under the flag)"
                )
                lines.append(
                    f"    frame {frame.hex()} ({_crc_note(frame)}) — {gate}"
                )
        elif spec.name == "S":
            lines.append(
                "=== PHASE S: security probe — the HA connection "
                "test twin (spec 011 FR-9): after the raw session closes, "
                "reconnect through the integration's own client and run "
                "first read → acknowledged probe, printing the same "
                "'security probe:' verdict lines (contract 011 §3; "
                "spec 021: the pairing ladder was pruned from the "
                "integration) ==="
            )
    lines.append(
        f"allowlist: every TX frame is one of the {len(FRAME_ALLOWLIST)} "
        "allowlisted probe frames (research 009 §8); TX asserts membership"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GATT helpers (the aqua_client robustness patterns, standalone copies)
# ---------------------------------------------------------------------------

_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def _char_props(obj: object) -> list[str]:
    props = getattr(obj, "properties", None)
    if isinstance(props, (list, tuple, set, frozenset)):
        return [str(p) for p in props]
    return []


def _iter_chars(service: object) -> list:
    chars_obj = getattr(service, "characteristics", None)
    if chars_obj is None:
        return []
    values = getattr(chars_obj, "values", None)
    if callable(values):
        try:
            return list(values())
        except Exception:  # noqa: BLE001
            pass
    try:
        return list(chars_obj)
    except Exception:  # noqa: BLE001
        return []


def _alias(uuid: str) -> int | None:
    text = str(uuid).strip().lower()
    if len(text) == 36 and text.startswith("0000") and text[8:] == _UUID_SUFFIX:
        try:
            return int(text[4:8], 16)
        except ValueError:
            return None
    return None


def _is_vendor_service(svc_uuid: str) -> bool:
    """True unless the service UUID is a standard SIG service (the
    aqua_client heuristic: standard services never host the vendor pipe)."""
    alias = _alias(svc_uuid)
    return alias is None or not (0x1800 <= alias <= 0x18FF)


# ---------------------------------------------------------------------------
# The probe session (the bleak flow — verified by the owner, quickstart §2)
# ---------------------------------------------------------------------------


class ProbeSession:
    """One probe run: the GATT capture, the event timeline, the phases.

    Deliberately does NOT import aqua_client (plan 009 §4.2): the probe
    manages its own notifications (subscribe-all + per-char tagging)
    and its own reassembly (probe_boundary, contract 008-A §3).
    """

    def __init__(self, args: argparse.Namespace, plan: list[PhaseSpec]) -> None:
        self.args = args
        self.plan = plan
        self.client = None
        self.t0 = 0.0                       # run start (display stamps)
        self.subscribe_elapsed = 0.0        # the subscribe moment (E2)
        self.frames: list[tuple[float, bytes]] = []   # (elapsed, frame)
        self.block_times: list[float] = []            # elapsed stamps
        self.tx_count = 0
        self.phase_verdicts: list[tuple[str, str]] = []
        self.l_baseline: float | None = None   # Phase L cadence median
        self.q_positive: bool | None = None    # Phase Q's verdict
        self.write_uuid: str | None = None
        self.write_mode = False                # False = without-response
        self.disconnected = asyncio.Event()
        self._buf = bytearray()

    # ------------------------------------------------------------ stamps
    def _stamp(self, elapsed: float) -> str:
        return f"[{elapsed:08.3f}]"

    # ------------------------------------------------------- notifications
    def _make_handler(self, char_uuid: str):
        """Per-character notify handler (bleak callback shapes vary)."""

        def handler(*args):
            data = args[-1] if args else b""
            sender = args[0] if len(args) >= 2 else None
            uuid = str(getattr(sender, "uuid", char_uuid) or char_uuid)
            self._on_rx(uuid, bytes(data))

        return handler

    def _on_rx(self, uuid: str, data: bytes) -> None:
        elapsed = time.monotonic() - self.t0
        if self.args.debug and data:
            print(f"{self._stamp(elapsed)} chunk {len(data)}B {data.hex()} on {uuid}")
        self._buf.extend(data)
        if len(self._buf) > const.MAX_RX_BUFFER:
            # Honest instrument (contract 009 §7): never silently
            # truncate — clear loudly, keep the census honest.
            print(
                f"{self._stamp(elapsed)} !! receive buffer over "
                f"{const.MAX_RX_BUFFER}B ({len(self._buf)}B) — clearing"
            )
            self._buf.clear()
            return
        while True:
            expected = probe_boundary(bytes(self._buf))
            if expected is None or len(self._buf) < expected:
                break
            frame = bytes(self._buf[:expected])
            del self._buf[:expected]
            crc_ok = modbus.validate_crc(frame)
            desc = classify_frame(frame)
            print(
                f"{self._stamp(elapsed)} RX {len(frame)}B {desc} "
                f"crc={'OK' if crc_ok else 'FAIL'} on {uuid}"
            )
            if not crc_ok:
                continue
            self.frames.append((elapsed, frame))
            if _is_block_frame(frame):
                self.block_times.append(elapsed)
                for line in register_dump_lines(frame):
                    print(line)

    # ----------------------------------------------------------------- TX
    async def _tx(self, frame: bytes, with_response: bool, label: str) -> float:
        """Send one allowlisted frame; returns the TX elapsed stamp.
        Membership is asserted (plan 009 §4.4) — an unproven frame can
        never reach the link by accident."""
        assert frame in FRAME_ALLOWLIST, (
            f"TX frame {frame.hex()} is not in the probe allowlist "
            "(research 009 §8) — refusing to send"
        )
        elapsed = time.monotonic() - self.t0
        mode = "with-response" if with_response else "without-response"
        print(f"{self._stamp(elapsed)} TX {frame.hex()} ({label}, {mode})")
        await self.client.write_gatt_char(
            self.write_uuid, bytes(frame), response=with_response
        )
        self.tx_count += 1
        return elapsed

    # -------------------------------------------------------------- GATT
    def enumerate_gatt(self) -> list[tuple[str, str, list[str]]]:
        """Walk + print the device's services (the E4 capture, plan 009
        §4.2): every service/char/properties goes to stdout."""
        chars: list[tuple[str, str, list[str]]] = []
        services_obj = getattr(self.client, "services", None)
        if services_obj is None:
            print("GATT table: the client exposes no services object")
            return chars
        print("GATT table:")
        try:
            for service in services_obj:
                svc_uuid = str(getattr(service, "uuid", "") or "").lower()
                svc_handle = getattr(service, "handle", None)
                svc_chars = _iter_chars(service)
                print(
                    f"  service {svc_uuid} (handle {svc_handle}): "
                    f"{len(svc_chars)} char(s)"
                )
                for char in svc_chars:
                    uuid = str(getattr(char, "uuid", "") or "").lower()
                    props = _char_props(char)
                    print(
                        f"    char {uuid} (handle {getattr(char, 'handle', None)}) "
                        f"properties {props}"
                    )
                    chars.append((svc_uuid, uuid, props))
        except Exception as exc:  # noqa: BLE001
            print(f"GATT table walk failed: {type(exc).__name__}: {exc}")
        if not chars:
            # Enumeration fallback: direct UUID lookups (the client's
            # best-effort pattern) so the app-bucket UUIDs still resolve
            # (spec 010: ff02/2216/2a00 + ff03/2215/2aa6).
            for uuid in (
                *const.UUID_NOTIFY_BUCKETS,
                *const.UUID_WRITE_BUCKETS,
            ):
                try:
                    char = services_obj.get_characteristic(uuid)
                except Exception:  # noqa: BLE001
                    char = None
                if char is not None:
                    props = _char_props(char)
                    print(
                        f"  char {uuid} (handle {getattr(char, 'handle', None)}) "
                        f"properties {props} (direct lookup)"
                    )
                    chars.append(("", uuid, props))
        return chars

    def resolve_notify_chars(
        self, chars: list[tuple[str, str, list[str]]]
    ) -> list[tuple[str, list[str]]]:
        """Mirror of the production resolution (spec 010 FR-2): the
        app's notify bucket {ff02, 2216, 2a00} first, property-blind
        (research 010 §1); else the first vendor notify/indicate char;
        --subscribe-all takes EVERY notify/indicate char (C6)."""
        notify_pool = [
            (uuid, props)
            for _svc, uuid, props in chars
            if "notify" in props or "indicate" in props
        ]
        if self.args.subscribe_all:
            print(
                f"subscribe-all: {len(notify_pool)} notify/indicate "
                "characteristic(s) (C6)"
            )
            return notify_pool
        by_props = {uuid: props for _svc, uuid, props in chars}
        for uuid in const.UUID_NOTIFY_BUCKETS:
            if uuid in by_props:
                print(
                    f"notify characteristic: {uuid} "
                    f"(properties={by_props[uuid]}) — app bucket (research 010 §1)"
                )
                return [(uuid, by_props[uuid])]
        vendor = [
            (uuid, props)
            for (svc, uuid, props) in notify_pool
            if _is_vendor_service(svc)
        ]
        if vendor:
            print(
                f"notify characteristic: {vendor[0][0]} (properties={vendor[0][1]}) "
                "— vendor fallback (bucket UUIDs absent)"
            )
            return [vendor[0]]
        raise ProbePlanError(
            "no notify/indicate characteristic in the enumerated table"
        )

    def resolve_write_char(
        self, chars: list[tuple[str, str, list[str]]]
    ) -> None:
        """Mirror of the production resolution (spec 010 FR-2): the
        app's write bucket {ff03, 2215, 2aa6} first, property-blind
        (research 010 §1); else the vendor writable candidate. The
        chosen path + properties are PRINTED — the E4 second half
        (which write-resolution line the production log shows)."""
        by_uuid = {uuid: props for _svc, uuid, props in chars}
        uuid: str | None = None
        path = ""
        for bucket_uuid in const.UUID_WRITE_BUCKETS:
            if bucket_uuid in by_uuid:
                uuid, path = bucket_uuid, "app bucket (research 010 §1)"
                break
        if uuid is None:
            writable = [
                (svc, u, props)
                for svc, u, props in chars
                if ("write" in props or "write-without-response" in props)
                and _is_vendor_service(svc)
            ]
            notify_svcs = {
                svc
                for svc, _u, props in chars
                if "notify" in props or "indicate" in props
            }
            pool = [c for c in writable if c[0] in notify_svcs] or writable
            if pool:
                uuid, path = pool[0][1], "vendor writable candidate"
        if uuid is None:
            raise ProbePlanError(
                "no usable write characteristic in the enumerated table"
            )
        props = by_uuid[uuid]
        if "write-without-response" in props:
            self.write_mode = False
        elif "write" in props:
            self.write_mode = True
        else:
            # App parity (spec 010 FR-3, research 010 §1/§2.3): the app
            # writes withoutResponse regardless — no write property
            # advertised still means without-response, said loudly: a
            # write command here may be silently dropped (research 009
            # §4 Q4 — the Phase W datum).
            self.write_mode = False
            print(
                f"!! write characteristic {uuid} advertises NO write property "
                f"({props}) — a write command may be silently dropped "
                "(research 009 §4)"
            )
        self.write_uuid = uuid
        print(
            f"write characteristic: {uuid} properties {props} path={path} "
            f"mode={'with-response' if self.write_mode else 'without-response'}"
        )

    # -------------------------------------------------------------- phases
    async def run_phase(self, spec: PhaseSpec) -> bool:
        """Run one phase; returns True when its result is decisive
        (--stop-on-answer, contract 009 §7)."""
        if self.disconnected.is_set():
            print(f"link lost — Phase {spec.name} skipped")
            return False
        if spec.name == "L":
            return await self._phase_listen(spec)
        if spec.name == "B":
            return await self._phase_base_read(spec)
        if spec.name == "P":
            return await self._phase_poll(spec)
        if spec.name == "Q":
            return await self._phase_query(spec)
        if spec.name == "W":
            return await self._phase_write_type(spec)
        if spec.name == "E":
            return await self._phase_experimental(spec)
        raise ProbePlanError(f"unknown phase {spec.name}")  # unreachable

    async def _phase_listen(self, spec: PhaseSpec) -> bool:
        print(f"=== PHASE L: passive listen {spec.listen_s:.0f} s — NO TX ===")
        idx = len(self.frames)
        await asyncio.sleep(spec.listen_s)
        blocks = [(e, f) for e, f in self.frames[idx:] if _is_block_frame(f)]
        starts = Counter(_block_start(f) for _e, f in blocks)
        if blocks:
            onset = blocks[0][0] - self.subscribe_elapsed
            stats = cadence_stats([e for e, _f in blocks])
            cadence = (
                f"cadence min/median/max "
                f"{stats[0]:.2f}/{stats[1]:.2f}/{stats[2]:.2f} s"
                if stats
                else "cadence n/a (single frame)"
            )
            verdict = (
                f"push onset: t+{onset:.2f} s after subscribe; {cadence}; "
                "starts: "
                + ", ".join(f"{s}x{n}" for s, n in sorted(starts.items()))
            )
            if stats:
                self.l_baseline = stats[1]
        else:
            verdict = (
                f"no block pushes during {spec.listen_s:.0f} s — onset later "
                "than the listen window (E2 datum in itself)"
            )
        print(f"PHASE L verdict: {verdict}")
        self.phase_verdicts.append(("L", verdict))
        return False  # observation only, never decisive

    async def _phase_base_read(self, spec: PhaseSpec) -> bool:
        print(
            f"=== PHASE B: TX {BASE_READ.hex()} (base read) — listen "
            f"{spec.listen_s:.0f} s ==="
        )
        idx = len(self.frames)
        await self._tx(
            BASE_READ, self.args.write_with_response or spec.force_response,
            "base read",
        )
        await asyncio.sleep(spec.listen_s)
        kind, detail = analyze_answers(self.frames[idx:], start=7)
        verdict = f"answer: {detail}" if kind else f"none within {spec.listen_s:.0f} s"
        print(f"PHASE B verdict: {verdict}")
        self.phase_verdicts.append(("B", verdict))
        return kind is not None

    async def _phase_poll(self, spec: PhaseSpec) -> bool:
        print(
            f"=== PHASE P: poll {BASE_READ.hex()} every {spec.poll_every:.1f} s "
            f"× {spec.repeat} ==="
        )
        answered: tuple[int, str] | None = None
        for i in range(spec.repeat):
            idx = len(self.frames)
            await self._tx(
                BASE_READ, self.args.write_with_response,
                f"poll {i + 1}/{spec.repeat}",
            )
            await asyncio.sleep(spec.poll_every)
            kind, detail = analyze_answers(self.frames[idx:], start=7)
            if kind and answered is None:
                answered = (i + 1, detail)
                if self.args.stop_on_answer:
                    break
        if answered is None and not self.args.stop_on_answer:
            # late-answer tail past the last send (E3's 8 s+ question)
            idx = len(self.frames)
            await asyncio.sleep(self.args.b_window)
            kind, detail = analyze_answers(self.frames[idx:], start=7)
            if kind:
                answered = (spec.repeat, detail + " (late, after the last send)")
        verdict = (
            f"answered on attempt {answered[0]}: {answered[1]}"
            if answered
            else f"none in {spec.repeat} attempts"
        )
        print(f"PHASE P verdict: {verdict}")
        self.phase_verdicts.append(("P", verdict))
        return answered is not None

    async def _phase_query(self, spec: PhaseSpec) -> bool:
        print(
            f"=== PHASE Q: TX {QUERY_1001.hex()} (query 1001) — listen "
            f"{spec.listen_s:.0f} s ==="
        )
        idx = len(self.frames)
        tx_elapsed = await self._tx(
            QUERY_1001, self.args.write_with_response, "query block 1001"
        )
        await asyncio.sleep(spec.listen_s)
        reaction = query_reaction(
            [(e, classify_frame(f)) for e, f in self.frames], tx_elapsed
        )
        baseline = self.l_baseline
        if baseline is None:
            # no Phase L in this run: fall back to the pre-TX block
            # cadence observed earlier in the same connection
            pre = [e for e, f in self.frames[:idx] if _is_block_frame(f)]
            stats = cadence_stats(pre)
            if stats:
                baseline = stats[1]
        verdict = query_verdict(baseline, reaction)
        self.q_positive = verdict.startswith("POSITIVE")
        print(f"PHASE Q verdict: {verdict}")
        self.phase_verdicts.append(("Q", verdict))
        return bool(self.q_positive)

    async def _phase_write_type(self, spec: PhaseSpec) -> bool:
        print(
            f"=== PHASE W: TX {BASE_READ.hex()} write-WITH-response — listen "
            f"{spec.listen_s:.0f} s ==="
        )
        idx = len(self.frames)
        try:
            await self._tx(BASE_READ, True, "base read (write type experiment)")
        except Exception as exc:  # noqa: BLE001 — the ATT error IS the datum
            verdict = (
                f"ATT error: {type(exc).__name__}: {exc} — the write pipe "
                "is REFUSED (C1a)"
            )
            decisive = True
        else:
            await asyncio.sleep(spec.listen_s)
            kind, detail = analyze_answers(self.frames[idx:], start=7)
            verdict = (
                f"write accepted, then {detail}"
                if kind
                else f"write accepted, no reaction within {spec.listen_s:.0f} s"
            )
            decisive = kind is not None
        print(f"PHASE W verdict: {verdict}")
        self.phase_verdicts.append(("W", verdict))
        return decisive

    async def _phase_experimental(self, spec: PhaseSpec) -> bool:
        print("=== PHASE E: experimental (contract 009 §4) ===")
        for line in E_RISK_STATEMENT.splitlines():
            print(f"    {line}")
        if self.q_positive is not True:
            print(
                f"    refused: {EXPERIMENT_QUERY_BASE.hex()} — Phase Q did "
                "not prove the engine reacts to query-shaped frames on "
                "this pipe (contract 009 §4)"
            )
        decisive = False
        for frame in phase_e_frames(bool(self.q_positive)):
            idx = len(self.frames)
            label = (
                "query-shaped base read"
                if frame == EXPERIMENT_QUERY_BASE
                else "FC03 read at 1001 (control)"
            )
            await self._tx(frame, self.args.write_with_response, label)
            await asyncio.sleep(spec.listen_s)
            # the query-shaped frame asks the base block (start 7 counts);
            # the FC03 control only counts an FC03 answer — in-cycle
            # block pushes are noise for it (plan 009 §4.3)
            start = 7 if frame == EXPERIMENT_QUERY_BASE else None
            kind, detail = analyze_answers(self.frames[idx:], start=start)
            verdict = (
                f"{frame.hex()}: answer — {detail}"
                if kind
                else f"{frame.hex()}: none within {spec.listen_s:.0f} s"
            )
            decisive = decisive or kind is not None
            print(f"PHASE E verdict: {verdict}")
            self.phase_verdicts.append(("E", verdict))
        return decisive

    # -------------------------------------------------------------- summary
    def print_summary(self) -> None:
        print("\n=== SUMMARY ===")
        blocks = [f for _e, f in self.frames if _is_block_frame(f)]
        starts = Counter(_block_start(f) for f in blocks)
        print(
            f"frames received: {len(self.frames)} "
            f"({len(blocks)} block, {len(self.frames) - len(blocks)} other); "
            f"TX frames: {self.tx_count}"
        )
        if starts:
            print(
                "block starts: "
                + ", ".join(f"{s}x{n}" for s, n in sorted(starts.items()))
            )
        stats = cadence_stats(self.block_times)
        if stats:
            print(
                f"block cadence: min/median/max "
                f"{stats[0]:.2f}/{stats[1]:.2f}/{stats[2]:.2f} s "
                f"({len(self.block_times)} frames)"
            )
        for name, verdict in self.phase_verdicts:
            print(f"  {name}: {verdict}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """The CLI (AC-3: --help documents every phase and every flag)."""
    parser = argparse.ArgumentParser(
        description=(
            "Aqua Temp BLE session probe (spec 009 FR-3) — a raw-bleak "
            "measurement instrument: listens, sends ONE frame per phase, "
            "polls, queries a block, tries write-with-response and "
            "subscribes to every notifying characteristic, printing a "
            "full timestamped timeline (research 009 §11 decision data)."
        ),
        epilog=(
            "phases (comma-separated, run in canonical order L,B,P,Q,W,E):\n"
            "  L  passive listen --listen s after subscribe — NO TX: push\n"
            "     onset, cadence, per-start counts (E2/E7)\n"
            "  B  ONE base read (63030007002d3c54), then listen\n"
            "     --b-window s: any answer/exception/late answer (E1/E3)\n"
            "  P  poll the base read every --poll-every s, --poll-count\n"
            "     sends: answers-the-Nth-request (E3/C2/C5)\n"
            "  Q  ONE block query 1001 (631003e9002dd9e6): an immediate\n"
            "     out-of-cycle 1001 push proves writes reach the engine\n"
            "     (E4/C1 vs C3)\n"
            "  W  the base read with write-WITH-response: ATT errors\n"
            "     surface (E4/C1a)\n"
            "  E  experimental frames (research 009 §8) — requires\n"
            "     --experimental AND Phase Q in the same run (contract\n"
            "     009 §4)\n"
            "  S  the security twin (spec 011 FR-9): after the raw session\n"
            "     closes, reconnect through the integration's own client\n"
            "     and run the HA connection test exactly — first read,\n"
            "     acknowledged probe, with the same 'security probe:'\n"
            "     verdict lines (contract 011 §3; the pairing ladder was\n"
            "     pruned from the integration, spec 021)\n"
            "\n"
            "The probe NEVER writes registers (contract 009 §7); every TX\n"
            "frame is asserted against the allowlist before it goes out.\n"
            "Run next to the unit with the vendor app CLOSED (the\n"
            "single-connection rule)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS,
        help=f"BLE address of the unit (default: {DEFAULT_ADDRESS})",
    )
    parser.add_argument(
        "--phases", default=DEFAULT_PHASES,
        help=(
            f"comma-separated phases to run, canonical order "
            f"(default: {DEFAULT_PHASES}; E only with --experimental)"
        ),
    )
    parser.add_argument(
        "--listen", type=float, default=30.0,
        help="Phase L passive listen window in seconds (default: 30)",
    )
    parser.add_argument(
        "--b-window", type=float, default=15.0,
        help="answer window after B/Q/W/E sends in seconds (default: 15)",
    )
    parser.add_argument(
        "--poll-every", type=float, default=2.0,
        help="Phase P poll cadence in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--poll-count", type=int, default=15,
        help="Phase P poll sends (default: 15)",
    )
    parser.add_argument(
        "--subscribe-all", action="store_true",
        help="subscribe to EVERY notify/indicate characteristic and tag "
             "each event with its char (C6)",
    )
    parser.add_argument(
        "--write-with-response", action="store_true",
        help="send every phase's TX write-WITH-response (Phase W always "
             "does)",
    )
    parser.add_argument(
        "--experimental", action="store_true",
        help="unlock Phase E (the gated frames, contract 009 §4)",
    )
    parser.add_argument(
        "--stop-on-answer", action="store_true",
        help="stop at the first decisive phase result (contract 009 §7)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the phase plan and the exact frames (with CRCs) "
             "WITHOUT connecting",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="print every raw notification chunk",
    )
    return parser


async def run_probe(args: argparse.Namespace, plan: list[PhaseSpec]) -> int:
    """The bleak flow (verified by the owner at the panel, quickstart §2):
    connect -> print the GATT table (E4) -> subscribe -> settle ->
    phases -> summary. One connection for the whole run (never
    reconnects in a loop, contract 009 §7)."""
    # Lazy import on purpose: the pure helpers + --dry-run run without
    # bleak installed (tests/test_session_probe.py covers them).
    from bleak import BleakClient

    # Spec 011 FR-9: Phase S never runs inside the raw session — it
    # reconnects through the integration's own client AFTER the raw
    # link closes (the single-connection rule, spec 006 §9).
    session_plan = [spec for spec in plan if spec.name != "S"]
    session = ProbeSession(args, session_plan)
    session.t0 = time.monotonic()

    def _on_disconnect(_client) -> None:
        print("!! BLE link lost")
        session.disconnected.set()

    print(f"session probe: {args.address}")
    print(
        f"phases: {','.join(spec.name for spec in plan)} "
        f"(write-with-response: {args.write_with_response}, "
        f"subscribe-all: {args.subscribe_all}, "
        f"stop-on-answer: {args.stop_on_answer})"
    )
    client = BleakClient(args.address, disconnected_callback=_on_disconnect)
    print(f"[{0:08.3f}] connecting (timeout {const.CONNECT_TIMEOUT:.0f} s) ...")
    await client.connect(timeout=const.CONNECT_TIMEOUT)
    session.client = client
    print(f"[{time.monotonic() - session.t0:08.3f}] connected")

    notify_uuids: list[str] = []
    aborted = False
    try:
        chars = session.enumerate_gatt()
        for uuid, _props in session.resolve_notify_chars(chars):
            await client.start_notify(uuid, session._make_handler(uuid))
            notify_uuids.append(uuid)
        session.subscribe_elapsed = time.monotonic() - session.t0
        print(
            f"[{session.subscribe_elapsed:08.3f}] subscribed to "
            f"{', '.join(notify_uuids)}"
        )
        # Session parity (contract 009 §1): the 2 s settle after the
        # CCCD write — the app's own Duration, the integration's
        # SETTLE_AFTER_SUBSCRIBE.
        await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)
        for spec in session_plan:
            try:
                decisive = await session.run_phase(spec)
            except Exception as exc:  # noqa: BLE001 — keep the data we have
                print(
                    f"PHASE {spec.name} aborted: {type(exc).__name__}: {exc}"
                )
                aborted = True
                break
            if decisive and args.stop_on_answer:
                print(f"--stop-on-answer: stopping after Phase {spec.name}")
                break
        session.print_summary()
    finally:
        for uuid in notify_uuids:
            try:
                await client.stop_notify(uuid)
            except Exception:  # noqa: BLE001
                pass
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    # Spec 011 FR-9: the security twin runs after the raw session's
    # teardown — never two concurrent links against the unit.
    if any(spec.name == "S" for spec in plan):
        return await run_security_phase(args)
    return 1 if aborted else 0


async def run_security_phase(args: argparse.Namespace) -> int:
    """Phase S (spec 011 FR-9, contract 011 §2): the HA connection test
    reproduced EXACTLY with a terminal.

    The sequence IS the config-flow test's (contract 011 §2):
    connect (two attempts) → first read (the 2 s re-send bounds) → on
    timeout the acknowledged probe. Spec 021: the pairing ladder and
    its first-read retry were pruned from the integration — the
    production verdict `att-accepted-silent` (law L5, spec 020) proved
    no bond is needed. The run goes through the integration's OWN
    client seams — import aqua_client, never copy it (tasks 011
    §14): the classifier and the `security probe:` verdict lines are
    the shipped code, so a developer's terminal run and the
    operator's HA run cannot drift. The client's log lines are
    mirrored to stdout so the paste-back captures everything.
    """
    import logging

    import aqua_client  # lazy: the live tool runs with bleak installed
    import registers

    print(
        "=== PHASE S: security probe "
        "(the HA connection test twin, spec 011 FR-9) ==="
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = aqua_client._LOGGER
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    client = aqua_client.AquaTempBleClient(args.address)
    try:
        await client.connect(max_attempts=2)
        regs = await client.read_registers(
            registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT,
            resend_every=2.0, resend_max=3,
        )
        print(
            f"PHASE S verdict: answered on the FIRST read "
            f"({len(regs)} register(s)) — the unit answers this central; "
            "contract 011 §6 row 6 applies (spec 009's matrix, 011 stops)"
        )
        return 0
    except aqua_client.AquaTempTimeoutError as first_timeout:
        regs = await aqua_client.run_security_probe(client)
        if regs is None:
            print(
                "PHASE S verdict: still no answer — paste every "
                "'security probe:' line above into the "
                "report (quickstart 011 §5); the decision matrix of "
                "contract 011 §6 is binding for the follow-up"
            )
            return 1
        print(
            f"PHASE S verdict: the acknowledged write ANSWERED "
            f"({len(regs)} register(s)) — the case-closed line is above; "
            "the write TYPE is the gate, not a bond (contract 011 §2)"
        )
        return 0
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        plan = build_phase_plan(
            args.phases,
            experimental=args.experimental,
            listen=args.listen,
            b_window=args.b_window,
            poll_every=args.poll_every,
            poll_count=args.poll_count,
        )
    except ProbePlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            build_dry_run_text(
                args.address,
                plan,
                write_with_response=args.write_with_response,
                subscribe_all=args.subscribe_all,
            )
        )
        return 0
    try:
        return asyncio.run(run_probe(args, plan))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"probe failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())