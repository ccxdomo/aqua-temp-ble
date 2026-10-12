# Spec 016 plan — the write-control path

Date: 2026-10-12
The evidence and every decision's rationale: `research.md`. The
normative rules: `spec.md` + `contracts/write-control-contract.md`.
The owner's runbook: `quickstart.md`.

## 0. What changed in the world (why this plan is now possible)

Three facts were missing until this spec's research; all three are
now proven:

1. **The frame** — the vendor's power on/off write is
   `6310 03F3 0001 02 <0|1> <crc>` (register **1011**, value 1 = ON,
   0 = OFF), hardcoded in the app's own home-screen power handler
   (research §2, disassembly + CRC).
2. **The sequence** — there is NO activation/unlock/handshake before
   commands; the app's guards read local state only (research §2/§4
   Q2).
3. **The delivery question is narrowed to one bit** — our frames reach
   the firmware (the report bursts follow our TXs — spec 014 M-4),
   the GATT layer raises no gate (the X1 acknowledged write was
   ATT-accepted — spec 011/012), so the only open question is whether
   the firmware EXECUTES our command the way it executes the app's
   (research §4 Q4's H-EXECUTES vs H-APP-BOND-GATE).

The plan converts those facts into a proof instrument, then — only on
the proof's verdict — into a guarded control surface.

## 1. The path (phases, gates, one decision each)

```
Phase 1 (this spec, ships inert)
  ├── T1: write-plane constants (data) ............... registers.py
  ├── T2: build_write_control + self-test vectors ... modbus.py
  ├── T3: the guard extension (default-locked) ...... registers.py
  ├── T4: the probe (diff-revert oracle) ........... coordinator.py
  ├── T5: service + diagnostic button .............. services/button
  └── T6: tests (guard, probe verdicts, no-regression)
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │ GATE 1 — THE LIVE PROOF (the owner)   │  quickstart §1
        │ run the probe ONCE; read the verdict  │
        └───────────────────────────────────────┘
                            │
        executed+reversible          anything else
                            │                │
                            ▼                ▼
  Phase 2 (FR-8, AC-11):      STOP. The verdict line names the
  flip WRITE_CONTROL_PROVEN;   stage; the spec 011 pairing ladder
  wire the climate's           (pair(), rungs A/B/C — already
  hvac_mode to 1011 0/1;       shipped) is the named next step for
  mirror cross-check on        the bond-gate residual; re-run the
  every write.                 probe after pairing.
```

**One decision per gate, no shortcuts:** the guard extension is merged
BEHIND the gate (the code can ship inert — `WRITE_CONTROL_PROVEN =
False` refuses 1011 exactly as today), so the Coder's phase-2 task is
written, reviewed and parked; the OWNER's evidence flips it
(contract 016 §4).

## 2. Why the probe (and not just wiring the climate)

- The repo's write-security philosophy (spec 003 §6, contract 015-B)
  requires PROOF that a write acts before the guard opens. A read-diff
  proves what a register REFLECTS; only a write→mirror-diff proves
  what a write CONTROLS (contract 016 §2).
- The probe is the minimal honest instrument: two pinned frames, one
  diff-revert, one verdict line the owner can read from the HA log
  page — no terminal, no optimistic state, no climate surface exposed
  before the proof.
- The failure path is productive: `not-executed` routes to the pairing
  ladder (already shipped in spec 011); `att-error` prints the ATT
  chain (a diagnosis in itself); `not-reversible` names the
  remediation (turn the unit off from the panel/app).

## 3. The frame discipline (what may ever go on the wire)

- Phase 1: exactly the two pinned 11-byte frames, ON then OFF, on the
  ff03 bucket, `withoutResponse` (app parity — research §4 Q3).
- The probe sends the base read as its trigger/report request (the
  proven trigger — spec 014 §6) — never a query-block frame (its
  triggering power is [V], spec 014 §1.2).
- Phase 2: only `1011 = 0` (OFF) / `1011 = 1` (on-modes) through the
  guarded funnel. No mode register, no setpoint, no mute, no
  parameter-page registers — each is [V]/[A] (research §5/§8) and
  needs its own spec.

## 4. Risks and their controls

| Risk | Control |
|---|---|
| A probe run toggles the owner's pump unexpectedly | baseline gate (2011 must be 0); the probe is a diagnostic BUTTON the owner presses deliberately; the choreography (quickstart §0) says when |
| The ON write executes but the OFF revert fails (unit stays on) | the verdict line carries the remediation (turn off from the panel); the revert is attempted in the same run; the probe never claims success on a half-state |
| The vendor app holds the single connection (spec 006 §9) | the choreography: kill the app / phone BT off before the probe (quickstart §0) |
| A stale/other GATT layout routes the write elsewhere | FR-6: the verdict line logs the resolved write UUID; the ff03 bucket parity is already proven (spec 010) |
| Someone flips `WRITE_CONTROL_PROVEN` without the proof | the contract's §4 evidence line is the gate; the tasks.md phase-2 task is explicitly BLOCKED; the flag's comment names the required verdict string |
| The 1011 write has side effects beyond power (unknown firmware semantics) | the frame is the app's OWN frame, sent by the app's own toggle path (research §2); the value allowlist {0,1} prevents any other value; the diff-revert proves both directions before the guard opens |

## 5. Sequencing for the Coder

1. T1+T2 first (pure data + builder + self-test — zero behavior).
2. T3 (the locked guard) — regression tests prove no behavior change.
3. T4+T5 (the probe + its surfaces) — the only new runtime surface,
   inert until pressed.
4. T6 last (the full suite: 011/013/014/015 suites must pass
   unchanged — NFR-1).
5. Phase 2 (T7) written but parked behind the gate; merged only with
   the owner's recorded verdict (AC-11's precondition).

## 6. What this plan deliberately does NOT do

- Does not touch the base-block allowlist {8, 13, 14} (spec 003 §6
  stays frozen; the base block remains unserved by this firmware —
  spec 014 M-7).
- Does not write 2011, 2019, or any read home (contract 016 §2).
- Does not promise the write works — it builds the instrument that
  decides.
- Does not require the Windows node, the DEX, or any new capture
  choreography beyond one probe run.