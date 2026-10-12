# Spec 020 — Aqua Temp BLE: the integration reference

Date: 2026-10-12
Repo state: main @ 3316217, manifest 0.14.0
Status: **REFERENCE** — this is not a work spec. It documents the
integration **as it works today**, verified live on the owner's PHNIX
pool heat pump (Aqua Temp app, BlueNRG module). Specs 001-019 are
archived in [`../archive/`](../archive/) with their inventory in
`../archive/README.md`.

Confidence legend (the repo-wide convention):

- **[P] PROVEN** — verified live on the production unit (diff-revert,
  panel diff, photo calibration, physically heard), or byte-exact from
  the vendor APK disassembly and re-verified by the self-tests.
- **[A] ASSUMED** — reasonable default from static analysis; cheap to
  correct in `registers.py` if evidence ever contradicts it.
- **[V] TO-VERIFY** — open on other units; settled for this machine.

---

## 1. What this integration does

A Home Assistant custom integration (`custom_components/aqua_temp_ble`)
that talks to a PHNIX pool heat pump **directly over BLE** — no vendor
cloud, no account, no WiFi (the unit has none). On the production unit
(BlueNRG module, firmware that never answers classic Modbus reads):

- **Reads** five live temperatures, the heat setpoint, the power state
  and the mode state every poll cycle.
- **Displays** them as sensors plus one climate entity (heat-only).
- **Controls** the power (on/off) and the heat setpoint — both written
  to the unit, both **mirror-confirmed**, never optimistic.
- **Proves everything before exposing it**: quantities appear behind
  owner-confirmed calibration gates; writes appear behind owner-opened
  activation gates; diagnostic probes produce the evidence.

### The one mechanism to understand

**Trigger + listen.** The unit never answers a classic Modbus read
with classic data, and never pushes spontaneously. But it **answers
every request** — any valid frame — with a **burst of four pushed
register blocks** (1001, 1046, 2001, 2046; 45 registers each). So the
integration sends ONE vendor read frame (`63030007002d3c54`) per poll
cycle as a *trigger*, listens while the burst arrives on the notify
characteristic, stores the blocks, and decodes the snapshot from
them. The 8-second read timeout is the **expected** world, not an
error. [P — six production runs + every poll since]

## 2. User stories (all shipped and verified)

### US-1 — Read the water temperatures
As a pool owner, I want the inlet and outlet water temperatures in
Home Assistant. → Sensors `Inlet water temperature` (T02, pushed reg
2047) and `Outlet water temperature` (T03, 2048), plus `Coil
temperature` (T04, 2049), `Ambient temperature` (T05, 2050) and
`Exhaust temperature` (T06, 2051). Calibrated side-by-side against
the vendor app's parameter view — the photo comparison matched. [P]
`Suction temperature` (T01, 2046) reports `unknown` on this unit: its
sensor is absent (sentinel `0x7FFD`). [P]

### US-2 — Read the setpoint
I want the current heat setpoint displayed. → The climate entity's
`target_temperature`, decoded from pushed register **1046**. Proven by
a panel diff: changing the setpoint at the panel moved 290 → 300. [P]

### US-3 — See the power state
I want to see whether the pump is running. → The climate entity shows
`OFF` when the power mirror (pushed reg **2011**) is 0, `HEAT` when it
is 1 (heat-only rule — see §4). Proven by the owner's app toggle
diff-revert 0→1→0. [P]

### US-4 — Turn the pump on/off from Home Assistant
I want real power control. → The climate's turn-on/turn-off (and
OFF/HEAT mode buttons) write register **1011** with the vendor's own
frames — physically proven: the pump started and stopped, mirror 2011
followed. Behind the `write_control` option. [P]

### US-5 — Set the heat setpoint from Home Assistant
I want to change the pool's heat target. → The climate's target slider
writes register **1046** with the vendor's own frame shape
(10.0–40.0 °C, 0.5 steps, never clamped or rounded). Proven: mirror
1046 followed 300 → 295 → 300, and the owner sets the target from HA
in daily use. Behind the `write_setpoint` option. [P]

### US-6 — Trust the display
I never want a lie on the tile. → The state is only ever updated from
a **fresh pushed mirror** that postdates the write (the confirmation
cycle); unconfirmed writes leave the state untouched and raise a
persistent notification for an unconfirmed OFF. Unconfirmed quantities
decode to `unknown`; the `push_stream` diagnostic exposes stream
health. [P]

## 3. Acceptance criteria (verified behaviours, not future work)

- AC-1: every poll cycle captures the four blocks; the five
  T-sensors display live values matching the app. [P — verified]
- AC-2: the climate tile shows OFF/HEAT following the power mirror,
  never the mode mirror (heat-only machine; the mode register 2019 is
  a diagnostic). [P — verified]
- AC-3: power writes from HA start/stop the physical pump and the
  mirror confirms within one report (~≤20 s window). [P — verified]
- AC-4: setpoint writes from HA move the panel's setpoint display;
  the mirror confirms; out-of-range/off-step requests are refused with
  a loud error, never rounded. [P — verified]
- AC-5: nothing unconfirmed is ever displayed under a certain name;
  every write is guarded by the single funnel
  (`registers.ensure_writable`) plus client-side value rails. [P — code]
- AC-6: a stale stream (unit unreachable, vendor app holding the
  single BLE link) fails the cycle honestly — entities go
  unavailable rather than showing stale values (freshness gate). [P — code]

## 4. Heat-only display rule (important)

This machine is **heat-only**: the vendor app itself locks the mode,
and the mode mirror 2019 observed values are `0` (off world, and the
on-state after our power-only writes) and `4` (the app-flow on-state)
— a display gated on the mode would render the real running state
`unknown`. Therefore the climate's `hvac_mode` follows **power alone**:
`2011 == 0` → `OFF`, `2011 != 0` → `HEAT`; absent block → `unknown`.
The mode register stays observable as a diagnostic
(`PUSHED_MODE_VALUE_TO_NAME` maps 4 → heat; unmapped nonzero values
log one INFO per session per value). [P — spec 018]

## 5. Out of scope (deliberately)

- **Mode writes.** The vendor's own mode write targets the register
  pair 1012+1013 — an unpinned value that would clobber an unknown
  register — and the app locks the mode on this heat-only machine. The
  integration refuses non-OFF/HEAT hvac modes with an explicit error.
- **The base block (regs 7-51) as a data source.** The unit never
  answers the classic FC03 read with classic data (six instrumented
  runs, every poll since). The base read survives only as the trigger
  frame. Classic-mode code paths remain for hypothetical other-firmware
  units but are unreachable on this machine.
- **Fault code & firmware in push mode.** No pushed home was ever
  identified for them; the entities are not created (never guessed).
- **Binary sensors (compressor/pump/fan/valve/switches).** Their
  base-block registers were never pinned and have no pushed homes; no
  entity is created.
- **Pairing/bonding.** The production verdict is `att-accepted-silent`
  — the write pipe is open at the ATT layer without bonding. The
  pairing ladder remains available in the config-flow test as dormant
  evidence tooling (`pair_on_connect` ships OFF).

## 6. Where the details live

| Topic | File |
|---|---|
| Architecture (cycle, store, gates, entity surface) | [`plan.md`](plan.md) |
| The wire protocol: frames, registers, sentinels, failure modes | [`research.md`](research.md) |
| Register → quantity map (the complete data model) | [`data-model.md`](data-model.md) |
| Write contracts (power 1011, setpoint 1046) | [`contracts/write-control-contract.md`](contracts/write-control-contract.md) |
| Security & the gate ladder (the "portails") | [`contracts/security-gates-contract.md`](contracts/security-gates-contract.md) |
| Install, configure, verify, calibrate, troubleshoot | [`quickstart.md`](quickstart.md) |
| State of the world + maintenance backlog for the next spec | [`tasks.md`](tasks.md) |