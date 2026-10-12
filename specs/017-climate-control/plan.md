# Spec 017 plan — the controlled climate

Date: 2026-10-12
The evidence and every decision's rationale: `research.md`. The
normative rules: `spec.md` + `contracts/climate-control-contract.md`.
The owner's runbook: `quickstart.md`. The Coder's order:
`tasks.md`.

## 0. What changed in the world (why this plan is now possible)

1. **The proof exists** (research §1): the owner's production probe
   returned `EXECUTED + REVERSIBLE` (mirror 2011 followed `0→1→0`)
   AND he physically watched the pump start and stop. Contract 016
   §4's precondition is satisfied and recorded. The write plane is
   no longer hypothesis — it is the most proven part of the whole
   stack (frame [P disassembly], delivery [P six runs], execution
   [P live], revert [P live]).
2. **The report model is calibrated** (research §2): the trigger
   wakes the report in ~2.2 s; the FC16 write does not observably
   wake it. The probe's ~22-28 s stage gaps were its own robust
   order (a 20 s evidence-purity dead wait), not machine latency.
   So a fast, honest post-write confirmation is a SOLVED problem:
   trigger immediately, wait for the fresh mirror (~8-10 s typical).
3. **The mode question is closed by evidence** (research §5): the
   vendor's own mode write is a two-register pair that also writes a
   setpoint-shaped value to 1013, and the app refuses mode changes
   on this heat-only unit. The mode stays read-only — no speculation
   needed.
4. **The echo trap is identified before it bit** (research §6):
   `write_registers` + `WRITE_ECHO_EXPECTED=True` would raise a 3 s
   echo timeout on every control write. The control send gets its
   own fire-and-forget method on the same seam; the mirror is the
   confirmation (contract 017 §4).

The plan converts those four facts into: an owner-activated gate, a
guarded fire-and-forget send, a confirmation cycle that makes the
state follow a command in seconds, and a climate surface that tells
the truth about what it can and cannot do.

## 1. The activation design (FR-1)

```
registers.WRITE_CONTROL_PROVEN (016's switch, restart)   ─┐
                                                            ├─ OR → coordinator.write_control_allowed
entry.options["write_control"] (new checkbox, reload)     ─┘
```

- The property follows `push_control_confirmed`'s pattern exactly
  (one line, one precedent, tested both ways — AC-4/AC-5).
- The checkbox follows `push_control_confirmed`'s OptionsFlow
  pattern (config_flow.py's existing schema + strings/translations);
  its description names the required verdict (contract 016 §4's
  discipline, surfaced to the UI).
- Never automatic (research §4 Q1's three reasons); the shipped
  default is inert (AC-10/AC-3).

## 2. The write path (FR-2, contract 017 §3/§4)

```
climate command
  → coordinator.async_control_write_power(value)
      → registers.ensure_writable(1011, value)      # the funnel guard, first
      → client.write_control(1011, value)           # NEW: rail + build_write_control + _write_frame,
                                                    #     fire-and-forget, NEVER write_registers (the echo trap)
```

- One new client method, one new builder consumer, zero new seams:
  `_write_frame` is THE transport (contract 016 §5's rule); the
  probe's `write_probe_frame` stays untouched.
- The rail mirrors the probe's rail style (a ValueError before any
  byte, the message naming the rule) — defense in depth under the
  funnel guard, because the new method is public API of the client.
- AC-1 pins the builder parity (`build_write_registers(1011,[v])
  == build_write_control(1011,v) == the pinned strings`) — the
  classic funnel and the control send provably produce the same
  proven bytes.

## 3. The confirmation cycle (FR-3, contract 017 §5) — the core design

```
under _io_lock:
  write_control(1011, v)                t_write = now()
  _trigger_report("power write confirm") # ONE blocking base read (8 s window;
                                        # the burst lands ~2.2 s in — research §2)
  _wait_fresh_control(t_write)          # the probe's helper, UNCHANGED: fresh 2001
                                        # (last_seen > t_write), ≤ PROBE_MIRROR_TIMEOUT
  cross-check raw 2011 vs v → INFO confirmed | WARNING not-confirmed | WARNING unconfirmed
  snapshot: from_pushes(LIVE store, the 015 gates) → async_set_updated_data
```

Why this shape and not the alternatives:

- **Why not `async_request_refresh` alone:** debounced, and the poll
  cycle's captures copy at trigger-return can MISS a 2001 completing
  after the copy (research §4 Q3) — a cross-check against that
  snapshot could read a STALE mirror and log a false "not
  confirmed". The fresh-wait pattern (the probe's) is the proven
  oracle discipline.
- **Why trigger immediately (not the probe's wait-first order):**
  C-3 — the write does not wake the report; every second of wait
  first is a second of dead latency. The probe's 20 s dead wait was
  evidence purity (does the write wake it?); the confirmation has no
  such question to answer.
- **Why the snapshot push:** the entity plane must follow the
  command in seconds (the owner's actual complaint — research §3),
  and it must show the MIRROR's value, never the request (never
  optimistic — contract 016 §6). `from_pushes` over the live store is
  the same builder the poll uses — no second decode path.
- **Why no disconnect on completion:** unlike the probe (a one-shot
  instrument), the confirmation lives in the steady-state poll
  world; the link stays for the next cycle.
- **The trigger refactor:** `_probe_trigger` becomes
  `_trigger_report(context)` (a pure parameterization — the probe's
  log lines keep their exact strings so the 016 suite passes
  unchanged). One trigger implementation, two callers.

Latency budget (the numbers the quickstart promises): write ~0 s →
trigger window 8 s (burst at ~2.2 s) → fresh-wait ~0 s → snapshot
~0.1 s ⇒ **~8-10 s typical, ~28 s worst** (a burst that only
answers a retry that this path does NOT send — the next poll cycle
covers it, and the unconfirmed verdict says so honestly).

## 4. The climate surface (FR-5)

| State of the gates | Entity surface |
|---|---|
| `push_control_confirmed` off | NO climate entity (015's rule, unchanged) |
| control-confirmed, `write_control` off | the 015 read-only entity, byte-identical (AC-3) |
| control-confirmed, `write_control` on | `hvac_modes [OFF, HEAT]`, TURN_ON/OFF features, commands wired to FR-3; NO TARGET_TEMPERATURE (FR-8); diagnostics attributes (write_control + last verdict) |

- `hvac_action` untouched (OFF when off; unknown when on — the
  compressor binary is [V]; the quickstart pre-empts the
  "why is the activity pill empty" question).
- COOL/AUTO raise (heat-only — the same evidence as FR-8's mode
  exclusion); `set_temperature` keeps the 015 refusal.
- The classic-mode entity is untouched (its own base-block path).

## 5. The safety design (FR-7, contract 017 §5/§7)

Shipped: the gate (opt-in, reversible, never automatic), the
permanent allowlists (1011 / {0,1}), the audit line, the mirror
confirmation, the unconfirmed-OFF persistent notification (contract
016 §7's "told, never silently" made visible on the dashboard), the
documented risks in the quickstart.

Deliberately not built (research §4 Q7.6): time lockouts, per-command
confirmations, rate limits — the hazard they would address (a stray
automation call) is already covered by HA's own service-call trail +
the visible entity state, and friction would not stop a determined
mistake. The real engineering guard is the never-optimistic mirror
discipline: the tile can never lie that the unit is off.

## 6. Risks and their controls

| Risk | Control |
|---|---|
| An automation sends HEAT at 3 a.m. and the pump runs unattended | documented (quickstart §5); the tile shows HEAT truthfully; the audit line names it; OFF is one command away; the owner owns his automations |
| An OFF fails silently; the pump keeps running | the unconfirmed/mismatch verdicts + the persistent notification; the notification clears only on a confirmed cycle |
| The mirror disagrees with the request (a guard refused it, a link hiccup, a panel override) | the WARNING names both values; the tile shows the mirror's truth; no optimistic masking |
| The confirmation's 8-10 s looks "slow" to the user | the quickstart sets the expectation (~8-10 s, ~28 s worst) and explains why (the report is request-driven — research §2) |
| The write path is reused for another register "later" | the funnel guard + the client rail + the contract's non-transference (§2.4); the suite pins the refusal matrix |
| Someone enables the option without the proof | the option's description names the verdict; the contract's §2.2 says no release may auto-enable; the research §1 record is the auditable evidence |
| A stale mirror confirms a stale write | the freshness gate (`last_seen > t_write`) is the only oracle (AC-6 pins it with a scripted stale capture) |
| The confirmation runs concurrently with a poll | `_io_lock` serializes everything (the probe's precedent); the poll cycle's code is untouched (NFR-3) |

## 7. Sequencing (mirrors tasks.md)

1. T1 surfaces (const + options flow + the coordinator property +
   strings) — inert plumbing, both activation paths.
2. T2 the client send (rail + fire-and-forget + the parity
   self-test) — pure addition.
3. T3 the confirmation cycle (the trigger refactor + FR-3's method
   + the verdicts + the notification) — the core; the 016 probe
   suite must pass unchanged after the refactor.
4. T4 the climate wiring (FR-5) — the user-visible surface.
5. T5 the 017 suite (AC-1..AC-11) + the full-suite no-regression.
6. T6 version bump + the verification handoff (quickstart §2 =
   AC-12, the owner's live proof of the shipped surface).

## 8. What this plan deliberately does NOT do

- Does not extend the guard's address or value sets (activation
  only — research §4 Q6).
- Does not write the mode, the setpoint, the mute or any parameter
  register (research §5's pair-write evidence; contract §6).
- Does not touch `_async_update_push`, the probe, or any 001-016
  artifact (NFR-1/NFR-3).
- Does not add schedules, lockouts, or confirmation dialogs (FR-7's
  [D]).
- Does not promise the confirmation is instant — it promises the
  truth in ~8-10 s, with the numbers and the reason documented.