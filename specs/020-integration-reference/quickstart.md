# Spec 020 — Quickstart: install, configure, verify

The operational guide for the working integration. The production unit
is already calibrated and proven — this is both the "how it was done"
record and the "how to redo it on another unit" runbook.

---

## 1. Installation

Requirements: Home Assistant ≥ 2024.12 with a working Bluetooth adapter
(or proxy) on the host. `bleak` comes with HA.

- **HACS**: add this repository as a custom repository (category
  *Integration*), install *Aqua Temp BLE*, restart Home Assistant.
- **Manual**: copy `custom_components/aqua_temp_ble/` into your
  configuration's `custom_components/`, restart.

**Before any connection attempt: close the vendor app completely** —
the unit accepts a single BLE connection (the app holding it is the
most common cause of a failed test).

## 2. Adding the unit

Settings → Devices & Services → Add Integration → **Aqua Temp BLE**.

1. **Menu** — recognized units (advertised name starts with `BlueNRG-`,
   `WT-`, …) are listed first, then other nearby devices; a rescan and
   manual MAC entry are offered.
2. **Connection test** (~1 min): connect → subscribe → the vendor read
   as trigger → (if silent) the acknowledged-write probe → the push
   harvest. The unit's burst makes the test succeed in **push mode**.
3. **Confirm step**: name the device — the screen also shows what the
   test captured (power, heat target, inlet/outlet/ambient
   temperatures), decoded live from the burst.

The created entry stores `mode: "push"` — the read plane of this
firmware generation.

## 3. What you see right after setup

Everything is active by default (spec 022) — no checkboxes, no
activation step:

- Six temperature sensors showing live values within the first poll
  cycles, + the `Push stream` diagnostic.
- The climate entity: OFF/HEAT display, the setpoint read, the
  Heat/Off buttons and the target slider all working immediately.
- Three diagnostic buttons (probe / power probe / setpoint probe).

## 4. Verifying a new unit (optional — the confidence checks)

Spec 022 removed the confirmation gates: everything is active by
default, and the automatic write protections are unchanged. The four
verifications below are how the production unit was proven
(2026-10-12) — on a new or unknown unit they are worth redoing as
plain confidence checks; nothing to check, no option to flip.

1. **Temperatures** — run one poll, compare the six captured values
   (log: `block push captured: … hex=…`, or the probe button's store
   dump) against the vendor app's parameter view. [P done — the photo
   comparison matched]
2. **Power/mode display** — toggle the app's power slide, watch the
   diff-revert land on the same registers (2011 power, 2019 mode);
   re-verify against the app's display. [P done]
3. **Power control** — unit OFF, vendor app closed → run the **Power
   write probe** (button or `aqua_temp_ble.power_write_probe` service).
   Expected verdict line at WARNING:
   `power write probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0→1→0)`
   → the climate's Heat/Off buttons really start and stop the pump.
   [P done — pump heard]
4. **Setpoint control** — run the **Setpoint write probe**
   (`aqua_temp_ble.setpoint_write_probe`). Expected verdict line:
   `setpoint write probe: EXECUTED + REVERSIBLE (mirror 1046 followed S→T→S)`
   → the target slider sets the heat setpoint (10.0–40.0 °C, 0.5
   steps; out-of-range/off-step refused). [P done — mirror followed
   300→295→300]

A verdict other than `EXECUTED + REVERSIBLE` means: do not trust that
surface on that unit yet — read the verdict line, it always names the
state and the remediation.

## 5. Daily operation

- The poll cycle sends ONE read frame per interval (default 30 s,
  tunable 10-600 s) and consumes the burst; the read timeout each
  cycle is the expected world, not an error.
- The climate: `OFF`/`HEAT` follow the power mirror; `current_temperature`
  is the inlet water; `target_temperature` is the setpoint mirror.
- Writes are mirror-confirmed: after a button/slider action the state
  updates when the fresh report confirms it (≤ ~20 s window); an
  unconfirmed OFF raises a persistent notification telling you to check
  the panel.
- Diagnostics: `Push stream` (on/off + per-block counts + candidate
  raws), the climate's `last_power_write`/`last_setpoint_write`
  attributes, the three probe buttons.

## 6. Verification checklist (the acceptance pass)

- [ ] The 5 live temperature sensors match the vendor app (T01 shows
  `unknown` — its sensor is absent on this unit).
- [ ] The climate shows OFF/HEAT following the panel's power state.
- [ ] The target shows the panel's setpoint (÷10 scaling).
- [ ] Turn ON from HA → the pump starts; turn OFF → it stops; the tile
  follows within ~20 s.
- [ ] Set 30.0 °C from HA → the panel's setpoint display follows.
- [ ] A 30.3 °C request is refused loudly (never rounded); 45 °C is
  refused (never clamped).
- [ ] Vendor app open → HA shows connect failures/unavailable; closed →
  the next cycle recovers.

## 7. Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `cannot_connect` in the flow / unavailable entities | the single BLE link is held (usually the vendor app) | close the vendor app; power-cycle the unit if stale |
| `push stream silent after trigger` | no burst within the window | distance/radio; the cycle retries |
| Entities `unknown`/unavailable | the honesty rules, never a bug: T01 is sentinel (sensor absent), a block is missing, or the store is stale (freshness) | expected behaviour — no stale value is shown; the `Push stream` diagnostic and the probe button's store dump show the raws |
| `unconfirmed` write | the fresh mirror never came back | check the panel; the notification/OFF case tells you what to do |
| Log lines at WARNING | the deliberate evidence lines (probe verdicts, build marker, block captures) | read them — they are the diagnostics |
| Per-cycle `trigger read timed out as expected` at INFO | normal-world heartbeat (verbose; cleanup backlog) | harmless — see tasks.md |

## 8. Calibration surface (one-line edits, `registers.py`)

- A permuted T-window on another unit → one edit in `PUSHED_TEMPS`.
- A different setpoint home → `PUSHED_CONTROL["setpoint"]`.
- The write planes and value disciplines are NOT calibration switches —
  they are proven constants with their own contracts.