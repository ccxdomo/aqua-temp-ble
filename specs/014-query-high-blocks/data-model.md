# Data model 014 — the request/response model and the block → quantity mapping

Status: derived from `research.md` §1-§8 (every table row carries its
status tag; the frame vectors are pinned in `modbus.py` self-test §10
and were re-verified for this spec). This file does NOT change the
base-block mapping (registers 7-51, spec 001/003) nor the pushed-plane
mapping of `data-model 013 §6` — it documents the **response model**
(the unit answers requests with the parameter dataset) and re-states
the cartography with the 2026-10-11 22:41 second capture. Legend:
**[P]** PROVEN, **[A]** ASSUMED (one calibration run corrects),
**[V]** TO-VERIFY.

---

## 1. The request/response model [P — research 014 §5]

| Request (ours, allowlisted) | The unit's answer (observed 6 runs) |
|---|---|
| FC03 read `63030007002d3c54` (start 7, qty 45) — Write Command or acknowledged | **A report burst**: block frames `63 10 start qty 5A data:90 crc` for starts 1001/1046/2001/2046 (the parameter dataset), starting 0.2-5 s after the TX; composition varies per window [P] |
| — nothing sent (idle window, zero-TX listen) | **silence** — 0 frames in the idle window and in the 30 s zero-TX coordinator window [P] |

Consequences (the design's axioms):

1. The request's register address (7) does NOT select the response's
   content — the response is the unit's canonical dataset dump [P obs].
2. The base block (7-51) is served by NO grammar on this firmware:
   no FC03 answer, no Modbus exception, no start-7 block frame — ever
   [P — absence, six runs].
3. The report stops within ≤ 30 s after requests stop [P — bounds];
   therefore the read plane must request, then listen (spec 014 FR-1).

The block frame grammar [P — CRC-verified on live frames,
research 008 §2.1]:
`63 10 start:2 qty:2 bytecount=0x5A data:90 crc:2LE` — 99 bytes,
45 big-endian signed int16 registers per frame.

## 2. Block → content cartography (confirmed by the second capture)

| Block | Content | Confidence | Exposure |
|---|---|---|---|
| **1001-1045** | H-family settings window 1018-1023 (H04 = 105.0 at reg 1021 [P panel-match]) + flags 1012/1037-1040 | window [A-strong], anchor [P] | log/diagnostic only — never entities, never writable (spec 003 §6) |
| **1046-1090** | R-family ÷10 threshold ladder 1046-1060 + 1068 | [A-strong] | log/diagnostic only |
| **2001-2045** | UNKNOWN — but its **full 99-byte hex exists in the 22:41 log** (the instant harvest return proves all four starts were captured, research 014 §8.3) | content [V] — one paste closes it (quickstart §0) | follow-up spec's decision |
| **2046-2090** | **T-family live temperatures, window 2046-2051** — second capture 22:41:13: `2046=32765 2047=140 2048=135 2049=135 2050=135 2051=145` | window [P — sentinel anchor ×2]; member order [A — calibration run] | temperature entities behind `PUSH_TEMP_CONFIRMED` (unchanged) |

## 3. The T-window — both captures side by side

| reg | capture 1 (research 008 §2.5) | capture 2 (22:41:13) | quantity | Status |
|---|---|---|---|---|
| 2046 | 32765 (0x7FFD) | 32765 | T01 Suction — ABSENT (sentinel → `unknown`) | **[P — the unit's only absent sensor; anchor ×2]** |
| 2047 | 140 → **145** (live change) | 140 | T02 candidate (inlet water) | liveness [P]; identity [A] |
| 2048 | 140 | 135 | T03 candidate (outlet water) | [A] |
| 2049 | 140 | 135 | T04 candidate (coil) | [A] |
| 2050 | 135 | 135 | T05 candidate (ambient) | [A] |
| 2051 | 150 | 145 | T06 candidate (exhaust) | [A] |

Decode rules unchanged (spec 003 decode-contract): sentinels
`{-9990, 32765}` → `unknown`; plausibility −45.0…+150.0 °C; ÷10 scale.
The vendor's 3276.5 °C display bug must never replicate — reg 2046
decodes `unknown`, the honest state.

The `registers.py` mapping (`PUSHED_TEMPS`) is UNCHANGED by this spec
(data-model 013 §6 stays the authority); a permuted calibration
outcome remains a one-line edit there.

## 4. The two data planes + the merged snapshot (FR-2's revival path)

| Plane | Dataset | Registers | Served by | Status |
|---|---|---|---|---|
| Base | status/control: power, mode, setpoint, bounds, inlet/outlet/ambient, fault, firmware | 7-51 | FC03 answer — **never observed on this unit** | [P apk] / [P-absence on the wire] |
| Pushed (the report) | parameter view: T live temps, H settings, R thresholds, unknown 2001 | 1001-1180, 2001-2090 | the request-triggered block burst | [P — decoded frames] |

`AquaTempData.from_merged(regs, captures, confirmed)` (spec 014 FR-2,
used only when the trigger read completes — never observed yet):

- typed fields ← base answer (`from_regs` semantics; base wins for
  shared quantities — contract 013 §4.2);
- `registers` dict ← `{**base_regs, **confirmed_t_block_raws}` — the
  windows are disjoint (7-51 vs 2046-2051), so the union is
  collision-free by construction [P];
- pushed raws enter ONLY when `push_temp_confirmed` (the honesty gate —
  unchanged, contract 013 §4.3);
- mode stays `"push"`; the re-evaluation is a future spec's decision.

Entity surface in push mode: UNCHANGED (mirrors always created —
`unknown` until the gate opens; push-only keys behind the gate;
fault/firmware/climate never — no honest pushed home; `push_stream`
diagnostic always).

## 5. What this data model does NOT claim

- The T-window member order (which register is inlet vs outlet…) —
  [A] until the calibration run (quickstart 013 §2, unchanged).
- Any home for power/mode/setpoint/fault/firmware in the pushed plane —
  [P-absence] for blocks 1001/1046/2046; block 2001 is [V] (the paste).
- That the query-block frames (the app's parameter-page requests) are
  answered per-block on this unit — [V] (quickstart §3 experiment);
  the design does not depend on it (the trigger is the proven base
  read).
- The burst's persistence/duration semantics beyond the observed
  bounds (≤ 30 s after the last request) — [V] (U-7).
- The H/R member encodings (H02 0=C? H06 enum?) — [V] (inherited
  U-6 of research 013 §7).