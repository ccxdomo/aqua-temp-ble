# Spec 024 — The fault code sensor + the latch documentation (register 2074's second face and its true semantics)

Date: 2026-10-12
Repo state: main @ 7ee702b, manifest 0.17.0 (the spec-023 build: the
Fault binary, the proven code map, the app-label record)
Status: **APPROVED FOR IMPLEMENTATION** — the evidence is the owner's
2026-10-12 follow-up run (research 024 §1): E03 re-provoked (2074 =
256, the home re-confirmed), the machine turned OFF while healthy
(2074 STAYED 256 — the fault word is LATCHED), the physical supply
cut (2074 → 0 — the latch cleared). Bound by
[`contracts/fault-code-sensor-contract.md`](contracts/fault-code-sensor-contract.md)
(which extends the active
[`../023-fault-exposure/contracts/fault-exposure-contract.md`](../023-fault-exposure/contracts/fault-exposure-contract.md)).

The one-paragraph story: the owner watched his new Fault entity
against a live E03 and discovered the second half of the truth — the
fault word **2074 is a latched memory**: it kept reading 256 while
the machine sat healthy and OFF (the entity faithfully showed the
machine's memory, not an active fault), and only cutting the unit's
power supply cleared it to 0. He also asked for the one thing the
binary cannot give: **the code itself on the tile** ("the error code
should appear instead of 'Problematique'") — a `problem`
binary_sensor renders localized on/off words through HA's
translation layer, so the code can never ride its state. This spec
therefore (a) ADDS one `sensor` — **"Fault code"**: `No fault` /
`E03` / `unknown code (raw N)` — the display face of the SAME
register (the binary stays, untouched, as the automation face), and
(b) documents the latch everywhere users read it. No freshness
exception, no timing attribute, no map change: the honesty rules all
stand.

---

## 1. Functional requirements

### FR-1 — One Fault code sensor, created automatically (the display face)

A push-mode entry gets exactly ONE new entity: a `sensor` named
**Fault code**, created unconditionally at setup alongside the six
temperature sensors, the `push_stream` diagnostic and the Fault
binary — no option, no checkbox, no confirmation (read-only decoding;
the 022 active-by-default world). It is a LITERAL entity in
`sensor.py`'s push branch (the `push_stream` precedent — no registry,
no RegDef, no placeholder). Classic-mode entries create NO fault code
sensor (register 47 is unreachable — law L1; the classic `fault_code`
RegDef stays untouched as the classic plane's own surface).

- name **"Fault code"** (renders `<device> Fault code`; entity_id
  `sensor.<device>_fault_code` via `has_entity_name`);
- key `pushed_fault_code`, unique_id `<ADDRESS>_pushed_fault_code`
  (plane-prefixed, DISJOINT from the classic `fault_code` key —
  contract 024 §3);
- NO `device_class` (a free-form string state; an `enum` class would
  demand a closed options list that the open-ended unknown-code
  family violates), NO `entity_category` (operational display
  signal), NO `state_class`, NO unit;
- icon `mdi:alert-circle` (the classic `fault_code` RegDef's icon).

### FR-2 — The state rule (single source of truth in registers.py)

The entity's state is `registers.fault_code_state(raw)` where `raw =
data.registers.get(2074)` (the same fresh 2046-block capture the
binary reads — one register, one source):

- `raw == 0` → **`No fault`** (the fault memory is empty since the
  last power-up — a decoded truth, research 024 §4);
- `raw != 0`, mapped (256) → **the panel code** (`"E03"`);
- `raw != 0`, unmapped → **`unknown code (raw N)`** (contract 022 §3
  carried into the STATE);
- register absent (no usable snapshot) → **`unknown`** — never a
  guessed "No fault";
- stale/silent stream → the cycle fails and the entity goes
  **`unavailable`** with every other entity (the shared freshness
  gate; FR-6).

Never optimistic: the state only ever follows a fresh pushed block,
exactly like the binary, the climate and the temperatures.

### FR-3 — The evidence attributes (the binary's set, mirrored)

`extra_state_attributes` carries the SAME evidence set as the Fault
binary (contract 023 §2): `register` (2074), `raw`, `hex` (`0x0100`
style), `set_bits` (the set-bit indices — arithmetic only, NO names,
forever), `code` when the raw maps, `code_label` when an app label is
recorded (E03's "water flow problem"). The banking protocol
(quickstart 023 §4) works unchanged from either entity. No timing
attributes (contract 024 §6 — the store's `first_seen`/`last_seen`
date the listening window, never the fault).

### FR-4 — registers.py: the state helper (the map untouched)

- NEW `PUSHED_FAULT_CLEAR_STATE = "No fault"` with the latch
  evidence comment;
- NEW pure helper `fault_code_state(raw) -> str | None`: None → None
  (absent → the entity's unknown); 0 → `PUSHED_FAULT_CLEAR_STATE`;
  non-zero mapped → the map's code; non-zero unmapped →
  `f"unknown code (raw {raw})"` (stdlib-only, pure, mirroring the
  023 helpers beside it);
- `PUSHED_FAULT_CODE_MAP` is UNTOUCHED (`{256: "E03"}` — one row per
  recorded observation, contract 023 §6); `PUSHED_FAULT_CODE_APP_
  LABELS`, `decode_fault_flag`, `fault_code`, `fault_code_app_label`,
  `fault_set_bits`, `REG_PUSHED_FAULT`, `PUSH_CONTROL_CANDIDATES`,
  `KNOWN_LABELS` are UNTOUCHED. Bit-to-flag-name tables remain
  FORBIDDEN (contract 023 §3).

### FR-5 — The binary is untouched (addition, not replacement)

`binary_sensor.py` stays **byte-identical** (the sensor is added;
the binary remains the automation surface "is a fault on record?").
The 023 suite must pass unmodified. The sensor's state and the
binary's state decode the same raw through the same registers module
— they can never disagree.

### FR-6 — The freshness NON-change (no fault-specific gate — documented)

NO fault-specific freshness, staleness or age code is added
anywhere. The fault entities ride the SHARED cycle freshness gate
(contract 013 §6.1) exactly like the temperatures: the gate tests the
STREAM's recency, never VALUE movement — a latched word rides fresh
blocks forever and passes correctly; a silent module fails the cycle
and everything goes unavailable together (honest: while deaf we
cannot know the fault state, and a guessed clear is forbidden). The
reasoning is normative in contract 024 §5 so no future spec "fixes"
it backwards.

### FR-7 — README: the latch and the new tile (three spots)

1. **Features table** — the fault row becomes the two-entity truth
   with the latch pointer (plan §2.4's verbatim row).
2. **"Entities (push mode)" table** — a new `Fault code` row with
   the state vocabulary and the one-line latch semantics (plan §2.4).
3. **Troubleshooting** — the existing Fault row gains the latch
   sentence; a new row answers the exact confusion the owner hit:
   "the fault code shows while the unit is off / healthy" → the
   latched-memory explanation + the power-cycle remedy (plan §2.4's
   verbatim row). No sentence anywhere may claim that turning the
   unit off clears a fault.

### FR-8 — docs/CALIBRATION.md §3: the stale bullet fixed (stale since 023)

The "Fault code & firmware: no home was ever identified in the
pushed blocks — no entity is created" bullet is WRONG since spec 023
(023's tasks missed it — research 024 §8). 024 rewrites it: the
fault word (2074) IS exposed (the 023 binary + the 024 code sensor)
and is LATCHED (memorized until the supply is cut — owner-proven
2026-10-12); firmware stays unexposed (no home identified).

### FR-9 — No portal, no gate, no service (the read-only guarantee)

Nothing in this spec adds any confirmation surface: no option key,
no config-flow step, no service, no global switch; the push options
schema stays exactly `poll_interval` + `temperature_unit`.
Composing contract 024 §7: **no "clear fault" surface may ever
exist** — clearing a latched fault is a physical act on the machine
(cut the supply), never an integration feature. The write plane is
untouched: 2074 is refused by the funnel forever.

### FR-10 — Manifest 0.18.0

`custom_components/aqua_temp_ble/manifest.json` version bumps to
`0.18.0` (the 023→0.17.0 precedent; HACS sees the change).

## 2. Acceptance criteria

- **AC-1** A push entry creates exactly ONE new sensor: name "Fault
  code", unique_id `<ADDRESS>_pushed_fault_code`, icon
  `mdi:alert-circle`, no device_class, no entity_category, no unit,
  no state_class, attached to the entry's device; a classic entry
  creates none; the binary surface is UNCHANGED (still exactly one
  binary_sensor). [test]
- **AC-2** The state table: raw 0 → `No fault`; 256 → `E03`; 4 →
  `unknown code (raw 4)`; absent register → `unknown`; no snapshot →
  `unknown`; unavailable while the cycle fails (the shared gate). [test]
- **AC-3** Attributes mirror the binary's set: raw 256 → `code:
  "E03"`, `code_label: "water flow problem"`, `set_bits: [8]`, `raw:
  256`, `hex: "0x0100"`; raw 0 → no `code`, no `code_label`,
  `set_bits: []`; raw 4 → `code: "unknown code (raw 4)"`,
  `set_bits: [2]`, no `code_label`; NO timing attribute ever. [test]
- **AC-4** The owner's evidence pinned byte-exact: feeding the 023
  E03 frame (99 bytes, CRC 551d) through the real store → the sensor
  reads `E03` (and the binary ON); the healthy twin (2074 = 0) →
  `No fault` (and OFF). [test]
- **AC-5** registers: `PUSHED_FAULT_CLEAR_STATE == "No fault"`;
  `fault_code_state` pure and total over {None, 0, 256, 4, 8192};
  `PUSHED_FAULT_CODE_MAP` unchanged (exactly one row); no bit name
  anywhere in the integration (the forbidden-names grep). [test]
- **AC-6** Freshness NON-change: no fault-specific freshness code
  exists (grep: no new gate/staleness reference in the fault paths);
  the shared gate's behavior is unchanged (the existing suites stay
  green unmodified). [test + grep]
- **AC-7** `binary_sensor.py` byte-identical (git diff empty); the
  full 023 suite passes unmodified; PLATFORMS unchanged. [CI + test]
- **AC-8** README: the features row, the entities row and the
  troubleshooting rows state the latch + the new tile; no "turning
  the unit off clears the fault" claim anywhere; CALIBRATION.md §3
  states the new truth. The three translation JSONs are untouched
  (key parity green). [manual + test]
- **AC-9** No new option key, flow step, service or confirmation
  anywhere; the push options schema stays exactly `poll_interval` +
  `temperature_unit`; 2074 refused by the funnel (write-plane suites
  green). [test]
- **AC-10** Manifest `0.18.0`; the full suite and the `modbus.py`
  self-test green; the value rails, allowlists and pinned frames
  byte-identical (the untouchables, plan §5). [CI]

## 3. Out of scope

- **The vendor's raw↔code table beyond the proven point** — the APK
  assets (`assets/faults/faults_*.json`) remain the owner's optional
  static lead (contract 023 §6, unchanged).
- **The bit names** (which protection bit 8 names) — [A]/[V] in
  research 023 §2; NEVER in entity code.
- **Any fault WRITE path, including a "clear fault" service** —
  clearing is physical (cut the supply), never an integration
  feature (contract 024 §7).
- **Timing attributes** ("fault since X") — rejected: the store's
  timestamps date the listening window, never the fault; the
  recorder is the honest timeline (research 024 §5.2, contract
  024 §6).
- **Any freshness exemption for the fault** — the shared gate is
  already correct for a latched word (research 024 §6, contract
  024 §5).
- **The classic-mode fault code entity** — register 47 unreachable
  (law L1).
- **specs 001-023** — never modified. The spec-020 reference's stale
  fault bullet is the PARENT's recommended follow-up revision after
  024 ships (research 024 §5.1) — not a task of this spec.
- **The firmware entity** — still no pushed home; unchanged.