# Spec 012 plan — the visibility fix, block by block

Hard constraints inherited (binding): **no terminal on the HA Pi for
the operator** (quickstart steps stay UI-only); specs 001-011 files are
read-only (this spec's contract amends 011 §3's level column from its
own file); message strings are frozen (contract 012 §1.3); the
production/poll path and the option path are untouched.

## The one-paragraph diagnosis the fix encodes

The probe fired and returned its verdict (research 012 §3: 20.9 s =
~4.9 s connect [the operator's historical 4.4-5.0 s] + 8.0 s read +
8.0 s probe window ⇒ att-accepted-silent), but contract 011 §3 emitted
every evidence line at INFO while the operator's reading surface (HA
Settings → System → Logs — the `system_log` ring buffer) stores only
WARNING and above (research §4). The fix: move the decisive-once
evidence grammar to WARNING (strings byte-identical), add a
build-marker WARNING line so the running build is always identifiable,
and make the test suite assert LEVELS (the coverage hole — the 011
caps store messages only, so an operator-invisible grammar passed
green).

## Implementation order (Coder blocks; details in tasks.md)

| Block | Content | Files |
|---|---|---|
| 1 | the level amendment: 22 `_LOGGER.info` → `_LOGGER.warning` sites in `run_security_probe` + the pairing ladder | `aqua_client.py` |
| 2 | the build marker: manifest-version helper + the first-line WARNING in `_async_test` | `config_flow.py` |
| 3 | version bump 0.7.0 → 0.8.0 | `manifest.json` |
| 4 | tests: caps record levels; level-contract test; build-marker test; version-drift test | `tests/test_security_probe_011.py` |
| 5 | README pointer to the 012 quickstart | `README.md` |

Landing discipline: block-by-block, `python3 -m pytest tests/ -q`
green after each; block 1+2+4 are the operator-visible deliverable
(the panel shows the verdict on the NEXT run); block 3 rides with 2
(the marker must not lie); block 5 closes.

## Block 1 — the level amendment (`aqua_client.py`)

Exactly 22 call sites change `_LOGGER.info(` → `_LOGGER.warning(`;
**no message string, argument order or lazy %-formatting changes**:

- `run_security_probe` (module function, :2273-2420): :2317 (entry
  line), :2337 (classified verdict), :2342 (att-accepted-silent),
  :2354 (Modbus-exception-frame), :2365 (acknowledged-write-answered),
  :2393 (probe pair catch `pairing: failed`), :2412 (still-silent),
  :2416 (answered-after-pairing).
- pairing ladder: :901, :908, :916, :929 (pair() body); :943, :945,
  :949 (rung A `_call_pair_method_once`); :972, :993, :995, :999
  (rung B `_pair_via_reconnect`); :1011, :1032, :1039 (rung C
  `_rung_c_retry`).
- :2387 (pair budget) is ALREADY WARNING — untouched.
- Every edited site's comment gains one clause citing
  `contract 012 §2` (the level move; the research 012 §4 mechanism).
  The function docstrings of `run_security_probe`/`pair` gain one
  sentence: "Evidence lines are WARNING (contract 012 §2): the
  operator's log panel (system_log) shows only WARNING+."
- DO NOT touch: census lines (:2075 block), idle-window summary
  (:1859/:1867), enumeration/summary/attempt lines (:624/:763/:774),
  re-send lines (:1821), TX/RX/chunk DEBUG lines, the option-path
  DEBUG marker (connect pair=True), any WARNING/ERROR line.

## Block 2 — the build marker (`config_flow.py`)

Module level (after the imports, near `TEST_TIMEOUT`):

```python
def _manifest_version() -> str:
    """The integration's manifest version — the build marker (spec
    012 FR-2, contract 012 §3): the panel must always identify which
    build produced the lines below. Guarded: the marker never breaks
    the flow, and never lies (a test pins equality with the manifest).
    """
    try:
        import json
        from pathlib import Path
        return json.loads(
            Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
        )["version"]
    except Exception:  # noqa: BLE001 — marker must never break the flow
        return "unknown"


BUILD_VERSION = _manifest_version()
```

In `_async_test`, immediately after `t_test = time.monotonic()` (:472)
and before `self._stage = "connect"` (:473):

```python
        # Spec 012 FR-2 (contract 012 §3): the test's first line names
        # the running build at WARNING — the panel the operator reads
        # stores only WARNING+ (research 012 §4), and a marker mismatch
        # on an "updated" install means the files never loaded (the
        # stale-module world, research 012 §5).
        _LOGGER.warning(
            "connection test to %s: build %s", self._address, BUILD_VERSION
        )
```

No other change: the handlers, the stages, the UI keys and the
failure-line formats stay byte-identical.

## Block 3 — version bump

`manifest.json`: `"version": "0.7.0"` → `"version": "0.8.0"`.
The marker reads it automatically (Block 2); Block 4's drift test pins
the pair.

## Block 4 — tests (`tests/test_security_probe_011.py`, additive)

1. `_LogCap.emit` (:283) and `_FlowLogCap.emit` (:1212): also append
   `record.levelname` to a new `self.levels` list (keep `messages` as
   is — every existing assertion keeps passing untouched).
2. New tests (appended in a clearly-marked "Part 7 — spec 012" section;
   they live in this file because the whole flow harness —
   FakeProbeClient, `_make_flow`, `_drive_progress`, the synthetic
   package — lives here; duplicating it in a second file would fork
   the stubbing):
   - `test_flow_probe_evidence_lines_are_warning`: the
     att-accepted-silent script (:1467 pattern); assert EVERY captured
     message starting `security probe:` or `pairing:` has levelname
     "WARNING"; assert census lines (`block push observed`) are NOT
     at WARNING (they stay INFO — contract 012 §1.2).
   - `test_flow_build_marker_logged_once_first`: the marker line
     appears exactly once, is the FIRST captured record, at WARNING.
   - `test_build_version_matches_manifest`: `cf.BUILD_VERSION ==
     manifest.json version` and `!= "unknown"` (drift pin — the marker
     can never lie about the build).
3. Regressions: full `pytest tests/ -q` green — the census suites
   (`lvl >= logging.INFO` assertions in test_census_009.py) are
   untouched because census lines stay INFO; the connect-timing INFO
   assertions are untouched because the connect path is untouched.

## Block 5 — docs

`README.md`: repoint the next-steps pointer (currently at the 011
quickstart) to `specs/012-probe-not-firing/quickstart.md` with two
sentences: the 0.8.0 build makes the probe verdict visible in the
System → Logs panel, and the quickstart's §0 recovers the verdict
already sitting in the existing log file.

## Verification checklist (all blocks)

- [ ] `grep -n "security probe: re-sending" aqua_client.py` — the call
      is `_LOGGER.warning(`.
- [ ] `git diff` shows NO string changes: every reworded line is a
      contract violation (contract 012 §1.3).
- [ ] `python3 -m pytest tests/ -q` green (197 + the 3 new).
- [ ] `python3 -m py_compile` clean on `aqua_client.py`,
      `config_flow.py`; `python3 -c "import aqua_client"` standalone
      smoke still passes.
- [ ] `grep -n "connection test to %s: build %s" config_flow.py` —
      exactly one site, before the `try:`; the format matches
      contract 012 §3 byte-for-byte.
- [ ] `manifest.json` == 0.8.0 and the drift test enforces it.
- [ ] specs 001-011 files untouched (`git status`).
- [ ] Completion report must include: the quickstart 012 §0 grep
      results from the operator's EXISTING log (the retroactive
      verdict), and — after the operator updates + fully restarts —
      the panel-pasted `security probe:` lines.

## Success criteria for this spec (what closes it)

- The operator can read the probe verdict from the panel (or its
  absence, with a build marker proving why) without any terminal, on
  the very next test run.
- The already-run test's verdict is recovered from the existing log
  file (quickstart §0) and routed per contract 011 §6 (predicted:
  att-accepted-silent → E5 phone capture — the follow-up spec's
  input, already decided by the evidence ladder).
- No regression in any grep-stable format; the suite now FAILS if
  anyone ever again hides a decisive-once line below WARNING.