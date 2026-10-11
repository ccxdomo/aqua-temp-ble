# Aqua Temp (com.phinx.poolheater4) — BLE Reverse Engineering Findings

Date: 2026-10-10
Target: Aqua Temp / Piscina Temp — package `com.phinx.poolheater4`
Goal: understand the BLE protocol of the pool heat pump (PAC) to control it
(temperature read, target/setpoint, on/off) without depending on the vendor cloud.

Status: **protocol identified and CRC-verified.** Transport, frame format,
scaling, register map and command set are established. See §11 for the working
client (`ble_probe.py`) and §12 for what remains unproven.

---

## 1. Artifacts analysed

| Artifact | Source | Size | Notes |
|---|---|---|---|
| `aqua-temp.apk` | Aptoide (base APK only) | 39.3 MB | MD5 `fa6e6c2b8f22b42923db226b27f5234f` (matches Aptoide) |
| `aqua-temp.xapk` | APKPure (`b/XAPK/...2040_842c2aab`) | 69 357 310 B | **contains ABI splits** |
| `config.arm64_v8a.apk` | extracted from XAPK | — | `lib/arm64-v8a/*.so` |
| `libapp.so` | ABI split | 11 010 960 B | SHA256 `b0ae8c66faf7ffcc028c57480cf814fce9527b9029fa9c55d1946fd8b710d19f` |
| `libflutter.so` | ABI split | 11 581 856 B | SHA256 `ac977ad375350cf07a9cb88b60530f2e035423eb22932af48c60426d5010ac20` |
| `classes*.dex` | base APK | 7.8 / 0.2 / 4.1 MB | Java side (plugin code) |

App version: **2.2.1 (versionCode 2040)**, minSdk 24, targetSdk 36.
Dart version: **3.12.2** — extracted from `libflutter.so` `.rodata`
(engine IDs `e9ed4fc9f1544c58d8a9347c1fc9471d8dd7c465`,
`0cd610717bde95fd88343c64f81c11ba5e5c0010`).

Build flags (from Dart snapshot header): `product no-code_comments
dwarf_stack_traces_mode dedup_instructions no-asan no-msan no-tsan
no-shared_data arm64 android compressed-pointers`.

> Note: the Dart version is NOT in `libapp.so` (compiled with
> `no-code_comments`). It is only in `libflutter.so`. This is how blutter
> detects it (`extract_dart_info.py`, reading `.rodata`).

## 2. Application architecture

- **Flutter** app, **Dart AOT** (obfuscated) + Java plugins.
- Entry: `cn.cjlmonster.aquatemp.CustomApplication` / `MainActivity`.
- Embedded **web modules** (Vue.js, non-obfuscated) under
  `assets/flutter_assets/assets/server/`:
  - `phnix-assistant/` — AI assistant ("Nikigpt"), unrelated to BLE
  - `web-client/` — chart/energy pages
- `assets/env/prod.json`:
  ```json
  { "env": "prod-eu", "appId": 14,
    "apiBaseUrl": "https://cloud.go-heating.com/crmservice/api/",
    "aiBaseUrl": "https://ai.go-heating.com/ai-assistant/api",
    "mqttHost": "mqtts://47.254.178.157", "mqttPort": 8883 }
  ```
  `appId 14 = AquaTemp`, `appId 16 = WarmLink` (sibling app).
- The cloud path exists (REST + MQTT) but **is not the owner's path**: the
  owner's unit has **no WiFi** and is driven over **BLE directly**.

## 3. BLE transport — RECOVERED

Found in `classes3.dex` string table (Java plugin, NOT obfuscated):

| Constant | Value |
|---|---|
| `BLE_SERVICE_UUID` | (no 128-bit literal in DEX → built/assigned in code) |
| `BLE_WRITE_CHARACTERISTIC_UUID` | `0000ff01-0000-1000-8000-00805f9b34fb` |
| `BLE_NOTIFY_CHARACTERISTIC_UUID` | `0000ff02-0000-1000-8000-00805f9b34fb` |
| CCCD | `00002902-0000-1000-8000-00805f9b34fb` |
| other UUIDs present | `0000fec7`, `0000fec8`, `0000fee7`, `0000ffff` |

Full UUID list in `classes3.dex` (8 total, exhaustive):
`00000000-…` (null), `00002902-…`, `0000fec7-…`, `0000fec8-…`,
`0000fee7-…`, `0000ff01-…`, `0000ff02-…`, `0000ffff-…`.

**Protocol shape**: write commands on `ff01`, subscribe to notifications on
`ff02`. Plugin identified: **`flutter_blue_plus`** (channel
`flutter_blue_plus/methods`, callbacks `OnCharacteristicReceived`,
`OnCharacteristicWritten`, strings `writeCharacteristic`, `setNotifyValue`).

### Device identification (scan filter)

From the Dart pool (`pp.txt`):

```
BLE_NAME_HIFLYING     ← device name prefix
BlueNRG-              ← ST BlueNRG module prefix  (OWNER'S UNIT USES THIS)
PROV_                 ← provisioning prefix (other models)
```

Owner confirmed by observation: the unit advertises as **`BlueNRG-...`**.
App filters discovered devices by **name**, not by advertised UUID.

### Pairing procedure (from i18n strings)

> "Appuyez et maintenez [icon] pendant 5 secondes. L'interface affichera
> «bLE» et clignotera une fois par seconde …"
> (= "Hold [icon] 5 s; display shows 'bLE' blinking 1×/s") — i.e. the unit
> enters BLE pairing mode when the panel shows `bLE`.

## 4. Business vocabulary recovered (Dart pool)

`pp.txt` (blutter) contains ~28 951 objects including the app's own strings:

| Concept | Strings |
|---|---|
| Ambient temp | `ambientTemp` |
| Target temp (setpoint) | `Doeltemperatuur` (nl), `coolToTargetTemp`, `heatToTargetTemp` |
| Outlet / inlet | `outletTemp`, `inletTemp`, `InletWaterTemp`, `EnOutletWaterTempSensor` |
| Modes | `boostMode`, `autoMaintainTemp`, `autoModeEngaged`, `coolingEngaged`, `calcMode`, `FinalMode` |
| Power | `AVRPower`, `enAIPower`, `EmergencySwitch`, `AppSwitch` |
| Status / faults | `compressorStatus`, `deviceStatus`, `deviceFault`, `isFault`, `isMonitorFault`, `ATSensorFault`, `InletWaterTempSensorFault` |
| Device identity | `deviceCode`, `deviceCodeList`, `areaCode`, `aqua_temp_`, `AquaTemp_Android` |
| Timers | `addTimer`, `editTimer`, `cloudTimer`, `EnTimerSlient` |

The app-level BLE layer is in classes `BleManger` (sic) and `BleLinker`,
methods `startBleLink`, `disposeBleLink`, `startBleScan`, `bleName`.

> `BleLinker` + `apLink`/`apLinkChangeWifi`/`smart_AP_XXX` + Chinese WiFi
> strings = **provisioning of the WiFi models**. The owner's unit is NOT one
> of those; for this unit BLE is the **operating channel**.

## 5. Toolchain built for this analysis

blutter was compiled and run successfully on the gateway (Raspberry Pi, aarch64):

```
git clone https://github.com/worawit/blutter
python -m venv venv && venv/bin/pip install pyelftools requests
# system deps: clang ninja-build libicu-dev libcapstone-dev
venv/bin/python blutter.py <dir-with-libapp-libflutter> <outdir> \
    --dart-version 3.12.2_android_arm64
# NOTE: without libflutter, pass the FILE, not the dir:
blutter_dartvm3.12.2_android_arm64 -i libapp.so -o out
```

Output produced: `pp.txt` (3.4 MB, 28 951 objects), `objs.txt`, `asm/`
(1405 files), `blutter_frida.js`, `ida_script/`.

## 6. LIMITATION — function bodies are not decompilable

The Dart snapshot uses **`dedup_instructions`** + **`compressed-pointers`** +
**obfuscation**. Consequence:

- blutter recovers **structure** (classes, fields, method signatures, all
  pool strings) — ✅
- blutter does **NOT** recover **function bodies**: every closure/function is
  emitted as `// ** addr: 0x…, size: -0x1` with an **empty body**.

This is why the frame bytes had to be recovered from **string constants in the
Object Pool** rather than from decompiled code. That approach succeeded (§11).

## 7. Toolchain notes / reproduction

```bash
# extract APK/XAPK
tar -xf aqua-temp.xapk -C xapk/                 # -> config.arm64_v8a.apk
tar -xf xapk/config.arm64_v8a.apk -C abinat/    # -> lib/arm64-v8a/*.so

# Dart version (from libflutter.so, NOT libapp.so)
python -c "import re;d=open('libflutter.so','rb').read();\
print(re.search(rb'\x00([0-9][0-9a-zA-Z._-]{2,10}) \((stable|beta|dev)\)',d).group(1))"
```

## 8. Frame-format evidence (pool strings)

From blutter's `pp.txt` — the app's own debug-log strings:

```
[pp+0x20aa8] "日志:发送的指令"     "log: command sent"
[pp+0x20ab0] "  字节"             "  bytes"
[pp+0x20ab8] "日志:指令已发送"
[pp+0x20ad0] "日志:指令发送失败"
[pp+0x20b18] "writeCharacteristic"
[pp+0x20b40] "Hex string must have an even length"
[pp+0x20b48] "日志:指令 6310"       "log: command 6310"
[pp+0x20b50] "6310"
```

Established: frames are **hex strings**, and the app **logs its own outgoing
frames** (so logcat shows them in cleartext).

## 9. The decoder class `RM` (frame interpreter)

`{id:3252, name:"RM", size:344}` — ~50 `late final` fields, i.e. a lazily
populated frame-decoding structure. Field ↔ meaning, mapped by adjacency in
`pp.txt`:

| Offset | Field | Meaning (adjacent strings) |
|---|---|---|
| `0x7c` | `RM.uDg` | — |
| `0x80` | `RM.vDg` | `"Remote on/ off switch open"` |
| `0x84` | `RM.wDg` | `"Boost Mode"` |
| `0x98` | `RM.ADg` | `"inCoolingModeTheOneKeyMute"` |
| `0xa0` | `RM.Syd` | `"Boost Mode"` |
| `0xa4` | `RM.Ayd` | `"ambientTemp"` |
| `0xcc` | `RM.Tzd` | `"Ambient Temp."` |
| `0x110` | `RM.LDg` | `"Outlet Temp."` |
| `0x118` | `RM.tAd` | `"outletTemp"` |
| `0x11c` | `RM.MDg` | `"Running"` |
| `0x128` | `RM.PDg` | `"Compressor Status"` |
| `0x130` | `RM.RDg` | `"realTimePowerWithUnit"` |
| `0x13c` | `RM.hzd` | `"Inlet Temp."` |
| `0x140` | `RM.izd` | `"Ambient Temp."` |
| `0x144` | `RM.kzd` | `"H72: ", "H70: ", "H71: "` |
| `0x150` | `RM.WDg` | `"DTU Ver"` |
| `0x108` | `RM.Pzd` | `"masterSlaveSwitchClose"` |
| `0x124` | `RM.ODg` | `"deviceIsNotOnline"` |

Class `jzk` (id 1048765) holds 9 static closures = the **initializers** of the
`late final` fields. Two return `double` — they are the temperature
conversions:

```
0x689c54  (double)   ← Inlet Temp conversion
0x68a308  (double)   ← Ezd conversion
```

**Scaling**: temperatures are transmitted as `int(value × 10)`.
Constants adjacent to `RM.hzd` (Inlet Temp): `double(325)`, `double(315)`
(= 32.5 °C / 31.5 °C).

## 10. Debug password `"66"`

Pool evidence: entering `66` switches the UI from *status* parameters to the
**control parameters** view (`查看控制参数` vs `查看状态参数`), exposing raw
values including `H70/H71/H72`. This gives direct read access to the raw
registers from inside the official app, without any instrumentation.

## 11. PROTOCOL — Modbus RTU over GATT (CRC-verified)

**Slave address `0x63` (= 99). Modbus RTU framing. CRC16-Modbus,
little-endian.**

Frames recovered as hex string constants in the Object Pool:

| Frame (hex) | CRC calc | CRC stored | Verified |
|---|---|---|---|
| `63030007002D3C54` | `0x543C` | `3C 54` | ✅ |
| `631003E9002DD9E6` | `0xE6D9` | `D9 E6` | ✅ |
| `63100416002DE8A2` | `0xA2E8` | `E8 A2` | ✅ |
| `63100443002DF8B2` | `0xB2F8` | `F8 B2` | ✅ |
| `63100470002D08BD` | `0xBD08` | `08 BD` | ✅ |
| `631007D1002D591B` | `0x1B59` | `59 1B` | ✅ |
| `631007FE002D68D2` | `0xD268` | `68 D2` | ✅ |
| `631004f30004080001` | (incomplete) | — | prefix of the time-sync frame; app appends the clock before CRC |

CRC16-Modbus reference implementation (verified against the 7 frames above):

```python
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
# wire order is little-endian: low byte first
```

### 11.1 Read frames

**Base status read** — standard Modbus FC03:

```
63 03 0007 002D [CRC_lo CRC_hi]
 │  │    │    └── quantity: 45 registers
 │  │    └─────── start register: 7
 │  └──────────── function 0x03 (read holding registers)
 └─────────────── slave 0x63
```

→ returns 90 bytes (`45 × 2`) of register data.

**High-block reads** — `0x10` reused as "query block"; 45 registers per block,
blocks contiguous (delta `0x2D` = 45 between consecutive block starts):

```
regs 1001-1045 · 1046-1090 · 1091-1135 · 1136-1180
regs 2001-2045 · 2046-2090
```

Matching recovered frames: `03E9`=1001, `0416`=1046, `0443`=1091, `0470`=1136,
`07D1`=2001, `07FE`=2046.

### 11.2 Write frames — standard Modbus FC16 (write multiple registers)

```
63 10 [reg hi reg lo] [qty hi qty lo] [bytecount] [data...] [CRC_lo CRC_hi]
```

### 11.3 GATT handles

| Handle | Role |
|---|---|
| `ff01` → char `2214` | **write** (commands out) |
| `ff02` → char `2216` | **notify** (status in) |
| `ff03` → char `2215` | (secondary) |

The `ff01`/`ff02` UUIDs are the **generic Chinese-module convention**
(`0xFF00`-`0xFFFF`), not vendor-specific.

> **Correction (spec 010, 2026-10-11):** the Dart operating channel
> selects write = ff03 / notify = ff02 by UUID bucket
> ({ff03,2215,2aa6} / {ff02,2216,2a00}); the `ff01` DEX constant belongs
> to the non-operating Java BLE path. The `ff01→2214/ff02→2216/ff03→2215`
> "handle" pairs are two GATT generations' UUID sets. See
> `specs/010-write-characteristic/research.md`.

### 11.4 Field ↔ register mapping

Class `VR.Yzd` maps display labels to register indices:

```
T01-T06   temperature group
H01-H06   status group (H70/H71/H72 seen in UI)
D01-D06   digital/state group
E04, E08  fault/sensor entries
F13       fault entry
R01-R11   runtime/energy registers
O01-O04   output registers
S01-S06   setpoint registers
P03       power register
```

**Not fully resolved:** the exact `label → register index` mapping. The
indices are compiled as **immediate operands inside function bodies**, which
`dedup_instructions` hides (§6). The mapping is established to ~80 %; the
remaining 20 % requires a live read (see §12).

## 12. Base block register map (45 registers, FC03 from reg 7)

Recovered by Architect from a `List<String>(45)` in the pool — exactly 45 names
for the 45 registers returned by `63030007002D3C54`.

| Reg | Name | Notes |
|---|---|---|
| 8 | `Power` | **on/off** |
| 11 | `EnBoostMode` | boost |
| 13 | `Mode` | heat / cool / auto |
| 14 | `SetCurrentTemp` | **setpoint** (×10) |
| 19-21 | `SetCool/Heat/AutoTargetTemp` | per-mode setpoints |
| 22-25 | `SetCool/Heat Min/MaxTargetTemp` | bounds |
| 26-28 | `InletWaterTemp`, `OutletWaterTemp`, `AT` | **live temps (÷10)** |
| 47 | `Fault1` | fault code |
| 48-51 | firmware versions | read-only |

Semantic names on the "Status parameters" page: `Compressor`, `Circulate pump`,
`4-way valve`, `High/Low fan`, `Suction Temp.`, `Inlet/Outlet water Temp.`,
`Coil Temp.`, `Exhaust Temp.`, `HP/LP/Flow/Remote/Mode switch`.
Sentinel `-999.0` = sensor absent.

The "Control parameters" page maps to the high block 2001-2090:
`Cooling set`, `Heating set`, `Auto. Set`, `Defrost…`, `EEV Mode`, etc.

### 12.1 Write mechanism (partially read from code)

```
address = param->field_1f[0]      // list of register addresses
call 0x65011c(address, value, 2, "02")
// conversion F->C with factor 10.0
```

→ write frame: `63 10 [addr:2] 0002 04 [value:4] [CRC]`

### 12.2 How to close the last gap (label -> numeric register)

The numeric addresses are built at runtime (`field_1f` list), invisible to
static analysis. But the app **logs the address on every write**:

```
"参数选择修改 ----- index = X  address = Y"
"H72: … H70: … H71: …"
"日志:指令 6310 …"          (the full outgoing frame)
```

```bash
adb logcat -c
adb logcat | grep -aE "address|6310|H70|H71|H72|指令"
```

Change the setpoint / toggle power in the official app → the log prints the
exact register address. That yields the complete label->register table.

## 13. What remains unproven / next steps

1. **Exact register index per label** (the last ~20 % of the map).
2. **Scaling for non-temperature values** (power/runtime).
3. **Which registers are writable** and their accepted ranges/units.

Three ways to close these, in increasing difficulty:

1. **Debug password `"66"`** in the official app → read raw control parameters
   directly, correlate with a capture. No instrumentation.
2. **Send `63030007002D3C54`** over BLE (see `ble_probe.py`) and match returned
   values against known temperatures (e.g. inlet at 24.3 °C → look for `243`).
3. **Frida** (`aqua_hooks.js`) → dump the resolved `VR.Yzd` map at runtime.

## 14. Deliverables in this directory

```
FINDINGS.md      this file
modbus_ble.py    Modbus-over-BLE client (read/write, CRC16, live decode)
ble_probe.py     raw BLE probe (observation)
aqua_hooks.js    Frida hooks (39 addresses) for runtime inspection
blutter/         pp.txt · objs.txt · blutter_frida.js  (raw artefacts)
```
