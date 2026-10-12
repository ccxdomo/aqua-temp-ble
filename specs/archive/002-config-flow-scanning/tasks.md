# Tasks 002 — Scanning config flow implementation

Status legend: ☐ open · ☑ done. All tasks are open at publication time
(this spec is prospective — the Coder implements, the Reviewer validates
against `spec.md` §5 and `quickstart.md`).

Order matters within each section; sections are ordered by dependency.

---

## 1. Constants (`const.py`)

- ☐ T-1 Add `SCAN_WAIT_SECONDS = 8.0` (bounded rescan delay, FR-6).
- ☐ T-2 Add `MAX_OTHER_DEVICES = 30` (other-tier cap, FR-4).
- ☐ Reuse — do **not** duplicate — `NAME_PREFIXES`, `UUID_WRITE`,
  `UUID_NOTIFY` for recognition (FR-4, `contracts/device-list-format.md`).

## 2. List builder (`config_flow.py`)

- ☐ T-3 Implement `_build_device_list(hass) -> (labels, counts)` exactly
  per `contracts/device-list-format.md` (exclusion, name rule
  `info.name != info.address`, prefix/UUID recognition, RSSI sort, cap,
  label format, counts).
- ☐ T-4 Compare UUIDs lower-cased; defensive RSSI sort key
  (`isinstance(int)`, else `-200`).
- ☐ T-5 Keep it a **pure function** of its inputs (no I/O beyond the
  history read, no logging storms) so a stdlib-only test can drive it
  with fake infos.
- ☐ T-6 (optional but recommended) stdlib-only unit test
  `tests/test_config_flow_list.py` with fake service-info objects
  covering: exclusion, unnamed label, tier order, cap, RSSI sort —
  no HA import (inject a fake `async_discovered_service_info` /
  fake infos via a seam parameter).

## 3. Flow steps (`config_flow.py`)

- ☐ T-7 Rewrite `async_step_user` as the **menu** step (FR-1):
  `async_scanner_count == 0` → abort `no_bluetooth_adapter`; empty list →
  `async_show_menu(step_id="no_devices", …)`; otherwise pick-menu with
  `description_placeholders` counts (AC-11). Menu options route to
  `async_step_device` / `async_step_scan` / `async_step_manual`.
- ☐ T-8 Add `async_step_device`: form with `vol.In(self._device_labels)`,
  sets `_address`, `_origin="device"`, unique-id guard → `test`
  (contracts/config-flow-states.md §2).
- ☐ T-9 Add `async_step_scan`: progress step (`async_show_progress`),
  `_async_wait` = `asyncio.sleep(const.SCAN_WAIT_SECONDS)`, `_scan_done`
  flag → return to `async_step_user` (same re-entry pattern as the
  existing `test` step).
- ☐ T-10 Update `async_step_manual`: keep semantics; add the pairing tip
  to the description via strings (FR-7); keep `_origin="manual"`.
- ☐ T-11 Update `async_step_test`: pass `hass=self.hass` to
  `AquaTempBleClient` (FR-9); extend failure routing with the new
  `"device"` origin (back to `async_step_device(errors=...)`).
- ☐ T-12 **Do not touch** `async_step_bluetooth`, `async_step_confirm`,
  the options flow, and the unique-id guard order (FR-10/FR-12).

## 4. Texts (`strings.json`, `translations/en.json`, `translations/fr.json`)

- ☐ T-13 Apply the exact keys/texts of `plan.md` §6 (user menu,
  device step, scan step, no_devices menu, manual description, abort
  `no_bluetooth_adapter`) — EN in `strings.json` + `en.json`, FR in
  `fr.json`; keep existing keys (test/confirm/errors) unchanged.
- ☐ T-14 Remove the replaced old `user` step description/data block;
  keep key sets identical across the three files (AC-9).

## 5. Docs

- ☐ T-15 README: update the *Configuration* section (menu flow,
  all-devices list, rescan) and add a troubleshooting row
  "unit not listed → pairing procedure (bLE)" — English only (repo rule).

## 6. Gates & QA

- ☐ T-16 Run static gates: `py_compile` on all touched files, `modbus.py`
  self-test, `tests/test_modbus.py`, `tests/test_reassembly.py` —
  all green (AC-10).
- ☐ T-17 Walk `quickstart.md` scenarios achievable on the test instance
  (V2, V3, V4, V6, V7 at minimum; V1/V5/V8/V9 with hardware) and tick
  the visual checklist — especially **V-3/V-4/V-6/V-7** from
  `research.md` §5 (dropdown order, first-step menu, menu placeholders,
  adapter-less abort).
- ☐ T-18 Reviewer: validate against `spec.md` §5 (AC-1..AC-11) and the
  two contracts; confirm no HA-version drift (NFR-1: no API outside the
  research.md §2 table).

## 7. Explicitly out of scope (do not do)

- ☒ No manifest `bluetooth` matchers (research.md §1-Q5).
- ☒ No connection-based candidate filtering.
- ☒ No changes to test/confirm/options/coordinator/client internals
  (client only gains the already-supported `hass` argument).
- ☒ No commit/push/deploy (the orchestrator handles git).