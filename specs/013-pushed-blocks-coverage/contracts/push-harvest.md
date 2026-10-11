# Contract 013 — push-harvest read plane (normative)

Status: **PLANNED** — normative for everything spec 013 ships. Cited as
`contract 013 §N` in code comments. Inheritance: contracts 008-A/B
(frame grammar, reassembly, matching) and 009 §3/§5 (census formats,
request semantics) stay binding and byte-identical; this contract adds
the pushed-plane rules on top. Where they interact, the older contract
wins unless this file explicitly amends a level (only §2 does).

## 1. The zero-TX invariant [P0]

1.1 The harvest path (`harvest_pushes`, the store, the FR-3 stage, the
push-mode coordinator cycle) transmits **no frame of any kind** — no
FC03 read, no query, no write, no ATT operation beyond the CCCD
subscription that `connect()` already performs. The push stream is
received, never requested.

1.2 Rationale (research 013 §0.4, §5.5): the request path is dead under
H-APP-GATE (`att-accepted-silent` [P]); a pure listener gives the
firmware's application layer nothing to drop. Nothing this spec adds
can be refused.

1.3 Enforcement: `harvest_pushes` performs no write-characteristic
call; the test gate asserts the fake write char received zero writes
(AC-2). The 008/009 allowlist discipline (research 009 §8) remains
the authority for every frame the integration may EVER send — this
spec adds none.

## 2. Logging levels (the only amendment to an older contract)

2.1 NEW line, WARNING, ≤ 4 per connect (first push per distinct start):
`block push captured: start=%d qty=%d len=%d crc=OK hex=%s` (the full
99-byte hex). Rationale: the operator's panel keeps WARNING+ only
(contract 012 §2); this line IS the 2001-2045 capture mechanism (U-1)
and the calibration data source.

2.2 The idle-window summary line (contract 009 §3) moves INFO →
**WARNING**; message byte-identical. All other census formats of
008/009 stay at their shipped levels and byte-exact — 012's grep
stability is preserved.

2.3 The harvest stage's own evidence lines (entry, success summary
with T-window raws, timeout) are WARNING (decisive-once grammar,
contract 012 §2 pattern). Repeat-push census lines stay INFO (16-per-
window verbosity must not become WARNING noise — the 008/009
discipline).

## 3. The pushed-block store

3.1 Per connect, the client maintains `{start: PushCapture}` for every
CRC-valid block frame (idle or in-flight), each capture holding: the
latest decoded `{reg: raw}` (45 signed int16, BE — `parse_block_response`
with the start/qty echoed BY THE FRAME), a first/last-seen timestamp
and a count. Reset on every connect (census-state precedent).

3.2 The store never feeds anything until its consumer applies the
decode rules (sentinels `{-9990, 32765}` → `unknown`, plausibility
window −450…+1500 — contract 003 decode rules, unchanged) and the
confirmation gate (§5).

3.3 Latest-frame-wins per register window: a re-push with one changed
register (2047: 140 → 145 [P]) replaces the window atomically. Partial
windows (CRC-invalid or fragmented-incomplete) never enter the store.

## 4. Merge policy (two planes, one truth per quantity)

4.1 One entity key per physical quantity — never two entities for
base reg 26 and push reg 2047 (they are the same water temperature).

4.2 A fresh base-plane answer overrides pushed values for shared keys
(the base block is the richer, app-canonical status dataset). In
practice: classic mode never reads the push plane for values; push
mode reads pushes only. If a base answer ever arrives in push mode, the
next spec decides the mode; this spec treats it as an anomaly to log
(WARNING) and otherwise ignore.

4.3 In push mode, a key's value comes from its pushed register ONLY
when `PUSH_TEMP_CONFIRMED` is true; otherwise the key decodes
`unknown`. No unconfirmed register is ever displayed under a certain
name — the repo rule, restated as an invariant.

4.4 Control keys (power/mode/setpoint/fault/firmware) have NO pushed
home [P/V]; in push mode they are `None` by design, the climate entity
is not created, and no write is attempted. Optimistic state is
forbidden (spec 001 precedent).

## 5. Calibration gate (FR-5 procedure)

5.1 The confirmation is a human-verified side-by-side comparison
(quickstart §2): the app's T-family display (or home-screen
inlet/outlet) photographed, app killed / phone BT off, connection test
within ~2 min, the success summary's T-window raws compared.

5.2 Confirmation requires: (a) the T01 sentinel at 2046 (the anchor),
(b) the five live values within thermal drift of the photographed
display (±1.0 °C for a ≤ 2 min gap on an idle unit [A]), (c) the owner
checks "values matched". Any permutation/mismatch ⇒ NO flip; the
mapping table is corrected in `registers.py` first (one line), the
test re-run.

5.3 `PUSH_TEMP_CONFIRMED=False` is the shipped default; the flip is an
entry option (with restart — the calibration-switch precedent of
`WRITE_ECHO_EXPECTED`/`WRITE_MODE_SETPOINT_TOO`).

## 6. Freshness and failure semantics (push mode)

6.1 A store entry is fresh while `now - last_seen ≤ max(2 × poll
interval, 60 s)`. Stale store ⇒ `UpdateFailed("push stream silent")`;
the link is recycled on the next cycle (U-3's defensive stance — the
long-run cadence is unproven).

6.2 Push-mode failures never fall back to sending requests (§1); the
recovery ladder is: reconnect → re-harvest → fail honestly.

6.3 The `push_stream` diagnostic (FR-6) reports counts/ages/mode so
the owner sees stream health without a terminal.

## 7. Integrity (FR-7)

7.1 Pushed-plane entity addresses must lie in 2001-2090; duplicates
within the pushed registry raise at import; mirror pairs (base+push
for one key) are legal, cross-key collisions in one plane are not.

7.2 `modbus.parse_block_response` stays the ONE decoder for block
frames (no second parser in the client/coordinator — the 008 refactor
discipline).

## 8. Non-goals restated

No frames (§1); no 2001-2045 semantics decisions (capture only — the
follow-up spec's job); no write-path experiments; no O/S/D/P entity
work; no changes to any 001-012 behaviour beyond §2's levels and the
additive store/stage/entities.