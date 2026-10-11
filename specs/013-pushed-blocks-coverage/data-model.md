# Data model 013 — the pushed blocks: register → quantity mapping

Status: derived from `research.md` §2-§3 (every table re-verified
byte-by-byte against the pinned frame vectors + reference CRC for this
spec). Legend: **[P]** PROVEN (CRC-verified frame / APK / panel),
**[A]** ASSUMED (strong, one calibration run corrects), **[V]** TO-VERIFY.
This file does NOT change the base-block mapping (registers 7-51,
spec 001/003) — it documents the **pushed plane** and its mapping into
HA quantities. `registers.py` remains the single source of truth; the
mapping below is what the Coder transcribes into it (spec 013 FR-5).

---

## 1. The two data planes [P]

| Plane | Dataset | Registers | Read mechanism | Status |
|---|---|---|---|---|
| **Base plane** | status/control: power, mode, setpoint, bounds, inlet/outlet/ambient, fault, firmware | 7-51 | FC03 read `63030007002d3c54` | [P apk] — **answers never observed on this unit** [P, 3 production runs] |
| **Pushed plane** | parameter view: T/H/R families (+ unknowns incl. S/O/D/P candidates) | 1001-1180, 2001-2090 | spontaneous block frames on ff02 | [P — CRC-verified frames] |

No offset relates the planes [P — research §3]. The same *quantity*
(inlet water temperature) can appear in both planes under different
registers — the **merge policy** (contracts/push-harvest.md §4) decides
which plane an entity reads.

## 2. Block 1001-1045 — H-family settings block

Frame [P]: `631003e9002d5a…563a` (99 B, CRC 0x3A56, pinned vector).

| reg | raw | quantity | Status |
|---|---|---|---|
| 1012 | 1 | unknown flag (D/P-family candidate) | [V] |
| 1018 | 1 | H01 Power down memory (YES) | [A] |
| 1019 | 0 | H02 Temperature unit (0 = C) | [A] encoding / [V] semantics |
| 1020 | 0 | H03 Comp. stop (0.0 °C) | [A] |
| **1021** | **1050** | **H04 Exhaust Temp. High (105.0 °C — safety threshold)** | **[P — panel match]** |
| 1022 | 1 | H05 Outlet Water Temp. Sensor (ON) | [A] |
| 1023 | 2 | H06 Mode type (heat-only enum) | [A] |
| 1037 | 2 | unknown (P-family candidate) | [V] |
| 1038 | 30 | unknown (P02 Interval candidate — panel showed 0; unit mismatch [V]) | [V] |
| 1039 | 3 | unknown (P03 Duration = 3 candidate) | [A-weak] |
| 1040 | 1 | unknown (P01 "Nor" candidate) | [A-weak] |
| rest | 0 | unused / not-photographed members | [V] |

Exposure: **no entities** (spec 003 §6 policy — H never writable, no
entities); values visible in the probe/harvest logs only.

## 3. Block 1046-1090 — R-family thresholds block

Frame [P]: `63100416002d5a…8576` (99 B, CRC 0x7685, pinned vector).

| reg | raw | /10 | | reg | raw | /10 |
|---|---|---|---|---|---|---|
| 1046 | 290 | 29.0 | | 1054 | 100 | 10.0 |
| 1047 | 5 | 0.5 | | 1055 | 350 | 35.0 |
| 1048 | 5 | 0.5 | | 1056 | -70 | -7.0 |
| 1049 | 150 | 15.0 | | 1057 | 130 | 13.0 |
| 1050 | 400 | 40.0 | | 1058 | 40 | 4.0 |
| 1051 | 270 | 27.0 | | 1059 | 8 | — |
| 1052 | 5 | 0.5 | | 1060 | 3 | — |
| 1053 | 5 | 0.5 | | 1068 | 270 | 27.0 |

Quantity: R-family regulation thresholds, ladder at 1046-1060 [A-strong
— panel range 0.5-40.0 fits; 11 photographed members vs 15 ladder
values: the extra members are unphotographed R/EEV/defrost parameters
[V]]. 1051/1068 (27.0) are setpoint-shaped [A-weak — never entity data
without calibration]. Exposure: no entities (spec 003 §6 policy).

## 4. Block 2001-2045 — unknown block

Header [P] (`631007d1002d5a…9200`, 99 B declared); **data
underdetermined** [V — research 008 §2.4: 40 CRC-compatible
reconstructions]. Visible islands: 1, 13, 168 (**16.8 °C** — live-temp
candidate [A]), 64. Candidate homes for the S/O/D/P families [A].
**The FR-1 full-hex capture closes this table** — data-model update
follows the first clean capture (U-1).

## 5. Block 2046-2090 — T-family live temperatures block

Frames [P ×2]: `631007fe002d5a…f49b` (CRC 0x9BF4) and `…a565`
(CRC 0x65A5), both pinned vectors.

| reg | push 1 | push 2 | /10 | quantity | Status |
|---|---|---|---|---|---|
| **2046** | 32765 (0x7FFD) | 32765 | — | **T01 Suction — ABSENT** (sentinel → entity `unknown`) | **[P-grade — the unit's only absent sensor]** |
| **2047** | **140** | **145** | **14.0 → 14.5** | **T02 Inlet water — LIVE** | **[P liveness]** / [A identity — calibration pins] |
| 2048 | 140 | 140 | 14.0 | T03 Outlet water | [A] |
| 2049 | 140 | 140 | 14.0 | T04 Coil | [A] |
| 2050 | 135 | 135 | 13.5 | T05 Ambient | [A] |
| 2051 | 150 | 150 | 15.0 | T06 Exhaust | [A] |
| 2052-2090 | 0 | 0 | — | unused tail | [P zeros] |

Decode rules (unchanged, spec 003 decode-contract): sentinels
`{-9990, 32765}` → `unknown`; plausibility window −45.0…+150.0 °C;
÷10 scale. **The vendor's 3276.5 °C display bug must never replicate**
— reg 2046 decodes to `unknown`, which is the honest state.

## 6. Entity mapping (the shipped surface, FR-5)

`registers.py` gains (single source of truth; Coder transcribes):

```python
# --- pushed plane (spec 013) ---
PUSH_TEMP_BLOCK_START = 2046          # [P] the T-family block
PUSH_TEMP_CONFIRMED = False           # calibration switch (quickstart §2)

# member -> pushed register; the T-window hypothesis (research §2.4).
# A permuted calibration outcome is a one-line edit here.
PUSHED_TEMPS: dict[str, int] = {
    "suction_temp":   2046,   # T01 — sentinel on this unit -> unknown
    "inlet_water_temp": 2047, # T02 — live-proven register [P]
    "outlet_water_temp": 2048,# T03
    "coil_temp":     2049,   # T04
    "ambient_temp":  2050,   # T05
    "exhaust_temp":  2051,   # T06
}
```

| Entity key | Base-plane reg [P apk] | Pushed-plane reg | Creation rule |
|---|---|---|---|
| `inlet_water_temp` | 26 | 2047 [A] | exists today (base-pinned); **value source** = base answer if fresh, else pushed (merge policy) |
| `outlet_water_temp` | 27 | 2048 [A] | same |
| `ambient_temp` | 28 | 2050 [A] | same |
| `coil_temp` | None [V base] | 2049 [A] | created only when `PUSH_TEMP_CONFIRMED` (push plane) |
| `exhaust_temp` | None [V base] | 2051 [A] | same |
| `suction_temp` | None [V base] | 2046 [P-grade] | same (reads `unknown` on this unit — correct) |
| `fault_code` | 47 | — none identified | base-plane only; **not created in push mode** |
| `firmware` | 48-51 | — none identified | base-plane only; not created in push mode |
| climate (8/13/14) | base plane | — none identified | **not created in push mode** (no honest control data) |
| O01-O04, S01-S03 binaries | None [V] | — unknown (2001-2045 candidate) | not created until U-1 + pinning |
| `push_stream` (diagnostic) | — | — | always created in push mode (FR-6) |

Integrity rules (extension of `check_registry_integrity`, FR-7):
pushed addresses must lie in 2001-2090; no duplicate pushed addresses;
base-plane and pushed-plane addresses of one key never collide with
another key's address in the same plane.

## 7. Merge policy summary (normative version: contracts/push-harvest.md §4)

- A base-plane answer (if the unit ever answers) **overrides** pushed
  values for the same key — the base block is the app's own status
  dataset and the richer one (control + fault + firmware).
- In push mode (no base answers), a key's value comes from its pushed
  register **only when `PUSH_TEMP_CONFIRMED`**; until then the pushed
  values are visible in logs/diagnostics only — **no unconfirmed data
  under a certain name** (repo rule).
- Sentinels and plausibility failures decode to `unknown` on both
  planes identically (spec 003 decode-contract — unchanged).

## 8. What this data model does NOT claim

- The T-window member order (which register is inlet vs outlet…) —
  [A] until the calibration run (U-2). The window itself is anchored.
- Any home for power/mode/setpoint/fault/firmware in the pushed
  plane — [P absence] for blocks 1001/1046/2046; block 2001-2045 is
  [V] (U-1).
- That the push stream runs indefinitely — [V] (U-3; FR-4 monitors).
- S/O/D/P family homes — [V] pending the 2001 capture.