# Spec 017 — The controlled climate: opening the proven write path (phase 2)

Date: 2026-10-12
Repo state: main @ d9d7632, manifest 0.12.0
Status: **proposed (Coder-ready)** — the phase-2 gate condition is
SATISFIED: the owner's production probe verdict `EXECUTED +
REVERSIBLE` + his physical confirmation are recorded verbatim in
`research.md` §1 (contract 016 §4's record).
Depends on: specs 003 §6 (write security), 013/014 (the push plane),
015 (the read-only climate + the 2011/2019 pinning), 016 (the probe,
the locked guard `ALLOWED_WRITE_CONTROL` + `WRITE_CONTROL_PROVEN`,
contract 016).
Evidence: `research.md` (this directory) — every FR below cites it.

## 1. Problem

The proof is made. The owner ran the shipped power-write probe on
his real machine and the mirror followed `0→1→0`; the pump
physically started and stopped (research 017 §1 — contract 016 §4's
precondition). The write path (register 1011, values 0/1, the two
pinned frames) is proven live in BOTH directions.

But the climate is still read-only (spec 015 FR-8), the guard
extension ships locked (`WRITE_CONTROL_PROVEN = False`, spec 016
T3), and the owner's own observation shows the gap: while the probe
turned the unit ON, the climate never showed heating — the probe
never updates the HA snapshot and locks out the poll for its whole
run, so the ON state was invisible by design (research 017 §3).

This spec opens the control the honest way: a per-owner activation
gate, the proven power writes wired to the push-mode climate, an
immediate post-write confirmation cycle (the state follows a command
in seconds, decided by the mirror, never optimistic), the mode and
setpoint explicitly NOT pilotable (research 017 §5's pair-write
evidence), and proportionate safety surfacing for a remotely
controlled heat pump.

## 2. Functional requirements

### FR-1 — The activation gate (the owner's act, never automatic)

- `const.py`: `DEFAULT_WRITE_CONTROL = False`.
- The integration's OptionsFlow (the `push_control_confirmed`
  precedent) gains a checkbox **`write_control`** (default False),
  labeled/described in `strings.json` + translations as requiring
  the probe's `EXECUTED + REVERSIBLE` verdict (contract 016 §4).
- `coordinator.write_control_allowed` (the `push_temp_confirmed`
  property pattern): `registers.WRITE_CONTROL_PROVEN or
  bool(entry.options.get("write_control", False))` — BOTH activation
  surfaces are honored: the per-entry option (no restart; saving
  reloads the entry) and the shipped global switch (the calibration
  precedent; restart). No code path may set either; the shipped
  default is byte-identical to 0.12.0 (AC-10).
- The proof that authorizes the activation is recorded in research
  017 §1 (the decision log). Flipping without it remains a contract
  violation (contract 016 §4's discipline carries over verbatim).

### FR-2 — The control send (client, fire-and-forget)

`aqua_client.py` gains `async write_control(self, address: int,
value: int) -> None`:

- **Client-side rail**: `address == registers.WRITE_REG_POWER` and
  `value in {0, 1}` (else `ValueError` BEFORE any byte goes out —
  defense in depth on top of the funnel guard, the
  `write_probe_frame` rail precedent).
- Frame: `modbus.build_write_control(address, value)` — the
  pinned-parity builder (research 017 §6; AC-1).
- Send: fire-and-forget via the SAME `_write_frame` seam
  (withoutResponse, app parity — contract 016 §5). It must NOT use
  `write_registers` (the classic echo path: `WRITE_ECHO_EXPECTED`
  would raise a 3 s echo timeout on every control write — research
  017 §6's echo trap; contract 017 §4).
- Raise `AquaTempConnectionError` when not connected (the
  fire-and-forget path's existing precondition check).
- The probe's pinned seam (`write_probe_frame`) is UNCHANGED.

### FR-3 — The post-write confirmation cycle (coordinator)

`coordinator.py` gains `async_control_write_power(self, value: int)
-> dict[str, Any]`, the ONLY write path the climate uses:

1. **Guard first**: `registers.ensure_writable(WRITE_REG_POWER,
   value)` — before any lock or I/O (the funnel rule; fails loudly
   with the 016 message while the gate is closed).
2. Connect if needed (the cycle's connect pattern, `pair` option
   respected); transport failures propagate to the caller.
3. `t_write = _now()`; audit line at INFO:
   `power write via 1011 = <v> — mirror should follow in the next
   report` (the 016 FR-9 string, verbatim).
4. Under `_io_lock` (single-flight, serialized against polls):
   a. `await client.write_control(1011, value)` (FR-2);
   b. ONE trigger — the blocking base read (the proven trigger,
      research 017 §2/C-3: the write alone does not wake the
      report; the trigger does, ~2.2 s). Reuse the probe's helper,
      refactored to a shared `async _trigger_report(context)` used
      by both the probe (context "power write probe" — behavior
      byte-identical, its 016 tests keep passing) and the
      confirmation (context "power write confirm");
   c. `await self._wait_fresh_control(t_write)` (the probe's
      helper unchanged: a 2001 capture with `last_seen > t_write`,
      bounded by `const.PROBE_MIRROR_TIMEOUT`).
5. **Cross-check** the fresh capture's raw 2011 against `value`:
   - equal → INFO `power write confirmed by mirror (2011 = <v>)`;
   - different → WARNING `power write not confirmed by mirror
     (mirror 2011 = <x>, requested <v>)` — both values in the line;
   - no fresh capture → WARNING `power write unconfirmed (no fresh
     report within <t> s) — the unit's state is UNKNOWN: check the
     panel`.
6. **Snapshot push (confirmed/mismatch only)**: build
   `AquaTempData.from_pushes(dict(client.pushed_blocks),
   push_temp_confirmed, push_control_confirmed)` — the LIVE store,
   the same builder the poll cycle uses — and
   `await self.async_set_updated_data(data)`. The entity plane
   reflects the MIRROR, never the request. On the no-report path:
   NO snapshot push (the state stays; the next poll cycle retries
   naturally).
7. **Unconfirmed-OFF surface**: when `value == 0` and the verdict
   is unconfirmed (no-report) or mismatch → create the persistent
   notification (`persistent_notification.async_create`, id
   `aqua_temp_ble_power_unconfirmed_<address>`, message naming the
   unit, the requested OFF, and the remediation "check the panel;
   the unit may still be ON — turn it OFF from the vendor app or the
   panel"). A later CONFIRMED cycle for the same address dismisses
   it (`async_dismiss`). This is contract 016 §7's revert
   obligation made visible (research 017 §4 Q7.5).
8. Do NOT disconnect on completion (unlike the probe — the
   confirmation lives in the steady-state poll world; the next
   cycle reuses the link).
9. Return the stage dict (the written value, the fresh capture's
   raws + relative times, the verdict) — the tests' oracle.
10. `async_write_register` (the classic funnel) is UNCHANGED.

### FR-4 — The verdict/audit surface (the exact strings)

| Event | Level | Line |
|---|---|---|
| write accepted | INFO | `power write via 1011 = <v> — mirror should follow in the next report` |
| mirror confirms | INFO | `power write confirmed by mirror (2011 = <v>)` |
| mirror disagrees | WARNING | `power write not confirmed by mirror (mirror 2011 = <x>, requested <v>)` |
| no fresh report | WARNING | `power write unconfirmed (no fresh report within <t> s) — the unit's state is UNKNOWN: check the panel` |
| unconfirmed OFF | WARNING + persistent notification | the notification of FR-3 step 7 |

Never optimistic: no state mutation outside the snapshot push, which
carries only mirror-decoded values (contract 016 §6).

### FR-5 — The climate surface (push mode)

`climate.py` (push mode only; the classic entity is byte-identical):

- When `coordinator.write_control_allowed` (AND the existing
  `push_control_confirmed` — the 015 layering unchanged; the
  entity exists only behind the 015 gate):
  - `_attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]` (heat-only
    unit; COOL/AUTO are unsupported — research 017 §4 Q4);
  - advertise the turn-on/turn-off features per the shipped HA
    climate API (`ClimateEntityFeature.TURN_ON | TURN_OFF` where
    the installed HA defines them); `TARGET_TEMPERATURE` is NEVER
    advertised (no setpoint surface — FR-8);
  - `async_turn_on` → `await coordinator.async_control_write_power(1)`;
  - `async_turn_off` → `…(0)`;
  - `async_set_hvac_mode(OFF)` → `…(0)`; `(HEAT)` → `…(1)`;
    any other mode → `HomeAssistantError` (unsupported);
  - `async_set_temperature` → `_refuse_write` (the 015 refusal);
  - `hvac_action` UNCHANGED (OFF when power is False; None
    otherwise — the compressor binary is uncalibrated [V]; do not
    guess);
  - diagnostics: `extra_state_attributes` gains `write_control`
    ("allowed"/"locked") and the last write verdict + its raw
    values (FR-3 step 9's dict) — the cheap support surface.
- When NOT allowed: the 015 read-only behavior byte-identical
  (`hvac_modes=[]`, no features, `_refuse_write`'s exact message —
  the 015 suite passes unchanged).
- Write-path exceptions propagate to HA (the entity surfaces the
  error; no optimistic fallback).

### FR-6 — Never optimistic (restated as a requirement)

No code in this spec optimistically updates power/mode/target
state. The ONLY source of displayed state is the pushed mirror
decoded by `from_pushes` (the 015 gates applying). The vendor app
itself is optimistic (research 017 §5's `state.field_f` update
before the write) — we deliberately are not (contract 016 §6).

### FR-7 — The safety set (proportionate, no over-engineering)

Shipped by this spec: the activation gate (FR-1), the address+value
allowlists (016's guard, unchanged), the audit line + verdicts
(FR-4), the mirror confirmation (FR-3), the unconfirmed-OFF
persistent notification (FR-3 step 7), and the DOCUMENTED risks
(quickstart §5: a remote start is a real pump start — day or night;
the unit's own controller governs its internal protections exactly
as when the vendor app toggles the same register). Deliberately NOT
built: time-window lockouts, per-command confirmation dialogs, rate
limiting — [D] (research 017 §4 Q7.6: single-owner integration; HA's
own service-call audit + the visible entity state are the remaining
guardrails; friction here would not add safety).

### FR-8 — The mode and setpoint exclusions (read-only, by evidence)

- **Mode**: NO mode write exists in this spec — the vendor's own
  mode write is a PAIR write (1012+1013: `[mode, setpoint×10]`,
  research 017 §5 [P disassembly]) that would write an unpinned
  register (1013), and the app REFUSES mode changes on this
  heat-only unit ([P apk] guard + i18n). `hvac_mode` DISPLAY stays
  the 015 decode: 2019 → `PUSHED_MODE_VALUE_TO_NAME` ([A] 4 →
  heat; unmapped → unknown + one warning per session).
- **Setpoint**: NO setpoint read (no pushed home [P — the 015
  scan]), NO setpoint write (address [V] — runtime). No target
  display, no `TARGET_TEMPERATURE` feature, `set_temperature`
  refuses. The quickstart says it plainly (no fake UI); the named
  follow-up is the logcat oracle + a future spec with its own
  proof (contract 016 §4 non-transference).

## 3. Non-functional requirements

- **NFR-1 — No behavior change by default.** With both activation
  surfaces off (shipped), the integration's entity set, write
  surface, guard behavior and logs are byte-identical to 0.12.0
  (AC-10); the 015/016 suites pass UNCHANGED.
- **NFR-2 — One source of truth.** The frames come from the
  builder (`build_write_control`), cross-checked against the pinned
  strings by AC-1's test; no duplicated frame constants anywhere.
- **NFR-3 — The poll cycle is untouched.** `_async_update_push` is
  NOT modified (the confirmation is a separate method; the
  013/014 suites pass unchanged).
- **NFR-4 — No new dependencies.** The notification is the HA core
  `persistent_notification` component; everything else reuses the
  shipped plumbing.
- **NFR-5 — English-only artifacts** (repo rule).

## 4. Acceptance criteria

- **AC-1 Funnel parity**: `modbus.build_write_registers(1011, [1])
  == modbus.build_write_control(1011, 1) == bytes.fromhex(
  registers.WRITE_FRAME_POWER_ON)`; the same for 0/OFF (test).
- **AC-2 Client rail**: `client.write_control(1011, 2)` /
  `(1012, 1)` raise `ValueError` and the fake write characteristic
  records ZERO writes (test).
- **AC-3 Gate closed = 0.12.0**: with both surfaces off, the
  push-mode climate is byte-identical to the 015 read-only surface
  (`hvac_modes == []`, writes refused with the 015 message) and the
  015 + 016 suites pass unchanged.
- **AC-4 Option path**: with the option `write_control: true`,
  `hvac_modes == [HVACMode.OFF, HVACMode.HEAT]`; `turn_on` /
  `set_hvac_mode(HEAT)` records EXACTLY the pinned ON frame bytes on
  the fake write char (withoutResponse); `turn_off` /
  `set_hvac_mode(OFF)` the OFF frame; COOL/AUTO raise;
  `set_temperature` raises; `TARGET_TEMPERATURE` is not advertised.
- **AC-5 Switch path**: with `registers.WRITE_CONTROL_PROVEN =
  True` (test-patched), the same surface as AC-4 (both activation
  paths honored).
- **AC-6 The confirmation, confirmed**: a scripted fresh 2001
  capture (mirror follows, `last_seen > t_write`) → the confirmed
  INFO verdict; the snapshot is pushed (the climate tile reflects
  the MIRROR); a STALE capture (`last_seen <= t_write`) never
  satisfies the wait (the scripted test pins the freshness gate).
- **AC-7 Mismatch**: the fresh mirror disagrees with the request →
  the WARNING verdict naming both values; the tile shows the
  MIRROR's state (never the request).
- **AC-8 No report / unconfirmed OFF**: no fresh capture in the
  window → the WARNING unconfirmed line; NO snapshot push (the
  state is unchanged); for `value == 0` the persistent notification
  is created with the remediation text, and a later confirmed cycle
  dismisses it (tests).
- **AC-9 Write error**: the write raises → the error propagates to
  the caller; NO trigger, NO cross-check, NO snapshot push, NO
  notification (the fake records zero post-write reads).
- **AC-10 No-regression**: the FULL existing suite passes with the
  gates closed; the probe (016 suite) is unchanged and still
  gate-independent.
- **AC-11 The poll cycle untouched**: the 013/014 suites pass
  unchanged (`_async_update_push` not modified).
- **AC-12 The live verification (the owner — quickstart 017 §2)**:
  with control enabled, tapping HEAT starts the pump AND the
  climate tile shows HEAT within ~15 s, with the audit + confirmed
  lines in the log page; tapping OFF stops it and the tile shows
  OFF. This AC closes with the owner's recorded observation (the
  016 AC-11 pattern — the shipped surface's first live proof).

## 5. Out of scope

- The mode write (1012-1013) — research 017 §5's pair-write evidence
  + the vendor's heat-only lock; needs a different unit or its own
  future spec with its own proof.
- The setpoint (read home [P-absent] / write address [V]) — the
  logcat oracle is the named follow-up (quickstart 015 §2).
- The mute write (1076), the timer/parameter pages — unchanged
  (016 §5's map).
- Any change to the base-block allowlist {8, 13, 14} (spec 003 §6
  stays frozen), to `_async_update_push`, or to the probe.
- Schedules, time-window lockouts, rate limiting (FR-7's [D]).