# Spec 024 — Contract: the fault-code sensor and the latch semantics

Status: **ACTIVE** — this contract carries the owner's 2026-10-12
latch proof (research 024 §1) into force. It EXTENDS
[`../023-fault-exposure/contracts/fault-exposure-contract.md`](../023-fault-exposure/contracts/fault-exposure-contract.md)
(which stays active and binding for the binary surface) with: the
latch semantics (§2), the code-sensor shape and state rule (§3), the
supersession of that contract's §5 "no second entity" clause (§4),
the freshness ruling (§5) and the timing prohibition (§6). Where
this contract is silent, contract 023 binds.

---

## 1. The pin (carried forward, plus the latch)

- **The fault word is pushed register 2074**, in the 2046 block —
  pinned by the owner's 023 E03 diff-revert, RE-CONFIRMED by the
  2026-10-12 follow-up run (research 024 §1.1: 256 while the panel
  displayed E03, a second independent observation).
- **The latch [P owner, 2026-10-12]:** the fault word is a LATCHED
  memory. A fault sets it; the value persists through the machine's
  logical off and through BLE link restarts (fresh bursts re-serve
  it); removing the unit's physical power supply clears it to 0.
  The power-up memory reset is the only clear PROVEN.
- **The wire rule is unchanged:** `2074 == 0` → clear; `2074 != 0` →
  the unit reports a fault code. The SEMANTICS of the non-zero half
  are: **the unit memorized a fault since the last power-up — NOT
  necessarily an active fault.** Every user-facing sentence about
  the fault surface must respect this (§2).

## 2. The latch semantics (what any fault surface may claim)

- A fault entity may claim: "the unit reports a fault code" /
  "the unit reports no fault". It must NEVER be documented or
  worded as "an active fault is in progress" — the register cannot
  prove that.
- The documented remedy for a stale latched code: check the panel;
  if the machine is fault-free, cut the unit's power supply to
  clear the memory. This is the ONLY clear this contract records.
- The entities' honesty is unchanged in kind: the tile reports the
  machine's memory faithfully. "The entity does not lie" — a
  non-zero word on a healthy machine is the machine's own annunciation.

## 3. The code sensor (exactly one per push entry, in ADDITION to the binary)

- **Platform:** `sensor`, a literal entity in `sensor.py`'s push
  branch (the `push_stream` literal precedent — no registry, no
  RegDef, no placeholder: a second literal entity can only appear
  via a new spec with new evidence).
- **Name:** `Fault code` (renders `<device> Fault code`; entity_id
  `sensor.<device>_fault_code` via `has_entity_name`).
- **Key / unique_id:** `pushed_fault_code` / `<ADDRESS>_pushed_fault_code`
  — plane-prefixed and DISJOINT from the classic plane's `fault_code`
  key (reg 47, the 023 data-model §6 disjointness rule; the classic
  RegDef and its classic entity stay untouched and unreachable —
  law L1).
- **Classification:** NO device_class (a free-form string state; an
  `enum` class would demand a closed options list that the
  open-ended unknown-code family violates), NO entity_category
  (operational display signal), NO state_class, NO unit.
  **Icon:** `mdi:alert-circle` (the classic `fault_code` RegDef's
  icon — the repo's own fault icon).
- **The state rule (single source of truth
  `registers.fault_code_state`):**
  - `2074 == 0` → `"No fault"` (the fault memory is empty since the
    last power-up — a decoded truth, research 024 §4);
  - `2074 != 0`, raw in `PUSHED_FAULT_CODE_MAP` → the panel code
    (today: 256 → `"E03"`);
  - `2074 != 0`, raw unmapped → `"unknown code (raw N)"` (the
    contract 022 §3 honesty rule, carried into the STATE);
  - register absent → `unknown` (None) — never a guessed "No fault";
  - stale/silent stream → `unavailable` with every other entity
    (the shared freshness gate; §5).
- **Attributes:** the SAME evidence set as the Fault binary
  (contract 023 §2): `register` (2074), `raw`, `hex`, `set_bits`
  (arithmetic only — NO bit names, forever), `code` when the raw
  maps, `code_label` when an app label is recorded. The banking
  protocol (quickstart 023 §4) works from either entity.
- **The code map is untouched:** `PUSHED_FAULT_CODE_MAP = {256:
  "E03"}` — one row per RECORDED live observation; extending it
  needs one observation + one row (contract 023 §6, unchanged).
  Guessed tables and bit-to-flag-name mappings remain FORBIDDEN in
  entity code.

## 4. The supersession (contract 023 §5, "no second entity for the code")

Contract 023 §5 recorded: "No second entity for the code… A
code-history diagnostic sensor is a FUTURE spec's option, only if
the owner asks for it." **The owner asked (2026-10-12: "the error
code should appear instead of 'Problematique'"), and the ask is a
DISPLAY need the binary cannot serve:** a `problem` binary_sensor
renders its state through HA's translation layer ("Problematique"/
"OK"), so the code cannot ride the state, and attributes are
invisible on dashboard tiles. The one-quantity-one-entity principle
(contract 013 §4.1) is served by keeping ONE SOURCE (register 2074,
one decode discipline in `registers.py`) behind TWO entity faces
with disjoint purposes: the binary — "IS a fault on record?"
(automations); the sensor — "WHICH fault?" (display). This
supersedes that clause of contract 023 §5. All its other rules
(no writes, no classic entity, no gate, honesty limits) remain
in force for BOTH entities.

## 5. The freshness ruling (no fault-specific gate — a deliberate NON-change)

The fault entities participate in the SHARED cycle freshness gate
(contract 013 §6.1) and in NOTHING fault-specific:

- The gate tests the STREAM's recency (a fresh 2046-block capture),
  never VALUE movement. A latched word rides fresh blocks forever
  and passes correctly — no "value must have changed" rule exists
  anywhere in this integration, and none may be added for the fault
  (research 024 §6: a latched fault is by definition a value that
  does not move; an age-on-value rule would wrongly void it).
- The machine logically OFF keeps answering the trigger (proven
  during the latch run — research 024 §1.5): a stopped machine does
  not look stale.
- A SILENT module (supply cut, out of range, the vendor app holding
  the link) fails the cycle → `unavailable` for the fault entities
  with everything else: while deaf we cannot know the fault state,
  and a guessed clear is forbidden. The power-cut clear passes
  through this world in order: silence → unavailable → stream
  returns → "No fault".
- **Standing rule:** no future spec may add a fault-specific
  freshness, staleness or age treatment without superseding this
  section with live evidence.

## 6. The timing prohibition (no fault dating from the store)

The store's `first_seen`/`last_seen` are block-level,
connect-scoped bookkeeping (the first/latest push of the block START
in the CURRENT BLE session — contract 013 §3.1). They date our
LISTENING WINDOW, never the fault. Therefore:

- No "fault since X" / "fault age" attribute on either fault entity
  (it would lie in both directions — research 024 §5.2).
- No latch-derived boolean attribute (the latch is design behavior,
  always true — documentation, not data).
- The RECORDER's state history is the honest timeline (what HA
  observed and when). The unit provides no fault-timestamp register
  [V]; none may be invented.

## 7. Non-goals / standing rules (composed with contract 023 §5)

- **No fault WRITE path, ever** — 2074 is in NO allowlist; the funnel
  refuses it forever. Composed: **no "clear fault" service, button
  or option may ever exist** — clearing a latched fault is a
  PHYSICAL act on the machine (cut the supply), never an
  integration feature. The write plane stays byte-identical.
- **No classic-mode fault code entity** (register 47 unreachable —
  law L1; the classic `fault_code` RegDef stays untouched).
- **No portal, gate, option, checkbox, service or confirmation** for
  the sensor — read-only decoding, active by default (the 022 world;
  the push options schema stays exactly `poll_interval` +
  `temperature_unit`).
- **No bit names in code** (contract 023 §3, forever).
- **No timing attributes** (§6).
- **The binary_sensor.py file stays byte-identical** — the sensor is
  an ADDITION, not a replacement (spec 024 FR-5).
- **The honesty limits stand:** absent block → unknown; silent
  stream → unavailable; a fault that never moves 2074 is INVISIBLE
  to both entities (the entity is exactly as truthful as the pushed
  plane).

## 8. The extension protocol (unchanged, referenced)

Each new proven code row: the panel's displayed code + the 2074 raw
(the attributes of EITHER entity, or the WARNING hex, or the probe
dump), handed to the repo record; a one-row edit to
`PUSHED_FAULT_CODE_MAP`. The dry-run flow trip (E03) is the only
proven-safe provocation; everything else is recorded opportunistically
(quickstart 023 §4, referenced by quickstart 024 §4). The APK's
`assets/faults/faults_*.json` remains the optional static lead for
the vendor's own table.