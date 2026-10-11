# Spec 007 — Connect speed gap: measure where the time goes before fixing

Status: **PLANNED** — built from `research.md` (vendor-app reverse of the
session sequence, the installed connect stack dissected, and the
failure-stage arithmetic). Scope guard: this spec does **not** redesign the
config flow (002/004), device resolution (005), the session parity items
(006) or the register/protocol layer (001/003). It adds **connect-stage
timing instrumentation** and ships the **measurement runbook** that the
next failed run must be read through.

## 0. The production fact this spec answers

On the owner's real installation: the **vendor app** completes
list → select → values-displayed in **3-5 s** (phone Bluetooth ON for its
own tests), while **our connection test** takes **>40 s and fails**
(phone Bluetooth OFF during every HA test — the vendor-link-holds-the-unit
explanation is eliminated by protocol). Same hardware, same distance.

Research verdict (research.md, proofs there):

1. **Our >40 s is the 2 × 20 s connect-window signature** — both
   bleak-retry-connector attempts expire without the link/discovery ever
   completing; every post-connect failure mode would fail faster and
   under a different error key (research §8). The stall is at the
   **connect/discovery stage**, before any spec-006 parity code runs.
2. **The vendor's 3-5 s is a genuine live session** — connect → discover
   (Android cache-warm) → subscribe → 2 s constant → read; the budget
   closes without invoking a value cache (research §7).
3. **No correct fix is identifiable from the artifacts** — the leading
   root causes (GATT discovery stall, MTU-exchange firmware quirk,
   HCI-contention on the host adapter) are host/stack-level and mutually
   indistinguishable without one instrumented failing run plus an HCI
   trace (research §9).
4. **Therefore: instrument first, measure, then design the fix in a
   follow-up spec.** This spec's deliverable is the ability to convert the
   next failed run into a root-cause class, plus the runbook that tells
   the owner exactly which observations to capture.

This spec deliberately ships **no behavioural change**: no new frames, no
changed timeouts, no changed attempt counts, no changed sleeps. Anything
else would be patching a stall we have not yet localized.

## 1. User stories

- **US-1** As the pool owner, when the connection test fails again, I want
  the log to tell me *which stage* ate the time and what the real
  transport error was, so I stop guessing between "too far", "app holds
  the link" and "unit refuses".
- **US-2** As the pool owner, I want a short, ordered measurement
  checklist (run the test → capture btmon → bluetoothctl differential →
  optional adb logcat) with a decision table, so one evening of
  measurements replaces weeks of speculation.
- **US-3** As the developer, I want per-stage timings (resolve / connect /
  notify / write-char / settle / first-read) logged on every connect —
  success AND failure — so the >40 s decomposes into numbers instead of
  one opaque duration.
- **US-4** As the developer, I want the attempt-window arithmetic
  (max attempts × 20 s) visible at INFO level in our own log, so the
  40 s failure is self-explaining without enabling debug for three
  external libraries.
- **US-5** As the integrator, I want the runbook's decision table to map
  each possible observation to its consequence ("host-level, file an
  adapter/firmware issue", "wedge, power-cycle", "post-connect bug, new
  spec"), so the follow-up spec is written from data, not folklore.

## 2. Functional requirements

### FR-1 — Connect stage-timing instrumentation in `AquaTempBleClient` [P0]

`AquaTempBleClient.connect()` records `time.monotonic()` timestamps for
each stage and logs a machine-greppable summary at INFO on success, and a
partial summary plus the failing stage on failure. Stages (normative
names, contract `contracts/connect-stage-timing.md`):

| Stage | Boundaries |
|---|---|
| `resolve` | start of `_resolve_device()` → device obtained (or `None`) |
| `connect` | start of `establish_connection(...)` (or raw-client loop) → client returned (or exception) |
| `notify` | start of `start_notify(ff02)` → returned |
| `write-char` | `_resolve_write_characteristic()` duration |
| `settle` | the `SETTLE_AFTER_SUBSCRIBE` sleep (logged as its configured value) |
| `first-read` | logged by the *caller* (config-flow test / coordinator) around the first `read_registers` |

Rules:
- No behavioural change: same calls, same order, same error semantics;
  only timestamps + log lines are added.
- The existing ERROR line (`connect to %s failed: %s`, spec 005 FR-2)
  gains the failing stage and elapsed time — the exception chain stays
  verbatim (it already carries the decisive `BleakNotFoundError … <-
  TimeoutError` signature, research §8).
- Timings use a module-level `_now = time.monotonic` seam so tests can
  fake the clock without patching `time` globally.
- The standalone path (`tools/smoke_client.py`, no HA) benefits
  automatically — same method.

### FR-2 — Attempt-window transparency at INFO [P0]

Immediately before calling `establish_connection`, log one INFO line
naming the budget arithmetic, e.g.
`connecting to %s: up to %d connect attempt(s) of %.0f s each (bleak-retry-connector BLEAK_TIMEOUT)` —
with the max_attempts argument and the 20.0 s constant cited from research
007 §4. This converts the ">40 s" UX into a log that explains itself:
two exhausted 20 s windows are visible as one line + one failure line
with elapsed ≈ 40.5 s.

### FR-3 — Config-flow test logs stage + elapsed on every failure branch [P0]

`config_flow._async_test()` records the test start time and appends
`at stage X after Y.Y s (budget ZZ s)` to each existing failure log line
(spec 005 FR-2 branches — the UI error keys and their mapping are NOT
changed). The stage for connect-phase failures is `connect`; the
read-phase failures report `first-read`. No new UI strings, no
translation changes.

### FR-4 — Measurement runbook shipped (no code) [P1]

`quickstart.md` (this kit): ordered protocol — (1) run the failing test
once with the new logs enabled and capture the stage lines; (2)
`bluetoothctl` timed connect differential; (3) `btmon` HCI trace during
one failing test; (4) HA-stopped differential (scanning contention);
(5) wedge clearing; (6) optional `adb logcat` of one vendor session
(decides the 2 s constant's role, the MTU question, and the cache
question in one capture); (7) a report template and the decision table.
Every step states what it proves and its [P]/[V] status.

### FR-5 — Decision table maps observations → follow-up spec [P1]

The runbook's table is normative for what happens next (research §9):

| Observation (logs/HCI) | Root-cause class | Consequence |
|---|---|---|
| ERROR chain `BleakNotFoundError … <- TimeoutError`, elapsed ≈ 2×20 s, `btmon` shows no `LE Connection Complete` | H-B: link never establishes (adapter/RF/address-type) | host-level: adapter placement/USB dongle, address type check; integration unchanged |
| `LE Connection Complete` present, `Read By Group Type` loops/never completes | H-A: GATT discovery stall | host/module-level (BlueZ cache cold, research §3); integration unchanged; file with btmon evidence |
| `LE Connection Complete` → `ATT Exchange MTU Request` → link drop | H-C: MTU-exchange firmware quirk | host/module-level; report to vendor; integration unchanged |
| Connect succeeds in logs but `first-read` times out | post-connect bug | NEW spec — our side, fixable in code |
| Connect succeeds after power-cycle only | H-D wedge | runbook: wedge-clearing procedure; integration unchanged |
| Vendor logcat shows values before first 指令已发送 | vendor cache renders | research Q7 upgraded; comparison reinterpreted (does not change the fix path) |

## 3. Non-functional requirements

- **NFR-1** Touched files (nothing else):
  `custom_components/aqua_temp_ble/aqua_client.py`,
  `…/config_flow.py`,
  `tools/smoke_client.py` (logging setup only),
  plus tests under `tests/`. `const.py` is **NOT** touched.
- **NFR-2** **No behavioural change of any kind**: no new/changed BLE
  frames, no changed constants (no budget, no attempts, no settle, no
  prefixes), no changed error-key mapping, no new UI text.
- **NFR-3** No protocol-layer changes (specs 001/003 own that), no
  config-flow redesign (002/004), no resolution changes (005), no session
  parity changes (006) — 007 only *observes* the path they built.
- **NFR-4** Standalone mode keeps working without Home Assistant (no new
  HA imports; the instrumentation is pure stdlib + logging).
- **NFR-5** Durable artifacts in English; every new comment cites
  `research 007 §…`.
- **NFR-6** All existing tests stay green; new tests only ADD coverage.

## 4. Acceptance criteria

- **AC-1** A successful connect against the test fake emits ONE INFO
  summary line containing all five connect-path stage names with
  non-negative durations that sum to the total (unit test, fake clock).
- **AC-2** A failing `establish_connection` (fake raising
  `BleakNotFoundError` with a `TimeoutError` cause) makes the client log
  the failure at stage `connect` with the elapsed value and the full
  exception chain; the summary is absent/partial accordingly (unit test).
- **AC-3** The INFO attempt-window line is emitted before the connect call
  and names the max_attempts value passed (unit test).
- **AC-4** Every failure branch of `_async_test` logs `at stage … after …
  s` with the test budget; UI keys unchanged (unit test on the branch
  mapping).
- **AC-5** `python3 -m py_compile` clean on all touched files;
  `pytest tests/` fully green; no file outside NFR-1's list modified.
- **AC-6** `quickstart.md` exists with the seven steps and the decision
  table (delivered with this kit — Coder verifies presence only).

## 5. Risks & guards

| Risk | Guard |
|---|---|
| Logging adds overhead/latency to the connect path | timestamps only (`time.monotonic()`), lines built once per connect, INFO level; no per-PDU logging |
| Stage lines leak into every coordinator poll (noise) | summary is one line per connect; coordinator already logs one INFO per cycle since spec 005-era code |
| Someone reads `stage=connect` as "bluetoothctl will work fine" | runbook step 2 exists precisely to separate integration-path failures from host-level ones |
| Instrumentation drifts into a behavioural change (NFR-2) | AC-5's file list + NFR-2 verbatim; Reviewer greps the diff for `const.py`, `SETTLE`, `max_attempts`, `TEST_TIMEOUT` — all must be untouched |
| The next run succeeds (transient) and proves nothing | runbook step 1 says: repeat the test 2-3× in one session and capture all runs; a single success is itself data (research §9 H-D) |