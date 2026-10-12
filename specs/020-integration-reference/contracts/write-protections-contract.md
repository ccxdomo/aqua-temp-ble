# Spec 020 — Write protections (the automatic guardrails)

The security posture of the integration, in one place: the write
funnel, the allowlists, the value rails, and the read-side honesty
rules. Nothing in this file is aspirational — every rule is enforced
in `registers.py` / `coordinator.py` / `aqua_client.py` and pinned by
tests.

> **Revision note (2026-10-12).** Revised after spec 022: the
> activation gates were removed; everything is active by default.
> The automatic write protections are unchanged. The former
> owner-confirmation gates are kept as a historical record in §2.

---

## 1. The single write funnel

`registers.ensure_writable(address, value)` is the ONLY path a
production write may take (`coordinator.async_write_register` calls
it as its first statement; the control/setpoint confirmation cycles
call it before any I/O). A register outside the allowlists — or a
value outside its rail — raises `ValueError` and **zero bytes move**.

### The allowlists (permanent)

| Plane | Registers | Values |
|---|---|---|
| Base (classic, dormant) | {8, 13, 14} (+19/20/21 behind `WRITE_MODE_SETPOINT_TOO`, off) | — |
| Control | **1011** | {0, 1} |
| Setpoint | **1046** | raw 100..400 step 5 |

Everything else — the mode pair 1012/1013, the mute 1076, installer
thresholds, the whole read plane 1001-2090 — is refused, forever, with
a named error. There is deliberately NO generic write surface: an
erroneous H04/R-limit write can damage the machine or prevent it from
starting. [P contract 003 §6 / 016 §4 / 019 §2.2]

### Non-transference

**Every register needs its own proof.** The power proof authorizes
1011 with {0,1} and NOTHING else; the setpoint proof authorizes 1046
with its range and NOTHING else. A proof never transfers across
addresses, across value sets, or across risk classes (a reversible
boolean vs a regulation value). [P contracts 016 §4 / 019 §4]

## 2. The removed owner gates (historical record)

Specs 013-019 shipped four owner-confirmation gates — two calibration
gates (what could be DISPLAYED) and two activation gates (what could
be WRITTEN). Each gate was a per-entry option OR a global switch in
`registers.py`, and each opened on the production entry only after its
evidence was recorded:

| Gate (option / global switch) | Opened | Evidence (the owner's act, 2026-10-12) |
|---|---|---|
| `push_temp_confirmed` / `PUSH_TEMP_CONFIRMED` | the 6 temperature sensors | the side-by-side photo calibration [P done] |
| `push_control_confirmed` / `PUSH_CONTROL_CONFIRMED` | the climate entity (display + target read) | the power/mode diff-revert (2011/2019 pinned) [P done] |
| `write_control` / `WRITE_CONTROL_PROVEN` | power writes | the power probe's `EXECUTED + REVERSIBLE` verdict (mirror 2011 followed 0→1→0) [P done] |
| `write_setpoint` / `WRITE_SETPOINT_PROVEN` | setpoint writes | the setpoint probe's `EXECUTED + REVERSIBLE` verdict (mirror 1046 followed S→T→S) [P done] |

Spec 022 (Option B: the hunt is over — every guarded surface was
proven) removed all four: the option keys, the global switches, the
coordinator gate properties and the funnel's gate threads no longer
exist in the code. What survives is everything that never needed a
human: the allowlists, the value rails, the mirror confirmation, the
freshness rule, the sentinels. The verification probes remain, as
measurement instruments (they were never control surfaces).

## 3. Read-side honesty

- An absent block decodes `None` → `unknown`: no value is ever
  displayed without a live pushed source.
- The freshness rule (`max(2 × poll_interval, 60 s)`) fails the cycle
  on a stale store → entities `unavailable`, never stale values.
- Sentinels decode `unknown` (both conventions: −9990, 32765); the
  vendor's 3276.5 °C display bug cannot happen here.
- The plausibility window (−45..+150 °C) + one WARNING per address per
  session for implausible raws.
- Optimistic state is forbidden: the only state mutation after a write
  is the mirror-built snapshot (a fresh block that postdates the
  write).

## 4. Pairing (pruned)

The spec-011 pairing ladder never earned its evidence: the production
verdict was `att-accepted-silent` (writes are accepted at the ATT
layer without bonding — law L5), so it never ran in anger. The 021
cleanup pass pruned the whole surface (`agent.py`, `client.pair()`,
the `pair_on_connect` option); no pairing surface ships.

## 5. Where each rule lives

| Rule | Code |
|---|---|
| Funnel + allowlists + value rails | `registers.ensure_writable`, `ALLOWED_WRITE_*`, `SETPOINT_*_RAW` |
| Client rails | `aqua_client.write_control` (1011, 0/1 only), `_setpoint_write_frame` (int/range/step) |
| Probe seams | `write_probe_frame` (pinned strings only), `write_setpoint_probe_frame` |
| Entity surfaces | `climate.py` (hvac_modes/supported_features/extra_state_attributes), `sensor.py` (six T-sensors + push_stream — unconditional in push mode) |
| Honesty decode | `registers.decode_temp`, `coordinator.decode_temp_logged`, `AquaTempData.from_pushes` |