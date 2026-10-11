# Spec 013 — Pushed-blocks coverage: read the unit from its own push stream

Status: **PLANNED** — built from `research.md` (the pushed-block
cartography, the T-window/H-window/R-window mapping, the offset-question
verdict, the harvest feasibility trace) and `data-model.md` (the
register → quantity mapping). Scope guard: this spec adds a
**listen-only harvest read plane** and the entity surface it honestly
supports. It sends **no new frames of any kind** (FR-2: the harvest
sends nothing at all), changes no protocol bytes, no request-path
behaviour of specs 008-012 (the first-read, probe and census stay
byte-identical), no write allowlist, no base-block mapping. The verdict
of spec 009 §9 ("push-only = Track B, rejected as replacement") is
**revisited with the new evidence** — the `att-accepted-silent`
production verdict plus the T-window mapping — and Track B is promoted
to the primary read plane for units whose request path is silent
(research 013 §0.5).

## 0. Why this spec exists (one paragraph)

Three production runs prove the unit ignores our byte-perfect,
att-accepted base read (research 009 §0.2, spec 011 E-1/E-3, the 0.8.0
`att-accepted-silent` verdict) while simultaneously **pushing four
CRC-valid 45-register blocks** (1001/1046/2001/2046) at ~2 frames/s on
the notify characteristic we already subscribe to. Research 013 maps
those pushes: block 2046-2090 is the T-family live-temperature window
(2046 = the T01 sentinel anchor [P-grade], 2047 = live-proven 14.0 →
14.5 [P]) — i.e. **the inlet/outlet water temperatures the owner wants
are already in the air, unasked**. The base block's control data
(power/mode/setpoint/fault/firmware) has no identified pushed home
[P/V], so control stays blocked — but monitoring does not have to wait
for the request-path mystery (E5) to be solved. This spec harvests the
pushes (zero TX — the firmware's application-layer gate has nothing to
drop), captures the never-decoded block 2001-2045 (the biggest
remaining evidence gap, FR-1), and exposes the temperature entities
behind a one-run calibration gate (FR-5). The integration stops failing
`no_response` against a unit that is, in fact, talking to us
constantly.

## 1. User stories

- **US-1** As the pool owner, I want the connection test to SUCCEED
  when the unit is demonstrably communicating — pushing CRC-valid
  blocks — even though it never answers requests, so the integration
  stops dead-ending at `no_response` on a unit that talks constantly.
- **US-2** As the pool owner, I want the inlet and outlet water
  temperatures (and ambient, coil, exhaust) as entities in Home
  Assistant, sourced from the push stream, once I have confirmed
  with one side-by-side comparison that the mapped registers are the
  right ones — so no unconfirmed register is ever displayed under a
  certain name.
- **US-3** As the pool owner, I want the next connection test's log to
  contain the complete hex of the first push of each distinct block
  start — visible in the HA log panel (WARNING, per spec 012's lesson)
  — so one copy-paste delivers the never-captured block 2001-2045
  and fresh copies of all four blocks.
- **US-4** As the developer, I want the harvest to be pure-listening
  (zero frames transmitted) — so the H-APP-GATE silence cannot apply:
  nothing is requested of the unit that it could refuse.
- **US-5** As the developer, I want the coordinator in push mode to
  never send the doomed 8-second base read per poll cycle — so updates
  are fast (push latency ≤ ~4 s observed) and the unit is not spammed
  with requests it ignores.
- **US-6** As the integrator, I want the base plane kept intact
  (request path, allowlist, base mapping) — so any unit whose request
  path works (possibly other users' units, possibly this one after
  E5/pairing evidence) keeps today's exact behaviour, and the push
  plane is purely additive.

## 2. Functional requirements

### FR-1 — Full-hex push capture, operator-visible [P0]

- The client keeps a per-connect **pushed-block store**: for every
  CRC-valid block frame received (idle or in-flight), decode the 45
  registers (`parse_block_response`, start echoed by the frame) and
  store `{start: {reg: raw}}` plus first/last-seen timestamps and
  per-start counts. Nothing is discarded unread anymore (research
  §5.2: today the 90 data bytes are counted, then thrown away).
- The **first** block push of each distinct start per connect logs the
  complete 99-byte hex at **WARNING** (spec 012's operator-visibility
  lesson: the panel keeps WARNING+ only; ≤ 4 lines per connect —
  bounded):
  `block push captured: start=%d qty=%d len=%d crc=OK hex=%s`.
  Repeat pushes keep the existing INFO census lines byte-identical
  (012-pinned formats untouched).
- The idle-window summary line moves INFO → **WARNING** (level change
  only; message byte-identical) — it is the push-onset evidence (E2)
  and is emitted once per connect.
- The store and the hex lines survive for the whole connection
  (reset per connect, like the census state).

### FR-2 — Listen-only harvest API [P0]

- New client method `async harvest_pushes(wanted_starts, timeout)`:
  with the notify characteristic already subscribed, **sends ZERO
  frames** and returns when every start in `wanted_starts` has been
  seen (CRC-valid) or `timeout` elapses. Returns the collected store
  entries + timestamps. Never raises on partial results (the caller
  decides); asserts no-write in tests (fake client: write char
  receives nothing).
- The harvest reuses the existing reassembler machinery (boundary
  loop + CRC + block discriminator) so BOTH fragmentation regimes
  work (single 99-byte notification [P observed]; 5×20-byte chunks
  [P vector] — the idle path today loses fragmented frames;
  the harvest path must not: test with the chunked vector).
- Default `wanted_starts = {1001, 1046, 2001, 2046}` (the four
  observed starts; the app's other two — 1091/1136 — are appended
  if ever seen [V]).

### FR-3 — Connection test: harvest fallback, push-mode success [P0]

- Stage order (existing stages untouched, byte-identical):
  `connect → first-read (FC03, re-send ×4) → security probe →
  **push-harvest (NEW)** → fail`.
- The harvest stage runs ONLY when the first read timed out AND the
  probe returned None (the att-accepted-silent / no-answer world —
  exactly the 0.8.0 production trace). Budget: within the existing
  70 s test budget (connect ≈ 5 s + 8 + 8 leaves ~49 s; harvest
  timeout 30 s — observed: all four starts within ~16 s,
  research §5.4).
- **Success criterion:** ≥ 1 CRC-valid push of the T-family block
  (start 2046). Then the test SUCCEEDS with mode `push`; the success
  summary (WARNING, operator-visible) lists the captured starts, the
  per-start counts and the T-window values
  (`T-window raws: 2046=…, 2047=…, …`). If pushes arrived but no
  2046 within the window, keep listening up to the timeout; if still
  nothing, fail with the EXISTING `no_response` key + census message
  (no new failure UI keys).
- The created config entry records `mode: "push"` (data field). A
  unit whose FC03 answers never enters the harvest stage (mode stays
  classic — today's behaviour, US-6).
- The confirm step's description gains a push-mode note (additive
  translation string; existing keys untouched).

### FR-4 — Coordinator push mode [P1]

- For entries with `mode == "push"`: the poll cycle sends **no
  request at all** — it reads the client's pushed-block store and
  builds the snapshot from it (freshness rule: a store entry is used
  only if its last-seen timestamp is within `max(2 × poll_interval,
  60 s)`; stale ⇒ `UpdateFailed("push stream silent")` and the
  connection is recycled on the next cycle — research U-3's defensive
  stance).
- Classic entries (base read answers): byte-identical today's
  behaviour. The push store is maintained in BOTH modes (it costs
  nothing and documents the stream).
- If a base-plane answer ever arrives in push mode (e.g. after future
  pairing evidence), the base values override pushed values for
  shared keys (merge policy, contracts §4) — and the mode may be
  re-evaluated by a future spec (NOT this one).

### FR-5 — Pushed temperature mapping + calibration gate [P1]

- `registers.py` gains the pushed-plane constants of
  `data-model.md §6` (PUSH_TEMP_BLOCK_START, PUSHED_TEMPS,
  PUSH_TEMP_CONFIRMED=False) — single source of truth; per-member
  one-line editable (a permuted calibration outcome is one edit).
- Entity creation rule (honesty gate, spec 003 discipline):
  - base-pinned keys (`inlet_water_temp`, `outlet_water_temp`,
    `ambient_temp`) exist as today; in push mode their values come
    from the pushed mirrors (2047/2048/2050) — **only when
    `PUSH_TEMP_CONFIRMED` is true**; until then, in push mode they
    show `unknown` (never unconfirmed data under a certain name).
  - push-only keys (`suction_temp`, `coil_temp`, `exhaust_temp`) are
    created only when `PUSH_TEMP_CONFIRMED` is true.
- `PUSH_TEMP_CONFIRMED` is set by the calibration run
  (quickstart §2): the owner photographs the app's T-family display,
  kills the app/BT, runs the connection test within ~2 min, compares
  the T-window values shown in the success summary, and flips the
  option in the confirm step ("values matched"). The option is
  stored on the entry; a mismatch is reported and pins nothing.
- Sentinels/plausibility: unchanged spec-003 decode rules
  (2046 → `unknown` on this unit — the honest state).

### FR-6 — Push-stream diagnostic [P2]

- One diagnostic sensor per push-mode entry: `push_stream`
  (attributes: per-start counts, first/last push age, mode). State:
  `on` while the store is fresh, `off`/`unavailable` when stale.
  Purpose: U-3 (long-run behavior) becomes observable by the owner
  without any terminal.

### FR-7 — Integrity check extension [P2]

- `check_registry_integrity` accepts pushed-plane addresses
  (2001-2090) in a **separate** pushed registry; duplicates within a
  plane still raise; cross-plane collisions per key are allowed only
  for the documented mirror pairs (data-model §6); a pushed address
  outside 2001-2090 raises (same loud-failure spirit as spec 003).

## 3. Non-functional requirements

- **NFR-1** Touched: `aqua_client.py` (FR-1/2), `config_flow.py`
  (FR-3), `coordinator.py` (FR-4/6), `registers.py` (FR-5/7), the
  sensor platform (FR-5/6), translations (FR-3 note), tests, this kit.
  Untouched: `modbus.py` (vectors already ship), `const.py` timeouts
  (harvest timeout is a new constant, existing ones unchanged),
  entities' existing keys/icons, base-plane mapping values.
- **NFR-2** No new frame ever transmitted by anything this spec adds
  (FR-2's zero-TX invariant; grep gate in tasks). The 008-012 request
  path, census formats, probe, pairing ladder: byte-identical.
- **NFR-3** Version bump 0.9.0 + build-marker line (012 FR-2 pattern)
  so the operator's panel identifies the running build.
- **NFR-4** All existing tests stay green (012-pinned message formats
  untouched; the new WARNING hex lines are additive lines, not
  changes); the modbus self-test stays byte-identical.
- **NFR-5** Standalone mode keeps working (no HA imports in client/
  registers; harvest API importable by tools).
- **NFR-6** Durable artifacts in English; every new comment cites
  `research 013 §…` / `contract 013 §…` / `data-model 013 §…`.

## 4. Acceptance criteria

- **AC-1** (FR-1): a fake-notify run feeding the four pinned frames
  (1001, 1046, 2001-mock, 2046×2) produces exactly 4 WARNING
  `block push captured:` lines (one per distinct start, full 198-char
  hex), INFO census lines for repeats, one WARNING idle summary; the
  store decodes to the exact register tables (spot-assert 1021=1050,
  2047=140→145, 2046=32765); a fragmented feed (5×20-byte chunks of a
  block frame while idle) also lands in the store (FR-2 invariant).
- **AC-2** (FR-2): `harvest_pushes({2046}, 30)` with a scripted
  notification at t+5 completes at t+5 and the fake write
  characteristic received ZERO writes; with no notifications it
  returns partial results at the timeout without raising.
- **AC-3** (FR-3): flow test — first read times out, probe returns
  None, harvest sees a 2046 push ⇒ test succeeds, entry mode `push`,
  success WARNING summary lists starts + T-window raws; harvest sees
  nothing ⇒ existing `no_response` key + census message
  (byte-identical). Probe answered ⇒ no harvest stage runs.
- **AC-4** (FR-4): push-mode coordinator cycle performs zero
  `read_registers` calls (mock asserts), builds the snapshot from the
  store, honors the freshness rule (faked clock), raises UpdateFailed
  on a stale store. Classic mode: byte-identical single-read cycle
  (existing tests re-run green).
- **AC-5** (FR-5): `PUSH_TEMP_CONFIRMED=False` ⇒ push-only keys absent,
  base keys `unknown` in push mode; `=True` ⇒ keys present, values from
  pushes (2047 → 14.5 from the pinned vector); the sentinel reg 2046
  decodes `unknown`; a permuted mapping edit (swap 2047/2048) changes
  the sources accordingly (single-source-of-truth test).
- **AC-6** (FR-7): duplicate pushed address raises; pushed address
  outside 2001-2090 raises; shipped registries pass (test).
- **AC-7** `pytest tests/` fully green; self-test unchanged; git
  status clean outside NFR-1's list; version 0.9.0 in manifest and
  build marker.

## 5. Risks & guards

| Risk | Guard |
|---|---|
| Push stream stops after minutes/hours (U-3, all observations ≤ 21 s) | FR-4 freshness rule + recycle; FR-6 makes it observable; harvest never trusts a single window |
| T-window member order is permuted (2050 is inlet, not ambient) | FR-5 calibration gate: nothing confirmed without the side-by-side run; one-line fix in PUSHED_TEMPS; sentinel anchor (2046=T01) is the invariant |
| Full-hex WARNING lines leak "too much" into the panel | ≤ 4 lines/connect, first-per-start only (FR-1); repeats stay INFO census |
| Push mode misleads on units whose requests DO work | FR-3: harvest runs only after read+probe both failed; classic units never enter it |
| The harvested 2001 block reveals base-control homes and tempts scope creep | Out of scope §6: mapping/using 2001 content is the FOLLOW-UP spec's decision, written from the FR-1 capture |
| Stale pushed data shown as current | freshness rule (FR-4) + per-push timestamps; UpdateFailed on stale |
| Duplicate entities (base reg 26 + push reg 2047) | one key per quantity, planes merge per contracts §4 — never two entities for one quantity |

## 6. Out of scope (explicit)

- Any transmitted frame, of any shape — the harvest sends nothing;
  block queries stay probe-only (spec 008/009 allowlist untouched).
- Write-path testing (the "does the app-layer gate eat writes too"
  question) — evidence-gated, separate spec, after E5/pairing data.
- Deciding or consuming block 2001-2045's content — this spec only
  CAPTURES it (FR-1); the follow-up spec maps it.
- The O/S/D/P families' entity exposure — homes unknown (maybe 2001);
  spec 003 policy unchanged.
- Changing the request path, census grammar, probe/pairing ladder, or
  any 001-012 contract (this spec only ADDS: harvest + store + levels
  of two lines + entity gating).
- Climate/control in push mode — no honest control data exists in
  the pushes [P/V]; the entity is simply not created, and the spec
  says so in the user-facing note.