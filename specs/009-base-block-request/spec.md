# Spec 009 — Base-block request: measure delivery, then decide

Status: **PLANNED** — built from `research.md` (the byte-accounting proof,
the temporal analysis, the cause ranking, the frame allowlist). Scope
guard: this spec does **not** redesign the transport decode (008 —
shipped and proven by the census), the register map (001/003), the
config flow UX (002/004), device resolution (005), session parity (006)
or connect instrumentation (007). It ships the **instrumentation that
separates the remaining causes** (research §7: C1-C6) and exactly one
evidence-collecting behaviour change (a bounded, app-parity re-send of
the unchanged base read in the connection test). **No blind fix**: no new
request frame enters the integration (research §8); every strategy
change waits for the probe/E5 evidence (research §11 decision matrix).

## 0. The production fact this spec answers

The spec-008 build (ce41aa5) fixed the decode and made the client
honest — and honesty revealed the real situation (research §0):

```
no matching response within 8.0s
(received 12 frame(s) / 1188 bytes;
 blocks seen: 1001, 1046, 2001, 2046;
 none for start 7)
at stage first-read after 13.0 s (budget 70 s)
```

12 × 99 = 1188 exactly: the unit sent **nothing but pushes** — no FC03
answer, not even a Modbus exception frame — for our byte-perfect
vendor-proven base read. The unit's answer, if any, does not reach the
wire we listen on, does not exist, or needs more requests than one
(research §7). Two production runs produced the same silence.

This spec does not guess which. It instruments: a per-start, timestamped
census; a bounded re-send in the connection test (the cheapest possible
production experiment — an idempotent read, repeated); a standalone
session probe with phases that isolate each cause; and the runbook that
captures the two decisive artifacts (the existing log's GATT/enumeration
lines; one HCI snoop of a vendor session on THIS unit).

## 1. User stories

- **US-1** As the pool owner, I want the connection test to keep trying
  the way the vendor app plausibly does — sending the identical
  vendor-proven request a few times within the same window — so that a
  unit which answers "the Nth request" or needs a clear gap between
  pushes passes the test without any new protocol guess.
- **US-2** As the pool owner, I want the next failed run's log to tell me
  the per-block push counts, the push onset relative to the subscribe,
  and the push timing relative to each send — so one run distinguishes
  "pushes before my request" from "pushes after it" and "4-block cycle"
  from an uneven stream (research §0.3, §1.1).
- **US-3** As the developer, I want a standalone probe (no Home
  Assistant) that listens, sends one frame, polls, queries a block,
  tries write-with-response and subscribes to every notifying
  characteristic — with a full timestamped timeline — so ONE run at the
  panel separates C1/C2/C5 from C3/C6 (research §7, §11).
- **US-4** As the developer, I want the never-sent experimental frames
  (`63100007002db997`, `630303e9002d5c25` — research §8) to exist ONLY
  behind an explicit flag in the probe, never in the integration — so
  no unproven frame can ever reach production by accident.
- **US-5** As the integrator, I want the evidence runbook to name the
  exact log lines to grep in the EXISTING production log (ff03
  properties, write-resolution path, idle census) and the one definitive
  capture (vendor HCI snoop) — so the follow-up spec is written from
  data, not theories.

## 2. Functional requirements

### FR-1 — Census upgrade: counts, onset, timestamps [P0]

- Per-request census lines gain a request-relative timestamp
  (`t+%.2fs` from the request start) — `block push observed:
  start=%d qty=%d len=%d crc=%s (waiting for start %s; t+%.2fs)`.
- The timeout message gains **per-start counts** and first/last offsets,
  e.g.: `no matching response within 8.0s (received 12 frame(s) / 1188
  bytes; blocks seen: 1001x3, 1046x3, 2001x3, 2046x3; first t+0.4s,
  last t+7.8s; none for start 7)` — the 4-block-cycle model
  (research §0.3) becomes testable from one run.
- **Idle-window summary line** (NEW, INFO, logged once at the first
  request of a connection): `idle window before first request: N block
  push(es), starts seen: 1001, 1046, …, first at t+%.2fs after
  subscribe` — answers push-onset (E2) without per-push spam; the
  per-connect first-idle-INFO line stays as shipped.
- Spec-008's guarantees are preserved: counters are honest totals;
  nothing is dropped silently; the "got 0 bytes" blind spot stays dead
  (tests updated to the new grammar, keeping the old properties pinned).

### FR-2 — Bounded app-parity re-send of the base read [P0]

- In the **config-flow connection test only** (first-read stage), the
  client re-sends the **identical, unchanged** frame
  (`63030007002d3c54`) up to 3 additional times, 2.0 s apart, INSIDE the
  existing 8.0 s window (sends at t+2.0/4.0/6.0 s; no timeout constant
  changes; stage arithmetic unchanged, research §0.1).
- Every re-send logs at INFO: `first-read re-send 1/3: 63030007002d3c54`
  — the production log must show attempt counts plainly.
- Rationale (research §1.3/§2.3): the app's 2 s Duration sits in the
  subscribe→read slice [P pool] and may be a poll cadence [V]; retry
  counts are invisible statically [V]. The re-send is the cheapest
  in-production test of C2/C5 — and a plausible fix for free if it is.
- Safety (research §8): an FC03 read is idempotent; 4 × 8 bytes of
  extra airtime per connection test cannot modify unit state; the frame
  is vendor-proven byte-for-byte; the unit demonstrably ignores
  requests without consequence (2 production runs).
- The coordinator's polling path is **unchanged** (single read per
  cycle) until evidence (NFR-3) — no new runtime behaviour outside the
  connection test.

### FR-3 — Standalone session probe `tools/session_probe.py` [P0]

Raw-bleak CLI tool (no Home Assistant, smoke_client-style import of the
component's `modbus`/`const`), default address = the owner's unit.
Phases (plan §3; every frame CRC-decoded on the fly; ms timestamps;
phase markers; end-of-run summary):

- **L (listen)** — connect, subscribe, NO TX for `--listen` s
  (default 30): push onset vs subscribe, cadence, per-start counts,
  cycle order, change-triggered re-push (E2/E7).
- **B (base read)** — ONE TX of `63030007002d3c54`, listen 15 s
  (default): any answer/exception/late answer (E1/E3).
- **P (poll)** — re-send the base read every `--poll-every` s
  (default 2.0) × `--poll-count` (default 15): answers-the-Nth-request
  (E3/C2/C5).
- **Q (query)** — ONE TX of `631003e9002dd9e6` (block query 1001 — an
  app-proven frame), listen 15 s: an immediate out-of-cycle 1001 push
  proves our writes reach the engine (E4/C1 vs C3).
- **W (write type)** — repeat the base-read TX with
  write-**with**-response: ATT errors surface; a reaction would prove
  the write-command path was the problem (E4/C1a).
- **E (experimental)** — OFF unless `--experimental`: sends
  `63100007002db997` (query-shaped base request) and
  `630303e9002d5c25` (FC03@1001), both research §8, ONLY after Phase Q
  in the same run; prints the risk statement first. Never linked into
  the integration.
- `--subscribe-all`: subscribe to EVERY notify/indicate characteristic
  in the enumerated table, tag frames per characteristic (C6).

The probe is a **measurement instrument**: it never writes registers
(reads/queries only), never reconnects in a loop, and stops at the
first phase whose result is decisive if `--stop-on-answer` is given.

### FR-4 — Runbook: the evidence capture protocol [P1]

`quickstart.md`: (0) grep the EXISTING production log for the enumeration,
write-resolution and idle-census lines (E4/E2 halves, zero new run
needed); (1) run the new build's connection test, paste the census
(counts + onset); (2) run the probe phases at the panel, paste the
timeline; (3) the definitive capture — HCI snoop of one vendor session
on THIS unit (E5, research §2.4: the premise "the app works here" is
itself unproven); (4) report template. Each step states what it proves.

### FR-5 — Session-request contract shipped [P1]

`contracts/session-request.md` (normative): the frame allowlist
(research §8), the session order, the re-send bounds, the
experimental-frame gates, and the decision matrix (research §11) that
the follow-up spec must obey.

## 3. Non-functional requirements

- **NFR-1** Touched files: `custom_components/aqua_temp_ble/aqua_client.py`
  (FR-1/FR-2), `…/config_flow.py` (FR-2 call site, one line), NEW
  `tools/session_probe.py` (FR-3), tests under `tests/`, the spec-009
  kit. `modbus.py`, `registers.py`, `coordinator.py`, entities,
  translations: **untouched**.
- **NFR-2** No timeout constant changes (REQUEST_TIMEOUT, SETTLE,
  SILENCE, WRITE_ECHO, MAX_RX_BUFFER, TEST_TIMEOUT all stay); the
  re-send lives INSIDE the existing 8 s window.
- **NFR-3** The coordinator polling path (one read per cycle) is
  byte-identical in behaviour; the re-send option is opt-in via
  parameter and used only by the connection test.
- **NFR-4** No new frame in the integration: the connection test sends
  only `63030007002d3c54` (possibly ×4); block queries stay probe-only
  (spec 008 scope); experimental frames live only in the probe behind
  `--experimental`.
- **NFR-5** Standalone mode keeps working (no HA imports in
  aqua_client; session_probe.py imports modbus/const only).
- **NFR-6** All existing tests stay green except census-message
  grammar updates (FR-1 changes the two pinned census strings
  deliberately — the old properties remain pinned in updated tests);
  the modbus.py self-test stays byte-identical.
- **NFR-7** Durable artifacts in English; every new comment cites
  `research 009 §…` / `contract 009 §…`.

## 4. Acceptance criteria

- **AC-1** Census grammar (FR-1): a fake-notify run with the four
  production block starts ×3 asserts the timeout message contains
  `1001x3`, `1046x3`, `2001x3`, `2046x3`, `first t+`, `last t+`, and
  `none for start 7`; census lines carry `t+%.2fs`; the idle-window
  summary line appears exactly once per connection before the first
  request (unit tests, faked clock).
- **AC-2** Re-send (FR-2): with the re-send parameter set, a fake
  write characteristic receives exactly 4 writes of
  `63030007002d3c54` at t+0/2/4/6 s in an 8 s window, each logged
  (INFO); without it, exactly 1 write (coordinator path, pinned).
  A matching answer arriving after any re-send completes the request
  (unit test). The timeout message and stage budget are unchanged when
  nothing arrives (unit test).
- **AC-3** Probe (FR-3): `python3 -m py_compile` clean; `--help`
  documents every phase; a dry-run mode (`--dry-run`) prints the phase
  plan and the exact frames it would send, WITHOUT connecting
  (unit-tested); the experimental phases refuse to run without
  `--experimental` (unit-tested gate); Phase Q's frame is the proven
  `631003e9002dd9e6` (unit-tested vector).
- **AC-4** Spec-008 regressions: pushed-block matching, fragmented
  block reassembly, echo self-heal, exception fail-fast, honest-count
  tests all stay green (updated grammar only).
- **AC-5** The kit ships: spec/plan/research/contracts/session-request/
  quickstart/tasks (this kit) — presence checked; quickstart §0 greps
  name the exact log-line prefixes.
- **AC-6** `pytest tests/` fully green; `python3
  custom_components/aqua_temp_ble/modbus.py` self-test unchanged and
  passing; no file outside NFR-1's list modified (`git status` gate).

## 5. Risks & guards

| Risk | Guard |
|---|---|
| Re-send angers the unit / floods the link | 4 × 8 bytes idempotent reads, inside one window, connection test only (FR-2 bounds); the unit ignored 2 single shots without any observable effect [P] |
| Re-send masks a real delivery bug (answers arrive, we stop investigating) | The census counts answers per attempt; every re-send logged; E4/E5 evidence steps stay in the runbook regardless of test outcome |
| Census spam at INFO | Idle pushes stay one-INFO-per-connect + the single idle-summary line; request-window census stays bounded by window length |
| Experimental frames leak into production | Probe-only + `--experimental` flag + allowlist contract (research §8, contract §4); NFR-4 grep gate in tasks |
| Probe misreads a push as a "reaction" | Phase Q's verdict is OUT-OF-CYCLE arrival vs Phase L's measured baseline cadence (plan §3.4) — not "did a 1001 push occur" (one arrives every ~2.7 s anyway) |
| Message-grammar change breaks greps/tools | Old properties re-pinned in updated tests (NFR-6); the strings.json/UI keys untouched (NFR-1) |

## 6. Out of scope (explicit)

- Any change of the base-read request bytes, address or count
  (research §3: the request is right; delivery is the question).
- Any new frame in the integration, including the query-shaped base
  request — that is a spec-010 decision gated on Phase Q/E evidence
  (research §11).
- Consuming pushed blocks into entities (Track B, research §9) —
  semantics uncalibrated; needs E6.
- Write-path changes (write type in the integration, ff03-vs-candidate
  order) — E4/Phase W evidence first.
- The vendor-app HCI analysis (E5 capture is shipped as a runbook step;
  the analysis belongs to the follow-up spec).