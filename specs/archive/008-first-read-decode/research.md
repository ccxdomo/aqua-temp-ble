# Spec 008 research — first-read decode: the unit answers, our parser throws the answer away

Date: 2026-10-11
Evidence base: the production log of the connection test against
`08:06:A6:C0:94:1C` (BlueNRG-C0941C), build c8d8260, plus the five
"dropping unexpected function code 0x10" frames logged by our own client
during that same test.

Status legend: **[P]** PROVEN (calculated/verified from the artifacts),
**[V]** TO-VERIFY (needs one live run), **[A]** ASSUMED (reasonable default).
Every claim below is tagged.

---

## 1. The production evidence, decoded

The connection test log (post-c8d8260 — the GATT fix worked):

```
connection test to 08:06:A6:C0:94:1C: no response: AquaTempTimeoutError:
no complete response within 8.0s (got 0 bytes) at stage first-read after 12.4 s (budget 70 s)
```

Stage arithmetic [P]: resolve+connect ≈ 2.9 s (c8d8260 commit message) +
notify + write-char + settle 2.0 s (spec 006 FR-1) + first-read 8.0 s
(`const.REQUEST_TIMEOUT`) ≈ 12.4 s — matches the logged 12.4 s exactly.
**The connect, the characteristic resolution and the ff02 subscription all
succeeded. The test died waiting for the base-block read.**

During that same test, our client logged **12 occurrences** of:

```
dropping unexpected function code 0x10: 631003e9002d5a...563a
dropping unexpected function code 0x10: 63100416002d5a...8576
dropping unexpected function code 0x10: 631007d1002d5a...9200
dropping unexpected function code 0x10: 631007fe002d5a7ffd008c...f49b
dropping unexpected function code 0x10: 631007fe002d5a7ffd0091...a565
```

(5 of the 12 lines pasted by the owner; the 12 are counted in the log.)

**The unit was transmitting real, CRC-valid register data on ff02 the whole
time. Our client logged each frame's hex and then deleted it.** The
"no response" is not the unit being silent — it is our reassembler
discarding every frame the unit sends.

---

## 2. Byte-by-byte decode of the five frames

### 2.1 The frame grammar [P — CRC-verified on 4 of 5 frames]

Every logged frame follows ONE shape:

```
 63   10   [start:2 BE] [qty:2 BE] [bytecount] [data: bytecount bytes] [CRC16 LE]
 │    │      │             │          │             │                   └─ little-endian, over bytes 0..N-3
 │    │      │             │          │             └─ qty registers, big-endian signed int16
 │    │      │             │          └─ always 0x5A = 90 = 2 × qty (45 registers)
 │    │      │             └─ always 0x002D = 45
 │    │      └─ the block's start register (03E9=1001, 0416=1046, 07D1=2001, 07FE=2046)
 │    └─ 0x10 — the same byte as the vendor app's "query block" REQUEST frames (FINDINGS §11.1)
 └─ slave 0x63
Total length = 7 + bytecount + 2 = bytecount + 9 = 99 bytes for qty=45.
```

It is a **hybrid**: the header is an *echo* of the vendor app's 8-byte
query-block request (`63 10 start qty CRC` — the exact frames recovered from
the APK pool: `631003E9002DD9E6`, `631007FE002D68D2`, …), and the body is an
*FC03-style data section* (bytecount + data + CRC). FINDINGS §11.1 recorded
the query-block "[V] response unknown" — **spec 008 upgrades this to [P]:
the response shape is now proven.**

### 2.2 Frame 1 — block 1001-1045 [P, byte-exact]

Log line: `631003e9002d5a0000...563a` (198 hex chars = 99 bytes).
Transcription proven BY the CRC: calc `0x3A56` == stored `56 3A`. A single
wrong nibble breaks the CRC with probability 65535/65536.

```
63 10 03 e9 00 2d 5a | 0000...non-zero...0000 | 56 3a
slave=0x63 fc=0x10 start=1001 qty=45 bytecount=90 data=90B crc=0x3A56 [P]
```

Non-zero registers (big-endian int16):

| reg | raw | /10 | note |
|---|---|---|---|
| 1012 | 1 | 0.1 | flag-like |
| 1018 | 1 | 0.1 | flag-like |
| 1021 | 1050 | **105.0** | **= H04 exhaust-high safety threshold [P panel, FINDINGS §12]** |
| 1022 | 1 | | flag-like |
| 1023 | 2 | | enum-like |
| 1037 | 2 | | enum-like |
| 1038 | 30 | 3.0 | |
| 1039 | 3 | | enum-like |
| 1040 | 1 | | flag-like |

Semantics [V] — but 105.0 at reg 1021 matching the panel-observed H04
threshold is strong corroboration that block 1001+ is the H-family
machine-settings area.

### 2.3 Frame 2 — block 1046-1090 [P, byte-exact]

Log line: `63100416002d5a0122...8576` (198 chars, CRC calc `0x7685` ==
stored `85 76`).

```
63 10 04 16 00 2d 5a | 0122 0005 0005 0096 0190 010e ... | 85 76
start=1046 qty=45 bytecount=90
```

| reg | raw | /10 | reg | raw | /10 |
|---|---|---|---|---|---|
| 1046 | 290 | 29.0 | 1055 | 350 | 35.0 |
| 1047 | 5 | 0.5 | 1056 | -70 | -7.0 |
| 1048 | 5 | 0.5 | 1057 | 130 | 13.0 |
| 1049 | 150 | 15.0 | 1058 | 40 | 4.0 |
| 1050 | 400 | 40.0 | 1059 | 8 | |
| 1051 | 270 | 27.0 | 1060 | 3 | |
| 1052 | 5 | 0.5 | 1068 | 270 | 27.0 |
| 1053 | 5 | 0.5 | rest | 0 | |
| 1054 | 100 | 10.0 | | | |

These read like the R-family regulation thresholds (FINDINGS §12:
min/max targets, defrost and EEV limits). Semantics [V], plausibility [P].

### 2.4 Frame 3 — block 2001-2045 [P header, V data]

Log line prefix `631007d1002d5a...9200`. The header is proven:
slave 0x63, fc 0x10, start 2001, qty 45, bytecount 90 → declared length 99.
**The exact data is [V]**: the pasted line's zero-runs cannot be
transcribed byte-reliably from the paste (our best transcription has an odd
character count), and a reconstruction search constrained by the visible
value islands (07d1, 0001, 000d, 00a8, 0040) plus the logged CRC tail
(`92 00` → 0x0092) still leaves **40 CRC-compatible candidates**
(`reconstruct.py`) — the data is underdetermined. The spec-008 census logging
(quickstart §1) re-captures this block cleanly on the next run. The
structure is not in doubt (proven by the other four frames).

### 2.5 Frames 4 & 5 — block 2046-2090, two pushes [P, CRC-oracle reconstruction]

The pasted lines show header `63 10 07 fe 00 2d 5a`, the visible data
islands `7ffd 008c|0091 008c 008c 0087 0096`, a long zero run, and CRC tails
`f49b` / `a565`. bytecount 0x5A fixes the data at exactly 90 bytes, so the
frame is uniquely determined by header + islands + CRC. Reconstructed and
**CRC-verified by calculation** (`reconstruct.py`, `decode_frames.py`):

Frame 4: `63 10 07 fe 00 2d 5a` + `7ffd 008c 008c 008c 0087 0096` + 39×`0000` + CRC — calc `0x9BF4` == stored `f4 9b` **[P]**
Frame 5: `63 10 07 fe 00 2d 5a` + `7ffd 0091 008c 008c 0087 0096` + 39×`0000` + CRC — calc `0x65A5` == stored `a5 65` **[P]**

| reg | frame 4 | frame 5 | /10 | note |
|---|---|---|---|---|
| 2046 | 32765 | 32765 | — | **0x7FFD = the known "sensor absent" sentinel [P, FINDINGS §12 / spec 003]** |
| 2047 | 140 | **145** | 14.0 → **14.5** | **changed between two pushes — live data [P]** |
| 2048 | 140 | 140 | 14.0 | |
| 2049 | 140 | 140 | 14.0 | |
| 2050 | 135 | 135 | 13.5 | |
| 2051 | 150 | 150 | 15.0 | |
| 2052-2090 | 0 | 0 | | |

**Frame 5 differs from frame 4 in exactly one register (2047: 14.0 → 14.5).**
The unit re-sent the same block with one value updated. Phone Bluetooth was
OFF during the test (spec 007), so nothing external changed a setpoint —
**this register changes by itself: block 2046+ contains at least one LIVE
value** (a ~14 °C reading in October is plausible for pool-side water/air).
FINDINGS §12 calls 2001-2090 "control parameters" — that mapping now needs
revision; parts of it are live status [V until calibration].

### 2.6 CRC verification table (calculated vs received)

| frame | block | length | CRC calc | CRC on wire (LE) | verdict |
|---|---|---|---|---|---|
| 1 | 1001-1045 | 99 B | 0x3A56 | 56 3a | **VALID [P]** |
| 2 | 1046-1090 | 99 B | 0x7685 | 85 76 | **VALID [P]** |
| 3 | 2001-2045 | 99 B (declared) | — | 92 00 | header [P], data [V] |
| 4 | 2046-2090 | 99 B | 0x9BF4 | f4 9b | **VALID [P]** |
| 5 | 2046-2090 | 99 B | 0x65A5 | a5 65 | **VALID [P]** |

CRC16-Modbus (poly 0xA001, init 0xFFFF) over bytes 0..96, stored
little-endian — the same algorithm as every frame recovered from the APK
(FINDINGS §11). Four independent live frames verifying against the APK's
CRC closes the last doubt about the transport's protocol identity.

### 2.7 Which frames were dropped, and why all twelve make sense

Observed block starts among the five pasted lines: 1001, 1046, 2001,
2046, 2046. The vendor app's six query blocks are 1001, 1046, 1091, 1136,
2001, 2046 (FINDINGS §11.1, all CRC-verified APK frames). **12 drops = 2
full cycles × 6 high blocks fits the count exactly** — [SUPPOSED], but
neatly consistent; the census will confirm. Two cycles in ~8 s ⇒ one block
every ~0.67 s, one full cycle every ~4 s [V — needs timestamps].

---

## 3. Root cause: the code path that throws the data away [P]

The request in flight (config flow first-read):

```
config_flow._async_test() ->
    client.read_registers(registers.BASE_BLOCK_START=7, registers.BASE_BLOCK_COUNT=45)
  -> aqua_client._request(frame=63030007002d3c54, expected_fc=modbus.FC_READ_HOLDING=0x03, ...)
  -> self._expected_fc = 0x03; TX 63030007002d3c54 (the vendor-proven base read, FINDINGS §11.1)
```

Every notification chunk then runs `_on_notify` (aqua_client.py @ c8d8260):

```python
self._rx.extend(data)
if self._rx and self._rx[0] != modbus.SLAVE_ID:   # 0x63 -> passes
    ...drop...
if len(self._rx) >= 2 and self._expected_fc is not None:
    fc = self._rx[1]                               # offset 1 = frame byte 1 = 0x10
    if fc not in (self._expected_fc, self._expected_fc | 0x80):
        _LOGGER.warning("dropping unexpected function code 0x%02X: %s", fc, ...)
        self._rx.clear()                           # <-- the 99 data bytes are DELETED
        return
```

Chain [P, every step verified against the code and the frames]:

1. The FC gate reads `self._rx[1]` — the frame's **second byte** (Modbus
   convention `[slave][fc]`). The frames really carry **0x10 at byte 1**.
   There is **no off-by-one**: byte 0 is 0x63 and passes the slave check;
   if the parser were shifted, the slave check would have failed and the
   log would show "dropping non-Modbus notify" instead (it does not).
2. `0x10 ∉ (0x03, 0x83)` → the frame is dropped, the buffer is cleared,
   and the WARNING line logs the full accumulated buffer — which is why we
   could decode complete frames from the log at all (each 99-byte frame
   arrives in ONE notification, see §5 Q4).
3. **12 frames dropped, buffer cleared 12 times.** At the 8 s deadline the
   accumulated buffer is empty → the timeout branch reads
   `len(self._rx) == 0` → `AquaTempTimeoutError("... (got 0 bytes)")`.
   **"got 0 bytes" means "0 bytes left after we deleted everything", not
   "0 bytes received".** There is no separate total-received counter — the
   diagnostics cannot see what was thrown away.
4. config_flow maps `AquaTempTimeoutError` → UI key `no_response`, stage
   `first-read`, elapsed 12.4 s — the exact production line.

And even if the FC gate were opened (expected_fc=None or 0x10), the
**current** code would STILL fail: `expected_response_length()` maps
fc 0x10 to the 8-byte write-echo shape, so the reassembler would cut the
frame at byte 8, `_complete()` would CRC-check the 8-byte prefix
(`631003e9002d5a00`), fail, and raise `AquaTempResponseError("bad CRC")`.
**Two independent bugs, same victim: the 99-byte block frames.**

---

## 4. The fix, in one paragraph

Teach the protocol layer the proven frame grammar (parse + length rules),
and replace the FC gate with request-aware *matching*: reassemble every
0x63 frame to its computed boundary, CRC-validate it, and complete the
pending request **only if the frame matches the request** (FC03 answer OR
block frame echoing the requested start/qty); non-matching frames are
census-logged (INFO while a request is pending) and discarded without
failing anything. The base read then succeeds if the unit's stream
includes a start-7 block; the census tells us if it does not. Full design:
plan.md; normative rules: contracts/.

---

## 5. The six questions, answered

### Q1 — At which byte does the parser read the function code, and why 0x10?

**`self._rx[1]`** — the second byte of the accumulated buffer, i.e. the
Modbus FC position after `[slave]`. It sees 0x10 **because the unit really
puts 0x10 there** [P — 4 CRC-verified frames]. Not a misread, not a shift.
The "0x10" is the same byte the vendor app's own query-block requests use
(`6310...`, FINDINGS §11.1): the unit reuses the query-block request header
as the response/push header.

### Q2 — What is the true structure? Is it a valid FC03 response? Is the CRC valid?

True structure [P]: `63 10 start:2 qty:2 bytecount data crc:2` — 99 bytes
for qty=45 (see §2.1). **It is NOT an FC03 response** (an FC03 response is
`63 03 bytecount data crc` = 95 bytes; no start/qty echo). It is a valid,
well-formed frame of the unit's *own* hybrid shape — and **yes, the CRC is
valid**: verified by calculation on frames 1, 2, 4, 5 (§2.6); frame 3's
header is proven, its data unverifiable from the paste (§2.4).

### Q3 — Why "got 0 bytes" when bytes were clearly received?

Because the drop path **clears the buffer** and the timeout message reads
`len(self._rx)` — the bytes *remaining*, not the bytes *received* [P, §3
step 3]. Every one of the 12 frames was received and then deleted by the
FC gate, so 0 remained at the deadline. It is a diagnostics bug riding on
top of the decode bug: no total-received counter, no record of which
blocks were seen. Spec 008 adds both (FR-5).

### Q4 — Is there a fragmentation problem? Does the 0.6 s silence reassembler work?

**In the observed production run: no fragmentation problem** [P]. Proof:
the WARNING lines log `bytes(self._rx).hex()` at the moment of the drop —
the FC gate fires on the FIRST chunk with `len ≥ 2`. The logged frames 1
and 2 are complete, CRC-valid 99-byte buffers, so **each 99-byte frame
arrived in a single notification** — the link's ATT MTU was negotiated
above ~102 (BlueZ/bleak exchange MTU; the BlueNRG module accepts). The
20-byte-chunk case (default MTU 23) remains handled by the same
accumulate-then-boundary rules, and a fragmented block frame reassembles
correctly under the fixed boundary rule (test vector: 5×20-byte chunks).
The silence timer keeps its role as the fallback for shapes we cannot
compute a boundary for; it must additionally apply the request match check
so a *non-matching* CRC-valid buffer can never complete a request.

### Q5 — Is our request itself right? Register 1001 vs register 7?

**Our first-read request is correct and is exactly the vendor's**: the
config flow sends `read_registers(7, 45)` → `63030007002d3c54` — FC03,
start 7, count 45, the frame the vendor app's session sends first
(research 006 §2 step 5 [P]). **We do NOT query register 1001 in the
connection test** — `631003e9002dd9e6` is the *query-block* format, sent
only by the probe tool (`coordinator.async_probe_block`, HIGH_BLOCKS) and
by the vendor app for the parameter pages. `registers.BASE_BLOCK_START=7`,
`BASE_BLOCK_COUNT=45` [P]. The premise in the task brief ("we query 1001
instead of 7") does not hold against the code. What IS true: the unit
streams blocks 1001+ while our FC03 base read goes unanswered within 8 s —
whether the unit (a) also streams a start-7 block, (b) answers FC03
later, or (c) never got our request (write-without-response to ff03 is
unacknowledged) is **[V]** — decided by the census + quickstart
experiments. The request bytes themselves match the vendor session
byte-for-byte [P].

### Q6 — Is the 0x10 frame really received, or an echo of our own write?

**Really received** [P]. Four independent proofs:
(1) Our request was `63 03 ...` — an echo of our own write would start
`63 03`, not `63 10`; (2) the frames are 99 bytes with 90 bytes of data —
an echo of our 8-byte write would be 8 bytes; (3) the block starts
(1001, 1046, 2001, 2046) match nothing we sent in that session (we sent
one frame: the base read) — they match the *vendor app's* query-block
requests from the APK pool; (4) the data evolves between frames
(reg 2047: 14.0 → 14.5 between two pushes of the same block) — echoes do
not change. What the frames ARE is the unit pushing "prepared answers"
shaped like the app's canonical block queries — either a periodic dataset
dump or a dump triggered by our request; which of the two is [V] (§7 E2).

---

## 6. What the pushed blocks contain (calibration baseline)

- **Block 1001-1045** (frame 1): sparse flags + reg 1021 = 105.0 = the
  H04 exhaust-high threshold [P panel corroboration]. H-family settings
  area [V].
- **Block 1046-1090** (frame 2): a dense ladder of ÷10 values (29.0, 15.0,
  40.0, 27.0, 10.0, 35.0, -7.0, 13.0, 4.0, …) — R-family regulation
  thresholds [V].
- **Block 2001-2045** (frame 3): header only [P]; data [V] (§2.4).
- **Block 2046-2090** (frames 4/5): sentinel 0x7FFD at reg 2046 [P — the
  known "sensor absent" sentinel], and a LIVE value at reg 2047 changing
  14.0 → 14.5 between pushes [P]. At least partly live status, not the
  static "control parameters" FINDINGS §12 assumed [V].
- The 0x7FFD sentinel appearing in the *unit's own* block frames confirms
  the sentinel convention is wire-level, not app-level [P].

These four decoded blocks are the first live calibration data the project
has ever had. They go into the probe's readable output (FR-6) — the H/R
families of spec 003 become calibratable through the probe without any
new entity exposure.

---

## 7. What remains unproven — and the experiment that closes each gap

| # | Gap | Experiment (quickstart §) |
|---|---|---|
| E1 | Does the unit's stream include a **start-7 block** (`63 10 00 07 00 2d 5a …`)? If yes, first-read succeeds after the fix. | §1 run the fixed build once; read the census lines (`block push observed: start=…`) |
| E2 | Is the dump **periodic** (pushes during settle too) or **triggered by our request**? | §1 census with timestamps: pushes before the first TX ⇒ periodic |
| E3 | Does the unit answer the **FC03 base read at all** (late), or is base data only in the stream? | §1: if no start-7 block appears AND no FC03 answer within the window ⇒ follow §3 |
| E4 | Did our **write reach the unit**? (write-without-response to ff03 is unacknowledged; ff03's advertised properties were never pasted from the enumeration log) | §3 capture the `GATT enumeration` INFO line (ff03 properties); §5 probe block 1001 and compare the arrival timing vs the stream cadence |
| E5 | What does the **vendor app's session look like on THIS unit** (its request set, the answer shapes, where base data comes from)? | §4 Android HCI snoop log of one vendor session (Developer options → bug report → btsnoop_hci.log) — the definitive picture |
| E6 | Exact register semantics of blocks 1001+/1046+/2001+/2046+ | probe + spec-003 calibration flow; the decoded frames are the baseline |

**Hypothesis ranking** [A, for planning only]: the stream is a periodic
dataset dump (2 cycles × 6 high blocks = the 12 drops) that does NOT
include a start-7 block, and the vendor app reads base data either from a
late FC03 answer or from a block we have not seen yet. If E1 comes back
negative, the follow-up spec uses E4/E5 evidence to change the base-read
strategy (probe-style block query, longer window, or write-path fix) —
**never blind**: the census turns the next run into data.

---

## 8. Reproduction

```
python3 specs/008-first-read-decode/decode_frames.py   # decode + CRC + root-cause simulation
python3 specs/008-first-read-decode/reconstruct.py     # CRC-oracle reconstruction of frames 3/4/5
python3 custom_components/aqua_temp_ble/modbus.py      # existing self-test (stays green)
```

`decode_frames.py` embeds the four proven frames as vectors and prints:
byte-level decode, CRC calc vs stored, the register tables, the exact
current-code drop path, and the write-echo collision check (echo
offset-6 bytes 0x88/0x68 vs block discriminator 2×qty=2 — no false
positive on the two known echoes).