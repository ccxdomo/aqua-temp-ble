# Spec 003 — Sensor coverage: the seven parameter families

Status: **PLANNED** — written before implementation. The Coder implements
from this kit; the Reviewer validates against the acceptance criteria.

Scope: extend `custom_components/aqua_temp_ble` with the sensor and
binary-sensor coverage confirmed by the owner's **panel observation** of the
official app (debug password `"66"`): the complete parameter table of the
heat pump — seven families **T / H / R / P / S / O / D** — the **dual
sensor-absent sentinel**, and a hard **read-only security policy** for the
installer settings. The climate entity, the config flow, the options flow
and the polling cadence are untouched except where stated.

Confidence legend (same as specs 001/002, with inline source tags):
- **[P] PROVEN** — verified byte-exact in the vendor APK (`[P apk]`,
  CRC-verified frames / pool strings), or **observed in the official app's
  parameter view** via the debug password (`[P panel]` — owner's
  photographs, 2026-10). **A panel observation proves LABELS and DISPLAYED
  VALUES — never numeric register indices.** `[P live]` = confirmed by a
  BLE read (none yet in this spec).
- **[A] ASSUMED** — reasonable default, cheap to correct.
- **[V] TO-VERIFY** — cannot be pinned without one live session.
- **[D] DECISION** — design choice, no external fact to verify.

The complete family/label/value map with per-row status lives in
`data-model.md`; the normative entity table in
`contracts/entity-contract.md`; the decode rules in
`contracts/decode-contract.md`; the pinning procedure in
`contracts/calibration-contract.md`.

---

## 1. Background — the panel observation

`FINDINGS.md` §10 predicted that the app's debug password `"66"` switches
the UI from the *status parameters* view to the *control parameters* view,
exposing raw register values. The owner used it and photographed the
parameter table (2026-10). This is the biggest evidence gain since the APK
analysis: it reveals the **complete family structure** and **real values**
for the unit:

| Family | Meaning (observed) | Members known | Labels known | Values observed |
|---|---|---|---|---|
| **T** | live temperatures | T01–T06 | all 6 | yes (incl. an absent sensor) |
| **H** | machine settings | H01–H06 (+ H70/H71/H72 elsewhere [P apk]) | H01–H06 | yes |
| **R** | regulation thresholds (installer, °) | R01–R11 | no (group semantics only) | range 0.5–40.0 °C |
| **P** | filtration program | P01–P04 | all 4 | yes |
| **S** | safety inputs (switches) | S01–S06 [P apk] | S01–S03 | yes |
| **O** | outputs | O01–O04 | all 4 | yes |
| **D** | digital states | D01–D06 [P apk] | **none** — page not photographed | no |

Key observed values (the full table: `data-model.md`): T01 Suction
**3276.5 °C** (= sensor absent — the sentinel, see FR-1), T02 Inlet 17.0,
T03 Outlet 19.5, T04 Coil 19.5, T05 Ambient 18.0, T06 Exhaust 17.5;
H04 Exhaust Temp. High **105.0 °C** (a safety threshold); S01 HP OFF,
S02 LP OFF, S03 Flow ON; O01 Compressor OFF, O02 Circulate pump OFF,
O03 High fan OFF, O04 4-way valve OFF. The unit was observed **idle**
(compressor and pump off).

### 1.1 What the observation PROVES

- The family structure and member counts [P panel, corroborating
  FINDINGS.md §11.4 `VR.Yzd` [P apk]].
- The labels of T01–T06, H01–H06, P01–P04, S01–S03, O01–O04 [P panel].
- The **second sentinel convention**: T01 displays 3276.5 °C ⇒ raw 32765
  = `0x7FFD` (two below the int16 maximum) for an absent sensor [P panel] —
  in addition to the `-9990` (−999.0 °C) convention already recovered from
  the APK [P apk, FINDINGS.md §12].
- The ×10 temperature scale on independent anchors: H04 = 105.0 °C,
  T02 = 17.0 °C [P panel], consistent with the pool constants 325/315
  [P apk, FINDINGS.md §9].
- The unit is configured **"Heat only 4-way valve"** (H06) [P panel] —
  the owner's machine is a heat-only unit; mode semantics (reg 13) must
  be calibrated with that in mind.
- FINDINGS' family-semantic guesses for **S**, **R**, **P** were wrong and
  are now corrected by observation: S = safety inputs (not "setpoints"),
  R = regulation thresholds (not "runtime/energy"), P = filtration program
  (not "power") [P panel].

### 1.2 What the observation does NOT prove

- **The numeric register index of any family member.** The app view shows
  labels and values only. The base-block map stays at ~80 % [P apk /
  V indices]; for the new families the index knowledge is **0 %**. The
  calibration surface (`registers.py` + the probe tool) remains the only
  path to pinning — see `contracts/calibration-contract.md`.
- The raw wire encoding of panel values (displayed ÷10 for T, ON/OFF ↔ 0/1
  for S/O) — [A] until a live read.
- Which sentinel convention each register uses on the wire — [V].

Therefore this spec mostly **prepares the entity layer** (placeholders,
decode rules, security invariants) so that one calibration session turns
labels into entities without further code changes.

## 2. User stories

### US-1 — All live temperatures
As a pool owner, I want every temperature the unit reports — inlet,
outlet, ambient (today) plus **suction, coil, exhaust** (once their
registers are pinned) — as proper temperature sensors, so I can monitor
the heat pump fully from Home Assistant.
→ Sensor placeholders `suction_temp` (T01), `coil_temp` (T04),
`exhaust_temp` (T06), registry-driven like the existing three [D].

### US-2 — Honest "sensor absent"
As a pool owner, I want an absent sensor to show `unknown` — never the
vendor's own display of the sentinel as `3276.5 °C`.
→ FR-1 (both sentinel conventions) + FR-2 (plausibility guard).

### US-3 — Safety inputs visible
As a pool owner, I want the **HP / LP / Flow switch** states as
diagnostic binary sensors (once pinned), so a pressure or flow trip is
visible without the vendor app.
→ Binary placeholders `hp_switch` (S01), `lp_switch` (S02),
`flow_switch` (S03), entity category DIAGNOSTIC.

### US-4 — Output states visible
As a pool owner, I want **compressor, circulate pump, high fan, 4-way
valve** states (once pinned), so automations can react to real activity.
→ Existing placeholders `compressor` (O01), `circulate_pump` (O02),
`four_way_valve` (O04) — labels now panel-confirmed — plus NEW
`high_fan` (O03).

### US-5 — Installer settings unreachable from HA
As the owner, I want the installer parameters (**H**, **R**, **P**) to be
**unreachable for writing from Home Assistant** — no entities, no
services, no register writes ever — because an erroneous write to H04
(Exhaust Temp. High, 105.0 °C) or an R limit can **damage the heat pump
or prevent it from starting**.
→ §6 (security) + FR-9/FR-10.

### US-6 — Calibrate in one session
As a power user, I want to pin the new registers by editing
`registers.py` only — probe dump + the observed panel worksheet in hand —
and see the new entities appear after a restart.
→ `contracts/calibration-contract.md`; FR-7 gating; FR-14 stable aliases.

### US-7 — No unconfirmed data under a label
As the owner, I want the integration to keep refusing to display any
value whose register is not confirmed (spec 001 US-10).
→ FR-7: `address=None` ⇒ entity **not created** (the repo's established
equivalent of `disabled_by_default`; rationale in `research.md` §6).

### US-8 — No regression
As the owner, I want the climate entity, options flow, scanning config
flow and poll cadence to behave exactly as before this spec.
→ FR-12/FR-13; AC-7; NFR-4.

## 3. Functional requirements

### Decode layer (all in stdlib-only `registers.py`)

- **FR-1 Sentinel set.** `SENTINEL_RAWS = frozenset({-9990, 32765})`.
  Any raw register value equal to a member ⇒ sensor absent ⇒ state
  `unknown`. Evidence: `-9990` [P apk — app string "−999.0 °C = sensor
  absent", FINDINGS.md §12]; `32765` (`0x7FFD`, displayed 3276.5 °C)
  [P panel — T01 Suction on the owner's unit]. Which convention applies
  to which register on the wire: [V] — one live read closes it. The
  legacy `SENTINEL_RAW` stays valid as the `-9990` member (FR-14).
- **FR-2 Plausibility guard.** A temperature whose raw value falls
  outside `[TEMP_PLAUSIBLE_MIN_RAW, TEMP_PLAUSIBLE_MAX_RAW]`
  (defaults −450 … +1500, i.e. −45.0 … +150.0 °C) ⇒ state `unknown` +
  **one warning per (address) per HA session** (pattern of
  `_logged_unknown_modes`). Rationale: catches unknown future sentinels
  (e.g. other `0x7FFx` values) and mis-decoded garbage; bounds chosen
  against observed facts (H04 = 105.0 °C [P panel] leaves headroom;
  winter ambient stays far above −45 °C). The vendor app **displays**
  `3276.5 °C` — we never do.
- **FR-3 Decode helpers (stdlib-only).**
  `is_sentinel(raw)`, `is_plausible_temp(raw)`,
  `decode_temp(raw) -> float | None`, `decode_flag(raw,
  active_when_nonzero) -> bool | None` — see
  `contracts/decode-contract.md`. The coordinator's `_scaled` and the
  sensor platform consume them; no temperature decode logic remains in
  HA-dependent modules.
- **FR-4 Per-RegDef binary polarity.** `RegDef` gains
  `active_when_nonzero: bool | None` (None ⇒ global default
  `BINARY_ACTIVE_WHEN_NONZERO`, kept for calibration continuity). The S
  and O families may use different conventions (observed: S03 Flow ON
  while idle vs O01 Compressor OFF — §7 E-4).

### Entities

- **FR-5 New sensor placeholders** (all `address=None` today, created
  only after pinning): `suction_temp` (T01), `coil_temp` (T04),
  `exhaust_temp` (T06) — `device_class=temperature`, native unit `°C`,
  `state_class=measurement`, family-tagged `T01/T04/T06`.
- **FR-6 New binary placeholders** (all `address=None` today):
  `hp_switch` (S01), `lp_switch` (S02), `flow_switch` (S03) — entity
  category DIAGNOSTIC; `high_fan` (O03) — operational like its siblings.
  The existing placeholders `compressor` (O01), `circulate_pump` (O02),
  `four_way_valve` (O04) are **kept** (keys, unique ids and icons
  unchanged); their labels are now panel-confirmed [P panel] and they
  gain family tags. No device_class on any binary sensor until polarity
  semantics are proven live (§7 E-4, `research.md` §9).
- **FR-7 Creation gating.** An entity is created iff its RegDef has
  `address is not None` **and** the address lies inside the polled base
  block (7…51). An address pinned **outside** the base block ⇒ entity
  not created + one loud warning (high-block polling is a future phase,
  §8). This keeps the "no unconfirmed data under a label" rule intact
  and prevents permanently-`unknown` entities reading registers the
  coordinator never polls.
- **FR-8 No entities for H / R / P / D.** No sensor, binary, number,
  select or switch entity is ever created from these families. Read
  access for diagnostics stays available through the read-only probe
  (`probe_registers` service, spec 001) once their blocks are mapped.

### Security (detailed in §6)

- **FR-9 Write allowlist.** `registers.ALLOWED_WRITE_ADDRESSES` =
  `{8, 13, 14}` ∪ ({19, 20, 21} iff `WRITE_MODE_SETPOINT_TOO`).
  `registers.ensure_writable(address)` raises `ValueError` for anything
  else; `coordinator.async_write_register` calls it before any I/O
  (defense in depth — the climate entity is today the only writer).
- **FR-10 No generic write surface.** No "write register" service,
  no exposed writable H/R/P entities, no writes to high blocks
  1001–2090 ever (reaffirms spec 001 FR-2). The probe stays read-only
  (spec 001 contract unchanged).
- **FR-11 Import-time integrity.** `registers.py` validates at import:
  unique non-None addresses across `SENSORS ∪ BINARIES`; unique keys;
  placeholders must have `address=None`. Violation ⇒ `ValueError` at
  import (a mis-calibration cannot silently shadow one label with
  another's register).

### Regression

- **FR-12 Climate untouched.** `climate.py` is byte-identical in
  behaviour; `hvac_action` stays `None` while powered (compressor-based
  HEATING/COOLING action is future work, §8).
- **FR-13 Probe unchanged.** The probe log format and service response
  are unchanged (new labels appear automatically through
  `KNOWN_LABELS` once addresses are pinned — no format change).
- **FR-14 Stable aliases.** `SENTINEL_RAW` remains importable (=
  −9990 member of the set) so existing tests and tools keep working.

## 4. Non-functional requirements

- NFR-1 `registers.py` stays **stdlib-only**; all new decode/guard logic
  is unit-testable without Home Assistant (`tests/test_registers.py`).
- NFR-2 Minimum HA stays **2024.12**; this spec uses **no new HA API**
  (only `EntityCategory`, already used by `sensor.py`).
- NFR-3 English-only durable artifacts (repo rule); no secrets in code.
- NFR-4 Touch points limited to: `registers.py`, `coordinator.py` (decode
  wiring + write guard), `sensor.py` + `binary_sensor.py` (decode path,
  names, DIAGNOSTIC category), new `tests/test_registers.py`, existing
  tests (only if an alias migration touches them),
  `docs/CALIBRATION.md` + `README.md`. **No changes** to `climate.py`,
  `modbus.py`, `aqua_client.py`, `config_flow.py`, `entity.py`,
  `__init__.py`, `strings.json`/translations.
- NFR-5 Gates: `py_compile` on all touched files; `modbus.py` self-test;
  `tests/test_modbus.py`, `tests/test_reassembly.py`,
  `tests/test_config_flow_list.py`, `tests/test_config_flow_walkthrough.py`,
  and the new `tests/test_registers.py` — all green.

## 5. Acceptance criteria

- **AC-1** Sentinel decode: `decode_temp(-9990) is None`,
  `decode_temp(32765) is None`, `decode_temp(170) == 17.0`,
  `decode_temp(195) == 19.5`, `decode_temp(None) is None`
  (`tests/test_registers.py`).
- **AC-2** Plausibility: `decode_temp(2000) is None`,
  `decode_temp(-5000) is None`, `decode_temp(-100) == -10.0` (winter
  ambient passes); the coordinator logs **one** warning per address per
  session for implausible values.
- **AC-3** Placeholders ship disabled-by-absence: with the shipped
  `registers.py`, **no new entity is created** (all new RegDefs have
  `address=None`) — asserted by a registry-integrity test over
  `SENSORS`/`BINARIES`.
- **AC-4** A pinned sensor (test fixture sets e.g.
  `exhaust_temp.address = 33` inside 7…51) produces an entity with the
  exact key/name/unique_id/device_class/unit/state_class of
  `contracts/entity-contract.md`.
- **AC-5** An address pinned outside 7…51 (e.g. 1001+) ⇒ entity not
  created + one warning mentioning the high-block phase.
- **AC-6** Write guard: `ensure_writable(8)`, `ensure_writable(13)`,
  `ensure_writable(14)` pass; `ensure_writable(15)` raises;
  with `WRITE_MODE_SETPOINT_TOO=True`, `ensure_writable(20)` passes;
  `ensure_writable(1004)` raises (high block — never writable).
- **AC-7** Regression: for an identical register snapshot, the existing
  entities (inlet/outlet/ambient/fault/firmware/climate) report identical
  states before/after; `test_config_flow_walkthrough.py` passes
  unchanged (it uses `SENTINEL_RAW`, which still exists — FR-14).
- **AC-8** Polarity: `decode_flag(0, True) is False`,
  `decode_flag(1, True) is True`, `decode_flag(0, False) is True`
  (inverted), `decode_flag(None, …) is None`; a RegDef-level
  `active_when_nonzero=False` overrides the global switch.
- **AC-9** Integrity: the import-time check raises on a duplicate
  address / duplicate key (exercised via a test that builds a broken
  registry); the shipped registries pass it.
- **AC-10** Docs: `docs/CALIBRATION.md` gains the panel worksheet +
  pinning procedure + the H/R/P safety warning; `README.md` lists the
  full entity table including not-yet-created placeholders and the
  read-only policy.
- **AC-11** All gates green (NFR-5 list).
- **AC-12** Review verdict confirms: no H/R/P entity, no write surface
  beyond the allowlist, no probe change, `climate.py` untouched,
  no speculative D-family work.

## 6. Security — the read-only policy for installer settings

**This section is normative (FR-9/FR-10 are its enforcement).**

What is at stake [P panel for the values]:

- **H04 Exhaust Temp. High = 105.0 °C** is a **safety shutdown
  threshold**. Raising it can overheat the refrigerant circuit; breaking
  its encoding can disable the protection.
- **R01–R11** include per-mode **max limits** (observed range up to
  40.0 °C) and regulation differentials — an erroneous write can make
  the unit hunt, short-cycle, or refuse to start.
- **P01–P04** drive the filtration program — a wrong duration/interval
  mis-times the pool circulation.
- These are **installer parameters**; the vendor hides them behind the
  debug password `"66"` [P apk, FINDINGS.md §10].

Policy:

| Family | Exposure in HA | Writable from HA | Rationale |
|---|---|---|---|
| T (live temps) | sensors (once pinned) | never (no write path exists) | read-only by nature |
| S (safety inputs) | binary sensors, DIAGNOSTIC (once pinned) | never | inputs, not writable |
| O (outputs) | binary sensors (once pinned) | never | the unit drives them; forcing outputs fights the controller |
| H (machine settings) | **none** | **never** | safety thresholds (H04) |
| R (regulation thresholds) | **none** | **never** | installer regulation limits |
| P (filtration program) | **none** | **never** | schedule integrity |
| D (digital states) | **none** (unmapped anyway) | **never** | unknown semantics |

Enforcement layers:

1. **No entity** exists for H/R/P/D ⇒ no UI write surface (FR-8).
2. **Allowlist guard** at the single write funnel
   (`coordinator.async_write_register` → `registers.ensure_writable`,
   FR-9) — even future code cannot write outside {8, 13, 14 (+19–21
   behind the default-off mirror switch)}.
3. **No generic write service** — deliberately rejected (FR-10,
   `research.md` §7): a power-user "write register" action would bypass
   the allowlist and is the single most dangerous feature possible for
   this device.
4. **Probe is read-only** (spec 001 contract, unchanged) — the only
   sanctioned way to inspect unmapped families.
5. The integration never uses the vendor app or its debug password; all
   data comes from BLE reads.

## 7. Edge cases

| # | Case | Behaviour |
|---|---|---|
| E-1 | T01 Suction absent on this unit (panel: 3276.5 °C) | raw sentinel (either convention) → `unknown`; the vendor app displays the sentinel, we never do (FR-1/FR-2) |
| E-2 | Panel display unit switched to °F (H02 = F) | wire encoding of temperatures may change [V] — implausible °F-scaled values would surface as `unknown` + warning, never as garbage °C; runbook instructs keeping the panel unit at °C or re-calibrating |
| E-3 | T03 Outlet and T04 Coil both display 19.5 °C | calibration ambiguity — the worksheet procedure (`contracts/calibration-contract.md` §4) resolves it with multiple anchors / a state change |
| E-4 | S03 Flow switch reads ON while the unit is idle | polarity semantics unproven [V] — per-RegDef polarity (FR-4); entities appear only after calibration pins it |
| E-5 | A family member turns out to live in a high block (1001–2090) | entity not created + loud warning (FR-7); high-block polling is a future phase (§8) |
| E-6 | Negative winter ambient (raw −100 → −10.0 °C) | displays normally — plausibility floor is −45.0 °C, far below any real value (AC-2) |
| E-7 | A future unknown sentinel (e.g. `0x7FFF` = 32767) | plausibility guard → `unknown` + one warning; never displayed as +3276.7 °C |
| E-8 | Two labels pinned to the same register | import-time integrity check raises (FR-11) — a mis-calibration fails loudly at startup |
| E-9 | Fault code / firmware semantics | unchanged from spec 001 ([A]/[V] items U-6/U-5) |
| E-10 | Unit is heat-only (H06 "Heat only 4-way valve" [P panel]) | no behaviour change in this spec; noted for the mode-map calibration (reg 13) — a cool write may be rejected by the controller [V] |

## 8. Out of scope

- **High-block polling** (reading 1001–2090 in the poll cycle) — requires
  live-verified response shapes first; sketched in `research.md` §10 as
  phase B.
- **D-family mapping** — the D page has not been photographed; no labels
  exist, so no speculation (task rule). It stays a known-but-unmapped
  family (`data-model.md` §8).
- **H/R/P read-only diagnostic entities** — rejected for now
  (`research.md` §8); possible future spec once indices are confirmed.
- **Climate changes**, including `hvac_action` derived from O01 and
  heat-only mode awareness — future work.
- **Individual R01–R11 label capture** — not photographed; the group
  semantics are documented in `data-model.md` §5.