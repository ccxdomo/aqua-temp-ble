# Spec 009 tasks — ordered implementation blocks for the Coder

Work top-to-bottom; each block ends green (`pytest tests/` + the
modbus self-test). Normative refs: spec.md (FR/AC), plan.md (design),
contracts/session-request.md (rules), research.md (proofs). Do not
touch anything outside spec.md NFR-1's file list.

## Block 0 — baseline (no code changes)

- [x] 0.1 `python3 custom_components/aqua_temp_ble/modbus.py` — self-test
      PASS as-is (FINDINGS §11 + spec-008 vectors). Output must stay
      byte-identical at the end (NFR-6).
- [x] 0.2 `pytest tests/` — fully green on ce41aa5.
- [x] 0.3 Re-read research 009 §0/§7/§11 (the byte-accounting proof,
      the cause ranking, the decision matrix) — every design choice
      below cites it.

## Block 1 — census upgrade (FR-1, AC-1, AC-4)

- [x] 1.1 RED-first: extend/`tests/test_census_009.py` NEW —
      - AC-1 vector run: 3×(1001, 1046, 2001, 2046) fake-notify →
        timeout message contains `1001x3`, `2046x3`, `first t+`,
        `last t+`, `none for start 7` (faked clock, `_now` seam);
      - idle-window summary: pushes during settle → exactly one INFO
        summary line with starts + `t+%.2fs after subscribe`;
        no pushes → `0 block push(es)`;
      - invariants: bytes still counted for CRC-invalid frames;
        nothing dropped silently; census never fatal (re-pin the
        008 properties against the new grammar).
- [x] 1.2 Implement per plan §2: `_req_t0`/`_req_first_seen`/
      `_req_last_seen`/`_idle_pushes` state (+ resets in connect/
      disconnect), `t+%.2fs` suffix on census lines, per-start-counts
      timeout message, idle-summary INFO line at the first request.
      Strings byte-exact per contract 009 §3.
- [x] 1.3 Update the pinned-message assertions in
      `tests/test_reassembly_matching.py` (grammar change is
      deliberate — NFR-6; keep the semantic properties).

## Block 2 — bounded re-send (FR-2, AC-2)

- [x] 2.1 RED-first: `tests/test_first_read_resend.py` NEW —
      - 4 writes at t+0/2/4/6 with `resend_every=2.0, resend_max=3`,
        payloads all `63030007002d3c54`, each logged;
      - exactly 1 write without the parameters (coordinator parity);
      - answer after the 2nd send → completes once, loop stops;
      - timeout path: message + budget unchanged;
      - disconnect mid-window → no leaked re-send task.
- [x] 2.2 Implement per plan §3: `read_registers(..., *, resend_every,
      resend_max)` parameters → `_request` re-send loop (asyncio.Event
      guard + cancel in `finally`; single-flight untouched).
- [x] 2.3 Call site: config_flow first-read passes
      `resend_every=2.0, resend_max=3` (one line; contract 009 §5).
- [x] 2.4 Grep gate: `resend_` appears only in aqua_client.py,
      config_flow.py and tests (NFR-3).

## Block 3 — session probe (FR-3, AC-3)

- [x] 3.1 RED-first: `tests/test_session_probe.py` NEW (pure helpers):
      dry-run text (phases + frames + CRCs, no connection); allowlist
      membership (every TX frame must be in the module frozenset);
      `--experimental` gate (Phase E refuses without the flag);
      Phase-Q verdict logic on synthetic timelines (in-cycle arrival =
      negative, immediate out-of-cycle = positive, per plan §4.3);
      cadence stats correctness.
- [x] 3.2 Implement `tools/session_probe.py` per plan §4: CLI
      (phases L/B/P/Q/W/E, `--listen/--b-window/--poll-every/
      --poll-count/--subscribe-all/--write-with-response/
      --experimental/--dry-run/--debug`), bleak connect + GATT table
      print (the E4 capture), own reassembly via the modbus layer
      (boundary rule per contract 008-A §3), per-event ms-timestamped
      lines, per-block register dumps, per-phase verdicts + end-of-run
      summary. NO HA imports, NO register writes (contract 009 §7).
- [x] 3.3 `python3 -m py_compile` on the tool; `--dry-run` output
      eyeballed against research §8's frame table (CRCs included).

## Block 4 — kit + docs

- [x] 4.1 Confirm the spec-009 kit exists as delivered (spec/plan/
      research/contracts/session-request.md/quickstart/tasks) — the
      Coder does NOT edit the kit (AC-5 presence check).
- [x] 4.2 Every new comment cites `research 009 §…` / `contract 009
      §…` (NFR-7); all comments in English (repo rule).

## Block 5 — final gates (hand to Reviewer)

- [x] 5.1 `python3 -m py_compile` on aqua_client.py / config_flow.py /
      tools/session_probe.py.
- [x] 5.2 `python3 custom_components/aqua_temp_ble/modbus.py` — self-test
      PASS, Block-0 output byte-identical (NFR-6).
- [x] 5.3 `pytest tests/` fully green (including updated 008 pins).
- [x] 5.4 Grep gates (plan §6): no timeout-constant changes;
      `63100007002d|630303e9` only under `tools/`; `resend_` only in
      the three named files; no new TX frame in
      `custom_components/`.
- [x] 5.5 `git status` — only NFR-1 files modified.
- [ ] 5.6 Reviewer checklist: AC-1 … AC-6 one by one; re-run 5.4's
      grep gates on the final diff.

## Definition of done

All ACs of spec 009 pass; the connection test re-sends the unchanged
vendor frame ≤4× inside the unchanged window and logs every attempt;
the census reports per-start counts, onset and timing; the probe
exists, compiles, dry-runs and enforces its gates; no file outside
NFR-1's list changed. Production evidence (quickstart §0-§3) then feeds
the decision matrix (research §11) — the follow-up spec is written from
that data, not before.