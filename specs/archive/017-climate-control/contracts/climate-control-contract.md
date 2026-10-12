# Contract 017 — Climate control: the activated write plane

Normative rules governing the CONTROLLED CLIMATE surface of spec
017: the activation of the proven power write, the confirmation
discipline, and the exclusions. This contract EXECUTES contract 016
(whose text stays authoritative and untouched — the guard, the
pinned frames, the transport parity, the honesty rules); it adds the
phase-2 rules 016 §4 reserved for "after the proof".

> The proof is the gate's precondition; the owner's act is its
> cause. A control surface never appears by itself.

## §1 Definitions

- The **activation gate** is the OR of the two owner-set surfaces
  (spec 017 FR-1): the per-entry option `write_control` and the
  global `registers.WRITE_CONTROL_PROVEN`. The coordinator exposes
  it as `write_control_allowed`.
- The **confirmation cycle** is spec 017 FR-3's method
  (`async_control_write_power`): guard → write → trigger →
  fresh-mirror wait → cross-check → snapshot push.
- The **fresh mirror** is a pushed 2001-block capture whose
  `last_seen` is strictly greater than the write's timestamp — the
  only oracle a verdict may read (the probe's discipline, contract
  016 §3, restated for control).
- The **persistent notification** is the unconfirmed-OFF surface of
  FR-3 step 7 (the revert obligation's visibility).

## §2 The activation rule (when the write plane opens)

1. The authorization evidence is the recorded production proof:
   the probe's `EXECUTED + REVERSIBLE` verdict line + the owner's
   physical confirmation, quoted verbatim in research 017 §1 (the
   decision log). This record SATISFIES contract 016 §4's
   precondition — recorded in this repo, it is the artifact a future
   reader cites.
2. The gate is set ONLY by the owner: the option checkbox or the
   registers switch, then (for the switch) a restart. **No code path,
   update, or default may ever set either.** A shipped release that
   auto-enables control is a violation of this contract.
3. The gate is reversible at any time (uncheck → reload → the write
   surface is gone; the read-only 015 surface returns).
4. **Non-transference (contract 016 §4, restated):** the power
   proof authorizes exactly one register (1011) with exactly two
   values (0/1). The mode (1012-1013), the setpoint (runtime [V]),
   the mute (1076) and every parameter page remain CLOSED — each
   needs its own pinned frame, its own mirror and its own §3-style
   proof in a future contract.

## §3 The write rule (what may go on the wire, how)

A control write is legal ONLY when ALL of:

1. `registers.ensure_writable(1011, value)` passes — the funnel
   guard, first, before any lock or I/O (the address AND the value
   allowlist, permanent — contract 016 §4's value discipline).
2. The frame is built by `modbus.build_write_control(1011, value)`
   — byte-equal to the two pinned strings (asserted by the suite;
   one source of truth).
3. The send is fire-and-forget on the `_write_frame` seam
   (`withoutResponse`, the ff03 bucket — contract 016 §5's parity
   table) via `client.write_control`, whose client-side rail admits
   only `(1011, 0)` and `(1011, 1)`.
4. The write is followed, in the same lock hold, by exactly ONE
   trigger (the base read) and one fresh-mirror wait — C-3's rule:
   the write does not wake the report; the trigger does
   (research 017 §2). No re-send of the trigger (the §4.1 discipline,
   spec 014).

## §4 The echo amendment (no FC10 echo on the control plane)

The classic funnel's echo expectation (`WRITE_ECHO_EXPECTED`, a
runbook-step-W calibration switch bound to `write_registers`) does
NOT apply to the control plane. The unit demonstrably executes
without-response writes; no FC10 echo has ever been observed
(research 017 §6). Reusing `write_registers` for a control write
would wait 3 s and RAISE on a missing echo — misreporting a proven,
successful write as a failure.

Rule: **the MIRROR is the confirmation, never an echo.** A control
write's success is decided only by §5's fresh-mirror cross-check.
For contract 016 §5's "single funnel" wording, the funnel is the
GUARD (`ensure_writable`) + the SEAM (`_write_frame`); the echo wait
is a classic-path transport detail that must not leak into the
control plane. This is the sole amendment this contract makes to
016 §5's letter; its intent (one guarded path, no parallel writes)
is unchanged and reinforced by the client rail.

## §5 The confirmation rule (the verdict discipline)

1. The verdict oracle is the FRESH MIRROR (§1) — a capture that
   postdates the write. A stale capture never confirms.
2. Three verdicts, all logged (FR-4's exact strings):
   - **confirmed** (INFO) — the fresh raw 2011 equals the requested
     value;
   - **not confirmed** (WARNING) — the fresh mirror disagrees; both
     values in the line;
   - **unconfirmed** (WARNING) — no fresh report within the window;
     the line states the unit's state is UNKNOWN and names the
     remediation.
3. **Never optimistic** (contract 016 §6, restated): no state is
   mutated on the request's strength. The snapshot push (confirmed
   and not-confirmed verdicts) carries ONLY mirror-decoded values —
   the entity plane shows the mirror's truth even when it differs
   from what was asked.
4. On the unconfirmed verdict NO snapshot is pushed; the next poll
   cycle retries naturally.
5. **The unconfirmed-OFF obligation (contract 016 §7, executed):**
   an OFF whose verdict is unconfirmed (or contradicted) raises the
   persistent notification with the remediation ("check the panel;
   the unit may still be ON — turn it OFF from the vendor app or the
   panel"); the next confirmed cycle for the same unit dismisses it.
   A potentially-running unit is NEVER a silent end state.

## §6 The exclusions (the mode and the setpoint, by evidence)

1. **No mode write** — on this unit, ever, under this contract. The
   vendor's own mode frame writes the PAIR 1012-1013 carrying
   `[mode, per-mode setpoint ×10]` (research 017 §5 [P
   disassembly]): a mode write would also write an unpinned
   setpoint-shaped register (1013), and the app itself refuses mode
   changes on heat-only units ([P apk]). `set_hvac_mode` exposes
   OFF/HEAT only — both pure power writes; the mode DISPLAY stays
   the 015 read path (2019 → PUSHED_MODE_VALUE_TO_NAME, [A] 4 →
   heat, unmapped → unknown + one warning per session).
2. **No setpoint surface** — no read home exists ([P — the 015
   scan]), no write address is proven ([V] — runtime). No target
   display, no TARGET_TEMPERATURE feature, no fake UI. The named
   follow-up is the logcat oracle (quickstart 015 §2) + a future
   spec.
3. `hvac_action` stays honest: OFF when the power is off, unknown
   when on (the compressor binary is uncalibrated [V]). No guessed
   heating/cooling action.

## §7 The audit obligation

Every executed control write leaves, in the owner's log page:

- the audit line (INFO): register, value, expectation (FR-4);
- the verdict line (INFO/WARNING per §5);
- on the unconfirmed-OFF path: the persistent notification.

There is no silent control. A write whose trail is absent from the
log is a contract violation (this is also the support diagnostic:
quickstart §3's table maps every line to its meaning).

## §8 The verification obligation (the shipped surface's first proof)

The option may be enabled only toward quickstart 017 §2's live
verification (AC-12): one HEAT command (the pump starts, the tile
follows, the confirmed line logs) and one OFF command (the pump
stops, the tile follows). Until that verification is recorded, the
feature is "enabled but unverified" — the quickstart says so. The
power-write probe (016) remains the standing re-verification and
revert instrument and stays gate-independent.