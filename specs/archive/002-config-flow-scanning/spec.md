# Spec 002 — Config flow: visible scanning & device selection

Status: **PLANNED** — written before implementation. The Coder implements
from this kit; the Reviewer validates against the acceptance criteria.

Scope: the **user-initiated config flow** of
`custom_components/aqua_temp_ble`. Today a unit is only listed when its
advertised name starts with the vendor prefixes; a unit that does not
advertise its name is invisible, the user gets no explanation, cannot
rescan, and is dropped straight into manual MAC entry. This spec makes
discovery **visible, explainable and retryable**. The live connection test
and entry creation (spec 001) are untouched except where stated.

Confidence legend (same as spec 001):
- **[P] PROVEN** — verified in the HA sources pinned at our minimum
  version **2024.12.0** (`habluetooth==3.6.0`, `home-assistant-bluetooth==1.13.0`)
  or in this repo's reviewed code. Exact references in `research.md` §2.
- **[A] ASSUMED** — reasonable default, cheap to correct.
- **[V] TO-VERIFY** — cannot be pinned without live hardware / visual check.
- **[D] DECISION** — design choice (no external fact to verify).

---

## 1. Background — why units are invisible today

- Current `config_flow._discovered_devices()` keeps only devices whose
  `name` starts with `NAME_PREFIXES`, and `async_step_user` falls back to
  the manual step when the filtered list is empty.
- **Root cause [P]**: HA's `BluetoothServiceInfoBleak.name` is computed as
  `advertisement.local_name or device.name or device.address`. A BlueNRG
  unit that does not advertise a name appears in the history **with its
  address as name** — it can never match a name prefix, so it is silently
  filtered out. The name is a display convenience, not an identity.
- The ff01/ff02 GATT service UUIDs are **not guaranteed to appear in the
  advertisement** [V — BlueNRG module behaviour; the vendor app itself
  filters by name only [P], which is why it always sees its units].
- `async_step_bluetooth` (passive discovery) is currently **dormant**:
  the manifest has no `"bluetooth"` matchers key, so HA never triggers a
  `SOURCE_BLUETOOTH` flow for this domain [P]. It is kept unchanged.

## 2. User stories

### US-1 — See every nearby device
As a pool owner, I want the flow to list **all** Bluetooth devices HA has
discovered (recognized Aqua Temp units first, other devices below), so I
can find my heat pump even when it does not advertise its name.

### US-2 — Pick anything and let the flow prove it
As a pool owner, I want to select **any** listed device — including
"Unnamed" entries — and have the flow run its live connection test, so
compatibility is decided by the Modbus read, not by the advertised name.
→ The existing 20 s test step is the compatibility oracle. **[D]**

### US-3 — Rescan without leaving the flow
As a pool owner, I want a **"Scan again"** action while configuring, so I
can put the unit in pairing mode and find it without restarting the flow.

### US-4 — Understand why nothing is listed
As a pool owner, when nothing is found I want to see the **pairing
procedure** (hold the panel button ~5 s until the display shows `bLE`
blinking 1×/s — the unit then advertises as `BlueNRG-…`), the
vendor-app-closed warning and the adapter check, so I can fix the cause
myself. **[P]** procedure from `FINDINGS.md` §3.

### US-5 — Manual entry stays one click away
As a pool owner, I want manual MAC entry to remain available for
MAC-changed units, reachable from the menu — not forced on me.

### US-6 — No duplicates, no dead ends
As the owner, I want already-configured units **excluded from the list**
(and an abort on address reuse), so I cannot create duplicate entries.

### US-7 — Bilingual texts
As a French-speaking owner, I want every flow label translated (EN + FR),
including the rescan and manual actions.

### US-8 — Passive discovery untouched
As the owner, I want the dormant `async_step_bluetooth` step and the
unique-id rules kept as-is, so nothing that works today regresses.

## 3. Functional requirements

### Flow structure

- FR-1 The flow starts with a **menu** (`user` step): *Select a discovered
  device* / *Scan again* / *Enter the Bluetooth address manually*. A menu
  (not pseudo-entries inside a dropdown) is used because dropdown option
  values are **not translatable** via `strings.json`, while
  `menu_options` labels are. **[D]**, menu API **[P]**
- FR-2 If no Bluetooth scanner is available at all
  (`bluetooth.async_scanner_count(hass) == 0` **[P]**), the flow aborts
  with reason `no_bluetooth_adapter` instead of showing an empty list.
- FR-3 If no unconfigured device was discovered, the `user` step becomes a
  **no-devices menu** (pairing procedure in the description) with exactly
  two options: *Scan again* / *Enter the Bluetooth address manually*.
- FR-4 **Device list step** (`device`): a form listing every discovered
  connectable device from the HA Bluetooth history, excluding
  already-configured addresses, in three tiers in this order **[D]**:
  1. **Recognized** — advertised name starts with a `NAME_PREFIXES` entry
     or the advertisement exposes ff01/ff02 service UUIDs (secondary,
     opportunistic signal **[V]**);
  2. then **other devices**, sorted by RSSI (nearest first);
  3. unnamed devices land in tier 2, labelled "Unnamed device".
  The other-devices tier is capped (constant `MAX_OTHER_DEVICES`, default
  30) to keep the dropdown usable **[D]**.
- FR-5 Labels: `"<name> — <address> — RSSI <rssi>"`; a device whose
  computed name equals its address gets `"Unnamed device — <address> —
  RSSI <rssi>"` (name-fallback rule **[P]**).
- FR-6 **Scan step** (`scan`): a progress step waiting a bounded
  `SCAN_WAIT_SECONDS` (default 8 s), then returning to the `user` step with
  a freshly built list. HA scanners run continuously (ACTIVE mode by
  default at 2024.12 **[P]**), so a rescan is a history re-read plus a
  grace delay for units just entering pairing mode. No scan is initiated
  by the flow itself.
- FR-7 **Manual step**: unchanged semantics (MAC format validation,
  unique-id guard), with the pairing-procedure hint added to its
  description.
- FR-8 Selecting a device (or submitting a manual address) routes to the
  existing **test step**; a failed test returns to the step the choice
  came from (`device` or `manual`) with the existing error keys.
- FR-9 The test step passes `hass` to `AquaTempBleClient` so device
  resolution prefers the HA Bluetooth cache
  (`async_ble_device_from_address`) before falling back to bleak scanning.
  The constructor already accepts `hass` **[P]**; the flow currently does
  not pass it.
- FR-10 Unique id: `address.upper()` on every path (bluetooth, device,
  manual); `async_set_unique_id(..., raise_on_progress=False)` +
  `_abort_if_unique_id_configured()` preserved unchanged.
- FR-11 Already-configured exclusion via `ConfigFlow._async_current_ids()`
  compared against `address.upper()` **[P]** (core pattern: inkbird,
  bthome, switchbot).
- FR-12 `async_step_bluetooth` is kept byte-identical in behaviour; no
  manifest `bluetooth` matchers are added (rationale in `research.md` §3).

### Texts

- FR-13 Every new label is defined in `strings.json` and
  `translations/en.json` + `translations/fr.json` (exact keys in
  `plan.md` §6). Menu option labels live under
  `config.step.<menu>.menu_options.<key>`. **[P]** key layout.
- FR-14 The no-devices description contains the full pairing procedure:
  hold ~5 s → display `bLE` blinking 1×/s → advertises as `BlueNRG-…`,
  plus the single-connection warning (close the vendor app).

## 4. Non-functional requirements

- NFR-1 **Minimum HA version stays 2024.12** — every API used is verified
  at tag `2024.12.0` (`research.md` §2). Nothing from newer HA is used
  (`async_request_active_scan`, AUTO scanning mode, callback `replay`
  parameter are explicitly out).
- NFR-2 Changes are confined to `config_flow.py`, flow constants in
  `const.py`, `strings.json`, `translations/{en,fr}.json`, plus README
  wording. No coordinator/client/platform changes (FR-9 is a constructor
  argument pass-through).
- NFR-3 The list build is a pure history read — **no BLE I/O, no scan
  in a form step**; the only wait is the bounded scan step (NFR: ≤ 10 s).
- NFR-4 English-only durable artifacts; user-facing texts EN + FR.

## 5. Acceptance criteria

- AC-1 Unit in pairing mode (`bLE` on the panel), vendor app closed: the
  unit appears in the device list (recognized tier if it advertises
  `BlueNRG-`, otherwise in the other tier) and can be configured
  end-to-end (test → confirm → entities).
- AC-2 A device that advertises **no name** appears as
  "Unnamed device — <address> — RSSI <value>"; selecting it runs the test;
  if it is not a heat pump the test fails with the existing error keys and
  the flow returns to the device step (no crash, no dead end).
- AC-3 "Scan again" (menu option, and from the no-devices menu) re-shows
  the `user` step after ~`SCAN_WAIT_SECONDS` with a refreshed list, without
  leaving the flow; pressing it repeatedly is harmless.
- AC-4 With no unconfigured device discovered: the flow shows the
  no-devices menu with the pairing procedure (5 s hold, `bLE` blinking,
  `BlueNRG-` name) and both actions.
- AC-5 On an instance without any Bluetooth scanner: the flow aborts with
  `no_bluetooth_adapter` and the translated message.
- AC-6 An already-configured unit never appears in the device list;
  entering its address manually aborts with `already_configured`.
- AC-7 After a failed test, the flow returns to the exact step the choice
  came from (`device` or `manual`), with the same error keys as today
  (`cannot_connect`, `no_response`, `crc_error`, `invalid_address`).
- AC-8 Manual entry still works: valid MAC → test; malformed MAC →
  `invalid_address`; the step description mentions the pairing tip.
- AC-9 `strings.json`, `translations/en.json`, `translations/fr.json`
  contain the same key sets (no orphans); menu labels display in English
  and French when the HA language is switched.
- AC-10 `python3 -m py_compile` clean on all touched files; the existing
  protocol test gates (`test_modbus.py`, `test_reassembly.py`) still pass.
- AC-11 The `user` menu shows live counts in its description:
  recognized / total nearby devices (excluding configured).

## 6. Edge cases

| # | Case | Behaviour |
|---|---|---|
| E-1 | Unit advertises no name | Name falls back to the address **[P]** → labelled "Unnamed device", selectable |
| E-2 | Unit name is a near-miss of a prefix (e.g. `BlueNRG_`, `bluenrg-X`) | Prefix match is exact `startswith` on the lower-cased name; a near-miss lands in the other tier — still selectable |
| E-3 | Advertisement lacks ff01/ff02 UUIDs | Expected [V]; recognition falls back to the name tier; never hidden |
| E-4 | RSSI is `None`/odd | Sort key treats missing RSSI as worst (`-200`); label shows what the history holds |
| E-5 | Only already-configured devices nearby | Same as E-no-devices: no-devices menu (configured units are excluded by design, note in description) |
| E-6 | Vendor app holds the single BLE connection | Test fails `cannot_connect` → back to `device`/`manual` with the existing hint |
| E-7 | User selects an impostor device (TV, phone…) | 20 s test budget, then `no_response`/`cannot_connect` → back with error; no entry created |
| E-8 | Same device seen by several scanners | Impossible duplicate in the list: history is keyed by address **[P]** |
| E-9 | Two config flows race (menu open + passive discovery) | `raise_on_progress=False` + unique-id guards (existing behaviour preserved) |
| E-10 | Remote Bluetooth proxies only (no local adapter) | `async_scanner_count > 0` → flow proceeds; devices seen by non-connectable proxies are not listed (connectable-only read) **[A]** — noted, not blocking |
| E-11 | User spams "Scan again" | Each click = one bounded wait; no scan storm (HA owns the radio) |
| E-12 | MAC changed since last configuration | Old address excluded via current ids; new address appears in the list; manual path still available |

## 7. Out of scope

- Adding manifest `bluetooth` matchers (passive discovery activation) —
  rejected for now, see `research.md` §3.
- Any change to the live test, confirm step, options flow, or polling.
- Connect-based pre-filtering of the list (connecting to every candidate
  to probe registers) — rejected, see `research.md` §1.
- Non-connectable advertisement display, "last seen" staleness column.
- BLE connection-slot management (`bleak-retry-connector`).