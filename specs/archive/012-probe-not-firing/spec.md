# Spec 012 — Probe not firing? No: probe firing, verdict invisible — the log-level visibility fix

Status: **PLANNED** — built from `research.md` (the full execution trace of the
observed 20.9 s production run, the duration arithmetic, and the Home
Assistant system-log panel semantics). Scope guard: this spec changes
ONLY the config-test path's **log levels** and adds ONE build-marker
line. It sends no new frames, changes no protocol bytes, no UI keys, no
census formats, no production/poll behavior, no options behavior, and
touches no requirement of specs 001-011 except the **level column** of
contract 011 §3 (amended additively by `contracts/log-visibility.md`;
the 011 files themselves stay as historically landed).

## 0. Why this spec exists (one paragraph)

The operator ran the d860933 connection test (specs 010+011, debug
logging enabled on `custom_components.aqua_temp_ble`) and reported:
the final failure line `no response: AquaTempTimeoutError: … within
8.0s … at stage first-read after 20.9 s` appeared, but **zero
`security probe:` and zero `pairing:` lines** — the probe looked dead.
The trace (research 012 §2) proves the wiring is airtight: every entry
into `run_security_probe` logs an entry line *before anything else*
(aqua_client.py:2317), and every `return None` path logs its verdict
first — an unlogged probe is **impossible**. The arithmetic (research
§3) proves the probe **did run**: 20.9 s = ~4.9 s connect (the
operator's two historical production connects: 4.4 s and 5.0 s) + the
8.0 s first-read window + **one more 8.0 s window that only the probe
stage can consume**, and the elapsed leaves no room for a pair or
retry stage — the arithmetic fingerprint of the
`att-accepted-silent` verdict. The verdict lines exist in
`home-assistant.log`, but they are emitted at **INFO**, and the
operator's reading surface — HA Settings → System → Logs, the
`system_log` ring buffer — **stores only errors and warnings**
[research §4, HA docs]: INFO lines are invisible there regardless of
the logger's debug level. Spec 011's own assumption (FR-2.5: "the
operator reads them from the HA UI log view") was false for the panel
the operator actually uses. The probe fired; its evidence was
unreadable. This spec makes the evidence **visible**, makes the
running **build self-identifying** (the one ambiguity the trace could
not close from the outside), and closes the test hole that let an
operator-invisible evidence grammar pass the suite (research §6).

## 1. Functional requirements

### FR-1 — Evidence lines move INFO → WARNING [P0]

Exactly the probe/pair/retry evidence grammar of contract 011 §3 — the
**decisive-once lines** — is re-emitted at WARNING. **The message
strings are byte-identical** (grep stability, 011 AC-3); only the
level changes. The amended lines (call sites at d860933):

- `aqua_client.py run_security_probe` (:2317 entry, :2337 classified
  verdict, :2342 att-accepted-silent, :2354 Modbus-exception-frame,
  :2365 acknowledged-write-answered, :2393 pairing-failed catch,
  :2412 still-silent-after-pairing, :2416 answered-after-pairing);
- `aqua_client.py` pairing ladder (:901, :908, :916, :929, :943, :945,
  :949, :972, :993, :995, :999, :1011, :1032, :1039 — every
  `pairing:` line);
- the already-WARNING pair-budget line (:2387) and every pre-existing
  WARNING/ERROR line of specs 005-011 stays untouched.

**Unchanged levels (contract 012 §2):** the high-volume diagnostics —
census lines (`block push observed`, `frame observed`), idle-window
summary, `GATT enumeration`, `using write characteristic`,
`connect summary`, `Connected to`, `connecting to … attempt(s)`,
`first-read re-send n/3` — stay at INFO; TX/RX and chunk lines stay at
DEBUG; the production option marker `connect with pairing requested`
stays DEBUG (011 §3.5). No census format changes (008/009 greps keep
matching).

### FR-2 — Build marker: one WARNING line per connection test [P0]

Every `_async_test` invocation starts with exactly one line:

```
connection test to <address>: build <version>
```

at WARNING, as the test's **first** log line. `<version>` is read from
`manifest.json` at module import (guarded; `"unknown"` on any failure —
the marker must never break the flow). Purpose: the running build
becomes identifiable from the operator's panel — the stale-module
world (research §6) is permanently detectable, and the operator can
always confirm an update actually loaded. The 007-stable failure-line
formats are untouched.

### FR-3 — Tests assert levels (the observer-visibility contract) [P0]

- Both log caps in `tests/test_security_probe_011.py` (`_LogCap`
  :276, `_FlowLogCap` :1207) additionally record each record's
  `levelname` (additive; existing string assertions unchanged).
- New tests: (a) every `security probe:` / `pairing:` line captured in
  the flow walkthroughs is at WARNING; (b) the build marker is logged
  exactly once, first, at WARNING; (c) the marker's version equals
  `manifest.json`'s version (drift pin — the marker can never lie).

### FR-4 — The operator verification path (quickstart) [P0]

`quickstart.md` ships the Tier-0 retroactive check (the already-run
test's verdict is recoverable from the downloaded full log — no new
run, no terminal), the full-restart discipline for the 012 build, and
the outcome routing (att-accepted-silent → the contract 011 §6
decision matrix → E5 phone capture).

### FR-5 — Non-regression [P0]

UI keys unchanged (`no_response`, `cannot_connect`, …); the final
failure lines byte-identical (007/009 formats); census and connect
formats unchanged; success path still runs no probe, no pair, and no
`security probe:`/`pairing:` line — the build marker is the single
new line there; the poll/coordinator path and the `pair_on_connect`
option path are untouched; `TEST_TIMEOUT` stays 70.0 s and the
contract 011 §4 arithmetic is unchanged.

### FR-6 — Version bump [P1]

`manifest.json` 0.7.0 → **0.8.0** (AC-6 of 011 landed 0.7.0 in
d860933; the marker and the manifest move together — FR-3c pins
equality).

## 2. Acceptance criteria

- **AC-1**: `python3 -m pytest tests/ -q` fully green (the 197
  existing + the new 012 tests); the existing probe assertions keep
  passing unchanged (they match message strings).
- **AC-2**: level contract — a fake-client flow walkthrough of the
  timeout path asserts: probe entry line at WARNING, verdict line at
  WARNING, and zero `security probe:`/`pairing:` records at any other
  level; census lines still at INFO.
- **AC-3**: build marker — present exactly once per test attempt, the
  first captured line, at WARNING, with the version equal to
  `manifest.json`'s (drift-pinned).
- **AC-4**: grep stability — `block push observed`,
  `idle window before first request`, `connect summary`, the re-send
  lines, and the final `connection test to … no response …` /
  `… failed: …` / budget lines are byte-identical to the 008/009/007
  formats (regression suites green without edits).
- **AC-5**: success-path non-regression — first read answers ⇒ no
  probe, no pair, no probe/pair log lines; the build marker is the
  only added line.
- **AC-6**: `manifest.json` version 0.8.0; `python3 -m py_compile`
  clean on touched files; the standalone import smoke
  (`python3 -c "import aqua_client"`) still passes.
- **AC-7**: durable artifacts in English; specs 001-011 files
  untouched (012's contract amends the 011 §3 **level column**
  additively from its own file); every new/edited comment cites
  `research 012 §…` / `contract 012 §…`.

## 3. Non-goals

- No new protocol bytes, frames, probes, or re-send changes (the
  att-accepted-silent routing already says: no more probing frames —
  contract 011 §6 row 3).
- No pairing-behavior changes, no coordinator/option threading (the
  deferred 011 leftovers are a separate landing; the option is earned
  only after X2/X3 evidence).
- No census/level changes beyond FR-1's list; no UI-key or translation
  work.
- No E5 analysis (the phone capture is the follow-up the verdict
  routes to, per contract 011 §6).