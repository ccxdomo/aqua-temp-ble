# Spec 008 data model — the block frames and what they carry

Status: derived from `research.md` §2/§6. Every table row is either
**[P]** (CRC-verified live frame / APK / panel evidence) or **[V]**
(needs calibration). Nothing here changes `registers.py` — the base-block
mapping (7..51) is untouched; this file documents the *transport-level*
frame model and the observed high-block data as a calibration baseline.

## 1. Frame inventory (all shapes seen on this unit's wire)

| Frame | Direction | Shape | Length | Status |
|---|---|---|---|---|
| Base read request | TX (ours + vendor app) | `63 03 start:2 qty:2 crc:2` | 8 | [P apk] `63030007002d3c54` |
| Query-block request | TX (vendor app; ours via probe) | `63 10 start:2 qty:2 crc:2` | 8 | [P apk] 6 frames, CRC-verified |
| FC03 response | RX (documented; not observed live) | `63 03 bytecount data crc:2` | bytecount+5 = 95 | [P apk shape] [V on this unit] |
| **Block frame** | **RX (the 12 drops)** | `63 10 start:2 qty:2 bytecount data crc:2` | bytecount+9 = 99 | **[P — CRC-verified ×4 live]** |
| Write echo | RX (documented) | `63 10 start:2 qty:2 crc:2` | 8 | [P apk] [V on this unit] |
| Exception | RX | `63 fc|0x80 code crc:2` | 5 | [P modbus] [V on this unit] |

The block frame is the new entry — the hybrid of the query-block request
header and the FC03 data body (research §2.1). Its discriminator from the
write echo is the bytecount byte at offset 6 (`== 2*qty` → block; else
echo — the two known echoes carry 0x88/0x68 there [P]).

## 2. Block-frame field map [P]

```
offset  size  field            value observed (qty=45)
0       1     slave            0x63
1       1     fc               0x10 (query-block byte reused as response)
2       2     start            BE — 1001 / 1046 / 2001 / 2046 observed
4       2     qty              BE — 45 (0x002D)
6       1     bytecount        0x5A = 90 = 2*qty  (the echo discriminator)
7       90    data             45 × BE signed int16
97      2     crc16-modbus     LE, over bytes 0..96
```

Register decode: `raw = int16 BE` at `7 + 2*i` → register `start + i`.
Same sentinel set as the base block (`-9990`, `32765`), same ÷10
temperature scale (spec 003 decode-contract applies to any block).

## 3. Observed block data (the calibration baseline)

### 3.1 Block 1001-1045 (frame 1, CRC 0x3A56 [P])

Non-zero: reg 1012=1, 1018=1, **1021=1050 (=105.0 = the H04 exhaust-high
threshold [P panel])**, 1022=1, 1023=2, 1037=2, 1038=30, 1039=3, 1040=1;
all other registers 0. Reading: sparse flags/enums + one safety
threshold — H-family settings area [V].

### 3.2 Block 1046-1090 (frame 2, CRC 0x7685 [P])

Non-zero: 1046=290, 1047=5, 1048=5, 1049=150, 1050=400, 1051=270,
1052=5, 1053=5, 1054=100, 1055=350, 1056=-70, 1057=130, 1058=40, 1059=8,
1060=3, 1068=270. ÷10 ladder: 29.0, 15.0, 40.0, 27.0, 10.0, 35.0, -7.0,
13.0, 4.0 … — R-family regulation thresholds [V].

### 3.3 Block 2001-2045 (frame 3, header [P], data [V])

Header: start=2001, qty=45, bytecount=90 → 99 bytes. Data unverifiable
from the pasted line (research §2.4); visible islands include a 0x00A8
(=16.8 ÷10) value. Re-capture via the census/probe.

### 3.4 Block 2046-2090 (frames 4/5, CRCs 0x9BF4 / 0x65A5 [P])

| reg | push 1 | push 2 | note |
|---|---|---|---|
| 2046 | 32765 | 32765 | 0x7FFD sentinel — sensor absent [P] |
| 2047 | 140 | **145** | **live: changed 14.0 → 14.5 between pushes [P]** |
| 2048 | 140 | 140 | 14.0 |
| 2049 | 140 | 140 | 14.0 |
| 2050 | 135 | 135 | 13.5 |
| 2051 | 150 | 150 | 15.0 |
| 2052-2090 | 0 | 0 | |

FINDINGS §12 mapped 2001-2090 to "control parameters" — the changing
reg 2047 proves at least part of this block is **live status** [P for the
change, V for the semantic]. October pool-side values (14-15 °C) are
plausible [A].

## 4. Per-request response model (what completes which request)

| Request (TX) | May complete with | Match key |
|---|---|---|
| FC03 base read `63030007002d3c54` | FC03 response (95 B) **or** block frame start=7 qty=45 | fc=0x03 & bytecount=90; or fc=0x10 & (start,qty)=(7,45) |
| Query block `6310 XXXX 002d` | block frame with the same start/qty | (start,qty) echo |
| Write (H1) `6310 XXXX 0001 02 …` | 8-byte echo | (start,qty) echo, len=8 |
| any | exception `fc|0x80` | fails fast (unchanged) |

All other CRC-valid frames → census, discard, keep waiting (contract
`reassembly-matching.md`).

## 5. Blocks known to the vendor app vs observed in the stream

| Block | APK query frame [P] | Observed in the 12 drops |
|---|---|---|
| 1001-1045 | `631003e9002dd9e6` | yes [P] |
| 1046-1090 | `63100416002de8a2` | yes [P] |
| 1091-1135 | `63100443002df8b2` | [V] — likely among the 7 unpasted lines |
| 1136-1180 | `63100470002d08bd` | [V] — idem |
| 2001-2045 | `631007d1002d591b` | yes [P] |
| 2046-2090 | `631007fe002d68d2` | yes [P] (twice) |
| base 7-51 | `63030007002d3c54` (FC03) | **[V] — the E1 question (research §7)** |

12 drops = 2 cycles × 6 high blocks fits exactly [SUPPOSED]; whether a
start-7 block frame exists in the stream is THE open question the census
answers. No high-block data flows into entities (spec 003 §6); the probe
(FR-5) is the only consumer.