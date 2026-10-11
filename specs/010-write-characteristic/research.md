# Spec 010 research — the TRUE write characteristic, read from the app's own code

Date: 2026-10-11
Evidence base: **raw disassembly of the vendor app itself** —
`libapp.so` (Dart AOT, sha256 `b0ae8c66…`) plus the blutter artifacts
(`pp.txt`, `objs.txt`, full `asm/` output at `/tmp/blutter_out/` on the
gateway). Every address below was disassembled with capstone from
`/tmp/flutterapp/lib/arm64-v8a/libapp.so` and cross-checked against
blutter's own `asm/nzk.dart` rendering. Pool offsets are `[pp+0x…]`
entries in `blutter/pp.txt`.

Status legend: **[P]** PROVEN (read in the disassembly, addresses given),
**[V]** TO-VERIFY (one live run decides), **[A]** ASSUMED (reasonable
default), **[D]** DECISION.

---

## 0. TL;DR — the answer, black on white

> **The vendor app writes to `ff03` on the owner's unit.**
> The Dart-side session manager classifies GATT characteristics into two
> buckets **by UUID, not by advertised properties**:
>
> - **notify bucket** (state field `0x2b`): `{ff02, 2216, 2a00}` → subscribed (setNotifyValue);
> - **write bucket** (state field `0x2f`): `{ff03, 2215, 2aa6}` → every command frame is written here, always `WriteType.withoutResponse`.
>
> On the owner's unit (service `ff01` containing chars `ff03` and `ff02`)
> this resolves to **write = ff03, notify = ff02** — the exact pair our
> integration already uses at runtime. The "ff01 is the write char" claim
> (FINDINGS §3/§11.3, DEX string-table constants) belongs to a **different,
> non-operating code path** and is wrong for this unit.
>
> Consequence: hypotheses H1 (wrong write char), H2 (different write
> format/type) and H3 (missing handshake) of the task brief are all
> **disproven by the app's own code**. Our session is already app-parity
> on char, write-type, frame bytes and sequence. The silence mystery
> therefore moves to the last standing causes: the app may ALSO get no
> answers on this unit (push-only firmware), or something below the GATT
> layer differs — decided by E5 (one HCI snoop of a vendor session on
> THIS unit) plus one grep of the existing production log.

The proof below is complete enough to be re-verified by anyone with the
two files (`libapp.so` + capstone, or blutter's `asm/nzk.dart`).

---

## 1. Where the app stores "the characteristic to write" — no hardcoded single UUID

The app never stores one write UUID. It stores **two characteristic
objects** in the BLE session state, picked at service-discovery time.
This was recovered from the **discovery classifier closure** of the
session manager (class `bS`, library `nzk` — the "BleManger" storage
singleton, spec 006 §2):

```
[closure] Future<Null> <anonymous closure>(dynamic, List<ZMa>) async
    // ** addr: 0xa146dc, size: 0x628        (nzk.dart, blutter asm)
```

`ZMa` = flutter_blue_plus' `BluetoothService` (the same type argument
appears in the FBP method-channel plumbing at `[pp+0x21640]`).
The closure is the `OnDiscoveredServices` callback: it receives the
service list and classifies it.

### 1.1 The classifier code, fully reconstructed [P]

Outer loop (per service): builds the debug log
`"日志:发现服务 <uuid>  特征<n> 特征<lastChar>"` (`[pp+0x21258/60/68]`),
then — at `0xa148b4-0xa14968` — compares the **service UUID** against
three strings:

| Address | Compared string | On match (TRUE) | Meaning |
|---|---|---|---|
| `0xa148b4` | `"ff01"` [pp+0x21270] | enter char loop | vendor service, generation 1 |
| `0xa14900` | `"2214"` [pp+0x21278] | enter char loop | vendor service, generation 2 |
| `0xa1494c` | `"1800"` [pp+0x21280] | skip service | GAP — scanned, but see §1.3 |

Services matching **none** of the three are skipped entirely (branch at
`0xa14968` → `0xa14c20`).

Inner loop (per characteristic, `0xa14984-0xa14c14`): compares each
**characteristic UUID** against two triples, and **stores the
characteristic object** into the session state on match:

```
0xa14a00  "ff02"  ┐
0xa14a4c  "2216"  ├─ match → 0xa14ab8: state->field_2b = char   (notify bucket)
0xa14a98  "2a00"  ┘

0xa14b18  "ff03"  ┐
0xa14b64  "2215"  ├─ match → 0xa14bd0: state->field_2f = char   (write bucket)
0xa14bb0  "2aa6"  ┘
```

The stores, verbatim from blutter's own `asm/nzk.dart` [P]:

```
// 0xa14ab8: ldur            x2, [fp, #-0x10]        ← the closure context
// 0xa14abc: LoadField: r1 = r2->field_f              ← the state object
// 0xa14ac8: StoreField: r1->field_2b = r0            ← NOTIFY bucket

// 0xa14bd0: ldur            x2, [fp, #-0x10]
// 0xa14bd4: LoadField: r1 = r2->field_f
// 0xa14be0: StoreField: r1->field_2f = r0            ← WRITE bucket
```

### 1.2 Boolean polarity — why the match sets are certain [P]

The compiled comparisons end in `tbz w0, #4, <store>` (branch if bit 4 is
ZERO) for the two first strings of each triple and `tbnz w0, #4, <skip>`
(bit 4 NON-ZERO) for the third. Reading TRUE/FALSE from the same file's
own constants settles the polarity beyond doubt — blutter annotates
them directly in the connection-state closure (`0xa137dc`):

```
// 0xa13878: r1 = true   → add x1, NULL, #0x20 ; true
// 0xa138dc: r2 = false  → add x2, NULL, #0x30 ; false
```

`true` = 0x20 (bit 4 clear), `false` = 0x30 (bit 4 set). Hence each
`tbz` after a comparison branches **on match**, and the `tbnz` after the
third comparison branches **on non-match**. The buckets are therefore
exactly `{ff02, 2216, 2a00}` → `field_2b` and `{ff03, 2215, 2aa6}` →
`field_2f` — no other reading of the branch graph is consistent.

### 1.3 What the two buckets mean — the app's GATT model [P]

The nine UUID shorts + the standard-GATT strings (`"1800"`, `"2a00"`,
`"2aa6"`, and `"1801"`/`"2A05"` at `[pp+0x216d8/0x216e0]`) were the
pool's whole UUID vocabulary (all 4-char hex strings in `pp.txt`,
exhaustively enumerated). They describe **two vendor GATT generations
plus GAP fallbacks**:

| Layout | Service | Notify char (bucket A / field_2b) | Write char (bucket B / field_2f) |
|---|---|---|---|
| Gen 1 (**the owner's unit**, `BlueNRG-C0941C`) | `ff01` | `ff02` | `ff03` |
| Gen 2 (older/other modules — the "2214" service) | `2214` | `2216` | `2215` |
| GAP fallback (both buckets get overwritten by any vendor match — stores are last-wins) | `1800` | `2a00` (Device Name) | `2aa6` (Central Addr. Res.) |

Two consequences:

- The old FINDINGS §11.3 table (`ff01 → char 2214, ff02 → char 2216,
  ff03 → char 2215`) was a **misreading**: those pairs are not
  "characteristic → handle", they are the two generations' UUID sets
  laid out adjacently in the pool.
- **The Dart app never writes to a characteristic named `ff01`.** It is
  not in the write bucket. On a unit whose GATT exposes a *characteristic*
  `ff01` (the classic FF00-service Chinese modules), this app would
  ignore it.

### 1.4 How the UUIDs are compared — why "contains" vs "==" does not matter [P]

Before every comparison the code calls a helper at `0x5d9f64` (tagged
call, direct) with the UUID object, then a GDT-dispatched method
(`GDT[cid-0xffc]`), then compares the result against the 4-char constant.
Disassembly of the helper (real entry `0x5d9f64`, body reachable through
the dedup trampoline at `0x5d9f60 → b 0x5d9e24`) shows it is FBP's
uuid-string normalizer:

```
0x5d9f8c-0x5d9f9c:  r0 = <guid>.str  (internal string, [pp+0x1c478] "remote_id" family)
0x5d9f90-0x5d9f94:  "0000" [pp+0xdab8] involved (prefix/short-form handling)
0x5d9fc0-0x5d9fc4:  "-0000-1000-8000-00805f9b34fb" [pp+0x1d158]  ← the UUID base suffix
0x5d9fd8-0x5da054:  endsWith/startsWith checks then substring-style
                    extraction (bounds-checked via 0x309010, impl 0x308f24)
```

i.e. it reduces `0000ff01-0000-1000-8000-00805f9b34fb` to a short form
comparable with `"ff01"`. Whether the final test is `==` on the short
form or `contains` on the full form, **the classified sets are identical**
(a full UUID `contains` exactly one of the nine shorts, and only its
own). Both readings were checked against every branch outcome; they
agree on all services/chars of this unit's table.

---

## 2. What the app does with each bucket — the session sequence [P]

Right after classification, the classifier tail calls the
**session-continuation function** at `0xa14d04` (call site `0xa14ca8`).
Its full flow, from disassembly:

```
0xa14d48-0xa14d64:  if state->field_2b != null:      ← NOTIFY bucket
0xa14d64:               bl 0xa14220(state.field_2b)  ← SUBSCRIBE (see §2.1)
0xa14d70-0xa14d7c:      await the subscribe future
0xa14d84-0xa14d94:  x2 = [pp+0x212c8] Duration(seconds: 2)
0xa14d94:               bl 0x34733c(null, 2s)       ← Future.delayed(2s)
0xa14da0:               await
0xa14da4-0xa14db0:  bl 0xa14ea8(state, "63030007002D3C54")   ← SEND base read
0xa14de8-0xa14e44:  if state->field_2b != null:
                       attach value listener: 0x5db564(bucket-A char) →
                       0x8a9398 with closures [pp+0x212d8] = 0x5dbf00
                       (void (dynamic, List<int>) — the notification
                       data handler) and [pp+0x212e0] = 0x5dbe7c
                       (error handler); logs "是否订阅成功: " [pp+0x212e8]
```

This is the **entire** pre-write sequence: subscribe the notify char,
wait 2 s, write the base read to the write char, then attach the data
listener. There is no other frame, token, unlock, MTU dance or ordering
trick anywhere between connect and the first write (all pool references
of the session function are accounted for above).

### 2.1 The subscribe wrapper = setNotifyValue on the notify bucket [P]

`0xa14220` (called with `x1` = the field_2b char at `0xa14d64`) spans
`0xa14220-0xa14658` and contains **all seven** `"setNotifyValue"`
string references of the whole binary (`0xa14460, 0xa144d0, 0xa14508,
0xa1452c, 0xa14580, 0xa145ac, 0xa145fc` — full-text pool-load scan,
§4 method). FBP's CCCD constant
`"00002902-0000-1000-8000-00805f9b34fb"` [pp+0x21620] and
`"force_indications"` [pp+0x21638] live in the FBP layer it calls.
The same wrapper is also called from the reconnect path (`0xa1410c`,
inside the on-connected handler `0xa13c88-0xa14220` region) —
re-subscription after reconnect, same bucket.

### 2.2 The sender — writes go through ONE function, and it demands the write bucket [P]

`0xa14ea8` (called at `0xa14db0` with `(state, "63030007002D3C54")`):

```
0xa14ed8-0xa14ee4:  require state->field_7  != null   (device/session object)
0xa14ee8-0xa14ef4:  require state->field_2f != null   ← WRITE bucket, else abort
0xa14ef8-0xa14f04:  bl 0x5dbd28(state, frameString)   ← hex → bytes
0xa14f34-0xa14f5c:  build log "日志:发送的指令" + frame + "  字节" + bytes
0xa14f90:           if frameString.length >= 20 chars: iterate state->field_5f
                    (a per-element dispatched call — queued/derived data for
                    long frames; the 16-char base read skips this path)
0xa1503c-0xa15048:  w0 = state->field_2f; save to [fp-0xa0]      ← WRITE char
0xa1504c-0xa151d8:  allocate Uint8List(len(bytes)), memcpy the bytes
0xa151d8-0xa151e0:  x1 = [fp-0xa0] (= field_2f char), x2 = bytes
                    bl 0xa15354                                    ← THE WRITE
0xa15224:           on success log "日志:指令已发送" [pp+0x20ab8]
0xa15308:           on failure log "日志:指令发送失败" [pp+0x20ad0]
```

The hex converter `0x5dbd28` (disassembled in full): rejects empty
strings, strips/replaces via `[pp+0x908]/[pp+0x910]`, **requires even
length** (odd → the `"Hex string must have an even length"` error,
[pp+0x20b40]), allocates `len/2`, then loops
`substring(s, i, i+2)` → `int.parse(pair, radix 16)` (`0x32f66c`, radix
arg `0x20`→Smi 16 at `0x5dbde8`). The app therefore writes **raw bytes
of exactly our frame** — `63 03 00 07 00 2D 3C 54` — no envelope, no
length prefix, no proprietary header.

### 2.3 The write wrapper — `writeCharacteristic` with `withoutResponse`, always [P]

`0xa15354` (the only function containing the `"writeCharacteristic"`
references, `0xa15584-0xa15718`; reached exclusively from the sender's
call at `0xa151e0` — verified by a whole-binary BL-target scan, §4):

```
0xa15380-0xa153a0:  device/connection check (char->field_7; 0x5c78ac;
                    on false → error path 0xa15684)
0xa153ac-0xa153bc:  FBP "global" [pp+0x1c3c0] plumbing
0xa153e8-0xa1541c:  build the method-channel argument object:
                      field_7  = remoteId
                      field_b  = characteristicUuid   (char->field_b)
                      field_13 = serviceUuid           (char->field_13)
0xa15420-0xa15428:  field_17 = [pp+0x20ad8] = WriteType PMa {0x1:"withoutResponse"}
0xa15584+        :  invoke channel "writeCharacteristic" with
                    "write_type" [pp+0x20b30] and "allow_long_write" [pp+0x20b38]
```

**`WriteType.withoutResponse` is the only write-type enum instance in
the entire object pool** (`objs.txt` has exactly one `PMa` object; no
`withResponse`, no `noResponse` string exists anywhere in `pp.txt`).
The app always writes without response. [P]

### 2.4 Every outgoing frame of the app goes through this one sender [P]

A whole-`.text` scan for `bl` instructions targeting `0xa14ea8` (the
sender) returns **20 call sites**, and for `0xa15354` (the write
wrapper) exactly **one** (inside the sender). The sender's callers:

| Call sites | Code region | What is sent |
|---|---|---|
| `0xa14db0` | session continuation | base read `63030007002D3C54` |
| `0x5ddaa0, 0x5ddb48, 0x5ddbf0, 0x5ddc98, 0x5ddd40, 0x5dddec` | block-query closures (six — one per frame constant `[pp+0x21518-0x21568]`) | `6310 03E9/0416/0443/0470/07D1/07FE 002D …` |
| `0x64fbd8, 0x650210, 0x652630, 0x652858, 0x65319c, 0x6533c8, 0x653c1c, 0x653e48` | `[mrk] _nR` parameter pages | FC16 writes (`"日志:指令 6310"` builder at `0x64fd28`) |
| `0x6a14b8, 0x6a1774, 0x6a2158, 0x6a2588` | control pages | power/mode/setpoint writes |

**All** of them pass through `0xa14ea8`, which refuses to run without
`state->field_2f` (the write bucket) and writes to that char. There is
no second write path in the Dart code. [P]

---

## 3. The DEX constants (ff01 write / ff02 notify) — what they actually are [P that they exist, P that the Dart channel ignores them]

FINDINGS §3 correctly reported the Java-side string-table constants:

```
BLE_WRITE_CHARACTERISTIC_UUID  = 0000ff01-0000-1000-8000-00805f9b34fb
BLE_NOTIFY_CHARACTERISTIC_UUID = 0000ff02-0000-1000-8000-00805f9b34fb
```

But the operating channel of this app is the **Dart side driving the
flutter_blue_plus plugin** (channel `flutter_blue_plus/methods`; the
write wrapper of §2.3 is that channel's client). In the Dart code:

- `ff01` is **not** in the write bucket `{ff03, 2215, 2aa6}` — a
  characteristic `ff01` would never be selected for writing;
- the session's method-channel argument object carries
  `serviceUuid`+`characteristicUuid` **dynamically** from the classified
  characteristic — no Dart code path needs or uses a hardcoded write
  UUID.

The DEX constants therefore belong to a **different Java BLE stack**
bundled in the same APK (the DEX also carries `fec7/fec8/fee7/ffff` —
Telink-mesh and provisioning-era UUID families). That stack serves the
WiFi-provisioning / other-module flows (the `BleLinker` wizard, spec 006
§3), **not** the BlueNRG operating channel: the operating scanner
(`BlueNRG-`/`WT-` filter) feeds the Dart session manager, whose code is
what §§1-2 disassemble. The "ff01 write" reading was an artifact of
pool-adjacency (§1.3) plus the DEX string table; the disassembly of the
actual write path settles it.

---

## 4. Method — how these facts were extracted (reproducible) [P]

1. **blutter artifacts** (already in repo, `blutter/pp.txt`, `blutter/objs.txt`):
   pool strings, object pool, class list. The full run output — including
   the `asm/` tree and `ida_script/addNames.py` — survived on the gateway
   at `/tmp/blutter_out/`, with the input at
   `/tmp/flutterapp/lib/arm64-v8a/libapp.so`.
2. **blutter asm**: `asm/nzk.dart` contains the classifier closure
   (`0xa146dc`) and the connection-state closure — the only session
   functions whose bodies survived dedup. These gave the UUID comparison
   chains and both bucket stores verbatim.
3. **capstone (5.0.9, installed in /tmp/blutter-venv)**: linear
   disassembly of `libapp.so` regions whose function bodies were deduped
   (`size: -0x1` in blutter): the session continuation `0xa14d04`, the
   subscribe wrapper `0xa14220`, the sender `0xa14ea8`, the write wrapper
   `0xa15354`, the hex converter `0x5dbd28`, the uuid helper
   `0x5d9f64/0x5da060`. Boolean polarity verified from the annotated
   `true`/`false` constants in `nzk.dart`.
4. **Pool-reference scans** (raw word decoding of the whole `.text`,
   0x2f0000-0xa70ff0): every `add PP,#page,lsl#12` + `ldr [x?,#off]` pair
   matching the key strings (writeCharacteristic ×7, setNotifyValue ×7,
   the nine UUID shorts, base-read frame, Duration 2s, PMa enum, CCCD,
   requestMtu ×5). Located every use site listed above.
5. **BL-target scan**: decoded every `bl` in `.text` targeting
   `0xa14ea8` / `0xa15354` / `0xa14220` / `0xa14d04` — the call graph of
   §2.4.

Artifacts for the Coder: the scanner scripts live at `/tmp/scan_words.py`
and `/tmp/disasm.py` on the gateway (reproduce with
`/tmp/blutter-venv/bin/python`).

---

## 5. Answers to the brief's seven questions

| # | Question | Answer | Status |
|---|---|---|---|
| 1 | Where does the app store the write characteristic? | No single hardcoded UUID. Two characteristic **objects** in the session state: `field_2b` (notify) and `field_2f` (write), filled by UUID classification at discovery (§1). | [P] |
| 2 | ff02 or ff03 for writing? | **ff03** on this unit (write bucket `{ff03, 2215, 2aa6}`); ff02 is the notify char (`{ff02, 2216, 2a00}`). The app never writes to a char `ff01`. | [P] |
| 3 | Write with or without response? | **Without response, always** — the only WriteType enum instance in the pool, used by the single write wrapper. Our implementation matches. | [P] |
| 4 | Activation/handshake before writes? | **None.** Sequence: classify → subscribe(ff02) → 2 s delay → write(ff03). The CCCD subscription is the only GATT write before the first command. (Confirms spec 006 §8 with code-level proof.) | [P] |
| 5 | Frame format of writes? | Plain hex string → bytes (even-length check, `int.parse` radix 16) → `writeCharacteristic`. `63 03 00 07 00 2D 3C 54` byte-identical to ours; no envelope, no length prefix, no header. | [P] |
| 6 | Does the app read start-7 via FC03, or another mechanism? | **Yes — FC03 write to the write char in every session**: the session continuation sends `"63030007002D3C54"` unconditionally after subscribe+2 s. The six block queries are separate, on-demand calls through the same sender. | [P] |
| 7 | MTU / connection configuration? | The app's session code **never calls requestMtu** (its 5 string refs are all inside the FBP glue that implements the API, `0xa10c04-0xa10cb4`; no session-region reference). No connectionPriority call exists anywhere (spec 006 §6). Connect parity (20 s timeout, retry) unchanged since spec 006. | [P] |

---

## 6. What this changes — and what it does not

### 6.1 Hypotheses killed [P]

- **H1 (write char is ff02 or another char): dead.** The app writes to
  the `{ff03, 2215, 2aa6}` bucket; on this GATT that is ff03.
- **H2 (write format/type differs): dead.** Without-response, raw
  Modbus bytes, same frame string constant, no MTU precondition.
- **H3 (activation/handshake needed): dead.** Subscribe → 2 s → write
  is the whole story; nothing else is sent before the base read.

### 6.2 Our implementation vs the app, field by field

| Aspect | App (proven) | Our integration (ce41aa5+) | Verdict |
|---|---|---|---|
| Notify char | ff02 (bucket A) | ff02 (`UUID_NOTIFY`, resolved first) | ✅ parity |
| Write char (runtime, this unit) | ff03 (bucket B) | ff03 (candidate/last-resort path — production log line `using write characteristic 0000ff03-…`) | ✅ parity in outcome |
| Write char (resolution logic) | **UUID bucket, property-blind** | ff01-first preference, then property-gated candidate scan | ⚠️ divergent logic (see FR-2) — can pick the wrong char on other GATT layouts, or refuse ff03 if it advertises no write property |
| Write type | withoutResponse, always | withoutResponse (property-gated to withResponse if ff03 advertised only "write") | ⚠️ see FR-3 |
| Frame | hex→bytes, `63030007002D3C54` | identical (CRC-pinned) | ✅ parity |
| Pre-write sequence | subscribe ff02 → 2 s → write | subscribe ff02 → 2 s settle → write | ✅ parity |
| Re-send cadence | no periodic re-send found in session code (the 2 s `Timer` at `0x5d5008` is an unrelated `<double>`-typed feature; "timerId" storage = the device schedule feature, not a poll) | 1 send + bounded re-send in the connection test | ✅ superset, harmless |

### 6.3 Where the silence must come from, now [A — ranking updated]

With H1/H2/H3 gone, the surviving causes from research 009 §7 are:

- **C3 push-only firmware / the app ALSO fails on this unit** (the
  premise "the app works here" was never an observation — research 009
  §2.4). The app is property-blind: it would write ff03 regardless of
  the char's advertised properties, and an Android-stack refusal would
  surface as the app's own "日志:指令发送失败" / `控制失败` toast.
  If ff03 lacks the write property on this unit, **the app cannot
  control this unit either**.
- **C5/E5-class delivery differences** (connection parameters, timing
  relative to the push stream, phone-stack quirks) — only visible in an
  ATT-level capture of a vendor session on THIS unit.

The two decisive, cheap checks: **(E4') grep the existing production
log** for the `GATT enumeration` and `using write characteristic
… (properties=…)` lines — ff03's advertised properties decide whether
the app could work here at all; **(E5) one HCI snoop of the vendor app
on this unit** — settles C3 vs C5 in one stroke (research 009 §11
decision matrix applies unchanged).

### 6.4 PROUVE / SUPPOSE ledger for this spec

**PROVEN (disassembly):** write bucket `{ff03, 2215, 2aa6}`; notify
bucket `{ff02, 2216, 2a00}`; service scan set `{ff01, 2214, 1800}`;
withoutResponse always; hex→bytes frame format; session order
subscribe → 2 s → write → listen; all 20 send call sites funnel through
one sender using the write bucket; no requestMtu / no handshake /
no periodic re-send in the session path; DEX `ff01` constant is not the
operating channel.

**SUPPOSED / TO-VERIFY (live):** that ff03 on **this unit** advertises a
write property (existing log grep, E4'); that the vendor app gets FC03
answers on **this unit** (E5); the exact push cadence onset (probe
Phase L, spec 009); whether writes land only in push-stream gaps (C5,
probe Phase P).

**NOT PROVEN (and now unnecessary for the write char):** the DEX
ff01-path's own call graph (dead end for the operating channel — noted
for a future DEX session only if E5 contradicts the Dart disassembly,
which would be extraordinary).