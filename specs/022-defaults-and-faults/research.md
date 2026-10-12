# Spec 022 — Research: active by default (the gate decision) + where the faults live

Date: 2026-10-12
Repo state studied: main @ 0d32de2, manifest 0.15.0
Sources: the code (all files), specs/020-integration-reference (all files),
the archived specs (001-019), FINDINGS.md, and a fresh dig into the
libapp.so disassembly artifacts (`blutter/pp.txt`, `blutter/objs.txt`,
and the FULL blutter output that survives at `/tmp/blutter_out/` —
including the `asm/` tree with 1407 class files, which the repo copy
does not track).

Confidence tags follow the repo convention: **[P]** proven (live or
byte-exact from the disassembly), **[A]** assumed, **[V]** open.

---

## Part 1 — VOLET 1: Option A vs Option B for the gates

### 1.1 What the gates are today (the inventory)

Four "portails", each = a per-entry option OR a global switch in
`registers.py`, all shipping **False**:

| # | Option key | Global switch | Gates |
|---|---|---|---|
| 1 | `push_temp_confirmed` (config-flow checkbox + options toggle) | `registers.PUSH_TEMP_CONFIRMED` | the 6 temperature sensors (values from the pushed T-block) |
| 2 | `push_control_confirmed` (config-flow checkbox + options toggle) | `registers.PUSH_CONTROL_CONFIRMED` | the climate entity (power display + setpoint read) |
| 3 | `write_control` (options toggle) | `registers.WRITE_CONTROL_PROVEN` | power writes (1011) |
| 4 | `write_setpoint` (options toggle) | `registers.WRITE_SETPOINT_PROVEN` | setpoint writes (1046) |

Plus the shipped defaults in `const.py` (`DEFAULT_WRITE_CONTROL`,
`DEFAULT_WRITE_SETPOINT`) and the option defaults embedded in
`config_flow.py` (checkboxes default False; options toggles default to
the stored value or False).

What the gates were FOR: during the hunt (specs 013-019) nothing was
proven, so every surface was locked behind an owner-verified evidence
act — photo calibration, toggle diff-revert, panel diff, write-probe
verdicts. The gates were the honesty system: *no unconfirmed data under
a certain name, no unproven write.*

What is proven NOW (all recorded in specs 020 and the archive):
- temperatures 2046-2051 — photo-calibrated against the app [P]
- power mirror 2011 / mode mirror 2019 — owner diff-revert [P]
- setpoint 1046 — panel diff 290→300 [P] and live write mirror
  300→295→300 [P]
- power write 1011 {0,1} — probe verdict EXECUTED + REVERSIBLE, pump
  heard, mirror followed [P]
- setpoint write 1046 — probe verdict EXECUTED + REVERSIBLE [P]

Every gate guards a surface whose evidence act has already been
performed and recorded. On this machine the gates now re-ask a question
that has been answered.

### 1.2 The two options

**Option A — keep the keys, default them True.**
- `const.DEFAULT_WRITE_*` → True; checkboxes pre-checked; options
  toggles default True.
- Fatal flaw #1 — **existing entries stay closed**: the options flow
  reads `current.get("write_control", DEFAULT)` — a STORED `false`
  beats the default. The owner's just-recreated entry (and any entry
  created before the flip) would need four manual re-ticks or a
  migration that overwrites user options — exactly the kind of silent
  option mutation the old contracts forbade.
- Fatal flaw #2 — **the confusing surface survives**: four toggles whose
  labels say "the captured temperatures match the app's parameter view
  (calibration verified)" would still be shown to a NEW user who never
  performed any calibration and cannot honestly attest anything. Either
  the texts keep lying-by-irrelevance, or they must be rewritten into
  something a newcomer understands — at which point the toggles no
  longer say anything worth toggling.
- Fatal flaw #3 — **dead code weight stays**: four option reads, four
  gate properties, gate branches in `from_pushes`/sensor/climate/options
  flow, plus their tests — all to support a switch nobody should ever
  flip off.

**Option B — remove the portals entirely.**
- Delete the four option keys, the four global switches, the two
  checkboxes, the four coordinator gate properties, the gate branches in
  every consumer, and the gate-specific tests. The integration becomes
  active by construction: sensors, climate, power control and setpoint
  control work the moment the entry is created.
- **Migration is trivial**: stale option keys on existing entries are
  simply never read again. HA's `OptionsFlow.async_create_entry`
  replaces the whole `entry.options` dict on the next options save, so
  the stale keys evaporate on the owner's next visit to Configure —
  and harm nothing until then. Entry data is unchanged (`mode: "push"`
  stays), flow VERSION stays 1, no `async_migrate_entry` needed. The
  owner's freshly recreated entry becomes fully active the moment the
  new build loads, whatever its stored options say.
- **Safety is not reduced** — the distinction the mission draws: the
  gates were HUMAN confirmations (ask the owner to re-attest proven
  evidence); the protection that actually stops bad writes is AUTOMATIC
  and stays byte-identical:
  - the single write funnel `ensure_writable` with the permanent
    address allowlists (1011 and 1046 ONLY — the mode pair 1012/1013,
    mute 1076, installer thresholds and the whole read plane are refused
    forever);
  - the permanent VALUE rails: `{0, 1}` for 1011; raw 100..400 in steps
    of 5 for 1046 (never rounded, never clamped);
  - mirror confirmation (the state only ever follows a fresh pushed
    block that postdates the write; unconfirmed OFF raises a persistent
    notification);
  - the freshness gate, sentinels, the plausibility window;
  - the vendor's own pinned frames, byte-asserted by the modbus
    self-test.
  Removing the gates removes the redundant human layer; every machine
  layer stays.

### 1.3 Decision — **Option B**

Chosen because the goal is simplification and the gates' purpose is
complete. The one-line test: *would a brand-new user understand each
remaining screen?* Under A they would meet four toggles describing
evidence rituals they never performed; under B they add the device,
name it, and everything works — with the automatic guards doing the
real protecting. The owner's existing entry needs no action. The code
loses an entire concept (the gate ladder) instead of carrying it as a
default-True zombie.

Scope note: the dormant classic-plane paths (base-block climate, classic
write branches) are untouched — they never had these gates; the gates
were push-mode concepts and vanish with their mode.

---

## Part 2 — VOLET 2: where are the faults?

### 2.1 What the disassembly PROVES about the app's fault machinery

All citations are pool offsets in `blutter/pp.txt` (byte-exact from the
APK's Dart snapshot) unless noted.

**a) The app owns a fault-code vocabulary, and it sits in the BLE
session's own pool region.** In the pool stretch occupied by the BLE
session class (`bS` — the class the FINDINGS calls BleManger/BleLinker;
`asm/nzk.dart`), between the GATT-discovery strings (pp+0x21210-0x212c0)
and the pushed-block query frames (pp+0x21510-0x21568:
`"03e9"…631003E9002DD9E6` … `"07fe"…631007FE002D68D2`), the pool
carries this flat string list (pp+0x21308-0x21508):

- **E-codes**: `E01`, `E02`, `E03`, `E05`, `E06`, `E07`, `E19`, `E29`,
  `E051`, `E081` — plus system-3 variants `E051-3`, `E01-3`, `E02-3`,
  `E03-3`, `E07-3`, `E06-3`. (`E04` and `E08` appear in the
  parameter-view label map instead — see d.)
- **F-codes**: `F01`, `F02`, `F03`, `F04`, `F05`, `F06`, `F07`, `F08`,
  `F09`, `F10`, `F11`, `F12`, `F15`, `F16`, `F17`, `F18`, `F19`, `F20`,
  `F22`, `F23`, `F24`, `F25`, `F26`, `F27`, `F28`, `F051`, `F031`,
  `F032`.
- **P-codes**: `P01`, `P02`, `P04`, `P05`, `P07`, `P081`, `P082`
  (+`P082-3`), `P09`, `P15`, and the bare words `PP`, `TP`.
- **eight zero-padded numeric strings**: `021`, `089`, `120`, `217`,
  `156`, `157`, `158`, `389` — semantics unresolved [V]; they sit inside
  the same list stretch, plausibly raw fault values or detail-IDs, but
  nothing pins them.

So YES — there is an E-code table vocabulary (E01-E08 + more), exactly
as the owner hoped. `E03` — the code the panel will show for the flow
fault — is among the pool strings [P]. What is NOT recovered is the
code→meaning table (see c).

**b) The app owns a bit-level status vocabulary that matches the fault
world.** pp+0x18f28: `List<String>(10)`:

```
Sys1HighPressureSwitch, Sys1LowPressureSwitch, WaterFlowSwitch,
EmergencySwitch, ModeSwitch, Master/SlaveSwitch, Sys1CompState,
InletWaterTempSensorFault, OutletWaterTempSensorFault, ATSensorFault
```

alongside the strings `"codes"`, `"bitCodes"` and the cloud endpoint
`"app/device/v5/getDataByCode"` (pp+0x18f38-0x18f48). The same ten names
appear again in the device-model pool region (pp+0x1b468-0x1b4f8).
**WaterFlowSwitch** is exactly the E03 world (no water flow); the three
`*SensorFault` names match the app strings FINDINGS already listed.
Interpretation [A]: these ten flags are bits packed in one or more
status word(s); the list ORDER may be the bit order — unproven until a
live diff.

**c) The app has a fault-list PAGE with its own data model and local
fault tables.** In the pool (pp+0x1abc8-0x1ad78):
- page names: `"蓝牙故障列表"` (Bluetooth fault list), `"故障列表"`
  (fault list), `"-查看故障详情"` (view fault details), i18n keys
  `faultList`, `faultListBLE`, `faultDetail`, `monitorFault`, plus
  `"No faults"` / `"noFaults"`;
- the cloud endpoint `"app/device/v3/getFaultDataByDeviceCode"` with the
  model fields `"dataId"`, `"protocalId"`, `"dataValue"`, `"faultCode"`,
  `"falutDetailId"` (sic — the vendor's own typo), `"errorLevel"`
  (pp+0x1ac10-0x1acf0);
- **the page loads fault tables from the APK's own assets**:
  `"assets/faults/"` + `"faults_"` + `".json"` (pp+0x1ad68-0x1ad78) —
  i.e. `assets/faults/faults_<something>.json`, one table per device
  family/protocol. These JSON files are the vendor's own
  code→description map. **They are NOT on this host**: the APK
  extraction kept only `lib/arm64-v8a/` (`/tmp/flutterapp/`); no APK,
  no assets dir anywhere on the gateway. Re-extracting the original
  APK's assets is the ONE remaining static lead — flagged as an
  optional follow-up for the owner (quickstart §0).

**d) The parameter-view page carries fault entries.** The label→register
map class (`VR.Yzd`) pool region (pp+0x21700-0x219c8) contains the
display labels `D01…D06`, `E04`, `E08`, `F13`, `R02/R03/R06/R07`,
`O01…O04`, `S01…S06`, `T01…T06`, `H01/H04/H05`, `P03`, `deviceNameType`
— each with a decode closure. Spec 003 already recorded E04/E08/F13 as
"fault entries [P apk]" from FINDINGS §11.4. So the app's parameter view
has fault-related rows — one more display surface fed from registers we
have not pinned.

**e) The device-STATUS model (cloud side).** pp+0x1b1a8-0x1b2d8: the
JSON fields `deviceOnline`, `deviceFault`, `productKey`,
`productionCode`, `dtuIccid`, `dtuSoftwareCode/Ver`,
`wifiSoftwareCode/Ver`, `isExpire`, `isFault`, `deviceStatus`
(`"ONLINE"`). These are the CLOUD status fields (device identity +
connectivity + fault presence) — the app's cloud world, not the BLE
wire format.

**f) A correction to FINDINGS' grouping — `isMonitorFault` is NOT a
device field.** It appears at pp+0x9df0 inside the USER-ACCOUNT model
pool stretch (`nickname`, `headUrl`, `email`, `token`, `accessKey`,
`tokenExpired`, …, `isMonitorFault`, `isAgreePrivacy`, `profile`) — it
is the account-level "fault monitoring" preference (subscribe to fault
notifications), not a device fault quantity. FINDINGS §4 lists it among
"Status / faults" strings; the research record should carry this
correction. `deviceFault`/`isFault`/`deviceStatus` ARE device-status
fields (cloud model, e).

### 2.2 What the disassembly CANNOT prove — the honest limit

The register indices that feed the fault display are compiled as
immediates inside function bodies. The APK was built with
`dedup_instructions` + obfuscation, and the blutter output confirms it
FOR THE EXACT FUNCTIONS WE NEED:

- the BLE session's **received-packet handler** — `asm/nzk.dart`, class
  `bS`, the notify callback `[closure] void (dynamic, List<int>)` at
  `0x5dbf00` — is emitted as `// ** addr: 0x5dbf00, size: -0x1` with an
  EMPTY body [P];
- **every label→register closure of `VR.Yzd`** (`asm/mzk.dart`, the
  closures behind D01…S06/T01…H05/P03 including E04, E08, F13) is
  likewise `size: -0x1`, empty [P].

(For contrast, the class's connect/discover closures DO carry real
instruction bodies in `asm/nzk.dart` — the emptiness is the dedup
artifact, not a tooling failure.)

Consequence, stated plainly: **the pushed register that carries the
fault — if the app reads one at all in BLE mode — is NOT recoverable
from the static artifacts.** No amount of further pool-string reading
will pin it; the immediates are simply not in the output. The only
remaining static lead is the APK assets (c). The definitive instrument
is the live fault-diff — the E03 protocol (quickstart.md).

### 2.3 The base-block candidate is dead — again

The classic plane's fault home is base register **47 = "Fault1"**
[pp+0x18f18, the 45-name list; FINDINGS §12]. The unit never serves the
base block (law L1, spec 020 — six instrumented runs, every poll since).
Register 47 is unreachable on this machine, full stop. Any fault
exposure must live in the pushed plane or nowhere.

### 2.4 The unidentified registers 2012 / 2017 / 2018 — decode analysis

Healthy-baseline values from the production captures (spec 015
research §2.3, corrected register count; registers.py labels):

| reg | baseline raw | hex | bits set | readings considered | verdict |
|---|---|---|---|---|---|
| 2012 | 1 | 0x0001 | 0 | status bitfield with only bit0 set while healthy; or a plain flag/enum (e.g. "report valid") | [V] — static across the whole 2026-10-12 diff, so NOT power/mode; a bitfield read against the §2.1-b list order would put bit0 = Sys1HighPressureSwitch — polarity/meaning unknown |
| 2017 | 13 | 0x000D | 0,2,3 | not temp-shaped (1.3 °C fits nothing here); a small counter/enum; a bitfield with bits 0/2/3 | [V] |
| 2018 | 168 | 0x00A8 | 3,5,7 | temp-shaped (16.8 °C — plausible for an idle unit in October, but redundant with the T-family); runtime counter (168 h = exactly one week); a bitfield with bits 3/5/7 | [V] |

Are they fault candidates? **Yes — all three, and no more than any other
member of the 2001 block.** The key structural fact: the healthy 2001
block reads 0 in 39 of 45 registers. A fault word would most plausibly
read **0 when healthy** (no fault) and go non-zero on a trip — so the
primary candidates are the thirty-nine zeros (2013-2016, 2020-2033,
2035-2045…), with 2012/2017/2018 as the "already-nonzero" wildcard
candidates whose VALUE or BIT PATTERN may change. The E03 diff watches
all 45 equally (the probe dumps the whole block); the short eyeball
list for the owner is: **2012, 2017, 2018, and every register that was
0 at baseline**.

The `-3` suffix codes and the `Sys1…` prefixes (§2.1-a/b) tell us the
vendor builds multi-system units; this unit is single-system, so the
plain codes apply.

### 2.5 The proof protocol — why E03 and why it is safe

The owner's unit is NOT plumbed (no water loop). Starting it will trip
the flow protection within minutes: the panel is expected to display
**E03** (the owner's own prediction; the vocabulary confirms E03 exists
[P]). This is:
- **reversible** — power off clears a flow fault (no water was harmed;
  the flow switch simply never closed);
- **low-risk** — dry-running a pool heat pump briefly is its normal
  commissioning state; the flow protection exists precisely to catch it;
- **the same instrument that pinned everything else** — the
  diff-revert: baseline capture → provoke → capture → clear → capture →
  the register(s) that moved WITH the fault and reverted WITHOUT it are
  the home(s). This is byte-for-byte the method that pinned 2011, 2019
  and 1046.

Full step-by-step for the owner: **quickstart.md**. Short watch list:
the whole 2001 block (probe logs all 45 raws with labels), eyes on
2012/2017/2018 and any 0→nonzero move. Tooling that already exists:
`aqua_temp_ble.probe_registers` (block `2001`, or the default
all-blocks trigger) and the `Push stream` diagnostic's
`control_candidates` attributes (2012 is already exposed there; this
spec ADDS 2017/2018 — see tasks T12).

One unknown to record honestly: whether the unit keeps pushing its
burst WHILE faulted [V]. If the stream goes silent during the fault,
capture immediately after the fault clears (still a valid diff, weaker
evidence — marked as such in the protocol).

### 2.6 How to expose a fault in HA — IF one is found

Decision (the exposure contract is in
`contracts/fault-evidence-contract.md`):

1. **The entity is a `binary_sensor` named "Fault"** (`device_class:
   problem`), created in push mode only, ON when the pinned fault word
   indicates a fault. Justification against the spec-021 deletion:
   021 pruned `binary_sensor.py` because its registry placeholders had
   no pinned base registers and no pushed homes — zero entities could
   ever be created; dead code. A diff-proven fault word is a REAL
   binary with a REAL home — the one case that justifies the platform's
   return. A fault IS a true binary (present/absent) and
   `device_class: problem` gives users the standard HA automation
   surface (alerts, notifications) for free.
2. **The raw value rides as `extra_state_attributes`** (diagnostic
   evidence: raw, hex, provenance) — not a second entity.
3. **The code STRING ("E03") is displayed ONLY if the protocol proves
   the raw↔code correspondence** (panel showed E03 while register X
   read raw N). The vocabulary is proven to exist (§2.1-a) but the map
   raw→code is not; an unproven code table is exactly the "unconfirmed
   data under a certain name" this repo never ships. Until then: ON/OFF
   + raw attributes. If proven for one code, the sensor maps that one
   raw and labels every other non-zero raw `unknown code (raw N)`.
4. **No exposure without the diff-revert evidence.** If the E03
   protocol fails to find a mover, the answer stays "the fault is not
   exposed" and the research record says why. We do NOT guess a home
   because the app has fault strings.

### 2.7 Is the "fault code absent" screen text accurate?

**Today: yes, accurate.** No pushed fault home is proven; no fault
entity exists; the old phrasing's sin was the REASON it gave ("stays
absent until the unit answers requests" — false since 014: the unit
answers every request with the burst; what is missing is the SOURCE,
not the response). The rewritten confirm text (VOLET 1) says the fault
code is **not displayed because no reliable source for it has been
identified in the unit's report** — true, simple, and self-updating in
one line if the E03 protocol lands a home (a follow-up flips that
sentence and adds the entity per the contract).

### 2.8 Open items [V] — the honest ledger

- The pushed fault home: UNKNOWN — not statically recoverable
  (§2.2), not in the base block (§2.3). The E03 diff is the only
  instrument that can pin it.
- The raw→E-code map: the vocabulary exists [P]; the map is in the APK
  assets (`assets/faults/faults_*.json`) which are NOT on this host.
- Whether the burst keeps flowing during a fault — **ANSWERED
  2026-10-12 [P]**: it DOES (the owner's reload during the live E03
  logged four `block push captured` WARNING lines, crc=OK — research
  Part 3.3). The protocol no longer needs the fallback branch.
- 2012/2017/2018 semantics [V] — the E03 diff may resolve some; they
  may equally turn out to be unrelated (mode/config/counter).
- The eight numeric pool strings (021/089/120/217/156/157/158/389) [V].
- `isMonitorFault` corrected to an account-level field (§2.1-f).
- On other firmware: whether classic-mode units answer the FC03 read
  (register 47 then works) — unchanged open item from spec 020.

---

## Part 3 — The 2026-10-12 owner addendum (three additions)

The owner reviewed the spec in progress (same day) and added three
items: the climate's current_temperature must be the inlet water
temperature (not ambient); the capture line lost its hex (a 021
regression, proven live); the machine is in E03 NOW and the owner
needs the watch list. Each is recorded here, then folded into the FRs
(FR-12/13/14), the plan (§3) and the tasks (T6/T13/T15).

### 3.1 current_temperature — the code audit (owner addition 1)

The owner's remark: a pool heat pump regulates the water ENTERING the
unit; ambient air is an indicator. Audit of climate.py @ 0d32de2:

- **push mode**: `current_temperature` reads
  `registers.PUSHED_TEMPS["inlet_water_temp"]` = **2047 (T02 — the
  live-proven inlet register [P])** through the shared
  `decode_temp_logged` — the same source, sentinel and plausibility
  rules as the inlet sensor entity (one quantity, one source,
  contract 013 §4.1);
- **classic mode**: `data.inlet_temp` = base register **26** — the
  classic inlet home;
- **ambient (2050, T05)**: appears ONLY as the separate ambient
  sensor entity; nothing in the climate reads it.

**Verdict [P audit]: the requirement is ALREADY met by the code.**
The remark becomes the PIN (FR-12): the spec now forbids the
regression explicitly and adds the regression test (AC-16). No
production change.

### 3.2 The capture hex — a regression, proven live (owner addition 2)

History, byte-exact:

- **contract 013 §2.1** designed the capture line as
  `block push captured: start=%d qty=%d len=%d crc=OK hex=%s` —
  WARNING, ≤ 4 per connect, first push per distinct start, full
  99-byte hex — because "the operator's panel keeps WARNING+ only
  (contract 012 §2); this line IS the 2001-2045 capture mechanism
  (U-1) and the calibration data source".
- **spec 021 (C2)** demoted the hex to a DEBUG twin ("the hex
  firehose was chatty on flaky links") — but the operator's panel
  never shows DEBUG: the demotion deleted the only PASSIVE capture
  instrument. The `_store_push_frame` docstring was left claiming
  "logs the complete hex at WARNING" — code and docstring diverged.
- **The loss is proven live**: on 2026-10-12 the owner reloaded the
  integration DURING the E03 fault; the log produced exactly four
  hex-less capture lines — the fault-state frames existed, the panel
  showed E03, and the values were thrown away at that exact moment.

**Does the probe 'base' solve it? PARTIALLY [P audit]:**

- The 021-repaired push-mode "base" probe (trigger + store dump) DOES
  dump on demand: one INFO header line per block (with `count=`) plus
  one INFO line per register (raw, hex, /10, label) for ALL FOUR
  blocks — and the service (`supports_response=OPTIONAL`) returns the
  same structure, so the owner gets the complete snapshot in
  Developer Tools WITHOUT logger changes and WITHOUT the hex line.
  The on-demand diff instrument exists TODAY.
- But it is ACTIVE, not passive: it needs the owner at the keyboard
  while the fault lives; the WARNING hex captures EVERY reload with
  zero action. The INFO dump is invisible in the operator's WARNING+
  panel (the service response is its visible surface), and the
  dump's `count=` field is the only freshness tell (a store gone
  stale during a silent burst would otherwise lie).

**Decision (the owner's call, 2026-10-12):** restore contract 013
§2.1 byte-exactly — hex back on the WARNING line, once per block per
connection (≤ 4 lines/connect, bounded; the flaky-link concern is
overruled as the price of the evidence). The DEBUG twin is deleted
(redundant). FR-13 / AC-17; probe paths untouched.

### 3.3 The E03 watch list (owner addition 3 — the machine is faulted NOW)

Situation: the unit is un-plumbed and tripped its flow protection;
the panel shows E03. §2.5's "does the burst keep flowing" open item
is ANSWERED by the owner's reload: **the burst keeps flowing during
the fault** — four `block push captured` WARNING lines, crc=OK,
logged DURING E03 [P owner, 2026-10-12]. The values of THAT reload
were lost to the §3.2 regression — re-capture (probe now, hex from
Phase A onward).

**Healthy baselines [P — production captures + owner lines]:**

| block | healthy-ON baseline (2026-10-12) | notes |
|---|---|---|
| 2001 | 2010=2001 (self-ref marker [P arith]), 2011=1 (power), 2012=1 (0x0001), 2017=13 (0x000D), 2018=168 (0x00A8), 2019=4 (mode heat); ~39 zeros: 2001-2009, 2013-2016, 2020-2033, 2035-2045 | the 2026-10-10 idle-OFF capture differed: 2011=0, 2019=0, **2034=4** (2034=0 in the ON baseline) — an unlabeled state enum that has MOVED between captures |
| 2046 | 2046=32765 (T01 sentinel), 2047=145 (inlet 14.5 °C), 2048=140 (outlet 14.0 °C), 2049=145, 2050=145 (ambient 14.5 °C), 2051=145; 2052-2090 all 0 | the 015-era capture: same shape, 2047=130 band |
| 1001 | 1012=1, 1018=1, 1021=1050 (H04 anchor), 1022=1, 1023=2 (heat-only), 1037=2, 1038=30, 1039=3, 1040=1; rest 0 | static ≥ 2 days, byte-identical frames [P] |
| 1046 | 1046=300 (the setpoint home [P — 290→300 panel diff, 300→295→300 write mirror]), 1047/1048=5, 1049=150, 1050=400, 1051=270, 1052/1053=5, 1054=100, 1055=350, … | static ladder; [A-weak] candidates 1049/1051/1055/1068 |

**The prioritized watch list (this is the FR-14 quickstart content):**

**P1 — block 2001 (the status plane):**
1. **The ~39 baseline-ZERO registers** (2001-2009, 2013-2016,
   2020-2033, 2035-2045): a fault word plausibly reads 0 when healthy
   → non-zero on trip. ANY 0→non-zero is the prime fault-home hit.
   Telling shapes: small integers (3 = the E03 number itself?),
   bitfields (0x0004/0x0005), the eight pool numerics
   021/089/120/217/156/157/158/389 [V].
2. **2012** (1 = bit0 only; static across the whole 10-12 diff): new
   bits = bitfield hit — IF the app's ten-flag list order (§2.1-b)
   is the bit order, WaterFlowSwitch = **bit 2** → expect raw 5
   (0x0005) or 4 (0x0004); polarity unknown [V] — also watch 1→0.
3. **2034** (0 healthy-ON; **4** in the 10-10 idle-OFF capture [P]):
   an unlabeled state enum PROVEN to move — the strongest
   "second value" lead; expect possibly a third value during E03.
4. **2017** (13) / **2018** (168): the already-nonzero wildcards — any
   value/bit change; if 2018 is a runtime counter (168 h = one week),
   a freeze or jump during the fault is informative.
5. **2010=2001** (self-reference): expected static; a move is notable.
6. **2011** (power) / **2019** (mode): the CONSEQUENCE plane — the
   trip may flip them (1→0); record, do not pin (homes [P] already).

**P2 — block 2046 (the temperature plane):**
7. **2047/2048** (inlet/outlet): no-flow drift/divergence — the
   physical signature of E03 (consequence, diagnostic).
8. **2046** (sentinel 32765): leaving sentinel = the sensor-fault
   plane (cf. the app's *SensorFault flags).
9. **2052-2090**: the 39-register zero tail — same 0→non-zero logic.

**P3/P4 — blocks 1001 and 1046:** config/ladder, static by nature
[P ≥ 2 days]; any move during a fault is surprising — check, low
prior.

**Patterns that make a hit:** 0→non-zero; new bits in 2012 (bit 2);
E-number-shaped or pool-numeric values; sentinel moves; a register
freezing mid-update. **Non-hits:** 2011/2019 moves (consequences),
temperature drift (physics), values that revert within one cycle
(mirror noise).

**Instruments (current build, today):** the
`aqua_temp_ble.probe_registers` service, block `base`, WITH
return-response — the complete four-block snapshot with labels (the
`count=` field is the freshness tell); the `Push stream` diagnostic's
`control_candidates` attributes (2011/2012/1046… now; 2017/2018
after T12); from FR-13 onward, the WARNING hex on every reload. **The
already-faulted entry:** panel code + time → probe fault-state →
clear → probe healthy → diff (the baseline is recorded above).