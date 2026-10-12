# Spec 014 — Query the high blocks: the request-triggered poll cycle

Status: **PLANNED** — built from `research.md` (the 0.9.0 production
trace: the unit pushes NOTHING spontaneously, ANSWERS every request with
its parameter-dataset block frames, and the spec-013 zero-TX push cycle
starves waiting for a stream that does not exist). Scope guard: this
spec changes **one production behavior** — the push-mode poll cycle
gains its trigger — and touches NOTHING else: the request path, census,
probe, pairing ladder, config-flow stages and frames, the write
allowlist, the store, the harvest API, the entity mapping and the
calibration gate are byte-identical to spec 013 (FR-5). The mode value
`"push"` is kept (the 2026-10-11 22:41 entry exists in the retry state
and recovers with the fixed cycle — no migration).

## 0. Why this spec exists (one paragraph)

The 0.9.0 production run (research 014 §0) proves three things at once:
the unit pushes no spontaneous stream (0 pushes in the idle window; 0
frames in the coordinator's 30 s zero-TX listen — the entry setup fails
`push stream silent` in a retry loop), it ANSWERS our requests with a
burst of its parameter-dataset block frames (the first 1001 capture
lands 5 s after the probe's acknowledged write; all four blocks are
captured by harvest time), and the T-window mapping is right (the
32765 sentinel anchor holds in a second independent capture). The
push-mode cycle that spec 013 built is therefore missing exactly one
thing: the REQUEST that the unit demonstrably answers. This spec adds
it: each push-mode poll cycle sends ONE vendor read frame — the base
read `63030007002d3c54`, the frame the unit has answered six times —
then listens with the harvest machinery spec 013 already proved, then
applies the unchanged freshness gate and entity mapping. The entry that
loops `push stream silent` today recovers by itself.

## 1. User stories

- **US-1** As the pool owner, I want the entry that failed with
  `push stream silent` to WORK after this fix — the poll cycle talks to
  the unit the way the unit demonstrably wants (one request, then its
  answer), instead of waiting silently for a stream that does not
  exist.
- **US-2** As the pool owner, I want the temperature entities in push
  mode to refresh at every poll cycle with the unit's own reported
  values (the T-window), behind the same one-run calibration gate as
  before — no unconfirmed register is ever displayed under a certain
  name.
- **US-3** As the pool owner, I want the UI text about push mode to
  tell the truth: the integration sends one read request per poll
  cycle (it does not "read without sending anything" — the 0.9.0
  wording is now false and must not survive an operator's reading).
- **US-4** As the developer, I want the poll cycle's failure semantics
  to distinguish the two worlds: silence after a LISTEN-ONLY window
  (0.9.0, impossible now) vs silence after a TRIGGER (the unit did not
  wake — a real, diagnosable condition with its own message).
- **US-5** As the developer, I want a request-path revival (the base
  read ever getting answered — the post-pairing/E5 world) to be
  OBSERVABLE in production: the cycle logs it once and uses the base
  data, so the future spec that re-evaluates the mode has its evidence.
- **US-6** As the integrator, I want everything else untouched: units
  whose request path works keep the classic cycle byte-identically; the
  flow, the store, the harvest stage, the allowlist and the 008-012
  log formats stay exactly as shipped (they produced the evidence this
  spec consumes).

## 2. Functional requirements

### FR-1 — The push-mode poll cycle: TRIGGER + LISTEN [P0]

`coordinator._async_update_push` becomes, in order (normative detail in
`contracts/query-cycle.md`):

1. **Connect** if needed (unchanged, including the `pair_on_connect`
   option threading).
2. **Trigger**: ONE `read_registers(BASE_BLOCK_START, BASE_BLOCK_COUNT)`
   call — single shot (no re-sends), inside the io lock, write type =
   the client's resolved default (without-response, app parity). On
   this unit the call raises `AquaTempTimeoutError` after the 8.0 s
   window: **caught**, logged at INFO with the census summary that
   rides in the exception message (the expected world — during the
   window the pushed-block store fills on the notify path, unchanged).
   Any OTHER `AquaTempError` (link lost, write failed): disconnect +
   `UpdateFailed("trigger read failed: <err>")` — an honest transport
   failure, never conflated with stream silence.
3. **Listen**: the existing `harvest_pushes({PUSH_TEMP_BLOCK_START},
   PUSH_HARVEST_TIMEOUT)` — returns instantly when the T-block is
   already in the store (the typical case: the burst arrived during the
   trigger window).
4. **Freshness gate**: unchanged (contract 013 §6.1 —
   `max(2 × poll_interval, 60 s)` on the T-block's `last_seen`).
5. **Snapshot**: fresh T-block → `AquaTempData.from_pushes` (unchanged
   mapping, unchanged `push_temp_confirmed` gate).
6. **Fail**: T-block absent or stale after trigger + listen →
   disconnect + `UpdateFailed("push stream silent after trigger")`
   (the new literal — FR-3); the next cycle's connect recycles the
   link (unchanged ladder stance).

### FR-2 — The revival path + `from_merged` [P1]

If the trigger read **completes** (an FC03 answer or a start-7 block
frame — never observed, the post-pairing/E5 world):

- Log ONCE per HA session at WARNING: the request path revived, a
  future spec re-evaluates the mode (the module-set pattern of
  `_logged_implausible`).
- Build `AquaTempData.from_merged(regs, captures, confirmed)`:
  typed fields (power/mode/setpoint/bounds/fault/firmware/binaries)
  from the base answer (`from_regs` semantics — contract 013 §4.2:
  the base plane wins for shared quantities); the `registers` dict =
  base regs merged with the confirmed T-block raws (the windows are
  disjoint: 7-51 vs 2046-2051 — the merge is collision-free by
  construction [P]); mode stays `"push"`; counts/timestamps from the
  store. The push-mode entity surface is unchanged (it reads pushed
  addresses — still fed); the base values are visible in the snapshot
  and the log line.
- No extra harvest in this branch (the trigger window already fed the
  store; the classical world must not wait 30 s for pushes that may
  never come).

### FR-3 — Failure-message semantics + the 013-suite re-pins [P0]

- The push-mode stream failure literal changes
  `push stream silent` → **`push stream silent after trigger`** — the
  new verdict means "the proven trigger did not wake the unit", a
  strictly stronger statement than 0.9.0's listen-only silence.
- `tests/test_coordinator_push_013.py` re-pins the literal (a cited
  deviation, the 013 idle-summary-level precedent) and **inverts its
  zero-TX assertion**: the 013 AC-4 vector "push-mode coordinator cycle
  performs zero `read_registers` calls" becomes "exactly ONE call per
  cycle with `(7, 45)`" — the whole point of this spec. Every other
  013 assertion stays green.

### FR-4 — UI / docs honesty [P1]

- The `confirm_push` step description (strings.json + translations
  en/fr) and the README push-mode section drop the false "reads the
  stream without sending anything" claim and state the truth: push mode
  sends one read request per poll cycle and reads the unit's block
  answer. Content update of existing keys — no new keys, no flow-logic
  change.

### FR-5 — Non-changes (the evidence discipline) [P0]

Byte-identical and untouched: `aqua_client.py` (store, harvest, census,
probe, re-send machinery), `config_flow.py` (stages, frames, harvest
stage, success criteria, confirm steps — the 22:41 run is the end-to-end
proof of the flow as shipped), `registers.py` (mapping, gate, integrity),
`sensor.py`, `const.py` (no new constants — the trigger reuses
`BASE_BLOCK_START/COUNT`, the listen reuses `PUSH_HARVEST_*`),
`modbus.py`; the classic-mode cycle; the 008-012 log formats. The write
allowlist is untouched: no register outside {8, 13, 14} (+19-21 behind
the switch) is ever written, and the high blocks are never written
(spec 003 §6).

### FR-6 — Version 0.10.0 [P2]

Manifest version bump (the per-spec pattern: 0.8.0 → 0.9.0 → 0.10.0);
the flow's build-marker line picks it up automatically (spec 012 FR-2).

## 3. Non-functional requirements

- **NFR-1** Touched files: `coordinator.py` (FR-1/2),
  `tests/test_coordinator_push_013.py` (FR-3 re-pins),
  `tests/test_coordinator_query_014.py` (new),
  `strings.json` + `translations/{en,fr}.json` + `README.md` (FR-4),
  `manifest.json` (FR-6), this kit. Nothing else — `git status` clean
  outside this list (AC-7).
- **NFR-2** All existing suites stay green except FR-3's two cited
  re-pins; the modbus self-test stays byte-identical.
- **NFR-3** Standalone discipline preserved (no HA imports in
  client/registers; `from_merged` lives on the dataclass, stdlib-only).
- **NFR-4** Durable artifacts in English; every new comment cites
  `research 014 §…` / `contract 014 §…`.
- **NFR-5** The trigger adds exactly one 8-byte read frame per poll
  cycle (~30 s default) — bounded, vendor-proven, read-only; no retry
  storm, no re-sends (research 014 §7).

## 4. Acceptance criteria

- **AC-1** (FR-1): fake client — `read_registers(7,45)` raises
  `AquaTempTimeoutError`, scripted notifications during the window feed
  the store with the pinned T-block vector ⇒ the cycle returns a
  `from_pushes` snapshot with the T-window values; `read_registers`
  called EXACTLY ONCE; no other write; NO UpdateFailed; the INFO
  expected-timeout line logged.
- **AC-2** (FR-1): trigger times out, no notifications, faked short
  harvest timeout ⇒ `UpdateFailed("push stream silent after trigger")`
  + disconnect called; trigger still exactly once.
- **AC-3** (FR-1): trigger raises `AquaTempConnectionError` ⇒
  `UpdateFailed("trigger read failed: …")` + disconnect; the stream
  literal NOT used.
- **AC-4** (FR-2): trigger returns base regs (pinned vector) ⇒
  `from_merged` snapshot: typed fields from the base answer; the
  confirmed T-block raws present in `registers`; unconfirmed ⇒ the
  pushed raws withheld (gate unchanged); the revival WARNING logged once
  per session (second cycle does not re-log); mode `"push"` preserved.
- **AC-5** (FR-3): the 013 suite green after the two cited re-pins
  (literal + inverted zero-TX assertion); a comment cites contract 014 §4.
- **AC-6** (FR-5): the classic-mode, flow-harvest, store, registers and
  census suites pass unmodified; the config-flow and client files are
  byte-identical (`git diff --stat` empty for them).
- **AC-7**: `pytest tests/` fully green; `python3
  custom_components/aqua_temp_ble/modbus.py` self-test green; manifest
  0.10.0; `git status` clean outside NFR-1's list.

## 5. Risks & guards

| Risk | Guard |
|---|---|
| The trigger stops waking the unit (firmware state, long-run flakiness) | the failure literal names it (FR-3); the `push_stream` diagnostic + per-cycle census lines accumulate the evidence; the re-send bounds and the acknowledged-write lever are named one-line fallbacks (research 014 §7) |
| A stale store entry (same connection) passes the presence-based harvest but fails freshness | the freshness gate fails the cycle honestly and recycles the link (contract 014 §4.2 — the same defensive stance as 013 §6.1) |
| Cycle time grows (8 s trigger + up to 30 s listen worst case) | the typical cycle is trigger-window-only (the harvest returns instantly); the coordinator serializes cycles anyway; the poll interval effectively lower-bounds nothing (documented in contract 014 §3) |
| The revival path is speculative (never observed) | it is [P]-shaped but [V]-triggered: log-once + merge are small, pure, test-pinned; the mode re-evaluation stays a future spec's decision (contract 013 §4.2 honored) |
| The 2001 hex paste (research 014 §8.3) tempts scope creep into mapping | out of scope §6: the paste is captured by quickstart §0; mapping 2001 is the FOLLOW-UP spec's decision |
| UI text drift (the false "without sending anything" claim) | FR-4's content update is pinned by the string files' review in tasks.md |

## 6. Out of scope (explicit)

- Any change to the connection test, its stages, frames or messages
  (the 22:41 run proves the flow end-to-end as shipped).
- Any new frame of any shape beyond the standing allowlist reads
  (research 009 §8); in particular NO query-block frame in production
  code (the probe-service experiment, quickstart §3, decides that
  upgrade).
- Mapping or consuming block 2001-2045's content (the follow-up spec,
  written from the quickstart §0 paste).
- The T-window member order / calibration flip (quickstart 013 §2
  unchanged).
- Control in push mode (no climate, no fault, no firmware entities —
  unchanged from spec 013 §6); the write allowlist is untouched.
- The mode re-evaluation on a revival (a future spec's decision —
  this spec only makes the revival observable, FR-2).