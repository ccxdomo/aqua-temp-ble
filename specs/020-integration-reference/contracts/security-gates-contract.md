# Spec 020 — Security & the gate ladder (the "portails")

The security posture of the integration, in one place: the write
funnel, the allowlists, and the owner-controlled gates that expose each
surface. Nothing in this file is aspirational — every rule is enforced
in `registers.py` / `coordinator.py` / `aqua_client.py` and pinned by
tests.

---

## 1. The single write funnel

`registers.ensure_writable(address, value, *, control_allowed=None,
setpoint_allowed=None)` is the ONLY path a production write may take
(`coordinator.async_write_register` calls it as its first statement;
the control/setpoint confirmation cycles call it before any I/O).
While a gate is closed the funnel raises `ValueError` and **zero
bytes move**.

### The allowlists (permanent)

| Plane | Registers | Values | Gate thread |
|---|---|---|---|
| Base (classic, dormant) | {8, 13, 14} (+19/20/21 behind `WRITE_MODE_SETPOINT_TOO`, off) | — | — |
| Control | **1011** | {0, 1} | `control_allowed` |
| Setpoint | **1046** | raw 100..400 step 5 | `setpoint_allowed` |

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

## 2. The gate ladder

Two calibration gates (what may be DISPLAYED) and two activation gates
(what may be WRITTEN). Each gate = a **per-entry option** OR a
**global switch in `registers.py`** (the calibration-switch precedent;
the option is the no-restart surface, the switch needs a restart).
All four options are OPEN on the production entry — the owner's
deliberate, verified acts. [P]

| # | Gate (option / global switch) | Opens | Required evidence (the owner's act) |
|---|---|---|---|
| 1 | `push_temp_confirmed` / `PUSH_TEMP_CONFIRMED` | the 6 temperature sensors (values from the pushed T-block) | the side-by-side photo calibration: captured values matched the app's parameter view [P done] |
| 2 | `push_control_confirmed` / `PUSH_CONTROL_CONFIRMED` | the climate entity (OFF/HEAT display + target read) | the power/mode diff-revert matched the app's display (2011/2019 pinned) [P done] |
| 3 | `write_control` / `WRITE_CONTROL_PROVEN` | power writes: climate TURN_ON/TURN_OFF + OFF/HEAT buttons | the power-write probe's `EXECUTED + REVERSIBLE` verdict (mirror 2011 followed 0→1→0) [P done] |
| 4 | `write_setpoint` / `WRITE_SETPOINT_PROVEN` | setpoint writes: the climate target slider | the setpoint probe's `EXECUTED + REVERSIBLE` verdict (mirror 1046 followed S→T→S) [P done] |

### Gate rules (enforced by design)

1. **Never automatic.** No code path, update, default or migration may
   ever SET any of the four options or the global switches. Opening a
   gate is the owner's act alone (checking the option / flipping the
   switch). The option's description names the required verdict line.
   [P contract 017 §2 / 019 §4.1]
2. **Independent and composable.** The owner may open power without
   setpoint or vice versa (separate risk classes); the surfaces layer:
   with both closed the climate is exactly the read-only mirror.
3. **Live surfaces.** The climate reads the gates from entry options
   live — saving the options re-renders the entity without a restart;
   the global switches apply after restart.
4. **Closed defaults.** All four ship False/closed: a fresh install
   displays nothing unconfirmed and writes nothing.
5. **The probes bypass the funnel by design** — through their own
   validated seams (pinned frames only / the value rail), from verified
   baselines; they are the evidence PRODUCERS for gates 3 and 4, never
   control surfaces.

## 3. Read-side honesty

- Unconfirmed quantities decode `None` → `unknown`: no unconfirmed raw
  is ever displayed under a certain name (gates 1-2).
- The freshness gate (`max(2 × poll_interval, 60 s)`) fails the cycle on
  a stale store → entities `unavailable`, never stale values.
- Sentinels decode `unknown` (both conventions: −9990, 32765); the
  vendor's 3276.5 °C display bug cannot happen here.
- The plausibility window (−45..+150 °C) + one WARNING per address per
  session for implausible raws.
- Optimistic state is forbidden: the only state mutation after a write
  is the mirror-built snapshot (a fresh block that postdates the write).

## 4. Pairing lever (dormant)

`pair_on_connect` ships OFF. Enabling it without a connection test that
produced `security probe: answered after pairing` is a contract
violation (the option exists to be earned). On the production unit the
verdict was `att-accepted-silent` — bonding is not the gate, the lever
stays closed, and the poll path is byte-identical with it off.

## 5. Where each rule lives

| Rule | Code |
|---|---|
| Funnel + allowlists + gate switches | `registers.ensure_writable`, `ALLOWED_WRITE_*`, `*_PROVEN` |
| Option gates (live) | `coordinator.push_temp_confirmed` / `push_control_confirmed` / `write_control_allowed` / `write_setpoint_allowed` |
| Client rails | `aqua_client.write_control` (1011, 0/1 only), `_setpoint_write_frame` (int/range/step) |
| Probe seams | `write_probe_frame` (pinned strings only), `write_setpoint_probe_frame` |
| Entity gate surfaces | `climate.py` (hvac_modes/supported_features/extra_state_attributes), `sensor.py` (confirmed keys only), `climate.async_setup_entry` (control gate) |
| Honesty decode | `registers.decode_temp`, `coordinator.decode_temp_logged`, `AquaTempData.from_pushes` |