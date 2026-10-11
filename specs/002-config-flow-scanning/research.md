# Research 002 — Design decisions & HA API verification

Status: **PROSPECTIVE** — every HA API this spec relies on was verified
against the exact minimum supported version, **tag `2024.12.0`**, and its
pinned libraries (`habluetooth==3.6.0`, `home-assistant-bluetooth==1.13.0`,
`bleak==0.22.3` — see `bluetooth/manifest.json` + `package_constraints.txt`
at that tag). Sources were fetched and read during spec writing; file/line
references below. Anything not in §2 is a hypothesis and is labelled.

Legend: **[P] PROVEN** (read in the sources, reference below) ·
**[H] HYPOTHESIS** (not fully verifiable statically — Coder/reviewer
checklist in §5) · **[D] DECISION** (design choice, nothing to verify).

---

## 1. The seven design questions

### Q1 — Filtering: name / connect / both?

**Decision: no list-time filtering beyond tiering; the live test is the
compatibility oracle. [D]**

- Name-only filtering is the current bug: HA synthesizes
  `name = local_name or device.name or address` **[P, §2.2]** — an unnamed
  BlueNRG unit has `name == address` and can never match a prefix.
- Advertised service UUIDs (ff01/ff02) are **not guaranteed** in the
  advertisement on this module **[H]** — the task brief states it and the
  vendor app itself scans **by name only** [P — FINDINGS.md §3], implying
  even the vendor does not rely on advertised UUIDs. We use them as a
  *secondary, opportunistic* recognition signal (tier 1), never to hide
  anything.
- Connect-to-every-candidate pre-filtering (reliable but slow: 15 s
  connect timeout × N devices) was rejected: a form step must be instant,
  the unit accepts one connection at a time, and the flow already has a
  live test. **[D]**
- Net: **every** discovered device is offered (tiered, capped); any pick
  gets validated by the existing 20 s Modbus read. Wrong picks cost one
  bounded test and return with a clear error.

### Q2 — Are unnamed devices really in the HA history?

**Yes. [P]**

- `async_discovered_service_info(hass, connectable=True)` returns
  `histories.values()` of the bluetooth manager **[P, §2.1]**: the
  histories are dicts keyed by **address** — every device that advertised
  anything gets an entry; names are not a condition of storage.
- The service-info `name` falls back to the address **[P, §2.2]** (exact
  lines in `habluetooth==3.6.0`, the version 2024.12 pins). So an unnamed
  device is present with `name == address`.
- **Handling**: `has_real_name = info.name != info.address`; label
  "Unnamed device" otherwise. `device.name` may also populate from the
  BlueZ cache between advertisements; the address-equality rule covers
  both cases. **[D]**
- Entries are pruned by the availability watchdog
  (`UNAVAILABLE_TRACK_SECONDS = 300` in `bluetooth/const.py` at 2024.12
  **[P]**) — the list contains devices seen recently, which is exactly the
  set we want; RSSI is the latest advertisement's.
- The list reads `connectable=True` (matching the existing code and
  `aqua_client` semantics); devices seen only by non-connectable proxies
  are not listed — they cannot be connected to anyway. **[D]**

### Q3 — Rescan without leaving the flow

**Decision: "Scan again" menu option → progress step → back to the user
step. [D]** Mechanics all proven:

- Config flows are state machines; returning a new result from
  `async_step_scan` re-renders the flow in place — same pattern as the
  repo's existing `test` progress step **[P, this repo's
  `config_flow.py`]** and `async_show_progress` **[P, §2.4]**.
- HA's local scanner runs continuously and in **ACTIVE** mode by default
  at 2024.12 (`bluetooth/__init__.py`: `BluetoothScanningMode.PASSIVE if
  passive else ACTIVE`) **[P, §2.5]** — the flow never needs to start a
  scan; it re-reads the history after a bounded grace delay
  (`SCAN_WAIT_SECONDS = 8` **[D]**) so a unit just entering pairing mode
  has time to advertise.
- **Rejected alternative**: `bluetooth.async_process_advertisements(...)`
  exists at 2024.12 **[P, §2.1]** but waits for the *first* advertisement
  matching a matcher and resolves on that single device — "wait for any
  *new* device" needs custom bookkeeping (which addresses are already
  known) on top of a callback designed for one device, with replay
  subtleties. The sleep-and-reread is simpler, deterministic and testable.
  **[D]**
- **Not available at 2024.12**: `manager.async_request_active_scan` and
  AUTO scanning (dev-branch only) — explicitly not used (NFR-1).

### Q4 — No results / pairing procedure

- **No scanner at all**: `bluetooth.async_scanner_count(hass)` **[P,
  §2.1]** returns the number of registered scanners; 0 → abort
  `no_bluetooth_adapter` (translated) — without a radio neither discovery
  nor connection can work, and an empty list would mislead. **[D]**
  (Alternative considered: keep the flow open with a note so the user can
  plug an adapter and rescan — rejected: HA usually needs the Bluetooth
  integration to pick up a new adapter, so staying in the flow buys
  little.)
- **Scanner present, nothing discovered**: `no_devices` **menu** with the
  full pairing procedure in the description + the two remaining actions
  (scan / manual). The procedure is the reverse-engineered one **[P —
  FINDINGS.md §3]**: hold the panel button ~5 s → display `bLE` blinking
  1×/s → the unit advertises as `BlueNRG-…`; plus the single-connection
  warning (close the vendor app) — all in FR-14 texts.
- Menus: `FlowHandler.async_show_menu(step_id, menu_options,
  description_placeholders)` **[P, §2.4]**; options route to
  `async_step_<key>`.

### Q5 — Coexistence with passive discovery (`async_step_bluetooth`)

**Decision: keep it untouched; do not add manifest matchers. [D]**

- **Current state [P]**: the manifest has **no `"bluetooth"` key**, so HA's
  integration matcher never matches this domain and `SOURCE_BLUETOOTH`
  flows are never created — the step is dormant code, kept as a safety net.
- Duplicate protection is unchanged and proven by both code paths setting
  `unique_id = address.upper()` with
  `async_set_unique_id(..., raise_on_progress=False)` +
  `_abort_if_unique_id_configured()` (current repo code, spec 001 FR-5).
- **Why not activate it now** (considered, rejected): the only passive
  matchers available would be name-based (`local_name` globs like
  `BlueNRG*` — 2024.12 matcher semantics are case-normalized fnmatch
  **[P, §2.6]**). `BlueNRG-` is a **generic STMicro module prefix** — every
  BlueNRG-based device in the house (sensors, locks, toys…) would get an
  "Aqua Temp BLE discovered" prompt, with a 20 s doomed connection test for
  impostors. ff01/ff02 advertised-UUID matchers are unreliable **[H]**.
  Until a device-specific signal is pinned (e.g. a manufacturer-data ID via
  the probe tool), passive discovery stays off. Revisit after calibration
  if the probe shows a stable manufacturer-specific marker.

### Q6 — Already-configured devices

**Decision: exclude from the list; keep the abort as a backstop. [D]**

- Core BLE integrations do exactly this via
  `ConfigFlow._async_current_ids()` — inkbird, bthome, switchbot (all read
  at 2024.12) **[P, §2.3]**. Our unique_id is `address.upper()`, so the
  exclusion compares `info.address.upper()` against current ids.
- Showing configured units as greyed-out is impossible in a `vol.In`
  dropdown (no disabled options) and would dead-end into
  `already_configured` — exclusion + the note "already configured units
  are not listed" is the honest UX. **[D]**
- The manual path still aborts on a configured address (unchanged guard).

### Q7 — Texts & translation keys

- All flow labels go through `strings.json` / `translations/*.json` —
  exact keys in `plan.md` §6. Menu labels live under
  `config.step.<menu_id>.menu_options.<option>` **[P — switchbot
  `strings.json` at 2024.12 has exactly this layout]**.
- **Why a menu instead of "rescan" pseudo-entries in the device
  dropdown**: `vol.In` option *values* are raw strings from code — the HA
  frontend translates only field names (`data.<key>`), not select option
  labels **[H — see §5, V-3]**. A menu gets every label translated for
  free; a pseudo-entry would show English ("Scan again") to a French
  user. Hence FR-1. **[D]**

---

## 2. Verified HA API facts (all at tag 2024.12.0 unless noted)

| # | Fact | Source (fetched 2026-10-11) | Status |
|---|---|---|---|
| 2.1 | `homeassistant/components/bluetooth/api.py` exports: `async_discovered_service_info(hass, connectable=True)` (L70), `async_last_service_info(hass, address, connectable=True)` (L78), `async_ble_device_from_address(hass, address, connectable=True)` (L86), `async_scanner_count(hass, connectable=True)` (L64), `async_process_advertisements(hass, callback, match_dict, mode, timeout)` (L128), `async_rediscover_address` (L171). `bluetooth/__init__.py` re-exports `.api` (L55) | HA core repo, tag 2024.12.0 | **[P]** |
| 2.2 | `BluetoothServiceInfo(Bleak).name = advertisement_data.local_name or device.name or device.address` | `habluetooth` v3.6.0 tag, `src/habluetooth/models.py` L107/244/269 (re-exported via `home_assistant_bluetooth==1.13.0`, 12-line re-export verified) | **[P]** |
| 2.3 | `ConfigFlow._async_current_ids(include_ignore=True)` | HA core 2024.12.0 `homeassistant/config_entries.py` L2723; usage precedents: inkbird L47, bthome L135, switchbot L270 (`config_flow.py` of each) | **[P]** |
| 2.4 | `FlowHandler.async_show_menu(*, step_id=None, menu_options: Container[str], description_placeholders=None)`; result type MENU; chosen option dispatches to `async_step_<key>` | HA core 2024.12.0 `homeassistant/data_entry_flow.py` L854; usage precedent: switchbot `config_flow.py` L227 + `strings.json` menu_options layout | **[P]** |
| 2.5 | Local `HaScanner` default mode: `BluetoothScanningMode.PASSIVE if passive else BluetoothScanningMode.ACTIVE` (i.e. active unless explicitly disabled) | HA core 2024.12.0 `homeassistant/components/bluetooth/__init__.py` (~L323) | **[P]** |
| 2.6 | Manifest `local_name` matchers: case-normalized index + `_memorized_fnmatch` glob; too-short names rejected ("would match too broadly") | HA core 2024.12.0 `homeassistant/components/bluetooth/match.py` L194/376-388/424 | **[P]** |
| 2.7 | Manager histories are dicts keyed by address (`_all_history`, `_connectable_history`); `async_discovered_service_info` returns `histories.values()` (latest info per address); watchdog prunes silent devices (`UNAVAILABLE_TRACK_SECONDS = 300`, `bluetooth/const.py` at 2024.12) | `habluetooth` v3.6.0 `manager.py` (`async_discovered_service_info`); HA core 2024.12.0 `bluetooth/manager.py` + `bluetooth/const.py` | **[P]** |
| 2.8 | `async_show_progress(step_id, progress_task, description_placeholders=...)` — already used by this repo's `test` step | this repo `config_flow.py` (reviewed, spec 001) | **[P]** |
| 2.9 | `BluetoothScanningMode.ACTIVE` means active scanning (scan requests) at 2024.12 — used only as background fact (we start no scans) | `bluetooth/const.py`+`HaScanner` at 2024.12 | **[P]** |

Notably **absent at 2024.12** (dev-only; must NOT be used):
`manager.async_request_active_scan`, `BluetoothCallbackReplay`,
`async_address_reachability_diagnostics`, AUTO scanning mode,
`async_scanner_devices_by_address`'s newer behaviours.

## 3. Rejected alternatives (summary)

| Alternative | Why rejected |
|---|---|
| Connect-based candidate filtering | N × 15 s connect budget in a form step; one-connection unit; the live test already validates the final pick [D] |
| `vol.In` pseudo-entries ("⟳ Scan again" as a fake device) | Option values are untranslated code strings → English-only actions for FR users [H V-3]; menus give free translation |
| `async_process_advertisements`-based scan step | Single-match callback designed for waiting on ONE device; "any new device" needs extra bookkeeping; sleep+history-reread is deterministic [D] |
| Manifest `bluetooth` matchers (`local_name: BlueNRG*`) | Generic STMicro module prefix → false discovery prompts + doomed tests for every BlueNRG device in the house; revisit post-calibration if a specific marker is found [D] |
| Abort `no_devices_found` when list is empty (inkbird/bthome style) | Dead end without guidance; the pairing procedure + rescan is the whole point of this spec [D] |
| Show configured units greyed-out | `vol.In` has no disabled options; picking one dead-ends into an abort [P-ish limitation] → exclusion + note |

## 4. Assumptions (cheap to correct, flagged for review)

- **[A] Dropdown order** = `vol.In` dict insertion order (JSON object key
  order preserved through the flow result to the frontend select). Core
  integrations pass curated ordered dicts (switchbot/inkbird) which only
  makes sense if order is preserved — but we found no explicit frontend
  statement. **Visually verified in quickstart V3.**
- **[A] Cap 30** on the "other devices" tier — arbitrary usability
  constant (`MAX_OTHER_DEVICES`); trivial to tune.
- **[A] 8 s** rescan wait — long enough for a unit to start advertising in
  pairing mode, short enough not to annoy (`SCAN_WAIT_SECONDS`).
- **[H] RSSI always int** in history entries — bleak's
  `AdvertisementData.rssi` is `Optional`; the sort key defensively treats
  non-int as worst-case. Never blocks a listing.
- **[A] Proxies register as connectable scanners** when they support
  connections (ESPHome proxy docs); a proxy-only HA still passes the
  `scanner_count` gate. Edge E-10 documents the non-connectable caveat.

## 5. Coder/reviewer verification checklist (the [H] items)

- **V-3** Dropdown rendering order (tier 1 above tier 2) — visual check in
  a browser; if the frontend ever re-sorts, the tier labels in the
  `device` description still disambiguate.
- **V-4** Menu as the **first** step of a config flow renders correctly
  (switchbot precedent is mid-flow; mechanics are step-agnostic — confirm).
- **V-5** `scanner_count == 0` on a real adapter-less instance triggers
  the abort (e.g. HA in a container/VM without bluetooth passed through).
- **V-6** RSSI display for a mixed list (named + unnamed) looks sane.
- **V-7** `description_placeholders` on a MENU step render (verified in
  the signature; no core precedent was read for menus specifically —
  switchbot's menus use no placeholders).
- Protocol facts needing live hardware remain those of spec 001 §5 —
  nothing new here.

## 6. What was read (method)

During spec writing, the following sources were fetched and read
(2026-10-11): HA core tag 2024.12.0 — `components/bluetooth/{api.py,
const.py, manager.py, __init__.py, match.py}`,
`components/{inkbird,bthome,switchbot}/config_flow.py`,
`components/switchbot/strings.json`, `homeassistant/config_entries.py`,
`homeassistant/data_entry_flow.py`, `homeassistant/package_constraints.txt`,
`components/bluetooth/manifest.json`; PyPI/GitHub —
`habluetooth==3.6.0` (`models.py`, `manager.py`), `home-assistant-bluetooth==1.13.0`
(re-export shim); HA core dev branch — `components/bluetooth/{api.py,
models.py, manager.py}` (only to confirm which newer APIs to avoid).
Dev-branch-only APIs found this way are listed in §2's "absent" note.