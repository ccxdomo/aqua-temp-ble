# Spec 014 research — the unit ANSWERS requests: the "push stream" was request-triggered all along

Date: 2026-10-11
Evidence base: the 0.9.0 production log of 2026-10-11 22:40-22:41 against
`08:06:A6:C0:94:1C` (BlueNRG-C0941C) — the owner-pasted lines relayed in
the brief plus the entry-setup failure line; the five earlier production
runs (builds c8d8260, ce41aa5, 0b16e0a, d860933, 1ac276d — the 0.8.0
`att-accepted-silent` log); FINDINGS.md; specs 008/009/011/012/013 (every
claim inherited from them stays tagged there); the shipped code @ 9d752a7
(`aqua_client.py`, `coordinator.py`, `config_flow.py`, `registers.py`,
`const.py`, `sensor.py`, `modbus.py`).

Status legend: **[P]** PROVEN (calculated/verified from the artifacts or
the cited log), **[V]** TO-VERIFY (one named experiment decides),
**[A]** ASSUMED (reasonable, cheap to correct), **[D]** DECISION.
Every claim is tagged.

---

## 0. The production evidence, decoded

The 0.9.0 connection-test log (build 9d752a7, the spec-013 build), four
lines with timestamps — the first timestamped production log the project
has:

```
22:40:57  idle window before first request: 0 block push(es)
22:41:05  security probe: re-sending base read as acknowledged write (response=True)
22:41:10  block push captured: start=1001 qty=45 len=99 crc=OK
          hex=631003e9002d5a00…
22:41:13  push harvest: T-block captured (start=2046 ×3);
          T-window raws: 2046=32765 2047=140 2048=135 2049=135 2050=135 2051=145
```

And the resulting entry's setup failure (the coordinator's first
refresh, HA's retry loop):

```
Setup error, retrying: push stream silent
```

### 0.1 Timeline arithmetic [P — every number below is computed]

| Time | Event | Proof |
|---|---|---|
| ~22:40:52 | connect() completes; ff02 subscribed | connect ≈ 5 s before the idle line (research 007 stage arithmetic) |
| 22:40:52→22:40:57 | **idle window: ZERO pushes on a live, subscribed link** | the idle summary logs at the first request's TX (`_log_idle_summary_once`, code trace) — the line says `0 block push(es)` |
| 22:40:57 | first-read TX `63030007002d3c54` (+ re-sends t+2/4/6); 8.0 s window | the idle line's position = the first TX |
| 22:41:05 | window ends (22:40:57 + 8.0 = 22:41:05 — matches the probe line exactly); **security probe**: the same frame re-sent as an ATT Write Request | REQUEST_TIMEOUT arithmetic, exact |
| 22:41:05→22:41:13 | probe's 8.0 s window | 22:41:13 − 22:41:05 = 8.0 s, exact |
| 22:41:10 | **first push of block 1001 this connect** — 5 s after the probe TX | the `block push captured` WARNING fires on the FIRST push of each start per connect (`_store_push`, code trace) — so **no 1001 push existed before 22:41:10**, i.e. none during the whole first-read window despite its 4 writes |
| 22:41:13 | probe window ends; the push-harvest stage starts and **returns instantly** with 2046 ×3 already in the store | the success line is stamped at stage entry; `harvest_pushes({1001,1046,2001,2046}, 30)` returns instantly only when ALL FOUR wanted starts are already captured (code trace) |
| after | entry created with mode `push`; setup → coordinator first refresh → connect (fresh store) → 30 s zero-TX listen → **nothing** → `UpdateFailed("push stream silent")` → HA setup retry loop | the failure line; `_async_update_push` code trace |

The two 8-second gaps (22:40:57→22:41:05, 22:41:05→22:41:13) match
`const.REQUEST_TIMEOUT` exactly — the timestamps independently confirm
the stage model of specs 007/009/011 [P].

### 0.2 What the four lines prove [P]

1. **The unit pushes NOTHING spontaneously.** Two independent proofs:
   (a) the idle window: zero pushes on a subscribed link before the
   first request; (b) the coordinator's zero-TX cycle: a full 30 s
   request-free listen produced zero frames — `push stream silent`
   — and the setup retry loop repeats that silence every attempt.
2. **The unit ANSWERS when spoken to.** The first block capture
   (start=1001) lands 5 s after the probe's acknowledged write; per the
   owner's full-log reading, the captures of 1001/1046/2001/2046 all
   follow our sends. In every earlier run, every push ever seen arrived
   INSIDE a request window (first census offsets t+0.2-0.4 s) — there
   was never a request-free window until 0.9.0 built one, and it is
   silent.
3. **Our write path works.** The acknowledged write was ATT-accepted
   (the `att-accepted-silent` verdict — no ATT error), and the pushes
   that follow our TXs are the Modbus-engine-level proof that the bytes
   reach the firmware. The transport was never the problem.
4. **The unit answers with block 1001 — while every frame we ever sent
   asks for register 7.** Decode in §1.
5. **The T-window is the right window**: 2046 = 32765 = 0x7FFD, the
   sensor-absent sentinel of T01 Suction (this unit's only absent
   sensor) — the anchor holds in this second independent capture
   (first: research 008 §2.5).

### 0.3 The model change, in one sentence

Spec 013's model — "the unit continuously pushes a free-running stream;
harvest it with zero TX" — is **disproven by its own build**: the
"stream" is a **request-triggered report burst**. The unit answers any
valid incoming Modbus read with a burst of its parameter-dataset block
frames (1001/1046/2001/2046), beginning ~0.2-5 s after the request, and
the burst stops within ≤ 30 s after requests stop. The harvest machinery
spec 013 built is exactly the right LISTEN half; what it lacks is the
TRIGGER — and the 0.9.0 push mode, by never sending one, starves.

---

## 1. Q1 — the frame decoded byte-by-byte: why 1001 when we asked for 7?

### 1.1 The frame [P — header decode; the data tail exists in the owner's full log]

The pasted capture line (truncated hex `631003e9002d5a00…`):
`len=99`, `crc=OK`. Byte-by-byte, using the frame grammar proven in
research 008 §2.1 (and byte-identical to the pinned vector
`631003e9002d5a…563a` of the c8d8260-era capture, `modbus.py` self-test
§10):

| Offset | Bytes | Value | Meaning |
|---|---|---|---|
| 0 | `63` | 0x63 | slave address 99 [P] |
| 1 | `10` | 0x10 | the block-frame function byte — the SAME byte as the vendor app's query-block REQUEST frames (FINDINGS §11.1); NOT an FC03 response byte |
| 2-3 | `03 e9` | **1001** | **the block's start register, echoed by the UNIT** (big-endian) |
| 4-5 | `00 2d` | 45 | registers per block |
| 6 | `5a` | 90 | bytecount = 2×45 — the discriminator against the 8-byte write echo (research 008 §5 Q6) |
| 7-96 | 90 B | — | 45 signed big-endian int16 registers: regs 1001-1045 |
| 97-98 | 2 B LE | — | CRC16-Modbus over bytes 0-96 — logged `crc=OK` |

**The start register of the response is exactly 1001 [P].**

### 1.2 What the frame is NOT [P — elimination, each against the wire shapes]

1. **Not a re-framing/echo of our request.** Our TX was the 8-byte
   `63 03 00 07 00 2d 3c 54` (start=7). An echo or re-frame would carry
   start=7: either our own bytes back (GATT echo), or the Modbus FC10
   write-echo shape `63 10 00 07 00 2d crc` (8 bytes — the shape our
   reassembler's echo matcher knows and has never seen on this link),
   or an FC03 answer `63 03 5a <90> <crc>` (95 bytes). None of the three
   was ever observed — the 99-byte block frame with start=1001 is none
   of them.
2. **Not the answer to a different request.** The ONLY frames our stack
   sent in this session — and in EVERY captured production run — are
   base reads: `63030007002d3c54` (start 7), as Write Commands
   (first read + re-sends) and once as an acknowledged Write Request
   (the probe — the SAME frame bytes, only the ATT ack policy differs).
   No frame asking for 1001 has EVER been sent to this unit: the config
   flow and the coordinator only build `read_registers(7, 45)`, and the
   one code path that sends a query-block frame (`query_block`, the
   "Probe registers" service) was broken until spec 012 and no post-012
   probe run exists in evidence [P — code trace + absence in all logs].
3. **Not the probe's "twin" answering something else.** The probe sent
   the same start-7 frame; if the unit answered the probe's request
   literally, the answer would address 7. It does not — it starts its
   dataset at 1001.

### 1.3 The verdict [P for the observation; A for the mechanism]

**The 1001 block frame IS the unit's response to our base read.** The
mapping from request to response is not register-literal but
dataset-literal: the firmware answers ANY valid incoming read request
with a report of its parameter dataset, in block frames, beginning at
block 1001 — the first block the vendor app's own parameter-page query
asks for (FINDINGS §11.1: `631003e9002dd9e6`). The request's register
address (7) does not select the response's content; the response is the
unit's canonical dataset dump [A-strong for the mechanism — the
observation (bursts follow requests, start at 1001, never address 7) is
[P across six runs].

Timing: the response latency varies — t+0.2-0.4 s in the three earlier
runs' windows, t+5 s after the probe in this run [P — bounds; the
distribution is [V]].

---

## 2. Q2 — is there a 7 ↔ 1001 correspondence? NO [P — inherited + the new angle]

The brief's arithmetic, resolved:

| Observation | Reading |
|---|---|
| `1001 − 7 = 994` | **No pattern.** No offset survives the content test (below). |
| `1046 − 1001 = 45`, `2046 − 2001 = 45` | **The block stride**: 45-register blocks, contiguous series (FINDINGS §11.1). |
| `2001 − 1001 = 1000`, `2046 − 1046 = 1000` | **Two dataset series**: machine settings 1001-1180 (blocks at 1001/1046/1091/1136) and live/parameter data 2001-2090 (blocks at 2001/2046). The 1000 delta separates the series, nothing more [A]. |

The mirror is killed by content, not arithmetic [P — spec 009 §3.2 +
spec 013 §3, restated]:

- **reg 1021 ≠ reg 21**: reg 1021 = 1050 = H04 "Exhaust Temp. High"
  105.0 °C [P — panel match]; reg 21 = `SetAutoTargetTemp` [P apk] — a
  105 °C pool setpoint is absurd. Same number, different registers,
  different datasets.
- Block 1046-1090 mirrored −1000 would map to base 46-90 — beyond the
  base block's end (51).
- The live temps: base 26/27/28 vs pushed 2047/2048/2050 — deltas
  2021/2021/2022, not constant.

And the decisive new fact: **the base block (7-51) is a THIRD dataset,
and the unit returns it under NEITHER addressing** — never an FC03
answer (byte accounting: 12×99=1188, 14×99=1386, 16×99=1584 received
bytes in the three censused windows — zero bytes left for a 95-byte FC03
answer or a 5-byte exception frame), never a start-7 block frame, in
six runs [P — absence]. Register 7 and register 1001 are not "the same
register with two addressings": they are members of different datasets,
and this firmware serves exactly one of them — the one starting at 1001.

---

## 3. Q3 — what does the app REALLY ask? Both planes [P]

The brief asks whether the app "really answers" its own base read or
whether it only ever reads the high blocks. The APK evidence is
unambiguous (FINDINGS §11/§11.1, all frames CRC-verified):

1. **The app sends the base read `63030007002D3C54` FIRST, in EVERY
   session** [P — APK pool constant + spec 010 disassembly (session
   continuation 0xa14d04 writes it right after subscribe + 2 s, before
   any UI interaction; research 013 §4 Q3)]. The claim "the app never
   reads block 7" is **false** [P].
2. **The app ALSO sends the six query-block frames** for the parameter
   pages: 03E9=1001, 0416=1046, 0443=1091, 0470=1136, 07D1=2001,
   07FE=2046 [P — FINDINGS §11.1].

What is [V]: whether the unit ever ANSWERS the app's base read on this
unit (E5 — the HCI snoop, unchanged from specs 009/011/013), and
therefore what the app's home screen is fed by here. What is [P] and is
all the design needs: **the high-block dataset demonstrably flows to
ANY central that sends a request** — our own bursts prove it — and the
pushed blocks ARE the query-block dataset (the block frame is the
natural response form of the app's own query grammar: same FC byte,
same start/qty echo).

---

## 4. Q4 — address, timing or format? None: the response GRAMMAR

- **Address?** Our request is byte-identical to the vendor's own
  session-first frame [P — `modbus.py` self-test pins it]. The response
  proves the request is received and understood — the unit answers it.
- **Timing?** Four re-sends inside one window, acknowledged and
  unacknowledged writes, 8 s and 30 s windows, six runs: the FC03 answer
  never comes, under any timing [P].
- **Format?** CRC-verified against the APK constant [P].

The premise of the question inverts: **our block-7 request DOES respond
— with the block burst.** What never comes is the FC03-shaped answer
(and the Modbus exception a standard slave MUST send for an
unimplemented range — its total absence, with the ATT layer proven
open, means the request never reaches a standard Modbus FC03 handler at
all [P — research 009 §0.2's argument, now with the trigger proof]).
The "problem" is not our request; it is the unit's response grammar:
reads are answered with the parameter-dataset report, and that report
contains no base-block data. **The 1001 "response" IS the response to
our block-7 request.**

---

## 5. The new model — request-triggered report bursts [P]

| # | Statement | Status |
|---|---|---|
| M-1 | The unit never pushes spontaneously: idle window 0 pushes; 30 s zero-TX listen 0 frames (repeated by the setup retry loop) | [P — 0.9.0 log] |
| M-2 | Every push ever observed arrived inside/after a request window, 0.2-5 s after a TX | [P — six runs] |
| M-3 | The report is the parameter dataset: block frames 1001/1046/2001/2046, 45 regs each, CRC-valid | [P — decoded frames, research 008/013] |
| M-4 | The report is triggered by ANY valid request frame — both Write Commands (0b16e0a/1ac276d first-read windows) and the acknowledged Write Request (0.9.0 probe) triggered bursts | [P both types observed; A-strong "any valid frame"] |
| M-5 | The burst's composition varies per window (1001×12+1046×4 in one 8 s window; 1001×3+1046×2+2001×5+2046×4 in another) | [P — the census lines, research 013 F-5] |
| M-6 | The burst stops within ≤ 30 s after requests stop (the coordinator's zero-TX windows are silent) | [P — bounds; exact persistence [V]] |
| M-7 | The base block (7-51) is served by no grammar on this firmware: no FC03 answer, no exception, no start-7 block — ever | [P — absence, six runs] |
| M-8 | The report contains everything the app's parameter view shows: T (live temps), H (settings), R (thresholds) — and NOT the base control data | [P for T-anchor + H04 anchor; A-strong for windows; P-absence for control in 3 of 4 blocks; V for 2001-2045] |

Consequences for the 013 hypothesis ledger: H-7 ("the stream runs
indefinitely while connected") **DISPROVEN**; H-8 ("periodic-ish, not
change-triggered") **DISPROVEN**; the "spontaneous push stream" concept
is replaced by the **request-triggered report** (M-1..M-8). E2 of
research 009 §7 ("pushes before the first TX ⇒ periodic") is answered:
**NOT periodic — request-triggered** [P].

---

## 6. Q5 — the strategy: interrogate the high blocks (trigger + listen)

The brief's two options, verdict:

- **(b) "Understand the 7↔1001 link and ask for the right base block" —
  DEAD.** There is no link (§2) and no base-block delivery under any
  addressing we know (§4). Six runs asked; the answer is always the
  1001+ dataset. Asking differently cannot conjure a dataset the
  firmware does not serve.
- **(a) "Interrogate the high blocks that respond" — CORRECT**, with one
  precision: the *interrogation* needs a request the unit demonstrably
  answers, and the *read* consumes the high blocks from the answer.

**The design: a request-triggered harvest — TRIGGER + LISTEN.**

- **Trigger = the base read `63030007002d3c54`.** Rationale [P]: it is
  the vendor's own session-first frame (byte-parity with the app), it
  is in the standing read allowlist (research 009 §8 — unchanged), it
  is read-only, and it is the frame whose answer we have observed six
  times. Its register address (7) is irrelevant to what comes back —
  what matters is that the unit answers it with the dataset (M-2/M-3).
- **NOT the query-block frames as the trigger** (e.g. `631007fe002d68d2`
  for 2046): no query-block frame has ever been sent to this unit
  (§1.2), so its triggering power is [V] while the base read's is [P].
  The query-block experiment is the named upgrade path (§10 U-6): if the
  probe service's query(2046) yields a DIRECT 2046 block answer, the
  trigger swaps in one line (the frame constant) and the windows
  tighten.
- **Listen = the harvest machinery spec 013 already built and
  production-proved** (`harvest_pushes`, the store, the freshness gate):
  it captured everything once the requests flowed — the 22:41 harvest
  returned instantly with all four starts and 2046 ×3.

What this gives access to (Q7's cartography, §8): the live temperatures
(T-window, entity-grade behind the calibration gate), the H/R settings
(log/diagnostic-grade), block 2001's content (the follow-up's input).
What it cannot give: the base control data (power/mode/setpoint/fault/
firmware) — no home in the report [P-absence in 1001/1046/2046; V for
2001 — the paste of the existing hex lines decides, §8.3]. Control stays
honestly out, exactly as spec 013 §6 ruled.

---

## 7. Q6 — the push mode of spec 013: FIX, not abandon

What the 0.9.0 production run killed, precisely: **the zero-TX poll
cycle** (`_async_update_push` listens, sends nothing, and by M-1 nothing
arrives — the entry setup fails forever in the retry loop). What it
proved works: everything else spec 013 shipped —

- the store + full-hex capture lines (all four starts captured),
- the harvest listen (instant return once the burst had flowed),
- the T-window mapping + calibration gate (the second capture's
  sentinel anchor),
- the connection test end-to-end (the flow's own stages trigger the
  burst; the harvest stage succeeds — 22:41:13).

**The fix: amend the zero-TX invariant.** The push-mode poll cycle
becomes TRIGGER + LISTEN: exactly ONE vendor read frame per cycle (the
base read — allowlisted, read-only), then the existing harvest listen,
then the unchanged freshness gate and snapshot. The connection test
needs NO change (its earlier stages already trigger; the 22:41 run is
the end-to-end proof). The mode value stays `"push"` — the 22:41 entry
already exists in the retry state and recovers with the fixed cycle; no
migration, no schema churn. The harvest stage inside the flow keeps its
zero TX (it inherits the trigger from the flow's earlier stages).

Design detail verdicts:
- **Trigger write type**: the client's resolved default —
  write-without-response, app parity [P — both write types triggered
  bursts across runs (M-4); without-response is the vendor's own mode,
  research 010 §2.3]. The acknowledged variant (`force_response=True`)
  is the named fallback lever if the long-run ever shows flaky
  triggers — a one-line change, not an option.
- **Re-sends**: OFF for the poll trigger (single shot). The burst
  outlasts the TX (M-5/M-6); the failure ladder recycles the link; the
  re-send bounds stay available as a one-line change.
- **Failure message**: `push stream silent` → `"push stream silent
  after trigger"` — the new literal is a STRONGER verdict (silence even
  after a proven trigger) and must not be conflated with 0.9.0's
  listen-only silence. The 013 test suite re-pins the literal (a cited
  deviation, the 013 idle-summary precedent).
- **The revival path**: if the trigger read ever COMPLETES (an FC03
  answer — the post-pairing/E5 world), that is decisive evidence: log
  once per session at WARNING and build the snapshot from the base
  answer merged with the pushed extras (contract 013 §4.2: base wins;
  the mode stays — re-evaluation is a future spec's decision, honored).

---

## 8. Q7 — the useful data: cartography confirmed

### 8.1 The T-window (2046-2090) — second independent capture [P]

| reg | capture 1 (c8d8260 era, research 008 §2.5) | capture 2 (22:41:13) | reading |
|---|---|---|---|
| 2046 | 32765 (0x7FFD) | 32765 | **T01 Suction sentinel — the anchor holds twice** [P] |
| 2047 | 140 → 145 (live change) | 140 | T02 candidate — live-proven register [P liveness] |
| 2048 | 140 | 135 | T03 candidate [A] |
| 2049 | 140 | 135 | T04 candidate [A] |
| 2050 | 135 | 135 | T05 candidate [A] |
| 2051 | 150 | 145 | T06 candidate [A] |

The window is confirmed; the values drift plausibly between captures
(different moments, idle unit) [A]. The member order stays [A] until the
calibration run (quickstart 013 §2, unchanged) — the mapping and the
`PUSH_TEMP_CONFIRMED` gate are untouched by this spec.

### 8.2 The settings blocks — inherited [A-strong]

- 1001-1045: H-family window 1018-1023 (H04=105.0 at reg 1021 [P panel
  match]) + unresolved flags 1012/1037-1040.
- 1046-1090: the R-family ÷10 threshold ladder at 1046-1060 + 1068.
- Exposure unchanged: log/diagnostic only, never entities, never
  writable (spec 003 §6).

### 8.3 Block 2001-2045 — the capture ALREADY EXISTS in the owner's log [P — code-trace arithmetic]

The harvest stage returned instantly at 22:41:13 (§0.1), and
`harvest_pushes({1001, 1046, 2001, 2046}, …)` returns instantly only
when ALL FOUR wanted starts are in the store. Therefore the
first-push-per-start WARNING lines — each carrying the FULL 99-byte hex
— exist in the complete 22:41 log for **all four blocks**, including
the never-captured **2001-2045** (spec 013's U-1). One paste closes the
project's biggest remaining content gap without any new run
(quickstart §0). Expected consistency check for the paste: the 1001
hex should still show reg 1021 = 1050 (H04) if the settings are static
[V until pasted].

### 8.4 Control data — still unreachable [P-absence / V for 2001]

Power (8), boost (11), mode (13), setpoint (14), per-mode setpoints
(19-21), bounds (22-25), fault (47), firmware (48-51): no home in the
decoded blocks 1001/1046/2046 [P]; block 2001 is the honest exception
[V — §8.3's paste decides]. Until a home is found AND pinned, push mode
creates no climate, no fault, no firmware entity (unchanged from
spec 013 §6 — optimistic state forbidden).

---

## 9. The seven questions — one-line verdicts

| # | Question | Verdict |
|---|---|---|
| 1 | Why does the answer arrive for 1001 when we ask for 7? | The unit answers ANY read with its parameter-dataset report (block frames from 1001); the request's address does not select the content [P obs / A mech] |
| 2 | Is there a 7↔1001 correspondence? | NO — different datasets; the base block is served by no grammar on this firmware [P] |
| 3 | What does the app ask? | BOTH: the base read first every session [P] AND the six query-block frames [P]; whether the unit answers the app's base read here is E5 [V] |
| 4 | Address, timing or format? | None — the request is byte-perfect and ANSWERED; the response grammar (block report) never carries base data [P] |
| 5 | The right strategy? | (a) interrogate the high blocks: TRIGGER (the proven base read) + LISTEN (the 013 harvest) [D] |
| 6 | Push mode: fix or abandon? | FIX: replace the zero-TX cycle with trigger+listen; flow, store, harvest, mapping, gate unchanged; mode value stays "push" [D] |
| 7 | Is the useful data in the high blocks? | YES: T-window confirmed (2nd capture), H/R windows inherited, 2001's hex already in the log; control data still absent [P/A/V as tagged] |

---

## 10. Unproven ledger — and the experiment that closes each

| # | Gap | Experiment | Where |
|---|---|---|---|
| U-1' | Block 2001-2045 content (S/O/D/P homes? control mirrors?) | paste the four `block push captured` hex lines from the existing 22:41 log — no new run needed | quickstart §0 |
| U-2 | T-window member order | the calibration run (unchanged quickstart 013 §2) | quickstart §2 |
| U-6' | Does a query-block frame (e.g. query 2046) get a DIRECT block answer? | the "Probe registers" service, block=2046 — decides the Phase-2 trigger upgrade | quickstart §3 |
| U-3' | Long-run burst behavior WITH the trigger cycle (does every cycle's trigger wake the unit? months of cycles?) | the 0.10.0 cycle's own logs + the `push_stream` diagnostic; the new failure literal separates "trigger didn't wake" from 0.9.0's listen-only silence | quickstart §5 |
| U-7 | The burst's exact trigger semantics (any frame? reads only?) and its persistence after the last request | the 0.10.0 per-cycle census lines (latency + composition accumulate across cycles) | production logs |
| U-4 | The app's session on THIS unit (does the phone's base read get answered?) | E5 HCI snoop — unchanged | quickstart §4 |
| U-5 | Request-path revival (pairing) | unchanged Track A; the revival path (spec 014 FR-2) makes a revival OBSERVABLE in production | quickstart §4 |
| U-8 | H/R member encodings (H02 0=C? H06 enum? P-family 1037-1040?) | same captures as U-1'/U-2 | data-model updates |

---

## 11. Reproduction

```bash
# the frame grammar + CRC of §1 (the pinned 1001 vector):
python3 custom_components/aqua_temp_ble/modbus.py   # self-test §10

# §0.1 timeline arithmetic (verify by hand):
#   22:41:05 - 22:40:57 = 8.0 s  == const.REQUEST_TIMEOUT (first-read window)
#   22:41:13 - 22:41:05 = 8.0 s  == const.REQUEST_TIMEOUT (probe window)
#   22:41:10 - 22:41:05 = 5.0 s  (probe TX -> first 1001 push)
# byte accounting (zero bytes for FC03/exception in every window):
#   12 x 99 = 1188; 14 x 99 = 1386; 16 x 99 = 1584  (research 009 §0.2 method)
```