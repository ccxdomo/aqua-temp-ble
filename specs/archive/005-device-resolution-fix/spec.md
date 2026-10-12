# Spec 005 — Fix device resolution & make connection-test failures visible

Status: **PLANNED** — written after a production diagnosis session on a live
Home Assistant instance (post-`5aa887b`). The Coder implements from this kit;
the Reviewer validates against the acceptance criteria.

Scope: the **device resolution** inside `aqua_client._resolve_device()`, the
**failure logging** of the config-flow connection test
(`config_flow._async_test`), the **test budget arithmetic**
(`TEST_TIMEOUT` / `max_attempts`), and **one new user-facing error key**
(`not_found`) with its translations. Nothing else changes. The config flow's
overall design (menu, list, tiering, progress contract) is spec 002's and
004's and is preserved byte-for-byte in observable behaviour; the Modbus
layer, sentinels, write guard and entities are untouched (spec 003).

Confidence legend (same as specs 001-004):
- **[P] PROVEN** — verified in the HA sources at our minimum version
  **2024.12.0** AND at **2026.10.1** (the era the affected instance runs
  on), in the pinned libraries (habluetooth 3.6.0/7.1.2,
  bleak-retry-connector 3.6.0/4.7.1, bleak 0.22.3/3.0.2), or in this
  repo's reviewed code. Exact references in `research.md`.
- **[A] ASSUMED** — reasonable default, cheap to correct.
- **[V] TO-VERIFY** — needs the live instance / real unit; listed in
  `quickstart.md`.
- **[D] DECISION** — design choice (nothing external to verify).

---

## 1. Background — the production failure being fixed

After the spec-004 fix, the flow works: the device-selection screen lists
the heat pump (`BlueNRG-C0941C — 08:06:A6:C0:94:1C — RSSI -67`), the test
step runs, and failures route back to the form. But the **connection test
still fails** with the generic "Failed to connect to the device" message —
and produces **no log line at all**, so the real cause is invisible.

`research.md` proves, from source:

1. **Every test failure maps to a UI key without logging**
   (`_async_test`'s except branches) — the user can never see which stage
   failed or why.
2. **The 20 s test budget is smaller than one legitimate connect attempt**:
   bleak-retry-connector hardcodes 20 s per attempt
   (`BLEAK_TIMEOUT`), our `CONNECT_TIMEOUT = 15 s` constructor kwarg is
   dead for the connect call, and the budget must also cover notify setup
   + an 8 s read. Any connect that doesn't succeed quickly is silently
   cancelled mid-flight and reported as `cannot_connect`.
3. **Budget cancellation is structurally silent**: `CancelledError` is a
   `BaseException` that bypasses every `except Exception` in bleak-retry-
   connector and our client; `asyncio.timeout` converts it to `TimeoutError`,
   which `_async_test` swallows into `"cannot_connect"` with no log.
4. **Inside HA, the "raw bleak scan" fallback steps never scan**: bleak's
   `BleakScanner`/`BleakClient` are globally monkeypatched by the bluetooth
   manager (`install_multiple_bleak_catcher`), so our steps 2-3 are extra
   cache lookups with misleading log text ("scanning by name prefix…").
   The list and resolution step 1 read the same manager dict — coherent —
   but the connect path reads the *scanners'* current data (195 s
   retention), a genuine temporal gap that fails loudly ("No backend…"),
   not silently.
5. The remaining unknown is **why the connect stalls** (vendor app holding
   the unit's single BLE link is the leading hypothesis) — undiagnosable
   until the logging fix ships. The fix therefore puts diagnosis first.

## 2. User stories

- **US-1** As a pool owner, when the connection test fails, I want the log
  to tell me exactly what happened (device not found / connect refused /
  budget expired / read timeout / CRC), so I can act on the real cause
  instead of guessing.
- **US-2** As a pool owner, when the unit is no longer visible to Home
  Assistant, I want a distinct message telling me to put the unit in
  pairing mode and scan again — not the generic "failed to connect" advice
  about the vendor app.
- **US-3** As a pool owner, when the unit is simply slow to connect, I
  want the test to give the connection an honest chance to succeed
  instead of silently cutting it off at 20 s.
- **US-4** As the developer, I want resolution provenance in the logs
  (which cache tier or scan produced the device), so "listed but cannot
  connect" cases become diagnosable in one run.
- **US-5** As the owner, I want standalone mode (tools/smoke_client.py,
  unit tests) to keep its exact behaviour: raw bleak scans, same timeouts.
- **US-6** As the developer, I want the walkthrough tests to encode the
  new failure routing (not_found, budget timeout) and assert the logging,
  so the silent-failure class cannot regress unnoticed.

## 3. Functional requirements

### Block A — failure logging (P0: ships the diagnosis)

- **FR-1** `aqua_client` exposes its exception-chain renderer as a public
  helper (`exc_chain`) so other modules can log full cause chains; the
  private name `_exc_chain` remains as an alias (no behaviour change).
- **FR-2** `config_flow._async_test` logs every failure branch:
  - `AquaTempDeviceNotFoundError` → key `not_found`, log at WARNING with
    the exception message;
  - `AquaTempConnectionError` → key `cannot_connect`, WARNING with the
    exception chain;
  - `AquaTempTimeoutError` → key `no_response`, WARNING with the chain;
  - `AquaTempResponseError` → key `crc_error`, WARNING with the chain;
  - `TimeoutError` (test budget expiry) → key `cannot_connect`, WARNING
    naming the budget value and the likely causes (unit not accepting the
    connection in time — vendor app holding the single link);
  - unexpected `Exception` → unchanged `_LOGGER.exception`.
  Every log line includes the target address.
- **FR-3** `aqua_client._resolve_device` logs its provenance at each step
  in HA mode (DEBUG on each successful tier, WARNING when a lower tier is
  entered, truthful texts: "connectable cache", "non-connectable cache",
  "name-prefix match over the discovery history" — never "scanning" when
  inside HA), and keeps its existing standalone-mode log texts.

### Block B — honest budget arithmetic (P1)

- **FR-4** The config-flow test uses `max_attempts=1` (a bounded probe;
  the coordinator's steady-state connect keeps `max_attempts=2`). One
  attempt is the only strategy the budget can honestly cover
  **[P — brc's per-attempt timeout is a hardcoded 20 s]**.
- **FR-5** `TEST_TIMEOUT` is raised from 20.0 s to **40.0 s**: covers one
  full honest attempt (≤ 20 s connect + ~2 s notify setup + ≤ 8 s read +
  overhead) with margin **[P — research.md §5]**. This supersedes spec 004
  AC-4's "20 s budget" value (the *routing key* for budget expiry is
  unchanged: `cannot_connect`); spec 002/004 step texts are unaffected.
- **FR-6** The misleading comment claiming `const.CONNECT_TIMEOUT` stays
  effective per attempt inside HA is corrected: it is dead for brc connects
  (brc hardcodes 20 s) and only bounds the standalone raw-client path
  **[P]**. The constant itself is kept (standalone semantics unchanged).

### Block C — explicit HA-side resolution (P1)

- **FR-7** In HA mode (`hass is not None`), `_resolve_device` calls the HA
  APIs **explicitly** instead of relying on the bleak monkeypatch
  **[P — research.md §3.1]**, in this order (each step logged per FR-3):
  1. `async_ble_device_from_address(hass, address, connectable=True)`
     (manager connectable history — the list's source of truth);
  2. `async_ble_device_from_address(hass, address, connectable=False)`
     (HA's own fallback order **[P]**; logged as "only visible via a
     non-connectable scanner — connection may fail");
  3. name-prefix match over
     `async_discovered_service_info(hass, connectable=True)` (the
     MAC-may-have-changed case; same effective data as today's patched
     `discover()` **[P]**), keeping today's multi-match and
     different-address warnings.
  The import stays lazy with the existing `try/except ImportError`
  standalone guard.
- **FR-8** Raw `BleakScanner` usage (find_device_by_address / discover)
  moves under the `hass is None` branch and is **byte-identical in
  behaviour and log text** to today (standalone tools and tests).
- **FR-9** Resolution returning `None` raises the new
  `AquaTempDeviceNotFoundError` (subclass of `AquaTempError`) instead of
  `AquaTempConnectionError`. `aqua_client.connect()` no longer raises the
  unlogged "no Aqua Temp device found" `AquaTempConnectionError`; the new
  exception propagates (still an `AquaTempError`, so the coordinator's
  `except AquaTempError` handling is unchanged **[P — repo]**).

### Block D — user-facing error split (P2)

- **FR-10** New error key `not_found` in `strings.json`,
  `translations/en.json`, `translations/fr.json`:
  - EN: "Home Assistant can no longer see this device nearby. Put the
    unit in pairing mode (hold the panel button for about 5 seconds until
    the display shows \"bLE\"), make sure it is powered on, then scan
    again."
  - FR: "Home Assistant ne voit plus cet appareil à proximité. Mettez
    l'unité en mode appairage (maintenez le bouton du panneau environ 5
    secondes jusqu'à l'affichage « bLE »), vérifiez qu'elle est allumée,
    puis scannez de nouveau."
  `cannot_connect`'s text is unchanged (its advice — vendor app closed,
  range — remains correct for connect refusals and budget expiry).
- **FR-11** `not_found` routes to the same origin forms as the other
  errors (`manual` for manual entry, `device` otherwise — spec 004 FR-4
  routing, unchanged).

### Block E — tests (P2)

- **FR-12** `tests/test_config_flow_walkthrough.py` gains: a
  `not_found` routing case (FakeClient fault =
  `AquaTempDeviceNotFoundError`), a budget-expiry case (monkeypatched small
  `TEST_TIMEOUT` + a client whose connect outlives it → `cannot_connect`),
  and assertions that each failure branch logs (caplog) with the address.
  Existing cases keep passing (`cannot_connect`/`no_response`/`crc_error`
  routing matrix — spec 004).
- **FR-13** A unit test covers `_resolve_device`'s HA path with a fake
  bluetooth module injected: connectable hit, connectable-miss →
  non-connectable hit, all-miss → name-prefix hit, and all-miss →
  `AquaTempDeviceNotFoundError` from `connect()` (provenance log asserted).
- **FR-14** Gates: `python3 -m py_compile` on touched files; all five
  suites green (`test_config_flow_walkthrough.py`,
  `test_config_flow_list.py`, `test_modbus.py`, `test_reassembly.py`,
  `test_registers.py`). Durable artifacts in English (repo rule).

## 4. Non-functional requirements

- **NFR-1 Minimal & targeted**: changes confined to
  `custom_components/aqua_temp_ble/{config_flow.py, aqua_client.py,
  const.py (comment only), strings.json, translations/en.json,
  translations/fr.json}` and `tests/test_config_flow_walkthrough.py`
  (+ a new `tests/test_resolve_device.py` if FR-13 needs its own file).
- **NFR-2 Untouched**: `modbus.py`, `coordinator.py` (except no change at
  all), `registers.py`, all entity platforms, `manifest.json`, the
  scanning UX of spec 002 (menu/list/tiering/`SCAN_WAIT_SECONDS`), the
  progress-step contract of spec 004, sentinels/write-guard (spec 003).
- **NFR-3** No HA API newer than 2024.12.0 is used:
  `async_ble_device_from_address` and `async_discovered_service_info`
  exist with identical semantics at 2024.12.0 and 2026.10.1 **[P]**.
  `async_address_reachability_diagnostics` / `async_request_active_scan`
  (2026-era only) are explicitly NOT used.
- **NFR-4** Standalone mode (`hass is None`) is behaviour-identical to
  `5aa887b` (raw scans, same timeouts, same log texts).
- **NFR-5** The fix does not depend on the bleak monkeypatch being
  installed (explicit HA APIs), nor on the HA version (verified at both
  extremes).

## 5. Acceptance criteria

- **AC-1 (P0, the blocker)** On the live instance, a failed connection
  test produces at least one WARNING-or-higher log line from
  `custom_components.aqua_temp_ble` naming the failing stage, the address,
  and the underlying exception chain. No failure path maps to a UI key
  without a log line.
- **AC-2** When the address is in neither manager history tier and no
  name-prefix match exists, the origin form shows `not_found` with the
  pairing-mode/rescan advice, and the log says the device was not found.
- **AC-3** When the connect is slow (does not complete within 40 s), the
  test ends with `cannot_connect`, and the log explicitly names the budget
  expiry and its likely causes — no silent cancellation.
- **AC-4** When the unit accepts the connection within one honest attempt,
  the test succeeds end-to-end (connect + 45-register read + confirm form)
  — cases that failed at 20 s purely due to the budget now pass **[V]**.
- **AC-5** A device only visible via a non-connectable scanner is still
  offered for connection (non-connectable fallback — unchanged behaviour)
  and the resolution log states the connect may fail; a subsequent
  "No backend…" connect failure is logged at ERROR with its chain **[P —
  research §2.3]**.
- **AC-6** All previously-loud failures stay loud and correctly keyed:
  terminal connect errors → `cannot_connect` + ERROR log; read timeout →
  `no_response` + WARNING; CRC → `crc_error` + WARNING.
- **AC-7** The standalone tools still work identically (raw scans, 5 s
  timeouts, same log texts) — covered by FR-8/FR-13 and a smoke run.
- **AC-8** The walkthrough tests encode the new routing (not_found,
  budget expiry) and log assertions; all five suites green; red-first for
  the budget-expiry and not_found cases (they fail on current code).
- **AC-9** No regression of spec 004's progress contract: every failure
  still routes through `progress_done` to the origin form; no ValueError.
- **AC-10** Steady-state coordinator polling is unchanged (still
  `max_attempts=2`, no extra logging in the hot path beyond the existing
  connect-failure ERROR lines).

## 6. Edge cases

| # | Case | Behaviour |
|---|---|---|
| E-1 | Device in connectable history + live scanner path | normal connect path; resolution DEBUG line names tier 1 |
| E-2 | Device listed but last advertisement 195-495 s old (manager history only, no live scanner record) | resolution succeeds (tier 1); connect raises "No backend…" → ERROR log + `cannot_connect` (loud; the genuine list/connect gap, research §2.3) |
| E-3 | Device seen only by non-connectable scanners | tier-2 fallback with WARNING "connection may fail"; connect fails loudly if no connectable path exists |
| E-4 | Device absent from all history tiers, no name match | `AquaTempDeviceNotFoundError` → `not_found` + WARNING log; user advised pairing mode + rescan |
| E-5 | MAC changed (unit re-advertises under a new address, name matches) | tier-3 name-prefix match with the existing different-address WARNING; connection uses the new address |
| E-6 | Connect hangs (vendor app holds the unit) | budget expiry at 40 s → `cannot_connect` + explicit budget-expiry WARNING log |
| E-7 | Connect succeeds at ~35 s, read starts with < 5 s budget left | read's own 8 s `REQUEST_TIMEOUT` may fire first → `no_response` + WARNING (both paths now logged; whichever wins is deterministic by timestamps) |
| E-8 | Rapid retry after failure | spec 004 E-8 unchanged: one new task per attempt, stale `_test_error` reset |
| E-9 | Flow closed mid-test | spec 004 E-5 unchanged: manager cancels the task; CancelledError unwinds silently (no log, correct — nothing failed) |
| E-10 | Budget cancels a connect established in its final milliseconds | wrapper `finally` releases the slot **[P]**; possible stale BlueZ link is detected by brc's already-connected swap on the next attempt **[H — research §4.3]**; the new logs expose it if it happens |
| E-11 | Multiple name-prefix matches (several BlueNRG devices around) | existing multi-match WARNING; first match wins (insertion order — unchanged from the patched `discover()` behaviour **[P]**) |
| E-12 | `bleak_retry_connector` absent (standalone) | raw-client path unchanged; resolution uses raw scans only |

## 7. Out of scope

- Any change to why the unit stalls (vendor app, firmware) — hardware
  diagnosis follows the shipped logs ([V] items).
- Using 2026-era APIs (`async_request_active_scan`,
  `async_address_reachability_diagnostics`) even as enhancements.
- Touching the coordinator's connect strategy (`max_attempts=2` stands
  there; no budget wraps steady-state polling).
- Passive discovery / manifest matchers (spec 002 Q5 decision stands).
- Any UI redesign beyond the one new error key.