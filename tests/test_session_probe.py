#!/usr/bin/env python3
"""Session probe tests (spec 009 FR-3 / AC-3) — pure helpers only.

`tools/session_probe.py` is a raw-bleak MEASUREMENT INSTRUMENT (no
Home Assistant, no aqua_client import — plan 009 §4.2). These tests pin
its PURE surface so CI needs no bleak and no Bluetooth:

- the phase plan: default L,B,P,Q,W, canonical ordering, unknown-phase
  rejection, and the contract-009 §4 experimental gates (Phase E refuses
  without --experimental; Phase E must follow Phase Q in the same run);
- the frame allowlist (research 009 §8): every TX frame of every plan
  is a member; the 9 exact hexes are pinned; no FC10 write shape is in
  it (contract 009 §7 — the probe never writes registers);
- the dry-run text: phases + exact frames + CRC verdicts, and it never
  connects;
- Phase Q's frame is the app-proven 631003e9002dd9e6 (AC-3 vector);
- the probe's own boundary rule (contract 008-A §3): 99 from every
  prefix >= 7 of the proven block frame, 8 for the two known echoes,
  None below 7 bytes — behaviour equal to the shipped vectors;
- the Phase-Q verdict on synthetic timelines: an in-cycle arrival is
  NEGATIVE, an immediate out-of-cycle reaction is POSITIVE (plan 009
  §4.3 — the verdict is never "did a 1001 push occur", one arrives
  every cycle anyway);
- cadence stats correctness.

Red-first: the module does not exist on ce41aa5 — every test fails at
import until FR-3 lands.

The bleak flow itself is verified by the owner at the panel (quickstart
009 §2); these tests are the CI gate for everything pure.

Run standalone:

    python3 tests/test_session_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_DIR / "custom_components" / "aqua_temp_ble"
TOOLS_DIR = REPO_DIR / "tools"
sys.path.insert(0, str(COMPONENT_DIR))
sys.path.insert(0, str(TOOLS_DIR))

import modbus  # noqa: E402
import session_probe as sp  # noqa: E402

# The proven production frame for block 1001-1045 (research 008 §2.2,
# CRC 0x3A56) — the boundary vectors run over its prefixes.
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)


def _build_echo(start: int, qty: int = 1) -> bytes:
    """The 8-byte FC10 echo shape: 63 10 start:2 qty:2 crc:2."""
    head = (
        bytes([modbus.SLAVE_ID, modbus.FC_WRITE_MULTI])
        + start.to_bytes(2, "big")
        + qty.to_bytes(2, "big")
    )
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


def _build_fc03_response(bytecount: int = 90) -> bytes:
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, bytecount]) + b"\x00" * bytecount
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


def _build_exception(code: int = 2) -> bytes:
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING | 0x80, code])
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


# --------------------------------------------------------------------- tests
def test_default_phase_plan() -> None:
    """The default plan is L,B,P,Q,W in canonical order (plan 009 §4.1):
    L passive (no TX), B one base read, P the poll, Q the proven query
    frame, W the write-type experiment — E is NEVER in the default
    (US-4: experimental frames exist only behind the explicit flag)."""
    assert sp.DEFAULT_PHASES == "L,B,P,Q,W"
    plan = sp.build_phase_plan(
        sp.DEFAULT_PHASES, experimental=False,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    assert [s.name for s in plan] == ["L", "B", "P", "Q", "W"]
    # L: passive listen, NO TX frames
    assert plan[0].frames == () and plan[0].listen_s == 30.0
    # B: ONE base read + the 15 s answer window
    assert plan[1].frames == (sp.BASE_READ,) and plan[1].repeat == 1
    assert plan[1].listen_s == 15.0
    # P: the base read repeated on the cadence
    assert plan[2].frames == (sp.BASE_READ,) and plan[2].repeat == 15
    assert plan[2].poll_every == 2.0
    # Q: the app-proven block-query vector [P] — AC-3 pin
    assert sp.BASE_READ.hex() == "63030007002d3c54"
    assert plan[3].frames == (sp.QUERY_1001,)
    assert sp.QUERY_1001.hex() == "631003e9002dd9e6"
    # W: the base read with write-with-response FORCED
    assert plan[4].frames == (sp.BASE_READ,) and plan[4].force_response


def test_phase_e_requires_experimental_flag() -> None:
    """AC-3 / contract 009 §4: Phase E refuses to run without
    --experimental — a hard, testable error, never a silent skip."""
    try:
        sp.build_phase_plan(
            "L,B,P,Q,W,E", experimental=False,
            listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
        )
    except sp.ProbePlanError as err:
        assert "--experimental" in str(err), str(err)
    else:
        raise AssertionError("Phase E must refuse without --experimental")


def test_phase_e_requires_q_in_same_run() -> None:
    """Contract 009 §4: Phase E must FOLLOW Phase Q in the same run —
    the query-shaped base frame is meaningless without Q's verdict
    context."""
    try:
        sp.build_phase_plan(
            "E", experimental=True,
            listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
        )
    except sp.ProbePlanError as err:
        assert "Q" in str(err), str(err)
    else:
        raise AssertionError("Phase E must require Phase Q in the same run")


def test_canonical_order_and_dedup() -> None:
    """Requested phases run in CANONICAL order (L,B,P,Q,W,E) regardless
    of input order; duplicates collapse (plan 009 §4.1: P after B)."""
    plan = sp.build_phase_plan(
        "W,P,W", experimental=False,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    assert [s.name for s in plan] == ["P", "W"]


def test_unknown_phase_rejected() -> None:
    """Unknown phase letters are a hard error naming the valid set."""
    try:
        sp.build_phase_plan(
            "L,X", experimental=False,
            listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
        )
    except sp.ProbePlanError as err:
        assert "L,B,P,Q,W,E" in str(err), str(err)
    else:
        raise AssertionError("unknown phase letters must be rejected")


def test_allowlist_pinned_to_research_table() -> None:
    """The frame allowlist is EXACTLY research 009 §8's table (9 frames,
    all CRC-valid) — nothing else may ever be sent (contract 009 §2)."""
    expected = sorted([
        "63030007002d3c54",       # the base read [P]
        "631003e9002dd9e6",       # query 1001 [P]
        "63100416002de8a2",       # query 1046 [P]
        "63100443002df8b2",       # query 1091 [P]
        "63100470002d08bd",       # query 1136 [P]
        "631007d1002d591b",       # query 2001 [P]
        "631007fe002d68d2",       # query 2046 [P]
        "63100007002db997",       # experimental: query-shaped base read
        "630303e9002d5c25",       # experimental: FC03 at start 1001
    ])
    assert sorted(f.hex() for f in sp.FRAME_ALLOWLIST) == expected
    assert len(sp.FRAME_ALLOWLIST) == 9
    # every allowlist frame is CRC-valid (research §8: CRC-verified)
    assert all(modbus.validate_crc(f) for f in sp.FRAME_ALLOWLIST)
    # contract 009 §7: the probe NEVER writes registers — an FC10 frame
    # in the allowlist is the 8-byte query shape only, never a write
    # (a write carries bytecount+data: 11+ bytes).
    for frame in sp.FRAME_ALLOWLIST:
        if frame[1] == modbus.FC_WRITE_MULTI:
            assert len(frame) == 8, frame.hex()


def test_every_plan_tx_frame_is_allowed() -> None:
    """Every frame any valid plan would send is a member of the
    allowlist (plan 009 §4.4: TX asserts membership — pinned here so
    the assert can never fire at the panel)."""
    plan = sp.build_phase_plan(
        "L,B,P,Q,W,E", experimental=True,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    frames = [f for spec in plan for f in spec.frames]
    assert frames, "the plan must carry TX frames to check"
    assert all(f in sp.FRAME_ALLOWLIST for f in frames)


def test_dry_run_text_names_phases_frames_and_crcs() -> None:
    """AC-3: --dry-run prints the phase plan and the EXACT frames it
    would send with their CRC verdicts — WITHOUT connecting."""
    plan = sp.build_phase_plan(
        sp.DEFAULT_PHASES, experimental=False,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    text = sp.build_dry_run_text(
        "08:06:A6:C0:94:1C", plan,
        write_with_response=False, subscribe_all=False,
    )
    assert "no connection" in text.lower(), text
    for phase in ("L", "B", "P", "Q", "W"):
        assert f"PHASE {phase}" in text, text
    assert "63030007002d3c54" in text, text          # the base read
    assert "631003e9002dd9e6" in text, text           # Phase Q's frame
    assert "CRC OK" in text, text                     # CRCs included (3.3)
    assert "every 2.0 s" in text, text                 # the poll cadence
    assert "30" in text and "15" in text              # listen / answer windows


def test_dry_run_text_includes_experimental_frames_when_gated() -> None:
    """With --experimental + E in the plan, the dry run names BOTH
    experimental frames and their gate (contract 009 §4); without the
    phase, they never appear."""
    plan_e = sp.build_phase_plan(
        "L,B,P,Q,W,E", experimental=True,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    text_e = sp.build_dry_run_text(
        "08:06:A6:C0:94:1C", plan_e,
        write_with_response=False, subscribe_all=False,
    )
    assert "EXPERIMENTAL" in text_e, text_e
    assert "63100007002db997" in text_e, text_e
    assert "630303e9002d5c25" in text_e, text_e


def test_help_documents_every_phase_and_flag() -> None:
    """AC-3: --help documents every phase and every gate flag."""
    help_text = sp.build_arg_parser().format_help()
    for phase in ("L", "B", "P", "Q", "W", "E", "S"):
        assert phase in help_text, help_text
    for flag in (
        "--address", "--phases", "--listen", "--b-window",
        "--poll-every", "--poll-count", "--subscribe-all",
        "--write-with-response", "--experimental", "--stop-on-answer",
        "--dry-run", "--debug",
    ):
        assert flag in help_text, (flag, help_text)


def test_probe_boundary_block_prefixes() -> None:
    """The probe's own boundary rule (contract 008-A §3, pinned equal to
    the shipped vectors, plan 009 §4.2): 99 from EVERY prefix >= 7 of
    the proven block frame; None below 7 bytes (never guess)."""
    frame = bytes.fromhex(FRAME_BLOCK_1001)
    assert len(frame) == 99 and modbus.validate_crc(frame)
    assert all(sp.probe_boundary(frame[:p]) == 99 for p in range(7, 100))
    assert all(sp.probe_boundary(frame[:p]) is None for p in range(0, 7))


def test_probe_boundary_known_echoes() -> None:
    """The two known write echoes (offset-6 bytes 0x88/0x68 — the
    modbus self-test vectors) fall to the 8-byte echo branch at >= 7
    bytes; below 7 the boundary is unknown."""
    echo8, echo14 = _build_echo(8, 1), _build_echo(14, 1)
    assert echo8[6] == 0x88 and echo14[6] == 0x68
    assert sp.probe_boundary(echo8) == 8
    assert sp.probe_boundary(echo14) == 8
    assert sp.probe_boundary(echo8[:7]) == 8
    assert sp.probe_boundary(echo8[:6]) is None


def test_probe_boundary_other_shapes() -> None:
    """Exception -> 5; FC03 -> bytecount+5 (needs 3 bytes); wrong
    slave / too short -> None (contract 008-A §3 order)."""
    assert sp.probe_boundary(_build_exception()) == 5
    assert sp.probe_boundary(_build_fc03_response()) == 95
    assert sp.probe_boundary(b"\x63") is None
    assert sp.probe_boundary(b"\x62\x03\x5a") is None  # wrong slave


def test_classify_frame_vocabulary() -> None:
    """The classification vocabulary the verdicts match on (query_reaction
    greps 'block start=1001'; analyze_answers greps the kinds)."""
    block = bytes.fromhex(FRAME_BLOCK_1001)
    assert "block start=1001" in sp.classify_frame(block)
    assert "FC03 answer" in sp.classify_frame(_build_fc03_response())
    assert "exception" in sp.classify_frame(_build_exception())
    assert "echo" in sp.classify_frame(_build_echo(8, 1))


def test_q_verdict_in_cycle_arrival_is_negative() -> None:
    """Plan 009 §4.3: the FIRST post-TX 1001 arriving at its cycle slot
    (gap == the baseline median) is NEGATIVE — 'a 1001 push occurred'
    proves nothing, one arrives every cycle anyway."""
    frames = [
        (0.00, "block start=1001 qty=45 99B"),
        (0.67, "block start=1046 qty=45 99B"),
        (1.33, "block start=2001 qty=45 99B"),
        (2.00, "block start=2046 qty=45 99B"),
        (2.67, "block start=1001 qty=45 99B"),   # the cycle's next slot
    ]
    reaction = sp.query_reaction(frames, tx_t=1.0)
    assert reaction is not None, "a post-TX 1001 arrived"
    gap, detail = reaction
    assert abs(gap - 0.67) < 1e-9, gap
    verdict = sp.query_verdict(0.67, reaction)
    assert "NEGATIVE" in verdict and "in-cycle" in verdict, verdict


def test_q_verdict_immediate_reaction_is_positive() -> None:
    """Plan 009 §4.3: a 1001 arriving IMMEDIATELY after the TX (gap well
    below 50% of the baseline median) is POSITIVE — the writes reach
    the engine (C1 dead for queries)."""
    frames = [
        (0.00, "block start=1001 qty=45 99B"),
        (0.67, "block start=1046 qty=45 99B"),
        (0.75, "block start=1001 qty=45 99B"),   # injected immediate reaction
        (1.33, "block start=2001 qty=45 99B"),
        (2.00, "block start=2046 qty=45 99B"),
        (2.67, "block start=1001 qty=45 99B"),
    ]
    reaction = sp.query_reaction(frames, tx_t=0.70)
    gap, _detail = reaction
    assert abs(gap - 0.08) < 1e-9, gap
    verdict = sp.query_verdict(0.67, reaction)
    assert "POSITIVE" in verdict, verdict


def test_q_verdict_no_reaction_is_negative() -> None:
    """No 1001 after the TX at all: NEGATIVE — the write pipe is the
    suspect (C1 alive, research 009 §11)."""
    frames = [
        (0.00, "block start=1046 qty=45 99B"),
        (0.67, "block start=2001 qty=45 99B"),
    ]
    reaction = sp.query_reaction(frames, tx_t=0.10)
    assert reaction is None
    verdict = sp.query_verdict(0.67, reaction)
    assert "NEGATIVE" in verdict, verdict


def test_q_verdict_no_baseline_is_inconclusive() -> None:
    """A reaction with no Phase-L cadence baseline is INCONCLUSIVE —
    the honest instrument refuses to guess the cycle (run Phase L)."""
    reaction = (0.08, "first post-TX 1001 at t+0.750s")
    verdict = sp.query_verdict(None, reaction)
    assert "INCONCLUSIVE" in verdict and "baseline" in verdict.lower(), verdict


def test_phase_e_frames_gated_by_q_verdict() -> None:
    """Contract 009 §4: the query-shaped base frame may be sent ONLY
    after Phase Q proved the engine reacts to query-shaped frames; the
    FC03-at-1001 control stays allowed either way (it isolates 'FC03
    handler off' from 'register 7 absent')."""
    assert sp.EXPERIMENT_QUERY_BASE.hex() == "63100007002db997"
    assert sp.EXPERIMENT_FC03_1001.hex() == "630303e9002d5c25"
    frames_pos = sp.phase_e_frames(q_positive=True)
    assert sp.EXPERIMENT_QUERY_BASE in frames_pos
    assert sp.EXPERIMENT_FC03_1001 in frames_pos
    frames_neg = sp.phase_e_frames(q_positive=False)
    assert sp.EXPERIMENT_QUERY_BASE not in frames_neg
    assert sp.EXPERIMENT_FC03_1001 in frames_neg


def test_cadence_stats() -> None:
    """(min, median, max) of the consecutive gaps; None below 2 frames."""
    assert sp.cadence_stats([]) is None
    assert sp.cadence_stats([5.0]) is None
    stats = sp.cadence_stats([0.0, 0.67, 1.33, 2.0])
    assert stats is not None
    mn, med, mx = stats
    assert abs(mn - 0.66) < 1e-9 and abs(med - 0.67) < 1e-9 and abs(mx - 0.67) < 1e-9
    mn, med, mx = sp.cadence_stats([0.0, 1.0, 3.0, 6.0])
    assert mn == 1.0 and med == 2.0 and mx == 3.0


def test_phase_s_valid_last_and_frameless() -> None:
    """Spec 011 FR-9: Phase S — the security twin — is a valid phase
    that carries NO raw frames: it runs after the raw probe session
    closes and reconnects through the integration's OWN client (import
    the client, never copy it — tasks 011 §14, contract 011 §1). The
    canonical order puts it LAST regardless of input order, and it is
    never in the default (the operator path is the HA UI, plan 011
    §Phase-2; the tool twin is for developers with a terminal)."""
    assert "S" in sp.PHASE_ORDER
    # reversed input: canonical ordering still yields S last
    plan = sp.build_phase_plan(
        "S,B", experimental=False,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    assert [s.name for s in plan] == ["B", "S"], plan
    s_spec = plan[-1]
    assert s_spec.frames == (), s_spec  # no raw frames: the client owns them
    assert "S" not in sp.DEFAULT_PHASES


def test_dry_run_text_includes_security_phase() -> None:
    """The dry run documents the security twin when S is requested —
    the same verdict machinery the HA connection test runs (spec 011
    FR-9; a developer reproduces the HA test exactly)."""
    plan = sp.build_phase_plan(
        "B,S", experimental=False,
        listen=30.0, b_window=15.0, poll_every=2.0, poll_count=15,
    )
    text = sp.build_dry_run_text(
        "08:06:A6:C0:94:1C", plan,
        write_with_response=False, subscribe_all=False,
    )
    assert "PHASE S" in text, text
    assert "security probe" in text.lower(), text


def main() -> int:
    tests = [
        test_default_phase_plan,
        test_phase_e_requires_experimental_flag,
        test_phase_e_requires_q_in_same_run,
        test_canonical_order_and_dedup,
        test_unknown_phase_rejected,
        test_allowlist_pinned_to_research_table,
        test_every_plan_tx_frame_is_allowed,
        test_dry_run_text_names_phases_frames_and_crcs,
        test_dry_run_text_includes_experimental_frames_when_gated,
        test_phase_s_valid_last_and_frameless,
        test_dry_run_text_includes_security_phase,
        test_help_documents_every_phase_and_flag,
        test_probe_boundary_block_prefixes,
        test_probe_boundary_known_echoes,
        test_probe_boundary_other_shapes,
        test_classify_frame_vocabulary,
        test_q_verdict_in_cycle_arrival_is_negative,
        test_q_verdict_immediate_reaction_is_positive,
        test_q_verdict_no_reaction_is_negative,
        test_q_verdict_no_baseline_is_inconclusive,
        test_phase_e_frames_gated_by_q_verdict,
        test_cadence_stats,
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
    print(f"PASSED: {len(tests)}/{len(tests)} session-probe tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())