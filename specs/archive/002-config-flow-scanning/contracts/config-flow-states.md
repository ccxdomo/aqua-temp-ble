# Contract — Config flow state machine (002)

Status: **PLANNED** — normative for the implementation. Describes every
step, its inputs/outputs and all legal transitions of the config flow
after this spec. Anything not listed here is a bug.

Confidence legend: **[P]** proven (HA 2024.12.0 / this repo) ·
**[D]** decision of this spec.

---

## 1. Steps

| step_id | kind | shown as | purpose |
|---|---|---|---|
| `user` | **menu** (first step) | 3 buttons, or no-devices menu | entry point; fresh counts each visit |
| `device` | form (dropdown) | `vol.In(labels)` | pick any discovered device |
| `scan` | progress | spinner ~8 s | bounded rescan, then back to `user` |
| `manual` | form (text) | MAC input | manual address |
| `test` | progress (existing) | live connect + 45-register read, 20 s | compatibility oracle |
| `confirm` | form (existing) | summary + name | entry creation |
| `bluetooth` | passive (existing, dormant) | n/a | kept unchanged |

Menu semantics: each `menu_options` key routes to `async_step_<key>`
(**[P]** FlowHandler); labels are translated under
`config.step.<menu_id>.menu_options.<key>`.

## 2. Transitions

```
START
  → user
user:
  scanner_count == 0                         → abort no_bluetooth_adapter
  list empty                                 → user-as-no-devices-menu
                                               options: scan | manual
  list non-empty                             → user-as-pick-menu
                                               options: device | scan | manual
  menu/device → device form
  menu/scan   → scan
  menu/manual → manual
scan:
  wait SCAN_WAIT_SECONDS → user                (always; failures impossible)
manual:
  bad MAC       → manual (error invalid_address)
  good MAC      → test (origin="manual")
device:
  pick address  → test (origin="device")
test:                                             (existing behaviour)
  ok            → confirm
  fail          → origin form with error base key:
                  origin "device"  → device
                  origin "manual" → manual
confirm:                                          (existing behaviour)
  submit        → create_entry (unique_id = address.upper())
bluetooth (dormant): → test (origin="user" semantics unchanged)
```

Notes:
- `scan` never skips `user`: after the wait the menu rebuilds with fresh
  counts, so the user sees what changed before choosing. **[D]**
- Returning to `user` from `scan` re-runs the list builder; previously
  picked `_address` state is irrelevant there and is not displayed.
- `no_devices` is **not** a separate step_id: the `user` step returns a
  menu whose `step_id="no_devices"` when the list is empty (same flow
  object, two presentations). **[D]** This keeps translation keys under
  `step.no_devices.*` without extra routing.

## 3. Flow state (instance attributes)

| attr | type | set by | consumed by |
|---|---|---|---|
| `_device_labels` | `dict[str, str]` | list builder (on every `device` entry and `user` visit) | `device` step schema |
| `_scan_done` | `bool` | `scan` progress task | `scan` re-entry routing |
| `_address` | `str \| None` | `device`/`manual`/`bluetooth` | `test`, `confirm` |
| `_name` | `str \| None` | `bluetooth` step (existing) | client |
| `_regs` | `dict \| None` | `test` (existing) | `confirm` |
| `_test_error`, `_test_done` | existing | `test` | routing |
| `_origin` | `"user" \| "device" \| "manual"` | each entry point | `test` failure routing |

`_origin` gains the value `"device"` (new); `"user"` is only produced by
the dormant bluetooth path (existing behaviour).

## 4. Unique-id rules (unchanged, normative)

- `unique_id = address.upper()` on **every** path that produces an entry
  candidate (`device`, `manual`, `bluetooth`).
- `await self.async_set_unique_id(..., raise_on_progress=False)` then
  `_abort_if_unique_id_configured()` **before** entering `test`.
- Already-configured addresses are additionally filtered out of the
  device list (belt) so the abort is only reachable via manual entry
  (braces).

## 5. Error keys

| key | produced in | surfaces on |
|---|---|---|
| `invalid_address` | `manual` validation | manual form |
| `cannot_connect` / `no_response` / `crc_error` | `test` task | origin form (`device`/`manual`) |
| `no_bluetooth_adapter` | — | abort reason (not a form error) |

## 6. Entry creation (unchanged)

```
data:    {"address": <as chosen>, "name": <user-typed>}
options: {"poll_interval": 30, "temperature_unit": "C"}
```

The address is stored as entered (existing behaviour); only `unique_id`
is normalized to upper case.