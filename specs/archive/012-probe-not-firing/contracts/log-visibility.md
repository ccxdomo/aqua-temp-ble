# Contract 012 — operator-visible log grammar (the level amendment)

These rules bind every implementation of spec 012 and every follow-up
that touches the probe/pair path or the config-test log lines.
Companion contracts: 011 §3 (the probe/pair grammar — **this contract
amends its level column only**; where the two disagree on a LEVEL,
this contract wins; the message STRINGS are identical, and the 011
file remains as historically landed), 007 §3 (stage instrumentation —
unchanged).

Proof base: research 012 (all tags there). The core fact [P — research
012 §4, HA docs]: the operator's primary log surface is HA
Settings → System → Logs — the `system_log` ring buffer — which
"only stores the last errors and warnings". Records below WARNING
never appear there, at any logger configuration; per-logger levels
route to `home-assistant.log` (the downloadable full file) only.

## §1 The visibility rule (normative)

1. **Decisive-once evidence lines MUST be emitted at WARNING.** A
   line is decisive-once when it is the evidence a routing decision
   reads — emitted at most a handful of times per connection test and
   never in a high-volume stream. The operator must be able to read
   the outcome from the panel without downloading anything.
2. **High-volume diagnostics stay at INFO** (and DEBUG for raw
   bytes): census lines, idle-window summaries, per-connect
   enumeration and connect summaries, re-send cadence lines. Turning
   a 16-lines-per-window stream into WARNING noise is a contract
   violation (the 008/009 census discipline stands).
3. **Message strings are frozen** (grep stability): no level change
   may reword any line. The 007 failure-line formats and the 008/009
   census formats are byte-identical before and after this contract.

## §2 The level table (amending contract 011 §3)

**WARNING (operator-visible) — the evidence grammar:**

| Line (prefix) | Source (d860933 sites) | Was |
|---|---|---|
| `security probe: re-sending base read as acknowledged write (response=True) on <uuid> to <addr>` | aqua_client.py:2317 | INFO |
| `security probe: verdict <v>: <chain>` | :2337 | INFO |
| `security probe: verdict att-accepted-silent (ack ok, no Modbus answer within <t>s; census: <…>)` | :2342 | INFO |
| `security probe: acknowledged write answered by a Modbus exception frame (<chain>) — …` | :2354 | INFO |
| `security probe: acknowledged write answered — the Write Request woke the Modbus engine (…)` | :2365 | INFO |
| `security probe: pair stage exceeded the <N> s pairing budget (…)` | :2387 | WARNING (unchanged) |
| `pairing: failed: <exc chain>` (probe pair catch) | :2393 | INFO |
| `security probe: still silent after pairing (census: <…>)` | :2412 | INFO |
| `security probe: answered after pairing (<n> attempt of the window)` | :2416 | INFO |
| `pairing: attempting via rung A/B/C …` | :908 / :916 / :1011 | INFO |
| `pairing: bond state before/after: <paired\|unknown>` | :901 / :929 | INFO |
| `pairing: succeeded` / `pairing: skipped: already bonded` | :949/:943, :999/:993 | INFO |
| `pairing: failed: <chain>` (ladder) | :945, :995 | INFO |
| `pairing: unavailable: <reason>` | :972, :1032, :1039 | INFO |
| `connection test to <addr>: build <version>` (NEW, spec 012 FR-2) | config_flow.py (new) | — |

**INFO (file-only diagnostics — unchanged):** `block push observed…`,
`frame observed…`, `idle window before first request…`,
`block push observed while idle…` (first only), `GATT enumeration…`,
`using write characteristic…`, `last-resort…`, `connect summary…`,
`Connected to…`, `connecting to… up to N connect attempt(s)…`,
`first-read re-send n/3: …`.

**DEBUG (unchanged):** TX/RX frame lines, ff02 chunks, disconnect
notes, `connect with pairing requested (option pair_on_connect)`
(011 §3.5 — the production option path is untouched by this
contract).

**WARNING/ERROR (pre-existing, unchanged):** the test-level failure
lines of contract 007 §3.1 (`no response`, `failed`, `bad response`,
`device not found`, the budget line, `unexpected error`), the
connect-stage ERROR lines, `dropping non-Modbus notify`.

## §3 The build marker (normative)

1. Format (byte-frozen):
   `connection test to <address>: build <version>`
2. Level: WARNING. Frequency: exactly once per connection-test
   attempt, as the test's FIRST log line (before the connect stage).
3. `<version>` is read from `manifest.json` at module import, guarded
   (any failure ⇒ `"unknown"`); the marker must never break the flow
   and never lie (a drift test pins equality with the manifest).
4. Purpose: the panel always identifies WHICH build produced the lines
   below it. A missing or old-version marker on a supposedly updated
   install means the update never loaded (restart required) — that is
   the marker doing its job (research 012 §5).

## §4 Budget & routing (unchanged, restated)

The 70.0 s envelope, the stage order, the UI keys and the contract
011 §4 arithmetic are untouched. The att-accepted-silent verdict's
routing is contract 011 §6 row 3: E5 priority; no additional probing
frames; pairing is not the indicated fix.

## §5 Safety invariants

- This contract changes NO protocol bytes, NO timing constants and NO
  UI keys. Only levels and one added line.
- The success path emits no `security probe:`/`pairing:` line at any
  level; the build marker is its single new line.
- The poll/coordinator path and the `pair_on_connect` option path are
  out of this contract's reach (FR-7 of 011 stands).

## §6 What this contract does NOT decide

- The verdict of the operator's pending run (quickstart 012 §0/§1
  deliver it; the arithmetic predicts att-accepted-silent — research
  012 §3).
- Whether the evidence grammar of FUTURE experiments (E5 follow-ups)
  goes to the panel as WARNING or to the config-entry UI as
  placeholders — an evidence-gated decision for the next spec.
- The 011 leftovers (strings.json option description, coordinator
  option threading) — a separate landing, untouched here.