# Data model 003 — The complete parameter map (seven families)

Status: **PLANNED** — this is the consolidated register-table evidence of
the project: the seven families (**T / H / R / P / S / O / D**), every
known label, every observed value, and the honest per-aspect status of
each row. Sources: the APK static analysis (`FINDINGS.md`, CRC-verified)
and the **panel observation** — the owner's photographs (2026-10) of the
official app's parameter view opened with the debug password `"66"`
[FINDINGS.md §10].

Legend — status is tracked **per aspect**, because the panel proves
different things for different aspects:

- **Label status**: `[P panel]` photographed in the app's parameter view ·
  `[P apk]` recovered from the APK · `[V]` unknown.
- **Value status**: `[P panel]` displayed value photographed · `[A]`
  encoding hypothesis (displayed ÷10, ON/OFF↔1/0) · `[V]` unknown.
- **Index status**: the numeric register address. **NOT PROVEN for every
  new family member** — see §10. Base-block addresses (7–51) carry their
  spec-001 status.

Entity exposure follows `spec.md` §6; the normative entity table is
`contracts/entity-contract.md`.

---

## 1. Wire encoding (recap + this spec's update)

| Property | Value | Status |
|---|---|---|
| Transport | Modbus RTU frame in GATT notifications (ff02), slave `0x63` | [P apk] |
| Base block | FC03 read, start 7, count 45 → registers 7..51 (the only block polled) | [P apk] |
| High blocks | 1001–1045, 1046–1090, 1091–1135, 1136–1180, 2001–2045, 2046–2090 (probe-only, response shapes [V]) | [P apk] frames / [V] shapes |
| Register width | 16-bit **signed** int (big-endian unless `REG_DATA_LITTLE_ENDIAN`) | [P apk] framing / [A] endianness |
| Temperature scale | `value = int(°C × 10)` — corroborated by H04 (105.0), T02 (17.0) and pool constants 325/315 | [P] |
| **Sensor-absent sentinels** | **`{-9990, 32765}`** — both map to `unknown` (§11) | [P] (both conventions evidenced) |
| Plausibility window | raw −450…+1500 (−45.0…+150.0 °C) → outside = `unknown` + warning | [D] |
| Binary encoding | assumed 0/1 per register, polarity per-RegDef | [A] |

## 2. Family overview

| Family | Meaning | Members known | Labels known | Values observed | HA exposure (spec 003) |
|---|---|---|---|---|---|
| **T** | live temperatures | T01–T06 | 6/6 | 6/6 | sensors (3 existing [P] + 3 placeholders) |
| **H** | machine settings | H01–H06 + H70/H71/H72 [P apk] | H01–H06 | 6/6 | **none — never writable** |
| **R** | regulation thresholds (installer) | R01–R11 | 0/11 (group semantics only) | range 0.5–40.0 °C | **none — never writable** |
| **P** | filtration program | P01–P04 | 4/4 | 4/4 | **none — never writable** |
| **S** | safety inputs (switches) | S01–S06 [P apk] | S01–S03 | 3/3 | binary sensors, DIAGNOSTIC (placeholders) |
| **O** | outputs | O01–O04 | 4/4 | 4/4 | binary sensors (placeholders) |
| **D** | digital states | D01–D06 [P apk] | **0/6** — page not photographed | none | **none** (unmapped — no speculation) |
| E04, E08, F13 | fault entries [P apk — FINDINGS §11.4] | — | — | — | none (fault handling stays reg 47, spec 001) |

Unit state during observation: **idle** (compressor OFF, circulate pump
OFF, fan OFF, 4-way valve OFF) [P panel].

## 3. T family — live temperatures

| Member | Label [P panel] | Observed value [P panel] | Expected raw (×10) [A] | Entity | Numeric index |
|---|---|---|---|---|---|
| T01 | Suction Temp. | **3276.5 °C — sensor ABSENT (sentinel)** | 32765 (`0x7FFD`) or −9990 (`0xD8FA`) — which one on the wire is [V] | `suction_temp` (placeholder) | **[V] NOT PROVEN** |
| T02 | Inlet water | 17.0 °C | 170 (`0x00AA`) | `inlet_water_temp` (existing, reg 26 [P apk]) | base reg 26 [P]; identity T02≡26 [A] |
| T03 | Outlet water | 19.5 °C | 195 (`0x00C3`) | `outlet_water_temp` (existing, reg 27 [P apk]) | base reg 27 [P]; identity T03≡27 [A] |
| T04 | Coil | 19.5 °C | 195 (`0x00C3`) | `coil_temp` (placeholder) | **[V] NOT PROVEN** |
| T05 | Ambient | 18.0 °C | 180 (`0x00B4`) | `ambient_temp` (existing, reg 28 [P apk]) | base reg 28 [P]; identity T05≡28 [A] |
| T06 | Exhaust | 17.5 °C | 175 (`0x00AF`) | `exhaust_temp` (placeholder) | **[V] NOT PROVEN** |

Notes:

- T01 will likely read **absent on this unit even after calibration**
  (the suction sensor is not installed) — the entity will show `unknown`,
  which is the correct, honest state (spec §7 E-1).
- T03 and T04 both display 19.5 °C — a calibration ambiguity, resolved by
  the worksheet procedure (`contracts/calibration-contract.md` §4).
- The existing base-block addresses for inlet/outlet/ambient are proven
  [P apk, spec 001]; the *identity* with the panel's T02/T03/T05 is a
  strong hypothesis [A] — one live read (26 == 170 etc.) confirms it.

## 4. H family — machine settings (READ-ONLY POLICY, no entities)

| Member | Label [P panel] | Observed value [P panel] | Expected raw [A] | Numeric index |
|---|---|---|---|---|
| H01 | Power down memory | YES | encoding unknown [V] | **[V] NOT PROVEN** |
| H02 | Temperature unit | C | encoding unknown [V] | **[V] NOT PROVEN** |
| H03 | Comp. stop | 0.0 °C | 0 | **[V] NOT PROVEN** |
| H04 | **Exhaust Temp. High** | **105.0 °C — SAFETY THRESHOLD** | 1050 (`0x041A`) | **[V] NOT PROVEN** |
| H05 | Outlet Water Temperature Sensor | ON | encoding unknown [V] | **[V] NOT PROVEN** |
| H06 | Mode type | "Heat only 4-way valve" | encoding unknown [V] | **[V] NOT PROVEN** |
| H70–H72 | seen in the app elsewhere ("H70: ", "H71: ", "H72: ") | meaning unknown | — | **[V]** [P apk FINDINGS §9] |

Policy: **never writable from HA, no entities** (spec §6). H02 is the
display-unit setting — if the user switches the panel to °F, the wire
encoding of temperatures may change [V] (spec §7 E-2): keep the panel at
°C with this integration. H06 tells us the owner's unit is **heat-only**
— relevant for the reg-13 mode-map calibration [V].

## 5. R family — regulation thresholds (installer parameters, READ-ONLY POLICY, no entities)

| Member | Status |
|---|---|
| R01–R11 | **Count** [P apk + panel]; **individual labels NOT photographed** [V] |

Group semantics observed [P panel]: 11 parameters in degrees,
observed range **0.5–40.0 °C** (raw 5–400 [A]); the set covers
heating/cooling setpoints, regulation differentials, the power-outage
cutoff, and per-mode min/max limits. These are **installer settings**
that the regulation logic consumes — an erroneous write can damage the
unit or prevent startup (spec §6). Expected raws while idle:
**unknown per-member** [V] — only the range is known.

Policy: **never writable from HA, no entities** (spec §6).

## 6. P family — filtration program (READ-ONLY POLICY, no entities)

| Member | Label [P panel] | Observed value [P panel] | Expected raw [A] | Numeric index |
|---|---|---|---|---|
| P01 | Mode | "Nor" (Normal [A]) | encoding unknown [V] | **[V] NOT PROVEN** |
| P02 | Interval | 0 min | 0 | **[V] NOT PROVEN** |
| P03 | Duration | 3 (minutes [A]) | 3 | **[V] NOT PROVEN** |
| P04 | Advance | 0 | 0 | **[V] NOT PROVEN** |

Correction over FINDINGS §11.4 ("P03 power register"): the panel shows
P03 is the filtration **Duration** [P panel] — see `research.md` §13.

Policy: **never writable from HA, no entities** (spec §6).

## 7. S family — safety inputs (binary sensors after calibration)

| Member | Label [P panel] | Observed value [P panel] | Expected raw [A] | Entity | Numeric index |
|---|---|---|---|---|---|
| S01 | HP switch | OFF | 0 | `hp_switch` (placeholder, DIAGNOSTIC) | **[V] NOT PROVEN** |
| S02 | LP switch | OFF | 0 | `lp_switch` (placeholder, DIAGNOSTIC) | **[V] NOT PROVEN** |
| S03 | Flow switch | ON | 1 | `flow_switch` (placeholder, DIAGNOSTIC) | **[V] NOT PROVEN** |
| S04 | *label not photographed* | — | — | none | [V] |
| S05 | *label not photographed* | — | — | none | [V] |
| S06 | *label not photographed* | — | — | none | [V] |

- S04–S06 exist [P apk — FINDINGS §11.4 "S01–S06"]. Hypothesis only: the
  status-page names "HP/LP/Flow/**Remote/Mode** switch" [P apk —
  FINDINGS §12] suggest S04 = Remote switch, S05 = Mode switch —
  **UNVERIFIED, do not rely on it** [A]. No entity until photographed
  and pinned.
- Polarity: S03 reads ON while the unit is idle [P panel] — semantics
  ("flow detected" vs "switch healthy/closed") undecidable from one
  photo [V]; per-RegDef polarity exists for exactly this reason
  (`research.md` §9).
- Correction over FINDINGS §11.4 ("setpoint registers"): S = safety
  inputs [P panel] — `research.md` §13.

## 8. O family — outputs (binary sensors after calibration)

| Member | Label [P panel] | Observed value [P panel] | Expected raw [A] | Entity | Numeric index |
|---|---|---|---|---|---|
| O01 | Compressor | OFF | 0 | `compressor` (existing placeholder) | **[V] NOT PROVEN** — expected home: base regs 29–46 [A] |
| O02 | Circulate pump | OFF | 0 | `circulate_pump` (existing placeholder) | **[V] NOT PROVEN** — expected home 29–46 [A] |
| O03 | High fan | OFF | 0 | `high_fan` (placeholder) | **[V] NOT PROVEN** |
| O04 | 4-way valve | OFF | 0 | `four_way_valve` (existing placeholder) | **[V] NOT PROVEN** — expected home 29–46 [A] |

- The three existing placeholder keys are **retained unchanged** (they
  predate this spec and their labels are now panel-confirmed) —
  cross-identity with the status-page names is [A] (§14).
- Open question (no speculation): FINDINGS §12 mentions "High/Low fan" —
  the panel shows only O03 "High fan" in O01–O04; where a low-fan state
  lives (another O member? D? the same register?) is **[V]**.

## 9. D family — digital states (KNOWN BUT UNMAPPED)

D01–D06 exist [P apk — FINDINGS §11.4 "digital/state group"]. **The D
page has not been photographed**: no labels, no values, no semantics —
nothing more is known. Per the task rule this spec does **no speculative
work**: no placeholders, no entities, no probe annotation. Required
before any D work: one panel photograph of the D page + calibration
(`research.md` §12).

## 10. Numeric register index — NOT PROVEN

> **The panel observation proves labels and displayed values. It does
> not prove numeric register indices for any T/H/R/P/S/O member.**

- The app's parameter view displays no addresses.
- The APK's `VR.Yzd` label→index map is built from runtime immediates
  hidden by `dedup_instructions` [P apk — FINDINGS.md §6/§11.4]; only
  the base block (7–51) was recovered from a pool list (~80 % certain,
  spec 001).
- Consequently: every new entity in this spec ships as `address=None`
  and is **created only after a live calibration pins its register**
  (`contracts/calibration-contract.md`). Nothing in the shipped code
  asserts a numeric index for T01/T04/T06, S01–S03, O01–O04, or any
  H/R/P member.
- The probe (read-only) + the §13 worksheet are the sanctioned path to
  turning each `[V]` index into `[P live]`.

## 11. Sentinels — the two conventions

| Convention | Raw | Evidence | Status |
|---|---|---|---|
| App-defined "sensor absent" | **−9990** (−999.0 °C; `0xD8FA` unsigned view) | APK status-page definition [P apk — FINDINGS §12] | [P] |
| Firmware max-value marker | **32765** (`0x7FFD`; displayed 3276.5 °C) | Panel: T01 Suction on this unit [P panel] | [P] |

- Which convention applies to which register/family on the wire:
  **[V]** — one live read closes it (finding the suction register is
  itself a calibration anchor: look for 32765 or −9990).
- Both are members of `SENTINEL_RAWS` → state `unknown`
  (`decode-contract.md`).
- The vendor app **displays** 3276.5 °C for an absent sensor [P panel];
  this integration never does (spec §7 E-1/E-7).
- Out-of-plausibility raws (outside −450…+1500) are treated as absent
  with a warning — the guard against a third, unknown convention.

## 12. Scale — corroborated

`register = int(°C × 10)` — established by pool constants 325/315 [P apk]
and corroborated by H04 = 105.0 °C and T02 = 17.0 °C [P panel]. No
change to `TEMP_SCALE = 10.0`.

## 13. Observed-value worksheet (calibration anchors)

Idle-state raws **predicted** from the panel photo (displayed ×10 [A] —
encoding unverified until the live read):

| Member | Displayed | Predicted raw (hex) | Note |
|---|---|---|---|
| T01 Suction | 3276.5 (absent) | **32765 (`0x7FFD`) or −9990 (`0xD8FA`)** | whichever appears = this register's sentinel convention [V] |
| T02 Inlet | 17.0 °C | 170 (`0x00AA`) | expect base reg 26 [A] |
| T03 Outlet | 19.5 °C | 195 (`0x00C3`) | expect base reg 27 [A] |
| T04 Coil | 19.5 °C | 195 (`0x00C3`) | ambiguous with T03 — disambiguate per contract §4 |
| T05 Ambient | 18.0 °C | 180 (`0x00B4`) | expect base reg 28 [A] |
| T06 Exhaust | 17.5 °C | 175 (`0x00AF`) | |
| H03 Comp. stop | 0.0 °C | 0 | |
| H04 Exhaust high | 105.0 °C | 1050 (`0x041A`) | distinctive — likely unique in a dump |
| S01 / S02 | OFF / OFF | 0 / 0 [A] | |
| S03 Flow | ON | 1 [A] | polarity to confirm live |
| O01 / O02 / O03 / O04 | all OFF | 0 [A] | |
| P02 / P03 / P04 | 0 / 3 / 0 | 0 / 3 / 0 [A] | P01 "Nor" encoding unknown |
| R01–R11 | 0.5–40.0 °C range | 5–400 | per-member values unknown |

Procedure (normative version in `contracts/calibration-contract.md`):
probe base + high blocks while the unit is in the same idle state →
correlate → pin addresses in `registers.py` → restart → verify entities
against the panel.

## 14. Cross-identity hypotheses (NOT proof)

| Hypothesis | Evidence shape | Status |
|---|---|---|
| T02 ≡ reg 26, T03 ≡ reg 27, T05 ≡ reg 28 | labels match; base addresses proven independently | [A] — one live read confirms |
| O01/O02/O04 ≡ the placeholders expected in base regs 29–46 | FINDINGS §12 status names = panel O labels | [A] — expected home |
| T01/T04/T06 live in base 29–46 or in high blocks 1001–1180 | status-page placement suggests the status high block | [V] — determines whether entities can be created now (base) or need phase B (high-block polling, `research.md` §10) |

## 15. Entity exposure summary

| Will become an entity (after pinning) | Never an entity (this spec) |
|---|---|
| T01 suction, T04 coil, T06 exhaust (sensors) | all H members (incl. H70–H72) |
| T02/T03/T05 (already entities, regs 26/27/28 [P]) | all R members |
| S01 HP, S02 LP, S03 Flow (binary, DIAGNOSTIC) | all P members |
| O01 Compressor, O02 Circulate pump, O03 High fan, O04 4-way valve (binary) | all D members (unmapped) |
| — | E04/E08/F13 fault entries (faults stay reg 47, spec 001) |

Existing non-family entities unchanged: climate (8/13/14), fault code
(47), firmware (48–51), probe button/service.

## 16. Reconciliation with FINDINGS

Family-semantics corrections (S/R/P) and confirmations (T/H/O) are
recorded in `research.md` §13; per-family notes appear in §5–§8 above.
FINDINGS.md itself is untouched (historical analysis record); this data
model + `contracts/calibration-contract.md` supersede its §11.4 family
guesses wherever they conflict.