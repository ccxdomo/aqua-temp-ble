# Spec 020 — The Aqua Temp wire protocol (consolidated reference)

This is the document to read to understand what the heat pump actually
says on the air. It consolidates the protocol knowledge scattered across
specs 001-019 into one place, corrected to the live-proven state.
Every frame below is CRC-verified byte-exact (vendor APK vectors
cross-checked by `modbus.py`'s self-test); every register claim carries
its evidence tag.

- **[P]** proven (live diff-revert/panel diff/photo on the owner's unit,
  or byte-exact disassembly re-verified by the self-tests)
- **[A]** assumed (static-analysis default, one constant to flip)
- **[V]** open on other units

---

## 1. Transport layer

| Layer | Fact | Evidence |
|---|---|---|
| Physical | BLE, single-connection link — the vendor app holding it blocks HA (and vice versa) | [P live] |
| GATT service | `0000ff01-0000-1000-8000-00805f9b34fb` (the vendor data service) | [P disassembly] |
| Write characteristic | `0000ff03-…` — written **without response** (the app's only WriteType); with-response only if the char advertises write-only | [P disassembly + live] |
| Notify characteristic | `0000ff02-…` — all responses arrive here, possibly fragmented (5 × 20-byte chunks observed; whole 99-byte frames too) | [P live] |
| App buckets | notify `{ff02, 2216, 2a00}`, write `{ff03, 2215, 2aa6}` — picked by UUID alone, properties advisory. The ff01 write constant in old docs was a DEX misreading: the app never writes ff01 | [P disassembly] |
| Name prefixes | `BlueNRG-`, `WT-`, `hiflying-`, `prov_` (the app's own scan filter) | [P disassembly] |
| Connect sequence | connect → discover → subscribe ff02 → **2 s settle** → first request | [P disassembly + live] |

## 2. The Modbus dialect

Modbus RTU frames tunneled as raw bytes on the write characteristic;
responses as notifications.

| Element | Value |
|---|---|
| Slave address | `0x63` |
| CRC | CRC16-Modbus (poly `0xA001`, init `0xFFFF`), appended **little-endian** |
| Data byte order | **big-endian** int16 (proven by the 2001-block's self-reference: payload index 9 reads `07 D1` = 2001 = the block's own start) [P] |
| Temperature scale | `value = int(degrees_C × 10)` (30.0 °C = raw 300) [P] |

### 2.1 Frame formats

**FC03 read (the trigger):**
```
63 03 <start:2BE> <count:2BE> <crc:2LE>
base block:  63 03 0007 002D 3C54        → 63030007002d3c54   [P]
```
Classic response shape `63 03 <bytecount> <data…> <crc>` (95 bytes for
count=45) — **never observed from this unit**; see §4 law L1.

**Block query (FC10 shape, 8 bytes, no data):**
```
63 10 <start:2BE> <qty:2BE> <crc:2LE>
1001:  631003e9002dd9e6   1046:  63100416002de8a2
1091:  63100443002df8b2   1136:  63100470002d08bd
2001:  631007d1002d591b   2046:  631007fe002d68d2      [P]
```

**Block response (the pushed frame — the unit's one answer shape):**
```
63 10 <start:2BE> <qty:2BE> <bytecount> <data:bytecount> <crc:2LE>
bytecount == 2 × qty   →   45-register frame = 99 bytes
```
Discriminated from an 8-byte FC10 echo by the offset-6 byte (echo carries
a CRC byte there; block frames carry `2×qty`). [P — 4 CRC-valid live
captures + the disassembly pool]

**Control write (single register):**
```
63 10 <addr:2BE> 0001 02 <value:2BE> <crc:2LE>     (11 bytes, qty=1, bytecount=02)
power ON:   631003f30001020001f131    (1011 ← 1)   [P disassembly + live]
power OFF:  631003f3000102000030f1    (1011 ← 0)   [P disassembly + live]
setpoint 30.0 °C: 63100416000102012c5049   (1046 ← 300)  [P builder parity]
setpoint 29.5 °C: 631004160001020127118e   (1046 ← 295)  [P builder parity]
```

**Modbus exception frame** `63 <fc|0x80> <code> <crc>` (5 bytes) —
reachable on the wire; the reassembler fails the pending request on it.
[P shape]

## 3. The register map (what we read and write)

### 3.1 The pushed blocks (the live read plane)

The burst contains exactly four 45-register blocks [P live]:

**Block 2001-2045 — live machine state:**

| Register | Quantity | Evidence |
|---|---|---|
| 2001-2009 | unknown (2010 = `0x07D1` self-reference = block start, the byte-order proof) | [P] |
| **2011** | **power state mirror** — 0 = off, 1 = on (flag decode: raw ≠ 0) | [P — owner app-toggle diff-revert 0→1→0, 2026-10-12] |
| 2012 | flag, static 1 across the diff — excluded as power candidate | [V] |
| 2017/2018 | unknown | [V] |
| **2019** | **mode mirror** — observed 0 (off world + on-state after our power-only writes) and 4 (app-flow on-state). **Diagnostic only** — the display follows power (heat-only unit) | [P diff-revert; display rule P spec 018] |

**Block 2046-2090 — live T-family temperatures (÷10, °C):**

| Register | Quantity | Evidence |
|---|---|---|
| 2046 | T01 Suction — **sentinel `0x7FFD`** on this unit (sensor absent → `unknown`) | [P panel photo + live] |
| 2047 | T02 Inlet water temperature | [P live 14.0→14.5 + photo calibration] |
| 2048 | T03 Outlet water temperature | [P photo calibration] |
| 2049 | T04 Coil temperature | [P photo calibration] |
| 2050 | T05 Ambient temperature | [P photo calibration] |
| 2051 | T06 Exhaust temperature | [P photo calibration] |

**Block 1046-1090 — the parameter ladder:**

| Register | Quantity | Evidence |
|---|---|---|
| **1046** | **heat setpoint (÷10)** — the panel diff moved it 290 → 300 for +1.0 °C | [P owner panel diff 2026-10-12] |
| 1049/1050/1054 | ladder members: min-bound/diff-family, max-bound (40.0), min-bound (10.0) candidates | [A-weak] |
| 1051 | cool setpoint candidate (family-map cool slot) | [A-strong] |
| 1068 | auto setpoint candidate (family-map auto slot) | [A-strong] |

**Block 1001-1045 — the machine-settings ladder (contains the WRITE plane):**
1011 (power write), 1012/1013 (mode write pair), 1076 (mute write) live in
this window — see §3.3.

### 3.2 The base block (7-51) — the classic plane, DEAD on this unit

Recovered from the APK's 45-name list [P apk]: power 8, boost 11, mode
13, current setpoint 14, cool/heat/auto targets 19/20/21, min/max bounds
22-25, inlet 26, outlet 27, ambient 28, fault 47, firmware 48-51. The
unit **never serves this block** (law L1) — the read frame survives only
as the poll trigger. Its entity names live on via the pushed mirrors.

### 3.3 The write plane (recovered from the app's disassembly)

| Register | Write | Status |
|---|---|---|
| **1011** | power ON/OFF — value 1/0, the app's ONLY hardcoded power write | [P disassembly + live proof: pump heard, mirror 2011 followed] |
| 1012 (+pair 1013) | mode — a **pair write**; 1013 is unpinned (setpoint-shaped) → writing the mode would clobber an unknown register; the app itself locks the mode on this heat-only machine | [P shape / A semantics — NEVER writable here] |
| **1046** | heat setpoint — `value = temp × 10`; the write target and the read home coincide (a parameter register, read AND written by the vendor's own app) | [A-strong disassembly map + P owner live: mirror followed 300→295→300] |
| 1076 | mute — frame recovered, semantics unpinned | [P frame / V semantics — never writable] |

The vendor's per-family/per-mode setpoint address map (data only, from
the disassembly): family 2 heat slot = 1046 — pinned for this heat-only
unit; the other slots (1135-1137, 1051, 1068, 1073-1075) are installer
thresholds and are never written.

### 3.4 Write/read plane relationship

The write plane and the read plane are **different registers**: power is
written at 1011 and read back at the mirror 2011; the mode would be
written at 1012/1013 and read at 2019 — the offset is not constant. The
setpoint is the exception: written AND read at 1046 (the fresh 1046-block
is the write's confirmation natively). [P]

## 4. Behavioural laws (the unit's contract)

- **L1 — The classic read is never answered.** Byte-perfect, ATT-accepted
  base reads produce no FC03 response and no exception — six instrumented
  runs, every poll since. The block-of-7 request is a dead end. [P]
- **L2 — Every request is answered with the burst.** Any valid frame
  (FC03 read, block query, control write) is followed within ~0.2-5 s by
  pushes of the four blocks (1001/1046/2001/2046, in a loose order, ≥1
  frame per block, ~2 frames/s). **The unit never pushes spontaneously**
  (0 frames in idle windows). This is why the poll cycle is trigger+listen
  and why the write confirmation sends its own trigger. [P]
- **L3 — Writes execute without response or echo.** Fire-and-forget
  Write-Commands execute (power proven physically); no FC10 echo has ever
  been observed — the mirror is the only confirmation. [P]
- **L4 — Single connection.** The unit serves one BLE link; a second
  connect is refused/hangs (vendor app first). [P live]
- **L5 — Writes are accepted at the ATT layer without pairing.**
  Production verdict `att-accepted-silent`; bonding is not the gate. [P]

## 5. Sentinels & plausibility

Two "sensor absent" conventions decode to `unknown`, never to a number
(the vendor's own app renders 3276.5 °C for the second one — we do not):

- `-9990` — the app's status-page sentinel [P apk]
- `32765` (`0x7FFD`) — observed for the absent T01 on this unit [P live]

Plausibility window: raw −450..+1500 (−45.0..+150.0 °C; the highest
legitimate system value observed is the H04 exhaust threshold at 105 °C).
Outside the window → `unknown` + one WARNING per address per session.
[P panel + code]

Setpoint discipline: raw 100..400 (10.0-40.0 °C) in steps of 5 (0.5 °C);
every observed raw is a multiple of 5. Out-of-range or off-step requests
are refused loudly — never rounded, never clamped. [P decode + contract]

## 6. Failure modes & their symptoms

| Failure | Symptom in HA | Root cause / remedy |
|---|---|---|
| Vendor app holds the link | connect fails after 2 × 20 s attempts (`cannot_connect`) | close the vendor app completely [P] |
| Unit out of range/asleep | connect fails or harvest stays empty | distance, adapter; rescan |
| Trigger sent, no burst | `push stream silent after trigger` → cycle fails, entities unavailable | unit rebooting or truly unreachable — honest unavailable, next cycle retries |
| Stale store between cycles | entities `unavailable` (freshness gate) | by design — no stale values shown |
| Fragmented frames | reassembled by the boundary rule + 0.6 s silence timer | handled [P live] |
| CRC-corrupt frame | censused at DEBUG, skipped; request keeps waiting | radio noise; self-heals |
| ATT security error on write | config-test security probe classifies + (only then) pairing ladder | never observed on this unit (L5) |
| Unconfirmed write | state unchanged; WARNING (+ persistent notification for an unconfirmed OFF) | mirror never came back within 20 s — check the panel |
| Wrong-HA-world revival | one WARNING "trigger read answered" | never observed; would re-open the classic plane deliberately |

## 7. Open items [V]

- Family key semantics of the setpoint address map (other unit types).
- The mode enum's full member list (only 0 and 4 observed).
- The `bLE` panel gesture is the WiFi-provisioning mode, not a proven
  BLE requirement — discovery works without it on this unit.
- Registers 1001-1009, 2012-2018, 2020-2045, 2052-2090: unlabeled.
- Whether other-firmware units answer the classic read (classic paths
  kept reachable for them).