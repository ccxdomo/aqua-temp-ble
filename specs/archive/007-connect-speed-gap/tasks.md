# Tasks 007 — Ordered implementation blocks for the Coder

Work top-down; each block ends with its gates. Repo rule: durable artifacts
(code comments, commit messages) in **English**. Do not touch anything
outside NFR-1's file list (`aqua_client.py`, `config_flow.py`,
`tools/smoke_client.py`, `tests/`). The normative design is `plan.md` +
`contracts/connect-stage-timing.md`; proofs are in `research.md` (§ refs
below). **NFR-2 is absolute: this spec OBSERVES the connect path — no
behavioural change whatsoever** (no new frames, no changed constants, no
changed attempt counts, no changed sleeps, no new UI text). If a task
seems to require one, stop and report instead.

---

## Block A — Stage-timing instrumentation in the client (P0)

**A1. `aqua_client.py`: time seam** *(FR-1)* — **DONE** — add `import time` and the
module-level `_now()` wrapper (plan §1.1). No other import changes.

**A2. `aqua_client.py`: stage timestamps in `connect()`** *(FR-1)* — **DONE** — record
`t0` / `resolve` / `connect` / `notify` / `write-char` / `settle`
timestamps per plan §1.2. Constraints:
- every existing call, argument, exception handler and message-shape stays
  byte-compatible where not explicitly extended;
- the existing `Connected to %s (%s)` INFO line stays (extra summary
  emitted just before it);
- failure lines gain `at stage … after … s` per contract §3.1/§3.2, with
  the `exc_chain` chain untouched;
- durations are computed only from completed stages — never negative.

**A3. `aqua_client.py`: attempt-window INFO line** *(FR-2)* — **DONE** — before
`establish_connection` (and its standalone twin in the raw path), per
contract §2.1, citing `research 007 §4` in the comment.

**A4. Tests** — **DONE** (new `tests/test_connect_timing.py`, red-first then 6/6) — extend `tests/test_connect_parity.py` (or add
`tests/test_connect_timing.py`): fake `aqua_client._now`; assert
- one `connect summary` INFO on success whose five stage durations sum to
  the total (AC-1);
- the failure ERROR contains `at stage connect`, the elapsed value, and
  `TimeoutError` in the chain when the fake raises `BleakNotFoundError(…)
  from TimeoutError()` (AC-2);
- the attempt-window INFO precedes the connect call and names the
  `max_attempts` value (AC-3).

**Gate A:** `pytest tests/ -q` green; `python3 -m py_compile
custom_components/aqua_temp_ble/aqua_client.py`; `git diff --stat` shows
only the allowed files.

> **PASSED** — 80/80 pytest green; py_compile clean; the diff shows only
> `aqua_client.py`, `config_flow.py`, `tools/smoke_client.py`,
> `tests/…` (plus a pre-existing `manifest.json` formatting/version
> change that predates this spec — not the Coder's edit).

## Block B — Config-flow test stage/elapsed logging (P0)

**B1. `config_flow.py`: `_async_test()` elapsed + stage label** *(FR-3)* — **DONE** —
test start time at entry; stage label `connect` → `first-read` →
`disconnect` as the body progresses; append
`at stage <stage> after <e.1f> s (budget 70.0 s)` to **every** existing
failure `_LOGGER.warning` branch (spec 005 FR-4 mapping preserved —
contract §3.3 table). No new UI keys, no translation edits, no schema
edits, `TEST_TIMEOUT` untouched.

**B2. Tests** — **DONE** (5 branch assertions extended + new unknown-branch test, red-first) — extend the walkthrough tests: for each failure injection,
the warning line contains `at stage ` + `after ` + `budget`; the UI
error key is unchanged (AC-4).

**Gate B:** `pytest tests/ -q` green; `py_compile` on `config_flow.py`;
grep the diff: `TEST_TIMEOUT`, `max_attempts`, `SETTLE` must appear only
in comments/log strings, never as changed values.

> **PASSED** — grep of the diff verified: `TEST_TIMEOUT`, `max_attempts`,
> `SETTLE` appear only in comments, docstrings and log format
> strings/args. Values unchanged: `TEST_TIMEOUT = 70.0`,
> `SETTLE_AFTER_SUBSCRIBE = 2.0`, `max_attempts = 2` at every call site.

## Block C — Standalone visibility (P1)

**C1. `tools/smoke_client.py`** — **DONE** — add
`logging.basicConfig(level=logging.INFO, …)` at `main()` entry so the new
INFO summary/failure lines are visible without HA (plan §3). Nothing else
changes; the register dump output stays byte-identical.

**Gate C:** manual smoke: `python3 tools/smoke_client.py <address>` against
the test fake (or syntax check if no device): summary lines reach stderr.

> **PASSED** — no device at the bench, so the smoke tool ran against a
> faked transport: the attempt-window, `connect summary` and `Connected
> to` lines reached **stderr** at INFO (real clock, settle 2.0 s visible),
> and the stdout register dump stayed byte-identical (46 lines).

## Block D — Kit verification (P0, no code)

**D1.** **DONE** — Verify the spec kit is complete in `specs/007-connect-speed-gap/`:
`spec.md`, `plan.md`, `research.md`, `quickstart.md`, `tasks.md`,
`contracts/connect-stage-timing.md` (AC-6). Do not edit the specs.

**Final gate (all blocks):**
- `pytest tests/ -q` fully green; `python3 -m py_compile` on all touched
  files;
- `git diff --stat` audit: exactly `aqua_client.py`, `config_flow.py`,
  `tools/smoke_client.py`, `tests/…` — **`const.py` absent**;
- Reviewer spot-checks: summary line format matches contract §2.2
  verbatim; the `exc_chain` chain is intact in every failure line.

> **PASSED** — kit complete (6 files, AC-6); 80/80 pytest green;
> py_compile clean on every touched file; `const.py` absent from the
> diff (byte-identical); the summary format is pinned byte-exact by
> `tests/test_connect_timing.py` (contract §2.2) and the `exc_chain`
> chain is asserted in every failure-line test. Notes for the Reviewer:
> `tests/test_connect_timing.py` is new (untracked), and
> `specs/007-connect-speed-gap/` is untracked as delivered; the
> `manifest.json` diff predates this spec.

## Post-implementation (owner-run, not the Coder's)

The measurements are the OWNER's (quickstart steps 1-6). Their outcome
selects the next spec from research §9's decision table:
- H-A/H-B/H-C confirmed → host-level runbook, integration unchanged;
- post-connect stage confirmed → new spec with the captured evidence;
- 2 s constant = poll cadence (logcat) → follow-up re-words spec 006
  FR-1's rationale only.