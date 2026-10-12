# Data model 015 — the four pushed blocks, register by register

The consolidated register→quantity evidence of the pushed plane, decoded
from the four complete CRC-verified 0.10.0 frames (research.md §1-§2).
This file is the reference the Coder's `registers.py` constants must
match; every row carries its status and its evidence source.

Legend: **[P]** proven (decode/CRC/panel/photo), **[P apk]** recovered
from the APK, **[P panel]** photographed in the app's parameter view,
**[P owner]** the owner's observation, **[A]** assumed (window/label
hypothesis), **[V]** to-verify (the named experiment decides). Scale:
temperatures are raw = int(°C × 10) [P]; sentinels {-9990, 32765} decode
to `unknown` [P].

---

## 1. Block 1001-1045 — machine settings (H-family + unknowns)

Static across ≥ 2 days / ≥ 2 connects [P — byte-identical frames].
45 registers, 9 non-zero.

| reg | quantity | value (this capture) | status | evidence |
|---|---|---|---|---|
| 1001-1011 | unknown/unused | 0 | [V] | decode |
| 1012 | unknown flag | 1 | [V] | decode — never seen to change |
| 1013-1017 | unknown/unused | 0 | [V] | decode |
| **1018** | **H01 Power down memory** | 1 = YES | [A] | H-window fit (spec 013 §2.1) |
| **1019** | **H02 Temperature unit** | 0 = C | [A] | H-window fit; encoding unverified |
| **1020** | **H03 Comp. stop** | 0 → 0.0 °C | [A] | H-window fit |
| **1021** | **H04 Exhaust Temp. High** (safety threshold) | 1050 → **105.0 °C** | **[P panel]** | exact panel match |
| **1022** | **H05 Outlet Water Temp. Sensor** | 1 = ON | [A] | H-window fit |
| **1023** | **H06 Mode type** | 2 = heat-only | [A] | H-window fit; options Chiller/Heat pump/Heat only [P apk] |
| 1024-1036 | unknown/unused | 0 | [V] | decode |
| 1037 | unknown (P-family candidate) | 2 | [V] | decode |
| 1038 | unknown (P02 Interval candidate) | 30 | [A-weak] | decode; panel P02=0 mismatch — state change or wrong member |
| 1039 | **P03 Duration candidate** | 3 | [A] | matches panel P03=3 [P panel] |
| 1040 | unknown (P01 Mode candidate) | 1 = "Nor"? | [A-weak] | decode |
| 1041-1045 | unknown/unused | 0 | [V] | decode |

Exposure policy: **none of these registers is an entity, and NONE is
ever writable** (spec 003 §6 — the H-family includes safety thresholds;
H04 is the worked example).

## 2. Block 1046-1090 — regulation / defrost / EEV thresholds

Static across ≥ 2 days [P]. 45 registers, 16 non-zero. The
control-parameters page's label list [P apk, research §5.2] names the
editable members ("Cooling set", "Heating set", "Auto. Set", "Cool
diff", …, "Max. heat") — the per-member register alignment is [V] until
the logcat oracle (quickstart §2) or the setpoint-diff pins it.

| reg | value | /10 | candidate label | status |
|---|---|---|---|---|
| **1046** | 290 | 29.0 °C | Cooling set / Heating set | [A-weak] |
| 1047 | 5 | 0.5 °C | Cool diff / differential family | [A-weak] |
| 1048 | 5 | 0.5 °C | differential family | [A-weak] |
| **1049** | 150 | 15.0 °C | **Heating set candidate** (winter pool setpoint shape) | [A-weak] |
| **1050** | 400 | 40.0 °C | Max. heat / Max. cool (matches FALLBACK_MAX_TARGET) | [A-weak] |
| **1051** | 270 | 27.0 °C | **setpoint candidate** (classic pool setpoint) | [A-weak] |
| 1052 | 5 | 0.5 °C | differential family | [A-weak] |
| 1053 | 5 | 0.5 °C | differential family | [A-weak] |
| 1054 | 100 | 10.0 °C | Min. cool / Min. heat | [A-weak] |
| **1055** | 350 | 35.0 °C | Max. cool | [A-weak] |
| **1056** | -70 | -7.0 °C | Stat Defrosting Temp (defrost start) | [A] |
| 1057 | 130 | 13.0 °C | End Defrosting Temp / EEV | [A-weak] |
| 1058 | 40 | 4.0 °C | Super Heat (EEV) | [A-weak] |
| 1059 | 8 | — | Duration/Cycle member | [V] |
| 1060 | 3 | — | Defrosting Cycle member | [V] |
| 1061-1067 | 0 | — | unused/zero | [V] |
| **1068** | 270 | 27.0 °C | second setpoint candidate (the later "Auto. set" label — the list has two) | [A-weak] |
| 1069-1090 | 0 | — | unused/zero | [V] |

**The setpoint candidate set (the whole pushed plane):**
{1046=29.0, 1049=15.0, 1051=27.0, 1055=35.0, 1068=27.0} — collapsed by
the owner's displayed value + the ±0.5 °C diff (quickstart §1 step S).

Exposure policy: **never writable, never an entity** (spec 003 §6). A
pinned user-setpoint member may later become the push-mode climate's
TARGET read behind the confirmation gate (contract §7) — read only.

## 3. Block 2001-2045 — the unknown block (complete decode, this spec)

45 registers, 5 non-zero [P — research §2.3]. No setpoint-shaped value,
no sentinel, no firmware group [P scan].

| reg | value | candidate meaning | status |
|---|---|---|---|
| 2001-2009 | 0 | unknown/unused | [V] |
| **2010** | 2001 (0x07D1) | self-reference (block base address / marker / device-area code) — NOT a temperature (200.1 °C fits nothing) | [V] |
| 2011 | 0 | zero flag — S-family candidate window member | [V] |
| **2012** | 1 | **flag — the strongest POWER candidate in the 2000-series** | [V — single capture, no liveness] |
| 2013-2016 | 0 | zero flags — S/O-family candidate window members | [V] |
| **2017** | 13 | unknown (not temp-shaped; count/enum/timer) | [V] |
| **2018** | 168 | temp-shaped (16.8 °C) or runtime (168 h = 1 week) — unknown | [V] |
| 2019-2033 | 0 | unknown/unused — no clean S01/S02/S03 = 0/0/1 fit (research §6.2) | [V] |
| **2034** | 4 | **small enum — the strongest MODE candidate in the 2000-series** | [V — single capture] |
| 2035-2045 | 0 | unknown/unused | [V] |

**2026-10-12 update (spec 015 Phase A results, research 015 §8.1):** the
owner's toggle-and-diff pinned two homes in this block — **2025 = Power**
(`0000` off → `0001` on → `0000` off across the app's power slide +
revert) and **2034 = Mode** (`0000` → `0004` → `0000` across the same
toggle), both **[P owner diff-revert]**. The 2012 flag candidate is
EXCLUDED for power (static `0001` across the whole diff). The mode
VALUE mapping (4 → heat) stays [A] — this unit is heat-only. No
setpoint home was identified in any pushed block.

Exposure policy: **no entity, never writable** until a diff experiment
pins a member AND the confirmation gate opens; even then: read-only
diagnostics/entities per contract §7 — a read correlation never proves
write semantics (contract §8).

## 4. Block 2046-2090 — the live T-window (calibrated)

45 registers, 6 non-zero. Third independent capture; values drift live.

| reg | quantity | value | status |
|---|---|---|---|
| **2046** | **T01 Suction** | 32765 (0x7FFD sentinel) → `unknown` | **[P]** sensor absent on this unit |
| **2047** | **T02 Inlet water** | 130 → 13.0 °C | **[P owner photo]** |
| **2048** | **T03 Outlet water** | 125 → 12.5 °C | **[P owner photo]** |
| **2049** | **T04 Coil** | 125 → 12.5 °C | **[P owner photo]** |
| **2050** | **T05 Ambient** | 125 → 12.5 °C | **[P owner photo]** |
| **2051** | **T06 Exhaust** | 135 → 13.5 °C | **[P owner photo]** |
| 2052-2090 | unknown/unused | 0 | [V] |

Exposure policy: temperature entities behind `PUSH_TEMP_CONFIRMED`
(shipped, spec 013/014) — unchanged by this spec.

## 5. The base block (7-51) — the reference plane, NOT pushed

The complete 45-name list [P apk — research §5.1] fixes the base map
this project already implements (`registers.py`):

| reg | name | reg | name |
|---|---|---|---|
| 7 | SetPVReadyMode | 30 | SetTimerSlientStopTime |
| **8** | **Power** | 31 | SetTimerSlientStopTime… (see list) |
| 9 | SetFrequencyMode | 32/33 | EnTimer1On / EnTimer1Off |
| 10 | SetFanMode | 34-37 | SetTimer1 Start/Stop Hour/Minute |
| **11** | **EnBoostMode** | 38 | SetCompType |
| 12 | EnAPPTimeUpdate | 39 | Sys1CompWorkingFreq |
| **13** | **Mode** | 40/41 | SetHeat/CoolMaxFreq |
| **14** | **SetCurrentTemp** | 42-44 | EnSupportAI / SetAIMode / EnAIPower |
| 15 | ModeType | 45 | CompressorCurrentDetect |
| 16 | SetTempUnit | 46 | InverterBoardACInputVoltage |
| 17 | EnOutletWaterTempSensor | **47** | **Fault1** |
| 18 | EnSlient | 48-51 | MainBoard/Display SoftwareVer/Code |
| **19-21** | **SetCool/Heat/AutoTargetTemp** | | |
| **22-25** | **SetCool/Heat Min/MaxTargetTemp** | | |
| **26-28** | **InletWaterTemp / OutletWaterTemp / AT** | | |

This dataset is served to us by no grammar (no FC03 answer, no start-7
block — six runs [P]). It remains the write allowlist's plane
({8, 13, 14}) and the presumed source of the app's home-screen control
display in world (a) [V — E5].

## 6. The never-pushed blocks (1091-1180)

Queried by the app [P apk], never observed in any push [P — censuses].
Candidate home of the S/O/D/P families (research §6.2) and possibly
control-adjacent states (D01-D06 = "digital states", page never
photographed [V]). Readability test: the probe service, block=1091/1136
(quickstart §4).

## 7. The control-home candidate register (the whole plane, one table)

| quantity | candidates (priority order) | status | decider |
|---|---|---|---|
| **Power (read)** | **2025 — PINNED** [P owner diff-revert 2026-10-12: 0 → 1 → 0]; (2012 EXCLUDED — static across the diff); base 8 (world a, not pushed) | **[P]** | re-verify vs the app after opening the gate (quickstart §5) |
| **Mode (read)** | **2034 — PINNED** [P owner diff-revert 2026-10-12: 0 → 4 → 0]; base 13 (world a) | **[P]** (the VALUE mapping 4 → heat is [A], D-3) | mode-diff / logcat oracle; quickstart §5 re-verify |
| **Setpoint (read)** | none identified [P scan — no setpoint-shaped value in any push]; 1046 / 1049 / 1051 / 1055 / 1068 remain [A-weak] candidates; base 14 (world a) | [V] | displayed-value match + ±0.5 °C diff (quickstart §1 step S) |
| **Power/Mode/Setpoint (write)** | base {8, 13, 14} [P apk name list]; vendor's actual addresses = the logcat oracle | [V] | logcat §2 — the allowlist waits for THIS |

Rules: a candidate becomes a mapping ONLY through the diff/logcal/oracle
experiments (contract §7); a read-pinned candidate never implies write
semantics (contract §8); the confirmation gate mirrors the
`PUSH_TEMP_CONFIRMED` precedent (per-key option OR global switch).