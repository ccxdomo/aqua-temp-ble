# Spec 023 — Data model: the fault word (register 2074)

The single source of truth in code is `registers.py` (constants +
decode helpers) + the coordinator snapshot (`AquaTempData.registers`,
the 2046-block capture). This file mirrors the fault mapping for
human readers. Evidence tags as in `research.md` ([P] proven / [A]
assumed / [V] open).

## 1. The register

| Property | Value | Evidence |
|---|---|---|
| Address | **2074** | [P — owner diff-revert 2026-10-12] |
| Home block | 2046 (the T-family block, regs 2046-2090) | [P — the block's frame grammar; research 023 §1.2] |
| Position in block | index 28 (0-based); data offset 56 bytes; frame bytes 63-64 (0-based) | [P arithmetic] |
| Byte order | big-endian (0x0100 = 256, not 0x0001) | [P — three anchors, research 023 §1.3] |
| Healthy value | **0** | [P — the baseline/cleared captures, the owner's diff] |
| E03 value | **256 = 0x0100 = bit 8** | [P — the byte-exact E03 frame; panel displayed E03] |
| Write status | **never writable** — refused by `ensure_writable` forever (no allowlist entry) | [P — contracts 003 §6 / 023 §5] |

## 2. The decode rules (normative: contracts/fault-exposure-contract.md)

```
raw = data.registers.get(2074)      # the fresh 2046-block capture
raw is None            -> state unknown (never a guessed OFF)
stale/silent stream     -> the cycle fails -> entity unavailable
raw == 0               -> OFF (clear)
raw != 0               -> ON (fault present)
```

Attributes (evidence, never the state): `register` (2074), `raw`,
`hex` (`0x0100`), `set_bits` (the set-bit indices, arithmetic only —
e.g. `[8]`), `code` (see §3).

## 3. The raw <-> panel-code map (data, one row per PROVEN observation)

| raw | hex | bits | code | Evidence |
|---|---|---|---|---|
| 0 | 0x0000 | — | *(clear — no code attribute)* | [P — the healthy/cleared captures] |
| **256** | **0x0100** | **[8]** | **"E03"** | [P — panel displayed E03 while 2074 read 256, 2026-10-12] |
| other non-zero | — | arithmetic | `"unknown code (raw N)"` | the contract 022 §3 honesty rule |

In code: `PUSHED_FAULT_CODE_MAP = {256: "E03"}` — extending the map
requires one recorded observation per row (the panel's code + the
raw, in its diff-revert context — quickstart §4), then a one-line
edit.

## 4. The bit inventory

Observed on this unit:

| bit | raw value | observed state | Evidence |
|---|---|---|---|
| 8 | 0x0100 (256) | the E03 fault run (un-plumbed dry start) | [P] |
| 0-7, 9-15 | — | never observed set | no observation |

**No bit carries a name.** The vendor's ten-flag list
(research 023 §2.1) is the candidate vocabulary, but the
list-order-as-bit-order mapping is [A] and the list's own context is
the cloud model — so entity code never names bits; `set_bits` is
arithmetic only. For the record, the [A] hypothesis table (IF the
ten-name list order were the bit order of this word):

| bit | name under the list-order hypothesis [A] |
|---|---|
| 0 | Sys1HighPressureSwitch |
| 1 | Sys1LowPressureSwitch |
| 2 | WaterFlowSwitch |
| 3 | EmergencySwitch |
| 4 | ModeSwitch |
| 5 | Master/SlaveSwitch |
| 6 | Sys1CompState |
| 7 | InletWaterTempSensorFault |
| **8** | **OutletWaterTempSensorFault** — the observed E03 bit would be THIS flag, not the flow switch [A] |
| 9 | ATSensorFault |

Note the honest tension: the owner's physical expectation (E03 =
the flow fault, WaterFlowSwitch) and the list-order hypothesis
(bit 8 = OutletWaterTempSensorFault) disagree. Both readings stay
[A]/[V]; neither enters code. What E03 means awaits the vendor's
table (`assets/faults/faults_*.json` — still not on this host) or
future recorded observations.

## 5. Where 2074 sits in the block (the pushed-plane map, updated)

Block 2046 (regs 2046-2090), the T-family block — now with its
proven status member:

| Register | Family | Quantity | Entity | Evidence |
|---|---|---|---|---|
| 2046 | T01 | Suction temperature | sensor (sentinel -> `unknown`) | [P] |
| 2047 | T02 | Inlet water temperature (also the climate's `current_temperature`) | sensor | [P] |
| 2048 | T03 | Outlet water temperature | sensor | [P] |
| 2049 | T04 | Coil temperature | sensor | [P] |
| 2050 | T05 | Ambient temperature | sensor | [P] |
| 2051 | T06 | Exhaust temperature | sensor | [P] |
| 2052-2073 | — | zeros on this unit (unknown/unused) | none | [P — the diff] |
| **2074** | — | **the fault word (bitfield)** | **binary_sensor "Fault" (problem)** | **[P — diff-revert]** |
| 2075-2090 | — | zeros on this unit (unknown/unused) | none | [P — the diff] |

Diagnostics: 2074 rides the `Push stream` `control_candidates`
attributes and the probe store-dump labels (spec 023 FR-5/FR-9).

## 6. The classic plane (unchanged, dead on this unit)

Base register 47 = "Fault1" [P apk — the 45-name list]. The unit
never serves the base block (law L1, spec 020): no classic fault
entity, none creatable. The classic `fault_code` RegDef keeps its
base-plane home and stays uncreated in push mode (the sensor
platform's skip rule); the fault word 2074 is a DIFFERENT,
pushed-plane home — it does not alias the classic key (entity keys
stay disjoint: `fault` (binary, pushed) vs `fault_code` (classic,
never created on this unit)).

## 7. The snapshot shape (delta to spec 020's data-model §4)

```
AquaTempData.registers   # the 2046-block raws ALREADY carry 2074 —
                         # no new coordinator field is needed
push_control_raws        # gains 2074 automatically via
                         # PUSH_CONTROL_CANDIDATES (diagnostics)
```

No new field on `AquaTempData`. The fault decode reads
`data.registers` through the shared helpers — the same source
discipline as the temperatures (one quantity, one source).