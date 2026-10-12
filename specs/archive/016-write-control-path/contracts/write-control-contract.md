# Contract 016 — Write control: the proof-gated write plane

Normative rules governing ANY write to the vendor's CONTROL plane
(the 1001-series command registers). This contract EXTENDS contract
015-B (the base-block allowlist rule — that text stays authoritative
and untouched); it follows the same philosophy: **a read-diff proves
what a register REFLECTS, never what writing it CONTROLS** — and adds
the write-plane counterpart:

> A write is proven to control only by a diff-revert against a pinned
> READ mirror, or by the vendor's own frame proven by disassembly and
> confirmed live.

## §1 Definitions

- The **write plane** is the register set the vendor app WRITES
  commands to (research §5's map). The **read plane** is the pushed
  report (spec 013/014). They are DIFFERENT registers (power: write
  1011, read 2011) — no constant-offset rule may be assumed
  (research §3).
- A **pinned write** is a frame whose address, shape and value
  semantics are all [P] (today: the power on/off pair; the mute pair
  is [P frame] with [V] semantics).
- The **mirror** of a write is the pushed register whose diff-revert
  pinned it as that quantity's read home (power: 2011 — spec 015 /
  registers.py).
- The **probe** is the diagnostic instrument of spec 016 FR-4: the
  write→harvest→diff→revert→verdict cycle.

## §2 The evidence rule (what may justify a control write)

A control register may be written ONLY when ALL of:

1. The frame is a **pinned write** (disassembly-proven address + shape
   + values — research §1/§2), or recovered from the vendor's own
   logcat oracle (FINDINGS §12.2) and byte-verified against the
   builder's grammar.
2. A **mirror** exists and is pinned as the quantity's read home
   (spec 015's rule — the diff-revert pinning).
3. The **live proof** has produced the verdict
   `EXECUTED + REVERSIBLE` (§3) — the write→mirror diff followed BOTH
   directions.

Rule: **never write a read home.** The read homes (2011, 2019, …)
reflect state; writing them is out of contract even though they are
the proof oracles.

## §3 The proof protocol (normative shape)

```
baseline:  connect → trigger → harvest → mirror == 0 (else REFUSE)
step 1:    write the pinned ON frame  → harvest → mirror == 1?
step 2:    write the pinned OFF frame → harvest → mirror == 0?
verdict:   EXECUTED + REVERSIBLE  (both steps true)
           not-executed           (step 1 false)
           not-reversible         (step 2 false — carry the remediation)
           att-error              (the write raised — the chain verbatim)
           no-report              (no fresh report block)
```

Mandatory properties:

- The probe writes ONLY the pinned frames of ONE register per run,
  values only from the value allowlist.
- The baseline gate (mirror == 0) is mandatory — the probe never
  toggles an unverified state.
- Both directions are required for the positive verdict (a one-way
  change is NOT proof — the 015 pinning rule applied to writes).
- The verdict is logged at WARNING (the operator's log page —
  contract 012 §2) and names the stage on failure.
- The owner's no-terminal choreography is quickstart 016 §1 — the
  protocol must be runnable from the HA UI alone.

## §4 The guard-extension rule (when 1011 opens)

`ALLOWED_WRITE_CONTROL = {1011: {0, 1}}` is the ONLY write-plane
entry this contract admits, and it opens ONLY when:

1. The probe's §3 verdict is `EXECUTED + REVERSIBLE` — recorded by the
   owner (the verdict line quoted in the spec's decision log /
   PR description; quickstart §2 records the exact expectation).
2. The owner flips `WRITE_CONTROL_PROVEN = True` and restarts HA
   (the calibration-switch precedent).

Value discipline: **the value allowlist is part of the guard** — a
write of 1011 with any value other than 0 or 1 raises, forever, even
when proven. Extending the values requires a new contract.

New registers (mode 1012-1013, setpoint [V], mute 1076, the
parameter pages) require their own §2 evidence (their own pinned
frames, mirrors and §3 proofs) — the power proof does NOT transfer.

## §5 The transport parity rule (unchanged, restated)

Every control write goes through the existing single funnel
(`async_write_register` → `ensure_writable` → the client), on the
ff03 bucket, `withoutResponse`, as raw frame bytes (spec 010 §6.2's
parity table stays normative). No second write path may be created —
the probe uses the same `_write_frame` seam (the 011-suite
override point).

## §6 The honesty rules (surfacing, never silent)

- A control write never optimistically updates state: the mirror (the
  next report) decides (spec 001's rule; FR-9's cross-check).
- A mismatch between the requested state and the mirror logs a
  WARNING naming both values.
- The probe's verdict lines are the ONLY evidence the contract
  accepts; the climate exposes write capability ONLY while
  `WRITE_CONTROL_PROVEN` is True.

## §7 The revert obligation

Any code path that writes the power register ON in a context whose
success is not confirmed MUST ensure the OFF path is available and
named to the owner (the probe's `not-reversible` remediation line is
the template: "turn the unit OFF from the vendor app or the panel").
Leaving the unit powered on is an acceptable END STATE only with the
owner told, never silently.