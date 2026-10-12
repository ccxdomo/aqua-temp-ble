# Spec 015 research — the control data hunt: decoding every pushed block, hunting power/mode/setpoint

Date: 2026-10-12
Evidence base: the four complete CRC-verified pushed-block frames of the
0.10.0 production log (relayed in the spec brief; two byte-identical to the
pinned c8d8260-era vectors, the 2001 frame complete for the first time, the
2046 frame matching the owner-confirmed calibration); the APK artifacts
(`blutter/pp.txt`, FINDINGS.md, specs 003/008/010/013/014); the shipped code
@ fcc9d38 (`registers.py`, `coordinator.py`, `aqua_client.py`, `climate.py`,
`sensor.py`); the owner observations (the app displays on/off, mode and a
setpoint; the T-family calibration photo).

Status legend (the repo convention): **[P]** PROVEN
(calculated/verified — every decode below was computed, not assumed),
**[P owner]** the owner's observation, **[P panel]** the parameter-view
photographs (2026-10), **[P apk]** recovered from the APK, **[V]**
TO-VERIFY (one named experiment decides), **[A]** ASSUMED (reasonable,
cheap to correct), **[D]** DECISION.

Reproduce every table in this file:

```bash
python3 specs/015-control-data/decode_blocks_015.py
```

The four frames live verbatim in `specs/015-control-data/frames.txt`
(198 hex chars each; line 4 reconstructed byte-exactly by the CRC oracle —
see §1.4).

---

## 0. TL;DR — the verdicts, black on white

1. **All four pushed blocks are now decoded register-by-register,
   CRC-verified.** Block 2001-2045 — the never-decoded block, this spec's
   candidate #1 — contains exactly **five** non-zero registers:
   `2010=2001, 2012=1, 2017=13, 2018=168, 2034=4`. **No setpoint-shaped
   value, no provable power/mode pair.**
2. **The power/mode/setpoint are NOT FOUND — verdict: NOT IDENTIFIABLE in
   the pushed blocks with current evidence [P for the decode scans; V for
   the single-capture blocks' liveness].** The complete 2001 decode and the
   whole-plane setpoint scan (no raw in 200..400 outside the static
   1046-ladder) close the "hidden in 2001" hope [P]. What remains are
   *candidates* (2012 flag, 2034 enum, the 1046 R-ladder) that only the
   **toggle-and-diff calibration** (quickstart §1) can promote or kill.
3. **No offset relates the base block (7-51) to the pushed plane** —
   confirmed against the new data: every candidate offset (+994, +1000,
   +1994, +2014) dies on content (§3). `1001 + n ≡ 7 + n` is FALSE; reg
   1002 is NOT base reg 8.
4. **The app demonstrably displays on/off, mode and a setpoint [P owner].**
   Static analysis CANNOT decide their source: (a) an FC03 base answer
   served to the phone but never to us, (b) a mapping in the pushed blocks
   we have not pinned, (c) the never-pushed blocks 1091/1136 the app also
   queries. The three worlds survive every artifact (§7).
5. **New APK evidence recovered by this spec:** the complete 45-name
   base-block list (registers 7-51 — §5.1), the control-parameters page's
   ordered label list containing "Cooling set / Heating set / Auto. Set"
   among the R-family (§5.2), and the proof that every power/mode/setpoint
   WRITE in the app funnels through the control-parameters code path that
   logs `参数选择修改 ----- value = … index = … address = …` (§5.3) —
   **one logcat session reveals the exact vendor write addresses**.
6. **The write path stays closed [D]:** the allowlist is NOT extended
   (§9.8/§10); writes are ATT-accepted but Modbus-silent [P,
   att-accepted-silent]; any future extension requires vendor-proven
   write addresses (logcat/HCI) — never speculation about 2012/2034.

---

## 1. The four frames — method and CRC verification

### 1.1 Provenance [P]

| frame | start | qty | source | relation to prior evidence |
|---|---|---|---|---|
| 1 | 1001 | 45 | 0.10.0 log | byte-identical to the pinned c8d8260-era vector (CRC 0x3A56) — research 008 §2.2 |
| 2 | 1046 | 45 | 0.10.0 log | byte-identical to the pinned vector (CRC 0x7685) — research 008 §2.3 |
| 3 | 2001 | 45 | 0.10.0 log | **complete for the first time** (CRC 0x0092) — supersedes the truncated c8d8260-era paste (research 008 §2.4, 40 reconstruction candidates) |
| 4 | 2046 | 45 | 0.10.0 log | new capture (CRC 0xE5F3); values match the owner-confirmed calibration photo (2047=13.0 °C T02 [P owner]) |

The frame grammar is the proven hybrid shape (research 008 §2.1, pinned in
`modbus.py` self-test §10): `63 10 <start:2 BE> <qty:2 BE> <bytecount=0x5A>
<data: 90 B> <crc16 LE>` — 99 bytes, 45 big-endian signed int16 registers.

### 1.2 CRC verification [P — computed by `decode_blocks_015.py`]

| frame | len | CRC stored | CRC calc | verdict |
|---|---|---|---|---|
| 1001 | 99 B | 0x3A56 | 0x3A56 | VALID |
| 1046 | 99 B | 0x7685 | 0x7685 | VALID |
| 2001 | 99 B | 0x0092 | 0x0092 | VALID |
| 2046 | 99 B | 0xE5F3 | 0xE5F3 | VALID |

### 1.3 A note on the old 2001 paste [P]

The c8d8260-era truncated paste ended in the same CRC tail (`92 00`) as the
complete 0.10.0 capture; a different 90-byte body would carry a different
CRC with overwhelming probability, so the two captures are byte-identical
(parsimonious reading; the alternative — a 2^-16 CRC coincidence — is not
credible). The old reconstruction's "0040 (64)" island (research 008 §2.4)
does not appear in the complete capture; the real non-zero set is
{2010, 2012, 2017, 2018, 2034}. The complete frame supersedes the
reconstruction; the 40-candidate underdetermination is closed.

### 1.4 Line-4 reconstruction note [P]

The brief's 2046 hex was transcribed with a zero-run length ambiguity
(the paste shows an uncountable `0000…` run). The frame was therefore
reconstructed byte-exactly from the observed window (regs 2046-2051 =
`7ffd 0082 007d 007d 007d 0087`) + 39 zero registers + the CRC oracle:
the computed CRC (0xE5F3, wire `f3 e5`) matches the brief's stated tail
exactly — the reconstruction IS the brief's frame [P]. It is pinned in
`frames.txt` line 4.

---

## 2. THE COMPLETE DECODE — all four blocks, register by register [P]

Every value below was computed by `decode_blocks_015.py` (struct.unpack,
signed big-endian, CRC pre-verified). Zero registers are listed with their
raw (0) and marked with their candidate semantics where a window makes it
meaningful.

### 2.1 Block 1001-1045 — the H-family machine-settings block (9 non-zero of 45)

| reg | raw | hex | /10 | reading | status |
|---|---|---|---|---|---|
| 1001-1011 | 0 | 0x0000 | 0.0 | unknown/unused | [V] |
| **1012** | **1** | 0x0001 | — | flag — unknown (single capture; 1001/1046 static across captures) | [V] |
| 1013-1017 | 0 | 0x0000 | 0.0 | unknown/unused | [V] |
| **1018** | **1** | 0x0001 | — | **H01 "Power down memory" = YES** | [A-strong — 6/6 H-window fit, spec 013 §2.1] |
| 1019 | 0 | 0x0000 | — | **H02 "Temperature unit" = C (0=C?)** | [A] |
| 1020 | 0 | 0x0000 | 0.0 | **H03 "Comp. stop" = 0.0 °C** | [A] |
| **1021** | **1050** | 0x041A | 105.0 | **H04 "Exhaust Temp. High" = 105.0 °C — the anchor** | **[P panel — exact match]** |
| 1022 | 1 | 0x0001 | — | **H05 "Outlet Water Temp. Sensor" = ON** | [A] |
| 1023 | 2 | 0x0002 | — | **H06 "Mode type" = heat-only (enum 2)** | [A] — options seen in pool: Chiller/Heat pump/Heat only [P apk §5.2] |
| 1024-1036 | 0 | 0x0000 | 0.0 | unknown/unused | [V] |
| 1037 | 2 | 0x0002 | — | unknown — P-family candidate? | [V] |
| 1038 | 30 | 0x001E | 3.0 | unknown — P-family candidate (P02 Interval?) | [A-weak] |
| 1039 | 3 | 0x0003 | — | unknown — **P03 Duration = 3 fits [P panel]** | [A] |
| 1040 | 1 | 0x0001 | — | unknown — P-family candidate (P01 "Nor"=1?) | [A-weak] |
| 1041-1045 | 0 | 0x0000 | 0.0 | unknown/unused | [V] |

The H-window hypothesis (spec 013 §2.1) survives the second complete
capture unchanged: 6 consecutive registers 1018-1023 fit the six
panel-observed H values (YES, C, 0.0, 105.0, ON, heat-only), anchored by
the unique 1050 at H04. **Static across ≥ 2 days and ≥ 2 connects**
(byte-identical frames) [P].

### 2.2 Block 1046-1090 — the regulation/defrost/EEV ladder (16 non-zero of 45)

| reg | raw | hex | /10 | candidate label (§5.2 list) | status |
|---|---|---|---|---|---|
| **1046** | **290** | 0x0122 | 29.0 | "Cooling set" (page order #1)? | [A-weak] |
| 1047 | 5 | 0x0005 | 0.5 | "Cool diff" (#4)? | [A-weak] |
| 1048 | 5 | 0x0005 | 0.5 | "Cool stop" (#5)? / "Heat diff." (#6)? | [A-weak] |
| 1049 | 150 | 0x0096 | 15.0 | "Heating set" (#2)? — 15.0 °C is a plausible winter pool setpoint | [A-weak] |
| 1050 | 400 | 0x0190 | 40.0 | "Max. cool"/"Max. heat" (#9/#11)? — matches FALLBACK_MAX_TARGET 40.0 | [A-weak] |
| 1051 | 270 | 0x010E | 27.0 | "Heating set"/"Auto. Set" (#2/#3)? — classic pool setpoint | [A-weak] |
| 1052 | 5 | 0x0005 | 0.5 | differential family | [A-weak] |
| 1053 | 5 | 0x0005 | 0.5 | differential family | [A-weak] |
| 1054 | 100 | 0x0064 | 10.0 | "Min. cool"/"Min. heat" (#8/#10)? — plausible min bound | [A-weak] |
| 1055 | 350 | 0x015E | 35.0 | "Max. cool" (#9)? | [A-weak] |
| **1056** | **-70** | 0xFFBA | -7.0 | **"Stat Defrosting Temp" — classic defrost-start threshold** | [A] |
| 1057 | 130 | 0x0082 | 13.0 | "End Defrosting Temp"? / EEV? | [A-weak] |
| 1058 | 40 | 0x0028 | 4.0 | "Super Heat" (EEV target, 4.0 K classic)? | [A-weak] |
| 1059 | 8 | 0x0008 | 0.8 | "Max./Min. Duration"? "Defrosting Cycle"? | [V] |
| 1060 | 3 | 0x0003 | — | "Defrosting Cycle"? | [V] |
| 1061-1067 | 0 | 0x0000 | — | unused/zero | [V] |
| **1068** | **270** | 0x010E | 27.0 | second 27.0 — the later "Auto. set" (§5.2 label list has TWO auto-set labels)? | [A-weak] |
| 1069-1090 | 0 | 0x0000 | — | unused/zero | [V] |

**Static across ≥ 2 days and ≥ 2 connects** (byte-identical frames) [P].
The ladder's values sit exactly in the panel-observed R-family range
(0.5-40.0 °C, 11 members [P panel]) plus defrost/EEV territory (-7.0,
4.0) — consistent with the control-parameters page's ordered label list
(§5.2) whose ~50 parameters span the whole 1001-1180 machine-settings
series. The per-member label→register alignment is **not resolvable
statically** (dedup hides the immediates) — the page's write log
(§5.3/quickstart §2) resolves it register-by-register, or the
setpoint-diff experiment pins the user-relevant members.

### 2.3 Block 2001-2045 — the never-decoded block, NOW COMPLETE [P] (5 non-zero of 45)

| reg | raw | hex | /10 | reading | status |
|---|---|---|---|---|---|
| 2001-2009 | 0 | 0x0000 | 0.0 | unknown/unused | [V] |
| **2010** | **2001** | 0x07D1 | 200.1 | **self-reference: the register holds its own block's start address** — block base/marker? device/area code? | [V] — 200.1 °C is not a temperature; 2001 is not a flag |
| 2011 | 0 | 0x0000 | — | zero flag | [V] |
| **2012** | **1** | 0x0001 | — | **flag = 1 — the strongest POWER candidate in the 2000-series** (the only non-zero boolean) | [V] — single capture, no liveness, no cross-moment diff |
| 2013-2016 | 0 | 0x0000 | — | zero flags | [V] |
| **2017** | **13** | 0x000D | 1.3 | unknown — NOT temp-shaped (1.3 °C fits nothing physical here); count/enum/timer? | [V] |
| **2018** | **168** | 0x00A8 | 16.8 | temp-shaped (16.8 °C — plausible at some other moment); runtime (168 h = exactly one week)? unknown | [V] |
| 2019-2033 | 0 | 0x0000 | — | unknown/unused — a 0,0,1 triple for S01/S02/S03 (HP OFF, LP OFF, Flow ON [P panel]) does NOT fit this window cleanly (§6.2) | [V] |
| **2034** | **4** | 0x0004 | — | **small enum — the strongest MODE candidate in the 2000-series** (4 = ?) | [V] — single capture |
| 2035-2045 | 0 | 0x0000 | — | unknown/unused | [V] |

**What the block does NOT contain [P — decode scan]:**
- **no setpoint-shaped value** — no raw in 200..400 anywhere in the block;
- **no plausible power+mode PAIR** — one boolean (2012) and one enum
  (2034) exist, but nothing correlates them, and one capture cannot show
  liveness;
- **no sentinel** (0x7FFD/-9990) — no absent-sensor registers;
- **no firmware-shaped group**, no fault-shaped value.

The old hope — "control mirrors hidden in 2001" (research 013 §2.3,
candidate home of S/O/D/P) — is now bounded by data: whatever
S/O/D/P content this block holds, at most 5 registers of it are non-zero,
and none is a temperature setpoint. If the S/O/D/P families live here,
most of their members read ZERO in this capture (an idle unit's S/O
states are OFF [P panel] — consistent, unfalsifiable from one capture).

### 2.4 Block 2046-2090 — the T-family live-temperatures block (6 non-zero of 45)

| reg | raw | hex | /10 | reading | status |
|---|---|---|---|---|---|
| **2046** | **32765** | 0x7FFD | — | **T01 Suction — sensor absent (sentinel)** | [P — panel anchor + 3 captures] |
| **2047** | **130** | 0x0082 | **13.0** | **T02 Inlet water — CALIBRATED [P owner photo]** | [P] |
| **2048** | **125** | 0x007D | 12.5 | T03 Outlet water | [P owner photo] |
| 2049 | 125 | 0x007D | 12.5 | T04 Coil | [P owner photo] |
| 2050 | 125 | 0x007D | 12.5 | T05 Ambient | [P owner photo] |
| 2051 | 135 | 0x0087 | 13.5 | T06 Exhaust | [P owner photo] |
| 2052-2090 | 0 | 0x0000 | — | unused/zero | [V] |

Third independent capture of the window; values drift plausibly between
captures (13.0-13.5 band here vs 14.0-15.0 on 2026-10-10 — a cooling
trend consistent with the unit being off or idle [A-weak]). The T-window
is the project's only CALIBRATED pushed mapping — the template the
control hunt must reproduce (photo/diff → pin → confirm → entity).

---

## 3. The offset question — every mirror, killed by content [P]

The brief's structural observations are correct as arithmetic:
`1001 − 7 = 994`, `1046 − 1001 = 45`, `2001 − 1001 = 1000`,
`2046 − 1046 = 1000`. What they describe is the block layout (45-register
contiguous series; the 1000 delta separates the machine-settings series
1001-1180 from the live/parameter series 2001-2090 [P apk, FINDINGS
§11.1]) — **not a mirror of the base block**.

Every candidate offset, tested against the complete decode:

| offset | mapping | dies on |
|---|---|---|
| +994 (1001+n ≡ 7+n) | base 8 Power → 1002 = 0; base 13 Mode → 1007 = 0; base 14 SetCurrentTemp → 1008 = **0.0 °C setpoint** (absurd — the app displays a real setpoint); base 19-21 targets → 1013/1014/1015 = **all zero** (absurd); base 27 OutletWaterTemp → 1021 = **105.0 °C outlet water** (physically absurd); base 26 InletWaterTemp → 1020 = 0.0 vs the LIVE 13.0 °C at reg 2047 the same week [P calibrated] | [P] |
| +1000 | base 21 SetAutoTargetTemp → 1021 = **105.0 °C pool target** (absurd; and 1021 = H04 105.0 °C [P panel] — the same number, different quantity); base 26 Inlet → 1026 = 0.0 vs live 13.0; base 14 → 1014 = 0.0 | [P] |
| +1994 (2001+n ≡ 7+n) | base 8 Power → 2002 = 0; base 13/14 → 2007/2008 = 0; base 19-21 → 2013/2014/2015 = 0; base 26 Inlet → 2020 = 0.0 vs live 13.0 at 2047 | [P] |
| +2014 (2046+n ≡ 7+n) | base 8 Power → 2053 = 0; base 26 Inlet → 2072 = 0.0 while the ACTUAL inlet lives at 2047 (delta 2021); base 27 → 2073 (delta 2021); base 28 AT → 2078 (delta **2022** — non-constant) | [P] |

**Verdict: no constant offset; no mirror — CONFIRMED [P]** (first killed
by spec 013 §3 / spec 014 §2 on partial data; now re-killed against the
complete four-block decode). Register 1002 is NOT the power register; the
base block (7-51) is a third dataset, and **this firmware serves it to us
under no grammar we know**: no FC03 answer, no exception, no start-7
block, across six runs [P — spec 014 §2/M-7].

The live-temperature deltas (2021/2021/2022) additionally disprove any
member-wise high↔base temperature mapping [P].

---

## 4. Q1-Q8 — the brief's questions, answered

### Q1 — decode the four blocks

§2 above. Summary: 1001 = H-family settings (9 non-zero, static);
1046 = regulation/defrost/EEV ladder (16 non-zero, static); 2001 = 5
non-zero, semantics unknown (the self-reference 2010, the flag 2012, the
enum 2034, 13, 168); 2046 = the calibrated T-window + zeros. Revealing
values: **0x7FFD sentinel at 2046** [P]; **no 20-40 °C setpoint raw in any
block except the 1046 ladder** [P]; **booleans at 1012/1018/1022/1040
(static) and 2012 (single capture)** [P raw]; **enums at 1023=2, 1037=2,
1039=3, 1060=3, 2034=4** [P raw].

### Q2 — does block 2001 contain the control?

**Undetermined — and no longer the open field it was.** The complete
decode proves the block contains no setpoint [P] and only two
control-shaped candidates (2012 flag, 2034 enum) whose meaning one
capture cannot decide [V]. A power flag and a mode enum COULD be exactly
this shape — but so could a filtration flag and a fan-speed enum. The
block's other members (2010=2001 self-reference, 2017=13, 2018=168)
match no control semantics. **The toggle-and-diff experiment
(quickstart §1) is the only honest promotion path**; if no register of
2001 moves when the owner toggles power, the block is exonerated of
holding power [P-grade negative proof].

### Q3 — is block 1001 the base block shifted? Is reg 1002 the power?

**NO [P].** §3: the +994 mirror puts a 0.0 °C setpoint at 1008, three
zero per-mode targets, 105.0 °C outlet water at 1021, and 0.0 °C inlet at
1020 against the same-week live 13.0 °C. `1001 + n ≡ 7 + n` is FALSE.
Reg 1002 = 0 in this capture — and even IF the unit were off, the rest of
the mirror's content absurdities kill the hypothesis independent of the
power state. (The unit's actual power state during the captures is
UNKNOWN [V] — see Q6 — so 1002=0 alone proves nothing either way; the
mirror's death is content-driven.)

### Q4 — is there a constant offset between the two planes?

**NO [P].** §3. The 1000 delta is the series separator; 45 is the block
stride; the base block is a third dataset (Q3) served by no grammar we
can trigger [P — six runs, spec 014 §2]. The base block's home remains
FC03-only — the one frame shape this unit never sends us.

### Q5 — is the setpoint in the blocks? Could 1021=1050 be it?

**The setpoint has NO identified home in the pushed plane [P scan; V
candidates].** The whole-plane scan for raw 200..400 finds values only in
the 1046 ladder: 1046=290, 1050=400, 1051=270, 1055=350, 1068=270 (and
1049=150 = 15.0 °C sits just below the scan band but is setpoint-shaped
too). These are STATIC across ≥ 2 days [P] — consistent with installer
thresholds AND with an untouched user setpoint (the owner may not have
changed it) — so staticness neither proves nor disproves a setpoint
member. The control-parameters page's label list (§5.2) proves the app
EDITs setpoints named "Cooling set / Heating set / Auto. Set" somewhere
in the 1001-1180 series [P apk]; the per-member register alignment is
[V] (decided by the logcat write log or the setpoint-diff experiment).

**1021 = 1050 is NOT a setpoint [P]:** it is H04 "Exhaust Temp. High"
105.0 °C, matching the panel-photographed safety threshold exactly
[P panel, data-model 003 §4]. As a setpoint it is absurd (above the 40.0
max bound in the same ladder, above FALLBACK_MAX_TARGET, outside any
pool operating range). The "could 1050 be something else?" question is
answered: it is H04 — the strongest register-level panel match the
project has.

The honest statement: **if the app's home-screen setpoint comes from the
pushed plane at all, it is one of {1046, 1049, 1051, 1055, 1068} — a
five-candidate list the owner's displayed value + the ±0.5 °C diff
experiment collapses in one run** (quickstart §1 steps S). If no ladder
member matches the displayed value, the setpoint is not in the pushes
and the app reads it elsewhere (base 14 / blocks 1091-1136 — Q6).

### Q6 — how does the app obtain power/mode? Does it read block 7?

**It SENDS the base read every session [P apk — spec 010 §2: the session
continuation writes `63030007002D3C54` right after subscribe + 2 s],
and it also queries all six blocks [P apk].** Whether the unit ever
ANSWERS the app's base read on this unit is [V — E5, the HCI snoop,
unrun]. What IS proven:

- the app DISPLAYS on/off, mode and a setpoint [P owner] — so it
  receives live control data over BLE from somewhere (this unit has no
  WiFi; BLE is its only channel [P]);
- the app ENFORCES control guards from device data: it refuses power
  control when the emergency switch is open ("应急开关断开，开关机不可控"
  [P apk i18n]) and refuses mode changes on a heat-only unit ("当前模式
  为单热模式，不可设置为其他模式！" [P apk i18n]) — the app READS the
  emergency-switch and ModeType states;
- the unit's answer to OUR base read is the 4-block burst (1001/1046/
  2001/2046 — never 1091/1136, never a start-7 block, never an FC03
  answer) [P — six runs, spec 014 §1-§2];
- naive position-wise mapping of the pushed blocks onto the 45-name
  base list (§5.1) would read Power from 1002=0/2002=0/2047=130 —
  i.e. always-off or 13.0 °C-as-power — and SetCurrentTemp from
  1008=0 — i.e. a 0.0 °C setpoint; a home screen fed this way would show
  patently broken values, while the owner sees a working display
  [P owner]. So IF the app is push-fed, its mapping is NOT naive — it
  would be a hidden non-positional mapping (the 2012/2034/ladder
  candidates).

**The three surviving worlds (all consistent with every artifact):**

| world | source of the app's power/mode/setpoint | status |
|---|---|---|
| (a) request-fed | the unit answers the PHONE's FC03 (identity/bond-keyed app-layer gate — research 011 §11 rank 2); home screen reads base 8/13/14 | [V — E5 decides] |
| (b) push-fed, hidden mapping | the app decodes the burst non-positionally: power at 2012? mode at 2034? setpoint in the 1046 ladder? | [V — the diff experiment decides] |
| (c) other-blocks | the app's six-block queries get answers we never see (1091/1136 contain S/O/D/P — possibly control-adjacent states) | [V — the probe experiment C4 decides] |

Static analysis cannot separate them — **E5, the toggle-diff, and the
1091/1136 probe are the three named deciders** (quickstart §1/§3/§4).

### Q7 — can the push mode feed the climate entity?

**Today: NO [D — unchanged from contract 013 §4.4].** The climate needs
power/mode/setpoint, none of which has a confirmed pushed home [P scan /
V candidates] — and spec 001's rule forbids optimistic state. **The gate
is evidential, not architectural:** the coordinator's push cycle already
captures all four blocks every cycle (trigger + listen, spec 014); IF
the diff experiment pins the homes (and the confirmation gate opens,
contract §7), `from_pushes` can decode them and a READ-ONLY climate
becomes honest (hvac_mode from power+mode, target from the setpoint
home, bounds from the fallbacks until pinned). **The write path is
separately and additionally blocked at the transport level [P —
att-accepted-silent]** — see Q8 and §10.

### Q8 — the write guard: extend it to the high plane?

**NO — not now, not speculatively [D].** Three independent reasons:

1. **No proven high-plane write target exists.** 2012/2034 are READ
   candidates only; writing an unpinned register in the parameter plane
   risks corrupting machine settings (the H04 exhaust-high safety
   threshold lives one window away at 1021 — an erroneous write there is
   a safety hazard, spec 003 §6's rationale verbatim).
2. **The vendor's own write path is the only sanctioned oracle [P apk]:**
   every power/mode/setpoint write in the app funnels through the
   control-parameters code path (spec 010 §2.4's four control-page call
   sites, all in the [nrk] class whose pool cluster carries the
   `参数选择修改 ----- value = … index = … address = …` log strings
   — §5.3) and the app logs the register address on every write
   (FINDINGS §12.2). **One logcat session while the owner toggles power /
   mode / setpoint prints the exact addresses** (quickstart §2). Until
   that evidence exists, the allowlist stays {8, 13, 14} (+19-21 behind
   the switch) — the addresses the 45-name base list [P apk §5.1] says
   the vendor's own status/control model uses.
3. **Writes are currently swallowed anyway [P]:** our ATT-accepted writes
   produce zero Modbus bytes back (att-accepted-silent, six runs) — the
   application-layer gate eats them. Extending the allowlist before the
   delivery gate is understood would add risk with zero capability.

The time-sync frame the app sends (`631004f30004080001…`, FINDINGS
§11.1) proves the app DOES write high-plane registers (register 1267,
machine-settings series) — so the vendor write addresses could turn out
to be base-plane, high-plane, or both; only the logcat oracle decides
[V].

---

## 5. New APK evidence recovered by this spec

### 5.1 The complete 45-name base-block list [P apk — pp+0x18f18]

```
List<String>(45) ["SetPVReadyMode", "Power", "SetFrequencyMode", "SetFanMode",
"EnBoostMode", "EnAPPTimeUpdate", "Mode", "SetCurrentTemp", "ModeType",
"SetTempUnit", "EnOutletWaterTempSensor", "EnSlient", "SetCoolTargetTemp",
"SetHeatTargetTemp", "SetAutoTargetTemp", "SetCoolMinTargetTemp",
"SetCoolMaxTargetTemp", "SetHeatMinTargetTemp", "SetHeatMaxTargetTemp",
"InletWaterTemp", "OutletWaterTemp", "AT", "EnTimerSlient",
"SetTimerSlientStartTime", "SetTimerSlientStopTime", "EnTimer1On",
"EnTimer1Off", "SetTimer1StartHour", "SetTimer1StartMinute",
"SetTimer1StopHour", "SetTimer1StopMinute", "SetCompType",
"Sys1CompWorkingFreq", "SetHeatMaxFreq", "SetCoolMaxFreq", "EnSupportAI",
"SetAIMode", "EnAIPower", "CompressorCurrentDetect",
"InverterBoardACInputVoltage", "Fault1", "MainBoardSoftwareVer",
"MainBoardSoftwareCode", "DisplaySoftwareVer", "DisplaySoftwareCode"]
```

Position i ↔ base register 7+i. This INDEPENDENTLY confirms the entire
established map (8=Power, 11=EnBoostMode, 13=Mode, 14=SetCurrentTemp,
19-21 per-mode targets, 22-25 bounds, 26-28 Inlet/Outlet/AT, 47=Fault1,
48-51 firmware [P apk, FINDINGS §12]) and extends it: reg 15 **ModeType**
(the H06 quantity!), reg 16 **SetTempUnit** (H02!), reg 17
**EnOutletWaterTempSensor** (H05!) — three H-family members ALSO live in
the base block; timers at 29-37; compressor type/frequencies at 38-41;
AI settings at 42-44; CompressorCurrentDetect 45; InverterBoardACInputVoltage 46.
(The adjacent `List<String>(10)` at pp+0x18f28 holds the switch/fault-sensor
names: Sys1HighPressureSwitch, Sys1LowPressureSwitch, WaterFlowSwitch,
EmergencySwitch, ModeSwitch, Master/SlaveSwitch, Sys1CompState,
Inlet/Outlet/ATSensorFault — the app's emergency-switch guard (Q6) reads
from this family's data.)

**Consequence for the offset hunt:** the base block is a coherent,
complete, NAMED dataset — status + config + control. The pushed plane is
a different, also-coherent dataset (settings + live temps + unknowns).
Two datasets, three mirrors tested, zero survivors (§3).

### 5.2 The control-parameters page's ordered label list [P apk — pp+0x345b0-0x347c0]

The pool cluster of the control-parameters page ("蓝牙控制参数", the
`[nrk] _AS` class — the page behind the debug password "66") carries its
UI label strings in page order:

> P-family: "Nor."/"Stop"/"Nor", (120), "min", "Duration", "Advance" —
> then: **"Cooling set", "Heating set", "Auto. Set"**, "Cool diff",
> "Cool stop", "Heat diff.", "Heat stop", "Min. cool", "Max. cool",
> "Min. heat", "Max. heat" — then defrost: "Ambient"/"Time"/"Ambient+Time",
> "Speed", "Stop cool", "Stop heat", "Min.start time", "Min.stop time",
> "Coil", "Ctrl probe", "NO"/"Yes", "Start Blower", "Blower Min",
> "Blower Max" — then H-family: "Power down memory", "Chiller"/"Heat
> pump"/"Heat only", "Mode type", "Comp. stop.", "Manual.", "EEV Mode",
> "Super Heat", "Init. Place", "N", "Min. Place", "Defrost Place",
> "Cooling Place", "EEV exhaust set temp.", "Stat Defrosting Temp",
> "End Defrosting Temp", "Defrosting Cycle", "Max. Duration",
> "Min. Duration", "Eco.", "Defrost type", "Temp difference", "Power off
> temp difference(Heating)" (45), "Power off temp difference(Cooling)",
> "Auto. set", "Comp. stop" (150), "Exhaust Temp.High", "Outlet Water
> Temperature Sensor", "Heat only(4-way valve)".

Facts this pins:

- The **eleven temperature parameters** "Cooling set … Max. heat" are the
  panel's R-family (11 members, 0.5-40.0 °C [P panel]) — **the user's
  setpoints are EDITABLE MEMBERS of this page** [P apk], living somewhere
  in the 1001-1180 machine-settings series the page queries.
- The H-family labels of this page ("Power down memory", "Mode type",
  "Comp. stop.", "Exhaust Temp.High", "Outlet Water Temperature Sensor",
  "Heat only(4-way valve)") match the 1018-1023 window's panel-observed
  values 1:1 [P panel + P decode] — the H-window hypothesis gets a
  second, independent corroboration.
- FINDINGS §12's guess ("the Control parameters page maps to the high
  block 2001-2090") is **disproven for the setpoint members** [P — the
  complete 2001 decode contains no setpoint-shaped value (§2.3)]; the
  page's dataset is the 1001-1180 series (blocks 1001/1046/1091/1136).

### 5.3 The write-address oracle [P apk — pp+0x34900-0x34910]

The same pool cluster carries the write log strings:

```
"参数选择修改 ----- value = "   ("parameter change ----- value = ")
"参数选择修改 ----- index = "   ("parameter change ----- index = ")
" address = "
"蓝牙控制参数-确认修改参数"     ("control parameters - confirm change")
"蓝牙控制参数-编辑参数"         ("control parameters - edit parameter")
"蓝牙控制参数"                  ("control parameters")
```

and spec 010 §2.4's call-site scan already proved every outgoing frame
funnels through ONE sender (0xa14ea8) whose callers include exactly four
control-page call sites (0x6a14b8, 0x6a1774, 0x6a2158, 0x6a2588 — power/
mode/setpoint writes) — all inside this same [nrk] class. **Therefore:
toggling power / mode / setpoint in the app prints
`value = V index = I address = A` in logcat** [P — the strings exist in
exactly the code region that performs those writes]. This is the
vendor-proven write-address oracle (FINDINGS §12.2), now strengthened:
one logcat session covers all three control writes (quickstart §2).

### 5.4 The home-screen control surface [P apk — i18n pool 0x45b20-0x46380]

- power toggle: "开关机" (power on/off), "滑动开机"/"滑动关机" (slide to
  power on/off), "开"/"关" (ON/OFF);
- guards: "应急开关断开，开关机不可控" (emergency switch open → power not
  controllable), "从机状态下，开关机不可控" (slave state → power not
  controllable), "当前模式为单热模式，不可设置为其他模式！" (heat-only
  → mode locked) — the app READS these states before allowing control;
- setpoint: "目标温度不能高于最高温度！/不能低于最低温度！" (target
  clamped by min/max — the base 22-25 bounds);
- status verbs: "加热到目标温度" (heat to target) + "heatingEngaged"
  "制热运行中", "冷却到目标温度" + "coolingEngaged" "制冷运行中",
  "自动恒温" + "autoModeEngaged" "自动运行中", "待机" (standby);
- failure toasts: "控制失败！" (control failed), "指令控制出错"
  (command control error), "设备不在线，不可控制！" (device offline, not
  controllable).

The app's home screen is a full control surface reading live state —
reinforcing Q6's conclusion that the app receives live control data
over BLE [P owner + P apk].

---

## 6. Cross-referencing the panel observations [P panel ↔ P decode]

### 6.1 What matches

| panel (photo, idle unit) | pushed decode | verdict |
|---|---|---|
| T01 sentinel / T02-T06 temps | 2046-2051 | [P — calibrated] |
| H01 YES, H02 C, H03 0.0, H04 105.0, H05 ON, H06 heat-only | 1018-1023 = (1, 0, 0, 1050, 1, 2) | [A-strong — anchored by H04] |
| R-family: 11 members, 0.5-40.0 °C | the 1046-1060 ladder (15 values, incl. 0.5 ×4, 10.0, 35.0, 40.0) | [A-strong] |
| P03 Duration = 3 | 1039 = 3 | [A] |
| O01-O04 all OFF (idle) | zero runs everywhere (uninformative) | [V] |
| H70/H71/H72 (labels exist [P apk]) | no candidate identified | [V] |

### 6.2 What does NOT match cleanly

- **S01 HP=OFF, S02 LP=OFF, S03 Flow=ON**: no 0,0,1 triple fits any
  window of the four blocks cleanly (2011/2012/2013 = 0,1,0;
  2032/2033/2034 = 0,0,4; every other flag is static). Either the states
  changed between photo and capture, or the S-family lives in the
  never-pushed blocks 1091/1136 [V]. NOTE: the photo was taken with the
  unit idle; a capture-time flow-switch change (pump primed?) is plausible.
- **P02 Interval = 0** vs the P-candidate 1038 = 30 [A-weak] — either
  the interval changed (owner set 30 min?) or 1038 is not P02.
- The panel photographed S/O/P pages DISPLAYING values — so those pages'
  datasets reach the phone somehow [P panel]: via the pushed blocks
  (then the homes are among the pushed registers, mostly zero at capture
  time) or via 1091/1136 answers we never receive [V — C4 decides].

---

## 7. The hypothesis ledger

| # | statement | status |
|---|---|---|
| H-1 | The four pushed blocks are the complete pushed plane: 1001/1046/2001/2046, never 1091/1136, never start-7, never an FC03 answer | [P — six censused runs] |
| H-2 | 1001/1046 are static (byte-identical across ≥ 2 days / ≥ 2 connects) | [P — CRC identity] |
| H-3 | 2046-2051 is the T-window (calibrated) | [P owner photo] |
| H-4 | 1018-1023 is the H-window (H04 anchor + §5.2 label-list corroboration) | [A-strong] |
| H-5 | The 1046-1060(+1068) ladder is the regulation/defrost/EEV set including the three editable setpoints | [A-strong labels / V alignment] |
| H-6 | No offset relates base 7-51 to the pushed plane | [P — §3] |
| H-7 | No setpoint-shaped value exists in 1001/2001/2046; the only candidates are the five ladder values (§2.2) | [P scan / V candidacy] |
| H-8 | 2012 (flag) and 2034 (enum) are the strongest 2000-series power/mode candidates | [V — one capture, no liveness] |
| H-9 | The base block is served to the phone but not to us (world a) | [V — E5] |
| H-10 | The app's power/mode/setpoint WRITES log their register addresses in logcat | [P apk — §5.3] |
| H-11 | The unit was off or idle during the captures (cooling water trend; no exhaust heat) | [A-weak] |
| H-12 | The S/O/D/P families live in the never-pushed blocks 1091/1136 (world c) | [V — C4] |

---

## 8. What remains unproven — and the experiment that closes each

| # | gap | experiment | quickstart |
|---|---|---|---|
| U-1 | The power home: which register flips when the owner toggles power? (candidate: 2012; or none of the pushes → world a/c) | **the toggle-diff**: capture → toggle in the app → capture → diff → toggle back → diff (reversibility = proof) | §1 step P |
| U-2 | The mode home (candidate: 2034; may be untestable — heat-only mode lock [P apk]) | mode-diff if the app allows a mode change; else the logcat write address + the photo value | §1 step M |
| U-3 | The setpoint home (candidates: 1046/1049/1051/1055/1068; or none) | the owner notes the DISPLAYED setpoint; the ±0.5 °C diff pins the member | §1 step S |
| U-4 | The vendor's exact write addresses for power/mode/setpoint | **the logcat oracle** — adb logcat while toggling in the app; grep `参数选择修改` / `address` / `6310` | §2 |
| U-5 | World a vs b vs c (the app's source; whether the phone gets an FC03 answer) | E5 — the HCI snoop of one vendor session (unchanged since spec 009) | §3 |
| U-6 | Do direct block queries (1091/1136) get answers? (never-pushed blocks readable?) | the "Probe registers" service, block=1091/1136 (+ 2001 as control) — spec 014's U-6', still unrun | §4 |
| U-7 | 2001's semantics (2010/2012/2017/2018/2034) | any U-1/U-2 diff that moves a 2001 register; multiple captures across states | §1 |
| U-8 | The unit's power state at capture time (H-11) | the owner notes the app's on/off at each capture | §1 step 0 |

### §8.1 The 2026-10-12 diff results (owner-run — [P])

The quickstart §1 toggle-and-diff ran in production (panel + app):
baseline capture → power slide in the app → capture → revert → capture.
Two registers followed the change AND the revert — the pinning rule of
contract 015-A §2.1 is satisfied for both:

| quantity | home | the diff (block 2001) | status |
|---|---|---|---|
| **Power (read)** | **2025** | `0000` (off, 07:47) → `0001` (on, 08:31) → `0000` (off, 08:37) | **[P owner diff-revert]** |
| **Mode (read)** | **2034** | `0000` → `0004` → `0000` across the same toggle | **[P owner diff-revert]** |
| Setpoint | none identified | no register moved with a setpoint change; no setpoint-shaped value exists in 2001 (§2.3 scan) | the ladder five stay candidates [V] — quickstart §1 step S |

Consequences:

- **U-1 and U-2 are CLOSED** (pinned by diff-revert); **U-8 is closed for
  the three 2026-10-12 captures** (the states are recorded above — the
  owner observed the app's display at each step).
- **2012 is EXCLUDED for power** [P]: the flag candidate read a static
  `0001` across the whole diff (off and on alike).
- **H-8 is superseded**: the 2000-series power home is 2025, not 2012;
  the mode candidate 2034 is confirmed as the mode home.
- **U-7 is partially closed**: 2025/2034 are pinned; the remaining 2001
  unknowns (2010 self-reference, 2012, 2017=13, 2018=168) stay [V].
- **The mode VALUE mapping stays [A]** (D-3): the diff proved 2034
  follows the power state, not the enum's full semantics; on this
  heat-only unit (H06 = heat-only [P panel]; the app locks mode changes
  [P apk]) the on-state value 4 maps to `heat` — surfaced in the
  diagnostics, never silent; unmapped values render unknown + one
  warning per session.
- The verification step stays mandatory (quickstart §5): after opening
  the gate the owner compares `hvac_mode` against the app's display; a
  mismatch closes the gate and corrects the one line in
  `registers.PUSHED_CONTROL`.
- **The write posture is unchanged** (§10/contract 015-B): a read-diff
  proves what a register REFLECTS, never what writing it CONTROLS;
  `ensure_writable` keeps the allowlist {8, 13, 14} + 19-21.


---

## 9. The eight brief questions — one-line verdicts

| # | question | verdict |
|---|---|---|
| 1 | Decode the four blocks | §2 — complete, CRC-verified; 1001/1046 static, 2001 five non-zero unknowns, 2046 the calibrated T-window |
| 2 | Does 2001 contain control? | UNDETERMINED — no setpoint [P]; candidates 2012/2034 [V]; the toggle-diff decides |
| 3 | Is 1001 the base block shifted (power at 1002)? | NO [P — content]; 1002=0 regardless uninterpretable without the capture-time power state |
| 4 | Constant offset between the planes? | NO [P — §3 kills +994/+1000/+1994/+2014] |
| 5 | Is the setpoint in the blocks? Is 1021 it? | No identified home [P scan]; candidates = the 1046 ladder five [V]; 1021=H04, NOT a setpoint [P panel] |
| 6 | How does the app get power/mode? | Sends base read + six block queries [P apk]; source of its display is [V] — worlds a/b/c, decided by E5 / diff / probe |
| 7 | Can push mode feed the climate? | Not today [D]; becomes honest IF the diff pins homes + the confirmation gate opens (read-only; writes separately blocked) |
| 8 | Extend the write guard to the high plane? | NO [D] — vendor-proven addresses only (the logcat oracle); writes currently undelivered anyway [P] |

---

## 10. Security notes (the write path — restated for the record)

- The allowlist `{8, 13, 14}` (+19-21 behind `WRITE_MODE_SETPOINT_TOO`)
  stays EXACTLY as is [D]. `ensure_writable` is untouched by this spec.
- Any future extension requires: (1) the vendor-proven write address
  (logcat `address = A` or an HCI capture of an app write), (2) the
  delivery gate understood (post-E5 / post-pairing world), (3) its own
  spec with its own evidence — the spec 003 §6 rationale (an erroneous
  H/R write can damage the unit) applies verbatim to the whole 1001-2090
  plane.
- The pushed-plane READ candidates (2012/2034) are never written on the
  strength of a read correlation alone: a read-diff proves what a
  register REFLECTS, not what writing it CONTROLS [D].

## 11. Reproduction

```bash
python3 specs/015-control-data/decode_blocks_015.py   # §2 tables + §3 mirror tests + scans
cat specs/015-control-data/frames.txt                  # the four CRC-verified frames
python3 custom_components/aqua_temp_ble/modbus.py     # the frame grammar self-test (unchanged)
```

Pool citations: `blutter/pp.txt` lines 20812 (the 45-name list),
60473-60522 (the control-parameters label cluster), 60576-60578 (the
write-address log strings), 25812/77583/77645/77613/77724/77624 (the
home-screen control strings), 20814 (the switch/fault list).