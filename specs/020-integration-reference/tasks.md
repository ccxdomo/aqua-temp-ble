# Spec 020 — State of the world & maintenance backlog

This spec is a REFERENCE, not a work order — there are no development
tasks in it. This file records (a) what is done and proven, and (b) the
**cleanup backlog** produced by the 2026-10-12 audit: the precise,
actionable list (file + line + what to do) that a follow-up maintenance
spec/Coder should consume. No code was touched by this audit.

---

## 1. State of the world (done & proven)

| Capability | Status | Evidence |
|---|---|---|
| Config flow: discovery, test (trigger+probe+harvest), push entry | working | live use |
| 5 live temperatures + T01 `unknown` (sensor absent) | working, calibrated | photo match [P] |
| Setpoint display (1046) | working | panel diff 290→300 [P] |
| Power display (2011 → OFF/HEAT, heat-only rule) | working | diff-revert [P] |
| Power control (1011 {0,1}) | working, gate `write_control` open | probe verdict + pump heard [P] |
| Setpoint control (1046, 10.0-40.0/0.5) | working, gate `write_setpoint` open | probe verdict 300→295→300 [P] |
| Mirror-confirmed writes, unconfirmed-OFF notification | working | code + live use |
| Freshness gate, honest unavailable | working | code |
| Probes (register/power/setpoint) as diagnostics | working | live use |
| Tests: ~33 files, per-spec suites + modbus self-test | green | repo |

## 2. Cleanup backlog (actionable, file + line + action)

Audited against the push-mode production world. Items are grouped by
the decision they need; **items D1-D3 need the owner's decision**
(keep-for-other-units vs prune-to-push-only), the rest are unambiguous.

### A. Broken surfaces in the push world (fix)

| # | Where | Problem | Action |
|---|---|---|---|
| A1 | `coordinator.py:726-734` (`async_probe_block`, block "base") | On a push-mode unit `read_registers(7,45)` is a guaranteed 8 s timeout — the burst blocks never match start=7; the probe button always fails | Make "base" in push mode trigger + dump the pushed store with labels (the useful calibration view), or re-route the default to a high block |
| A2 | `button.py:59-61` ("Probe registers" button) | Hardcodes the broken `async_probe_block("base")` | Route to the fixed probe (A1) |
| A3 | `__init__.py:31` (`PROBE_BLOCKS`) + `services.yaml:27,33,35` | Selector offers "base", "1091", "1136" — base never answers (law L1); 1091/1136 are not in the unit's burst (only 1001/1046/2001/2046) → guaranteed timeouts; "High blocks are experimental" description is stale (they are now the proven read plane) | Drop or annotate base/1091/1136 as expected-timeout; update the description |
| A4 | `tools/smoke_client.py` (module docstring + read path) | Promises "reads the 45 base registers" — guaranteed timeout on this unit | Update to trigger+listen or deprecate with a banner |

### B. Obsolete texts that lie about current behavior (rewrite)

| # | Where | Problem | Action |
|---|---|---|---|
| B1 | `README.md:15` | "Status: pre-calibration" — calibration is done and verified | Update the status block |
| B2 | `README.md:27-31` | Features table: [V] rows claim suction/coil/exhaust + binaries "appear after calibration"; climate "[V] write to confirm live" | Rewrite for the push world: 6 T-sensors display (2046-2051); climate reads AND writes power/setpoint (proven); binaries never (no pushed homes); fault/firmware absent in push mode |
| B3 | `README.md:36` | "GATT write `0000ff01-…`" — refuted (write is ff03, spec 010) | Fix: service ff01, write ff03, notify ff02 |
| B4 | `README.md:37` | "Base read … 95-byte response" — presented as the working read; it is only the trigger now | Describe trigger+listen; data comes from the block burst |
| B5 | `README.md:38` | "Writes: FC10 … H1 layout" — the proven control writes are single-register frames to 1011/1046, fire-and-forget, no echo | Fix |
| B6 | `README.md:89` | "…or the ff01/ff02 GATT service UUIDs" (discovery) — the code matches ff03/ff02 | Fix |
| B7 | `config_flow.py:120` (`_build_device_list` docstring) | Docstring says "ff01/ff02 service UUIDs" while the code checks UUID_WRITE(ff03)/UUID_NOTIFY(ff02) — stale spec-002-era text | Fix the docstring |
| B8 | `strings.json:52` + `translations/en.json:52` + `translations/fr.json:52` (confirm_push) | "climate control (writes) … stay absent until the unit answers requests again" — WRONG: control is available behind `write_control`/`write_setpoint` after the built-in probes (proven). Fault/firmware absence remains true | Rewrite: monitoring sensors (temp gate) → climate read-only (control gate) → control can be enabled later in the options after the probes |
| B9 | `strings.json:41` + translations (test step) | "reading the base register block (7-51)" — the test is trigger+probe+harvest; the read's timeout is expected | Reword ("connecting and probing the unit's data report…") |
| B10 | `strings.json` no_devices/device/manual steps + translations | The "bLE" pairing gesture is presented as THE way to make the unit visible — unproven [V] (spec 006: it is the WiFi-provisioning gesture; this unit was discovered without it) | Soften: vendor-app closed + range first, gesture as a last resort |
| B11 | `docs/CALIBRATION.md:17,52-54` (+ the classic steps M/W/S) | The runbook assumes the base block answers ("press the probe button, expect the base block log") — never true on this unit | Replace with the push-world procedure (photo comparison + diff-revert + the two probes = `quickstart.md §4`), or rewrite in place pointing to spec 020 |
| B12 | `BLUEPRINT.md:32,34,139` | GATT table: "write ff01 [P]", "ff03 role unknown — do not use [V]" + the UUID_WRITE=ff01 snippet — refuted by spec 010 | Historical doc: add a SUPERSEDED banner at the top pointing to specs/020 (do not rewrite the body) |
| B13 | `FINDINGS.md:64,73,289-303` | The original ff01 write claims (a correction note already exists at §11.3) | Add a top banner: "see specs/020-integration-reference for the corrected protocol reference" |

### C. Logging verbosity (tune)

| # | Where | Problem | Action |
|---|---|---|---|
| C1 | `coordinator.py:652-655` | `"push cycle: trigger read timed out as expected (census: …)"` at INFO fires EVERY cycle (~2,880 lines/day at 30 s) — the world is proven, this is noise | Demote to DEBUG; keep WARNINGs (stream silent, trigger answered) untouched |
| C2 | `aqua_client.py:2108-2113` (`_store_push`) | First push per block per connect logs the FULL 99-byte hex at WARNING (4 lines/connect; chatty on flaky links) | Keep the one-line "block push captured: start=…" at WARNING/INFO; move the `hex=…` payload to DEBUG |
| C3 | `aqua_client.py:1277-1296` (`_enumerate_gatt`) | Full GATT table at INFO on EVERY connect | Demote the per-service/char lines to DEBUG (keep a one-line INFO summary if wanted) |

Already verified quiet: the per-frame block census is at DEBUG (spec
018), unmapped mode values log once per session per value, probe
verdicts are one WARNING line by design, the config-test build marker is
flow-time only.

### D. Dead / dormant code (owner decision: keep vs prune)

| # | Where | What | Note |
|---|---|---|---|
| D1 | Classic-mode plane: `coordinator.py` (`_async_update_data` classic branch, `from_regs`, `from_merged`, `MODE_NAME_TO_HVAC`/`HVAC_TO_MODE_VALUE`), `climate.py` classic write branches, `coordinator.async_write_register`, `aqua_client.write_registers` (+ `parse_write_echo`, the FC10-echo wait) | Unreachable on this unit (the classic read never answers); kept for hypothetical other-firmware units | If kept: document as the classic plane; if pruned: a large, test-covered deletion — the owner decides |
| D2 | `binary_sensor.py` + `registers.py` BINARIES + the `[V]` placeholders (`REG_COMPRESSOR`…`REG_FLOW_SWITCH`, lines 59-65) | Zero binary entities ever created (no base pin, no pushed homes) | Same decision; if kept, it is registry-driven and harmless |
| D3 | `agent.py` (whole file), `aqua_client.py:928` `pair()` + the pairing rungs + the `pair_on_connect` option | Dormant since the `att-accepted-silent` verdict (L5); runs only on an ATT security error in the config test | Recommend: keep `pair()` in the test path; consider removing the `pair_on_connect` production lever (never earned) |

Repo hygiene notes: `*.pyc`/`__pycache__` are properly ignored; the
`blutter/` reverse-engineering artifacts are tracked (research
provenance — keep); root-level research tools (`ble_probe.py`,
`modbus_ble.py`, `aqua_hooks.js`) are the original APK-analysis
instruments — keep as provenance or move to `tools/` in a cleanup spec.

## 3. Suggested next spec

A single **cleanup spec (021)** consuming §2: A1-A4 (probe fix) and
B1-B11 (texts) as the mandatory core, C1-C3 (logging) as the polish,
D1-D3 behind the owner's keep/prune decision. Everything else in the
integration is proven and should not be touched.