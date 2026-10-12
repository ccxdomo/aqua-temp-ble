# Spec 020 — Data model: the register → quantity map

The single source of truth in code is `registers.py` (constants) +
`coordinator.AquaTempData` (decode). This file mirrors that map for
human readers, current as of manifest 0.14.0. Evidence tags as in
`research.md` ([P] proven / [A] assumed / [V] open).

## 1. Read plane — pushed blocks (the live data)

Four 45-register blocks per burst, latest-frame-wins per start:

### Block 2001 (regs 2001-2045) — live machine state

| Register | Quantity | Decode | Entity | Evidence |
|---|---|---|---|---|
| 2010 | block self-reference (`0x07D1` = 2001) | — | none (byte-order proof) | [P] |
| **2011** | **Power** (state mirror) | flag: raw ≠ 0 → on | climate `hvac_mode`: 0→OFF, ≠0→HEAT | [P diff-revert] |
| 2012 | flag (static 1 in the diff) | — | none | [V] |
| 2017, 2018 | unknown | — | none | [V] |
| **2019** | **Mode** (state mirror) | raw enum; 0 = off-world/after-our-writes, 4 = app-flow heat | diagnostic only (never gates display; unmapped nonzero → 1 INFO/session) | [P diff-revert] |

### Block 2046 (regs 2046-2090) — live T-family temperatures

Decode: `raw / 10` °C; sentinels (−9990, 32765) → `unknown`; plausibility
raw −450..1500 → else `unknown` + WARNING once.

| Register | Family | Entity (sensor) | On this unit | Evidence |
|---|---|---|---|---|
| 2046 | T01 | Suction temperature | `unknown` (sentinel `0x7FFD` — sensor absent) | [P photo] |
| 2047 | T02 | Inlet water temperature (also the climate's `current_temperature`) | live | [P photo + live] |
| 2048 | T03 | Outlet water temperature | live | [P photo] |
| 2049 | T04 | Coil temperature | live | [P photo] |
| 2050 | T05 | Ambient temperature | live | [P photo] |
| 2051 | T06 | Exhaust temperature | live | [P photo] |

### Block 1046 (regs 1046-1090) — parameter ladder

| Register | Quantity | Entity | Evidence |
|---|---|---|---|
| **1046** | **Heat setpoint** (`raw / 10` °C) | climate `target_temperature` (read); write target (behind `write_setpoint`) | [P panel diff + live write] |
| 1049 | ladder member (min-bound/diff family) | none | [V] |
| 1050 | max-bound candidate (40.0) | none (display uses the guard's static 10.0/40.0) | [A-weak] |
| 1051 | cool setpoint candidate | none | [A-strong] |
| 1054 | min-bound candidate (10.0) | none | [A-weak] |
| 1068 | auto setpoint candidate | none | [A-strong] |

### Block 1001 (regs 1001-1045) — machine-settings ladder

Contains the **write plane** registers (§2). Pushed values for this
window are stored and visible as raw diagnostics only; no entity maps
them. [P store]

## 2. Write plane

| Register | Command | Value discipline | Gate | Status |
|---|---|---|---|---|
| **1011** | power ON/OFF | {0, 1} only, forever | `write_control` option OR `WRITE_CONTROL_PROVEN` | [P proven live] |
| **1046** | heat setpoint | raw 100..400, multiples of 5 (10.0-40.0 °C / 0.5 °C step); never rounded/clamped | `write_setpoint` option OR `WRITE_SETPOINT_PROVEN` | [P proven live] |
| 1012+1013 | mode (pair write) | — | **never writable** (1013 unpinned; app locks mode) | [P shape] |
| 1076 | mute | — | **never writable** | [P frame / V] |

Write↔read relationship: power written 1011 / read 2011; setpoint
written AND read 1046. The write target is validated against the mirror
that postdates it (fresh block, `last_seen > t_write`). [P]

## 3. The base block (regs 7-51) — classic plane, dead on this unit

Kept as the APK-proven label map and the dormant classic-mode surface:

| Register | Quantity | Note |
|---|---|---|
| 8 | Power | write home in the classic plane (never reachable here) |
| 11 | Boost mode | [P name] |
| 13 | Mode (0/1/2 cool/heat/auto [A]) | — |
| 14 | Current setpoint (÷10) | classic write home |
| 19/20/21 | Cool/Heat/Auto targets | behind `WRITE_MODE_SETPOINT_TOO` (off) |
| 22-25 | Cool/Heat min/max bounds | — |
| 26/27/28 | Inlet/Outlet/Ambient | names reused by the pushed mirrors |
| 47 | Fault 1 | no pushed home → no entity in push mode |
| 48-51 | Firmware (joined "a.b.c.d" [V]) | no pushed home → no entity in push mode |

## 4. Snapshot shape (`AquaTempData`)

The coordinator's single state object, decoded per cycle:

```
registers: dict[int, int]        # confirmed T-block raws (push mode)
power, mode_value, hvac_mode     # from block 2001 (behind control gate)
target_temp                      # from block 1046 (behind control gate)
mode: "classic" | "push"         # entry plane
push_confirmed, push_counts, push_first_seen, push_last_seen
push_control_raws                # diagnostic candidates (2011/2012/2019/1046/…)
```

Classic fields (boost/min_target/max_target/inlet/outlet/ambient/fault/
firmware/binaries) decode `None` in push mode — never guessed.

## 5. Entity → source summary (push mode)

| Entity | Source register(s) | Gate |
|---|---|---|
| climate `hvac_mode` | 2011 (power) | `push_control_confirmed` |
| climate `target_temperature` | 1046 | `push_control_confirmed` |
| climate `current_temperature` | 2047 (inlet) | `push_temp_confirmed` |
| climate min/max/step | static 10.0/40.0/0.5 (guard constants) | — |
| 6 temperature sensors | 2046-2051 | `push_temp_confirmed` |
| `Push stream` diagnostic | store freshness + counts + candidate raws | always |
| 3 probe buttons | services | always (diagnostic) |

Unique ids: `<ADDRESS>_<key>`; device identifiers `{domain, address}`;
manufacturer PHNIX. One entity per quantity on the two planes (a mirrored
base key never duplicates its pushed twin — the same entity switches
source). [P contract 013 §4.1]