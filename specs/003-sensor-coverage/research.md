# Research 003 — Decisions, evidence and risk policy

Status: **PROSPECTIVE** — records every decision of spec 003 with its
evidence so future work does not re-litigate it. Evidence classes: the
APK static analysis (`FINDINGS.md`, CRC-verified), the **panel
observation** (owner's photographs of the official app's parameter view
via debug password `"66"`, 2026-10), and this repo's reviewed code.

Legend: **[P]** proven (source-tagged apk/panel) · **[A]** assumed ·
**[V]** to-verify · **[D]** decision.

---

## 1. The panel observation as an evidence class

The vendor app hides a raw-parameter view behind the debug password
`"66"` [P apk — FINDINGS.md §10: the string switches the UI from
"查看状态参数" (view status parameters) to "查看控制参数" (view control
parameters)]. The owner opened it and photographed the table
(2026-10). What that evidence class proves — and what it cannot — drove
the whole spec:

- **Proves**: family structure and member counts; labels; **displayed**
  values; the existence of a **second sentinel convention** (§2); the ×10
  scale on new anchors (§3); that FINDINGS' family-semantics guesses for
  S/R/P were wrong (§13).
- **Cannot prove**: numeric register indices (the view shows no
  addresses), raw encodings of displayed values, polarity of switches,
  wire-level sentinel values.

Decision: treat panel facts as **proven for label/value statements only**
and keep every numeric-index statement `[V]` [D]. This is why spec 003
prepares placeholders + decode rules + security invariants, and leaves
pinning to the calibration contract.

## 2. Sentinel policy — two conventions, one set, one guard

**Evidence:**

- `-9990` (−999.0 °C) — recovered from the APK: the "Status parameters"
  page defines "sensor absent" as −999.0 °C [P apk — FINDINGS.md §12].
- `32765` (`0x7FFD`, displayed 3276.5 °C) — observed on the panel for
  **T01 Suction Temp** on the owner's unit, where the suction sensor is
  not installed [P panel]. 32765 sits two below the int16 maximum
  (32767/`0x7FFF`) — a classic "max value = unused" firmware convention.
- Notably, the **vendor app itself displays the sentinel as 3276.5 °C**
  [P panel] — a display bug our integration must not replicate.

**Decisions:**

1. `SENTINEL_RAWS = frozenset({-9990, 32765})` — treat **both**
   conventions as sensor-absent → state `unknown` [D]. Which convention
   each register/family uses on the wire is [V] until one live read; the
   set makes both safe from day one.
2. **Plausibility guard**: a temperature outside −45.0…+150.0 °C (raw
   −450…+1500) → `unknown` + one warning per address [D]. Bounds
   rationale: the highest legitimate temperature observed anywhere in the
   system is the H04 exhaust-high **safety threshold at 105.0 °C** [P
   panel], so 150.0 °C gives ample headroom while catching any
   `0x7FFx`-family sentinel; the floor −45.0 °C is far below any winter
   ambient a pool PAC survives, so real negatives (e.g. −10.0 °C, raw
   −100) always display. The guard also catches mis-decoded garbage
   (wrong endianness would produce e.g. `0xAA00` = 43520 → guarded).
3. Legacy `SENTINEL_RAW` stays as an alias (FR-14) so tests/tools that
   reference it (`tests/test_config_flow_walkthrough.py`) keep working
   [D].

**Rejected**: mapping only `-9990` (would display 3276.5 °C on this very
unit — the exact bug the panel exposed); hard-coding `32765` as "the"
sentinel (the APK proves the other convention exists); auto-clamping
implausible values (a clamp *displays* a lie; `unknown` is honest).

## 3. Scale — corroborated, not new

`value = int(°C × 10)` was established by the pool constants 325/315 next
to the Inlet decoder [P apk — FINDINGS.md §9]. The panel adds two
independent anchors: H04 = 105.0 °C and T02 = 17.0 °C [P panel] both
consistent with ×10. Keep `TEMP_SCALE = 10.0`; nothing to flip.

## 4. Label → numeric index — still unproven; calibration stays central

The APK's `VR.Yzd` maps display labels to register indices, but the
indices are runtime immediates hidden by `dedup_instructions`
[FINDINGS.md §6/§11.4/§12.2]. The panel shows labels+values with **no
addresses**. Therefore:

- The base-block map stays at ~80 % [P apk / V indices] — unchanged by
  this observation.
- For the T01/T04/T06, S01–S03, O01–O04 members: **zero index knowledge**
  [V]. The observation *suggests* but does not prove cross-identities
  (§5).
- The calibration surface (`registers.py`, one place) + the read-only
  probe + the panel **worksheet** (`contracts/calibration-contract.md`)
  remain the only sanctioned path to pinning. One live session pins
  everything: idle-state raws for this unit are predictable (170, 195,
  180, 175, sentinel for suction, zeros for O01–O04, 0/0/1 for S01–S03 —
  `data-model.md` §13).

## 5. Cross-identity hypotheses (NOT proof)

| Hypothesis | Evidence | Status |
|---|---|---|
| T02 "Inlet water" ≡ base reg 26 `InletWaterTemp` | labels match; both proven separately (panel / APK) | [A] identity — one live read confirms (26 == 170) |
| T03 ≡ reg 27, T05 ≡ reg 28 | same shape | [A] |
| O01/O02/O04 ≡ the "Compressor/Circulate pump/4-way valve" placeholders expected in regs 29–46 | FINDINGS §12 lists those status-page names; panel confirms the O labels | [A] expected home |
| T01/T04/T06 somewhere in regs 29–46 or high blocks | unknown | [V] |

These hypotheses justify keeping the existing placeholder keys unchanged
(`compressor`, `circulate_pump`, `four_way_valve`) — they will very
likely be the panel's O01/O02/O04 — but the spec never asserts the
identity as fact [D].

## 6. `disabled_by_default` vs `address=None` — keeping the repo rule

The task allowed either `disabled_by_default` marking for
unconfirmed-index entities, or an equivalent strategy honouring **"no
unconfirmed data presented as certain"**.

**Decision: keep `address=None` ⇒ entity not created** (spec 001 US-10
pattern) [D]. Rationale:

1. A disabled entity still *exists* with a pinned-in-code guessed
   address; enabling it would display data from a **wrong register**
   under a confident name — the exact failure the repo rule forbids.
   `disabled_by_default` solves "clutter", not "wrongness".
2. The existing registries/skip logic, probe annotation and docs all
   already speak `address=None`; a second gating mechanism doubles the
   calibration surface for no benefit.
3. Entities appearing *after* pinning is a clean, testable invariant
   (AC-3/AC-4), and it matches what users of spec 001 were told
   ("binary sensors are absent by design until calibrated").

## 7. Read-only vs write — the risk policy

**Risk analysis (spec §6 is the normative version):**

- H04 Exhaust Temp. High = 105.0 °C is a **safety shutdown threshold**
  [P panel]. R01–R11 carry per-mode **limits** (observed up to 40.0 °C)
  and regulation differentials [P panel]. An erroneous write can
  overheat the circuit, make the unit short-cycle, or **prevent
  startup** (a corrupted R max can brick the day's heating).
- The write path today reaches exactly regs 8/13/14 (+19–21 behind the
  default-off mirror switch) — all in the base block, all
  user-operational, all proven [P apk].

**Decisions:**

1. **H/R/P are never writable from HA** — no entities (so no
   number/select/switch surface), and the single write funnel gains a
   runtime allowlist guard (`ensure_writable`, FR-9) [D]. The guard is
   defense in depth: today the climate entity is the only writer, but a
   future contributor must not be able to add a write "just this once"
   without touching the allowlist.
2. **No generic write-register service** — rejected although it is the
   classic power-user request. It would bypass the allowlist by design;
   for a device where one wrong H/R write can damage hardware, the
   escape hatch *is* the hazard [D]. Calibration is read-only probing +
   editing `registers.py`, never runtime writes.
3. **T/S/O read-only, no risk**: temperatures are passive reads; S are
   physical switch inputs; O are controller-driven outputs — the
   integration observes them and never writes them [D].
4. The probe stays read-only (spec 001 contract) — it is the sanctioned
   window onto unmapped families [D].

## 8. H/R/P as diagnostic entities — rejected for now

Read-only diagnostic sensors for H02 (unit), H04 (exhaust high), R
limits… were considered and rejected [D]:

1. Their numeric indices are unknown — there is no honest data to show
   (rule of §6).
2. Displaying settings invites "why can't I change this from HA?" and
   creates pressure toward the write surface we just forbade (§7.2).
3. The probe service already gives full read access for diagnostics
   once blocks are mapped.

Revisit only in a future spec, **after** live calibration pins indices,
and still read-only.

## 9. Binary polarity — per-RegDef, not global

Observed: S03 Flow switch reads **ON** while the unit is **idle** (O01
Compressor OFF, O02 Circulate pump OFF) [P panel]. Either the pool's
external filter pump was running (flow real) or the S-family encodes
"healthy/closed" rather than "flow detected" — undecidable from one
photo [V]. Meanwhile O-family OFF-at-idle is consistent with
active-when-nonzero [A].

**Decision**: `RegDef.active_when_nonzero` per entry (None ⇒ existing
global `BINARY_ACTIVE_WHEN_NONZERO` default), so the S family can be
calibrated to the opposite polarity of O without a code change [D].
Also: **no `safety` device_class until polarity semantics are proven
live** — HA's `safety` asserts "on = unsafe", which is precisely the
unproven part [D].

## 10. Creation gating and the high-block question (phase B sketch)

The T/S/O members may live outside the base block (regs 7–51) — in the
high blocks 1001–2090 (the app's Status/Control parameter pages
[FINDINGS.md §12]) or inside the unmapped 29–46 span. Unknown [V].

- Polling the base block only is a spec-001 invariant (one FC03 read per
  cycle, no storms) and high-block **response shapes are unverified
  [V]** — so this spec does **not** extend polling [D].
- Gating (FR-7): pinning an address outside 7…51 ⇒ entity not created +
  loud warning. A permanently-`unknown` entity reading a register the
  coordinator never polls would be a silent lie — worse than absence.
- **Phase B (future spec)**, sketched for continuity: after a live
  session verifies high-block response shapes, the coordinator may gain
  an optional "extra blocks containing pinned registers" list — one
  additional read per block per cycle, serialized behind the same
  `asyncio.Lock`, still read-only. `contracts/calibration-contract.md`
  tells the user what to record for that decision (which block each
  member was found in).

## 11. Climate untouched; hvac_action is future work

O01 Compressor would enable a truthful `hvac_action`
(HEATING/COOLING vs IDLE) — but the task pins climate as unchanged, and
wiring action before O01's register (and heat-only semantics, H06 [P
panel]) is calibrated would be optimistic guessing. Out of scope
(spec §8); noted for the post-calibration spec.

## 12. D-family — known but unmapped, no speculation

D01–D06 exist [P apk — FINDINGS.md §11.4]; the D page was **not
photographed**, so there are no labels, no values, no semantics. Per the
task rule, this spec only records the family as known-but-unmapped
(`data-model.md` §8) and requires one more panel photograph before any
D work starts [D]. No placeholder, no entity, no probe annotation.

## 13. FINDINGS family-semantics corrections (recorded)

Static analysis had guessed the family meanings from adjacency; the
panel corrects three of them. Recorded so future work trusts the
observation, not the guesses:

| Family | FINDINGS §11.4 guess | Panel truth [P panel] |
|---|---|---|
| S01–S06 | "setpoint registers" | **safety inputs** (HP switch, LP switch, Flow switch observed) |
| R01–R11 | "runtime/energy registers" | **regulation thresholds** — installer temperature parameters (0.5–40.0 °C) |
| P01–P04 (P03) | "power register" | **filtration program** (Mode/Interval/Duration/Advance) |
| T01–T06 | "temperature group" | confirmed — Suction/Inlet/Outlet/Coil/Ambient/Exhaust |
| O01–O04 | "output registers" | confirmed — Compressor/Circulate pump/High fan/4-way valve |
| H01–H06(+) | "status group" | confirmed — machine settings (+ H70/H71/H72 [P apk]) |

## 14. Alternatives considered / rejected

| Alternative | Why rejected |
|---|---|
| `disabled_by_default` entities with guessed registers | displays wrong-register data once enabled — violates the repo rule (§6) |
| Expose H/R/P as read-only numbers now | no honest data (indices unknown); invites write pressure (§8) |
| Generic "write register" service for power users | bypasses the allowlist by design; one wrong H/R write can damage the unit (§7) |
| Poll high blocks in this spec | response shapes unverified [V]; would break the one-read-per-cycle invariant prematurely (§10) |
| Sentinel = 32765 only | the APK proves −9990 exists too (§2) |
| Auto-clamp implausible temperatures | a clamp displays a lie; `unknown` + warning is honest (§2) |
| `safety` device_class on HP/LP switches now | asserts "on = unsafe" — polarity unproven (§9) |
| Speculative D-family placeholders | no labels exist; the task forbids speculation (§12) |
| Optimistic entity state after writes | already rejected in spec 001 (device-confirmed state only) — unchanged |