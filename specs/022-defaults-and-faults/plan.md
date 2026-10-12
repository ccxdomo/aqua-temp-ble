# Spec 022 — Plan: the design

Date: 2026-10-12
The decision record (why Option B) lives in `research.md` Part 1.
This file is the WHAT and the HOW for the Coder: the change map,
file by file, plus the exact text copy.

---

## 1. The shape of the change (VOLET 1)

### 1.1 registers.py — the switches die, the guards stay

**Delete** (with their comment blocks):
- `PUSH_TEMP_CONFIRMED`
- `PUSH_CONTROL_CONFIRMED`
- `WRITE_CONTROL_PROVEN`
- `WRITE_SETPOINT_PROVEN`

**Keep byte-identical** (the automatic protection — do not touch):
- `ALLOWED_WRITE_BASE`, `ALLOWED_WRITE_CONTROL`, `ALLOWED_WRITE_SETPOINT`
- `SETPOINT_MIN_RAW` / `SETPOINT_MAX_RAW` / `SETPOINT_STEP_RAW`
- `WRITE_FRAME_POWER_ON` / `WRITE_FRAME_POWER_OFF`, `WRITE_REG_*`
- `PUSHED_TEMPS`, `PUSHED_CONTROL`, `PUSHED_MIRRORS`, sentinels,
  plausibility, `KNOWN_LABELS`, the integrity check

**Change**:
- `ensure_writable(address, value=None)` — the signature LOSES the
  `control_allowed` / `setpoint_allowed` kwargs. The control branch
  (1011) enforces only the value allowlist {0,1}; the setpoint branch
  (1046) only the range rail. The locked-gate ValueError messages are
  REPLACED by value-discipline refusals only (the gate text names a
  probe verdict that no longer gates). New messages keep the loud
  naming style, e.g.:
  - control: `register {address} accepts only values {sorted(values)} (the value allowlist is part of the guard)`
  - setpoint: unchanged (the range/step message already exists and is
    gate-free).
- Docstrings updated: the funnel is the single write path; the
  allowlists and value disciplines are permanent (contracts 003 §6 /
  016 §4 / 019 §2.2 still hold); the human-gate paragraph is removed.

### 1.2 const.py

Delete `DEFAULT_WRITE_CONTROL` and `DEFAULT_WRITE_SETPOINT` with their
comment blocks. Everything else unchanged.

### 1.3 coordinator.py

**Delete** the four gate properties: `push_temp_confirmed`,
`push_control_confirmed`, `write_control_allowed`,
`write_setpoint_allowed` (and their docstrings).

**`AquaTempData.from_pushes`**: signature becomes
`from_pushes(cls, captures)`. The `confirmed` / `control_confirmed`
parameters and their gate branches go:
- the T-block always feeds `registers` (when the capture exists);
- the control decode (power/mode/hvac) always runs (when the 2001
  capture exists);
- the setpoint decode always reads the 1046-block.
- The honesty that REMAINS: absent block → `None`; sentinel/implausible
  → `unknown`; the heat-only display rule (power alone) unchanged;
  unmapped-mode INFO unchanged.
- Delete the vestigial `push_confirmed` field (no consumer).

**Write cycles** (`async_control_write_power` / `…_setpoint`): drop the
`control_allowed=self.write_control_allowed` /
`setpoint_allowed=self.write_setpoint_allowed` kwargs at their
`ensure_writable` call sites. Everything else in the cycles (validate →
funnel → write → trigger → fresh-mirror confirmation → verdict) is
unchanged — the mirror still decides, an unconfirmed OFF still raises
the persistent notification.

**Call sites** of `from_pushes` (the push cycle, the write
confirmations, the probe paths) drop their gate arguments.

### 1.4 climate.py

- `async_setup_entry`: push entries ALWAYS create the climate entity
  (the `coordinator.push_control_confirmed` check and its debug log go).
  The entity is created with the full surface (the `read_only` concept
  is deleted — it existed only to express the closed gate).
- `hvac_modes` property: push entity → `[OFF, HEAT]` (heat-only rule,
  spec 018) — unconditional.
- `supported_features`: `TURN_ON`/`TURN_OFF` (where the HA version
  defines them) + `TARGET_TEMPERATURE` — unconditional.
- The write methods (`async_turn_on/off`, `async_set_hvac_mode`,
  `async_set_temperature`): the gate checks go; the push branches
  always write through the coordinator's confirmation cycles. The
  non-OFF/HEAT hvac refusal stays (heat-only unit). `_refuse_write` is
  deleted (unreachable).
- `extra_state_attributes`: the `write_control`/`write_setpoint`
  markers go; keep the `last_power_write` / `last_setpoint_write`
  stage dicts unconditionally (they are the write diagnostics).
- The classic (non-push) entity path is untouched.

### 1.5 sensor.py

- Push entries: create ALL six T-sensors + the push_stream diagnostic,
  unconditionally (the `confirmed` branch and its debug logs go).
- The push `native_value` decode is unchanged (PUSHED_TEMPS lookup,
  sentinel/plausibility via the shared helper).
- fault_code/firmware: STILL not created in push mode (no proven home —
  VOLET 2 may change this later, with evidence).

### 1.6 config_flow.py

- `async_step_confirm_push`: schema is exactly
  `{vol.Required("name", default=DEFAULT_NAME): str}`. The created
  entry keeps `data = {address, name, mode: "push"}`; `options` =
  `{poll_interval, temperature_unit}` ONLY (no gate keys).
- **Live placeholders (FR-7)**: `_async_push_harvest` stashes the
  harvest captures (`self._push_caps = caps`) on success. A new pure
  helper builds the confirm placeholders from them:
  - inlet/outlet/ambient: raws 2047/2048/2050 from the 2046-capture,
    `/10` with one decimal, sentinel → `sensor absent`, absent →
    `unknown`;
  - power: raw 2011 from the 2001-capture → `off`/`on`, absent →
    `unknown`;
  - setpoint: raw 1046 from the 1046-capture → `x.x °C`, absent →
    `unknown`;
  - no mode line.
- `AquaTempOptionsFlowHandler.async_step_init`: the push branch adds
  NOTHING beyond poll_interval + temperature_unit — the four gate
  schema keys are deleted. Classic entries keep their (unchanged) form.
- The unused `DEFAULT_WRITE_*` imports go.

### 1.7 strings.json / translations/en.json / translations/fr.json

The three files stay in exact key parity. Changes:

**`config.step.confirm_push`** — new copy (EN shown; fr.json carries
the faithful translation):

```json
"title": "Name your device",
"description": "The connection test to {address} succeeded.\n\nThe unit reports live data over Bluetooth: your heat pump's sensors, power state and heat target are read directly, and its power and setpoint can be controlled from Home Assistant.\n\nWhat the test captured:\n- Power: {power}\n- Heat target: {setpoint}\n- Inlet water temperature: {inlet}\n- Outlet water temperature: {outlet}\n- Ambient temperature: {ambient}\n\nChoose a friendly name for this heat pump:",
"data": { "name": "Name" }
```

Notes: no gate sentence, no probe-verdict sentence, no "stays absent
until the unit answers" lie. Fault/firmware: mention NOTHING on the
add screen (see options/README copy for the honest one-liner) — the
screen sells what works, the docs tell the whole truth. The five
placeholder values are decoded live per §1.6.

**`options.step.init`** — keep `poll_interval` + `temperature_unit`
labels; DELETE `push_temp_confirmed`, `push_control_confirmed`,
`write_control`, `write_setpoint` labels and BOTH `data_description`
entries.

**Everything else** (user/device/scan/no_devices/manual/test/confirm
steps, errors, aborts, services) is already accurate post-021 —
unchanged.

### 1.8 README.md

- The status block: drop "quantities appear only behind owner-verified
  gates" (line ~14).
- The features table: drop "behind the `write_control` gate" /
  "behind the `write_setpoint` gate" qualifiers — control is
  unqualified.
- §"Adding the unit" step 3: drop the checkbox sentence.
- DELETE the "The gates (the honesty system)" section (the four-gate
  table + evidence column); replace with a short "What you get"
  paragraph: everything works immediately; the protections that remain
  (value rails, allowlists, mirror confirmation) in one sentence
  each.
- §"Entities" table: drop the gate column qualifiers.
- Troubleshooting: drop the "a gate is closed" row; add "Fault code /
  firmware: not displayed — no reliable source identified in the unit's
  data report so far (the vendor app's own fault table lives in the
  APK; see specs/022)".
- §"Writes go only to the two proven registers, each behind its gate" →
  "each within its permanent value guard ({0,1}; 10.0-40.0 °C in 0.5
  steps)". Drop the "keep the write gates closed until you have run
  the probes" sentence.

### 1.9 docs/CALIBRATION.md

§2's gate-opening sequence becomes "Verifying a new unit (optional)":
the same four verifications (photo comparison, app-diff, the two
probes) presented as CONFIDENCE checks for a new/unknown unit, with
their expected outcomes — no gates to open, no options to flip. §1's
"four owner-verified gates" line updated to point to spec 022 for the
rationale. The probe mechanics (byte order, diff method) are unchanged
and remain the reference method.

### 1.10 Tests

The suites touching the gate symbols (18 files — e.g.
`test_flow_harvest_013.py`, `test_control_015.py`,
`test_coordinator_push_013.py`, `test_climate_control_017.py`,
`test_write_control_016.py`, `test_setpoint_019.py`,
`test_registers_push_013.py`, `test_security_probe_011.py`):
- closed-gate assertions (entity absent / writes refused / decode
  None while unconfirmed) are REMOVED or INVERTED to the new
  unconditional world;
- gate-opening fixtures (options with the four keys) simplify to
  no-options fixtures;
- `ensure_writable` tests: the gate-locked refusals become value-rail
  refusals; the kwargs disappear from the calls;
- NEW minimal coverage: AC-2/AC-3 (entry shape + options schema),
  AC-5 (symbol absence grep), AC-7 (live placeholders with canned
  captures), AC-11 (2017/2018 in control_candidates).
- The modbus self-test and the pinned-frame assertions MUST stay green
  untouched (the frames and rails did not change).

---

## 2. VOLET 2 — the design

### 2.1 What ships in THIS spec (the Coder's scope)

1. **Diagnostics widening (FR-8)**: add
   `"2017": "unknown [V] — 13/168 candidate family (spec 022)"`-style
   entries — precisely:
   ```python
   2017: "unknown [V] — candidate, spec 022 fault hunt",
   2018: "unknown [V] — candidate, spec 022 fault hunt",
   ```
   to `registers.PUSH_CONTROL_CANDIDATES` (dict order irrelevant; the
   push_stream entity and the probe logs consume it automatically).
   No entity, no decode, no label claim.
2. **Nothing else — except the addendum**: no fault entity
   (FR-10), and the FR-13 hex restoration ALSO ships (§3.2 — the
   addendum; the machine is faulted NOW). The evidence protocol is
   documentation for the OWNER (quickstart.md); the exposure contract
   (FR-11) binds the FOLLOW-UP.

### 2.2 The phases (who does what)

- **Phase A — the Coder**: VOLET 1 complete + FR-8 + FR-13 (addendum:
  the hex restoration — the machine is faulted NOW and the passive
  capture instrument must return). This spec's tasks.md, tasks
  T1-T15.
- **Phase B — the owner**: the E03 run — LIVE as of 2026-10-12 (the
  unit is un-plumbed and in E03 NOW): follow the already-faulted
  entry (capture fault state → clear → capture healthy). The
  "does the burst keep flowing" question is ANSWERED [P — research
  Part 3.3]: it does. Output: the fault-state probe response, the
  healthy-state probe response, the panel's displayed code — and,
  from Phase A onward, the WARNING hex passively captures every
  reload.
- **Phase C — the follow-up (next spec)**: IF the diff pins a home →
  the contract activates (binary_sensor "Fault" + attributes; the code
  string only behind a proven raw↔code link; texts updated). IF NOT →
  the research record stays honest, optionally after the owner
  re-extracts the APK assets (`assets/faults/faults_*.json`) for the
  vendor's code table.

### 2.3 The untouchables (both volets)

- The value rails and address allowlists (FR-4).
- The write confirmation cycles (mirror decides; unconfirmed-OFF
  notification).
- The heat-only display rule (spec 018).
- The freshness gate, sentinels, plausibility window.
- The probes and their pinned frames.
- The dormant classic plane.
- specs 001-021, FINDINGS.md, BLUEPRINT.md (historical documents; the
  banner corrections are 021 backlog, not this spec's scope).

---

## 3. The addendum design (2026-10-12 — three owner additions)

The owner reviewed the spec in progress and added three items. None
reopens a decision; each folds into the phase map above. The analysis
record is research.md Part 3; the FRs are FR-12/13/14, the ACs
AC-16/17/18, the task anchors T6/T13/T15.

### 3.1 climate.py — the inlet pin (FR-12): NO production change, test only

Verified in 0.15.0 (research Part 3.1): `current_temperature` push
mode reads `PUSHED_TEMPS["inlet_water_temp"]` (2047) through the
shared `decode_temp_logged`; classic mode reads `data.inlet_temp`
(base reg 26); ambient (2050) never feeds the climate. The owner's
requirement is ALREADY met. The Coder:
- adds the FR-12 regression test to the climate suite (AC-16): canned
  2047 raw → value; sentinel/absent → None; canned 2050 → never the
  climate's `current_temperature`;
- touches NO climate decode code.

### 3.2 aqua_client.py — the hex restoration (FR-13)

In `_store_push_frame`'s first-capture branch (`cap is None`), restore
the contract 013 §2.1 line byte-exactly and delete the DEBUG twin:

```python
# before (spec 021 C2):
_LOGGER.warning("block push captured: start=%d qty=%d len=%d crc=OK", ...)
_LOGGER.debug("block push captured: start=%d hex=%s", ...)

# after (013 restored, FR-13):
_LOGGER.warning(
    "block push captured: start=%d qty=%d len=%d crc=OK hex=%s",
    start,
    qty,
    len(frame),
    frame.hex(),
)
```

- The comment block is rewritten: contract 013 §2.1 restored by spec
  022 FR-13 — 021 C2's demotion put the evidence below the
  operator's WARNING+ panel (contract 012 §2), and the live E03 reload
  proved the loss; the flood stays bounded ≤ 4 lines per connect
  (021's flaky-link concern is overruled by the owner).
- The `_store_push_frame` docstring (which 021 left claiming hex at
  WARNING while the code logged DEBUG) becomes true again — no edit
  beyond the citation refresh.
- `tests/test_push_store_013.py`: the C2-era assertions invert — the
  WARNING lines now REQUIRE `hex=` with the byte-exact frame hex
  (`hexes[0] == FRAME_BLOCK_1001`, …), the DEBUG hex twin count becomes
  0, and the file's pinned-grammar header comment is updated. This is
  AC-17.

### 3.3 quickstart.md (T15) — the addendum updates to the owner protocol (FR-9 + FR-14)

`quickstart.md` EXISTS (authored 2026-10-12: the step-by-step protocol
— baseline → provoke → fault capture → clear → cleared capture →
diff — plus the evidence checklist and the unexpected-cases ladder).
The addendum (T15) folds the following INTO it — content requirements
beyond FR-9's step skeleton:
- the **already-faulted entry** (the unit is in E03 NOW): panel code +
  time → probe the fault state → clear → probe the healthy state →
  diff; the healthy baseline tables live in research Part 3.3;
- the **prioritized watch list table**: block 2001 (the ~39 zeros,
  2012 bit-watch with the bit-2 = WaterFlowSwitch slot [V], the 2034
  state enum, the 2017/2018 wildcards, the 2010 self-reference, the
  2011/2019 consequence plane); block 2046 (2047/2048 no-flow drift,
  the 2046 sentinel, the 2052-2090 zero tail); blocks 1001/1046
  (static by nature — low prior);
- the **telling patterns**: 0→non-zero; new bits in 2012; E-number-
  shaped or pool-numeric values (021/089/120/217/156/157/158/389 [V]);
  sentinel moves; a register freezing mid-update;
- the **instruments, by name**: the `aqua_temp_ble.probe_registers`
  service, block `base`, return-response enabled (the dump carries
  every raw of all four blocks with labels; the `count=` field is the
  freshness tell — a count frozen between two probes = a stale
  store), the `Push stream` diagnostic's `control_candidates`
  attributes (2017/2018 join via T12), and the restored WARNING hex
  (FR-13) on every reload;
- the burst-flows-during-fault fact [P — the owner's four hex-less
  capture lines during E03] with its one-line caveat: the values of
  THAT reload were lost to the C2 regression — re-capture.

### 3.4 The task map

The addendum folds into the existing T1-T15 (no renumbering):
- **T6** — the FR-12 climate pin test (AC-16);
- **T13** — the FR-13 hex restoration (aqua_client.py + the store
  test, AC-17);
- **T15** — the quickstart with the FR-14 watch list (AC-14, AC-18);
- **T12** unchanged (2017/2018 join `PUSH_CONTROL_CANDIDATES` — FR-8);
- **T14** absorbs AC-16/AC-17 into the suites it already rewrites.