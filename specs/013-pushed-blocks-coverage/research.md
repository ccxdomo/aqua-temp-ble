# Spec 013 research — pushed-blocks coverage: the useful data is already in the pushes

Date: 2026-10-11
Evidence base: the production connection-test logs (builds c8d8260, ce41aa5,
0b16e0a, d860933 and the 0.8.0 run of 1ac276d — the `att-accepted-silent`
log pasted in this spec's brief); the four CRC-verified pushed block frames
pinned as vectors in `modbus.py` self-test §10 and re-decoded byte-by-byte
for this spec; the vendor-APK artifacts (FINDINGS.md, specs 006/010
disassembly); the panel observation (specs 003, owner's photographs of the
parameter view, 2026-10); the shipped code @ 1ac276d (`modbus.py`,
`aqua_client.py`, `registers.py`, `coordinator.py`, `config_flow.py`,
`const.py`).

Status legend: **[P]** PROVEN (calculated/verified from the artifacts),
**[V]** TO-VERIFY (one named experiment decides), **[A]** ASSUMED
(reasonable, cheap to correct), **[D]** DECISION. Every claim is tagged.

---

## 0. TL;DR — the answers, black on white

1. **YES — the inlet/outlet water temperatures are in the pushes, with
   high confidence.** The pushed block 2046-2090 contains a 6-register
   window (2046-2051) whose first member carries the `0x7FFD`
   sensor-absent sentinel — and this unit has **exactly one absent
   sensor: T01 Suction** (panel: 3276.5 °C display bug [P panel]) — and
   whose second member **changed live between two pushes**
   (2047: 14.0 → 14.5 °C [P]). Six consecutive registers, one sentinel
   at the T01 position, five plausible temperatures: this is the
   T01-T06 family of the app's parameter view, pushed [A-very-strong].
2. **The pushed blocks are the PARAMETER-VIEW dataset** (the T/H/R/P/S/O/D
   families the owner photographed behind debug password "66"), **not a
   mirror of the base block.** There is **no offset arithmetic**
   relating reg 7-51 to reg 1001+/2001+ [P — content disproves every
   mirror]. Reg 1021 is NOT reg 21: it is H04 "Exhaust Temp. High"
   105.0 °C [P panel-match], while reg 21 is `SetAutoTargetTemp`
   (a ~28 °C pool setpoint — a 105 °C pool is absurd).
3. **The base block's control data (power/mode/setpoint/fault/firmware)
   has NO identified home in the pushes** [P for the three decoded
   blocks; the fourth (2001-2045) is data-underdetermined [V] — the
   single most important capture gap left]. Control therefore stays
   blocked; **monitoring does not.**
4. The request path is dead for us but the **push path needs no
   request**: a pure-listening harvest (zero frames sent) reads
   everything the unit volunteers. Nothing the firmware's application
   layer can gate — because nothing is asked of it.
5. Consequence (spec 009 §9 verdict revisited with the new evidence):
   **Track B (harvest the pushes) is promoted to the primary read
   plane for this unit.** Track A (make the unit answer requests) keeps
   exactly its landed instrumentation (E5 HCI snoop; the optional
   one-shot `pair_on_connect` test) and loses nothing.

---

## 1. The production evidence this spec starts from

The 0.8.0 connection-test log (1ac276d, pasted in the brief):

```
connection test to 08:06:A6:C0:94:1C: build 0.8.0
security probe: re-sending base read as acknowledged write (response=True)
  on 0000ff03-... to 08:06:A6:C0:94:1C
security probe: verdict att-accepted-silent
  (ack ok, no Modbus answer within 8.0s;
   census: no matching response within 8.0s
   (received 14 frame(s) / 1386 bytes;
    blocks seen: 1001x3, 1046x2, 2001x5, 2046x4;
    first t+0.2s, last t+7.7s; none for start 7))
connection test ...: no response: AquaTempTimeoutError: no matching
  response within 8.0s (received 16 frame(s) / 1584 bytes;
  blocks seen: 1001x12, 1046x4; first t+0.2s, last t+7.7s; none for start 7)
  at stage first-read after 21.0 s (budget 70 s)
```

Facts, each independently pinned:

| # | Fact | Status |
|---|---|---|
| F-1 | The acknowledged Write Request was **accepted** (ATT_WRITE_RSP) — no ATT error, no `0x05`/`0x0F` security refusal. ff03 value writes are not GATT-security-gated for the unbonded Pi | [P — the verdict line; ATT semantics, research 011 §2] |
| F-2 | The Modbus engine answered **nothing** — 14 frames / 1386 bytes = 14 × 99 exactly: zero bytes for an FC03 answer, an exception frame, or garbage | [P — arithmetic, research 009 §0.2 method] |
| F-3 | Both windows censused **no start-7 block ever** — three production runs, five request sends (4 re-send + 1 acknowledged), zero answers, zero exceptions | [P] |
| F-4 | The pushes keep flowing at ~1.75-2 frames/s through everything we do | [P] |
| F-5 | The probe window saw **all four** starts (1001×3, 1046×2, 2001×5, 2046×4); the first-read window saw **only two** (1001×12, 1046×4) — the push mix **varies per window**; the "4-block cycle" model of research 009 §0.3 is dead as a *fixed* cycle | [P — the two census lines] |
| F-6 | 21.0 s elapsed = ~4.9 s connect + 8.0 + 8.0 — the att-accepted-silent fingerprint (research 012 §3 arithmetic) | [P] |

The verdict reading (inherited, established): the GATT server takes our
bytes; the firmware's application layer drops them (H-APP-GATE,
research 011 §11 rank 2; routing per contract 011 §6 row 3). Every
transport hypothesis (UUID, format, write mode, encryption, MTU,
whitelist) is dead. **The write passes; zero Modbus bytes come back.**

The question this spec answers: **does what we need already arrive
without being asked?**

---

## 2. What the unit pushes — the four blocks, decoded

Frame shape [P — CRC-verified on 4 live frames, research 008 §2.1;
pinned as self-test vectors in `modbus.py` §10]:
`63 10 start:2 qty:2 bytecount=0x5A data:90 crc:2LE` — 99 bytes,
45 big-endian signed int16 registers per frame.

All four blocks re-decoded register-by-register for this spec
(computation reproduced with the reference CRC; `parse_block_response`):

### 2.1 Block 1001-1045 — the H-family settings block [P frame]

Non-zero registers (9 of 45):

| reg | raw | /10 | reading |
|---|---|---|---|
| 1012 | 1 | — | flag, unknown [V] |
| 1018 | 1 | — | **H01 "Power down memory" = YES** [A] |
| 1019 | 0 | — | **H02 "Temperature unit" = C (0=C?)** [A] |
| 1020 | 0 | 0.0 | **H03 "Comp. stop" = 0.0 °C** [A] |
| **1021** | **1050** | **105.0** | **H04 "Exhaust Temp. High" = 105.0 °C [P — panel-match]** |
| 1022 | 1 | — | **H05 "Outlet Water Temp. Sensor" = ON** [A] |
| 1023 | 2 | — | **H06 "Mode type" = heat-only (enum 2)** [A] |
| 1037 | 2 | — | unknown [V] |
| 1038 | 30 | 3.0 | unknown [V] — P-family candidate (P02 Interval?) |
| 1039 | 3 | — | unknown [V] — P-family candidate (**P03 Duration = 3** fits [A]) |
| 1040 | 1 | — | unknown [V] — P-family candidate (P01 "Nor" = 1?) |

**The H-window hypothesis [A-strong]:** the six panel-observed H values
(H01 YES, H02 C, H03 0.0, H04 105.0, H05 ON, H06 heat-only — data-model
003 §4 [P panel]) fit the six consecutive registers 1018-1023 as
(1, 0, 0, 1050, 1, 2) — **6/6 members, anchored by the unique 105.0
match at H04**. Alternatives (1017-1022 etc.) break on the 1050 anchor.
Every member's *encoding* remains [A/V] (e.g. H02: 0=C is a guess);
the *window* and H04's identity are the load-bearing facts.

### 2.2 Block 1046-1090 — the R-family thresholds block [P frame]

Non-zero registers (16 of 45):

| reg | raw | /10 | | reg | raw | /10 |
|---|---|---|---|---|---|---|
| 1046 | 290 | 29.0 | | 1054 | 100 | 10.0 |
| 1047 | 5 | 0.5 | | 1055 | 350 | 35.0 |
| 1048 | 5 | 0.5 | | 1056 | -70 | **-7.0** |
| 1049 | 150 | 15.0 | | 1057 | 130 | 13.0 |
| 1050 | 400 | **40.0** | | 1058 | 40 | 4.0 |
| 1051 | 270 | 27.0 | | 1059 | 8 | — |
| 1052 | 5 | 0.5 | | 1060 | 3 | — |
| 1053 | 5 | 0.5 | | 1068 | 270 | 27.0 |

**The R-window hypothesis [A-strong]:** the panel observed 11 regulation
members in degrees, range **0.5-40.0 °C** (data-model 003 §5 [P panel]).
The dense ÷10 ladder at 1046-1060 (15 values: 29.0, 0.5, 0.5, 15.0,
40.0, 27.0, 0.5, 0.5, 10.0, 35.0, -7.0, 13.0, 4.0, 8, 3) sits exactly
in that range (the -7.0 is defrost/EEV territory — below the *observed*
panel range but the panel photographed only some members). This is the
regulation-thresholds dataset [A]. 1051/1068 = 27.0 twice — a
setpoint-shaped value [A-weak; see §6 Q6]. No live change observed
(single frame) — expected for installer settings.

### 2.3 Block 2001-2045 — pushed but DATA UNDERDETERMINED [V]

The header is proven (99 bytes declared); the pasted c8d8260-era hex was
underdetermined — 40 CRC-compatible reconstructions (research 008 §2.4).
Visible value islands: `0001`, `000d` (13), `00a8` (**168 = 16.8 °C**),
`0040` (64). A 16.8 °C value is a plausible live temperature [A]; the
block may hold the S/O/D/P families (the panel photographed S01-S03,
O01-O04, P01-P04 values — they live in SOME readable dataset [P panel]).
**No clean capture of this block exists anywhere in the project** —
the current census logs `start/qty/len/crc` only, never the data bytes
[P — code trace, `_census_frame` @ 1ac276d]. Closing this gap is FR-1
of this spec: one connection test, four WARNING lines, the block is
ours.

### 2.4 Block 2046-2090 — the T-family live-temperatures block [P frames ×2]

Non-zero registers (6 of 45 — everything else is zero):

| reg | push 1 | push 2 | /10 | reading |
|---|---|---|---|---|
| **2046** | **32765 (0x7FFD)** | 32765 | 3276.5 | **T01 Suction — ABSENT (sentinel) [P-grade anchor]** |
| **2047** | **140** | **145** | **14.0 → 14.5** | **LIVE value — changed between two pushes [P]** |
| 2048 | 140 | 140 | 14.0 | temp-shaped |
| 2049 | 140 | 140 | 14.0 | temp-shaped |
| 2050 | 135 | 135 | 13.5 | temp-shaped |
| 2051 | 150 | 150 | 15.0 | temp-shaped |

**The T-window hypothesis [A-very-strong]:**

- The **anchor**: reg 2046 carries `0x7FFD`, the firmware max-value
  sentinel the panel displayed as 3276.5 °C for **T01 Suction** — and
  T01 is **the only absent sensor on this unit** [P panel, data-model
  003 §3]. A register holding "sensor absent" in a pushed block must be
  a temperature register; the only absent one is T01.
- The **liveness**: reg 2047 changed 14.0 → 14.5 between two pushes of
  the same block [P] — live data, not settings (settings do not
  self-change; the phone was off, nothing external wrote).
- The **shape**: exactly 6 consecutive registers (2046-2051), one
  sentinel + five ÷10-plausible values in a tight 13.5-15.0 band —
  **exactly the T01-T06 family count** (Suction / Inlet / Outlet /
  Coil / Ambient / Exhaust [P panel]).
- The **coherence**: October evening, idle unit: inlet ≈ outlet (pump
  off, static water converges — 14.0/14.0), coil ≈ water (14.0),
  ambient 13.5, exhaust 15.0 (residual heat) — a physically coherent
  idle snapshot [A].
- The panel's earlier daytime snapshot (T02 17.0, T03 19.5, T04 19.5,
  T05 18.0, T06 17.5 [P panel]) and the pushed evening snapshot are
  different moments of the same six quantities — consistent under
  thermal drift [A].

If the window is in panel order (T01 at 2046 ⇒ T02 at 2047 … T06 at
2051):

**T02 Inlet water ≈ reg 2047 (live-proven). T03 Outlet water ≈ reg
2048.** The member-to-register assignment (which of 2047-2051 is inlet
vs outlet vs coil vs ambient vs exhaust) is [A] until ONE calibration
run pins it (quickstart §2): the app's displayed T-family read
side-by-side with a push capture. The window itself is anchored by
T01; a permuted member order is possible and is a one-line fix in the
mapping (repo discipline: registers.py, single source of truth).

### 2.5 What the pushes do NOT visibly contain [P]

Across the four decoded blocks (135 non-zero register-reads; 90
registers of block 2001-2045 unknown [V]):

- **no power flag** — nothing that looks like base reg 8 [V: the flags
  1012/1018/1022/1040 are static in the single 1001 frame observed]
- **no mode enum distinguishable from H06/P values** [V]
- **no user setpoint** — no 26-30 °C-pool-setpoint-shaped value stands
  out; the R-ladder holds 27.0/29.0 which are *candidates* [A-weak],
  not user-readable "the setpoint"
- **no fault code** — no base-reg-47-like home [V]
- **no firmware version group** [V]

The base block (power/mode/setpoint/19-25/fault/48-51) is a DIFFERENT
dataset from the pushes [P — content]. Verdict in §5 Q5.

---

## 3. The offset question — killed by content, not by arithmetic

The brief's structural observations are correct as arithmetic [P]:
`1001 − 7 = 994`, `2001 − 1001 = 1000`, `2046 − 1046 = 1000`,
`1046 − 1001 = 45`. But the structure they reveal is **block layout**,
not a mirror:

- The six query-block starts are two contiguous 90-register series:
  **1001-1180** (blocks at 1001, 1046=1001+45, 1091, 1136) and
  **2001-2090** (blocks at 2001, 2046=2001+45) — 45-register blocks,
  delta 45 between consecutive starts [P — APK frames, FINDINGS §11.1].
  The 1000 delta separates the two *dataset series* (machine settings
  vs live/parameter data), nothing more [A].
- **reg 1021 is NOT the same register as reg 21** [P]: reg 21 is
  `SetAutoTargetTemp` [P apk] — a pool setpoint (the app writes ~28-30 °C
  there); reg 1021 = 1050 = the H04 exhaust-high safety threshold
  [P panel-match]. A 105 °C auto-setpoint is absurd; the two registers
  are different physical quantities in different datasets.
- Block 1046-1090 under a −1000 mirror would map to base 46-90 — beyond
  the base block's end (51) [P — spec 009 §3.2].
- The live temps: base 26/27/28 (Inlet/Outlet/AT [P apk]) vs pushed
  2047/2048/2050 (T02/T03/T05 candidates): deltas **2021, 2021, 2022**
  — not constant [P]. No offset relates the two planes.

**Verdict: no constant offset; no mirror.** The pushed blocks are the
parameter-view dataset (T/H/R + unknowns), the base block is the
status/control dataset (power/mode/setpoint/fault/firmware). The app
carries decoders for BOTH: the RM class (~50 status fields matching the
base-block name list, FINDINGS §9/§12) and the VR.Yzd label→index map
(T01-T06, H01-H06, R01-R11, S01-S06, O01-O04, P01-P04, D01-D06 —
FINDINGS §11.4) [P apk]. The panel photographs are the VR.Yzd dataset
— i.e. **the high blocks** [A-strong]; the pushes deliver exactly those
blocks [P].

---

## 4. Q&A — the brief's six questions

### Q1 — Complete cartography of the pushed blocks; are T02/T03 identifiable?

§2 above is the cartography. Summary:

| Block | Content | Confidence |
|---|---|---|
| 1001-1045 | H-family machine settings (H04 anchor [P]; H01-H06 window 1018-1023 [A-strong]) + unresolved flags/enums 1012, 1037-1040 (P/D-family candidates [V]) | static settings |
| 1046-1090 | R-family regulation thresholds, ladder at 1046-1060 [A-strong] + 1068 | static settings |
| 2001-2045 | UNKNOWN — islands incl. 16.8 °C [V]; candidate home of S/O/D/P families [A] | unknown |
| 2046-2090 | **T-family live temperatures, window 2046-2051** (T01 sentinel anchor [P-grade]; 2047 live [P]) | **live data** |

**T02 (Inlet water) and T03 (Outlet water): YES, identifiable — in the
pushes, at regs 2047 and 2048 (window hypothesis), pending ONE
calibration run to pin the member order.** The inlet/outlet VALUES the
user sees in the app are T02/T03 — the same quantity the parameter view
displays [P panel]; whether the app's HOME screen reads them from the
base block (26/27) or from the pushes is E5's business (Q4), but the
data demonstrably transits the push stream [P — 2047 is live].

### Q2 — Constant offset base↔high? (reg 1021 ≡ reg 21?)

**NO [P].** §3: deltas are block layout; content disproves every mirror
(1050 ≠ a setpoint; 1046-1090 > base end; temp deltas 2021/2021/2022
non-constant). Reg 1021 = H04 threshold; reg 21 = SetAutoTargetTemp —
different registers, different datasets.

### Q3 — Does the app really read block 7?

**It SENDS it — every session, unconditionally [P]:** the session
continuation (disassembly 0xa14d04, call site 0xa14db0) writes
`63030007002D3C54` right after subscribe + 2 s, before any UI
interaction (spec 010 §2, research 010 §5 Q6). It is NOT a
pairing-only artifact. What is NOT proven:

- whether the unit ANSWERS it for the app on THIS unit [V — E5];
- whether the app NEEDS the answer (tolerates timeout and lives on
  pushes) [V — E5 / RM-decoder routing];
- what the home screen is fed by [V — E5].

Static analysis cannot decide the RM-decoder's input frame (dedup
hides the dispatch, FINDINGS §6). The honest statement: the app asks
for the base block every time; whether it ever receives it here is
unknown; **the app's parameter view demonstrably reads the high
blocks** [P panel — the T/H/R/P/S/O/D values were photographed], and
the unit volunteers those blocks without being asked [P].

### Q4 — Where do the live inlet/outlet temps the user sees come from?

Two worlds, both consistent with every artifact:

| World | Mechanism | Status |
|---|---|---|
| (a) request-fed | unit answers the phone's FC03 (but not the Pi's — H-APP-GATE keyed on central identity/bond); home screen from base 26/27 | [V — E5 decides] |
| (b) push-fed | unit answers NOBODY's FC03; the app's reassembler routes block frames into its decoders; home screen (or at least its temperatures) from pushes 2047/2048 | [V — E5] |

**Either way the integration wins**: the temps transit the pushes [P].
E5 (one HCI snoop of a vendor session on this unit — quickstart 009
§3, unchanged) closes the question for the CONTROL path; the MONITORING
path does not need it.

### Q5 — What does the integration need to work WITHOUT the base block?

**The path [D]:** listen-only harvest → decode block frames (the
parser already exists and is vector-tested: `parse_block_response`) →
map the T-window (registers.py) → one calibration run pins the member
order → expose temperature entities from pushes. **Zero frames sent**
[FR-2]: no request for H-APP-GATE to eat.

**Covered by pushes (after calibration):**

| Quantity | Pushed register | Status |
|---|---|---|
| Inlet water temp (T02) | 2047 (live-proven register) | [A→P after calibration] |
| Outlet water temp (T03) | 2048 | [A→P] |
| Ambient temp (T05) | 2050 | [A→P] |
| Coil temp (T04) | 2049 | [A→P] |
| Exhaust temp (T06) | 2051 | [A→P] |
| Suction temp (T01) | 2046 → sentinel → honest `unknown` on this unit | [P-grade anchor] |
| H-family settings (H04…) | 1021 etc. | diagnostics/probe-log only (spec 003 §6 policy: never entities, never writable) |
| R-family thresholds | 1046-1060 ladder | same policy |

**NOT covered — the base block has no proven pushed equivalent [P for
1001/1046/2046; V for 2001-2045]:** power (8), boost (11), mode (13),
setpoint (14), per-mode setpoints (19-21), min/max bounds (22-25),
fault code (47), firmware (48-51).

**Lost functionality in push-only mode [D, honest list]:** the climate
entity (needs power/mode/setpoint read AND write — spec 001's
"device-confirmed state only" rule forbids optimistic control), the
fault-code sensor, the firmware sensor, the O/S binary sensors (homes
unknown — maybe in 2001-2045 [V]). What remains is exactly what the
owner asked for first: **the live temperatures in Home Assistant.**

### Q6 — Are mode/setpoint/on-off readable in the pushes? If not, how does the app read them?

**Not identifiable with current evidence** [P content analysis §2.5;
the fourth block is the honest exception [V]]. Three possibilities for
the app: (a) it reads them from the FC03 base answer (unit answers the
phone — E5); (b) they hide in 2001-2045 (the 2001 capture decides);
(c) the app's home screen is partially blind on this unit too and the
owner's "live temps" observation is exactly the push-fed part (E5).
The R-ladder's 27.0/29.0 values are setpoint *candidates* [A-weak] —
not honest entity data. **No control surface is built on [V]**
(allowlist discipline: writes stay {8,13,14}, all base-block; a
write-path test is a separate evidence-gated experiment, NOT this
spec's scope).

---

## 5. Harvest feasibility — what the code already gives us

1. **The reassembler is done [P]:** `_frame_boundary` + `block_response_length`
   cut 99-byte block frames; CRC validated; both fragmentation regimes
   handled (single-notification [P observed] and 5×20-byte chunks
   [P vector]). Spec 008/009 shipped and production-proved this.
2. **The census counts but does not KEEP [P — code trace]:**
   `_census_frame` logs `start/qty/len/crc` (INFO) and `_idle_census`
   logs the first idle push (INFO) — **the 90 data bytes are discarded
   unread in every current path**. The only full-hex captures ever
   made were the c8d8260-era WARNING drop lines (research 008 §1) —
   which is how the four frames were decoded at all. FR-1 reinstates
   full-hex capture, at WARNING (spec 012's visibility lesson: the
   operator's panel keeps WARNING+ only), bounded to the FIRST push
   per distinct start per connect.
3. **`query_block` already completes on spontaneous pushes [P — code
   trace]:** `_matches()` accepts ANY block frame echoing
   `(start, count)` — a spontaneous push completes the request. The
   existing "Probe registers" service would return 2001's data today
   IF a 2001 push lands inside its 8.0 s window — observed 5× in one
   window, 0× in another [P — F-5]. Unreliable; FR-2's dedicated
   harvest (no TX, longer window) is the robust form.
4. **Push timing [P arithmetic]:** every observed 8 s window carried
   ≥14 frames, first at t+0.1-0.4 s; all four starts appeared within
   two consecutive windows (~16 s). A 30 s listen harvests all four
   blocks with wide margin. Long-run behavior (minutes/hours) is
   [V] — the design must monitor push freshness and reconnect on
   silence (FR-4).
5. **Safety [P]:** pure listening sends ZERO frames. No request for
   the app-layer gate to drop; no allowlist implications; nothing for
   the unit to refuse. The single-connection rule still applies
   (vendor app must be dead / phone BT off — spec 006 §9).
6. **A never-answered request costs 8 s per cycle [P — code]:** in
   push mode the coordinator should NOT send the base read at all
   [D]; the read attempt stays in the connection test (one shot, for
   units where the request path works — other users' units may answer).

---

## 6. Hypothesis ledger (what we now assert vs what we still suppose)

| # | Statement | Status |
|---|---|---|
| H-1 | The unit pushes blocks 1001/1046/2001/2046 spontaneously, CRC-valid, ~1.75-2 frames/s, mix varying per window | [P — 4 production runs] |
| H-2 | Block 2046-2090 carries the T01-T06 family at 2046-2051 (T01 sentinel anchor, T02 live-proven at 2047) | [A-very-strong; window anchored [P-grade]; member order [V — one calibration run]] |
| H-3 | Block 1001-1045 carries the H-family at 1018-1023 (H04 anchor at 1021) | [A-strong — 6/6 panel fit] |
| H-4 | Block 1046-1090 carries the R-family ladder at 1046-1060 | [A-strong] |
| H-5 | No offset/mirror relates base 7-51 to the high blocks | [P — content] |
| H-6 | The base-block control data (power/mode/setpoint/fault/firmware) has no home in the pushes | [P for 3 blocks; V for 2001-2045] |
| H-7 | The push stream runs indefinitely while connected | [V — longest observation ~21 s; FR-4 monitors freshness] |
| H-8 | The pushes are periodic-ish, not (only) change-triggered (1001 = static settings pushed 12× in one window) | [A — F-5; probe timestamps would refine] |
| H-9 | The app's parameter view reads the high blocks; the panel photos are high-block contents | [A-strong — VR.Yzd labels + six query frames + H04/T01 anchors] |
| H-10 | The app's home screen is fed by pushes on this unit (world b) | [V — E5] |

## 7. What remains unproven — and the experiment that closes each

| # | Gap | Experiment | Where |
|---|---|---|---|
| U-1 | Block 2001-2045 content (S/O/D/P homes? live values? base-block equivalents?) | FR-1 full-hex capture — one connection test, four WARNING lines | quickstart §1 |
| U-2 | T-window member order (which reg is inlet/outlet/coil/ambient/exhaust) | calibration run: photograph app T-family (or home screen), BT off, connection test within ~2 min, compare | quickstart §2 |
| U-3 | Long-run push behavior (does the stream persist? cadence over minutes?) | FR-4 freshness monitoring + the push-stream diagnostic entity | FR-4/FR-6 |
| U-4 | The app's session on THIS unit (request-fed vs push-fed home screen; write path) | E5 — HCI snoop, unchanged from specs 009/011 | quickstart §3 |
| U-5 | Whether the request path can be revived for control (bond-keyed app-layer gate) | the landed optional `pair_on_connect` one-shot test + E5 | out of scope here; Track A |
| U-6 | H/R member encodings (H02 0=C? H06 enum? P-family 1037-1040?) | same captures as U-1/U-2 (values already logged) | data-model updates |

## 8. Reproduction

```bash
# decode the four proven frames (the §2 tables):
python3 custom_components/aqua_temp_ble/modbus.py   # self-test §10 vectors
# byte-level re-verification of the §2 tables was done for this spec with
# struct.unpack('>h', ...) over the pinned vectors + reference CRC —
# reproduced in specs/013-pushed-blocks-coverage/data-model.md §2-§5.
# Arithmetic: 12×99=1188, 14×99=1386, 16×99=1584 — the zero-byte-for-
# anything-else proofs of research 009 §0.2 / spec 011 E-1.
```