# Spec 009 research — the base-block request: why the unit never answers it

Date: 2026-10-11
Evidence base: the latest production connection-test log (build ce41aa5,
the spec-008 census build) against `08:06:A6:C0:94:1C` (BlueNRG-C0941C);
the c8d8260 production log (5 pasted drop lines) decoded in spec 008;
the vendor-APK artifacts (FINDINGS.md, research 006 §2); the shipped code
(`modbus.py`, `aqua_client.py`, `registers.py`, `coordinator.py`,
`const.py`, `config_flow.py` @ ce41aa5).

Status legend: **[P]** PROVEN (calculated/verified from artifacts),
**[V]** TO-VERIFY (one live run decides), **[A]** ASSUMED (reasonable
default), **[D]** DECISION. Every claim is tagged.

---

## 0. The production fact, decoded

The log this spec answers:

```
connection test to 08:06:A6:C0:94:1C: no response: AquaTempTimeoutError:
no matching response within 8.0s
(received 12 frame(s) / 1188 bytes;
 blocks seen: 1001, 1046, 2001, 2046;
 none for start 7)
at stage first-read after 13.0 s (budget 70 s)
```

### 0.1 Stage arithmetic [P]

13.0 s = resolve+connect+notify+write-char ≈ 3.0 s (spec 007 connect
summary; c8d8260 measured ≈ 2.9 s connect) + settle 2.0 s
(`const.SETTLE_AFTER_SUBSCRIBE`) + request window 8.0 s
(`const.REQUEST_TIMEOUT`). The connect, the GATT enumeration, the ff02
subscription, the write-character resolution and the settle ALL succeeded.
The test died waiting for the answer to the base read — again.

### 0.2 The byte-accounting proof — the most important number in the log [P]

`received 12 frame(s) / 1188 bytes`. Each block frame is 99 bytes
(`63 10 start:2 qty:2 bytecount data:90 crc:2`, research 008 §2.1).
**12 × 99 = 1188 exactly.**

Every single byte received during the 8 s window reassembled into one of
the twelve complete, CRC-valid block frames the census counted. The
accounting leaves **zero bytes** for anything else. Therefore, within the
request window the unit sent:

- **no FC03 answer** (an answer would add 95 bytes: `63 03 5A <90> <crc>`);
- **no Modbus exception frame** (5 bytes: `63 83 <code> <crc>`) — the
  reply a standard Modbus slave MUST give to a well-formed request for an
  unimplemented register range;
- **no partial, corrupt or foreign bytes** (they would break the exact
  1188 arithmetic — a CRC-invalid frame is counted in `_req_rx_bytes` but
  not in `_req_frame_count`, so any garbage would show up as
  `bytes ≠ 99 × frames`).

**The unit's total reaction to our base read was: nothing. It kept
pushing its block cycle at the same cadence as if we had said nothing.**
This one line kills more hypotheses than any experiment could: the
response does not "arrive mangled" (spec 008 fixed that), it does not
arrive at all.

### 0.3 What the census upgraded vs spec 008 — the cycle model changes

Spec 008 §2.7 guessed the twelve drops were "2 full cycles × 6 high
blocks" (the app's canonical six: 1001, 1046, 1091, 1136, 2001, 2046 —
FINDINGS §11.1). **The census disproves that guess** [P]: only FOUR
distinct starts appear among the twelve frames:

```
1001, 1046, 2001, 2046
```

1091 and 1136 are **never pushed**. The consistent model is a **4-block
cycle `[1001, 1046, 2001, 2046]` repeated 3× in 8 s** [SUPPOSED — neat fit
(3×4=12), but the message prints sorted unique starts, not per-start
counts; an uneven split like 4/3/3/2 also sums to 12. FR-1 adds per-start
counts; the probe measures the true cadence]. Cadence estimate: one frame
every ≈ 0.67 s, one cycle every ≈ 2.67 s [V — needs timestamps; log
timestamps in the owner's paste are second-granular].

Corroboration from the c8d8260 run [P]: the five pasted drop lines were
starts 1001, 1046, 2001, 2046, 2046 — the same four starts, and NO
1091/1136 line among any of the 12 drops of that run either. Both
production windows are fully explained by the 4-block model.

### 0.4 What we sent, when [P]

Our session order (code trace, aqua_client.connect + config_flow
_async_test): resolve → connect → enumerate GATT (INFO) → resolve notify
(ff02) → CCCD subscribe → resolve write char (ff03, property-gated) →
**settle 2.0 s** → TX `63030007002d3c54` on ff03, write-without-response →
8.0 s window. The frame is byte-identical to the vendor app's own
first-frame-of-session constant [P — FINDINGS §11, CRC-verified in the
modbus.py self-test]. Exactly one frame was sent; nothing else.

---

## 1. Q1 — Temporal analysis: why does the block-7 request get no answer?

### 1.1 When does our request go out, relative to the pushes? [P/V]

- Our TX: ≈ 5.0 s after connect() entry (≈ 3.0 s resolve+connect, ≈ 0.1 s
  notify+char, 2.0 s settle) — [P arithmetic].
- Pushes DURING the 8 s request window: 12 frames [P census].
- Pushes during the 2.0 s settle (BEFORE our TX): **unknown from the
  pasted excerpt** [V]. The idle census logs the FIRST idle push per
  connect at INFO (`block push observed while idle: start=…`) and the
  rest at DEBUG — so the full log answers this by one grep (quickstart
  §0): INFO idle line present ⇒ pushes precede our TX (stream is
  periodic or subscribe-triggered); absent ⇒ push onset came later than
  2 s after subscribe (consistent with TX-triggered or late-start
  periodic). **This single line is decisive for periodic-vs-triggered
  and it is already in the existing log.**
- The 12 frames are evenly spread across the window (1188 bytes with 12
  complete frames and no overflow error implies steady arrival; the
  0.67 s cadence estimate) [V exact gaps].

### 1.2 Can the request be "drowned" by the push stream? [P: no, at our layer]

At the GATT layer, no: notifications (unit→central, ff02) and writes
(central→unit, ff03) are independent directions. At OUR receive layer,
no, three independent ways [P — code trace + the census itself]:

1. Each complete frame is consumed from `_rx` at its computed boundary
   (`del self._rx[:expected]` in the `_on_notify` loop) — the buffer
   never accumulates more than ~one frame plus the current partial.
2. The census proves 12 complete frames were extracted; an interleaved
   start-7 answer would have been extracted the same way (the boundary
   loop runs per frame, and `accept_block` matching would have completed
   the request — AC-2 of spec 008 pins exactly this).
3. `MAX_RX_BUFFER = 512` was never exceeded (a >512 buffer fails the
   request with "response too long" — absent from the log).

So the drowning hypothesis is dead on our side. **The only layer where
"drowning" remains possible is inside the unit** (e.g. a half-duplex
serial bridge that drops a request whose bytes arrive while the unit is
mid-push, or an engine that only polls its RX path between pushes) —
[V, cause C5 below]. If that is the mechanism, re-sending eventually
lands in a gap — which is what the bounded re-send (FR-2) and probe
Phase P test.

### 1.3 Could we have asked too early, or once where the app asks many times? [V]

Our settle (2.0 s after CCCD) is vendor parity [P — research 006 §2
step 4: the app's 2 s Duration sits in the subscribe→read slice]. But
the exact ROLE of that 2 s constant is [V]: settle delay **or** a
re-send/poll cadence — "a settle/poll cadence" (research 006 §2 step 4).
If the app re-sends the base read every 2 s until answered, a single
shot from us is not parity, it is a subset. Our production behaviour is
1 TX per window; the app's retry counts are invisible to static pool
analysis (research 006 §2.2 [V]). Two production runs, one TX each,
zero reaction — consistent both with "write never arrives" and with
"unit answers only the Nth request / after its cycle". The cheap
discriminator: send more than once (FR-2 in production, probe Phase P in
isolation).

### 1.4 The late-answer sub-hypothesis [V]

If the unit's answer latency exceeded 8 s, the window would expire just
before the answer. The byte accounting (§0.2) only covers the window
itself; a >8 s-late answer would land… where? In the connection test,
nowhere (the client disconnects). In steady coordinator polling, it
would land while the NEXT request is pending — and being a matching
FC03 shape it would complete THAT request (one-cycle lag). No
coordinator run has ever completed a read (no success log exists), and
both windows show zero FC03 bytes, so there is no positive evidence for
a late answer — but it is not excluded either. Probe Phase B listens
15 s after a single TX; Phase P polls for 30+ s: a late answer cannot
hide there.

---

## 2. Q2 — Does the vendor app do anything we do not?

### 2.1 What is PROVEN about the app's session [P — research 006 §2]

| Step | App behaviour | Ours (ce41aa5) | Parity |
|---|---|---|---|
| 1 | connect (20 s timeout, retry loop) | connect (brc, 2×20 s) | ✅ |
| 2 | discover services, log ff01/ff02/ff03 | enumerate GATT (INFO) | ✅ |
| 3 | subscribe ff02 (CCCD) | subscribe ff02 | ✅ |
| 4 | **2 s Duration in the subscribe→read slice** (role [V]) | settle 2.0 s, then ONE read | ⚠️ partial — if the 2 s is a poll cadence, the app may re-send |
| 5 | **send `63030007002D` first** (the first Modbus frame of every session) | send `63030007002d3c54` first | ✅ byte-identical |
| 6 | reassemble; block queries ON DEMAND (parameter pages) | query_block for probe only | ✅ (unused in flow) |
| 7 | write path: **write-without-response** | write-without-response (property-gated) | ✅ |
| 8 | no handshake/unlock/activation frame — absent by five independent proofs | none | ✅ |

There is **no** activation handshake, "start streaming" command, or
sequence number in the app [P — research 006 §8, absence proofs]. The
app does NOT wait for the pushes to finish before asking — no such
synchronisation exists anywhere in the pool. The app reads the base
block FIRST, in every session, before any high block [P — the frame
constant sits in the session-manager slice].

### 2.2 The GATT-layout divergence — the app's table is NOT our table [P]

| | App-developer unit (discovery logs, research 006 §2 step 2) | Owner's unit (c8d8260 production enumeration) |
|---|---|---|
| ff01 | **characteristic** (handle 2214) | **SERVICE** (not a char) |
| ff02 | characteristic (handle 2216), notify | characteristic, notify — pushes arrive [P] |
| ff03 | characteristic (handle 2215), role "unknown/secondary" | characteristic, **resolved write char** |

Two firmware generations with the same UUID family. Consequences:

- The app's write-target logic on a ff01-as-SERVICE unit is **unknown**
  [V]. If the app hard-codes char ff01 it cannot drive our unit at all;
  if it picks "first writable char of the vendor service" it lands on
  ff03 exactly like us. **The app has never been observed working on
  THIS unit** — see §2.4.
- Which resolution path OUR code took on this unit is in the existing
  log [P that the line exists; V its content, not yet pasted]: INFO
  `using write characteristic 0000ff03-… (properties=[…])` (candidate
  path — ff03 advertised a write property) vs WARNING `… does not
  advertise a write property; using it as the last-resort candidate`
  (last-resort path — the ATT write command may be silently dropped,
  §5 Q4). One grep decides (quickstart §0).

### 2.3 What the app does that we MIGHT be missing — the honest list [V]

1. **Re-send the base read** on the 2 s cadence until answered (the
   Duration's role is unproven) — tested by FR-2 + probe Phase P.
2. **Write to a different characteristic** on this unit's layout —
   decided by the enumeration grep + probe Phase Q (any reaction?) +
   E5.
3. **Use write-WITH-response** on this unit — the app's write-type
   proof (`withoutResponse`) comes from the app's own write path
   (research 006 §2.1 [P]) but on the app's unit; our unit's firmware
   may require Write Requests. Note: with without-response there is NO
   ATT error path — a non-accepting server drops the packet silently
   (§5 Q4), so "our write succeeded" is unobservable. Probe Phase W
   (write-with-response) makes the ATT layer speak.
4. **Nothing else** — no timer, no unlock, no order trick exists in the
   pool [P — absence proofs].

### 2.4 The unexamined premise: does the app even work on THIS unit? [V]

The brief's reasoning — "the app sends exactly our frame, therefore the
unit must answer it" — silently assumes the vendor app drives the
owner's unit successfully. **No artifact proves that**: no app
screenshot of this unit's values, no captured session, no owner
statement in the artifacts. FINDINGS §2 says the unit "is driven over
BLE directly" — an assumption about intent, not an observation. If the
app ALSO gets silence on this unit (e.g. the app's parameter pages are
fed by the pushes; the home screen shows stale/empty data), then
push-only mode (Q5) becomes the leading theory and the strategy pivots
(§9). **E5 (one HCI snoop of a vendor session on THIS unit) settles
the premise itself** — it is the single most decisive experiment left.

---

## 3. Q3 — Is register 7 the right block? Register 7 vs register 1007

### 3.1 Two request grammars exist [P]

- Base dataset: FC03 `63 03 0007 002D` — the app's first frame [P APK].
- High datasets: 8-byte query-block frames `63 10 start qty CRC` for
  starts 03E9/0416/0443/0470/07D1/07FE [P APK, all CRC-verified].

If register 7 and register 1007 were the same block with two
addressings, the app would not need two different function codes to
read them. The protocols are separate datasets, not one address space
with an offset.

### 3.2 The mirror hypothesis contradicted by the data [P]

If high block N meant base register N−1000, then:

- pushed reg 1021 = 1050 = 105.0 °C would be base reg 21 =
  `SetAutoTargetTemp` — a 105 °C pool setpoint is absurd; the panel-
  observed **H04 exhaust-high threshold is 105.0 °C** [P panel, FINDINGS
  §12] — block 1001+ is the H-family machine-settings area, not a
  setpoint mirror;
- pushed block 1046-1090 would map to base 46-90 — beyond the base
  block's end (51), yet the block is a dense ladder of ÷10 regulation
  thresholds (29.0/15.0/40.0/27.0/10.0/35.0/-7.0/13.0/4.0, research 008
  §2.3), not the sparse base tail (Fault1 + firmware versions);
- the pushed starts are **exactly four of the app's six canonical
  query-block starts** [P] — our unit speaks the app's canonical
  high-block numbering, which argues the app's canonical BASE numbering
  (7..51) is equally canonical on this firmware [A, strong].

**Verdict: FC03 start=7 count=45 is the right request** [P]. Register
1007 is not the base block under another address; it lives inside
blocks 1001-1045/1046-1090 whose decoded content is machine settings
(research 008 §2.2/§2.3). No block-numbering change is justified.

---

## 4. Q4 — Is our request well-formed and well-sent?

- **Frame bytes**: `63030007002d3c54`, byte-identical to the APK pool
  constant; CRC16-Modbus `0x543C` wire `3C 54` — verified by
  calculation and pinned in the modbus.py self-test [P]. Nothing to fix.
- **Characteristic**: ff03 — the write char resolved on this unit
  (c8d8260: ff01 is a service here; ff03 carries the write). Which
  resolution path ran, and ff03's advertised properties, are in the
  existing production log [V — grep, quickstart §0].
- **Write mode**: property-gated; the task brief and the shipped
  behaviour say write-**without**-response (vendor parity, research 006
  §2.1 [P]). With without-response:
  - there is **no ATT acknowledgement of any kind** — a Write Command
    is fire-and-forget; BlueZ returning without error only means the
    local stack queued/transmitted the PDU [P — ATT semantics];
  - a server whose characteristic does not accept the write (property
    not set, wrong handle, firmware quirk) drops it **silently** — no
    error reaches us. **"The write succeeded" is not a fact we have;
    it is not even observable on this path.** [P mechanism]
- **Is an ack expected?** No — the vendor app expects none either; the
  Modbus response IS the ack [P]. None came (§0.2).

Conclusion: the request is well-formed beyond doubt; whether it is
well-DELIVERED is exactly the open question, and the write path is
unobservable by design. That is why the instrumentation (probe Phase
Q/W, enumeration grep, E5) targets delivery, not formatting.

---

## 5. Q5 — Is the unit in "push" mode, deaf to requests?

Hypothesis: BLE-only units stream their datasets permanently and ignore
incoming FC03 reads; the app (on such units) would read everything from
the pushes.

- **Evidence FOR** [P where tagged]: the unit pushes 4 blocks
  spontaneously (at least from subscribe onward — the idle census line
  in the full log will pin the onset, §1.1); it answered our well-formed
  FC03 with total silence including no exception frame (§0.2) — a
  standard Modbus slave MUST answer or except; silence is the signature
  of a request path that is off or unattached.
- **Evidence AGAINST**: the app's session sends the base read first in
  EVERY session [P] and its RM decoder expects power/mode/setpoint
  fields (FINDINGS §9) that no pushed block visibly carries (§7); the
  app's parameter pages query 1091/1136 [P] which the unit NEVER pushes
  [P] — if pages work on this unit, queries must elicit answers;
  the app's whole UI is request/response-shaped.
- **The honest gap**: we have never observed the app on THIS unit
  (§2.4). If the app's requests also fall on deaf ears here, "push mode"
  is not a hypothesis any more, it is the design.

Discriminators: probe Phase Q (does a block QUERY on ff03 provoke an
immediate out-of-cycle push? — if yes, requests DO reach the engine and
push-only is dead), Phase W (write-with-response makes the ATT layer
report), and above all E5 (the app's session on this unit). [V]

---

## 6. Q6 — The exact twelve frames, and whether base data hides among them

- **Distinct starts [P]: 1001, 1046, 2001, 2046** — four blocks × 45
  registers: 1001-1045, 1046-1090, 2001-2045, 2046-2090. The base
  register numbers 7-51 appear in NONE of them [P — numbering].
- **Counts per start [V]**: 3×4 is the neat model (§0.3); per-start
  counts are not in the message (FR-1 adds them). Note from the c8d8260
  paste: two 2046 pushes with exactly one register changed between them
  (reg 2047: 14.0 → 14.5, research 008 §2.5 [P]) — consistent with
  periodic pushes catching a live change, or with a change-triggered
  re-push [V; probe timestamps decide].
- **Do the pushed blocks contain the base data under other numbers?**
  Content analysis of the decoded frames (research 008 §2.2/§2.3/§2.5):
  - 1001-1045: sparse flags + H04=105.0 — machine settings, static [P];
  - 1046-1090: dense ÷10 threshold ladder — regulation limits, static [P];
  - 2001-2045: data unknown [V — the paste was underdetermined];
  - 2046-2090: sentinel 0x7FFD at 2046, LIVE 14.0→14.5 at 2047, then
    14.0/14.0/13.5/15.0 and zeros [P values, V semantics]. At least ONE
    live value lives in the 2xxx family — a ~14 °C reading in October is
    plausible for pool water.
  - What the base block would show (registers.py [P]): power flag, mode
    enum, setpoint (~28-30 for a pool), THREE live temps, fault code,
    firmware. **No pushed block shows that shape.** The 2001 block's
    visible islands (1, 13, 168, 64 — 16.8? °C) leave room for live
    temps [V], but power/mode/setpoint have no observed home anywhere.
- **Verdict**: the base block's numbers never appear [P]; its VALUES are
  not visibly mirrored either, though the 2xxx family carries at least
  one live value [P] whose semantics are unknown [V]. The 12 frames do
  not contain a readable base block under another name.

---

## 7. Cause ranking — why no answer, most probable first

| # | Cause | For | Against | Discriminator (→ FR/experiment) |
|---|---|---|---|---|
| **C1** | **Our write never reaches the unit's Modbus engine** — ff03 is not the request pipe on this firmware, or the write-command is silently dropped (§4) | Total silence incl. NO exception frame — a slave that received a valid FC03 must answer or except [P]; ff03's role was never confirmed on any unit [P FINDINGS §11.3]; without-response failures are silent by design [P]; this unit's GATT layout already diverged from the app's once (ff01 service) [P] | The pushes prove the module is alive; nothing yet proves ff03 dead | Probe Phase Q (query 1001 — any reaction?), Phase W (write-with-response — ATT errors surface), enumeration grep (ff03 properties), E5 |
| **C2** | **The unit answers only after repeated requests / longer latency** — the app's 2 s Duration may be a poll cadence [V]; our single shot is a subset of app parity | App's 2 s constant sits in the read slice [P]; app has retry loops everywhere [P]; 2/2 production runs silent does not exclude "answers the Nth request" | No positive evidence of ANY answer yet | FR-2 (re-send ×3 in the test, production evidence), probe Phase B (15 s window), Phase P (poll 30 s) |
| **C5** | **Request-during-push collision** (unit's internal bridge half-duplex; request bytes arrive while the unit is mid-push and are dropped) | Would look EXACTLY like C1/C2 from outside; pushes occupy ~15-45 % of airtime at 0.67 s cadence [V frame duration] | Pure [V]; no artifact hints at it | Subsumed by C2's re-send test: polling eventually lands in a gap |
| **C3** | **Push-only firmware** (reads are never answered; data only flows via pushes) | Explains total silence + pushes neatly; app's operability on THIS unit unproven (§2.4) | App's UI is request-shaped [P]; 1091/1136 never pushed but app queries them [P] | Phase Q (queries answered?) + E5 (what the app's session really looks like here) |
| **C6** | **The answer goes to a characteristic we do not subscribe** (ff03 also notifies?) | GATT layouts on this unit already surprised us once [P] | App subscribes ff02 only [P]; pushes (unit-initiated) come on ff02 | Enumeration grep + probe `--subscribe-all` |
| **C4** | **Wrong block address on this firmware** (reg 7 absent here) | None — pure conjecture | App sends start=7 unconditionally [P]; unit speaks canonical high-block numbering [P]; a slave would answer an exception, not silence [P] | Only if everything else fails: Phase E's query shapes |

Ranking rationale [A]: C1 and C2 are the only causes consistent with ALL
proven facts (silence, no exception, pushes uninterrupted, request
byte-perfect); C5 is a mechanism inside C2's observable space; C3
requires the app to not work on this unit (possible, unproven §2.4);
C6 and C4 are contradicted by secondary evidence. **The measurement plan
is built so that ONE probe run + one log grep separates C1/C2/C5 from
C3/C6** — and E5 ends the whole table.

---

## 8. Frame inventory — what may ever be sent (allowlist discipline)

**In the integration [P — vendor-proven, unchanged]:**

| Frame | Hex | Status |
|---|---|---|
| Base read | `63030007002d3c54` | vendor first frame [P] — the ONLY read the integration sends |
| FC10 writes | `6310 addr:2 0001 02 val:2 CRC` (H1) | vendor write shape [P]; addresses restricted to {8,13,14}(+19-21 switch) by spec 003 §6 |
| Block queries (probe tool only) | `631003e9002dd9e6` (1001), `63100416002de8a2` (1046), `63100443002df8b2` (1091), `63100470002d08bd` (1136), `631007d1002d591b` (2001), `631007fe002d68d2` (2046) | the app's six parameter-page queries [P], all CRC-verified |

**Probe-only, experimental [D — new in this spec, evidence-gated]:**

| Frame | Hex (CRC verified by calculation) | Purpose | Gate |
|---|---|---|---|
| Block-query shape for start 7 | `63100007002db997` | if the engine answers query-shaped requests but not FC03, this asks the BASE block in the language the unit demonstrably speaks | Phase E only, `--experimental` flag, AFTER Phase Q proves the engine reacts to queries on ff03. Never-seen frame: a sloppy parser could read it as a truncated FC10 write (start 7, qty 45, bytecount missing) — Modbus would discard it awaiting more bytes [A]; the gate keeps the risk contained to a developer-run probe |
| FC03 read at start 1001 | `630303e9002d5c25` | tests whether the unit's FC03 handler works AT ALL (any range) — isolates "FC03 off engine-wide" from "reg 7 absent" | Phase E only, `--experimental`; read-only semantics, but never sent by the app |

**Never:** any other FC, any broadcast address, any write outside the
allowlist, any config-mode frame. The `631004f3…` time-sync frame stays
out (research 006 §5: manual settings feature, not a session step).

---

## 9. The "base block is unnecessary" track — evaluated

**The claim**: decode the pushed blocks and never ask for register 7.

**What the integration needs** (spec 003 / registers.py [P]): power,
mode, current setpoint (read AND write — writes target base regs
8/13/14), min/max targets, three live temps, fault code. Plus the
write-verification read-back (coordinator refresh after every write).

**What the pushes provably contain**: 180 registers of machine settings
(1001-1090), 90 registers of unknown content (2001-2045), 45 registers
with ≥1 live value (2046-2090). Base numbering absent [P §6]; base
values not visibly mirrored [P]; power/mode/setpoint without any
observed home [P absence].

**Verdict: REJECTED as a replacement; RETAINED as Track B.**
- Rejected because: control (the project's core) lives in the base
  block on the app's own protocol [P]; no pushed block shows
  power/mode/setpoint shapes; adopting pushes alone would deliver at
  best one uncalibrated live temperature and zero control — not the
  integration.
- Retained as **Track B** (harvest + calibrate the push stream) because:
  pushes are already flowing, CRC-valid, and free (no request needed);
  reg 2047 proves live data exists there [P]; IF E5 shows the app
  itself never gets an FC03 answer on this unit, Track B becomes the
  primary read plane (with semantics calibrated against the panel and
  the app's parameter pages, FINDINGS §12.2's logcat flow).
- **Track A stays primary**: make the unit answer requests (or prove
  the app's answer source on this unit). The two tracks share ALL the
  instrumentation of this spec — nothing is wasted whichever way E5
  points.

---

## 10. What remains unproven — and the experiment that closes each

| # | Gap | Experiment | Where |
|---|---|---|---|
| E1 | Start-7 block in the stream (8 s window: NO [P]); in a longer window | probe Phase B/P listen 15-30 s | quickstart §2 |
| E2 | Push onset vs our TX (periodic / subscribe-triggered / request-triggered); true cadence & per-start counts | grep the idle census line (existing log!) + probe Phase L | quickstart §0/§2 |
| E3 | FC03 answered late / on the Nth request | FR-2 re-send (production) + probe Phase B/P | quickstart §1/§2 |
| E4 | ff03's advertised properties; which write-resolution path ran; is our write delivered at all | grep `GATT enumeration` / `using write characteristic` / `last-resort` in the existing log; probe Phase Q/W | quickstart §0/§2 |
| E5 | **The vendor app's session on THIS unit** (premise of §2.4; write char, write type, request set, answer shapes, where base data comes from) | Android HCI snoop (`btsnoop_hci.log`), Wireshark `btatt` | quickstart §3 |
| E6 | Pushed-block register semantics (2047 live; 2001 unknown) | probe decode + panel/app calibration (spec 003 flow) | follow-up spec |
| E7 | Push cycle order & whether re-pushes are change-triggered (the 2047 14.0→14.5 double) | probe Phase L timestamps | quickstart §2 |
| E8 | Does the app re-send the base read, and how often (the 2 s Duration's role) | E5 capture; DEX smali of the session manager (Windows node) if needed | quickstart §3 |
| E9 | Does ANY writable char on this unit accept our frames (ff03 or another) | probe `--subscribe-all` + Phase W; enumeration | quickstart §2 |

---

## 11. Decision matrix — evidence → next action (normative in contracts/session-request.md)

| Observed result | Meaning | Next spec's action |
|---|---|---|
| Phase Q: query 1001 answered immediately (out-of-cycle push) | our writes DO reach the engine; FC03 specifically is ignored/unanswered | Phase E (query-shaped base read `63100007002db997`); if answered with a start-7 block frame → adopt query-shaped base reads in the integration (spec 010 + tests) |
| Phase Q: no reaction, but re-send/poll (FR-2 / Phase P) eventually yields ANY answer | engine answers only after retries / longer window | adopt app-parity polling for the first read (spec 010) |
| Phase W: write-with-response returns an ATT error on ff03 | the write pipe is refused → C1 confirmed | re-resolve write char from the enumeration (candidate/ff03 order), retry, fix in spec 010 |
| Nothing reacts on ff03, ever; enumeration shows another writable char | wrong pipe (C1) | retarget the write char; rerun probe |
| Nothing reacts on ANY pipe; E5 shows the app's session works on this unit | we diverge from the app in some observable way | diff our session against E5's capture line by line (char, write type, timing, frames) |
| Nothing reacts anywhere AND E5 shows the app ALSO gets no FC03 answer on this unit (its UI fed by pushes) | push-only firmware (C3) — Track B becomes primary | spec 010: harvest + decode the push stream; calibrate 2xxx semantics; control question reopens with E5's write evidence |

---

## 12. Reproduction

- The arithmetic and frame tables of §0/§6 re-verify with
  `python3 specs/008-first-read-decode/decode_frames.py` (vectors) and
  `python3 custom_components/aqua_temp_ble/modbus.py` (self-test).
- The experimental-frame CRCs of §8 recompute with the reference
  `crc16_modbus` (FINDINGS §11): `63100007002d` → `0x97B9` → wire
  `b9 97`; `630303e9002d` → `0x255C` → wire `5c 25` [P, verified].