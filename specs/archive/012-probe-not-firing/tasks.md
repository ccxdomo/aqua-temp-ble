# Spec 012 — Tasks (ordered, for the Coder)

Status legend: blocks are ordered; land block-by-block, `python3 -m
pytest tests/ -q` green after each. Touch ONLY the files listed
(spec.md AC-7). `specs/001-011` are read-only. Normative details:
contracts/log-visibility.md (the level table §2, the marker §3) —
copy the exact strings/levels from there, do not paraphrase, do not
reword any existing message (contract 012 §1.3: strings are frozen).

Diagnosis recap (research 012 §0-§4): the probe FIRED on the observed
run (20.9 s = ~4.9 connect + 8.0 read + 8.0 probe window ⇒
att-accepted-silent); its evidence lines are INFO and the operator's
log page (system_log) shows only WARNING+ — hence "no probe lines".
This spec moves the evidence grammar to WARNING, adds a build marker,
and makes the suite assert levels.

## Block 1 — the level amendment (`aqua_client.py` only)

1. `_LOGGER.info(` → `_LOGGER.warning(` at EXACTLY these 22 sites
   (contract 012 §2 table; NO string/argument changes):
   - `run_security_probe`: :2317, :2337, :2342, :2354, :2365, :2393,
     :2412, :2416;
   - ladder `pair()`: :901, :908, :916, :929;
   - `_call_pair_method_once`: :943, :945, :949;
   - `_pair_via_reconnect`: :972, :993, :995, :999;
   - `_rung_c_retry`: :1011, :1032, :1039.
2. On each edited site, extend the adjacent comment with one clause
   citing `contract 012 §2` (level move; research 012 §4 mechanism —
   the operator's system_log panel stores WARNING+ only). In the
   docstrings of `run_security_probe` and `pair`, add one sentence:
   "Evidence lines are WARNING (contract 012 §2): the operator's HA
   log page keeps only WARNING+."
3. DO NOT touch: :2387 (already WARNING), the census lines, the
   idle-window summary, enumeration/connect summary/attempt lines,
   re-send lines, TX/RX/chunk DEBUG lines, the pair-on-connect option
   DEBUG marker, any pre-existing WARNING/ERROR line.

## Block 2 — the build marker (`config_flow.py` only)

4. Add `_manifest_version()` + `BUILD_VERSION` at module level
   (plan.md Block 2 has the exact code; guarded; `"unknown"` fallback;
   comment cites `contract 012 §3`).
5. In `_async_test`, directly after `t_test = time.monotonic()`
   (:472), add the marker line (plan.md Block 2; format byte-frozen:
   `connection test to %s: build %s`; WARNING; once; BEFORE the try
   block). No other edit to `_async_test` — handlers, stages, formats
   and UI keys are untouched.

## Block 3 — version bump (`manifest.json` only)

6. `"version": "0.7.0"` → `"version": "0.8.0"` (the marker reads it;
   Block 4's drift test pins the pair — the marker never lies).

## Block 4 — tests (`tests/test_security_probe_011.py` only)

7. `_LogCap.emit` (:283) and `_FlowLogCap.emit` (:1212): append
   `record.levelname` to a new `self.levels` list (additive;
   `messages` and every existing assertion stay as-is).
8. New "Part 7 — spec 012" tests (they live in this file because the
   flow harness lives here — plan.md Block 4 rationale):
   - `test_flow_probe_evidence_lines_are_warning` — the
     att-accepted-silent script (the `test_flow_accepted_silent…`
     :1467 pattern): every captured `security probe:` / `pairing:`
     message is at WARNING; the census lines are NOT (INFO);
   - `test_flow_build_marker_logged_once_first` — marker present
     exactly once, first, at WARNING;
   - `test_build_version_matches_manifest` — `cf.BUILD_VERSION` ==
     the manifest's version and != "unknown".
9. Full regressions: `python3 -m pytest tests/ -q` green (197 + 3);
   the census INFO-level assertions of `test_census_009.py` are
   untouched (census stays INFO — contract 012 §1.2).

## Block 5 — docs (`README.md` only)

10. Repoint the next-steps pointer to
    `specs/012-probe-not-firing/quickstart.md` (two sentences: the
    0.8.0 build makes the probe lines visible on the Logs page; the
    quickstart §0 recovers the verdict already in the existing log).

## Verification checklist (all blocks)

- [x] `git diff` contains ZERO message-string changes (a reworded
      line is a contract violation, contract 012 §1.3).
- [x] `grep -c "_LOGGER.warning(" aqua_client.py` grew by exactly 22
      (18 -> 40, verified).
- [x] The marker exists exactly once, before the `try:` in
      `_async_test`, format byte-identical to contract 012 §3.
- [x] `manifest.json` == 0.8.0; drift test enforces equality with
      `BUILD_VERSION`.
- [x] `python3 -m pytest tests/ -q` green (203 = 197 + 6: the 3
      planned tests plus the all-scenarios flow test, the
      client-level ladder test and the census counter-example);
      census/connect/walkthrough suites untouched and green.
- [x] `python3 -m py_compile` clean on touched files; the standalone
      import smoke loads `aqua_client` (and `config_flow` behind the
      suite's stubs) — the bare `python3 -c "import aqua_client"`
      needs bleak installed and fails at the pre-existing bleak
      import on the dev Pi exactly as on d860933 (verified by running
      both; no regression).
- [x] specs/001-011 files untouched (`git status`); every new comment
      cites research 012 §… / contract 012 §….
- [ ] Completion report must include: the operator's §0 retroactive
      grep results (the verdict of the already-run test — predicted
      `att-accepted-silent` + `connect summary total ≈ 4.9 s`), and
      after the operator updates + FULLY restarts HA, the panel-pasted
      probe lines.

## Out of scope (explicit)

- No protocol bytes, no probe frames beyond the one the 011 build
  already sends (contract 011 §6 row 3: no more probing frames).
- No pairing-behavior or option-path changes; the 011 leftovers
  (strings.json option description, coordinator option threading)
  are a separate landing.
- No E5 (phone capture) analysis work — the quickstart routes the
  operator there; the follow-up spec consumes it.