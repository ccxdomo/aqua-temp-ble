# Contract 014 — the query cycle (normative)

Status: **PLANNED** — normative for everything spec 014 ships. Cited as
`contract 014 §N` in code comments. Inheritance: contracts 008-A/B
(frame grammar, reassembly, matching), 009 §3/§5 (census formats,
request/allowlist semantics), 010 (characteristic resolution), 011
(probe/pairing), 012 (log visibility, build marker), 013 §2/§3/§6/§7
(harvest logging, store, freshness, integrity) stay binding and
byte-identical except where this file explicitly amends them — only
§1 (the zero-TX invariant) and §4 (the failure literal) amend.

## 1. The zero-TX amendment (amends contract 013 §1) [P0]

1.1 The push-mode **poll cycle** transmits exactly **ONE frame per
cycle** — the vendor's own base read `63030007002d3c54` (FC03, start 7,
qty 45, CRC-pinned in the modbus self-test), sent as a single
write-without-response on the resolved write characteristic (app
parity, research 010 §2.3), with no re-sends.

1.2 Rationale (research 014 §0.2/§5): the unit pushes NOTHING
spontaneously — a request-free window is silent — and it ANSWERS any
valid read with its parameter-dataset block burst. The read plane must
request, then listen. The trigger frame is chosen because it is the
frame whose answer was observed six times [P]; it was already in the
standing read allowlist (research 009 §8) — this contract adds NO new
frame of any shape.

1.3 The connection test's push-harvest stage KEEPS its zero TX
(contract 013 §1.1 unchanged): inside the flow, the earlier stages
(first read, probe) are the trigger; the harvest only listens. The
store and the harvest API remain receive-only (contract 013 §1.3).

1.4 The write allowlist is untouched: no register outside
{8, 13, 14} (+19-21 behind `WRITE_MODE_SETPOINT_TOO`) is ever written;
the high blocks 1001-2090 are never written (spec 003 §6). The trigger
is a READ frame; nothing in this spec can change any unit state.

1.5 The acknowledged-write variant (`force_response=True`) and the
re-send bounds (`resend_every=2.0, resend_max=3`) are the named
fallback levers for a trigger that proves flaky in the long run — a
one-line change each, evidence-gated, never an option (research 014
§7 design detail verdicts).

## 2. The trigger step [P0]

2.1 Per push-mode cycle, in the io lock, after connect:
ONE `read_registers(BASE_BLOCK_START, BASE_BLOCK_COUNT)` call —
single-shot, the 8.0 s `REQUEST_TIMEOUT` window, no re-sends.

2.2 `AquaTempTimeoutError` is the EXPECTED outcome on this unit: caught,
logged at INFO as `push cycle: trigger read timed out as expected
(census: <exception message>)` — the census (frames/bytes/blocks/first/
last) rides in the exception message (contract 009 §3 format,
unchanged). Never an UpdateFailed by itself.

2.3 Any other `AquaTempError` (link lost, write failure): disconnect +
`UpdateFailed("trigger read failed: <err>")` — a transport failure,
never conflated with stream silence (spec 014 AC-3).

2.4 The store fills during the trigger window on the notify path —
unchanged machinery (contract 013 §3.1): every CRC-valid block frame,
idle or in-flight, feeds the store regardless of request matching.

## 3. The listen step [P0]

3.1 After the trigger: `harvest_pushes({PUSH_TEMP_BLOCK_START},
PUSH_HARVEST_TIMEOUT)` — the presence-based, zero-TX listen of
contract 013 §1.1, on the already-subscribed pipe. It returns instantly
when the T-block is in the store (the typical case: the burst landed
during the trigger window); otherwise it waits up to 30 s.

3.2 Cycle-time bound: 8 s (trigger) + up to 30 s (listen) worst case;
typical cycles are trigger-window-only. The io lock serializes cycles;
the poll interval does not lower-bound the cycle time (documented,
spec 014 §5).

3.3 The freshness gate is contract 013 §6.1, UNCHANGED: a store entry
is used only while `now - last_seen ≤ max(2 × poll interval, 60 s)`.
A stale-in-connection entry that passes the presence-based harvest is
caught here and fails the cycle (§4.2) — the same defensive stance as
013.

## 4. Failure semantics (amends contract 013 §6.2's ladder) [P0]

4.1 The push-mode ladder is now: connect → **trigger** → listen →
freshness → fail honestly. Push-mode failures still never fall back to
classic-mode reads, never re-trigger mid-cycle, never re-send: one
trigger per cycle, and the recovery is the next cycle's fresh connect.

4.2 The stream-failure literal is **`push stream silent after
trigger`** (was `push stream silent` — the 013 test suite re-pins it,
a cited deviation). Semantics: the proven trigger did not wake the
unit within trigger + 30 s — a strictly stronger verdict than 0.9.0's
listen-only silence; the two must never be conflated in logs or tests.

4.3 The failure disconnects and recycles the link (unchanged) — the
next cycle starts a fresh store and a fresh trigger (U-3'/U-7's
evidence accumulates in the per-cycle INFO lines of §2.2).

## 5. The revival path [P1]

5.1 If the trigger read COMPLETES (an FC03 answer or a start-7 block
frame — never observed; the post-pairing/E5 world): log ONCE per HA
session at WARNING — `push mode: trigger read answered — the request
path revived; using base data (mode stays push; a future spec
re-evaluates)` — and build the snapshot with
`AquaTempData.from_merged(regs, captures, confirmed)`.

5.2 Merge rules: typed fields from the base answer (`from_regs`
semantics — contract 013 §4.2: base wins for shared quantities);
`registers = {**base_regs, **confirmed_t_block_raws}` (windows disjoint
by construction [P]); pushed raws withheld unless
`push_temp_confirmed` (contract 013 §4.3 unchanged); mode stays
`"push"`; counts/timestamps from the store. No extra harvest in this
branch (the classical world must not wait 30 s for pushes).

5.3 The mode re-evaluation on a revival is a FUTURE spec's decision
(contract 013 §4.2 honored); this contract only makes the revival
observable and its data usable.

## 6. Untouched surfaces (restated, binding)

The connection test (stages, frames, messages, success criteria,
`mode: "push"` entry field), the client (store/harvest/census/probe),
`registers.py` (mapping, gate, integrity), `sensor.py`, the classic
mode cycle, and every 008-012 log format are byte-identical. The mode
value stays `"push"`; the confirm_push/options keys keep their ids
(their DESCRIPTION text is updated for honesty — spec 014 FR-4; keys
are not added or removed).

## 7. Logging levels

7.1 NEW line, INFO, per push-mode cycle (the expected world):
`push cycle: trigger read timed out as expected (census: …)` — the
per-cycle burst census (latency/composition evidence, U-7) at
non-noisy volume (one line per ~30 s cycle).

7.2 NEW line, WARNING, once per HA session (the revival — decisive
evidence, contract 012 §2's panel rule): §5.1's literal.

7.3 The failure `UpdateFailed` surfaces through HA's standard retry
UI (no new log format); the FR-4 string updates are content edits of
existing translation keys, no new keys.

7.4 Everything else keeps its shipped level (contract 012 §2 /
contract 013 §2 binding).