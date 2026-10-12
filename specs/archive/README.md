# Spec archive — 001 to 019

The project's working specs, in the order they were written. Specs 001-019
were the incremental reverse-engineering and feature kits that took the
integration from a first blueprint to a **working, owner-proven Home
Assistant integration**. They are archived as-is (untouched) because
their job is done: the integration works, and the reference
documentation for the *current* behaviour now lives in
[`../020-integration-reference/`](../020-integration-reference/).

Archive policy: nothing here is deleted or edited. Every spec stays
byte-identical to how it landed; `git log --follow` reaches every
history line. Read this file first to know what each spec still means.

Status legend used in the inventory below:

- **ACCURATE** — describes a mechanism that still exists and behaves as
  written in the shipped code.
- **PARTIAL** — parts still accurate, parts superseded or corrected by a
  later spec (the correction is named).
- **OBSOLETE** — describes a mechanism that was refuted live or fully
  replaced; kept as the historical record of why we moved on.

## Inventory

| Spec | Status | One-line justification |
|---|---|---|
| 001 — Aqua Temp BLE integration | **OBSOLETE** | Retroactive spec of the first build: assumes the base block (regs 7-51) answers FC03 polls and that the write characteristic is `ff01` — both refuted live (the unit never answers the base read, spec 009; the app never selects ff01, spec 010). Its transport constants (slave 0x63, CRC16-Modbus, ×10 scale, sentinels) survive in `modbus.py`/`registers.py`. Superseded by 008 (decode), 010 (GATT) and 013/014 (read plane). |
| 002 — Config flow scanning | **ACCURATE** | Menu-driven discovery, tiered device list, bounded rescan, manual address — implemented verbatim in `config_flow.py` and still the live flow. |
| 003 — Sensor coverage | **PARTIAL** | The decode rules (dual sentinels −9990/32765, −45..+150 °C plausibility window, per-RegDef polarity) and the read-only installer-family (H/R/P) policy are still in force in `registers.py`. The base-block-centric entity surface was superseded by the pushed plane (013/015/019); the [V] binary placeholders were never pinned and no binary entity ever materialized. The base write allowlist {8,13,14} survives only as the dormant classic-mode funnel. |
| 004 — Config flow progress fix | **ACCURATE** | The show-progress/show-progress-done contract (one task per attempt, re-entry rules) is the reason the flow survives modern HA; still live. |
| 005 — Device resolution fix | **ACCURATE** | The HA-cache resolution tiers, the every-failure-logs rule, the `not_found` error key — all live in `aqua_client._resolve_device` and the test's except branches. |
| 006 — Pairing & connect sequence | **PARTIAL** | Vendor-parity connect (2 s settle after subscribe, 2 connect attempts, extended name filter) — live and correct. The "bLE" panel gesture stays an unproven [V] (research 006 showed it is the WiFi-provisioning gesture; the owner's unit was discovered without it). The "no unlock frame" verdict stands. |
| 007 — Connect speed gap | **ACCURATE** | Stage/elapsed instrumentation shipped and still in the connect path and test lines; it was consumed by the 011/013 investigations. No behavior to retire. |
| 008 — First-read decode | **ACCURATE** | The block-frame grammar (`63 10 start qty bytecount data crc`), the reassembly rules (boundary, silence, deadline) and the match/census machinery are the live transport decode in `aqua_client.py`. |
| 009 — Base-block request | **PARTIAL** | The bounded re-send (config test: 3 re-sends 2 s apart) and the frame census shipped and still run. Its central question — "will the base block ever answer?" — was settled **no** on the production unit; the push-harvest strategy (013) superseded the plan. |
| 010 — Write characteristic | **ACCURATE** | Write=`ff03`, notify=`ff02`, selected by the app's own UUID buckets, property-blind — proven in the disassembly, shipped in `const.py`, confirmed live by every working write since. The old ff01 constant was a DEX misreading (correction recorded here and in FINDINGS.md §11.3). |
| 011 — Android vs Linux BLE | **PARTIAL** | The security probe (acknowledged re-send), the pairing ladder and the `pair_on_connect` lever are implemented and still run in the config-flow test between the first-read timeout and the push harvest. On the production unit the verdict was `att-accepted-silent` (no security error, no pairing needed) — the pairing path is dormant evidence tooling. |
| 012 — Probe not firing | **ACCURATE** | The WARNING-level visibility discipline for every decisive line (probe verdicts, build marker) shipped and is in force everywhere. |
| 013 — Pushed-blocks coverage | **PARTIAL** | The pushed-block store, the harvest, the freshness gate, `PUSHED_TEMPS` (2046-2051) and the `push_temp_confirmed` gate are the live read plane. Its premise — a *spontaneous* push stream — was corrected by 014: the unit pushes only in answer to a request. The zero-TX harvest itself is still correct machinery (used after the flow's own reads trigger the burst). |
| 014 — Query high blocks | **ACCURATE** | The trigger + listen poll cycle — ONE vendor read per cycle, the unit answers with its block burst, the store feeds the snapshot — is exactly the shipped `_async_update_push` and the reason the integration reads at all. |
| 015 — Control data | **ACCURATE** | Control homes 2011 (power) / 2019 (mode) pinned by the owner's diff-revert; the read-only climate behind `push_control_confirmed` — all live. Its "setpoint: None" was superseded by 019's 1046 pin. |
| 016 — Write control path | **ACCURATE** | The power write register 1011, the two pinned frames, the owner-triggered proof probe and the locked `WRITE_CONTROL_PROVEN` gate — live, and proven live by the owner (pump physically toggled, mirror 0→1→0). |
| 017 — Climate control | **ACCURATE** | The confirmation cycle (write → trigger → fresh mirror → verdict → snapshot push), the `write_control` option, the OFF/HEAT heat-only surface, the unconfirmed-OFF persistent notification — all live. |
| 018 — Mode values | **ACCURATE** | The power-wins display rule (hvac_mode follows power alone on this heat-only unit; the mode mirror 2019 is diagnostic-only) and the once-per-session unmapped-value INFO ledger — live. |
| 019 — Setpoint control | **ACCURATE** | The setpoint read home 1046, the value discipline (raw 100..400 in steps of 5 — 10.0-40.0 °C, 0.5 °C steps), the two-phase proof probe and the `write_setpoint` option — live and owner-proven (mirror followed 300→295→300; the owner sets the target from HA). |

## Reading order for a newcomer

If you want to understand how the working integration was reached,
read in this order: 001 (the naive world) → 008/009/010 (the unit
refuses the classic world; the real transport emerges) → 013/014 (the
pushed-plane pivot) → 015/016/017/018/019 (control, proven and gated).
Specs 002-007 and 011/012 are the connect/UX/visibility hardening
along the way.

If you want to understand how the integration *works today*, read
[`../020-integration-reference/`](../020-integration-reference/) instead.