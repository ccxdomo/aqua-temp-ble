# Spec 016 — The write control path: the vendor's on/off frame and the proof-gated write

Date: 2026-10-12
Status: proposed (Coder-ready)
Depends on: specs 003 §6 (write security), 010 (write char/parity),
011/012 (the security probe + verdict), 014 (trigger+listen), 015
(read-only climate + the 2011/2019 pinning)
Evidence: `research.md` (this directory) — every FR below cites it.

## 1. Problem

The integration reads the unit perfectly in push mode (5 calibrated
temperatures; the climate displays OFF/HEAT from the pinned read homes
2011/2019). The owner wants on/off CONTROL from Home Assistant. The
climate is read-only by security decision (spec 015 FR-8), the write
allowlist is `{8, 13, 14}` (+19-21 behind a switch), and the vendor's
actual on/off frame had never been recovered — so there was nothing
proven to write and no honest path to extending the guard.

This spec changes that state in a strictly evidence-gated way:

1. The vendor's on/off frame is NOW RECOVERED from the app's own
   disassembly (research §0/§2): **register 1011, value 1/0**,
   `631003f30001020001f131` / `631003f3000102000030f1`.
2. The climate STAYS read-only in this spec's default state. A
   **diagnostic power-write probe** (owner-triggered, reversible,
   verdict in the log) exists to produce the missing proof.
3. The guard extension to 1011 is a **separate, evidence-gated phase**
   that the OWNER unlocks after the probe's verdict — never before
   (contract 016 §3).

## 2. Functional requirements

### Phase 1 — data + the proof instrument (this spec's shipped state)

**FR-1 — Write-plane constants (data only).** `registers.py` gains
the write-plane map with evidence tags, as DATA, unreachable by any
write path:
- `WRITE_REG_POWER = 1011` `[P research §2]`
- `WRITE_REG_MUTE = 1076` `[P research §5]`
- `WRITE_REG_MODE = 1012` (+ the pair shape) `[P shape / A semantics]`
- `WRITE_FRAME_POWER_ON = "631003f30001020001f131"`,
  `WRITE_FRAME_POWER_OFF = "631003f3000102000030f1"` (or built via
  FR-2 and asserted equal — either implementation, ONE source of
  truth) `[P research §2.1]`
- `READ_MIRROR_POWER = 2011` (the proof oracle — the existing pushed
  control home, already in `PUSHED_CONTROL`) `[P spec 015]`

**FR-2 — Frame builder for the control shape.** `modbus.py` gains
`build_write_control(address: int, value: int) -> bytes` producing
exactly `6310 <addr:2> 0001 02 <val:2> <crc:2>` (11 bytes), reusing
the existing `_frame`/CRC plumbing. The shape is NORMATIVE: qty is
always `0001`, bytecount is always `02` for this builder (the
home-screen power shape — research §1.1). Self-test vectors (in
`modbus.py`'s self-test, the §10 precedent): both pinned power
frames and the mute frames (research §2.1's four CRC rows).

**FR-3 — The write-control guard (extended, default-locked).**
`registers.py` gains:
- `ALLOWED_WRITE_CONTROL: dict[int, frozenset[int]] = {1011: frozenset({0, 1})}`
  — the address AND the value allowlist (tighter than the base-block
  guard: only 0 or 1 may ever be written to 1011).
- `WRITE_CONTROL_PROVEN = False` — the calibration switch
  (the `WRITE_ECHO_EXPECTED` / `PUSH_TEMP_CONFIRMED` precedent:
  flipped by the owner after the live proof, then restart HA).
- `ensure_writable(address, value=None)` EXTENDED: the base-block
  rules are UNCHANGED (spec 003 §6 verbatim); a control-plane write
  (1011) is allowed ONLY when `WRITE_CONTROL_PROVEN` is True AND the
  value is in the allowlist. With the switch False, `ensure_writable`
  keeps refusing 1011 exactly as today — the shipped behavior is
  byte-identical. The single write funnel (coordinator
  `async_write_register` calls `ensure_writable` first) is preserved.

**FR-4 — The power-write probe (the proof instrument).** The
coordinator gains `async_power_write_probe() -> dict` implementing
the diff-revert protocol (research §4 Q7 / quickstart §1):

1. Connect (the normal path), send the trigger (the base read — the
   proven trigger, spec 014), harvest the report; require the 2001
   block and read `2011`.
2. **Baseline gate**: if `2011 != 0`, ABORT with the verdict
   `refused-baseline-not-off` (the probe never toggles a unit that is
   not confirmed off).
3. Send `WRITE_FRAME_POWER_ON` on the write characteristic —
   **withoutResponse, app parity** (spec 010 §2.3; the acknowledged
   variant adds nothing — the proof is in the mirror, research §4).
4. Harvest again (the write itself is a valid request frame — it
   should trigger a report, research §4 Q4; if the harvest window
   yields nothing fresh, send ONE base-read trigger and harvest once
   more — the robust order).
5. Require `2011 == 1`. Then send `WRITE_FRAME_POWER_OFF`, harvest,
   require `2011 == 0`.
6. Log the verdict at **WARNING** (the operator's log page keeps
   WARNING+ — contract 012 §2):
   - `power write probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0→1→0) — evidence for phase 2 (WRITE_CONTROL_PROVEN)`
   - or the failure stage: `not-executed` (2011 stayed 0 after ON),
     `not-reversible` (2011 stayed 1 after OFF — the line must carry
     the remediation: "turn the unit OFF from the vendor app or the
     panel"), `att-error` (the write raised — the chain verbatim),
     `no-report` (the harvest never saw a 2001 block).
7. Return the dict (for the service response / diagnostics): the
   stage outcomes and the three mirror observations (raws + times).
8. Disconnect on completion (the push-mode convention) or on failure.
9. The probe is single-flight (the `_io_lock`) and serializes against
   reads (the existing lock discipline).

**FR-5 — The service + button.** A service
`aqua_temp_ble.power_write_probe` (schema: `device_id`/`entity_id` —
follow the existing `probe_registers` precedent) and a diagnostic
BUTTON entity "Power write probe" (the `button.py` precedent —
`probe-registers`). Both route to FR-4. The button is a DIAGNOSTIC
entity (category `diagnostic`), NOT a control surface.

**FR-6 — Probe safety rails (normative).**
- The probe writes ONLY the two pinned frames (FR-1) — no other
  register, no value other than 1 then 0.
- The baseline gate (FR-4 step 2) is mandatory.
- The revert write (step 5) is attempted even if step 5's own harvest
  is slow — a `not-reversible` verdict with the remediation line is
  the worst case; the probe never leaves a half-state silently.
- If the write characteristic resolves to anything other than the
  ff03 bucket (the adaptive fallback), the probe logs the resolved
  UUID in the verdict line (evidence discipline — spec 010 FR-2).

**FR-7 — The climate stays read-only (default).** `climate.py` is
UNCHANGED in this phase: `_refuse_write` (spec 015 FR-8) remains;
no write method is advertised. The ONLY code that may write 1011
before the proof is FR-4's probe (an owner-triggered diagnostic).

### Phase 2 — the guarded control surface (BLOCKED on evidence)

**FR-8 — The proven write path (blocked by contract 016 §4).**
Behind `WRITE_CONTROL_PROVEN = True` (the owner flips it after
FR-4's verdict, then restarts HA):
- `async_write_register` accepts 1011 with value 0/1 (FR-3).
- The push-mode climate's `async_set_hvac_mode` maps:
  `HVACMode.OFF` → write 1011 = 0; `HVACMode.HEAT` (and any on-mode
  — this unit is heat-only, the app locks mode changes [P apk]) →
  write 1011 = 1. The write goes through the SAME guarded funnel
  (FR-3), followed by the existing debounced refresh (the
  confirmation read).
- The write failure surface: if the write raises, the climate reports
  the error to HA (no silent failure); the state is NOT optimistically
  updated (the mirror decides — the spec 001 rule).
- `WRITE_MODE_SETPOINT_TOO`-style conservatism: NO mode register
  write, NO setpoint write, NO mute write in this spec (each needs
  its own proof — research §8 U-2/U-3/U-5).

**FR-9 — Verification line.** With `WRITE_CONTROL_PROVEN = True`,
the climate's `async_set_hvac_mode` logs at INFO one line per write
(`power write via 1011 = <v> — mirror should follow in the next
report`), and the next coordinator refresh cross-checks the mirror
(2011) against the requested state; a mismatch logs a WARNING
(`power write not confirmed by mirror`). The mirror is the truth,
never the optimistic write.

## 3. Non-functional requirements

**NFR-1 — No behavior change by default.** With
`WRITE_CONTROL_PROVEN = False` (shipped), the integration's write
surface, guard behavior, entity set and logs are byte-identical to
0.11.0 except: the new probe service/button (FR-5, inert until
pressed) and the new constants/self-tests (FR-1/FR-2).

**NFR-2 — One source of truth.** The frames live in `registers.py`
(or are built by `modbus.build_write_control` and asserted against
`registers`' pinned strings in the self-test — a mismatch fails the
self-test, the modbus.py §10 precedent).

**NFR-3 — The evidence discipline.** Every new constant carries its
evidence tag in a comment (the registers.py convention). The write
map's [V] members (mode semantics, setpoint address, parameter-page
addresses) are documented as data with tags but are NOT reachable.

**NFR-4 — No new dependencies.** The probe reuses the client,
coordinator, harvest and service/button plumbing that exists.

**NFR-5 — English-only artifacts** (repo rule; this spec included).

## 4. Acceptance criteria

- **AC-1** `modbus.build_write_control(1011, 1)` returns the bytes of
  `631003f30001020001f131`; `(1011, 0)` → `631003f3000102000030f1`
  (self-test, pinned vectors — FR-2).
- **AC-2** With `WRITE_CONTROL_PROVEN = False`, `ensure_writable(1011, 1)`
  raises ValueError (guard refuses; test).
- **AC-3** With `WRITE_CONTROL_PROVEN = True`, `ensure_writable(1011, 1)`
  and `(1011, 0)` pass; `(1011, 2)` raises (value allowlist; test);
  `(1012, 1)` raises (mode not extended; test).
- **AC-4** The base-block rules are untouched: `ensure_writable(8, …)`,
  `(13, …)`, `(14, …)` behave exactly as 0.11.0 (regression tests —
  the existing suite must pass unchanged).
- **AC-5** The probe, driven against a fake client scripted
  `mirror: 0 → (write ON) → 1 → (write OFF) → 0`, returns the
  `executed+reversible` verdict and logs the WARNING verdict line
  (test with the 011-suite fake-client precedent).
- **AC-6** The probe with `mirror stays 0` returns `not-executed`;
  with `mirror stays 1` after the revert returns `not-reversible` and
  the verdict line contains the remediation text (tests).
- **AC-7** The probe refuses to write when the baseline 2011 != 0
  (`refused-baseline-not-off` verdict; test) and sends NO frame in
  that case (the fake client records zero writes; test).
- **AC-8** The probe sends exactly two frames per run: ON then OFF,
  both 11 bytes, byte-equal to the pinned vectors (fake-client write
  recording; test).
- **AC-9** The service validates the target device (the
  `probe_registers` precedent) and the button appears as a diagnostic
  entity (test/UI smoke).
- **AC-10** The climate advertises no write capability while
  `WRITE_CONTROL_PROVEN = False` (the 015 test suite passes
  unchanged).
- **AC-11 (phase 2, blocked)** With `WRITE_CONTROL_PROVEN = True`,
  setting hvac_mode OFF/HEAT writes 1011 = 0/1 through the funnel and
  the refresh cross-check logs the mirror confirmation/mismatch
  (FR-9; tests). This AC MUST NOT be merged before the owner's probe
  verdict is recorded in the spec's decision log (contract 016 §4).

## 5. Out of scope

- The mode write (1012-1013) — the mode enum is [A] and the unit is
  heat-only; a follow-up spec may take it after the logcat oracle
  (research §8 U-2).
- The setpoint write — the address is runtime [V] (research §8 U-3).
- The mute write (1076) — no climate surface needs it (U-5).
- The timer/parameter pages' writes (runtime addresses — the logcat
  oracle's territory).
- Any HCI capture choreography (spec 009's E5 stays available but is
  no longer on the critical path: the frame is proven statically and
  the probe decides the delivery question).
- Extending `ALLOWED_WRITE_BASE` (the base-block {8,13,14} stays
  exactly as spec 003 §6 froze it).