# Tasks 015 — the Coder's ordered work

Rules: each task is gated by its evidence precondition; NOTHING in
Phase B ships while the gate evidence is absent (AC-2 — the 0.10.0
behaviour is byte-identical with the gate closed). No commits/pushes
beyond the repo owner's flow. All artifacts English (repo rule).

---

## Phase 0 — tooling (may ship immediately; pure spec-internal tooling)

- [x] **T0.1** Verify the decode tooling runs green:
  `python3 specs/015-control-data/decode_blocks_015.py` → all four CRCs
  VALID, the §2 tables of research.md reproduced (AC-1). Keep
  `frames.txt` + the script in the spec directory (they are the
  evidence pins; do NOT move them into the component).

## Phase A — evidence (owner-run; the Coder only records results)

- [x] **T-A1** Relay quickstart §1 (the toggle-and-diff) to the owner;
  collect: the step-0 photos, the labeled capture hexes, the diffs,
  the verdicts (pinned / silent) per quantity. Update
  `research.md` §8 (U-1..U-3, U-8) and `data-model.md` §7 with the
  results — statuses flip [V]→[P] (pinned) or the negative verdict is
  recorded (FR-4).
- [ ] **T-A2** If quickstart §2 (logcat oracle) was run: record the
  vendor write addresses+values in `data-model.md` §7 (write row) and
  contract 015-B §4; do NOT touch the allowlist (FR-12/AC-6).
- [ ] **T-A3** If §4 (probe 1091/1136) was run: record the census
  outcome; if a direct block answer arrived, open the follow-up
  (S/O/D/P calibration) as a new spec candidate — not here.

## Phase B — the read-only climate behind the gate (ONLY after T-A1 pins homes)

- [x] **T-B1** `registers.py`: add `PUSHED_CONTROL` (default `None`
  per key — the pinned homes land here with [P] evidence comments),
  `PUSH_CONTROL_CONFIRMED = False`, the `MODE_VALUE_TO_NAME` reuse note
  (heat-only: the mapping may stay [A] — D-3). Extend
  `check_registry_integrity` (FR-11d: keys {power, mode, setpoint},
  addresses within 1001-2090, no cross-plane duplicates). No shipped
  behaviour change (AC-2).
- [x] **T-B2** `coordinator.py`: the `push_control_confirmed` property
  (the `push_temp_confirmed` pattern — global OR per-entry option);
  `from_pushes` decodes the confirmed control keys (power flag,
  mode_value/hvac_mode with the unknown-mode WARNING path, target_temp
  ÷10 + plausibility). Unconfirmed → the exact 0.10.0 snapshot (FR-7).
- [x] **T-B3** `climate.py`: create the entity in push mode ONLY when
  the gate is open; READ-ONLY surface (no TARGET_TEMPERATURE feature,
  no write methods advertised; FR-8); hvac_action OFF when power
  False; bounds from the fallbacks (FR-9).
- [x] **T-B4** Diagnostics (FR-10): the push_stream attributes (or the
  probe output) expose the candidate raws — 2012, 2034, and the ladder
  five 1046/1049/1051/1055/1068 — with their [V] tags so the owner can
  re-run the diff without a terminal.
- [x] **T-B5** Tests (`tests/test_…_015.py`, FR-11):
  (a) gate-closed non-regression — the 0.10.0 from_pushes snapshot and
  the absent climate entity; (b) gate-open decode paths — pinned
  power/mode/setpoint from a synthetic capture; (c) the read-only
  climate surface (no writable features, hvac_mode mapping, the
  unknown-mode WARNING once); (d) integrity checks (FR-11d) including a
  negative test per violation.
- [x] **T-B6** Docs: the calibration runbook gains the control-diff
  procedure (quickstart §1 content, operator-facing, EN) +
  the `push_control_confirmed` option's description.

## Phase C — out of scope (recorded; a future spec's tasks)

- [ ] The write allowlist decision (contract 015-B's three doors:
  vendor-proven address + delivery gate + own spec). Nothing here.

## Definition of done

- Phase 0: AC-1 green.
- Phase A: research/data-model updated with the owner's evidence
  (statuses flipped or negative verdicts recorded).
- Phase B: AC-2 (byte-identical with the gate closed) + AC-3 (the
  read-only climate with the gate open) + AC-5 (every mapping tagged) +
  AC-6 (allowlist untouched) + the FR-11 suite green.
- Phase A silent-outcome variant (if the diffs move nothing): AC-4 —
  the negative verdict recorded, the follow-ups named, NO Phase B code
  (the gate stays closed; the spec ships as evidence + tooling only).