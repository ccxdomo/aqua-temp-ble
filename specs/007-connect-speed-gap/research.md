# Spec 007 — Connect speed gap: vendor app 3-5 s vs integration >40 s (research)

Status: **WRITTEN** — produced by the Architect from static analysis of the
blutter Dart-pool artifacts (`blutter/pp.txt`, `blutter/objs.txt`) of the
vendor app **Aqua Temp v2.2.1** (`com.phinx.poolheater4`, versionCode 2040),
plus source-level analysis of our client (`custom_components/aqua_temp_ble/`)
and the installed connect stack (bleak-retry-connector 3.6.0/4.7.1, bleak
0.22.3/3.0.2, habluetooth 3.6.0/7.1.2 — the sources pinned by spec 005 §9).

Question this spec answers (production fact, measured by the owner):
**the vendor app completes the whole session (list → select device → values
displayed) in 3-5 s on the owner's real installation, while the Home
Assistant connection test takes >40 s and fails.** The phone's Bluetooth is
OFF during every HA test, which eliminates "the vendor app holds the single
BLE link" as the cause. Same hardware, same distance (the PAC sits next to
the HA host), same user journey. Factor 8-10.

Evidence legend (extends specs 005/006):
- **[P]** PROVEN — verbatim in the artifacts (exact pool offset / source line).
- **[PK]** PLATFORM KNOWLEDGE — well-established behaviour of Android /
  BlueZ / BLE that no artifact in this repo shows, but which is standard
  platform behaviour. Cited as such, never mixed with [P].
- **[A]** ASSUMED — reasonable default, cheap to correct.
- **[V]** TO-VERIFY — needs one live measurement; the quickstart says which.
- **[H]** HYPOTHESIS — ranked, with the measurement that decides it.

TL;DR:
1. **The >40 s failure is a connect-stage stall.** The arithmetic of our
   stack (2 connect attempts × 20 s hardcoded + backoffs) reproduces the
   observed duration exactly, while every post-connect failure mode
   (subscribe, read, CRC) would fail faster and under a *different* error
   key. The failure happens before any of spec 006's parity changes ever
   runs (§8).
2. **The vendor app's 3-5 s is a genuine live session** — connect →
   discover → subscribe → (2 s constant) → read — and the budget closes
   without needing a value cache (§1, §7). A stored-device cache IS proven
   (the app persists the bound device), a stored-`parameter` map exists,
   but neither is required to explain the speed (§7).
3. **The gap lives in the connect/discovery stage on the HA side**, and
   the leading explanations (GATT discovery never resolving over the air,
   MTU-exchange firmware quirk, HCI create-connection never completing on
   a contended single-radio adapter) are all **host/stack-level** — none
   is fixable by sending something different over GATT. The unit provably
   accepts connections from a nearby Android phone in ~1-2 s, so it is not
   globally refusing (§9).
4. **No fix is identifiable from the artifacts.** This spec therefore ships
   **instrumentation** (stage timing logs) plus a **measurement runbook**
   (quickstart) whose decision table converts the next failed run into a
   root-cause class. Measuring first is the deliverable, not a hedge.

---

## 1. Q1 — What the vendor app does between selection and first values

### 1.1 The session sequence [P]

Recovered from the session-manager pool region (class `nzk`/`bS`, the
`BleManger` singleton; storage keys at [pp+0x11340…0x115b8], class-name
string `"BleManger"` [pp+0x113d0]). All quotes verbatim; the pool slice of
one session-manager function contains, in order [pp+0x21160…0x212f8]:

| # | Step | Evidence |
|---|---|---|
| 1 | **Connect** (link establishment) | success log `"日志:设备已连接"` [pp+0x21210]; loss log `"日志:设备断开连接"` [pp+0x21218]; a connection-state listener is mandatory — `"日志:蓝牙设备为空，无法建立连接状态监听器"` [pp+0x21220]; FBP method `"connect"` [pp+0x1c408] |
| 2 | **`discoverServices()` — full enumeration at the API level** | log `"日志:发现服务 "` [pp+0x21258] then per-service/characteristic logging: `ff01→2214`, `1800`, `ff02→2216`, `2a00`, `ff03→2215`, `2aa6` [pp+0x21270…0x212b0]; empty characteristic set is an error `"日志: 服务 … 的特征为空"` [pp+0x212b8…0x212c0]; FBP method string `"discoverServices"` [pp+0x21678] |
| 3 | **Subscribe to ff02** (CCCD `00002902-…`, FBP `setNotifyValue` [pp+0x21600], CCCD UUID [pp+0x21620]) | subscription check log `"是否订阅成功: "` [pp+0x212e8]; error log `"日志:蓝牙返回错误: "` [pp+0x212f0] |
| 4 | **2-second Duration constant in the subscribe→read path** | `Duration(0x1E8480)` = 2 000 000 µs at [pp+0x212c8], between the empty-characteristics error and the base read frame — see §2 |
| 5 | **Send the base read** `"63030007002D3C54"` [pp+0x212d0] | FC03, slave 0x63, start 7, count 45 → 90 data bytes |
| 6 | **Reassemble & decode** | `"日志 蓝牙给的数据包头 "` [pp+0x212f8], `"日志:接受的数据包"` [pp+0x21508] → RM decoder |
| 7 | Block queries on demand | `03e9/0416/0443/0470/07d1/07fe` frames [pp+0x21510…0x21568] |

So: the app does **not** write to hard-coded raw handles and does not skip
discovery — it calls the plugin's `discoverServices()`, walks the result
(it *logs* every service and its characteristics), then picks ff01/ff02 out
of the discovered table by UUID. The handle numbers (`2214`/`2216`/`2215`)
appear in its log strings, not as wire-level shortcuts.

### 1.2 Scan behaviour — two flows, two filters [P]

| Flow | Filter | Evidence |
|---|---|---|
| **Add-device flow** (first contact) | `BlueNRG-`, `WT-` | [pp+0x1d270], [pp+0x1d278]; FBP `startScan` [pp+0x1cf28], stop guard `"[FBP] stopScan: already stopped"` [pp+0x1ce90], scanning state `"Ble Scanning"` [pp+0x1ce68]; live list with per-device toString `"BleDevice{name: …, mac: …, rssi: …}"` [pp+0x1ce10…0x1ce20]; breadcrumbs `"蓝牙设备扫描-选择设备"` → `"蓝牙设备扫描-连接蓝牙"` [pp+0x1ce50/0x1ce28] |
| **BLE provisioning flow** (WiFi models) | `BLE_`, `PROV_` | [pp+0x25150/0x25158], class `BleLinker` [pp+0x25238], `startBleScan` [pp+0x25268] — not the owner's unit |

The add flow scans *continuously while the list page is open* and the user
picks; it does not wait for a "complete" scan result. **Daily use does not
scan at all** — see §1.3.

### 1.3 Daily use: the stored device [P]

- Home-page tap: `"首页-点击设备卡片"` [pp+0x22430] immediately logs
  `"日志:蓝牙 MAC="` + `", NAME="` [pp+0x22420/0x22428] — the identity is
  already known before any connect; breadcrumb `"首页-连接蓝牙设备"`
  [pp+0x225b8].
- The `BleManger` storage singleton persists the bound device:
  SharedPreferences keys `"bleDevice"` (a typed object) [pp+0x11340],
  `"mac"` [pp+0x114f0], `"snCode"` [pp+0x11508]; the
  `plugins.flutter.io/shared_preferences` channel is embedded (pp.txt
  L8208). The session manager logs `"日志 : 存储的蓝牙 "` ("stored
  bluetooth") [pp+0x21da0] right next to `"日志:搜索设备"` ("search
  device") [pp+0x21d90].
- **[V]** whether the daily flow re-scans briefly to re-acquire the device
  object before connecting (`"日志:搜索设备"` hints at a search step) —
  either way it holds the identity from storage, so the resolution cost is
  ~0.

### 1.4 Connect attempts & timeouts [P for values, V for counts]

| Constant / behaviour | Evidence |
|---|---|
| **20 s per-connect timeout** | `Duration(0x1312D00)` = 20 000 000 µs [pp+0x21d70], in the same session-manager slice as `"日志:销毁蓝牙连接"` [pp+0x21168]; matches FBP's `"Timed out after "` message [pp+0x1c648] |
| Retry loop in the add flow | `"尝试重新连接... ("` [pp+0x1c398]; exhausted: `"未能成功连接到设备，已达到最大重试次数"` [pp+0x1c3a0]; UI key `Connect Error`/`connectError` [pp+0x1c380/0x1c388] |
| Auto-reconnect on session loss | `"日志:重连设备中 "` [pp+0x21238], `"日志:重连设备设备 ."` [pp+0x21240]; max reached → popup `"日志:重连设备已达到最大值 弹窗提示失败."` [pp+0x21228] |
| **Retry counts / backoff** | **[V]** — immediate operands, invisible to pool analysis (DED + `dedup_instructions`, FINDINGS §6). Spec 006 §10 G3 already lists this as a DEX gap |

### 1.5 The exact order (answer to the brief)

`connect (20 s window, retries on failure)` → `discoverServices()` (full,
logged) → `subscribe ff02` (CCCD) → **2 s constant sits here in the pool
slice** → `write 63030007002D3C54` → reassemble → display. There is no
step 0 (no unlock frame, no bonding — spec 006 §8 [P]).

---

## 2. Q2 — The 2-second constant: settle delay, or something else?

The constant: `Duration(2 000 000 µs)` [pp+0x212c8], in the same
session-manager function slice as the subscribe→read path (§1.1 steps 3-5),
immediately **before** the base read frame string [pp+0x212d0], with the
subscription-check log `"是否订阅成功: "` [pp+0x212e8] after it.

Three readings fit the pool layout; **static analysis cannot decide
between them** (function bodies are not decompilable, FINDINGS §6):

| Reading | Mechanism | Fits pool order? | Consequence for us |
|---|---|---|---|
| **(a) Settle delay** (spec 006 FR-1's reading) | `await Future.delayed(2 s)` between subscribe and the first read — covers CCCD propagation | yes | our 2 s `SETTLE_AFTER_SUBSCRIBE` is faithful |
| **(b) Subscription-confirmation window** | wait up to 2 s for a subscribe-confirmation signal before proceeding | yes | a 2 s sleep is a conservative superset |
| **(c) Poll cadence** | `Timer.periodic(2 s)` that (re)sends the base read while the status page is open — the first read fires at the first tick | yes (the two `bS` closures right after the frame string [pp+0x212d8/0x212e0] could be the timer callbacks) | the "settle" reading is then a misinterpretation — but harmless: we would simply read immediately, and our 2 s sleep adds 2 s once per connect |

What the artifacts DO say:
- **[P]** a 2 s duration is referenced by session-setup code that also
  references the subscribe check and the base read frame.
- **[P]** no `connectionPriority`/`requestConnectionPriority` string exists
  anywhere in the pool (spec 006 §6) — the app never tunes connection
  parameters.
- **[V]** whether base reads repeat every ~2 s while a status page is open
  (reading (c)). Nothing in the pool contradicts it; a poll loop is a
  normal design for a live dashboard.
- No `Timer` function references were found anywhere in pp.txt's pool
  entries (grep over "Timer::" — dart:async statics do not surface as
  Function entries in this dump), so this is **not** evidence against (c).

**Verdict on spec 006 FR-1:** the "settle" interpretation is **[V], not
[P]** — the spec 006 research said so itself (§2 step 4 marked it [V]), but
spec.md FR-1 hardened it into "vendor parity". If (c) is true, the
implementation is a benign misreading: it costs 2 s per connect and nothing
else. It does NOT affect the speed gap (§8). One `adb logcat` capture of a
vendor session decides it definitively (quickstart step 6): if
`"63030007002D3C54"` appears repeatedly at ~2 s intervals, it is a cadence;
if once, 2 s after the subscribe, it is a delay.

---

## 3. Q3 — Does our client do a full GATT enumeration?

Source read: `custom_components/aqua_temp_ble/aqua_client.py` (commit
6ae0afa).

- **We never iterate `client.services` ourselves.** `connect()` calls
  `start_notify(UUID_NOTIFY)` and `_resolve_write_characteristic()`, which
  does `client.services.get_characteristic(UUID_WRITE)` — a lookup in an
  **already-populated** collection. No walking of all
  services/characteristics in our code. **[P]**
- **But the enumeration happens anyway, inside bleak's `connect()`.**
  bleak's BlueZ backend calls `self._get_services(...)` at the end of
  `connect()` (bleak 3.0.2 `backends/bluezdbus/client.py` L301-306, read in
  `/tmp/bleak21`), which waits for BlueZ's `ServicesResolved` — i.e. the
  **full over-the-air discovery of every service, characteristic and
  descriptor**, performed by BlueZ. This runs **inside the same 20 s
  `async_timeout` window as the link establishment** (L153). **[P]**
- Cost on a weak link: the PAC's table is small (~3 services: GAP 1800 with
  2a00/2aa6, likely 1801, and the vendor service with ff01/ff02/ff03
  [pp+0x21270…0x212b0] → ~6-7 characteristics) — a few dozen ATT PDUs. On a
  clean link: ~0.5-2 s **[PK]**. On a marginal link with retransmits, it
  stretches arbitrarily; discovery is the most loss-sensitive GATT
  procedure because one unanswered read stalls the whole walk. **[PK]**
- **The vendor app does NOT avoid enumeration at the API level** (§1.1
  step 2 [P]) — but **Android serves `discoverServices()` from its
  system-wide GATT cache** after the first-ever successful connect to that
  address, so the app's daily sessions do no over-the-air discovery at
  all. **[PK]** BlueZ has the same class of cache
  (`/var/lib/bluetooth/<adapter>/<device>/cache`), but it is only written
  after a **successful** discovery — and our host has never completed one
  connect (the test never passed), so **every attempt re-discovers over
  the air.** **[PK + observed-never-succeeded]**

So both sides "do discovery"; the asymmetry is that the phone's cache is
warm from months of app usage and the Pi's is cold because our connect has
never once succeeded. This is the mechanism behind hypothesis **H-A**
(§9) — and note it is self-reinforcing: discovery-stall → no cache → next
attempt discovers over the air again.

---

## 4. Q4 — What `establish_connection` really does

Source read: bleak-retry-connector **3.6.0** and **4.7.1** (`__init__.py`,
`bluez.py`); constants and loop verified at 4.7.1
(`/tmp/bleak21/lib/python3.13/site-packages/bleak_retry_connector/__init__.py`).
Spec 005 §3.2 established the signature; this spec re-verifies the loop
mechanics because the >40 s number depends on them.

- **brc does not scan and does not enumerate services.** It requires the
  caller to pass a resolved `BLEDevice`; the enumeration is bleak's, inside
  brc's connect call. brc itself only: pre-checks for an already-connected
  device (`get_connected_devices` — D-Bus, fast) and, between attempts,
  `wait_for_disconnect`. **[P]**
- **Per-attempt connect timeout is hardcoded**: `BLEAK_TIMEOUT = 20.0` s
  (L105), passed to `client.connect(timeout=20.0, …)` (L544-546), wrapped
  in a `BLEAK_SAFETY_TIMEOUT = 60.0` (L116/L538). Our
  `const.CONNECT_TIMEOUT` constructor kwarg remains dead for the connect
  attempt (spec 005 §3.2 [P]). **[P]**
- **The retry loop counts classes, not raw attempts** (L492-565):
  `timeouts`, `connect_errors`, `transient_errors`; the terminal raise
  fires when `timeouts + connect_errors >= max_attempts` or
  `transient_errors >= 9` (`MAX_TRANSIENT_ERRORS`). An
  `asyncio.TimeoutError` per-attempt failure counts as a **timeout**, gets
  `BLEAK_DBUS_BACKOFF_TIME = 0.25` s backoff + `wait_for_disconnect`, and
  the terminal exception for exhausted timeouts is **`BleakNotFoundError`**
  ("Failed to connect after N attempt(s)") — a misleading name: nothing
  was "not found"; the connect window simply never completed.
  `KeyError`/`BrokenPipe`/`EOF` count as **transient** and get up to 9
  extra tries even with `max_attempts=2`. **[P]**
- **Timing arithmetic with `max_attempts=2` and a hanging connect**:
  attempt 1 consumes the full 20.0 s window → 0.25 s backoff +
  `wait_for_disconnect` (fast when nothing connected) → attempt 2 consumes
  20.0 s → raise. Total ≈ **40.5-43 s**, then our `connect()` logs the
  ERROR line with the cause chain (`BleakNotFoundError: … <-
  TimeoutError`) and the config flow shows `cannot_connect`. **[P]**
- **Does `max_attempts=2` legitimately double the time on failure?** Yes —
  exactly in the observed case: for fast failures (an active refusal —
  D-Bus error within ~1-5 s) the second attempt also fails fast; for a
  hanging connect the two attempts burn 2×20 s. The doubling only
  materializes when the connect *hangs*, which is what >40 s measures.
  **[P]**
- **Cold time**: on a healthy path the whole `establish_connection`
  returns in ~1-3 s (link + BlueZ discovery) **[PK]**; nothing in brc
  adds fixed overhead beyond the backoffs.

---

## 5. Q5 — Device resolution cost per test

- **Ours**: `_resolve_device()` (aqua_client) is three pure cache lookups
  inside HA (spec 005 §3.1 [P]); the config-flow list and the resolution
  read the same manager history; the HA wrapper re-resolves the best
  backend device at connect time, also from caches (spec 005 §2.3 [P]).
  **~0 s per test.** No scan is started anywhere in the HA path. **[P]**
- **Vendor's**: the add flow already holds the device from its live scan
  list; the daily flow holds the stored device from SharedPreferences
  (§1.3 [P]). **~0 s.**
- **Verdict: resolution is not the gap.** Both sides pay ~nothing; the
  parity is exact here.

---

## 6. Q6 — MTU

- **The app never tunes connection parameters** — no
  `connectionPriority`/`requestConnectionPriority` string exists anywhere
  in the pool. **[P absence, spec 006 §6]**
- **`requestMtu`**: the method string exists ([pp+0x1c528] `requestMtu`,
  [pp+0x1c558] `"mtu"`, [pp+0x1c598] `OnMtuChanged`) **but that pool region
  is the flutter_blue_plus plugin's method-channel codec** (same slice as
  `connect` [pp+0x1c408], `OnConnectionStateChanged` [pp+0x1c4e0],
  `remote_id`, `connection_state`, `disconnect_reason_code` — the plugin's
  full registry, registered whether or not the app calls it). The string's
  presence proves the plugin API is compiled in, **not** that the app calls
  it. **[V]** — who calls `requestMtu` and with what value remains spec
  006 §10 gap G2 (DEX session or one logcat).
- **The app does not need a big MTU.** At Android's default ATT MTU 23
  **[PK]**, the 90-byte base response arrives as ~5 × 20-byte notifications,
  and the app provably reassembles (packet-header/received-packet logs,
  §1.1 step 6 [P]). So a small MTU cannot explain the vendor's *speed*
  being fine — and it cannot explain our *failure* either: fragmentation
  is handled by our reassembly rules (aqua_client rules 1-3), and a
  read-stage problem would fail as `no_response` after ~11-14 s, not as
  `cannot_connect` after >40 s (§8).
- **Our side cannot control MTU**: BlueZ negotiates the ATT MTU itself
  during connection setup **[PK]**; bleak exposes no knob. The one MTU
  shaped risk is hypothesis **H-C** (§9): if this module's firmware
  mishandles BlueZ's automatic MTU exchange (Android at default 23 sends
  no exchange at all unless the app asks [PK]), the link could drop
  exactly when our side connects — a vendor-app-invisible, our-side-fatal
  quirk. `btmon` decides (quickstart step 3).

---

## 7. Q7 — Are the 3-5 s even real? The cache question

What is **[P]** about caching in the app:
- The **device identity** is cached and persisted: `bleDevice`/`mac`/
  `snCode` SharedPreferences keys (§1.3) — so the app's "list → select"
  journey starts from storage, instant.
- The app also persists a `"parameter"` map (`Map<String,int>`,
  [pp+0x11410]) and a `"fault"` map (`Map<String,bS>`, [pp+0x113b0]) under
  the `BleManger` keys, plus `"timerId"` [pp+0x11370] — i.e. last-known
  parameter/fault data survives sessions. Whether the status page renders
  these before the first live frame arrives is **[V]** (invisible
  statically; one screen-recording or logcat decides).

But the decisive reasoning is the **time budget of a live session** on the
same hardware:

| Stage (vendor, Android) | Estimate | Class |
|---|---|---|
| Direct connect to an advertising device (`connectGatt`, autoConnect=false) | ~0.5-2.5 s | [PK] |
| `discoverServices()` served from Android's GATT cache | ~0.1-1 s | [PK] |
| Subscribe ff02 (CCCD write) | ~0.1-0.5 s | [PK] |
| 2 s constant (settle or first poll tick — §2) | 2 s | [P constant, V role] |
| Base read + ~5 notification chunks at MTU 23 | ~0.1-0.5 s | [PK] |
| **Total** | **≈ 3-5 s** | — |

The owner's measured 3-5 s is **fully accounted for by a live session** —
no value cache is needed to explain it. The 2 s constant even *suggests*
the observed time includes a deliberate 2 s wait, which matches a
live-read pipeline better than a cache-render (a cache would render in
<1 s).

**Verdict on the brief's Q7: the cache hypothesis is PARTIALLY RETAINED
but DOWNGRADED to non-explanatory.** A stored-device cache is proven
(and our tier-1 resolution is its exact counterpart); a stored-parameter
cache exists; but the 3-5 s does not require values to be cached, and the
comparison remains valid for the question that matters: the app completes
an entire connect→discover→subscribe→read session in ≤5 s, while our
client does not complete even the connect/discovery stage in 40 s. The gap
is real and lives before any values exist. **[P reasoning]**

---

## 8. Q8 — Where does our test actually fail?

The observable facts: >40 s duration, `cannot_connect` error key
("Impossible de se connecter"), systematic (never a single success), no
logs captured before spec 005's logging shipped.

Signature table — every failure mode of the current code with its expected
duration and error key (all durations from the stack arithmetic of §4 and
the repo constants):

| Failure stage | Expected duration | Error key | Our log lines |
|---|---|---|---|
| Resolution miss (all caches) | ~0 s | `not_found` | step-3 WARNING (always fires) |
| **Connect window never completes (hang)** | **2 × 20 s + backoffs ≈ 40.5-43 s** | **`cannot_connect`** | our ERROR `connect to … failed: BleakNotFoundError: … <- TimeoutError` |
| Active connect refusal (link-level error) | ~1-10 s | `cannot_connect` | our ERROR with the D-Bus error |
| ff01/ff02 setup failure after connect | ~3-6 s | `cannot_connect` | our ERROR `post-connect setup … failed` |
| Read timeout (no response) | connect + 2 s settle + 8 s ≈ 11-14 s | `no_response` | WARNING `no response` |
| Bad CRC / shape | same as read, +silence | `crc_error` | WARNING `bad response` |
| Budget expiry mid-flight (70 s) | 70 s | `cannot_connect` | WARNING `exceeded the … budget` |

**The observed ">40 s + cannot_connect" matches the first row and nothing
else** — the 2×20 s connect-window signature. The >40 s duration is itself
the measurement: it is not a guess, it is the arithmetic of the stack we
run. **[P arithmetic]**. Confirmation is one log line away: the deployed
build (6ae0afa) logs the ERROR with the
`BleakNotFoundError: … Failed to connect after 2 attempt(s)` chain on
every failed run (spec 005 FR-2) — quickstart step 1 asks for exactly that
line. Until it is captured, the stage remains formally **[V]** — but the
duration signature makes every post-connect stage (the only stages spec
006's parity changes touch) implausible.

**Corollary — spec 006 could not have fixed this and did not:** the 2 s
settle, write-without-response, `wt-` prefix and the 70 s budget all
operate **after** a successful connect or **around** it; the failure happens
inside `establish_connection`, before any of them runs. The budget raise
40→70 s masked nothing here (the failure surfaces at ~40.5 s on its own)
but it also fixed nothing: it made the same stall take longer to report.
The one spec-006 change that does interact with the stall
(`max_attempts=2`) is what produces the exact >40 s figure on the current
build. **[P]**

---

## 9. The diagnosis — ranked hypotheses for the connect-stage stall

Established frame:
- The unit **accepts a connection from a nearby Android phone in ~1-2 s**
  (owner's app observation, phone BT on) — so it is not globally refusing
  centrals. **[P observation]**
- Phone BT off during all HA tests — the single-link-held-by-phone
  explanation is eliminated by protocol. **[P observation]**
- Our two 20 s windows expire without a D-Bus error — the connect is
  *hanging*, not being actively refused (a refusal raises within ~1-5 s).
  **[P arithmetic, §8]**
- The HA host hears the unit at RSSI -67 at close range — workable, but
  oddly weak for <1 m; hints at weak TX/antenna or RF attenuation on the
  HA host side, and discovery is more loss-sensitive than advertising
  sniffing. **[A]**

Ranked hypotheses (each with the measurement that decides it — all in the
quickstart):

| # | Hypothesis | Class | Mechanism | Decided by |
|---|---|---|---|---|
| **H-A** | **GATT discovery never resolves over the air on our host** | [H] leading | Link establishes, BlueZ's `ServicesResolved` never becomes true within the window (lossy link / firmware ATT quirk); every attempt re-discovers because the BlueZ cache stays cold (§3); Android never sees this because its GATT cache is warm | `btmon` during a failing test: `LE Connection Complete` present, `Read By Group Type` starts but never finishes / loops; `bluetoothctl` connect hangs at services |
| **H-B** | **HCI create-connection never completes** | [H] | BlueZ's initiator cannot complete the link: RF (single-radio Pi adapter scanning continuously while connecting — a known weak spot of built-in Pi Bluetooth), address-type/PHY mismatch, adapter state | `btmon`: no `LE Connection Complete` within the 20 s window while CONNECT_REQ/scan visible; differential: connect with HA stopped vs running |
| **H-C** | **MTU-exchange firmware quirk** | [H] | BlueZ auto-exchanges ATT MTU at connect [PK]; Android at default 23 sends no exchange unless the app asks [PK]; if this module's firmware breaks on the exchange, the link drops right after — invisible to the vendor app, fatal to us | `btmon`: `ATT Exchange MTU Request` immediately followed by link drop / silence |
| **H-D** | **Wedged module / stale link slot** | [H] unlikely | A previous cancelled attempt left the unit's single-connection slot occupied (spec 005 §4.3); phone BT off does not clear a *unit-side* wedge | Power-cycle the PAC + `bluetoothctl remove` → immediate retest (quickstart step 5) |
| H-E | Unit intermittently non-advertising (stale HA list entry) | [H] unlikely | The RSSI in HA's list can be up to ~195 s + 300 s old (spec 005 §2.1); if the unit stopped advertising, the initiator finds nothing | `btmon`/live scan: is the unit advertising *during* the failing test? The vendor app's live scan seeing it at the same moment weakens this |

What is **excluded** by evidence or arithmetic: the single-link rule
(user protocol, [P]); device resolution cost (§5, [P]); read/MTU/decode
stages (§6/§8, [P arithmetic]); a missing pairing gate (spec 006 §8, [P]);
every spec-006 parity item (§8 corollary, [P]).

**None of H-A/H-B/H-C is fixable by the integration sending something
different over GATT** — they are host/stack/adapter-level. That is why
this spec ships instrumentation + measurement instead of a patch: the next
failing run must name the stage and the HCI truth before any code change
is designed.

---

## 10. What remains unproven — and how this spec closes it

| # | Gap | Closed by |
|---|---|---|
| G-1 | Actual stage of the production failure (formally) | FR-1/FR-2 stage logs + the existing spec-005 ERROR line — quickstart step 1, one run |
| G-2 | HCI truth during the stall (link? discovery? MTU?) | `btmon` capture — quickstart step 3, one run |
| G-3 | Scanning-contention contribution | HA-stopped differential — quickstart step 4 |
| G-4 | Role of the 2 s constant (settle vs cadence) | `adb logcat` of one vendor session — quickstart step 6 (also spec 006 §10 G2/G5 class) |
| G-5 | `requestMtu` caller & value | same logcat (an `OnMtuChanged` line with a value proves the app requests one) — spec 006 §10 G2 |
| G-6 | Vendor-side per-stage durations on the same hardware | same logcat (timestamps of 设备已连接/发现服务/指令已发送) |
| G-7 | Whether the app renders cached values pre-read | same logcat (values on screen before the first 指令已发送 line?) |

A DEX/native session (Windows node, `C:\reverse\`) remains useful for
retry-count immediates (spec 006 §10 G3) but is **not** on the critical
path for the speed gap: quickstart steps 1-4 require no reverse tooling.

---

## 11. Sources

- `blutter/pp.txt`, `blutter/objs.txt` (this repo) — all `[pp+0x…]` refs.
- Repo at `6ae0afa` (clean tree): `aqua_client.py`, `config_flow.py`,
  `const.py`, `coordinator.py`, `tools/smoke_client.py`, translations.
- bleak-retry-connector 3.6.0 / 4.7.1 `__init__.py` + `bluez.py`; bleak
  0.22.3 / 3.0.2 `backends/bluezdbus/client.py` (read in
  `/tmp/bleak21`, pinned by spec 005 §9).
- Specs 005 (`research.md` §2-§6) and 006 (`research.md` §2, §6-§8) —
  carried-over proofs cited as such, re-verified where load-bearing.
- FINDINGS.md §3/§6/§11 — transport, DED limitation, protocol.