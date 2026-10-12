# Spec 017 research — the controlled climate: opening the proven write path

Date: 2026-10-12
Evidence base: **the owner's PRODUCTION probe run** (the shipped
0.12.0 probe, the owner's real unit `08:06:A6:C0:94:1C`, 2026-10-12 —
the logs quoted verbatim in §1); the owner's physical confirmation
(§1); the shipped code @ 0.12.0 (`registers.py`, `coordinator.py`,
`aqua_client.py`, `climate.py`, `const.py`, `config_flow.py`,
`modbus.py`); specs 013/014/015/016 (every inherited claim stays
tagged there); the raw disassembly (this session — the mode handler
fully decoded, §5) from the surviving blutter artifacts
(`/tmp/flutterapp/lib/arm64-v8a/libapp.so`, `/tmp/blutter_out/`,
`/tmp/disasm.py` — all re-verified present this session).

Status legend (the repo convention): **[P]** PROVEN (disassembly with
addresses / arithmetic / a cited spec / the owner's recorded
production logs), **[P owner]** the owner's observation,
**[P apk]** recovered from the APK, **[V]** TO-VERIFY (one named
experiment decides), **[A]** ASSUMED (reasonable, cheap to correct),
**[D]** DECISION.

---

## 0. TL;DR

> **The proof exists. The power write is PROVEN to control the real
> machine — the mirror followed 0→1→0 AND the owner watched the pump
> start and stop. Contract 016 §4's precondition is SATISFIED (§1).
> This spec opens phase 2: the guarded, mirror-confirmed power control
> on the push-mode climate.**
>
> What phase 2 ships:
> - `turn_on` / `turn_off` / `set_hvac_mode(OFF|HEAT)` → the two
>   PROVEN frames (`631003f30001020001f131` / `…0030f1`), through the
>   funnel guard, fire-and-forget on the ff03 bucket — byte parity
>   proven at the builder level (§6).
> - A post-write CONFIRMATION CYCLE: write → ONE trigger →
>   fresh-mirror wait → cross-check → snapshot push. The climate tile
>   follows a command in ~8-10 s typical (§2/§4 Q3), never
>   optimistically — the mirror decides (contract 016 §6).
> - The activation stays the OWNER's explicit act (a per-entry option
>   OR the shipped calibration switch) — never automatic (§4 Q1).
> - The MODE and the SETPOINT are NOT pilotable — and this session's
>   disassembly of the mode handler adds a decisive reason: the
>   vendor's own mode write is a TWO-register pair (1012+1013) that
>   also writes a setpoint-shaped value, and the app REFUSES mode
>   changes on this heat-only unit (§5). Read-only mode display
>   (2019), no setpoint UI (§4 Q4/Q5).

---

## 1. THE DECISION LOG — the production proof (contract 016 §4 SATISFIED)

The owner ran the shipped 0.12.0 power-write probe (spec 016 FR-4,
quickstart 016 §1) on his real machine. His log lines, verbatim:

```
power write probe [08:06:A6:C0:94:1C]: baseline: mirror 2011 = 0 (t+5.2s)
power write probe [08:06:A6:C0:94:1C]: sending power-ON frame
  631003f30001020001f131 on 0000ff03-...b34fb
power write probe [08:06:A6:C0:94:1C]: after ON: mirror 2011 = 1 (t+33.4s)
power write probe [08:06:A6:C0:94:1C]: sending power-OFF frame
  631003f3000102000030f1 on 0000ff03-...b34fb
power write probe [08:06:A6:C0:94:1C]: after OFF: mirror 2011 = 0 (t+61.5s)
power write probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0->1->0) —
  evidence for phase 2 (WRITE_CONTROL_PROVEN)
```

**And the owner's physical confirmation [P owner]**, verbatim:
*« ca s'est bien allumee et quand j'ai rappuye ca s'est eteint »*
("it really turned on, and when I pressed again it turned off").

The verdict satisfies contract 016 §3/§4 on every axis:

| Contract condition | Evidence |
|---|---|
| The frames are the disassembly-pinned writes (1011 = 1/0) | the exact pinned hex in the log lines [P] |
| The mirror (2011) followed the ON write | `after ON: mirror 2011 = 1 (t+33.4s)` [P] |
| The mirror followed the OFF revert | `after OFF: mirror 2011 = 0 (t+61.5s)` [P] |
| Both directions (executed AND reversible) | the verdict line: `EXECUTED + REVERSIBLE (mirror 2011 followed 0->1->0)` [P] |
| Logged at WARNING, the owner's log page | the verdict line format = quickstart 016 §2's exact string [P] |
| The owner's physical cross-check (beyond the contract) | the pump audibly/visibly started then stopped [P owner] |

**Hypothesis ledger closures (016 §7):**
- **W-5 (H-EXECUTES) — CLOSED [P]:** the firmware executes OUR
  power frame from OUR transport.
- **W-6 (H-APP-BOND-GATE) — DEAD [P]:** the probe ran on the
  production Pi over the plaintext (unbonded) link; the command
  executed and reverted. No firmware-level bond gate exists for
  command execution.
- **016 §8 U-1 — CLOSED.** The write path is proven live.

**This section is the contract 016 §4 record:** the owner's verdict
line, quoted verbatim above, is the evidence that authorizes the
guard activation. Phase 2 may be implemented and merged on the
strength of it (spec 017 AC-1..AC-11 are not blocked; 016's parked
task T7 is superseded by this spec's tasks — see tasks.md).

---

## 2. The 33.4 s decoded — the probe's robust order, not the machine's latency

The brief asks (Q8): why did the mirror take ~33 s to show ON? The
answer is in the probe's own code order (016 FR-4 step 4, contract
016 §3) + the production timestamps. Stage-by-stage reconstruction
(all times relative to probe start; the logged `t+N.Ns` is the
capture's `last_seen`, not the stage's return time):

| Stage | What happens | Clock (reconstructed) |
|---|---|---|
| connect | connect + subscribe + 2 s settle | t+0 → ~t+3 |
| trigger | the blocking base read (8 s window) goes out | ~t+3.0 |
| burst | the report burst lands (M-2's 0.2-5 s band) | **t+5.2** ← `baseline` |
| read ends | the FC03 timeout returns (no answer ever comes) | ~t+11 |
| **ON write** | the pinned ON frame, withoutResponse | ~t+11.1 |
| fresh-wait | 20 s window (`PROBE_MIRROR_TIMEOUT`) — **nothing arrives** | t+11.1 → t+31.1 |
| retry trigger | ONE base read (the robust order, 016 FR-4 step 4) | ~t+31.1 |
| burst | the fresh burst lands ~2.3 s after the trigger | **t+33.4** ← `after ON` |
| **OFF write** | ~t+39 (after the read timeout + logging) | ~t+39.2 |
| fresh-wait | 20 s — nothing; retry trigger ~t+59.2 | |
| burst | fresh ~2.3 s later | **t+61.5** ← `after OFF` |

Two numbers matter:

1. **The gap write→mirror is ~22.3 s at BOTH stages, and ~20 s of
   it is the probe's own dead wait** (the fresh-mirror window before
   the retry trigger). The mirror's true trigger-to-burst latency is
   **~2.2-2.3 s** — exactly M-2's band (0.2-5 s after a TX, spec 014
   [P six runs]).
2. **The FC16 power write did NOT observably elicit a fresh 2001
   block within its 20 s window — at either stage.** The base-read
   trigger did, both times, in ~2.2 s [P — the production timestamps,
   two independent stages]. 016's research §4 Q4 assumed "the write
   itself is a valid request frame — it should trigger a report";
   the production timing does NOT support that assumption for the
   power write. (Scope honesty: we cannot exclude a write-triggered
   burst arriving later than 20 s; the claim is bounded by the
   window. It does not matter for the design — C-3 below.)

**New hypothesis C-3 [P, scoped]:** the report is woken by the
base-read trigger (~2.2 s), not by the FC16 power write (≤20 s
window, both stages silent). Consequence for phase 2: the
confirmation cycle MUST send its own trigger immediately after the
write — never rely on the write to wake the report (FR-3), and never
copy the probe's wait-first order (that 20 s dead wait exists for
the probe's evidence purity, not for control latency).

The state CHANGE itself is physically immediate [P owner — the pump
started on the ON write]; only the REPORT is request-driven. The
remediated design lands the confirmation at **~8-10 s typical**
(the blocking 8 s trigger read; the burst lands ~2 s in, then the
fresh-wait returns instantly), **~28 s worst case** (a burst that
only answers the next trigger).

---

## 3. The owner's new problem, triaged — "it never showed heating while it was on"

The owner reports (verbatim): *« quand c'est allume, cela n'a jamais
change le statut en chauffage (peut-etre du au polling) »* ("while it
was on, the status never changed to heating — maybe due to the
polling"). Three candidate causes; the triage:

1. **The probe/poll interplay — THE cause of this observation
   [P — code reading].** The probe NEVER updates the coordinator
   snapshot: it only logs (016 FR-4 returns a dict; nothing touches
   `coordinator.data`). It holds `_io_lock` for its ENTIRE ~62 s
   run, so no poll cycle can run mid-probe. The unit was OFF again
   before the next cycle — the climate could never see the ON
   state, at any poll interval. This is by design: the probe is a
   measurement instrument (016 FR-7), and the climate's state comes
   only from poll snapshots (spec 001's rule). Not a bug — but it
   is exactly the gap phase 2 closes: a command through the
   controlled climate gets its OWN confirmation cycle and snapshot
   push (FR-3), so the state follows the command in seconds.
2. **The confirmation gate `push_control_confirmed` [V — owner-side,
   cannot be read from here].** In push mode the climate entity is
   created ONLY behind the gate (015 FR-8: `push_control_confirmed`
   option OR `registers.PUSH_CONTROL_CONFIRMED`). The owner's
   phrasing ("the status never changed") implies he was watching a
   climate entity, which implies the option is checked on his
   machine — but that lives in his HA config, not in any artifact
   here. Quickstart 017 §0 step 1 has him VERIFY it (one glance at
   the Configure dialog). If it were unchecked there would be no
   entity at all.
3. **The mode mapping 2019 = 4 → `heat` [A — 015 D-3].** Not the
   cause this run (no snapshot ever saw the ON state), but it
   matters in phase 2: when the unit is on, the fresh 2001 block
   should show 2019 = 4 (the 015 owner diff-revert: 0→4→0 across the
   same power toggle [P owner]) → `hvac_mode` HEAT. The mapping is
   [A] (this unit is heat-only — H06 [P panel] + the app's own
   mode lock [P apk]); an unmapped value renders `unknown` + ONE
   warning per session (contract 015-A §5) — honest, correctable in
   one line. The climate's `hvac_action` stays None while on (the
   compressor binary O01 is uncalibrated [V]) — the quickstart says
   so, so an empty activity pill is not read as a failure.

**Verdict:** the observation is expected behavior of the proof
instrument, not a defect; the controlled climate's confirmation
cycle (FR-3) is the piece that makes the status follow the command.

---

## 4. The brief's eight questions — verdicts

### Q1 — How is phase 2 opened, properly?

**The proof is recorded (§1) — the contract's precondition. The
activation is the OWNER's explicit act — never automatic [D].**

- The repo's philosophy is "a proof BEFORE an authorization": the
  proof is the NECESSARY condition; the owner's act is the
  sufficient one. Every shipped calibration switch follows this
  (`PUSH_TEMP_CONFIRMED`, `PUSH_CONTROL_CONFIRMED`, `pair_on_connect`)
  — none is ever auto-set, and contract 016 §6 says it plainly:
  "the climate exposes write capability ONLY while
  `WRITE_CONTROL_PROVEN` is True" — the OWNER must have set it.
- Two activation surfaces, both honored (FR-1):
  1. **A per-entry option `write_control`** (checkbox, default OFF)
     in the integration's Configure dialog — the
     `push_control_confirmed` precedent (config_flow's OptionsFlow);
     saving the option reloads the entry, NO HA restart needed.
  2. **The shipped global switch `registers.WRITE_CONTROL_PROVEN`**
     (016 T3, default False) — the `WRITE_ECHO_EXPECTED`
     calibration-switch precedent; requires an HA restart.
  The coordinator ORs them (the `push_temp_confirmed` property
  pattern): `write_control_allowed = registers.WRITE_CONTROL_PROVEN
  or options.get("write_control", False)`.
- Why not auto-flip after the verdict: (a) a write capability that
  appears by itself after an update violates least-surprise and the
  contract's own letter; (b) the owner may want the probe's proof
  recorded before exposing control to his household automations;
  (c) the option path gives a UI-only, no-restart, instantly
  reversible activation (quickstart §1/§4).
- The Coder ships EVERYTHING behind the gate, inert by default: with
  both surfaces off the shipped behavior is byte-identical to 0.12.0
  (AC-10) — the same discipline 016 used for its probe.

### Q2 — The complete climate: which commands, which frames?

PROVEN frames only (both directions live-proven, §1):

| Command | Register write | Frame (hex) | Status |
|---|---|---|---|
| `turn_on` / `set_hvac_mode(HEAT)` | 1011 = 1 | `631003f30001020001f131` | **[P]** |
| `turn_off` / `set_hvac_mode(OFF)` | 1011 = 0 | `631003f3000102000030f1` | **[P]** |

- The write goes through the funnel guard (`ensure_writable(1011,
  value)` — 016 T3's address+value allowlist, FR-6 below), then
  fire-and-forget on the ff03 bucket, `withoutResponse` (app parity;
  the echo question — §6).
- `hvac_modes = [OFF, HEAT]` (heat-only unit — no COOL/AUTO; the
  mode write is out, Q4). `set_hvac_mode(HEAT)` is a pure power ON;
  `set_hvac_mode(OFF)` a pure power OFF. Idempotent: a same-state
  command writes anyway (harmless) and the mirror confirms — simpler
  and safer than a read-first check.
- NO setpoint write, NO mode write, NO mute write (Q4/Q5) — 016
  FR-8's conservatism carried forward as a hard rule.

### Q3 — The refresh after a command (the design)

`async_request_refresh` alone is NOT sufficient:
- it is debounced (a coalesce window before the cycle starts), and
- the poll cycle snapshots the pushed store at trigger-return; a
  2001 block completing after that snapshot copy stays invisible
  until the NEXT cycle — a cross-check against that snapshot could
  compare a STALE mirror and log a false "not confirmed".

So the design is a dedicated **post-write confirmation cycle**
(FR-3), reusing the probe's PROVEN helpers:

```
guard:  ensure_writable(1011, value)                  # the funnel, first
write:  client.write_control(1011, value)             # fire-and-forget, §6
trigger:ONE base read (the proven trigger, ~2.2 s to burst; C-3: the write alone does not wake it)
wait:   _wait_fresh_control(t_write)                  # fresh 2001: last_seen > t_write
check:  fresh raw 2011 == value ? → INFO confirmed / WARNING mismatch
push:   from_pushes(live store, gates…) → async_set_updated_data   # the tile follows the MIRROR
```

- The freshness gate (`last_seen > t_write`) is the probe's oracle
  discipline (contract 016 §3): the mirror that confirms must
  postdate the write.
- The snapshot is built from the LIVE store (`client.pushed_blocks`)
  — the same builder the cycle uses, so no second decode path.
- **Never optimistic** (contract 016 §6): the tile shows what the
  MIRROR says, even on mismatch (a mismatch logs the WARNING and
  the tile shows the mirror's truth).
- Latency: ~8-10 s typical (the blocking 8 s trigger read; the burst
  lands ~2 s in; the fresh-wait returns instantly), ~28 s worst.
  The command itself returns when the WRITE is done (the
  confirmation is part of the same method — the UI waits at most
  one cycle; the service call is bounded and single-flight via
  `_io_lock`).

### Q4 — Is the mode pilotable? (the brief said: verify in the disassembly)

**NO — on this unit, and this session's disassembly makes it
decisive (§5).** The app HAS a mode frame [P — the handler decoded
this session]: it writes the PAIR 1012-1013 with
`[mode_value, per-mode setpoint ×10]`. Therefore:

1. A mode write would ALSO write register 1013 with a
   setpoint-shaped value whose semantics are UNPINNED — out of
   contract (contract 016 §4's non-transference: the power proof
   does not transfer; an unpinned pair register is unwritable).
2. The app's own guard REFUSES mode changes on heat-only units
   (field_0x6b != 1 → the "当前模式为单热模式…" mode-lock dialog
   [P apk i18n + P disassembly control flow]) — the vendor's own
   operating envelope excludes this unit.
3. The write-plane mode value space (UI index 0/1/2 — cool/heat/auto
   [A]) and the read-plane mode value (2019 = 4 when on [P owner])
   do not even share a proven mapping — the mode is unproven in BOTH
   directions on this unit.

The mode stays READ-ONLY: `hvac_mode` displays from 2019 via
`PUSHED_MODE_VALUE_TO_NAME` ([A] 4 → heat, correctable one line;
unmapped → unknown + one warning per session). `set_hvac_mode`
exposes OFF/HEAT only — both pure power writes.

### Q5 — Is the setpoint pilotable?

**NO — and it is not even displayed.** No pushed read home exists
[P — the 015 whole-plane scan: no setpoint-shaped value in any
block; the 1046-ladder candidates are [A-weak], never confirmed by a
diff]; the app's setpoint WRITE address is RUNTIME [V] (a
state-field chain — 016 §5's map; the value is temp×10 [P shape]).
The climate therefore advertises NO `TARGET_TEMPERATURE` feature,
`set_temperature` refuses loudly, and no target is displayed — no
fake button, no invented number. The honest user story (quickstart
§5): on/off from HA; the setpoint stays on the panel/vendor app; the
named follow-up is the logcat oracle (quickstart 015 §2) + a future
spec with its own §2/§3 evidence (its own pinned frame, mirror and
proof — contract 016 §4).

### Q6 — The write guard: the exact design for 1011

The guard ALREADY SHIPS (016 T3, done): `ALLOWED_WRITE_CONTROL =
{1011: frozenset({0, 1})}` behind `WRITE_CONTROL_PROVEN`, with
`ensure_writable(address, value=None)` threading the value through
the funnel (`async_write_register` calls it FIRST — before any lock
or I/O). **Phase 2 ACTIVATES it; it does not extend it.** The exact
behavior matrix with the gate OPEN:

| Call | Result |
|---|---|
| `ensure_writable(1011, 0)` / `(1011, 1)` | allowed (the proven values) |
| `ensure_writable(1011, 2)` | **ValueError** — "accepts only values [0, 1] (contract 016 §4 — the value allowlist is part of the guard)" |
| `ensure_writable(1012, 1)` (mode) | **ValueError** — "not writable (spec 003 §6)" — not extended, needs its own proof |
| `ensure_writable(2011, 0)` (a read home) | **ValueError** — never write a read home (contract 016 §2) |
| `ensure_writable(1076, 1)` (mute) | **ValueError** — [V] semantics, not extended |
| `ensure_writable(8/13/14, …)` | EXACTLY as 0.11.0 (base rules untouched — AC) |

**What happens if something writes another value:** the guard
raises BEFORE any lock or I/O — no byte reaches the wire. The value
allowlist is permanent (extending it requires a new contract). With
the gate CLOSED the whole control plane refuses with the 016 message
(byte-identical to 0.12.0 — the 016 suite pins it). The probe's own
pinned seam (`write_probe_frame`) stays separate and unchanged
(contract 016 §5); the climate's send path adds a CLIENT-side rail
of its own (FR-2: only `(1011, 0|1)` may pass — defense in depth
against future misuse of the new client method).

### Q7 — The safety of remote control (the guardrails)

The risk is real: a remotely-started heat pump (circulation pump +
compressor) running unattended — at night, on vacation, or after a
stray automation call — and the inverse hazard, a FAILED OFF
leaving it running silently. The proposed guardrail set (FR-7 —
proportionate, no over-engineering):

1. **The gate** (FR-1): opt-in per-entry option or the global
   switch; never auto-enabled; instantly reversible (uncheck →
   reload → the write surface is gone).
2. **The allowlists** (016's guard, unchanged): one register
   (1011), two values (0/1), forever; no generic write surface.
3. **The audit line** (FR-4): one INFO line per write naming the
   register, the value and the expectation — every command is
   attributable in the log page.
4. **The mirror confirmation** (FR-3): never optimistic; a mismatch
   or a silent report logs a WARNING naming both values.
5. **The unconfirmed-OFF surface** (FR-3 step 6): a FAILED or
   unconfirmed OFF raises a PERSISTENT NOTIFICATION ("the unit's
   state is UNKNOWN — check the panel") — the direct implementation
   of contract 016 §7's revert obligation ("told, never silently");
   the notification clears on the next confirmed cycle.
6. **What is deliberately NOT built [D]:** no time-window lockouts,
   no confirmation dialogs per command, no rate limiting — the
   single-owner integration does not need HA-level friction on top
   of HA's own service-call audit trail; the unit's own controller
   (H-family protections, H01 power-down memory, flow switch)
   governs its internal safety exactly as it does when the vendor
   app toggles the same register. The remote-start risk is
   DOCUMENTED (quickstart §5) rather than engineered around.

### Q8 — The 33 s latency: explanation and remediation

§2 above. Summary: the ~22-28 s gaps are the probe's own robust
order (a 20 s fresh-wait BEFORE the retry trigger — an
evidence-purity choice, not a control necessity); the write does
not observably wake the report (C-3); the trigger wakes it in ~2.2 s;
the state change is physically immediate [P owner]. Remediation
(FR-3): the confirmation sends its trigger IMMEDIATELY after the
write → the tile follows in ~8-10 s typical.

---

## 5. The mode handler, fully decoded (NEW disassembly evidence, this session)

The vendor app's home-screen MODE handler (closure 0x6a2318 → inner
0x6a2378, the `[frk] _dR` class — 016 §1.2's call site 12),
disassembled in full this session. The complete control flow:

```
0x6a2398-0x6a23a4:  state = context.field_0x17            [P]
0x6a23a8-0x6a23bc:  GUARD 1: state.field_0x9b (master/slave) —
                     slave state → dialog, ABORT, no write    [P]
                     (the same guard as the power handler — 016 §2)
0x6a2400-0x6a2418:  if state.field_f == requested mode →
                     RETURN (same-mode: no write)            [P]
0x6a241c-0x6a2454:  GUARD 2: state.field_0x6b == 1 required —
                     else the mode-lock DIALOG (0x6a2604:
                     pool strings → dialog builder 0x396438,
                     the "当前模式为单热模式，不可设置为其他模式！"
                     i18n cluster [P apk]) — ABORT           [P]
0x6a2458:           state.field_f = requested   ← the app's own
                     OPTIMISTIC local update                 [P]
0x6a245c-0x6a2480:  per-mode setpoint selection: the double at
                     state.field_0x23 / 0x2b / 0x33 for mode
                     0 / 1 / 2                               [P]
0x6a2488-0x6a2514:  setpoint × 10 → int → toRadixString(16) →
                     padLeft(4,"0") — the temp×10 hex word    [P]
0x6a2550-0x6a2568:  builder(session, addr=0x3F4 /*1012*/,
                     qty=2, bytecount="04", value=state.field_f,
                     stack-arg = temp×10) → 0x64fc0c          [P]
0x6a2570-0x6a2588:  sender(session, frame) → 0xa14ea8 → ff03  [P]
```

**The mode frame [P shape]:** `6310 03F4 0002 04 <mode:4>
<setpoint×10:4> <crc4>` — 13 bytes, writing registers **1012 AND
1013 as one FC16 pair**: `[mode_value, per-mode setpoint ×10]`.

Three consequences (Q4's verdict, restated):

1. **A mode write is a PAIR write that also writes 1013 with a
   setpoint-shaped value.** Register 1013's semantics are unpinned
   ([P shape / A semantics] — 016 §5); writing it is out of
   contract on this unit (the setpoint home is unidentified; a
   wrong ×10 value could alter a real machine setting). The
   non-transference rule (contract 016 §4) forbids it.
2. **The vendor's own app refuses mode changes on this unit**
   (GUARD 2 + the heat-only i18n) — there is no vendor-sanctioned
   mode write to reverse-engineer here.
3. **The app is optimistic** (state.field_f updated before the
   write) — the vendor trusts its UI; WE will not (contract 016
   §6: the mirror decides). A useful contrast for the record.

Reproduce: `/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a2318
0x6a2600` and `… 0x6a2604 0x6a2650` (the mode-lock dialog helper).

---

## 6. The funnel parity + the echo question [P arithmetic]

**Parity.** `modbus.build_write_registers(1011, [v])` builds
`payload = struct.pack(">HHB", 1011, 1, 2)` (= `03F3 0001 02`) +
`struct.pack(">H", v)` (= `0001`/`0000`) + the same `_frame`
plumbing (slave 0x63, FC 0x10, CRC16-Modbus little-endian) —
byte-identical to `modbus.build_write_control(1011, v)` and to the
two pinned strings (016 §2.1, CRC-verified). The FUNNEL therefore
produces the proven frames; the 017 suite pins this as a test
(AC-1: `build_write_registers(1011, [1]) == build_write_control(1011,
1) == bytes.fromhex(WRITE_FRAME_POWER_ON)`).

**The echo — a trap the design must avoid.** The shipped
`WRITE_ECHO_EXPECTED = True` is a CLASSIC-path calibration switch
(runbook step W): `client.write_registers` then WAITS up to 3 s for
an FC10 echo and RAISES `AquaTempTimeoutError` if none comes. If
the control write reused `write_registers`, every 1011 write would
wait 3 s and then raise — **misreporting a successful, proven write
as a failure** (the unit executes without-response writes; no FC10
echo has ever been observed [P — the probe's production run never
waited for one and got EXECUTED + REVERSIBLE]). Therefore:

- **[D] The control plane sends fire-and-forget** (a dedicated
  `client.write_control(address, value)` on the SAME `_write_frame`
  seam — withoutResponse, app parity; contract 016 §5's seam rule),
  and **the MIRROR is the confirmation, never an echo.**
- Contract 017 §4 records this as the amendment to contract 016
  §5's wording: the "single funnel" is the GUARD
  (`ensure_writable`) + the SEAM (`_write_frame`); the echo
  expectation is a classic-path transport detail that must not leak
  into the control plane.

---

## 7. The hypothesis ledger (spec 017, C-series)

| # | Statement | Status |
|---|---|---|
| C-1 | The pinned power frames control the real unit (write 1011 = on/off) | **[P — §1's verdict + the physical confirmation]** |
| C-2 | The plaintext (unbonded) link executes commands — no firmware bond gate | [P — the probe ran unpaired, executed + reversible] |
| C-3 | The base-read trigger wakes the report in ~2.2 s; the FC16 power write does not observably wake it within 20 s | [P, scoped to the 20 s window — §2] |
| C-4 | The confirmation cycle (write → trigger → fresh-mirror wait → cross-check → snapshot) yields honest state in ~8-10 s typical | [D — the design; quickstart §2's live test measures it] |
| C-5 | When on, 2019 = 4 → `hvac_mode` HEAT | [A — the 015 diff-revert + the heat-only unit; one-line correctable] |
| C-6 | The mode is not pilotable on this unit (pair-write hazard + the vendor lock) | [D — §5] |
| C-7 | The setpoint is not pilotable and not displayed (no read home [P scan], no write address [V]) | [D — Q5] |
| C-8 | The mode write's UI index space (0/1/2) maps to cool/heat/auto | [A — the per-mode setpoint order; never used by this spec] |

## 8. What remains unproven (and why it does not block phase 2)

| # | Gap | Status | Named decider |
|---|---|---|---|
| G-1 | The mode enum's semantics on either plane (the write UI index; the read value 4's full space) | [A] | a unit that allows mode changes (none here — heat-only) or the logcat oracle; NOT this spec |
| G-2 | The setpoint write address + the setpoint read home | [V] / [P-absent] | the logcat oracle (quickstart 015 §2) + a setpoint diff; a future spec |
| G-3 | The mute register 1076's semantics | [V] | a mute diff (LOW — no climate surface) |
| G-4 | Whether 1011 in the pushed 1001 block mirrors command or state (016 U-4) | [V] | a future probe capture (the owner's probe logs did not include the 1001 raws) |
| G-5 | Whether a power write elicits a burst beyond the 20 s window | [V — C-3's scope] | irrelevant to the design (the confirmation triggers explicitly) |
| G-6 | The compressor/O01 binary (the climate's `hvac_action` while on) | [V] | the O-family calibration (spec 003's path) |
| G-7 | The long-run cadence/reliability of the confirmation cycle (many cycles, radio fades) | [V] | the production logs after quickstart §2 |

## 9. Reproduction

```bash
# the mode handler (this session's §5 evidence):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a2318 0x6a2600
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a2604 0x6a2650   # the mode-lock dialog
# the frame builder + the power handler (016's evidence, unchanged):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x64fc0c 0x64ff30
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a15c8 0x6a1830
# the funnel parity (§6) — assert in any Python shell:
#   import modbus, registers
#   modbus.build_write_registers(1011, [1]) == modbus.build_write_control(1011, 1) \
#     == bytes.fromhex(registers.WRITE_FRAME_POWER_ON)
```

The §1 production log is the owner's own output (relayed verbatim in
this spec's brief); the §2 reconstruction is arithmetic on those
timestamps + the shipped code's stage order (`coordinator.
async_power_write_probe`, `const.PROBE_MIRROR_TIMEOUT = 20.0`,
`REQUEST_TIMEOUT = 8.0`).