# Contract 015-A — Control mapping promotion (read plane)

Normative rules for turning a pushed-plane register into the climate's
power / mode / setpoint source. This contract EXTENDS the 013/014 rules
(their texts stay authoritative); it follows the `PUSH_TEMP_CONFIRMED`
precedent (contract 013 §5) and the repo's "device-confirmed state only"
rule (spec 001).

## §1 Definitions

- A **control home** is a register R in 1001-2090 whose value reflects
  the unit's power state, mode, or user setpoint.
- A **candidate** is a register that merely COULD be a control home
  (data-model §7 lists them; they are [V] by construction).
- **Pinned** = promoted from candidate to home under §2.
- **Confirmed** = the confirmation gate (§4) is open for the key.

## §2 Pinning evidence (what may promote a candidate)

A candidate is pinned ONLY by one of:

1. **The diff-revert protocol** (quickstart §1): the register's value
   follows a user-made control change AND its revert, in at least one
   capture pair; the capture-time displayed state (step 0) is recorded.
   Reversibility is mandatory — a one-way change is a candidate still.
2. **The vendor write-address oracle** (quickstart §2) for the WRITE
   side: the logcat `address = A` line names the register the app
   writes for that control. The READ home may then be A (or its
   observed read mirror), cross-checked against §2.1.
3. **The owner photo match** may COLLAPSE a candidate set (e.g. the
   displayed setpoint = 27.0 → {1051, 1068}) but may never ALONE pin a
   member — the diff still decides which of the survivors it is.

A single capture NEVER pins anything (the 2001 lesson — research §2.3:
five non-zero registers, zero provable semantics).

## §3 Staticness handling

- Blocks 1001/1046 are byte-identical across captures [P]. A static
  candidate there (e.g. the ladder setpoint members) is NOT excluded —
  an untouched control is also static — but the diff must be run across
  an actual change to count (§2.1).
- A candidate that is static ACROSS a confirmed user change is excluded
  from that quantity's set [P-grade].

## §4 The confirmation gate

- `registers.PUSH_CONTROL_CONFIRMED` (default False) OR the per-entry
  option `push_control_confirmed` — the `push_temp_confirmed` pattern.
- Unconfirmed → the keys decode `None` (the 0.10.0 behaviour,
  byte-identical; the FR-11c non-regression test).
- The gate is per-entry OR global, opened by the owner after the §2
  evidence, closed by deleting the option (recoverable, no migration).

## §5 Entity surface (what confirmation unlocks)

- Confirmed power/mode/setpoint → the push-mode climate entity
  (READ-ONLY: FR-8 — no write features; the entity's diagnostics carry
  each key's evidence tag, e.g. `power: pinned by diff-revert 2026-10-xx`).
- hvac_mode: power False → `off`; power True + mode → the mapped mode
  (`MODE_VALUE_TO_NAME` — [A] stays [A] until the mode-diff or the
  oracle corrects it; an unmapped value renders `unknown`, the
  `_logged_unknown_modes` pattern).
- target_temperature: the confirmed setpoint home, ÷10,
  plausibility-guarded; bounds keep the fallbacks until separately
  pinned (FR-9).

## §6 Register discipline

- All homes live in `registers.PUSHED_CONTROL` — the single source of
  truth; every entry carries its [P]/[A]/[V] tag + evidence in a
  comment (AC-5).
- `check_registry_integrity` validates: addresses within 1001-2090,
  no duplicates across the pushed plane, keys limited to
  {power, mode, setpoint} (FR-11d).

## §7 Negative results

If the diff-revert moves NO register for a quantity across a confirmed
change, that quantity has no home in the pushed plane [P-grade]:
the climate (or the corresponding key) is not created; research.md
§8/§9 records the verdict and the follow-up path (E5, the oracle, the
revival lever). Negative proofs are permanent until contradicted by new
frames (a firmware update may change the dataset).

## §8 Read ≠ write

A pinned READ home NEVER authorizes writing that register (contract
015-B §2.4): a diff proves what the register REFLECTS, not what writing
it CONTROLS. Write authorization follows contract 015-B exclusively.