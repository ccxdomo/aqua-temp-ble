# Spec 006 — BLE pairing & connection sequence (vendor app reverse)

Status: **WRITTEN** — produced by the Architect from static analysis of the
blutter Dart-pool artifacts (`blutter/pp.txt`, `blutter/objs.txt`) of the
vendor app **Aqua Temp v2.2.1** (`com.phinx.poolheater4`, versionCode 2040).
All pool references `[pp+0x…]` are entries in `blutter/pp.txt`.

Question this spec answers (production blocker): **Home Assistant lists the
heat pump (`BlueNRG-C0941C`, 08:06:A6:C0:94:1C, RSSI -67) but the connection
test fails with "Impossible de se connecter". Is there a code/frame to send
before the PAC accepts a connection? And how do you put the PAC in pairing
mode when there is no explicit button?**

Confidence legend (same as specs 001-005):
- **[P] PROVEN** — read verbatim in the artifacts (exact pool offset given).
- **[A] ASSUMED** — reasonable default, cheap to correct.
- **[V] TO-VERIFY** — needs a live unit or a DEX session; how to verify is
  stated.
- **[D] DECISION** — design choice.

TL;DR of the whole research:
1. **There is NO code and NO frame to send before the PAC accepts a GATT
   connection.** The vendor app's own session is
   `connect → discover → subscribe ff02 → read`. No bonding, no pairing, no
   unlock frame, no PIN — proven by absence (§8).
2. The famous **"bLE" gesture** (hold a panel button 5 s) is the vendor's
   **configuration-mode** gesture documented for the WiFi-provisioning
   wizard ("network distribution"), **not** a proven gate for GATT
   connections. For the owner's BLE-only unit it is *probably* not needed —
   but one 2-minute live experiment (quickstart step 3) settles it either
   way. (§1, §9)
3. The leading root cause of the HA failure remains **the single-connection
   rule**: a BLE peripheral accepts exactly one central; if the vendor app
   on the owner's phone holds (or auto-reconnects to) the link, every other
   connect attempt is refused. (§9)
4. The `631004f3…` frame is the **manual "Time synchronization"** feature
   (settings menu → confirm dialog), assembled as
   `631004f30004080001 + <6-byte clock> + CRC`. It is **never** sent at
   session start and is **not** a connection key. (§5)

---

## 1. The pairing gesture — what the vendor app actually says

### 1.1 The instruction string [P]

English (i18n key `pressAndHoldTheFor5SecondsTheInterfaceWill`,
[pp+0x1af68], value; key at [pp+0x1af70]):

> "Press and hold the'[icon]'for 5 seconds, the interface will display
> “bLE”, and it will flash once per second, that is, it has entered the
> configuration mode. After the network distribution is successful, the
> display “888” flashes once per second, total 3 times."

The string exists in 12 languages (value offsets):

| Language | Offset | Language | Offset |
|---|---|---|---|
| EN | [pp+0x1af68] | HU | [pp+0x4aca8] |
| ZH | [pp+0x460f8] | FR | [pp+0x4b890] |
| SV | [pp+0x46de0] | ES | [pp+0x4c460] |
| PT | [pp+0x47aa0] | DE | [pp+0x4d178] |
| NL | [pp+0x493c8] | DA | [pp+0x4dd50] |
| IT | [pp+0x4a008] | CS | [pp+0x4e948] |

French verbatim ([pp+0x4b890]):
> "Appuyez et maintenez '[icon]' pendant 5 secondes. L'interface affichera
> \"bLE\" et clignotera une fois par seconde, ce qui signifie que vous êtes
> entré en mode de configuration. Après une distribution de réseau réussie,
> \"888\" clignotera une fois par seconde, pour un total de 3 fois."

Decoded, the gesture is:
- **press and hold** the button shown in place of `[icon]` — for **5 s**;
- success = the **panel display shows "bLE", blinking once per second**
  (configuration mode entered);
- on WiFi-capable units: after successful "network distribution"
  (= WiFi provisioning) the display shows **"888" blinking 3×**.

### 1.2 Which button? The `[icon]` placeholder [P/V]

- The literal placeholder string `[icon]` exists in the pool ([pp+0x28028],
  next to a text-alignment enum) — the i18n text is loaded and the
  placeholder is **replaced at runtime by an inline image widget**.
- The images that supply these icons in the add-device wizard are
  ([pp+0x1af20…0x1af30]):
  - `icon_icon_apwifi.png`
  - `icon_icon_xkqwifi.png`
  - `icon_icon_v3wifi.png`
- **No `icon_icon_ble.png` or `icon_icon_panel.png` string exists in the
  pool.** Which asset (or different asset) is substituted for the "bLE"
  instruction — and therefore **which physical button it depicts — is NOT
  resolvable from the Dart pool.** The images live in the APK
  (`assets/flutter_assets/…`), which is **not on this gateway** (it is on
  the Windows node, `C:\reverse\`) → see §10, DEX-gaps.
- **[V → SUPPOSED]** On PHNIX panels the hold-to-configure button is most
  plausibly the **power/ON-OFF button** (the only button with a "hold 5 s
  ergonomics" on these panels), but this is **not proven**. The
  authoritative answer = run the vendor app's add-device wizard once
  (Add Device → BLE type) and look at the photo it shows, or extract the
  asset in the DEX session. The app's wizard screenshot is the ground truth
  the user can see with zero reverse tooling.

### 1.3 Context: this instruction belongs to the network-provisioning wizard [P]

The "bLE" string sits in the middle of the **add-device / config-mode
instruction set** of the provisioning wizard ([pp+0x1aee8…0x1afa0]), together
with per-module-type animations and instructions:

| i18n key | Instruction (EN, abbreviated) | Module type |
|---|---|---|
| `wifiLedRedHint` | "Press the '[icon]' button for 3 seconds, and the indicator light State will turn red" | AP-WIFI dongle |
| `wifiLedReadyHint` | "Press and hold the \"Press\" button at the bottom for 3 seconds until the \"State\" indicator light turns red" | AP-WIFI dongle |
| `pressTheButtonShortly…` | "Press the'[icon]'button shortly, and the indicator light State will turn red" | XKQ/V3 dongle |
| `pressAndHoldTheFor5Seconds…` | **"hold '[icon]' 5 s → display shows 'bLE' blinking 1×/s"** | **panel-integrated BLE/WiFi gen-2 board** |
| `pressTheBottomIconButtonShortly…` | "Press the bottom '[icon]' button shortly… first indicator light flashes and fourth stays on" | DTU |
| `shortPressIconUntil` | "Short press'[icon]'until the first and the fourth indicator lights remain on" | DTU |

plus `resetDevice` / "Reset Device" ([pp+0x1af98…0x1afa0]) and the wizard
lottie assets `lot_ap_config.zip`, `lot_wifi2_config.zip`, `lot_wifi3.zip`,
`lot_4g_or_wifi_wifi2.zip`, `lot_ai_wifi.zip`, `lot_ai_wifi_4g.zip`
([pp+0x1aee8…0x1af18]).

Two structural facts follow:
1. The gesture is **the panel-integrated module's** config entry (display
   shows "bLE") — i.e. the family the owner's unit belongs to (BlueNRG
   module integrated in the heat pump, panel display).
2. The sentence's purpose is **entering configuration mode for "network
   distribution"** — the WiFi-credential provisioning flow of the
   gen-2 WiFi models. The owner's unit is BLE-only (FINDINGS §2): it has no
   WiFi to distribute. **Nothing in the app's daily-use flow ever asks for
   this gesture** (§2, §8) — it appears only in the wizard.

### 1.4 Does the display show "bLE" on the owner's unit? [V]

Not yet observed live. The panel is a 7-segment display ("888" is the
classic all-segments test pattern) — "bLE" renders as `bLЕ`-style segments.
The user can verify in 30 s: hold the (likely power) button ~5 s and watch
the display. If the panel never shows "bLE", the unit's firmware is a
different variant and the whole config-mode theory is void for this unit.

---

## 2. The vendor app's BLE session sequence (operating channel)

Recovered from the session-manager pool region (class `nzk`/`bS` = the
`BleManger` storage singleton; storage keys `bleDevice`, `timerId`, `fault`,
`parameter`, `mac`, `snCode` at [pp+0x11340…0x115b8], class-name string
"BleManger" at [pp+0x113d0]).

**The sequence, in the order the pool strings and logs were laid out
([pp+0x21168…0x21570]) — all quotes verbatim [P]:**

1. **Connect.** Log on success `"日志:设备已连接"` ("device connected")
   [pp+0x21210]; on loss `"日志:设备断开连接"` [pp+0x21218]. A
   connection-state listener is required — if no device: `"日志:蓝牙设备为空，
   无法建立连接状态监听器"` [pp+0x21220].
2. **Discover services.** Log `"日志:发现服务 "` ("services discovered")
   [pp+0x21258], then per-service/characteristic logging with the pairs:
   `ff01 → 2214`, `1800`, `ff02 → 2216`, `2a00`, `ff03 → 2215`, `2aa6`
   ([pp+0x21270…0x212b0]). Empty characteristic set is an error:
   `"日志: 服务 … 的特征为空"` [pp+0x212b8…0x212c0]. (2a00 = Device Name,
   2aa6 = Central Address Resolution — GAP service members; 1800 = GAP.)
3. **Subscribe to ff02 notifications.** Log `"是否订阅成功: "` ("was the
   subscription successful") [pp+0x212e8]; subscribe error log
   `"日志:蓝牙返回错误: "` [pp+0x212f0]. The subscribe uses the standard
   CCCD `00002902-0000-1000-8000-00805f9b34fb` ([pp+0x21620]) via the
   flutter_blue_plus `setNotifyValue` method ([pp+0x21600]).
4. **A 2-second Duration constant** (`int 0x1E8480` = 2 000 000 µs,
   [pp+0x212c8]) sits **immediately before** the base read frame in the
   same function slice — a settle/poll cadence in the subscribe→read path.
   **[V]** exact role (delay before first read vs 2 s poll interval vs
   subscribe-confirmation timeout) — function bodies are not decompilable
   (FINDINGS §6).
5. **Send the base read** `"63030007002D3C54"` [pp+0x212d0] — FC03, slave
   0x63, start register 7, count 45. This is the **first Modbus frame of
   every session**.
6. **Receive & reassemble.** Notification chunks are accumulated
   (`"日志 蓝牙给的数据包头 "` "packet header" [pp+0x212f8],
   `"日志:接受的数据包"` "received data packet" [pp+0x21508]); malformed
   input → `"控制失败"` "control failed" [pp+0x21300].
7. **Block queries on demand** (status/control-parameter pages): pairs of
   start-register + full frame, [pp+0x21510…0x21568]: `03e9`, `0416`,
   `0443`, `0470`, `07d1`, `07fe` → `631003E9002DD9E6`,
   `63100416002DE8A2`, `63100443002DF8B2`, `63100470002D08BD`,
   `631007D1002D591B`, `631007FE002D68D2`.
8. **Writes** (FC16) via the write path of §2.1 below; every outgoing frame
   is logged (`"日志:发送的指令"`, `"  字节"`, `"日志:指令已发送 "` at
   [pp+0x20aa8…0x20ab8]; failure `"日志:指令发送失败"` [pp+0x20ad0];
   generic UI error `Instruction control error` / `instructionControlError`
   [pp+0x20ac0/0x20ac8]).

**There is no step 0.** No frame, token, password, or handshake is sent
between `connect` and the `ff02` subscription. See §8 for the absence
proofs.

### 2.1 Write mode: WRITE WITHOUT RESPONSE [P]

The write path uses the flutter_blue_plus `writeCharacteristic` method
([pp+0x20b18], params `write_type` [pp+0x20b30], `allow_long_write`
[pp+0x20b38]) and the referenced **write-type enum object is
`{0x1: "withoutResponse"}`** ([pp+0x20ad8], Obj `PMa`), sitting directly
between the send-failure log and the method string. The write path also
logs the FC16 prefix (`"日志:指令 6310"`, `"6310"` [pp+0x20b48…0x20b50]).
→ **The vendor writes commands without response** (fire-and-forget GATT
write, no application-level ack expectation other than the echo/state read).

### 2.2 Timeouts & retries (app side) [P]

| Constant / behaviour | Evidence |
|---|---|
| **20 s connect timeout** | `int 0x1312D00` (20 000 000 µs) in the same `bS` session-manager region, [pp+0x21d70]; matches FBP's `"Timed out after "` message [pp+0x1c648] |
| Connect retry loop (add flow) | `"尝试重新连接... ("` ("trying to reconnect… (") [pp+0x1c398]; exhausted: `"未能成功连接到设备，已达到最大重试次数"` ("failed to connect, max retries reached") [pp+0x1c3a0]; UI error `Connect Error` / `connectError` [pp+0x1c380/0x1c388] |
| Auto-reconnect (session loss) | `"日志:重连设备中 "` [pp+0x21238], `"日志:重连设备设备 ."` [pp+0x21240]; max reached → popup: `"日志:重连设备已达到最大值 弹窗提示失败."` [pp+0x21228] |
| **Retry counts / backoff values** | **[V]** — immediate operands, invisible to static pool analysis (DED + `dedup_instructions`, FINDINGS §6) |
| Other durations (context) | 10 s near cloud control API `app/device/v3/control` [pp+0x18290/0x18298]; 15 s (dio HTTP) [pp+0x16158]; 30 s [pp+0x168d8]; 5 s near `mqttToken` [pp+0x234b0] — that 5 s belongs to the **MQTT** session manager (`"链接已断开 - isConnected: …, manualDisconnect: …, isTokenExpired: …"` + MQTT host strings, [pp+0x238c8…0x23930]), **not** to BLE |

### 2.3 Daily-use failure messaging [P]

The operating channel's only user-facing connection-error string is:

> "The Bluetooth connection has been disconnected due to signal issues.
> Please ensure the device is within range and reconnect."
> — [pp+0x2d5a8], key `theBluetoothConnectionHasBeen` [pp+0x2d5b0]
> (ZH twin [pp+0x46338])

**The vendor blames signal/range for connection failures in daily use —
never a missing pairing mode.** If GATT were gated behind the "bLE" gesture,
this screen (and the home-page connect flow `"首页-连接蓝牙设备"`
[pp+0x225b8]) would have to instruct it; no such string exists.

---

## 3. Two advertisement identities, two scanners [P]

The app contains **two distinct BLE scan flows with different name
filters**:

| Flow | Class | Name-prefix filter | Evidence |
|---|---|---|---|
| **Operating channel** (home page → device page, and the BLE add flow) | `Bxk`/`_mO` | `"BlueNRG-"`, `"WT-"` | [pp+0x1d270], [pp+0x1d278] |
| **BLE provisioning** (WiFi gen-2 "network distribution") | `BleLinker` (`ark`/`_jO`) | `"BLE_"`, `"PROV_"` | [pp+0x25150], [pp+0x25158] |

Supporting strings: `BleLinker` [pp+0x25238], `startBleLink` [pp+0x25190],
`disposeBleLink` [pp+0x25260], `startBleScan` [pp+0x25268], `bleName`
[pp+0x25188], lifecycle events `onBleLinkStart/Progress/Success/Timeout/
Error/Finish`, `onBleScanEnd`, `onApLink*` ([pp+0x24f50…0x25098]), and the
provisioning logs `"日志: flutter 蓝牙配网开始/失败了/超时了"` ("BLE
provisioning started/failed/timed out") [pp+0x25140/0x25108/0x25110].

Interpretation **[A]**:
- **Normal mode** — the unit advertises as `BlueNRG-XXXXXX` (default ST
  BlueNRG firmware name; the owner's unit: `BlueNRG-C0941C`). This is the
  name the **operating** channel scans for, i.e. the name under which the
  app connects for **daily use**.
- **Configuration/provisioning mode** — WiFi gen-2 units advertise under a
  provisioning name (`PROV_…` / `BLE_…`) so that the provisioning flow can
  find them while distributing WiFi credentials. Whether the BLE-only unit
  changes its advertisement in config mode is **[V]** (quickstart step 3
  also answers this: watch HA's discovery while "bLE" shows).

`"HIFLYING"` (FINDINGS §3) does **not** appear in the Dart pool — that
prefix came from the DEX string table (classes3.dex); it is consistent with
the operating-channel filter set.

### 3.1 Consequence for HA

Our `NAME_PREFIXES` (const.py) = `("bluenrg-", "hiflying-", "prov_")`. The
vendor's operating filter is `BlueNRG-` + **`WT-`**; `wt-` is missing from
our discovery/resolution tiers (FR-4). `prov_` is a *provisioning* name —
harmless to keep for discovery but it is not the operating identity.

---

## 4. The add-a-BLE-device flow (first contact) [P]

Add Device menu ([pp+0x13020]) → device types with images
([pp+0x13038…0x13158]): `AP-WIFI设备`, `DTU`, **`BLE` / `BLE设备`
(`pic_add_type_blue.png`)**, `WIFI二代设备`, `XKQ-WIFI设备`, `V3-WIFI设备`,
`WIFI&4G`, `AI-WIFI设备`, `AI-WIFI与4G设备`.

BLE-type flow (pool evidence, in order):
1. Page "Bluetooth Device" (`bluetoothDevice` [pp+0x30958]) with the scan
   banner "Searching for nearby Bluetooth devices"
   (`searchingForNearbyBluetoothDevices` [pp+0x308c8], art
   `assets/images/icon_ble_add.png` [pp+0x308b8]).
2. Scanning state "Ble Scanning" / `bleScanning` [pp+0x1ce68/0x1ce70];
   Bluetooth-off check `"日志: 蓝牙未开启"` [pp+0x1ceb0] → permission text
   `bluetoothPermissionIsNotEnabled…` [pp+0x1ceb8]; FBP `startScan`
   [pp+0x1cf28], filter names `BlueNRG-`/`WT-` (§3).
3. Device list with `"BleDevice{name: …, mac: …, rssi: …}"` toString
   ([pp+0x1ce10…0x1ce20]); page breadcrumbs `"蓝牙设备扫描-选择设备"`
   ("scan - select device") [pp+0x1ce50] and `"蓝牙设备扫描-连接蓝牙"`
   ("scan - connect Bluetooth") [pp+0x1ce28]; page title "Connect
   Bluetooth" [pp+0x1ce58].
4. Connect with the retry loop of §2.2; then **binding**: scan the unit's
   barcode (`scan_ble_code.zip` [pp+0x1c340]; fields "MAC Code" [pp+0x1c2f8],
   "WiFi Barcode/Serial No.:" [pp+0x1c308]), page "Bond Device" [pp+0x1c2e8]
   with confirm `"确认绑定"` [pp+0x1c2d0], then the cloud REST call
   `app/device/bound` [pp+0x1c2c0].
5. Model-code mapping seen in the same flow: `0901 → LinkedGo`,
   `0806 → Mini`, `0349 → PC1006` ([pp+0x1cde0…0x1ce08]).

**Notable [P]: the BLE add flow shows NO config-mode instruction.** The
"hold 5 s → bLE" text is not part of this flow's pages (§1.3); the BLE scan
runs against the **normal-mode** advertisement names. The config-mode
instructions belong to the WiFi provisioning wizard (and possibly other
types' add flows), not to the BLE type.

---

## 5. The `631004f3…` frame — full reconstruction [P + layout V]

Context (device-settings page class `kzk`/`_SR` region
[pp+0x18608…0x186f0]), all verbatim [P]:

- Menu item: `"TimeSynchronization"` / `timeSynchronization`
  ([pp+0x18608/0x18610]); ZH `"时间同步"` [pp+0x46398].
- Confirm dialog `"确认时间同步"` [pp+0x18620]; content: "This feature
  will synchronize your phones time to the device. Please ensure that your
  phones time settings are correct." / `timeSynchronizationContent`
  ([pp+0x186e8/0x186f0]).
- Transport candidates: cloud REST `"app/device/sendDevicePassthroughInstruction"`
  ([pp+0x18648]) with `"queryInstruction"` [pp+0x18640]; failure toast
  `"网络请求失败: "` ("network request failed") [pp+0x18628]; success
  `"Successfully~"` [pp+0x18630].
- The frame constant: `"631004f30004080001"` [pp+0x18650], assembled with
  the log `"拼接后的数据: "` ("the concatenated data") [pp+0x18658].

### 5.1 Reconstruction

Modbus FC16 (write 4 registers, byte count 8) — the constant is the
**prefix**; the app appends the 6-byte clock then the CRC:

```
 63  10  04 F3  00 04  08 | 00 01 | <6-byte clock> | CRC_lo CRC_hi
 │   │   │      │    │     │       └ reg 1267 = 0x0001 (trigger flag)
 │   │   │      │    │     └ 8 data bytes
 │   │   │      │    └ quantity = 4 registers
 │   │   │      └ start register = 0x04F3 (1267)
 │   │   └ FC16 write multiple registers
 │   └ slave 0x63
 └── total frame = 15 bytes
```

- Registers touched: **1267 (flag 0x0001), 1268-1270 (the clock)** —
  outside the known 1001-1180 / 2001-2090 blocks; the "time" register
  area.
- **Clock layout [V]** (most plausible, from byte budget): 3 big-endian
  registers packing `[yy MM] [dd HH] [mm ss]`. Illustrative example —
  2026-10-11 16:29:45 → clock bytes `1A 0A 0B 10 1D 2D` → full frame
  `631004f300040800011a0a0b101d2d` + CRC `5d85` (CRC16-Modbus verified with
  the reference implementation of FINDINGS §11):
  `631004f300040800011a0a0b101d2d5d85`.
  Alternative layouts (year-16, BCD) cannot be excluded statically; the
  app's own log `"拼接后的数据: "` prints the real assembly at runtime —
  one logcat capture during a manual time-sync resolves the layout
  definitively.

### 5.2 Verdict on the time-sync frame

- It is a **manual, user-triggered** settings feature (menu → confirm
  dialog), **not** part of the session sequence of §2 — it lives in a
  different class region than the session manager, and nothing references
  it from the connect/read path.
- It is **NOT a connection key**: nothing in the app sends it before or
  during connection establishment.
- The adjacent cloud endpoint indicates that for **online** units the frame
  travels via the vendor cloud (passthrough API). For the owner's BLE-only
  unit the feature is either hidden/unavailable or sent over BLE — **[V]**,
  and irrelevant to the connection problem either way.

---

## 6. MTU & connection parameters [P for the absence, V for the calls]

| Question | Evidence | Verdict |
|---|---|---|
| Does the app set connection priority? | No `connectionPriority`/`requestConnectionPriority` string **anywhere** in the pool (only Flutter `*ScrollPhysics` false positives) | **Never called [P]** (Dart AOT tree-shakes unreferenced code; the pool contains every referenced string) |
| Does the app call `requestMtu`? | Method string present ([pp+0x1c528] `requestMtu`, [pp+0x1c558] `mtu`, [pp+0x1c598] `OnMtuChanged`) — same per-method string placement as the *used* methods `connect` [pp+0x1c408], `discoverServices` [pp+0x1c678], `setNotifyValue` [pp+0x21600], `writeCharacteristic` [pp+0x20b18] | The method is referenced by reachable code — **[V]** by whom (app vs flutter_blue_plus internals) and **with what value**; needs DEX or one runtime log |
| Fragmentation handling | Response reassembly logs of §2 step 6; the RM decoder consumes full frames | The client **must accept any chunking** (20-byte chunks at MTU 23; single ~95-byte notification at higher MTU) |
| Android bonding | See §8 | none |

**BlueZ negotiates MTU automatically during connect — HA cannot control
it**, and our client already handles both fragmentation regimes
(aqua_client reassembly rules 1-3). If the module's firmware mishandles
large MTU exchange, that would manifest as an immediate post-connect drop;
it is host/module-level and out of the integration's control — quickstart
step 6 isolates it via `bluetoothctl`.

---

## 7. Timeout map (all duration constants found in the pool) [P]

| Value | µs | Offset | Region / likely role |
|---|---|---|---|
| 1 s | 0xF4240 | [pp+0xa88] | dart core |
| 2 s | 0x1E8480 | [pp+0x212c8] | **BleManger session path, adjacent to base read** (§2 step 4) |
| 3 s | 0x2DC6C0 | [pp+0x11870] | UI page |
| 4 s | 0x3D0900 | [pp+0x11880] | UI page |
| 5 s | 0x4C4B40 | [pp+0x234b0] | MQTT session manager (near `mqttToken`) — **not BLE** |
| 10 s | 0x989680 | [pp+0x18290] | cloud control `app/device/v3/control` |
| 15 s | 0xE4E1C0 | [pp+0x16158] | dio HTTP layer |
| 20 s | 0x1312D00 | [pp+0x21d70] | **BleManger — BLE connect timeout** |
| 30 s | 0x1C9C380 | [pp+0x168d8] | page class `KK` |

---

## 8. Absence proofs — there is no pairing gate in the app [P]

The user's question — "is there a CODE or a FRAME to send before the PAC
accepts the connection?" — is answered **NO**, on five independent
grounds:

1. **No bonding/pairing APIs.** No `createBond`, `removeBond`,
   `bondState`, `setPin`, or pairing-request string exists anywhere in the
   pool. The only bond-related string is `OnBondStateChanged`
   ([pp+0x1c840]) — one of ~12 event-stream names registered together in
   the flutter_blue_plus plugin's event registry ([pp+0x1c800…0x1c8c0]:
   `OnNameChanged`, `OnServicesReset`, `OnBondStateChanged`,
   `OnDiscoveredServices`, `OnCharacteristicReceived`,
   `OnCharacteristicWritten`, `OnDescriptorRead`, `OnDescriptorWritten`,
   `OnConnectionStateChanged`, `OnMtuChanged`, `OnScanResponse`) — the
   registry exists regardless of app usage, and **no method that would
   *create* a bond is referenced**.
2. **No handshake/unlock frame.** The session sequence (§2) goes
   connect → discover → subscribe → read. No frame constant, token, or
   password string is referenced from the connect path; the write-path
   logs enumerate every frame type the app sends (query frames §2 step 7,
   FC16 writes §2.1, time-sync §5) and none of them is a prerequisite.
3. **No BLE password/PIN/crypto.** Zero candidates in the pool; the only
   password in the device UI is the debug `"66"` parameter-view toggle
   ([pp+0x20c70], `passwordError` [pp+0x20c78]) — a UI feature, not a
   transport credential (FINDINGS §10).
4. **The operating channel scans the normal-mode name** (§3) and its
   failure messaging blames signal/range (§2.3) — inconsistent with a
   pairing-mode gate in daily use.
5. **The "bLE" instruction is scoped to the provisioning wizard** (§1.3),
   whose stated purpose is WiFi network distribution — a concept that does
   not exist on the owner's WiFi-less unit.

**Limits of the proof [V]:** static analysis proves the *app* requires no
gate; it cannot prove the *module firmware* refuses new centrals outside
configuration mode (a "first-provisioning whitelist" behaviour would be
invisible here because provisioning happens inside the wizard). One live
experiment settles it (quickstart step 3): retry the connection while the
panel displays "bLE". No DEX session can substitute for that experiment —
but the DEX session can still name the exact button (§10).

---

## 9. Root-cause model for the HA connection failure

The unit **advertising** (listed, RSSI -67) while **connect fails** is
consistent with all of the following candidates — ranked by likelihood:

1. **[A, leading] The single-connection rule.** A BLE peripheral accepts
   exactly ONE central at a time (controller-level fact, not vendor
   policy). The vendor app **auto-reconnects** (§2.2) — a phone with the
   Aqua Temp app open (even backgrounded) can hold or immediately re-grab
   the link, refusing HA. Note: HA's config-flow list mixes live scans and
   cached discovery entries — a visible RSSI does **not** prove the link
   is free. → quickstart step 1 (kill app + phone Bluetooth off, then
   rescan fresh and test).
2. **[V] First-provisioning gate in module firmware.** If no client has
   EVER completed a GATT session with this unit (no successful probe is
   recorded anywhere in the project artifacts), the module may only
   accept centrals while in configuration mode. → quickstart step 3 (the
   "bLE" experiment).
3. **[V] Wedged module / stale BlueZ state.** Cheap to clear:
   power-cycle the PAC (quickstart step 4); bleak-retry-connector already
   cleans stale BlueZ connections per attempt.
4. **[V] Host-side (BlueZ/adapter): MTU-exchange quirks of the module
   firmware, address-type confusion, weak adapter.** Isolated by the
   `bluetoothctl` differential (quickstart step 6). Nothing the
   integration can configure changes this — matched only by retries and
   proximity (RSSI ≥ -80 recommended for connects).
5. **[A] Range:** -67 dBm is workable but not comfortable; connect
   requests are more fragile than advertisement sniffing. USB-extension
   for the adapter is a classic fix.

The integration-side changes that follow are all **parity/robustness**
items (spec.md FR-1…FR-4) — none of them is a magic unlock, and the spec
says so plainly.

---

## 10. What remains unproven → DEX/native session (Windows node)

The base APK/XAPK and `classes*.dex` are **not on this gateway** (they are
on the Windows node under `C:\reverse\`, per FINDINGS §1). A dedicated
session there can close:

| # | Gap | Method |
|---|---|---|
| G1 | **The exact button behind `[icon]`** (§1.2) | Extract `icon_icon_*.png` (+ any panel asset) from the APK; run the wizard in the app; match asset → add type → instruction key |
| G2 | `requestMtu` caller & value (§6) | classes3.dex: flutter_blue_plus Java side (`connectGatt` flags, auto MTU request); or one logcat with the app |
| G3 | Retry counts / backoff (§2.2) | DEX constant pool + smali of the retry loops (operands invisible in the Dart pool) |
| G4 | `"WT-"` module identity (§3) | DEX string table |
| G5 | Scan timeouts of the add flow | DEX smali |
| G6 | Time-sync clock layout (§5.1) | Runtime logcat `"拼接后的数据: "` during a manual time-sync (no DEX needed if a live unit is available) |

None of G1-G6 blocks this spec: the connection question is answered by
§8 + the quickstart experiments; the button identity has a zero-tooling
workaround (§1.2 — the wizard's own photo).