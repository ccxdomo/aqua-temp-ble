# Spec 016 research — the app's CONTROL path: the on/off frame, recovered from the disassembly

Date: 2026-10-12
Evidence base: **raw disassembly of the vendor app itself** —
`libapp.so` (Dart AOT, sha256 `b0ae8c66…`) via capstone
(`/tmp/blutter-venv/bin/python /tmp/disasm.py <start> <end>`) and the
blutter artifacts (`pp.txt` 28 951 pool objects, `asm/` tree — both
still present on the gateway at `/tmp/blutter_out/`; the raw `pp.txt`
is also in-repo at `blutter/pp.txt`); the whole-`.text` BL-target scan
(`/tmp/scan_bl.py` — every call site of every write function, no gaps);
the pool-reference scan (`/tmp/scan_pool.py` — the page-context
strings); FINDINGS.md; specs 008/009/010/011/012/013/014/015 (every
inherited claim stays tagged there); the shipped code @ 0.11.0
(`registers.py` with the corrected 2011/2019 pinning, `aqua_client.py`,
`coordinator.py`, `climate.py`, `modbus.py`, `const.py`).

Status legend (the repo convention): **[P]** PROVEN (read in the
disassembly, addresses given; or CRC-computed; or established in the
cited spec), **[P apk]** recovered from the APK, **[P owner]** the
owner's observation, **[V]** TO-VERIFY (one named experiment decides),
**[A]** ASSUMED (reasonable, cheap to correct), **[D]** DECISION.

Every address below was disassembled with capstone from
`/tmp/flutterapp/lib/arm64-v8a/libapp.so` and cross-checked against
blutter's `asm/` class listings. Reproduce any region:

```bash
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x<start> 0x<end> flags
```

---

## 0. TL;DR — the mystery solved, black on white

> **The vendor app turns the heat pump ON and OFF by writing register
> 1011 with a single 16-bit value: `1` = ON, `0` = OFF.**
>
> The frame — recovered from the app's own home-screen power-slide
> handler, CRC-computed with the app's own pipeline:
>
> | Action | Frame (hex) | Bytes |
> |---|---|---|
> | **POWER ON** | `631003f30001020001f131` | 11 |
> | **POWER OFF** | `631003f3000102000030f1` | 11 |
>
> Wire decode (FC16 write, the app's command grammar):
> `63 10 03F3 0001 02 0001 F131` = slave 0x63, FC 0x10, start
> register **0x03F3 = 1011**, quantity 1, bytecount 2, value
> `0001` (ON), CRC16-Modbus `F131` (little-endian on the wire).
> OFF is the same frame with value `0000` and CRC `F130` → wire
> `30 F1`.
>
> **There is NO activation sequence.** The power handler checks two
> LOCAL state fields (master/slave switch, remote on/off switch — both
> decoded from the report pushes), toggles a boolean, builds the frame
> and sends it. Nothing else goes on the wire. The time-sync frame
> (`631004f30004080001…`) is a SETTINGS-page feature built next to the
> cloud passthrough-instruction code, NOT a pre-command unlock.
>
> **The write plane and the read plane are different registers.** The
> app writes power to **1011**; the pushed report mirrors the power
> STATE at **2011** (the diff-revert-pinned read home, registers.py).
> A 1000 offset holds for power only — the mode is written at 1012 but
> read at 2019 (spec 015) — so no constant-offset rule may be assumed.
>
> Consequence for the integration: the missing write frame is NO
> LONGER the blocker. What remains is one live proof: send the frame
> from our side and watch register 2011 follow it (quickstart §1). The
> transport is already app-parity (spec 010); our frames demonstrably
> reach the firmware (the report bursts follow our TXs — spec 014 M-4);
> the acknowledged-write probe proved there is no GATT-level gate
> (att-accepted, no error — spec 011/012). The guard is NOT extended
> until that proof exists (contract 016 §3).

---

## 1. How the frame was found — the complete write-call graph [P]

Spec 010 §2.4 proved every outgoing frame of the app funnels through
ONE sender (`0xa14ea8`) and listed its 20 call sites. This spec closed
the graph downward to the actual frame bytes:

### 1.1 The frame builder `0x64fc0c` [P — disassembled in full]

Every write frame is assembled by ONE function at `0x64fc0c`,
signature `(x1=session-obj, x2=address, x3=qty, x5=bytecount-string,
x6=value[, optional-stack-arg])`:

```
addr4    = value.toRadixString(16).padLeft(4, "0")     ← the x2 param
qty4     = value.toRadixString(16).padLeft(4, "0")     ← the x3 param
value4   = hexPad4(x6)                                 ← 0x64fed8: (& 0xFFFF)
                                                        → toRadixString(16)
                                                        → padLeft(4, "0")
optional = hexPad4(int.parse(stack-arg, 16))           ← present only when
                                                        the caller pushed one
frame    = "6310" + addr4 + qty4 + bytecount + value4 [+ optional] + crc4
```

- The pool constants: `"6310"` [pp+0x20b50], bytecount `"02"`
  [pp+0x20b68], bytecount `"04"` [pp+0x20a98], padding `"0"`
  [pp+0x4598], padLeft descriptors [pp+0xcb0] / [pp+0x1328] /
  [pp+0x20aa0] [P apk].
- The hex→bytes converter `0x5dbd28` (spec 010 §2.2) then the CRC
  `0x5def40` — **init `0xFFFF`** (`mov x3, #0xffff` at 0x5def80),
  the CRC16-Modbus init; the seven pool frames were CRC16-Modbus
  verified through this same pipeline in FINDINGS §11 [P].
- The returned hex string goes to the sender `0xa14ea8` →
  `writeCharacteristic(withoutResponse)` on the **ff03** bucket
  (spec 010 §2.2/§2.3 — unchanged by this spec).

**Frame shapes the builder produces** (both observed in callers):

| Shape | Bytes | Used for |
|---|---|---|
| `6310 <addr4> 0001 02 <val4> <crc4>` | 11 | single-register writes (power, mute, setpoint) |
| `6310 <addr4> 0002 04 <v1> <v2> <crc4>` | 13 | two-register writes (mode, timer: both values on the wire) |
| `6310 <addr4> 0002 02 <val4> <crc4>` | 11 | the nrk control-parameters page writes (qty says 2, bytecount says one 16-bit value — see §4.5) |

### 1.2 Every caller of the builder — the BL-target scan [P]

A raw word-level scan of the whole `.text` for `bl 0x64fc0c` returns
**12 call sites**; for the wrapper `0x65011c` **5 call sites**; the
sender's 20 sites are spec 010 §2.4's list (re-verified). No write path
exists outside this graph [P].

| # | Call site | Containing function | Page (identified by its pool strings) | What it writes |
|---|---|---|---|---|
| 1 | 0x64fba0 | 0x64fa08 (closure) | generic parameter-object write (`addr = obj.field_7`, `value = obj.field_f`) | runtime [V] |
| 2 | 0x6501e4 | 0x65011c (the wrapper) | — (callee, not a page) | — |
| 3-8 | 0x6525f8 / 0x652820 / 0x653164 / 0x653390 / 0x653be4 / 0x653e10 | the (dynamic,int,int)+(dynamic,bool) closure pairs of `[mrk] _nR` | **the TIMER page** — 蓝牙定时/静音定时/关机定时 (BLE timer / mute timer / power-off timer), `%02d:00~%02d:00` formats, "The timing start and end time cannot be set to the same time" | runtime addresses [V] |
| 9 | **0x6a1498** | `[frk] _dR._Jwd` 0x6a1384 | **home screen — MUTE** (静音, "The mute function is not available") | **reg 1076**, qty 1, "02", value = toggled bool [P] |
| 10 | **0x6a1754** | `[frk] _dR._Iwd` 0x6a15c8 | **home screen — POWER** (see §2) | **reg 1011**, qty 1, "02", value = toggled bool [P] |
| 11 | **0x6a2138** | `[frk] _dR` closure 0x6a1f54 | **home screen — SETPOINT** (提高/降低/目标温度 = raise/lower/target temperature; "The target temperature cannot be higher/lower…") | **runtime address** [V], qty 1, "02", value = temp×10 [P] |
| 12 | **0x6a2568** | `[frk] _dR` closure 0x6a2318 | **home screen — MODE** (制冷/制热/自动/模式 = cool/heat/auto/mode; "The mode cannot be set under the current model", "In slave mode, the mode is uncontrollable") | **regs 1012-1013**, qty 2, "04", values = [state.field_f, temp×10] [P shape / A semantics] |
| — | 0xa341c0 / 0xa3450c / 0xa34704 / 0xa348d0 (→ wrapper 0x65011c) | `[nrk]` region | **the control-parameters page** (蓝牙控制参数, the "66" debug page — spec 015 §5.3; the `参数选择修改 address = …` log oracle) | runtime addresses [V], qty 2, "02", value ×10 [P shape] |
| — | 0x650104 (→ wrapper) | 0x6500d4 (a closure adapter passing 4 stack args) | parameter write adapter | runtime [V] |
| — | 0x68fe60-0x6900b0 region (NOT a builder caller — the literal is loaded directly) | settings-page code | **TIME-SYNC**: builds `631004f30004080001` + padLeft(2,"0") clock bytes + CRC ("拼接后的数据" = "concatenated data" log at [pp+0x18658]) | reg **1267**, qty 4, bytecount 08, data `0001` + 6 clock bytes [P] |

The `[frk] _dR` class (id 4553, `asm/frk.dart`) is the **home-screen
BLE device page**: its pool cluster carries 开机/关机 (power on/off),
滑动开机/滑动关机-adjacent handlers, the fault view (查看故障,
faultListBLE), the timer widget (timerSetBle, 设备定时), the mode and
setpoint guards — and the field `late final bS _Awd` (the BLE session
manager, spec 010's class bS) [P apk].

### 1.3 The two home-screen boolean writes at a glance [P]

Site 9 (mute, `_Jwd`) and site 10 (power, `_Iwd`) are structurally
identical toggles:

```
_Jwd (mute), 0x6a1458-0x64a1498:
  x2 = state.field_0x3b ; x6 = (x2 == 0) ? 1 : 0 ; state.field_0x3b = x6
  builder(x2 = 0x434 /*1076*/, x3 = 1, x5 = "02", x6 = x6)

_Iwd (power), 0x6a1714-0x6a1758:
  x2 = state.field_7    ; x6 = (x2 == 0) ? 1 : 0 ; state.field_7    = x6
  builder(x2 = 0x3f3 /*1011*/, x3 = 1, x5 = "02", x6 = x6)
```

Both write the **NEW** boolean state (1 when turning on, 0 when turning
off) to a **hardcoded** register — the only two hardcoded single-bit
writes in the whole binary [P].

---

## 2. THE POWER HANDLER — closure `_Iwd` at 0x6a15c8, annotated [P]

The full control flow of the home-screen power slide, disassembled:

```
0x6a15d0-0x6a162c:  context = frame[0x10]; state = context.field_0x17
0x6a1630:           x2 = state.field_0x9b        ← GUARD 1 input
0x6a1634:           cbnz x2 → skip
0x6a1638-0x6a1670:  (x2 == 0) → dialog "Master/ Slave switch close,
                     unit under Slave mode" [pp+0x18920] — ABORT, no write
0x6a1688:           x0 = state.field_0x8b        ← GUARD 2 input
0x6a168c:           cmp x0, #1
0x6a1690:           b.ne 0x6a16e4                ← (x0 != 1) → continue
0x6a1694-0x6a16e0:  (x0 == 1) → dialog "Remote on/ off switch open"
                     [pp+0x188f8] — ABORT, no write
0x6a16e4-0x6a1728:  x2 = state.field_7          ← the current power bool
                     x6 = (x2 == 0) ? 1 : 0      ← the NEW state
                     state.field_7 = x6          ← optimistic local update
0x6a172c-0x6a1740:  type checks on context.field_0x13 (the session)
0x6a1740:           mov x2, #0x3f3              ← ADDRESS 1011
0x6a1744:           mov x3, #1                  ← QTY 1
0x6a1748-0x64a174c: x5 = [pp+0x20b68] = "02"     ← BYTECOUNT
0x6a1754:           bl 0x64fc0c                  ← build the frame
0x6a1758-0x6a1774:  sender(session, frame)       ← bl 0xa14ea8 → ff03
0x6a17dc-0x6a17fc:  toast strings "蓝牙泳池机-" / "开机" / "关机"
```

Three decisive facts:

1. **The guards read LOCAL state, not the wire.** `field_0x9b`
   (master/slave switch) and `field_0x8b` (remote on/off switch — the
   RM-decoded quantity `vDg` "Remote on/ off switch open", FINDINGS
   §9) are fields of the home-screen state object, populated from the
   decoded report data. **No protocol frame is exchanged before the
   write.** The i18n twins of the guard messages
   ("应急开关断开，开关机不可控" [pp+0x46168], "从机状态下，开关机不可控"
   [pp+0x45ef0]) are loaded by the slide-button UI code at
   0x7c8500/0x7c62a0 — the same two guards, UI side [P].
2. **The write value is the NEW state** (1 when the current state is
   off → turning ON; 0 when on → turning OFF) [P].
3. **The frame is assembled and sent in the same breath** — no queue,
   no retry, no session refresh between the toggle and the send [P].

### 2.1 The CRCs [P — computed, verified against the pipeline]

CRC16-Modbus (the FINDINGS §11 reference implementation, sanity-checked
against the pool's own `63030007002d` → `0x543C`):

| Frame body | CRC | Wire frame | Len |
|---|---|---|---|
| `631003f30001020001` | `0x31F1` | **`631003f30001020001f131`** | 11 |
| `631003f30001020000` | `0xF130` | **`631003f3000102000030f1`** | 11 |
| `631004340001 020001` (mute ON) | `0x4697` | `6310043400010200019746` | 11 |
| `631004340001 020000` (mute OFF) | `0x8656` | `6310043400010200005686` | 11 |

---

## 3. The write plane vs the read plane [P — with the one-offset caveat]

| Quantity | WRITE register (this spec, disassembly) | READ home (spec 015, diff-revert) | Offset |
|---|---|---|---|
| Power | **1011** (hardcoded in _Iwd) | **2011** (registers.py, corrected big-endian) | 1000 |
| Mode | **1012** (+1013) (hardcoded in the mode closure) | **2019** (registers.py) | 1007 — NOT 1000 |
| Mute | **1076** (hardcoded in _Jwd) | none identified | — |
| Setpoint | runtime address [V] | none identified (the ladder five are candidates) | — |

The 1000 offset holds for power and FAILS for mode — therefore **no
constant-offset rule may be assumed between the planes** [P]. The
firmware simply serves different registers for commands (1001-series)
and for the report (1001/1046/2001/2046 blocks). Consequences:

- The pushed 2001-block's 2011 is the STATE mirror — the proof oracle
  for any power write [D — the quickstart §1 protocol].
- The pushed 1001-block CONTAINS the write register 1011 (block window
  1001-1045): in every capture to date 1011 read `0000` (the unit was
  off — consistent with both "command echo" and "state mirror"
  semantics; which of the two 1011 reflects is [V] — the proof
  protocol watches BOTH registers).
- **Register 2011 must NEVER be written on the strength of the read
  pinning.** A read-diff proves what a register REFLECTS, never what
  writing it CONTROLS (contract 015-A §8, restated for the write plane
  by contract 016 §2) [D].

---

## 4. The brief's seven questions — verdicts

### Q1 — WHICH FRAME does the app send to power on/off?

**`63 10 03F3 0001 02 0001 F131` (ON) / `63 10 03F3 0001 02 0000 F130→wire 30 F1` (OFF)** — §2/§2.1. Register **1011**, value **1 = ON, 0 = OFF**, qty 1, bytecount 0x02, CRC16-Modbus little-endian. **[P — disassembly + CRC arithmetic; the ONLY hardcoded power write in the binary]**

### Q2 — Is there an ACTIVATION sequence before commands?

**NO — [P].** The complete pre-write wire traffic of the app's
session: classify services → subscribe ff02 (a CCCD write) → 2 s →
base read `63030007002D3C54` (spec 010 §2). The power handler then
reads two LOCAL fields (§2) and sends the command. No unlock, no
token, no handshake, no MTU request, no prepare-write. The time-sync
frame `631004f30004080001…` is built by the SETTINGS page (泳池机设置,
0x68fe60-0x6900b0, next to `app/device/sendDevicePassthroughInstruction`
[pp+0x18648] — the cloud passthrough API) — a separate feature for
WiFi/DTU units, **not** a pre-command frame on the BLE operating path
(no sender call site exists in that region — the BL scan §1.2 is
exhaustive) [P].

### Q3 — Do command writes go through ff03?

**YES — [P, spec 010, re-confirmed].** All 12 builder sites and all 20
sender sites funnel through `0xa14ea8`, which requires the write
bucket (`field_2f` = {ff03, 2215, 2aa6}) and writes
`WriteType.withoutResponse` — the ONLY write-type enum instance in
the pool. The power frame is sent by the SAME sender as everything
else. Our integration's transport is byte-parity with this path
(spec 010 §6.2).

### Q4 — Why `att-accepted-silent`? (hypotheses, ranked by probability)

First, the term splits into TWO different phenomena that spec 014
already separated:

**(a) The base-read silence (our FC03 `63030007002d3c54` gets no FC03
answer) — SOLVED, not a write problem at all:**

| # | Statement | Status |
|---|---|---|
| 1 | The unit answers EVERY valid request with a report burst (blocks 1001/1046/2001/2046), never an FC03 response — the base block is served by no grammar on this firmware | [P — spec 014 M-4/M-7, six runs] |
| 2 | Our writes DO reach the firmware: the bursts follow our TXs (both write-command and acknowledged-write triggers) | [P — spec 014 M-2/M-4] |
| 3 | The GATT layer accepts our acknowledged write with NO error (no 0x05/0x0F/0x03/0x01) — no GATT-level security/permission/handle gate on ff03 | [P — spec 011/012, the X1 verdict `att-accepted-silent` re-read as "ATT-accepted" + spec 014's M-4] |
| 4 | Therefore "silence" = the firmware's answer STYLE (report pushes instead of FC03 responses), never a delivery failure | [P — the only model consistent with 1-3] |

Killed by the above: H-BOND as the cause of the base-read silence
(the plaintext link demonstrably delivers frames the firmware
processes); H-STALE-CACHE, H-LOCAL-DROP, H-MTU, H-CONDPARAM (spec 011
§11 ranks 4-6 — unchanged, dead).

**(b) The control-write question THIS spec opens: will the unit execute
OUR `6310 03F3 …`? Ranked:**

| Rank | Hypothesis | Fits the evidence | Killer |
|---|---|---|---|
| 1 | **H-EXECUTES**: the firmware executes any valid FC16 command from any connected central — nothing in the protocol distinguishes the app's writes from ours (same char, same write type, same session shape, no token, byte-identical frame) | ✅ all: the app works [P owner]; our transport is app-parity [P]; our frames reach the firmware [P]; no GATT gate [P] | the live proof (quickstart §1) — expected to PASS |
| 2 | **H-APP-BOND-GATE** (residual): the firmware gates EXECUTION (not report-pushing) on an encrypted/bonded link; the phone is bonded, the Pi is not | ⚠️ possible but unevidenced: report-pushing and ATT acceptance demonstrably work on the plaintext link — a command-only bond check would be unusual; NOT dead (we have never sent a command frame) | the live proof failing → the spec 011 pairing ladder (already implemented: `pair()`, rungs A/B/C) re-tests on a bonded link |
| 3 | H-WRONG-PLANE (we would write the wrong register) | ❌ DEAD — the frame is disassembly-proven, hardcoded in the app's own power handler | — |
| 4 | H-PREPARE-WRITE (long ATT writes needed) | ❌ DEAD — the app sends the same 11-byte frame as a plain Write Command; no prepare/execute exists anywhere (spec 010 §2.3) | — |
| 5 | H-FRAME-FORMAT (missing header/CRC/field) | ❌ DEAD — the frame is byte-complete: addr+qty+bytecount+value+CRC, built and CRC'd by the app's own pipeline (§1.1) | — |

**The one experiment that separates rank 1 from rank 2 is the proof
protocol (quickstart §1) — a single power ON→OFF cycle with the 2011
mirror as the oracle.** No further static analysis can decide it.

### Q5 — WHAT must be written for on/off?

**Register 1011, value 1 (ON) / 0 (OFF), frame shape `6310 <addr4> 0001 02 <val4> <crc4>` — §2.1's pinned frames.** NOT 2011 (the read mirror — §3). The mode register (1012-1013) is NOT needed for on/off; on this heat-only unit the app itself locks mode changes ("当前模式为单热模式，不可设置为其他模式！" [P apk]). [P frame / D scope]

### Q6 — The write guard: how to extend it SAFELY?

**Two-phase, proof-gated [D] — contract 016 is normative:**

- **Phase 0 (this spec's shipped state): NO guard change.** The climate
  stays read-only (spec 015 FR-8); `ensure_writable` keeps
  `{8, 13, 14}` (+19-21 behind the switch). The write-plane constants
  land as DATA (pinned frames, the address map) — never reachable by
  any write path.
- **Phase 1 (the proof instrument): a diagnostic power-write probe**
  that sends the pinned frame ON, harvests the report, checks the 2011
  mirror, sends OFF, checks the revert — and logs the verdict at
  WARNING. It is the ONLY code allowed to write 1011 before the proof,
  it is owner-triggered (service + diagnostic button), and it refuses
  to run unless the baseline mirror reads 0 (it will not toggle an
  already-running unit).
- **Phase 2 (only after the probe's verdict is "executed + reversible",
  flipped by the owner): extend `ensure_writable` with the
  address+VALUE allowlist `{1011: {0, 1}}` behind a
  `WRITE_CONTROL_PROVEN` switch, and wire the push-mode climate's
  `async_set_hvac_mode` to it.** The mode (1012-1013) and the setpoint
  (runtime address) are NOT extended — they need their own proofs
  (the mode's first value semantics and the setpoint's address are
  [A]/[V]).

### Q7 — HOW TO PROVE that a write acts? (no terminal)

**The diff-revert oracle, powered by the pinned mirror: quickstart
§1.** One button press runs the probe: baseline report → assert
2011 == 0 → write `…0001 f131` (ON) → harvest → 2011 == 1? → write
`…0000 30f1` (OFF) → harvest → 2011 == 0? → verdict at WARNING.
The owner cross-checks WITHOUT any terminal: the app's display (if
reopened — the single-connection rule requires the vendor app CLOSED
during the probe), the panel/audible pump start, and HA's own climate
(OFF → HEAT → OFF). If 2011 follows the write AND the revert — the
write is PROVEN to control, and phase 2 may proceed. If not — the
verdict line names the failure stage (att-error / not-executed /
not-reverted) and the spec 011 pairing ladder is the named next step.

---

## 5. The complete recovered write map — for the record

All write-capable surfaces of the app, with their frame shapes (the
BL scan §1.2 is exhaustive — there is no 13th builder call site):

| Surface | Address | Frame | Value semantics | Status |
|---|---|---|---|---|
| **Power (home)** | **1011** | `6310 03F3 0001 02 <0\|1> crc` | 1 = ON, 0 = OFF (the NEW state) | **[P]** |
| Mute (home) | 1076 | `6310 0434 0001 02 <0\|1> crc` | the NEW mute state | [P frame] / [V semantics] |
| Mode (home) | 1012-1013 | `6310 03F4 0002 04 <v1> <v2> crc` | v1 = a state field [A: the mode enum], v2 = temp×10 [P] | [P shape] / [A+V semantics] |
| Setpoint (home) | runtime (state field chain) | `6310 <addr> 0001 02 <temp×10> crc` | temp×10 | [P shape] / [V address] |
| Timer page ×6 | runtime (param objects) | `6310 <addr> 0002 04 <v1> <v2> crc` | pairs (e.g. start hour+minute) | [P shape] / [V addresses] |
| Control-params page ×4 (the "66" page) | runtime (param objects) | `6310 <addr> 0002 02 <val×10> crc` | ×10 values | [P shape] / [V addresses] |
| Time-sync (settings) | 1267 (literal prefix) | `6310 04F3 0004 08 0001 <clock:6B> crc` | wall clock | [P] |

Notes:

- The **timer/parameter pages write RUNTIME addresses** — built into
  parameter objects (`addr = obj.field_7`, `value = obj.field_f`,
  exactly FINDINGS §12.1's `param->field_1f[0]` shape). The app's own
  logcat oracle (FINDINGS §12.2, quickstart 015 §2: grep
  `参数选择修改` / `address =`) resolves them — one live session
  prints every address the vendor writes. **We do not need them for
  on/off.** [P mechanism / V values]
- The **home screen's three controls hardcode their addresses**
  (1011, 1076, 1012-1013) — the only hardcoded FC16 targets in the
  binary. That is why static analysis suffices for power and not for
  the parameter pages. [P]
- The nrk page's `qty=2 + bytecount 02` shape (§1.1's third row) is
  genuinely inconsistent Modbus (qty 2 should carry bytecount 4) — the
  app sends it anyway for the control-parameters page. The home
  screen's power write is clean (qty 1, bytecount 2 — consistent).
  Our probe frame is the home-screen shape. [P]

## 6. What the power handler teaches about the app's own READ model

The guards read `state.field_0x9b` (master/slave) and
`state.field_0x8b` (remote on/off switch) — decoded state, and the
mode handler's guards read slave-mode and heat-only locks. This
confirms the app builds its home-screen state from the REPORT data
(the pushed blocks and/or its block-query answers), not from an FC03
answer — reinforcing spec 015 Q6's world (b)/(c) reading. The app
NEVER asks for register 7's answer to control the unit: it sends the
base read as a session opener (a trigger, in spec 014's model) and
controls from the decoded report. [P — the guards are local reads;
A-strong — the state's upstream is the report]

## 7. The hypothesis ledger

| # | Statement | Status |
|---|---|---|
| W-1 | The app's power on/off write is `6310 03F3 0001 02 <0\|1> <crc>` — register 1011, value = the NEW state | **[P — disassembly + CRC]** |
| W-2 | No activation/handshake/unlock precedes the command; the pre-write wire traffic is subscribe → 2 s → base read | [P — spec 010 + this spec §2] |
| W-3 | The time-sync frame is a settings/cloud-path feature, not a pre-command frame | [P — region analysis + exhaustive BL scan] |
| W-4 | The write plane (1011) and the read mirror (2011) are different registers; 1000 offset proven for power ONLY | [P] |
| W-5 | The unit will execute our power frame (H-EXECUTES) | [V — the proof protocol decides; leading hypothesis] |
| W-6 | A firmware-level bond gate blocks command EXECUTION only (H-APP-BOND-GATE) | [V — residual, low rank] |
| W-7 | The mode write's first value is the classic mode enum | [A — 0/1/2 cool/heat/auto assumed from the base-block names; NOT proven] |
| W-8 | The setpoint's runtime address resolves to 1013 or base 14 | [V — the logcat oracle or a live setpoint-diff] |
| W-9 | Reg 1011 in the pushed 1001-block mirrors the power COMMAND (as opposed to the state) | [V — the proof protocol's ON capture answers it] |

## 8. What remains unproven — and the named experiment for each

| # | Gap | Experiment |
|---|---|---|
| U-1 | Does the unit execute OUR power frame? | **the proof protocol (quickstart §1)** — the 2011 mirror diff-revert |
| U-2 | The mode write's enum values and the mode write's necessity on a heat-only unit | logcat oracle (quickstart 015 §2) + a mode-diff; LOW priority — mode locked on this unit [P apk] |
| U-3 | The setpoint write's register | logcat oracle; or a setpoint ±0.5 °C diff against the pushed ladder |
| U-4 | Whether 1011 (in the pushed 1001-block) mirrors command or state | the proof protocol's ON capture (the 1001-block raw at 1011) |
| U-5 | The mute register 1076's read mirror | a mute-diff (LOW priority; not needed for climate control) |
| U-6 | The timer/parameter pages' runtime addresses | logcat oracle — out of scope for climate; recorded for completeness |

## 9. Reproduction

```bash
# every region quoted above:
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a15c8 0x6a1830   # the power handler
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x64fc0c 0x64ff30   # the frame builder + hexPad
python3 /tmp/scan_bl.py 0x64fc0c 0x65011c 0xa14ea8              # the exhaustive call graph
python3 /tmp/scan_pool.py 0x462a8 0x462b0 0x18920 0x188f8       # the page-context strings
```

Pool citations: `pp.txt` lines 25460-25464 (the builder strings
"日志:指令 6310"/"6310"/"02"/"04"), 77583/77645/77724/77764-77765
(the home-screen control i18n), 685 (the [pp+0x1328] descriptor),
30512 (the CRC32 table — a red herring: the wire CRC is the bitwise
CRC16 at 0x5def40, init 0xFFFF [P]).

## 10. Artifact availability note (per the brief)

- The blutter output and libapp.so/libflutter.so SURVIVED on the
  gateway under `/tmp/flutterapp/` and `/tmp/blutter_out/` (checked
  this session; `pp.txt` is also in-repo at `blutter/pp.txt`).
- The Windows node's `C:\reverse\` XAPK and the DEX were NOT needed
  for this result: the whole answer came from the Dart AOT
  disassembly (the operating channel — spec 010 §3 already
  established the DEX BLE stack is not the operating path). The DEX
  remains unexplored only as a completeness item for the WiFi-
  provisioning path, which the owner's unit does not use.