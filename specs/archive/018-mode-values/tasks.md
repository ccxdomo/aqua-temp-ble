# Spec 018 tasks — ordered for the Coder

Working state: main @ b81b604, manifest 0.13.0. Everything lands in
one PR: the decode fix, the logging fix, the tests, the version bump.
Run the full suite after every task; the AC numbers refer to
spec 018. No code outside `plan.md` §1-§3; no spec 001-017 file is
touched (018 amends via its own contracts).

## Block 1 — the display fix (FR-1/FR-2/FR-3, AC-1..AC-3)

- [x] **T1 — `coordinator.py`**: the module-set rename + type tightening
  (`_logged_unknown_push_modes: set[int | None]` →
  `_logged_unmapped_push_modes: set[int]`, with the contract 018-A
  comment); the `from_pushes` decode block replacement (plan.md §1.2
  verbatim); the `from_pushes` docstring's D-3 sentence rewritten to
  the 018 rule; the surrounding contract citation line gains
  "amended by contract 018-A §2".
- [x] **T2 — `registers.py`**: the `PUSHED_MODE_VALUE_TO_NAME` comment
  rewrite (plan.md §2 verbatim; content unchanged — the 015 registry
  test `PUSHED_MODE_VALUE_TO_NAME == {4: "heat"}` must keep passing).
- [x] **T3 — new `tests/test_mode_display_018.py`**: the truth table
  (AC-1), the warning-gone assertions (AC-2: zero records containing
  `Unknown pushed Mode` at ANY level, including the (1, None) case),
  the once-per-session-per-value INFO diagnostic incl. the
  second-decode silence and the OFF-branch never reading mode
  (AC-3), gate-closed → None. Use the 015 suite's scripted-capture
  harness (tests/test_control_015.py:540-660) as the pattern.

## Block 2 — the logging fix (FR-4, AC-4)

- [x] **T4 — `aqua_client.py`**: `_census_frame`'s two BLOCK branches at
  `logging.DEBUG` (message text byte-identical; counters untouched —
  plan.md §3). Nothing else in the client.
- [x] **T5 — census-level test updates**: `tests/test_census_009.py`
  (~264-295, ~430, ~465), `tests/test_push_store_013.py` (~286-289),
  `tests/test_reassembly_matching.py` (~283-385): the
  `block push observed` level assertions drop to DEBUG; add to
  `tests/test_mode_display_018.py` the AC-4 pair (no INFO record per
  block frame; the timeout census summary still counts the frame).
  (Also updated: `tests/test_security_probe_011.py`'s spec-012
  counter-example pinned the old INFO level — re-pinned to DEBUG per
  contract 018-B, its never-WARNING intent unchanged.)

## Block 3 — the inherited suites + release

- [x] **T6 — `tests/test_control_015.py`**: flip the mode-gated decode
  tests to the 018 truth table (the `raws[2019] = 7` unknown-warning
  test becomes HEAT + the single INFO; any `(1, 0)` case becomes
  HEAT); keep untouched the gate-closed/None, power-0/OFF and
  mode-4/HEAT verdicts; update the contract citations. Verify-only:
  `test_climate_control_017.py`, the 013/014/016 suites (expected
  green untouched — a failure means the change left plan.md's ring).
- [x] **T7 — full run + bump**: `python3 -m pytest tests/ -q` green;
  `manifest.json` → `0.13.1`; README sweep (expected: no change).
  (Sweep outcome: the README's `hvac_mode` from power+mode wording
  described the amended decode — updated to the 018 rule; the
  2025/2034 register numbers in README/CALIBRATION are a pre-015-
  review miscount, out of 018's ring, reported to the parent.)

## Block 4 — the owner's verification (after deploy)

- [ ] **T8 — quickstart §1 (AC-7, closes the spec)**: the owner's
  recorded observation — ON → tile `heat` within ~10-15 s, no
  `Unknown pushed Mode` line; OFF → tile `off`; round trip stable;
  no `logging too frequently` line in the following hours.
- [ ] **T9 — quickstart §2 (AC-8, non-blocking)**: the long-ON
  observation of 2019 (the `push_stream` `control_candidates` raw,
  ~30 s / ~2 min / ~5 min) pasted into research.md §8 G-1 — either
  outcome closes the ledger row.

## Acceptance gate (the Coder's definition of done)

1. The full suite green (T7) with the 018 suite pinning AC-1..AC-4.
2. `git diff` touches exactly: `coordinator.py`, `registers.py`,
   `aqua_client.py`, `manifest.json`, the named test files, the new
   test file — nothing else.
3. The classic plane, `hvac_action`, the gates, the confirmation
   cycle, the funnel: byte-identical (plan.md §7's NOT list).
4. The PR body quotes the AC-7 record (T8) — the 016/017 closure
   pattern.