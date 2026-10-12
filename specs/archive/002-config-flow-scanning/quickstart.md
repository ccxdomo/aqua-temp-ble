# Quickstart 002 — Validate the scanning config flow

This repo deliberately has no Home Assistant test harness (spec 001 §7):
HA-dependent code is gated by `py_compile` + review + **manual scenarios**.
This file is the validation runbook for the new config flow. Scenarios
that need the unit are marked 🔴 hardware; everything else is doable on
any HA instance.

---

## 0. Static gates (no hardware)

```bash
python3 -m py_compile custom_components/aqua_temp_ble/config_flow.py \
                          custom_components/aqua_temp_ble/const.py
python3 custom_components/aqua_temp_ble/modbus.py      # self-test 37 checks
python3 tests/test_modbus.py                           # 11 tests
python3 tests/test_reassembly.py                       # 15 tests
```

✓ *pass criteria:* all green (unchanged code must stay unchanged-green).
Also: `strings.json`, `translations/en.json`, `translations/fr.json`
parse as JSON and contain identical key sets (quick script or jq diff).

## 1. V1 🔴 — Happy path: unit in pairing mode

1. Close the vendor app completely (single BLE connection).
2. Hold the panel button ~5 s until the display shows `bLE` blinking
   1×/s (pairing mode — FINDINGS.md §3).
3. HA → Settings → Devices & Services → Add Integration →
   **Aqua Temp BLE**.
4. Expect the **user menu** with counts in the description
   (recognized ≥ 1 if the unit advertises `BlueNRG-`).
5. *Select a discovered device* → the unit is in the first tier →
   select → live test → confirm → entities appear.

✓ *pass:* end-to-end setup without ever typing a MAC. ✓ *pass criterion
AC-1/AC-11.*

## 2. V2 — No Bluetooth adapter

Run on an HA instance/VM with no bluetooth hardware and no proxies
(or all bluetooth entries disabled).

✓ *pass:* the flow aborts immediately with the `no_bluetooth_adapter`
message (AC-5), in EN and in FR (switch HA language, retry).

## 3. V3 — Menu, rescan, dropdown order

On an instance with any Bluetooth traffic (any adapters, phones, TVs…):

1. Add Integration → user menu shows the three actions + counts.
2. *Scan again* → spinner ~8 s → back to the menu with refreshed counts
   (AC-3). Repeat twice — harmless.
3. *Select a discovered device* → dropdown shows: recognized devices
   first (if any), then other devices **nearest first**; unnamed entries
   read "Unnamed device — <address> — RSSI <n>".
4. **Visual check [V-3/V-6]:** dropdown order matches the tier order.
   If the frontend re-sorts, fall back to labeling tiers in the
   description and note it in the review.
5. Pick any non-heat-pump device → the test runs ≤ 20 s → error key
   (`cannot_connect` or `no_response`) → back on the device form (AC-2).

## 4. V4 — Empty list → no-devices menu

On an instance with a working adapter but nothing advertising (or block
the known devices), or after configuring every visible device:

1. Add Integration → the **no-devices menu** appears with the pairing
   procedure (hold ~5 s → `bLE` blinking → `BlueNRG-` name) and the
   vendor-app warning (AC-4).
2. Both actions present: *Scan again* / *Enter the Bluetooth address
   manually*.
3. The description mentions that configured units are not listed.

## 5. V5 — Manual path (unchanged semantics)

1. From the menu: *Enter the Bluetooth address manually*.
2. `foo` → `invalid_address` (EN + FR). 
3. 🔴 valid MAC of the unit → test → confirm (AC-8).

## 6. V6 — Duplicate protection

1. With one unit configured: start Add Integration again → the unit is
   **absent** from the device list (AC-6).
2. Enter its address manually → abort `already_configured` (EN + FR).

## 7. V7 — Translations

Switch HA language FR ↔ EN and walk the flow: menu labels, no-devices
menu, scan step, manual step, all errors and aborts (AC-9). Check for
orphan keys in either language.

## 8. V8 🔴 — Regression: passive discovery & options

1. Code review only (dormant): `async_step_bluetooth` unchanged (AC-7
   is a review item — no manifest matchers were added).
2. Options flow still opens: poll interval + unit, applies immediately.
3. Existing entities behave as before (spec 001 quickstart §6).

## 9. V9 🔴 — The original bug, closed

1. Unit **not** in pairing mode and **not** advertising a name (the
   situation that motivated this spec) → it still shows in the "other"
   tier by RSSI if the radio sees its advertisement; select it →
   test → configure.
2. If the unit is fully off/absent, it appears nowhere — the no-devices
   menu explains the pairing procedure instead of dropping the user on
   a MAC field.

## 10. Troubleshooting the flow itself

| Symptom | Likely cause / fix |
|---|---|
| Menu shows 0 devices but the unit advertises | pairing mode not entered (`bLE`), out of range, or adapter disabled — check HA Settings → System → Hardware |
| Unit visible but test fails `cannot_connect` | vendor app still holds the connection — kill it, retry |
| Dropdown order looks wrong | see V3 step 4 (frontend rendering); tiers also stated in the description |
| `no_bluetooth_adapter` on a machine with an adapter | bluetooth integration not set up / adapter down — check the Bluetooth integration card |
| French labels missing | stale translation cache — restart HA once after editing the files |