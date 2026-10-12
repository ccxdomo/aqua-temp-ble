# Spec 022 — Active by default + the fault codes

Date: 2026-10-12
Repo state: main @ 0d32de2, manifest 0.15.0
Status: APPROVED FOR IMPLEMENTATION — the design decision (gate removal,
Option B) and the fault plan are recorded in `research.md` and
`plan.md`. The fault EXPOSURE itself is conditional on live evidence and
is NOT part of the Coder's scope (see §3).

Two work packages:

- **VOLET 1 — Active by default.** The hunt is over: every surface the
  gates guarded is proven (temps photo-calibrated; power/mode
  diff-reverted; setpoint panel-diffed; both writes probe-proven
  EXECUTED + REVERSIBLE). The owner-confirmation portals go away
  entirely. What remains is the AUTOMATIC protection — the value
  rails, the address allowlists, mirror confirmation, freshness,
  sentinels — untouched.
- **VOLET 2 — Fault codes.** The vendor app demonstrably owns a fault
  vocabulary (E01-E08 and more) and decodes status flags, but the
  pushed register that carries a fault is NOT recoverable from the
  disassembly (the decode closures are dedup'd empty). This spec
  documents what IS proven, widens the live diagnostics, and hands the
  owner the reversible E03 experiment (pump not plumbed → flow fault)
  that can pin the fault home. NO fault entity ships without that
  evidence.

Addendum (2026-10-12, owner review): three owner additions are folded
into the two volets — **FR-12** (the climate's `current_temperature`
is the INLET water temperature — audited ALREADY true in code, now
pinned), **FR-13** (the capture line's hex returns to WARNING — the
021 demotion deleted the only passive fault instrument, a regression
proven live), **FR-14** (the E03 watch list is explicit and
prioritized, and the protocol gains the already-faulted entry — the
machine is in E03 NOW). They are marked "(addendum 2026-10-12)"
where they land; the record is addendum research Part 3.

---

## 1. VOLET 1 — Functional requirements

### FR-1 — Everything works immediately after adding the device

The integration is ACTIVE BY DEFAULT. A freshly added unit gets: the
six temperature sensors (live values), the climate entity (OFF/HEAT
display + setpoint read), power control (Heat/Off buttons write the
proven 1011 frames) and setpoint control (target slider writes the
proven 1046 frames) — with ZERO post-setup confirmation acts.

### FR-2 — The two add-flow checkboxes are gone

The `confirm_push` step asks for a NAME only. The
`push_temp_confirmed` / `push_control_confirmed` checkbox fields do
not exist in the schema, and the created entry stores no such options.

### FR-3 — The four portals are removed entirely (Option B)

- The per-entry option keys `push_temp_confirmed`,
  `push_control_confirmed`, `write_control`, `write_setpoint` no
  longer exist in the options flow, the coordinator, or anywhere else.
- The global switches `registers.PUSH_TEMP_CONFIRMED`,
  `registers.PUSH_CONTROL_CONFIRMED`, `registers.WRITE_CONTROL_PROVEN`,
  `registers.WRITE_SETPOINT_PROVEN` are deleted.
- The shipped defaults `const.DEFAULT_WRITE_CONTROL` /
  `const.DEFAULT_WRITE_SETPOINT` are deleted.
- The coordinator gate properties (`push_temp_confirmed`,
  `push_control_confirmed`, `write_control_allowed`,
  `write_setpoint_allowed`) are deleted; their consumers
  (climate/sensor creation, climate write surfaces, `from_pushes`
  gating) become unconditional in push mode.
- The `ensure_writable` gate threads (`control_allowed` /
  `setpoint_allowed` kwargs) are removed: the control and setpoint
  branches enforce ONLY the permanent value allowlists.
- The vestigial `AquaTempData.push_confirmed` field is removed (it has
  no consumer).
- The transport methods `aqua_client.write_control` /
  `write_setpoint` and `modbus.build_write_control` KEEP their names —
  they are the write rails, not gates.

### FR-4 — The value guards are untouched

`ALLOWED_WRITE_CONTROL = {1011: {0, 1}}` and
`ALLOWED_WRITE_SETPOINT = {1046: raw 100..400 step 5}` stay
byte-identical, forever, including their loud refusal messages. The
address allowlists stay (1012/1013/1076/… never writable). This is the
mission's key distinction: HUMAN confirmations are removed; AUTOMATIC
protections stay.

### FR-5 — Existing entries keep working with no migration

Stale option keys on existing entries are never read again; they are
dropped from `entry.options` at the owner's next options save (HA
replaces the whole options dict). No `async_migrate_entry`, no entry
data change (`mode: "push"` stays), no flow VERSION bump. The owner's
just-recreated entry becomes fully active when the new build loads.

### FR-6 — The texts tell the truth, simply

Every user-facing string that described the gate world is rewritten to
describe the integration AS IT IS:

- `strings.json` + `translations/en.json` + `translations/fr.json`:
  the `confirm_push` description (full rewrite: works immediately;
  fault code/firmware honestly "not displayed — no reliable source
  identified"); the four gate labels and the two `write_*`
  data_descriptions are DELETED with their schema keys; the options
  step keeps only `poll_interval` + `temperature_unit`.
- `README.md`: the gates sections (the "four gates" table, the
  gate-opening checklist, the gate references in feature/troubleshooting
  tables) are rewritten for the active world.
- `docs/CALIBRATION.md`: §2's gate-opening sequence becomes a
  new-unit VERIFICATION guide (optional, informational — no gates to
  open).
- Code comments/docstrings that document gates (registers.py switch
  blocks, coordinator gate properties, climate/sensor gate branches,
  config_flow schemas, button.py line ~118) are updated with the code.

### FR-7 — The confirm screen shows the captured live values

The add-flow's `confirm_push` step displays what the unit actually
reported during the test (decoded from the harvest captures: inlet /
outlet / ambient temperatures, power state, setpoint; per-value
fallback `unknown` when a block or value is absent). No "Mode" line
(heat-only unit; the mode mirror is a diagnostic). This replaces the
old all-`unknown` placeholder list — the screen now describes the
integration as it is. Only PROVEN decodes are used (2047/2048/2050
temps, 2011 power, 1046 setpoint).

### FR-12 — The climate's current_temperature is the INLET water temperature (addendum 2026-10-12)

The quantity the climate displays as "current temperature" is the
one the machine REGULATES: the water entering the unit. Push mode:
register **2047** (T02, `PUSHED_TEMPS["inlet_water_temp"]`), decoded
through the shared helper — the same source, sentinel and
plausibility rules as the inlet sensor entity (one quantity, one
source, contract 013 §4.1). Classic mode keeps the classic inlet
home (base register 26). The ambient temperature (2050, T05) NEVER
feeds the climate — it stays the separate ambient sensor, an
indicator only. Owner's rationale (2026-10-12): a pool heat pump's
thermostat regulates inlet water temperature, not ambient air.
**Code audit verdict: ALREADY TRUE in 0.15.0** (`current_temperature`
reads 2047 push / inlet classic; ambient is sensor-only — research
Part 3.1). This FR pins the behavior against regression: the Coder
changes NO production code, only adds the pin test (AC-16).

## 2. VOLET 2 — Functional requirements

### FR-8 — The fault candidates become visible in diagnostics

Registers **2017** and **2018** join `PUSH_CONTROL_CANDIDATES`
(2012 is already there) with `[V]` evidence tags, so the
`Push stream` diagnostic's `control_candidates` attributes expose
their raws alongside the control homes. Diagnostics only — no entity,
no label claiming a meaning. `PUSH_CONTROL_CANDIDATES` is
gate-independent already (no code change beyond the two entries).

### FR-9 — The E03 evidence protocol is documented for the owner

`quickstart.md` gives the owner the reversible, no-water experiment:
baseline capture → start the pump from HA → wait for the panel's fault
code → capture → clear → capture → diff. The watch list: the whole
2001 block (the probe dumps all 45 raws), eyeballs on 2012/2017/2018
and every register that read 0 at baseline. The protocol records the
panel's displayed code (expected E03; ANY code is equally valuable)
and whether the burst kept flowing during the fault.

### FR-10 — No fault entity without proof

No fault entity of any platform ships in this spec. The research
record states plainly: the pushed fault home is unknown and not
statically recoverable; the vendor's code table (E01-E08…) exists but
the raw→code map is not recovered (the APK's `assets/faults/*.json`
are not on this host). We do NOT expose an unproven fault.

### FR-11 — The exposure contract is pinned for the follow-up

`contracts/fault-evidence-contract.md` fixes the evidence bar (a
diff-revert on the pushed plane that moves with the panel's fault code
and reverts on clear) and the exposure shape (ONE `binary_sensor`
"Fault", `device_class: problem`, raw as attributes, the code string
only behind a proven raw↔code correspondence — the justified return of
the binary_sensor platform pruned by spec 021).

### FR-13 — The capture hex returns to WARNING (addendum 2026-10-12)

The first push frame per distinct block start, per connection, logs
ONE WARNING line carrying the COMPLETE frame hex — the contract 013
§2.1 format restored byte-exact:

    block push captured: start=%d qty=%d len=%d crc=OK hex=%s

- **Why (the regression):** spec 021 C2 demoted the hex to a DEBUG
  twin, but the operator's HA log panel keeps WARNING+ only
  (contract 012 §2) — the demotion deleted the ONLY passive capture
  instrument. The loss is proven live: the owner's 2026-10-12 reload
  DURING the E03 fault produced four hex-less capture lines — the
  fault-state values were thrown away at the exact moment they
  existed (the burst keeps flowing during the fault — research
  Part 3.3).
- **Flood control (inherent, unchanged from 013):** the line fires
  once per block per connection — ≤ 4 lines per connect, bounded;
  021's "chatty on flaky links" concern is OVERRULED by the owner as
  the price of the evidence.
- The DEBUG twin is DELETED (redundant once WARNING carries the hex);
  the `_store_push_frame` docstring — which 021 left claiming hex at
  WARNING while the code logged DEBUG — becomes true again.
- The probe paths are untouched: the store dump and its service
  response remain the ON-DEMAND instrument (research Part 3.2).

### FR-14 — The E03 watch list is explicit, prioritized, and starts from the faulted state (addendum 2026-10-12)

The owner's unit is un-plumbed and in E03 NOW. The quickstart protocol
(FR-9) therefore:
- gains the **already-faulted entry**: capture the fault state FIRST
  (probe response + the panel's code), then clear, then capture the
  healthy state — the healthy baseline is already recorded (research
  Part 3.3);
- carries the **prioritized watch list** verbatim (research Part 3.3):
  block 2001 — the ~39 baseline-zero registers (a 0→non-zero move is
  the prime fault-home hit), 2012 (new bits — bit 2 is the
  WaterFlowSwitch slot IF the app's ten-flag list order is the bit
  order [V]), 2034 (the state enum that read 4 idle / 0 healthy),
  2017/2018 (the already-nonzero wildcards), 2010 (the self-reference
  marker), 2011/2019 (the consequence plane); block 2046 — 2047/2048
  (inlet/outlet drift, the physical no-flow signature), the 2046
  sentinel, the 2052-2090 zero tail; blocks 1001/1046 — static by
  nature, any move is a low-prior candidate;
- names the **telling patterns**: 0→non-zero; new bits in 2012;
  E-number-shaped or pool-numeric values (021/089/120/217/156/157/
  158/389 [V]); sentinel moves; a register freezing mid-update;
- uses the CURRENT-build instruments by name: the
  `aqua_temp_ble.probe_registers` service with return-response (the
  store dump carries every raw of all four blocks, and its `count=`
  field is the freshness tell), the `Push stream` diagnostic's
  `control_candidates` attributes (2017/2018 join via FR-8), and —
  once FR-13 ships — the WARNING hex on every reload.

---

## 3. Acceptance criteria

### VOLET 1

- **AC-1** A fresh install: add device → name it → the six sensors
  show live values within the first poll cycles; the climate shows
  OFF/HEAT + the setpoint; Heat/Off buttons and the target slider
  write immediately. No gate screen, no checkbox, no options toggle
  anywhere in the flow. [tests + manual]
- **AC-2** The `confirm_push` schema is exactly `{name: str}`. The
  created entry's `options` contain only `poll_interval` and
  `temperature_unit`. [test]
- **AC-3** The options flow (push entries) offers exactly
  `poll_interval` + `temperature_unit`. Saving options does not
  reload the entry and drops any stale gate keys from a pre-022
  entry. [test]
- **AC-4** `ensure_writable` refuses 1011 with a value outside {0,1}
  and 1046 with a value outside 100..400-step-5 with the SAME loud
  messages as 0.15.0; every other register is refused with the
  spec-003 message. No gate kwargs remain in the signature. [tests]
- **AC-5** The removed symbols are gone: `PUSH_TEMP_CONFIRMED`,
  `PUSH_CONTROL_CONFIRMED`, `WRITE_CONTROL_PROVEN`,
  `WRITE_SETPOINT_PROVEN`, `DEFAULT_WRITE_CONTROL`,
  `DEFAULT_WRITE_SETPOINT`, the four option keys, the four coordinator
  gate properties — `grep` over `custom_components/` finds none (the
  transport `write_control`/`write_setpoint` methods and
  `build_write_control` remain). [CI grep in tests]
- **AC-6** A pre-022 entry (options storing the old keys, any values)
  becomes fully active under the new code with no action; the
  pre-022 world's closed-gate behaviours no longer exist to test. The
  suites that asserted closed-gate surfaces are inverted or removed.
  [tests]
- **AC-7** The confirm screen shows decoded live values from the
  harvest (temps, power, setpoint) with per-value `unknown` fallback;
  no mode line. [tests with canned captures]
- **AC-8** strings.json / en.json / fr.json: no text mentions gates,
  checkboxes, calibration confirmation, probe-verdict requirements or
  "stays absent until the unit answers"; the three files keep exact
  key parity; the fault/firmware sentence states the real reason.
  [test + manual read]
- **AC-9** README.md and docs/CALIBRATION.md contain no instruction
  to open a gate/checkbox; verification steps are presented as
  optional checks. [manual read]
- **AC-10** The full suite and the `modbus.py` self-test stay green
  (the pinned frames, allowlists and value rails are byte-asserted
  unchanged). [CI]
- **AC-16** (addendum) A push-mode climate test pins FR-12:
  `current_temperature` decodes a canned 2047 raw (145 → 14.5 °C,
  display-unit converted), decodes `None` when 2047 is
  sentinel/absent, and a canned ambient (2050) value NEVER appears as
  `current_temperature`. [test]

### VOLET 2

- **AC-11** The `Push stream` entity's `control_candidates` include
  2012, 2017, 2018 (raw + evidence tag) in every push snapshot.
  [test]
- **AC-12** `probe_registers` block `2001` output labels 2017/2018
  with their `[V]` tags (labels already present — kept). [test]
- **AC-13** No fault entity exists: `PLATFORMS` remains
  `[climate, sensor, button]`; `binary_sensor.py` does not exist.
  [test]
- **AC-14** The quickstart E03 protocol is executable as written with
  the CURRENT 0.22 build (no tooling gap): every step names the exact
  service/button and the exact log lines to compare. [manual]
- **AC-15** The evidence contract exists and states the bar: no fault
  entity may be created in any follow-up without a recorded
  fault-diff-revert on the pushed plane. [spec review]
- **AC-17** (addendum) The store test pins FR-13: the FIRST frame per
  distinct start logs ONE WARNING `block push captured: … crc=OK
  hex=<99-byte hex>` (hex present, byte-exact against the canned
  frames), a repeat frame of the same start logs NO new WARNING, and
  NO DEBUG hex twin exists. [test]
- **AC-18** (addendum) The quickstart's E03 protocol carries the
  FR-14 watch list (priorities, per-register expectations, telling
  patterns, the already-faulted entry) and names the exact
  probe-service call with return-response. [manual review]

---

## 4. Out of scope

- **The fault entity itself** — conditional on the owner's E03
  evidence run; a follow-up spec consumes the contract (FR-11).
- **The APK assets re-extraction** (`assets/faults/faults_*.json`) —
  optional static lead for the owner, documented in research §2.1-c;
  not Coder work.
- **The dormant classic plane** (base-block paths, classic climate
  write branches, `from_regs`/`from_merged`) — untouched, as in 020.
- **The probes** (register / power / setpoint) — unchanged; they
  remain the evidence instruments, now purely informational.
- **The pairing tooling** — pruned/dormant per 021/020; untouched.
- **Mode writes, mute writes, installer registers** — still never
  writable (non-transference stands).
- **specs 001-021** — never modified; 020 remains the historical
  reference for the gate era.