# Spec 024 — Research: the LATCH (the owner's power-cut proof) and the six decisions

Date: 2026-10-12
Sources: the owner's 2026-10-12 follow-up fault run (the three-step latch
experiment relayed with this spec's commission: the E03 re-confirmation,
the machine-off observation, the power-cut clear), the repo record (specs
020/022/023, the archived specs), and the code @ main 7ee702b (manifest
0.17.0 — the spec-023 build: the Fault binary, the proven code map, the
app-label record).

Confidence tags (the repo convention): **[P]** proven, **[A]** assumed,
**[V]** open.

---

## Part 1 — The new evidence: the fault word is LATCHED [P]

### 1.1 What the owner did (2026-10-12, after spec 023 shipped)

The owner set out to watch the new Fault entity against a live fault.
The sequence, exactly as reported:

1. **E03 provoked again** (the un-plumbed dry run — the only proven-safe
   provocation, quickstart 023 §4). The fault word **2074 read 256**
   again — the register home re-confirmed on a second, independent live
   run [P]. (The first confirmation was the 023 diff-revert; this is
   the second live observation of the same raw↔code point, 256 ↔ E03.)
2. **The machine turned OFF** — the logical off (the same power write
   the climate performs), the unit healthy and at rest, no fault on the
   panel. **2074 STAYED 256.** The Fault entity kept displaying the
   fault ("Problematique" in the owner's French HA locale) while the
   machine was healthy and off [P].
3. **The BLE link restarted** (the software restart of the module's
   session — the disconnects/reconnects the integration performs
   naturally; the module stays powered while the machine is plugged
   in). The latched value survived: fresh bursts after the restart
   re-served **2074 = 256** [P]. The latch therefore lives in the
   unit's own powered memory — it is NOT an artifact of our store
   (the per-connect store is rebuilt from the unit's own frames at
   every reconnect, and the unit kept reporting 256).
4. **The physical power supply cut** (unplug + replug). **2074 read
   0** — the fault word CLEARED, the entity returned to OK [P].

### 1.2 What this proves: the latch

**The fault word 2074 is a LATCHED fault memory.** A fault SETS it;
the value persists while the unit's electronics have power — through
the machine's logical off, through BLE link restarts — and is cleared
by removing the physical power supply. [P — the owner's step 2-4
experiment, 2026-10-12]

This is the normal behavior of an industrial fault coder: the machine
MEMORIZES a fault until an explicit clear, so a technician reading the
code later still sees what happened. The unit's "explicit clear" is
the power-up memory reset.

**The register rule of contract 023 §2 stands unchanged at the wire
level:** `2074 == 0` → clear; `2074 != 0` → the unit reports a fault
code. What the latch changes is the SEMANTICS of the second half:

> `2074 != 0` does NOT prove a fault is active RIGHT NOW. It proves
> the unit memorized a fault since the last power-up. The memorized
> fault may be stale — the machine can be healthy, off, at rest, and
> still report its latched code.

### 1.3 What the entity was showing all along (the "entity does not lie" record)

During step 2 the Fault entity displayed the fault on a provably
healthy machine. **The entity did not lie.** It faithfully reported
exactly what the machine memorizes — the integration decodes register
2074 and nothing else. The mismatch the owner observed ("Problematique"
on a healthy machine) is the machine's own annunciator semantics, not
an integration bug: a non-zero fault word means "a fault is on
record", and the panel is the authority for whether one is in
progress.

This must be documented where users read it (FR-7/FR-8): a user who
does not know the latch will conclude the integration is stuck — the
owner himself asked for the fault to be explained. The documentation
duty of this spec is exactly that: say what the tile means.

### 1.4 The reconciliation with the 023 record (the honest tension)

The 023 record (research 023 §1.1) ends its run with the
aller-retour `0 -> 256 -> 0` — a cleared capture with 2074 = 0.
Today's run proves the fault does NOT clear on the machine's logical
off alone. Both observations are facts; the tension is in the 023
record's UNRECORDED step — the action the owner took between the
fault capture and the cleared capture was never written down. The
candidate reconciliations, neither proven:

- **[A] The 023 clear was itself a power cycle** — the owner
  unplugged/replugged (or the supply was cut) between the fault and
  cleared captures, and the relayed record summarized it as "cleared
  the fault". Plausible: it is the natural way to clear a fault coder,
  and the 023 quickstart §2 step 5 already hedged "power-cycle if it
  latches".
- **[V] The unit can also self-clear under some unobserved
  condition** (a protection releasing, a panel interaction, the
  vendor app). Nothing supports or excludes it; today's run shows
  machine-off alone does NOT do it.

Neither reconciliation changes anything for the code: the entity rule
("report the register, never guess") is identical under both. What
changes is the DOCUMENTATION: the 023-era phrasing "the fault clears
(climate → Off)" is not reliable and must not be written anywhere
again; the PROVEN clear is the physical power cut (quickstart 024
§2). The 023 files stay untouched as the historical record — their
quickstart is superseded for the clearing step by THIS spec's
quickstart (which says so, the 023 §3 precedent).

### 1.5 The stream fact the latch run also proved

The machine, logically OFF and at rest, kept answering the poll
cycle's trigger with its block burst — the owner watched the entity
display the LIVE latched word for the whole off period, which is only
possible with fresh captures flowing (the freshness gate would have
failed the cycle otherwise). **The pushed stream does not stop when
the machine is logically off** [P]. This is the same world as spec
020's AC-2 (the climate renders OFF from fresh captures) — now
confirmed with the machine off for an extended period. It is the
decisive premise of Part 6 (freshness): the only world in which the
fault entities go unavailable is a SILENT module (unplugged, out of
range, the vendor app holding the single link) — and then everything
goes unavailable together, by design.

### 1.6 Where the latch lives (the [A] mechanism reading)

The observations are consistent with one mechanism: the fault memory
lives in the heat pump controller's powered RAM, and the BLE module
relays it. The machine's logical off does not cut the electronics'
supply (the module answers while the machine is off — Part 1.5);
cutting the unit's supply cuts both, and the memory resets on the
next power-up. Whether the module's own software state (distinct
from the controller's) plays any role is unobserved [A/V] — the
PROVEN boundary for users is one sentence: **the latched code clears
when the unit's power supply is cut; nothing else observed clears
it.**

---

## Part 2 — Q1 + Q6: the sensor (name, values, class, icon)

### 2.1 The HA constraint that forces a sensor (the binary CANNOT show the code)

The owner's request: *"the error code should appear instead of
'Problematique'"* — he wants to read **E03** on the tile. The
constraint that decides the surface:

- A `binary_sensor` with `device_class: problem` renders its state
  through HA's translation layer: `on` → "Problematique", `off` →
  "OK" (in the French locale — whatever the locale, it is HA's
  localized word, not ours). **The state of a binary_sensor is a
  boolean; it cannot carry a code string.** [P — HA's entity model;
  observed live: the owner's tile showed "Problematique".]
- The code string CAN only ride the binary's `extra_state_attributes`
  — visible in the more-info dialog, invisible on dashboard tiles
  and in card states. The owner's display need is therefore
  structurally unfixable on the binary.
- A `sensor` has a free-form string state. The tile shows
  `<device> Fault code` / `E03`. **That is the surface the owner
  asked for.**

**The decision: KEEP the binary (the automation surface — "is a fault
on record?") and ADD a sensor (the display surface — "which fault?").**
The sensor is an ADDITION, not a replacement; the binary and its 023
suite stay untouched (FR-5). This supersedes contract 023 §5's "no
second entity for the code" clause through that clause's own escape
hatch ("a code sensor is a FUTURE spec's option, only if the owner
asks for it" — the owner asked, and the ask is a DISPLAY need the
binary cannot serve; the supersession is normative in
contracts/fault-code-sensor-contract.md §4).

### 2.2 The decisions (Q1 and Q6, answered together)

| Property | Decision | Why |
|---|---|---|
| Platform | `sensor` (a literal entity in `sensor.py`'s push branch, the `push_stream` precedent) | the free-form state the owner asked for (§2.1) |
| Name | **"Fault code"** | renders `<device> Fault code`; the entity_id becomes `sensor.<device>_fault_code` (`has_entity_name`) — exactly the owner's proposal; the panel's own vocabulary |
| Key / unique_id | **`pushed_fault_code`** / `<ADDRESS>_pushed_fault_code` | plane-prefixed like the registers vocabulary (`PUSHED_FAULT_CODE_MAP`); DISJOINT from the classic plane's `fault_code` RegDef key (reg 47) — the 023 data-model §6 disjointness rule: a unit that ever ran classic mode keeps its orphaned `<ADDR>_fault_code` entity, and the pushed sensor must never collide with it |
| device_class | **NONE** | the state is a free-form string; `enum` would demand a closed `options` list, violated by the open-ended unknown-code family ("unknown code (raw 4)", "unknown code (raw 8192)"…) — HA would log state errors on every new raw |
| icon | **`mdi:alert-circle`** | the repo's own fault icon (the classic `fault_code` RegDef precedent) |
| entity_category | **NONE** | an operational signal the owner wants on the dashboard (same ruling as the Fault binary, research 023 Part 3) |
| state_class / unit | none | not a measurement |

**The state values (FR-2, the single source of truth
`registers.fault_code_state`):**

| Condition | State | Meaning |
|---|---|---|
| `2074 == 0` | **`No fault`** | the fault memory is empty since the last power-up [P] |
| `2074 != 0`, raw mapped (256) | **`E03`** | the panel code, proven live (023 + today's run — two independent observations) |
| `2074 != 0`, raw unmapped | **`unknown code (raw N)`** | the contract 022 §3 honesty rule, carried into the STATE (Part 3) |
| register absent | `unknown` (None) | never a guessed "No fault" — the binary's rule, same discipline |
| stale/silent stream | `unavailable` | the shared freshness gate fails the cycle; everything goes unavailable together (Part 6) |

The healthy-state string is **"No fault"** — English (the repo's
language rule for durable artifacts; the owner's chat stays French but
entity states are code). "OK" was rejected as ambiguous, "None/empty"
as dishonest (Part 4). The French UI translation of states is not an
integration concern (states are data, not UI chrome; the vendor panel
itself displays "E03", locale-free).

---

## Part 3 — Q2: the unknown codes (confirmed)

The vendor app carries a fault table (E01-E08, E19, E29… — the
fault-list page loads `assets/faults/faults_*.json`, research 022
§2.1-c), and the app names codes (E03 = "water flow problem" [P owner,
app display 2026-10-12]). But the raw↔code correspondences are NOT
that table — the app reads the code from the cloud/report plane we
cannot see, and no raw↔code pair is proven except **256 ↔ E03**
(confirmed twice: the 023 diff-revert and today's run).

**The rule stands, now in the STATE as well as the attributes
(contract 022 §3 → contract 023 §3 → contract 024 §3):** any non-zero
raw without a recorded observation reads **`unknown code (raw N)`** —
never a guessed code, never a bit name. `PUSHED_FAULT_CODE_MAP` keeps
exactly its one proven row; extending it needs one recorded
observation per row (the 023 §4 banking protocol, unchanged; the APK
assets remain the optional static shortcut).

One precision for the data model: the map is keyed by the RAW WORD,
not by bits. A multi-bit word (e.g. raw 260 = bits 2+8) is NOT
decomposed — the vendor's multi-fault encoding is unknown [V], and
raw 260 reads `unknown code (raw 260)` until a live observation
records what the panel showed for that word.

---

## Part 4 — Q3: the healthy-state value ("No fault" — judged honest)

**Judgment: display "No fault" when 2074 == 0. An empty or unknown
state would be LESS honest, not more.**

The argument, point by point:

1. **It is a decoded truth, not an assertion beyond the evidence.**
   The honesty rule of this repo forbids GUESSED values — "no guessed
   tables in entity code" (contract 022 §3). Raw 0 = clear is not a
   guess: it is proven twice over (the 023 diff-revert's healthy and
   cleared captures; today's post-power-cut capture), on a
   CRC-gated, freshness-gated read path. A sensor that shows
   "unknown" while holding a fresh, proven-clear register would
   under-claim what it knows.
2. **The latch makes the value precise, not vague.** "No fault" has
   an exact meaning on this machine: the fault memory is empty since
   the last power-up. The semantics are documented (README, FR-7),
   so the value claims exactly what the register proves.
3. **The unknown state keeps its honest job.** "unknown" stays
   reserved for the register being ABSENT (no usable snapshot) and
   "unavailable" for a silent stream. Four distinct states, four
   distinct meanings — no state is asked to stand for another.
4. **Dashboard readability (the owner's argument) follows from the
   truth, not instead of it.** A tile reading `<device> Fault code /
   No fault` is readable precisely because the value is real.

The binary's OFF and the sensor's "No fault" decode the SAME raw
through the same discipline — they can never disagree (one register,
one source, two faces; data-model 024 §2).

---

## Part 5 — Q4: documenting the latch + the first_seen verdict

### 5.1 Where the latch is documented (the map)

| Where | What it says | Task |
|---|---|---|
| README, features table | the fault row now names the latch and both entities | FR-7 |
| README, "Entities (push mode)" table | the Fault code row with the state vocabulary + one-line latch semantics | FR-7 |
| README, Troubleshooting | the latch row: "fault shows a code while the unit is off/healthy" → the memory explanation + the power-cycle remedy; the existing Fault row gains the latch sentence | FR-7 |
| docs/CALIBRATION.md §3 | the "Fault code & firmware: no home… no entity" bullet is STALE SINCE 023 (023's tasks missed it — Part 8) — 024 rewrites it: the fault word IS exposed (023 binary + 024 code sensor), it is latched; firmware stays unexposed | FR-8 |
| specs/024 (this spec) | the record: research Part 1, data-model §4, contract §2, quickstart §2 | this file |
| specs/020-integration-reference | its §5 "Fault code & firmware in push mode" bullet is stale since 023 — **out of 024's file scope** (the 001-023 files are never modified); the recommended follow-up for the parent: revise the reference after 024 ships, the way it was revised after 022 | spec.md §3 |

### 5.2 The first_seen question (verdict: NOT usable — do not date the fault from the store)

The parent asked whether the store's timestamps (`first_seen` /
`last_seen`) could support a "this fault dates from X" attribute. They
cannot. The store's `PushCapture` fields are block-level,
connect-scoped bookkeeping (aqua_client.py, the spec-013 store):

- `first_seen` = the monotonic timestamp of the **first push of the
  BLOCK (start 2046) in the CURRENT BLE connect session**. It says
  when we started listening, nothing about the fault. The fault may
  have latched before the connect (then "first_seen" postdates the
  fault and the attribute would claim a false recency) or after it
  (then "first_seen" predates the fault and the attribute would claim
  a false age). Both errors are lies.
- The store resets on every reconnect (it is per-connect by design,
  contract 013 §3.1) — the attribute's baseline would silently reset
  with every HA restart, mid-fault.
- The unit provides no fault-timestamp register [V — none identified;
  the pushed plane carries no such home]. The machine does not tell
  us when the fault occurred.

**Decision: no timing attribute on either fault entity.** The honest
timeline the owner already has is the RECORDER: the sensor's state
history shows when HA observed each state change ("No fault" → "E03"),
which is a true statement about our observation, never a false
statement about the machine. A "latched: true" attribute was also
rejected: the latch is DESIGN behavior (always true, on every fault)
— a constant attribute is documentation pretending to be data. The
latch is documented where users read (5.1), not encoded in the entity.

---

## Part 6 — Q5: freshness (confirmed — no fault-specific gate)

**The parent's ruling is confirmed, with the precise mechanism: no
fault-specific freshness code. The shared cycle gate already does
exactly the right thing for a latched word.**

- **What the existing gate actually tests:** the 2046-block capture's
  recency (contract 013 §6.1 — a capture within `max(2 × poll, 60 s)`,
  refreshed by each cycle's trigger+burst). It tests whether the
  UNIT IS TALKING — the STREAM's freshness — **never whether a
  VALUE moved.** A latched 256 rides every fresh block identically
  and passes the gate forever: value-movement is not, and never was,
  a condition of any entity in this integration. There is no "value
  must have changed recently to be trusted" rule anywhere to exempt
  the fault from — the hazard the parent flagged (a long-stopped
  machine wrongly reading stale/unknown) is real for such a rule,
  and no such rule exists.
- **The stopped-machine world is proven:** the machine, logically OFF
  for the whole observation, kept answering the trigger with fresh
  bursts carrying the latched word (Part 1.5) [P]. A stopped PAC does
  NOT look stale — its stream keeps flowing. (Power state and stream
  state are independent planes; the climate's OFF rendering has
  always depended on this, spec 020 AC-2.)
- **The one world that fails the gate is a SILENT module** (supply
  cut, out of range, the vendor app holding the single link) — and
  then the honest state for the fault is exactly the shared
  `unavailable`: while deaf, we cannot know whether a fault appeared
  or cleared, and we refuse to guess (the 022/013 discipline). The
  power-cut clear of Part 1.4 passes through this world in order:
  silence → unavailable → stream returns → "No fault". Correct at
  every step.
- **The power-cut subtlety, stated for the record:** the freshness
  gate would NEVER mark a latched fault as stale-by-age — the latch
  is invisible to the gate (the gate reads capture age, not raw
  age), and that is the desired behavior. No change, no exemption,
  no new code. FR-6 documents this as a NON-change so no future spec
  "fixes" it backwards.

---

## Part 7 — The honest ledger

**PROVEN [P]:**
- 2074 is the fault word — confirmed twice: the 023 diff-revert and
  today's re-run (256 while the panel displayed E03).
- **The LATCH:** 2074 = 256 persisted through the machine's logical
  off (healthy, at rest) and through BLE link restarts; a physical
  supply cut cleared it to 0. The fault word is a latched memory,
  cleared only by the power-up reset (observed).
- Fresh bursts kept flowing while the machine was logically off
  (the live latched word was displayed throughout — Part 1.5).
- The raw↔code point 256 ↔ "E03" — two independent live observations
  (023's run; today's run). The app label "E03 = water flow problem"
  (023's app-oracle record, unchanged).
- The HA constraint: a `problem` binary_sensor renders localized
  on/off words ("Problematique"/"OK"); its state cannot carry a code
  (observed on the owner's tile; HA's entity model).
- A `problem` binary's `on` state means "a fault is on record" —
  under the latch, NOT "a fault is active right now" (the direct
  consequence of the latch proof; the documentation's job to say so).

**ASSUMED [A]:**
- The latch lives in the controller's powered RAM, relayed by the BLE
  module (the mechanism reading of Part 1.6 — consistent with all
  observations, not directly observable).
- The 023 run's clear was itself a power cycle (the most plausible
  reconciliation of Part 1.4 — unrecorded, hence not claimed).

**OPEN [V]:**
- The meaning of every raw except 256 (the vendor's table — the APK
  assets `assets/faults/faults_*.json` remain the static lead).
- Which protection bit 8 names (the ten-flag list order conflict —
  unchanged from research 023 §2.3; never in code).
- Whether any clear exists besides the power cut (a vendor-app
  remote clear, a panel ack — nothing observed; the docs say only
  the power cut is PROVEN).
- The 023 run's actual clearing action (Part 1.4 — unrecorded).
- Whether other faults also ride 2074 (a fault that moves nothing on
  the word is invisible to both entities — the 023 stated limit,
  unchanged).
- The multi-fault word encoding (raw 260-style words — Part 3).

---

## Part 8 — Corrections to prior records

- **quickstart 023 §2 step 5** ("Clear: climate → Off (the fault
  clears; power-cycle if it latches)") — the hedge was right, the
  main clause was not: the latch is the PROVEN world, and the power
  cut is THE clear. Superseded by quickstart 024 §2 (023's file
  stays untouched as the historical record).
- **docs/CALIBRATION.md §3** ("Fault code & firmware: no home was
  ever identified… no entity is created (never guessed)") — stale
  since spec 023 (the fault home WAS identified and exposed; 023's
  tasks updated the README but missed this bullet). 024 fixes it
  (FR-8).
- **spec 020 §5** (the reference's "Fault code & firmware in push
  mode — no pushed home was ever identified" bullet) — stale since
  023; out of 024's file scope; the parent's revision is the
  recommended follow-up (Part 5.1).
- **The 023 research §2.3 expectation management:** nothing to
  correct — the register home, the code point and the app label all
  held on today's second run. The latch ADDS the semantics layer the
  023 record did not have (it could not have: the machine was
  cleared between captures, by whatever means).