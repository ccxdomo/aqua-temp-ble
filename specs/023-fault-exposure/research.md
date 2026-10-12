# Spec 023 — Research: the proof (register 2074) and the seven questions

Date: 2026-10-12
Sources: the owner's 2026-10-12 E03 run (the captures relayed with
this spec's commission), the repo record (specs 020/022, the archived
specs), the code @ main 84494aa (manifest 0.16.0), and a fresh dig
into the surviving disassembly artifacts at `/tmp/blutter_out/`
(`pp.txt`, the full `asm/` tree — present and readable; the APK
extraction at `/tmp/flutterapp/` still carries only `lib/`, so the
fault-table assets remain absent).

Confidence tags (the repo convention): **[P]** proven, **[A]**
assumed, **[V]** open.

---

## Part 1 — The evidence: the owner's E03 diff-revert (Q1)

### 1.1 What the owner did (2026-10-12, the 0.22 build)

The unit is un-plumbed. The owner captured the healthy blocks, started
the unit from HA (the proven power write — the pump runs dry), watched
the panel display **E03**, captured the faulted blocks, cleared the
fault, and captured the cleared state. The full-capture diff across the
four blocks shows exactly:

- **2074: 0 -> 256** — the ONLY register that appears with the fault;
- `2011: 0 -> 1` — the power consequence (the unit runs to heat; the
  power home is already [P], spec 015 — the fault run exercised it,
  it pinned nothing new);
- `2047/2049/2050/2051`: small temperature moves (thermal drift, the
  dry run heating the still air around the unit — physics);
- nothing else moved.

That is the 022 contract §1 evidence bar, item by item: (1) healthy
baseline capture [P]; (2) fault capture + the panel's code written
down (E03) [P]; (3) cleared capture with the mover reverted — the
`0 -> 256 -> 0` aller-retour [P owner]; (4) the burst state during
the fault: the captures themselves prove the stream kept flowing
(four `crc=OK` block lines during the fault — consistent with
research 022 §3.3) [P]; (5) healthy and fault values written down
(0 and 256) [P]. **The bar is satisfied; the exposure may ship.**

### 1.2 The frames, byte-exact

The block-frame grammar (pinned by the repo's own test constants and
`modbus.parse_block_response` — "63 10 start:2 qty:2 bytecount
data:bytecount crc16:2 (LE)", length = bytecount + 9 = **99 bytes**
for qty 45):

```
63 10 07FE 002D 5A | 45 registers, big-endian, 2 bytes each | CRC
```

The owner's E03-state capture of block 2046, reconstructed from its
semantic content (sentinel at 2046; the fault-capture temps; the zero
tail; 2074 = 0x0100):

```
631007fe002d5a 7ffd 00910091009600960096 00*44 0100 00*32 551d
   header      2046  2047........2051     2052-2073 2074 2075-2090 CRC
```

**CRC verification [P arithmetic]:** the reconstruction's computed
CRC-16/Modbus over the first 97 bytes is `0x1D55`, transmitted low
byte first as `55 1d` — **exactly the owner's frame tail**. A
coincidental match has probability 2^-16; the reconstruction is
byte-identical to the wire frame. The healthy twin (the same frame
with 2074 = 0) computes CRC `0xF054` (`54 f0`) — used as the test
fixture.

**The baseline frame [P semantics / relay caveat]:** the relayed
healthy baseline hex does not self-validate — the data as relayed
(sentinel; temps 0x0090, 0x0091 x4; all 39 tail registers zero)
computes CRC `0x53AD` (`ad 53`) against the relayed tail `93 f9`, and
NO single-register or plausible multi-register variant within the
temperature band reconciles the two (exhaustive single-register
preimage sweep over all 45 positions; pairwise and sampled
multi-register sweeps over the 12.8-16.0 °C band). The likeliest
cause is a transcription slip in the relay of the long zero run (three
independent retypings of it here produced three different lengths —
the string is human-hostile). **This does not touch the pinned
facts**, for three reasons: (a) the captured frames are CRC-gated at
capture time (`_store_push` logs `crc=OK` lines only for CRC-valid
frames — the owner's real baseline frame was valid); (b) the baseline
semantic content — 2074 = 0, the all-zero tail — is stated by the
owner's diff and corroborated by the E03 frame's CRC-PROVEN zero
tail: every tail register except 2074 reads 0 in the fault capture,
and the diff reports no other mover, so the baseline tail read 0 too,
including 2074; (c) the relay's own prose ("2074 = 0" baseline,
"l'unité est revenue", the 0 -> 256 -> 0 aller-retour) is the
owner's record. The research ledger therefore carries the baseline
frame as **[P semantics; hex-relay imperfect]** — an honest footnote,
not a weakening of the fault home.

### 1.3 The offset arithmetic (the decode, by the numbers)

- Register 2074's index in the block: `2074 - 2046 = 28`.
- Its data offset: `28 * 2 = 56` bytes after the `0x5A` bytecount.
- Its frame bytes: `7 + 56 = 63` and `64` (0-based) — the "octets
  63-64" of the relay, confirmed.
- The E03 frame's bytes there: `01 00` -> big-endian **0x0100 = 256
  = 2^8 — bit 8**. The baseline's: `00 00` -> 0.
- The zero run around it: 22 zero registers before (2052-2073 = 44
  zero bytes), 16 after (2075-2090).

**The byte order is proven by three anchors in the SAME frames:**

1. `2046 = 0x7FFD = 32765` — the proven T01-absent sentinel [P panel,
  spec 013]. Little-endian would read `0xFD7F = 64895` — nonsense.
2. The temperature raws decode as plausible October values
   big-endian (`0x0096 = 150 -> 15.0 °C`); byte-swapped,
   `0x9600 -> 3840.0 °C` — implausible (the plausibility window is
   -45.0..150.0 °C).
3. The spec-015 arithmetic on the sibling 2001 block (the block's
   own start `07 d1` = 2001 self-reference) pinned big-endian on the
   pushed plane [P].

### 1.4 The diff, all 45 registers of block 2046 [P]

| register | baseline | E03 | delta | classification |
|---|---|---|---|---|
| 2046 | 32765 (0x7FFD) | 32765 | — | the T01 sentinel, unchanged |
| 2047 | 144 (0x0090)* | 145 (0x0091) | +1 raw | thermal drift (inlet) |
| 2048 | 145 (0x0091) | 145 | — | unchanged (outlet) |
| 2049 | 145 (0x0091) | 150 (0x0096) | +5 raw | thermal drift (coil) |
| 2050 | 145 (0x0091) | 150 (0x0096) | +5 raw | thermal drift (ambient) |
| 2051 | 145 (0x0091) | 150 (0x0096) | +5 raw | thermal drift (exhaust) |
| **2074** | **0** | **256 (0x0100)** | **+256** | **THE FAULT — bit 8** |
| 2052-2073, 2075-2090 | 0 | 0 | — | the zero tail, unchanged |

(*) The relayed baseline hex reads 2047 = 0x0090 while the relay's
prose summarizes the drift as "+0.5 C" for 2047/2049/2050/2051 —
the two relay forms differ by one transcription in the SAME family
as §1.2's caveat (0x0090 vs 0x008C would make the prose exact). The
classification (thermal drift) is unaffected either way: the E03
side is byte-proven, and 2047-2051 are the photo-calibrated
temperature homes [P, spec 013].

Cross-block [P owner, full-capture diff]: `2011: 0 -> 1` (power — the
consequence plane, home already [P] spec 015); 2019 unchanged
(power-only writes never touch the mode plane — the spec-018 world);
blocks 1001/1046 static.

**Q1 verdict: CONFIRMED by arithmetic.** 2074 is the only register
whose change is not power or physics. The fault word is **2074**;
its fault-state value on this run is **256 = 0x0100 = bit 8**, and
its healthy-state value is **0**.

---

## Part 2 — Bit 8: which fault? (Q2) — proven vs assumed

### 2.1 What the disassembly proves [P pool]

The ten-flag list exists, at `pp+0x18f28` (beside `"codes"`,
`"bitCodes"`, and the cloud endpoint `"app/device/v5/getDataByCode"`
— `pp+0x18f38-0x18f48`), in EXACTLY this order (re-verified in the
surviving `/tmp/blutter_out/pp.txt`):

| index | name |
|---|---|
| 0 | Sys1HighPressureSwitch |
| 1 | Sys1LowPressureSwitch |
| 2 | **WaterFlowSwitch** |
| 3 | EmergencySwitch |
| 4 | ModeSwitch |
| 5 | Master/SlaveSwitch |
| 6 | Sys1CompState |
| 7 | InletWaterTempSensorFault |
| 8 | **OutletWaterTempSensorFault** |
| 9 | ATSensorFault |

The same ten names appear again in the device-model pool region
(`pp+0x1b468-0x1b4f8`) — but there they are INTERLEAVED with
non-flag model fields (SetCompType, Sys1CompWorkingFreq, the AI
fields, CompressorCurrentDetect...), i.e. that region is the model's
flat field-name set, NOT a bit table.

### 2.2 What the disassembly does NOT prove [the honest limit]

- **No surviving code consumes the ten-name list.** A full sweep of
  the `asm/` tree for its pool slot (`#0xf28` load patterns) finds
  ZERO references — the consumer sits in code the dedup pass emptied
  (consistent with research 022 §2.2: the received-packet handler and
  the label closures are `size: -0x1`, empty).
- **The "bitCodes" context is CLOUD, not BLE.** The class file
  `asm/quk.dart` — which has REAL instruction bodies — reads
  `"codes"` and `"bitCodes"` as fields of a decoded map (with
  null-to-empty-list fallbacks, iterable type checks): they are
  LISTS in the cloud response model of
  `app/device/v5/getDataByCode`. Nothing links that model to the
  BLE pushed plane, and nothing shows a bit index -> list index
  mapping.
- **The vendor's code table is still absent.** The fault-list page
  loads `assets/faults/faults_*.json` [P pool — research 022 §2.1-c];
  the APK extraction on this host still carries only `lib/`, so the
  raw<->code and code->description tables remain unrecovered.

### 2.3 The three plausible readings of bit 8 (all unproven)

- **List-order hypothesis [A]:** bit index = list index -> bit 8 =
  **OutletWaterTempSensorFault** (NOT WaterFlowSwitch — that would
  be bit 2, the 022 watch list's expectation for the flag candidate
  2012, which did NOT move). Physically plausible for a dry run: an
  un-plumbed unit's outlet sensor sits in a dry/static pocket and can
  read out of range.
- **Reversed-order hypothesis [A]:** bit 8 = Sys1LowPressureSwitch.
  Also plausible: a dry evaporator drops suction pressure fast.
- **The owner's expectation [A]:** bit 8 = the flow fault (E03 =
  water flow). Nothing static supports pointing bit 8 at
  WaterFlowSwitch — under the ONLY hypothesis that gives the list
  meaning, WaterFlowSwitch is bit 2.

### 2.4 The verdict (what this spec claims, exactly)

- **[P] raw 256 on 2074 occurred exactly while the panel displayed
  E03** — one live observation, the diff-revert context. The
  raw<->code point `256 <-> "E03"` is PROVEN for this unit.
- **[A] whether 2074 is decoded through the ten-flag list at all**
  (the list's context is the cloud model; the BLE consumer is
  unrecoverable).
- **[A] the list order as the bit order** — and under that compound
  assumption, bit 8 = OutletWaterTempSensorFault.
- **[V] which protection tripped at bit 8** — the vendor's table is
  absent from the disassembly; the panel code is the only semantic
  anchor.
- **[P, owner observation 2026-10-12] the E03 LABEL.** While the unit
  displayed E03, the owner opened the vendor app, which names the code:
  > "dans l'app ca dit E03 probleme de debit d'eau"
  (= "in the app it says E03 - water flow problem"). Source: the owner,
  reported in the working session on 2026-10-12, reading the vendor app
  UI on the live faulted unit. This pins the CODE's meaning (E03 =
  water flow), not the bit's position — see §2.3: the disassembly's
  ten-flag list order puts OutletWaterTempSensorFault at index 8, so
  which physical protection raises bit 8 remains [A].

Consequence for the exposure (contract 022 §3 carried forward): the
entity maps ONLY the proven point (256 -> "E03"); every other
non-zero raw reads `unknown code (raw N)`; **bit names are forbidden
in entity code** and live here as [A]. The parent commission's
proposed label "bit 8 = E03 (water flow)" is therefore recorded as
**"raw 256 = E03 [P]" + "bit 8 = water flow [A, unsupported by the
list order — see §2.3]"** — the honest split.

---

## Part 3 — How to expose (Q3): the decisions

- **ONE `binary_sensor` "Fault"** (`device_class: problem`), push
  mode only — the 022 contract §2 shape, now evidence-activated. A
  fault IS a true binary (present/absent), and `problem` gives the
  standard HA automation surface for free.
- **`2074 == 0` -> OFF; `2074 != 0` -> ON; absent -> unknown.** The
  simplest rule that the evidence proves (the diff proves nonzero =
  fault for THIS run; the rule claims nothing about which bit).
- **A separate "code" sensor? NO.** One quantity, one entity
  (contract 013 §4.1): the code string is an ATTRIBUTE of the fault
  state, derivable from the raw through the proven map. HA's recorder
  keeps attributes in state history (visible in more-info/history),
  automations trigger on the binary state and read the attribute.
  A code-history DIAGNOSTIC sensor remains a documented future option
  if the owner wants long-term graphs (out of scope here; spec.md
  §3).
- **The raw and the bits as attributes** (`raw`, `hex`, `set_bits`):
  pure arithmetic, no interpretation — `set_bits` exists so the NEXT
  fault observation records its bit pattern with zero tooling.
- **Naming/classification:** name "Fault" (renders "<device> Fault";
  `has_entity_name`), unique_id `<ADDRESS>_fault`, device: the entry's
  PHNIX device; **NO entity_category** — an operational signal, not
  plumbing (the push_stream diagnostic is DIAGNOSTIC; this is not).

## Part 4 — The platform return (Q4): what returns, what stays dead

Spec 021 pruned `binary_sensor.py` because it was a REGISTRY of
placeholders — no pinned base register, no pushed home, zero
creatable entities: dead code. Spec 022's contract §2 named the one
justified return: a binary with a REAL, diff-proven home. This spec
returns the platform WITHOUT the registry pattern:

- `const.PLATFORMS` gains `"binary_sensor"`;
- `binary_sensor.py` creates ONE literal entity per push entry
  (classic entries: none — law L1);
- **no `BINARY_SENSORS` registry, no `RegDef` loop, no placeholder
  entries** — the 021 dead-weight pattern cannot return because
  creation is not registry-driven;
- the other binary surfaces (compressor/pump/fan/valve/switches)
  stay pruned — their homes remain unpinned.

## Part 5 — Is block 2046 always captured? (Q5)

- The 2046 block is one of the four burst blocks [P — six censused
  runs, every poll since; `PUSH_HARVEST_STARTS` includes it].
- The poll cycle HARD-FAILS without a fresh T-block capture (the
  freshness gate reads the 2046-block's `last_seen`; silence ->
  `UpdateFailed("push stream silent after trigger")` -> every
  CoordinatorEntity goes unavailable).
- A stored capture always carries all 45 registers (99-byte CRC-valid
  frame; truncation/CRC failure is never stored).
- Therefore: healthy stream -> 2074 always readable; stale/silent ->
  the Fault entity is UNAVAILABLE with everything else — **never a
  guessed OFF** (contract 022 §2's rule, satisfied by the existing
  machinery, no new code needed). The one theoretical gap — a fault
  the unit stops reporting, or a fault that never moves 2074 — is
  stated plainly in plan §4: the entity can only be as truthful as
  the pushed plane.

## Part 6 — Discovering the other bits (Q6): the protocol

The map builds one proven row per observation. What each row needs:
the panel's displayed code + the 2074 raw (its `set_bits` list rides
the entity's attributes). How rows get banked:

- **Opportunistically (the primary protocol):** whenever ANY fault
  happens, write down the panel code and copy the Fault entity's
  attributes (the WARNING capture hex passively records the frames
  on every reload besides). Zero-risk, zero-tooling.
- **The one PROVEN-safe provocation:** the dry-run flow trip (E03) —
  the owner has done it; it is reversible and low-risk (research 022
  §2.5).
- **NOT to be provoked deliberately [the spec's safety line]:** HP
  switch (a deliberate high-pressure trip risks hardware), LP switch
  (uncontrolled on a dry unit), emergency switch (unknown wiring),
  sensor disconnections (opening the unit). The spec does not ask
  the owner to chase faults; it asks them to RECORD the ones that
  happen.
- **The remaining static lead:** the APK's `assets/faults/*.json` —
  if the owner can re-extract the original APK, the vendor's own
  table may pin the code meanings (and possibly the bit order)
  without another live run.

## Part 7 — The portal question (Q7)

**None. Confirmed.** The exposure is pure READ decoding of a
register the unit already pushes every cycle: no option key, no
config-flow step, no checkbox, no service, no confirmation — the
entity is created automatically for push entries like the six
sensors and the climate. The 022 contract §4 anticipated exactly
this: "nothing here reintroduces a human gate. The fault entity
ships ACTIVE like everything else (there is nothing to confirm —
the evidence bar is this contract's §1, satisfied before the code
exists)." The write plane gains nothing (2074 is refused by the
funnel forever). Nothing gates this spec's surface.

## Part 8 — The honest ledger

**PROVEN [P]:**
- 2074 is the fault home on the pushed plane: 0 healthy -> 256 (E03
  displayed) -> 0 cleared (the owner's diff-revert, 2026-10-12).
- The E03-state frame is byte-exact (CRC 0x1D55 = the relayed tail;
  §1.2); 2074 = 0x0100 = 256 = bit 8 at frame bytes 63-64 (offset
  arithmetic, §1.3); the block data is big-endian (three anchors).
- The diff classification: 2074 is the only non-thermal, non-power
  mover (§1.4).
- The raw<->code point: 256 <-> "E03" (one live observation).
- The ten-flag list exists in the pool, in the stated order (§2.1);
  the `"codes"/"bitCodes"` model fields exist (quk.dart, real
  bodies); the vendor fault-list page loads `assets/faults/*.json`.

**ASSUMED [A]:**
- The ten-flag list is a decode table for 2074's bits at all (the
  list's visible context is the CLOUD model; the BLE consumer is
  unrecoverable — zero surviving references).
- The list order = the bit order. Under that compound assumption,
  bit 8 = OutletWaterTempSensorFault (bit 2 = WaterFlowSwitch) —
  which is why "bit 8 = water flow" is NOT claimed anywhere in code
  or docs.

**OPEN [V]:**
- What E03 means (which protection tripped) — pending the APK assets
  or the vendor's documentation.
- The meaning of every other bit (0-7, 9-15) — pending future
  recorded observations (quickstart §4).
- Whether OTHER faults also ride 2074 (a fault that moves nothing on
  2074 would be invisible — plan §4's stated limit).
- The baseline frame's relayed hex (self-inconsistent — §1.2's
  caveat; the semantic facts stand).

**Corrections to prior records:**
- The 022 watch-list expectation "2012 new bits; WaterFlowSwitch =
  bit 2 -> expect raw 4/5 on 2012" did NOT materialize: 2012 did not
  move, and the fault landed in the 2046 tail instead. The watch
  list did its job (it predicted the shape of the hunt, not the
  address).
- The 022 research §2.4 note "a fault word would most plausibly read
  0 when healthy and go non-zero on trip" — CONFIRMED exactly (the
  thirty-nine-zeros logic; the hit landed in block 2046's tail, not
  block 2001 as the priority ordering guessed).