# Spec 024 — Data model: the fault word's two faces and the latch

The single source of truth in code is `registers.py` (constants +
decode helpers) + the coordinator snapshot (`AquaTempData.registers`,
the 2046-block capture). This file mirrors the fault-code mapping and
the LATCH semantics for human readers. Evidence tags as in
`research.md` ([P] proven / [A] assumed / [V] open). Contract 023
(data-model 023 §1-§4) carries the register's wire facts; this file
adds the second face and the semantics layer.

## 1. The register (unchanged from 023, plus the latch)

| Property | Value | Evidence |
|---|---|---|
| Address | **2074** | [P — the 023 diff-revert; RE-CONFIRMED by the 2026-10-12 follow-up run] |
| Home block | 2046 (the T-family block, regs 2046-2090; index 28; frame bytes 63-64; big-endian) | [P — research 023 §1.2-§1.3] |
| Healthy value | **0** | [P — the 023 healthy/cleared captures; the post-power-cut capture 2026-10-12] |
| E03 value | **256 = 0x0100 = bit 8** | [P — two independent live runs: the 023 diff-revert + the 2026-10-12 re-run] |
| Clearing rule | **LATCHED: only cutting the unit's power supply clears the word** (persists through the machine's logical off and BLE link restarts) | **[P — the owner's 2026-10-12 three-step experiment, research 024 §1]** |
| Write status | **never writable** — refused by `ensure_writable` forever | [P — contracts 003 §6 / 023 §5 / 024 §7] |

## 2. The flow: wire → store → snapshot → two faces

```
the unit answers the poll cycle's trigger with the block burst
        |
        v
block 2046 frame (99 bytes, CRC-gated — _store_push stores only
CRC-valid whole frames; latest-frame-wins; per-connect store)
        |
        v
captures[2046].registers          # {2074: 256, ...} the raw word
        |
        v
AquaTempData.from_pushes  ->  data.registers[2074]     (ONE source)
        |                                       |
        v                                       v
registers.decode_fault_flag(raw)      registers.fault_code_state(raw)
        |                                       |
        v                                       v
binary_sensor "Fault"                 sensor "Fault code"
is_on: None | False | True            native_value: None | "No fault"
                                      | "E03" | "unknown code (raw N)"
attributes (023, unchanged):           attributes (mirror of the
register, raw, hex, set_bits,         binary's set — contract 024 §3):
code, code_label                      register, raw, hex, set_bits,
                                      code, code_label
```

**One register, one source, two faces** (contract 024 §4): the binary
answers "is a fault on record?" (automations); the sensor answers
"which fault?" (display). Both decode the SAME `data.registers[2074]`
through helpers in the SAME module — they can never disagree. The
023-era "no second entity" clause is superseded by the owner's
display ask + the HA constraint (a `problem` binary renders localized
on/off words; a code cannot ride its state — research 024 §2.1).

## 3. The state table (normative: contract 024 §3)

| raw | binary | sensor state | attributes (`code`/`code_label`/`set_bits`) | Meaning |
|---|---|---|---|---|
| 0 | `off` | **`No fault`** | — / — / `[]` | the fault memory is empty since the last power-up [P] |
| **256** | `on` | **`E03`** | `"E03"` / `"water flow problem"` / `[8]` | the memorized fault the panel displayed E03 for [P ×2] |
| 4 (any unmapped non-zero) | `on` | **`unknown code (raw 4)"` | `"unknown code (raw 4)"` / — / `[2]` | an honest unknown — never a guessed code (contract 022 §3) |
| absent | `unknown` | `unknown` (None) | only `register` | no usable snapshot — never a guessed clear |
| stale/silent stream | `unavailable` | `unavailable` | — | the shared freshness gate fails the cycle; everything together |

The map is keyed by the RAW WORD, never by bits: a multi-bit word
(e.g. 260 = bits 2+8) is NOT decomposed — the vendor's multi-fault
encoding is unknown [V]; 260 reads `unknown code (raw 260)` until a
live observation records it.

## 4. The latch semantics (the meaning layer — the 024 addition)

The wire rule (contract 023 §2) is unchanged: `0` = clear, non-zero
= the unit reports a fault code. The latch adds the MEANING of the
non-zero half:

- **`2074 != 0` = the unit memorized a fault since the last power-up
  — NOT necessarily an active fault.** The memorized code can outlive
  the fault it records: the owner watched `256` sit on a healthy,
  logically-off machine [P].
- **`No fault` (raw 0) = the fault memory is empty since the last
  power-up** — the state after a supply cut, and the baseline state
  of a freshly powered unit.
- **The only proven clear is the supply cut.** Turning the machine
  off does NOT clear the word [P]; nothing else observed clears it
  ([V] any app/panel remote clear — never claimed).
- **The entities do not lie:** they report the machine's own fault
  memory faithfully. A latched code on a healthy machine is the
  machine's annunciation, and the docs (README, quickstart) teach
  the user to read it as such — check the panel; cut the supply to
  clear.

Where the user reads it: README (features row, entities rows, the
two troubleshooting rows), quickstart 024 §2-§3, this file.

## 5. Freshness (the shared gate; no fault-specific rule)

- The gate (contract 013 §6.1) tests the STREAM's recency — a fresh
  2046-block capture within `max(2 × poll, 60 s)` — **never VALUE
  movement.** A latched word rides every fresh block identically and
  passes forever: there is no "value must have changed" rule anywhere
  in this integration, and none may be added for the fault (contract
  024 §5's standing rule).
- The machine logically OFF keeps answering the trigger [P — the
  latch run itself: the live word was displayed for the whole off
  period; spec 020 AC-2's world]. A stopped machine does not look
  stale.
- A SILENT module (supply cut, out of range, the vendor app holding
  the single link) fails the cycle → both fault entities
  `unavailable` with everything else — honest: while deaf we cannot
  know the fault state, and a guessed clear is forbidden. The
  power-cut clear passes through this world in order: silence →
  unavailable → stream returns → `No fault`.

## 6. The timestamps (why they cannot date the fault)

The store's `PushCapture.first_seen` / `last_seen` are block-level,
connect-scoped bookkeeping: the monotonic ts of the first/latest push
of the block START (2046) in the CURRENT BLE session (contract 013
§3.1). They date our LISTENING WINDOW — never the fault:

- the fault may have latched BEFORE the connect (the attribute would
  claim a false recency) or AFTER it (a false age) — both errors are
  lies;
- the store resets on every reconnect, so any derived "fault age"
  would silently restart mid-fault at each HA reload;
- the unit provides no fault-timestamp register [V — none identified
  on the pushed plane].

**Therefore no timing attribute exists on either fault entity**
(contract 024 §6). The honest timeline is the RECORDER: the sensor's
state history shows when HA observed each transition — a true
statement about our observation, never a false one about the machine.

## 7. The snapshot shape (delta to spec 020's data-model §4)

```
AquaTempData.registers   # the 2046-block raws ALREADY carry 2074 —
                         # no new coordinator field, no coordinator edit
push_control_raws        # unchanged (2074 already rides the
                         # PUSH_CONTROL_CANDIDATES diagnostics, spec 023)
```

No new field on `AquaTempData`; no coordinator change. The sensor
reads `data.registers` through the shared helper — the same source
discipline as the binary and the temperatures.

## 8. The classic plane (unchanged, dead on this unit)

Base register 47 = "Fault1" [P apk]. The unit never serves the base
block (law L1, spec 020): no classic fault entity, none creatable.
The classic `fault_code` RegDef (key `fault_code`, unique_id
`<ADDR>_fault_code`) keeps its base-plane home and stays uncreated in
push mode. The pushed sensor's key is **`pushed_fault_code`** —
plane-prefixed and DISJOINT from the classic key (data-model 023
§6's rule): the two planes can never collide in the entity registry,
whatever a future firmware world does.