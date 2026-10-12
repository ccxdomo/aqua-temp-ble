# Contract 019 — The setpoint write: the regulation-value contract

Normative rules governing ANY write to the SETPOINT register
(1046) and the exposure of `TARGET_TEMPERATURE`. This contract
EXECUTES contract 016 (the write-plane philosophy: pinned frames,
mirrors, proofs) and contract 017 (the activation and confirmation
discipline); their texts stay authoritative and untouched. It adds
the rules a REGULATION value needs — the ones a boolean did not.

> The power proof proves power. A regulation register earns its
> own proof, its own gate, and a value allowlist — because a bad
> setpoint does not fail loudly; it heats wrongly.

## §1 Definitions

- The **setpoint register** is 1046 — the heat setpoint's
  parameter register on this unit: the pushed read home proven by
  the owner's panel diff (research §1 [P owner]) and the write
  target adjudicated from the vendor's family map (research §5
  [A-strong]).
- The **mirror** of the setpoint write is the SAME register — the
  fresh pushed 1046-block capture (`last_seen > t_write`). The
  write target and the read home coincide because the setpoint is
  a PARAMETER register (the "66" page's plane — read and written
  by the vendor's own app; research §4.2/§5).
- The **probe** is spec 019 FR-5's instrument: the no-op write
  (phase A) then the −0.5 °C diff-revert (phase B).
- The **gate** is the OR of the two owner-set surfaces:
  `registers.WRITE_SETPOINT_PROVEN` and the per-entry option
  `write_setpoint` (FR-9). It is the THIRD gate, separate from
  `WRITE_CONTROL_PROVEN` (§4).

## §2 The evidence rule (what may justify a setpoint write)

A setpoint write is legal ONLY when ALL of:

1. The frame is `modbus.build_write_control(1046, raw)` —
   byte-equal to the parity vectors of research §6 (asserted by
   the self-test; one source of truth).
2. The value `raw` is inside `[SETPOINT_MIN_RAW, SETPOINT_MAX_RAW]`
   (100–400 → 10.0–40.0 °C) AND a multiple of
   `SETPOINT_STEP_RAW` (5 → the 0.5 °C granularity). The value
   allowlist is PERMANENT; changing the bounds or the step
   requires a new contract.
3. The gate is open (the owner's act — §4) and the funnel guard
   (`ensure_writable(…, setpoint_allowed=…)`) has passed FIRST —
   before any lock or I/O.
4. The send is fire-and-forget on the `_write_frame` seam
   (contract 016 §5's parity table); NEVER `write_registers`
   (contract 017 §4's echo amendment — the mirror is the
   confirmation, never an echo).

Rule: **never write a setpoint value that was not explicitly
requested.** No clamping, no rounding, no "close enough": a
3 0 .3 °C request is refused (the step), a 41.0 °C request is
refused (the range). A regulation value is never reinterpreted.

## §3 The proof protocol (normative shape)

```
baseline:  connect → trigger → harvest the 1046-block → S = reg 1046
gates:     fresh block present; S plausible; S in [100..400]; S % 5 == 0
           (else verdict refused-baseline — NOTHING was written)
phase A:   write build_write_control(1046, S)   ← the register's OWN value
           → trigger → harvest → 1046 == S? AND the whole block unchanged?
           (a no-op: harmless by construction; it proves HARMLESSNESS,
            not execution — the quickstart says so)
phase B:   T = S − 5 (or S + 5 when S is at the min)
           write(1046, T) → harvest → 1046 == T?
           write(1046, S) → harvest → 1046 == S?
verdict:   EXECUTED + REVERSIBLE  (phase A held AND both B steps true)
           no-op-neighbor-changed  (phase A saw a block change — ABORT,
                                   the changed registers are named)
           not-executed           (1046 stayed S after the T write)
           not-reversible         (1046 stayed T after the revert — the
                                   line states the CURRENT setpoint and
                                   the remediation: restore it from the
                                   panel or the vendor app)
           att-error / no-report / refused-baseline
```

Mandatory properties:

- The probe writes ONLY `build_write_control(1046, ·)` frames,
  only values from {S, T}, only after the baseline gates pass.
- Phase B NEVER runs unless phase A held (the neighbor alarm is
  the hard stop).
- Both directions are required for the positive verdict (the 015
  pinning rule applied to writes; a one-way change is NOT proof).
- The verdict logs at WARNING (contract 012 §2) and names the
  stage on failure.
- The probe is the ONLY code that may write 1046 before the
  proof (its own validated seam — FR-6; the funnel stays closed).

## §4 The gate rule (the third gate, non-transference verbatim)

1. `WRITE_SETPOINT_PROVEN`/`write_setpoint` is set ONLY by the
   owner — after the probe's `EXECUTED + REVERSIBLE` verdict (§3),
   which MUST be recorded in the spec's decision log / PR. **No
   code path, update, or default may ever set either surface.** A
   shipped release that auto-enables setpoint control violates
   this contract.
2. The gate is reversible at any time (uncheck → reload → the
   write surface is gone; the read display stays — the home is
   owner-proven).
3. **Non-transference (contract 016 §4, restated):** the power
   proof authorizes 1011 with {0, 1}. It authorizes NOTHING here.
   The setpoint gate opens only on the SETPOINT probe's verdict.
4. **Why separate from the power gate:** a regulation VALUE is a
   different risk class from a boolean (§0); the owner may activate
   either without the other; the value disciplines differ
   (§2.2 vs {0,1}). Extending `WRITE_CONTROL_PROVEN` to cover 1046
   would silently attach an unproven register to a proven gate —
   the exact transfer this contract forbids.

## §5 The confirmation rule (the 017 discipline, the 1046 mirror)

1. Every control write is followed, in the same lock hold, by
   exactly ONE trigger and one fresh-block wait (C-3: the write
   does not wake the report — research 017 §2).
2. Three verdicts — confirmed / not-confirmed / unconfirmed —
   with 017 FR-4's log semantics; the snapshot push carries ONLY
   mirror-decoded values (never the request; contract 016 §6).
3. **No persistent notification on setpoint failures** [the
   deliberate divergence]: an unconfirmed or contradicted SETPOINT
   write leaves the machine at its old target — no running-unit
   hazard exists (contract 016 §7's concern); the WARNING + the
   tile's mirror-truth suffice. The revert obligation is carried
   by the probe (§3's not-reversible remediation), not by the
   control path.
4. The write is allowed regardless of power state: the register is
   the machine's stored parameter — the panel changed it while the
   unit was off [P owner].

## §6 The display rule (the target surface)

- The climate displays `target_temperature` from the pushed 1046
  mirror behind the EXISTING `push_control_confirmed` gate (the
  read home is [P owner]; a read-only target display is honest
  without any write capability — the 015 read-only pattern).
- `TARGET_TEMPERATURE` (the write affordance) is advertised ONLY
  while the setpoint gate is open.
- `min_temp`/`max_temp`/`target_temperature_step` are the guard's
  own constants (10.0 / 40.0 / 0.5) — one source of truth; the
  [A-weak] ladder bound candidates are NOT displayed until a
  future spec pins them.
- The requested value NEVER appears as state (§5.2); a mismatch is
  visible as the mirror's truth + the WARNING.

## §7 The exclusions

1. **No mode-aware addressing at runtime.** 1046 is pinned for
   this heat-only unit. The family map (research §4.1) ships as
   DATA with its [V] key; a multi-mode or other-family unit needs
   its own spec, its own proof, its own contract.
2. **No writes to the ladder's other members** (1049/1050/1051/
   1054/1055/1068…) — installer thresholds; the neighbor alarm
   (§3) exists precisely because a mis-aimed write would land
   here.
3. **No cool/auto setpoint surface** (1051/1068) — the mode is
   locked on this unit (contract 017 §6.1); the per-mode registers
   stay untouched.
4. The classic plane's setpoint path (base reg 14) is untouched
   (spec 003's territory).

## §8 The audit obligation

Every executed setpoint write leaves, in the owner's log page: the
audit line (INFO: register, raw value, expectation), the verdict
line (per §5), and the stage dict in `extra_state_attributes`
(`last_setpoint_write`). There is no silent control — the 017 §7
rule, restated.