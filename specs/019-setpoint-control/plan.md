# Spec 019 plan — the setpoint control

Date: 2026-10-12
The evidence and every decision's rationale: `research.md`. The
normative rules: `spec.md` + `contracts/setpoint-write-contract.md`.
The owner's runbook: `quickstart.md`.

## 0. What changed in the world (why this plan is now possible)

Two facts landed this session; the plan converts them into an
honest read surface, a proof instrument, and — only on the proof —
a guarded write surface:

1. **The read home is proven live** [P owner]: register 1046 IS the
   heat setpoint — the owner's panel diff moved it 290 → 300. The
   register is PUSHED (the 1046-block), so the mirror oracle that
   contracts 016/017 built the power proof on exists NATIVELY for
   the setpoint: no offset hunting, no read-plane guessing.
2. **The write plane is recovered** [P disassembly]: the vendor app
   writes the setpoint as ONE register (qty 1, "02", `temp × 10`)
   to an address resolved from the per-family, per-mode map
   `{1: [1135,1136,1137], 2: [1051,1046,1068], 3: [1073,1074,1075]}`
   — and the owner's family adjudicates to **1046, the same
   register the panel diff proved** [A-strong]. The 1013 hypothesis
   is dead as a static claim (zero `movz #1013` in the binary —
   research §3); the map is the truth, and its family-2 heat slot
   IS 1046.

The consequence that shapes everything below: **the write target
is a PARAMETER register that the unit pushes back to us.** Unlike
power (write 1011, mirror 2011 — two registers), the setpoint's
write target and read mirror are THE SAME register. That is what
makes a fully non-destructive first phase possible — and what
demands the strictest value discipline (a bad write is instantly
visible AND instantly effective: it changes the machine's
regulation target).

## 1. The path (phases, gates, one decision each)

```
Phase 1 (this spec, ships inert-by-default + the honest read)
  ├── T1: the read home wiring (the 1046-block decode) ....... coordinator/registers
  ├── T2: the write-plane constants (data) .................. registers
  ├── T3: the guard extension (default-locked, 3rd gate) ..... registers
  ├── T4: the parity vectors (build_write_control @ 1046) .... modbus self-test
  ├── T5: the probe (no-op → diff-revert, 1046 mirror) ....... coordinator
  ├── T6: the client seam + rails ........................... aqua_client
  ├── T7: the service + diagnostic button ................... services/button
  ├── T8: the climate read surface (target display) .......... climate
  └── T9: tests (guard, probe verdicts, read decode, no-regression)
                            │
                            ▼
        ┌─────────────────────────────────────────────────┐
        │ GATE — THE LIVE PROOF (the owner)               │  quickstart §1
        │ run the setpoint probe ONCE; read the verdict   │
        └─────────────────────────────────────────────────┘
                            │
        executed+reversible      anything else
                            │              │
                            ▼              ▼
  Phase 2 (FR-9/10/11, AC-9):  STOP. The verdict line names the
  the owner checks the         stage; a `not-executed` with the
  `write_setpoint` option      1046 frame (after one radio retry)
  (or flips WRITE_SETPOINT_    escalates to research §10 U-1's
  PROVEN); TARGET_             fallback (family 3's 1074) — a
  TEMPERATURE appears; the     spec addendum, never a blind
  mirror confirms every        retry.
  write.
```

**One decision per gate, no shortcuts** — the 016/017 discipline
verbatim: the phase-2 code may merge behind the closed gate (it
ships inert), the owner's probe verdict is the precondition, the
owner's act is the cause.

## 2. Why the probe is two-phase (the setpoint's own risk profile)

The power probe's oracle was a boolean toggle (0 → 1 → 0): binary,
reversible, and the probe REFUSED to touch a running unit. The
setpoint is a different risk class:

- an out-of-range write could drive continuous running or inhibit
  startup (a REGULATION value, not a switch);
- the write target IS the read register — so a "write the current
  value" frame is a TRUE no-op by construction: the register
  receives exactly what it already holds.

The design exploits that: **phase A writes the register's own
value** and verifies (a) the register kept it, (b) the WHOLE
45-register block is unchanged (the neighbor alarm — a mis-aimed
write would corrupt a ladder member, and this is where it is
caught). Phase A proves harmlessness — it does NOT prove
execution (a dropped write looks identical; the quickstart says so
plainly). **Phase B** is then the power probe's diff-revert in
 miniature: −0.5 °C (toward the range interior, never past a
 bound), verify the mirror follows, revert, verify again. The
 total physical effect: the pool's heat target dips half a degree
 for ~2 minutes — inside the machine's own regulation hysteresis
 (the ladder's diff/stop members read 0.5 [P decode]), and the
 unit accepts setpoint changes while off [P owner].

Why −0.5 and not −1.0: the smallest step that is provably visible
on the wire (the ×10 scale resolves 0.1, but the vendor's whole
value space is 0.5-grained — writing a non-0.5 value would itself
be off-spec [P decode]). Why toward the interior: a test value
must stay inside the guard's own range — the probe obeys the same
allowlist it will prove.

## 3. The frame discipline (what may ever go on the wire)

Exactly one register, one shape, one value space [P — research
§2/§6]:

```
63 10 0416 0001 02 <val:2 BE> <crc:2 LE>   — 11 bytes
val = temp_C x 10, in [100..400], multiple of 5
```

- Built ONLY by `modbus.build_write_control(1046, raw)` —
  byte-parity with the app's own builder pipeline (asserted by
  the self-test against research §6's pinned vectors; the
  `build_write_registers` cross-check keeps one source of truth).
- Sent ONLY fire-and-forget on the `_write_frame` seam
  (`withoutResponse`, ff03 — contract 016 §5). NEVER
  `write_registers` (the echo trap — contract 017 §4): the unit
  executes without-response writes and no FC10 echo has ever been
  observed [P — the 016/017 production runs].
- The MIRROR is the confirmation: the fresh pushed 1046-block
  (`last_seen > t_write`) — never an echo, never the request.

## 4. The guard (why a THIRD gate, and the value discipline)

`ensure_writable` grows a third branch beside the base-block rules
(003 §6) and the power branch (016 §4):

| Plane | Address allowlist | Value allowlist | Gate |
|---|---|---|---|
| base | {8, 13, 14} (+19-21 switch) | — (historic) | always (spec 003) |
| power | {1011} | {0, 1} | WRITE_CONTROL_PROVEN / write_control |
| **setpoint** | **{1046}** | **{100..400 step 5}** | **WRITE_SETPOINT_PROVEN / write_setpoint** |

Why a third gate and not an extension of `WRITE_CONTROL_PROVEN`
(research §8 Q8): (a) contract 016 §4's non-transference — every
register needs its own proof; (b) a different risk class — a
regulation VALUE whose misuse changes the machine's thermal
behavior, vs a reversible boolean; (c) independent activation —
the owner may want power control but not setpoint control (the
gates compose); (d) a different value discipline — a bounded range
with a granularity rule, not {0, 1}. The value allowlist is
PERMANENT (extending the range needs a new contract — 016 §4's
discipline verbatim).

Rails (defense in depth, the 017 pattern): the funnel guard FIRST
(before any lock or I/O) → the client's own setpoint rail (address,
range, step) → the probe's rail (the same, plus the baseline
gates). Three layers, one shape.

## 5. The confirmation cycle (phase 2, the 017 pattern verbatim)

```
validate (step, range) → raw = C x 10
→ ensure_writable(1046, raw, setpoint_allowed=…)
→ connect if needed → audit INFO
→ under _io_lock:
     client.write_setpoint(raw)          # fire-and-forget, FR-6's successor
     t_write = _now()
     ONE trigger                          # C-3: the write doesn't wake the report
     _wait_fresh_block(1046-block, t_write)
→ verdict: confirmed (INFO) / not-confirmed (WARNING, both values)
           / unconfirmed (WARNING, no fresh block)
→ snapshot push (confirmed/not-confirmed ONLY) — from_pushes of
  the LIVE store; the tile shows the MIRROR, never the request
→ stage dict → last_setpoint_write → extra_state_attributes
```

Two deliberate divergences from the power cycle (documented in
research §8 Q6/Q7):

1. **No persistent notification.** An unconfirmed OFF is a hazard
   (a silently running machine); an unconfirmed SETPOINT write is
   not — the machine keeps its old target, the tile shows the
   mirror's truth, the WARNING explains. The revert obligation
   (contract 016 §7) is satisfied by the probe's phase-B revert and
   the `not-reversible` remediation line, not by the control path.
2. **The write is allowed regardless of power state.** The panel
   changed the setpoint with the unit off [P owner] — the register
   is the machine's stored parameter, not a run-state command.

## 6. The climate surface

- **Read** (phase 1): `target_temperature` = the pushed 1046
  mirror, behind the EXISTING `push_control_confirmed` gate (the
  home is owner-proven; the display is honest today, independent
  of any write capability). min 10.0 / max 40.0 / step 0.5 — the
  SAME constants the guard enforces (one source of truth).
- **Write** (phase 2): `TARGET_TEMPERATURE` advertised only while
  `write_setpoint_allowed`; `async_set_temperature` validates,
  writes, confirms; refusals are loud (the 015 message with the
  gate closed; step/range errors named).
- **Unchanged**: `hvac_mode` (018's power-wins), `hvac_action`
  (None while on), `current_temperature` (the pushed inlet),
  the classic entity (base reg 14 — spec 003's plane).

## 7. The read decode fix (one subtle but critical detail)

`from_pushes` today decodes EVERY control key from the 2001-block
capture. The setpoint home (1046) lives in the **1046-block**. FR-1
fixes the decode to read the home from its OWN block's capture
(`pushed_block_start(s_addr)`); the integrity window
(PUSH_CONTROL_FIRST/LAST = 1001/2090) already covers 1046, and no
collision exists (the temperature mirrors are 2046+). The probe and
the confirmation cycle use the same generalized fresh-block wait
(FR-12) — one parameterization, four call sites (the 2001 wait, the
1046 probe waits, the setpoint confirmation wait).

## 8. What this plan deliberately does NOT do

- No mode-aware address selection at runtime (1046 is pinned for
  this heat-only unit; the family map ships as data with its [V]
  key — a multi-mode unit is a future spec's job).
- No display of the [A-weak] ladder bound registers as min/max
  (the static 10.0/40.0 is the single source of truth until a
  bounds-diff pins 1050/1054 — research §10 U-5).
- No optimistic state anywhere: the tile NEVER shows the requested
  value (contract 016 §6 — the mirror decides, the 017 §5.3 rule).
- No silent clamping: a 30.3 °C request is an ERROR, not a rounded
  30.5 — a regulation value is never reinterpreted (research §7).