# Spec 019 — The setpoint control: the proven read home, the recovered write plane, the proof-gated target

Date: 2026-10-12
Status: proposed (Coder-ready)
Depends on: specs 003 §6 (write security), 010 (write char/parity),
014 (trigger+listen), 015 (the pushed control plane + the read-home
pinning rule), 016 (the write contract — the funnel, the value
discipline, the probe pattern), 017 (the confirmation cycle + the
activation gates), 018 (the power-wins display)
Evidence: `research.md` (this directory) — every FR below cites it.

## 1. Problem

The climate controls power (proven, 017). The setpoint is the last
missing control: the owner changes it at the panel today; Home
Assistant neither displays nor sets it. The shipped state:
`PUSHED_CONTROL["setpoint"] = None` (no read home identified —
research 015 §2.3's scan), the climate advertises no
`TARGET_TEMPERATURE`, and `ensure_writable`'s control allowlist is
`{1011: {0, 1}}` only.

Two facts changed this session:

1. **The read home is proven live**: register **1046** is the heat
   setpoint — the owner's panel diff moved it 290 → 300 [P owner,
   research §1]. The register sits in the PUSHED 1046-block, so the
   integration can display it today and any write can be confirmed
   by a fresh pushed block natively.
2. **The write plane is recovered from the disassembly**: the
   vendor app writes ONE register (qty 1, "02", value `temp × 10`)
   to an address resolved at runtime from the per-family map
   `{1: [1135,1136,1137], 2: [1051,1046,1068], 3: [1073,1074,1075]}`
   indexed by MODE [P — research §4]. The owner's family adjudicates
   to **1046** (the heat slot of family 2 — the same register the
   panel diff proved [A-strong — research §5]); the alternative
   1013 hypothesis is disproven by the exhaustive immediate scan
   (zero `movz #1013` in the whole binary [P — research §3]).

This spec wires the read honestly, ships the write-plane data plus a
**two-phase non-destructive probe** (a no-op write first, then a
−0.5 °C diff-revert), and opens the guarded `TARGET_TEMPERATURE`
surface ONLY behind a new, separate gate (`WRITE_SETPOINT_PROVEN`)
after the probe's verdict — the power path's discipline
(contract 016 §4's non-transference) applied to a REGULATION value,
with a bounded value allowlist instead of {0, 1}.

## 2. Functional requirements

### Phase 1 — the read home + the write-plane data + the proof instrument (this spec's shipped state)

**FR-1 — The setpoint read home (the pushed decode).**
`registers.py`:
- `PUSHED_CONTROL["setpoint"] = 1046` [P owner — research §1].
- `coordinator.AquaTempData.from_pushes` decodes `target_temp`
  from the capture of the block that CONTAINS the home —
  `pushed_block_start(s_addr)` → the 1046-block capture — NOT from
  the 2001-block capture (today's code reads every control key from
  the 2001 window; 1046 lives in the 1046 window — research §1).
  The decode rides the EXISTING `push_control_confirmed` gate (the
  home is owner-proven; the 015 gate's semantics — "the control
  mapping is confirmed" — cover it). Unconfirmed ⇒ `target_temp`
  stays None exactly as today.
- `KNOWN_LABELS` corrections (research §1.1): 1046 → "setpoint home
  (heat) [P owner diff 2026-10-12]"; 1051 → "cool setpoint
  candidate [A-strong 019]"; 1068 → "auto setpoint candidate
  [A-strong 019]"; 1050/1054 keep their [A-weak] bound tags.
- `PUSH_CONTROL_CANDIDATES` gains nothing new (1046 is already
  listed — its tag comment updates to the proven wording).
- The integrity checks pass unchanged (1046 is inside the control
  plane window 1001-2090 and collides with nothing — FR-11d of
  015 already allows it).

**FR-2 — The write-plane constants (data only).** `registers.py`
gains, as DATA with evidence tags, unreachable by any write path
(the 016 FR-1 precedent):
- `WRITE_REG_SETPOINT = 1046` — the heat-mode write target
  [A-strong — research §5; the family map is data too]:
- `WRITE_SETPOINT_FAMILY_MAP = {1: (1135, 1136, 1137), 2: (1051,
  1046, 1068), 3: (1073, 1074, 1075)}` — mode-indexed
  (cool/heat/auto), documented for the record [P — research §4.1].
- `READ_MIRROR_SETPOINT = PUSHED_CONTROL["setpoint"]` (the proof
  oracle — 1046, the pushed home).
- `SETPOINT_MIN_RAW = 100`, `SETPOINT_MAX_RAW = 400` (10.0–40.0 °C)
  [D — research §7], `SETPOINT_STEP_RAW = 5` (the 0.5 °C
  granularity [P decode — every observed raw is a multiple of 5]).

**FR-3 — The frame builder parity.** `modbus.build_write_control(
1046, value)` (the existing builder, research 016 §1.1's grammar)
MUST produce the pinned vectors of research §6 — the modbus
self-test gains the parity rows (30.0 °C = `63100416000102012c5049`,
29.5 °C = `631004160001020127118e`) and asserts
`build_write_registers(1046, [300]) == build_write_control(1046,
300)` (one source of truth — the 017 AC-1 precedent).

**FR-4 — The write-setpoint guard (extended, default-locked).**
`registers.py`:
- `ALLOWED_WRITE_SETPOINT: dict[int, frozenset[int]] = {1046:
  frozenset(range(SETPOINT_MIN_RAW, SETPOINT_MAX_RAW + 1, 5))}` —
  the address AND the value allowlist (the value discipline of
  contract 016 §4, a RANGE instead of {0,1}: only 10.0–40.0 °C in
  0.5 steps may EVER be written to 1046).
- `WRITE_SETPOINT_PROVEN = False` — the THIRD gate's calibration
  switch (the `WRITE_CONTROL_PROVEN` precedent).
- `ensure_writable(address, value=None, control_allowed=None,
  setpoint_allowed=None)` EXTENDED: the base-block rules
  UNCHANGED (spec 003 §6); the power control rules UNCHANGED
  (016/017); a setpoint write (1046) is allowed ONLY when the
  setpoint gate is open AND the value is in the allowlist. With
  the gate closed, `ensure_writable(1046, …)` refuses exactly as
  0.13.2 does — the shipped behavior is byte-identical. The funnel
  rule is preserved (the guard runs FIRST, before any lock or I/O).

**FR-5 — The setpoint write probe (the proof instrument).** The
coordinator gains `async_setpoint_write_probe() -> dict` — the
TWO-PHASE protocol (research §8 Q5, quickstart §1):

1. Connect; trigger; harvest the fresh **1046-block**; read the
   baseline `S = registers[1046]`.
   **Baseline gates** — ABORT (verdict `refused-baseline`, nothing
   written) when: no fresh 1046-block; `S` absent/sentinel/implausible;
   `S` outside 100–400; `S` not a multiple of 5 (the probe refuses
   to compute a safe test value from a weird register).
2. **Phase A — the no-op (non-destructive by construction)**:
   build `build_write_control(1046, S)` — the register's OWN
   current value — send it (fire-and-forget, the `_write_frame`
   seam), trigger, harvest the fresh 1046-block. Verify: `1046 ==
   S` AND **the whole 45-register block is unchanged** vs the
   baseline (the neighbor-diff alarm — a mis-aimed write must be
   caught here, before anything real is attempted). Verdict
   `no-op-held` / `no-op-neighbor-changed` (ABORT — the line lists
   the changed registers; the quickstart's remediation table owns
   the follow-up). Epistemics documented: phase A proves
   HARMLESSNESS (a dropped write looks identical — it does NOT
   prove execution; phase B does).
3. **Phase B — the execution diff-revert** (only after
   `no-op-held`): test value `T = S − 5` if `S − 5 ≥ 100` else
   `S + 5` (toward the range interior; a −0.5 °C dip on any
   realistic pool setpoint). Send `build_write_control(1046, T)`,
   trigger, harvest, require `1046 == T`. Then send
   `build_write_control(1046, S)` — the revert — trigger,
   harvest, require `1046 == S`.
4. The verdict, ONE line at WARNING (contract 012 §2):
   - `setpoint write probe: EXECUTED + REVERSIBLE (mirror 1046 followed <S>→<T>→<S>) — evidence for WRITE_SETPOINT_PROVEN`
   - failures, each naming the stage + the remediation:
     `not-executed` (…stayed <S> — "if the panel's setpoint
     changed unexpectedly, restore it from the panel or the
     vendor app"), `not-reversible` (…stayed <T> — "**the setpoint
     is now <T/10> °C — restore it from the panel or the vendor
     app**"), `att-error` (the chain verbatim), `no-report`,
     `refused-baseline`, `no-op-neighbor-changed`.
5. Return the stage dict (the observations, the frames sent, the
   verdict); disconnect on completion or failure (the probe
   convention); single-flight under `_io_lock`; the probe is the
   ONLY code that may write 1046 before the proof (its frames go
   through the client's own validated seam — FR-6 — NOT through
   the funnel; contract 016 §5's pattern).

**FR-6 — The client's setpoint seam + rail.** `aqua_client.py`
gains `async write_setpoint_probe_frame(self, value_raw: int)` —
the probe's ONLY send path (the `write_probe_frame` precedent):
- Rail: value must be an int in
  `[SETPOINT_MIN_RAW, SETPOINT_MAX_RAW]` and a multiple of
  `SETPOINT_STEP_RAW`, else ValueError BEFORE any byte (the frame
  is built, not string-pinned — the probe's values are dynamic);
  the frame is `modbus.build_write_control(WRITE_REG_SETPOINT,
  value_raw)` — the parity builder; the address field of the
  frame is asserted == 1046 (the shape rail).
- Send: the `_write_frame` seam, the session's resolved write mode
  (withoutResponse app parity — the 016 §5 parity table). NEVER
  `write_registers` (the echo trap — contract 017 §4).

**FR-7 — The service + button.** A service
`aqua_temp_ble.setpoint_write_probe` (the `power_write_probe`
precedent) and a diagnostic BUTTON "Setpoint write probe"
(category `diagnostic` — a measurement instrument, NOT a control
surface). Both route to FR-5. The button's description names the
physical effect: the setpoint dips 0.5 °C for ~2 minutes and
returns.

**FR-8 — The climate's read surface (this phase, honest by
default).** `climate.py` (push mode only):
- `target_temperature` returns the snapshot's `target_temp` (the
  pushed 1046 mirror decoded by FR-1) — displayed whenever the
  `push_control_confirmed` gate is open, INDEPENDENT of the write
  gate (the home is [P owner]; a read-only target display is the
  015 read-only pattern).
- `min_temp = 10.0`, `max_temp = 40.0`,
  `target_temperature_step = 0.5` [D — research §7; one source of
  truth with the guard's range].
- `TARGET_TEMPERATURE` is advertised ONLY while
  `write_setpoint_allowed` (FR-9) — with the gate closed the tile
  shows the target but offers no controls (the 015/017 surface
  discipline).
- `hvac_mode`/`hvac_action` UNCHANGED (018's power-wins display).
- `current_temperature` UNCHANGED (the pushed inlet mirror).

### Phase 2 — the guarded control surface (BLOCKED on evidence)

**FR-9 — The activation gate.** The THIRD gate, separate by
design (research §8 Q8): `coordinator.write_setpoint_allowed` =
`registers.WRITE_SETPOINT_PROVEN OR entry.options.get(
"write_setpoint", False)` (the `write_control` precedent —
017 FR-1's pattern, live-read so the option needs no restart).
A per-entry checkbox `write_setpoint` (default False) joins the
OptionsFlow; `DEFAULT_WRITE_SETPOINT = False` in `const.py`. **No
code path may ever set either surface** (contract 017 §2's rule,
verbatim). The option's description names the required verdict
line (FR-5 step 4) — the owner's act is the gate's cause.

**FR-10 — The setpoint write path (blocked by contract 019 §4).**
Behind the open gate, `coordinator` gains
`async_control_write_setpoint(self, celsius: float) -> dict` —
the confirmation cycle (017 FR-3's exact pattern, the 1046-block
as the mirror):
1. Validate: a finite value, a 0.5 multiple, inside 10.0–40.0 °C
   (else ValueError — never silently clamp a REGULATION value; the
   entity surfaces the error).
2. `raw = int(round(celsius * TEMP_SCALE))`;
   `ensure_writable(WRITE_REG_SETPOINT, raw, setpoint_allowed=
   self.write_setpoint_allowed)` — the funnel FIRST.
3. Connect if needed; audit INFO (`setpoint write via 1046 =
   <raw> — mirror should follow in the next report`); under
   `_io_lock`: `client.write_setpoint(raw)` (the rail of FR-6's
   successor — the control variant accepts the same range),
   `t_write = _now()`, ONE trigger (C-3's rule — the write does
   not wake the report; research 017 §2), the fresh-block wait
   (see FR-12).
4. The three verdicts (017 FR-4's strings, setpoint-flavored):
   `confirmed` (INFO — the fresh 1046 == raw), `not-confirmed`
   (WARNING — the mirror disagrees; BOTH values in the line),
   `unconfirmed` (WARNING — no fresh block; the line states the
   state is UNKNOWN; **NO persistent notification** — unlike an
   unconfirmed OFF, a failed setpoint write leaves the machine at
   its old target: no hazard; the WARNING + the tile's mirror-truth
   suffice [D — research §8 Q6]).
5. The snapshot push (confirmed / not-confirmed ONLY) —
   `from_pushes` of the live store; the tile follows the MIRROR,
   never the request (contract 016 §6). NO snapshot on
   `unconfirmed`.
6. The stage dict (`value_raw`, `verdict`, `mirror_raw`,
   `mirror_age_s`) → `last_setpoint_write` → the climate's
   `extra_state_attributes` (the `last_power_write` precedent).

**FR-11 — The climate's write surface (blocked by the same gate).**
`async_set_temperature` (push mode, gate open): validate →
FR-10 → return. Gate closed → `_refuse_write`'s exact 015 message
(byte-identical). The write is allowed REGARDLESS of power state
[P owner — the panel changed the setpoint with the unit off;
research §1]. The classic entity's `async_set_temperature` is
UNTOUCHED (base reg 14 — spec 003's plane).

**FR-12 — The fresh-block wait, generalized.** The coordinator's
`_wait_fresh_control(before)` becomes `_wait_fresh_block(start,
before)` (the 2001-block call sites pass
`PUSH_CONTROL_BLOCK_START`; the setpoint paths pass 1046's block
start; the probe's helpers use the same). The 016/017 suites' log
lines are unchanged (the refactor is a pure parameterization —
the 017 T3 precedent).

## 3. Non-functional requirements

**NFR-1 — No behavior change by default.** With both setpoint
gate surfaces off (shipped), the integration is byte-identical to
0.13.2 except: the new constants/self-test rows, the probe
service/button (inert until pressed), and the READ wiring of
FR-1 (the target display appears behind the already-confirmed
control gate — the honest display of an owner-proven register;
the ACs pin the read gate).

**NFR-2 — One source of truth.** The frames come from
`modbus.build_write_control` (parity-asserted against research
§6's pinned vectors in the self-test — a mismatch fails it). The
range/step constants live once in `registers.py`; the climate's
`min_temp`/`max_temp`/step read them.

**NFR-3 — The evidence discipline.** Every new constant carries
its tag (the registers.py convention). The family map ships as
DATA ([P] with its [V] key) — never reachable by a write path.

**NFR-4 — No new dependencies.** The probe reuses the client,
harvest, trigger, service/button plumbing that exists.

**NFR-5 — English-only artifacts** (repo rule; this spec included).

## 4. Acceptance criteria

- **AC-1** The modbus self-test asserts
  `build_write_control(1046, 300) ==
  bytes.fromhex("63100416000102012c5049")` and the 29.5 row
  `…0127… == bytes.fromhex("631004160001020127118e")`, plus the
  `build_write_registers` cross-check (FR-3).
- **AC-2** With both setpoint gate surfaces False:
  `ensure_writable(1046, 300)` raises ValueError; the climate
  advertises NO `TARGET_TEMPERATURE`; `async_set_temperature`
  refuses with the 015 message; the 015/016/017 suites pass
  unchanged (NFR-1 — the no-regression gate).
- **AC-3** The guard's value discipline: with the gate open
  (test-patched), `ensure_writable(1046, 100)`, `(1046, 400)`,
  `(1046, 295)` pass; `(1046, 99)`, `(1046, 401)`, `(1046, 297)`
  (not a 0.5 multiple) raise; `(1045, 300)` and `(1047, 300)`
  raise (address allowlist); `(1011, 0|1)` behaves exactly as
  0.17 (the power rules untouched).
- **AC-4** `from_pushes` decodes `target_temp` from the
  1046-BLOCK capture (a scripted store with only that block
  fresh: target decodes; with the 2001 block only: target is
  None — the block-source fix of FR-1).
- **AC-5** The probe, scripted `1046: S → (no-op write) → S
  (block unchanged) → (T write) → T → (S revert) → S`, returns
  `executed+reversible` and logs the WARNING verdict line with
  the `<S>→<T>→<S>` raws (the 016 fake-client precedent).
- **AC-6** The probe's phase A: a scripted NEIGHBOR change during
  the no-op → verdict `no-op-neighbor-changed`, the line lists the
  changed register(s), and NO phase-B frame was sent (the fake
  records exactly one write — the no-op).
- **AC-7** The probe's failure verdicts: `not-executed` (1046
  stays S after the T write), `not-reversible` (1046 stays T after
  the revert — the line carries the "restore it from the panel"
  remediation and the CURRENT value), `refused-baseline` (a
  sentinel/absent/out-of-range S — zero writes sent),
  `no-report` (no fresh block — the remediation names the panel
  check).
- **AC-8** The probe sends ONLY `build_write_control(1046, …)`
  frames — 11 bytes, qty 1, bytecount 02 — and only values from
  {S, T} (the fake's write recording; the rail test: a probe
  attempt with an out-of-range S aborts BEFORE any write).
- **AC-9** (phase 2, blocked) With the gate open:
  `async_set_temperature(30.0)` writes the FR-3 frame through the
  funnel, the confirmation cycle runs, and the tile's target
  follows the MIRROR (a scripted mirror mismatch → the WARNING
  names both values and the tile shows the MIRROR's truth — never
  the request). `async_set_temperature(30.3)` raises (step);
  `(41.0)` and `(9.5)` raise (range); `(30.0, gate closed)` →
  the 015 refusal. This AC MUST NOT be merged before the owner's
  probe verdict is recorded (contract 019 §4).
- **AC-10** The activation surfaces: the option checkbox joins
  the OptionsFlow (default False); `write_setpoint_allowed` ORs
  the two surfaces live; no code path sets either (the tests
  assert the property reads options + the registers switch and
  nothing else).
- **AC-11** The whole existing suite passes with the gates
  closed (013/014/015/016/017/018 — zero regression); the probe's
  verdict lines and the FR-12 refactor keep the 016/017 log
  strings byte-identical.

## 5. Out of scope

- The cool/auto write addresses (1051/1068) — this unit is
  heat-only; a multi-mode unit needs its own spec (the family map
  documents the candidates — research §4.1).
- The real per-mode bound registers (1050/1054 [A-weak]) — a
  bounds-diff at the panel is a future spec; the static 10.0/40.0
  guard stands until then.
- The mode write (1012-1013), the mute (1076), the parameter-page
  writes — unchanged exclusions (contracts 016 §4, 017 §6).
- The logcat oracle, E5 (HCI), the family-key semantics (U-4) —
  research items, not control blockers.
- Any change to the classic plane's setpoint path (base reg 14 —
  spec 003's territory).