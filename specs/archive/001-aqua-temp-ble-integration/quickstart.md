# Quickstart 001 — Install, verify, calibrate

Validation scenarios for the Aqua Temp BLE integration. Every step is
read-only until the calibration section; the climate entity writes only
regs 8/13/14.

---

## 1. Prerequisites

- PHNIX pool heat pump driven by the "Aqua Temp" Android app; the unit was
  **paired at least once** with the vendor app (hold the panel button ~5 s
  until the display shows `bLE` blinking 1×/s).
- **Close the vendor app completely** before any test — the unit accepts a
  **single BLE connection**. If the app holds it, HA shows the entities as
  unavailable and retries politely.
- Home Assistant **2024.12+** with a working Bluetooth adapter
  (HA OS/Supervised usually fine; container installs need bluez + dbus).
- The unit advertises as `BlueNRG-…` (other models: `HIFLYING…`, `PROV_…`).

## 2. Install

### HACS
1. HACS → ⋮ → *Custom repositories* → add this repo, category **Integration**
2. Install **Aqua Temp BLE**, restart Home Assistant

### Manual
```bash
cd config/custom_components
git clone https://github.com/ccxdomo/aqua-temp-ble aqua-temp-ble-tmp
cp -r aqua-temp-ble-tmp/custom_components/aqua_temp_ble .
rm -rf aqua-temp-ble-tmp
```
then restart Home Assistant.

## 3. Configure (validation scenario V1)

Settings → Devices & Services → Add Integration → **Aqua Temp BLE**

1. Discovered units are listed (from the HA Bluetooth cache); otherwise
   enter the BLE MAC manually (`XX:XX:XX:XX:XX:XX`).
2. HA runs a **live connection test** (20 s budget: connect + one 45-register
   read) and shows what it read — power, mode, inlet/outlet/ambient
   temperatures. ✓ *pass criterion: real values shown, not "unknown".*
3. Name the device → entry created, entities appear within seconds.

Expected entities: 1 climate, 3 temperature sensors, `Fault code`,
`Firmware` (diagnostic), `Probe registers` button (diagnostic). The
firmware string appears in the device registry from the first cycle that
reports it. Compressor/pump/valve binary sensors are **absent by design**
until calibrated (no unverified data under a label).

## 4. Smoke test WITHOUT Home Assistant (validation scenario V2)

Next to the heat pump, vendor app closed:

```bash
pip install bleak
python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX
# optional: --name BlueNRG-XXX   --query 1001  (probe a high block)
```

✓ *pass criteria:* a `CRC=OK` line, 45 registers listed with sane `/10`
temperatures (e.g. inlet 24.3 → `reg 26 = 243 (0x00F3)`), labels on the
known registers, raw chunk logging (`DEBUG_RAW`) showing how the device
fragments its 95-byte response.

## 5. Verify the protocol layer (no hardware needed)

```bash
python3 custom_components/aqua_temp_ble/modbus.py   # self-test: 37 checks
python3 tests/test_modbus.py                        # 11 tests
python3 tests/test_reassembly.py                    # 15 tests
```

✓ *pass criteria:* all green; the 7 recovered APK frames verify CRC
byte-exact (they are the regression vectors).

## 6. Verify runtime behaviour (validation scenario V3)

- Setpoint: change the climate target → within one debounced poll the state
  reflects the device (no optimistic update: watch the panel).
- Power/mode: turn off / heat / cool / auto → the entity follows, confirmed
  by a re-read. If writes silently fail, see calibration step W.
- Options: Configure → set poll interval (10–600 s) and °C/°F. ✓ The unit
  switch applies **immediately** (entities re-render, no reload, no extra
  BLE traffic).
- Single-connection test: open the vendor app while HA polls → HA shows
  unavailable, reconnects after the app closes.

## 7. Calibrate (one live session, ~10 min)

Runbook: **`docs/CALIBRATION.md`**. Shortest path:

1. Press the **Probe registers** button (or call
   `aqua_temp_ble.probe_registers` with `block: base`) and open
   `home-assistant.log`.
2. Compare with the vendor app's Status page (inlet temp is the easiest
   anchor) → confirms scaling + endianness.
3. Pin the remaining constants in
   `custom_components/aqua_temp_ble/registers.py` (mode map, binary
   registers, write behaviour) and restart HA.

Cheapest write-side evidence: with the vendor app connected on a paired
Android phone, `adb logcat | grep -aE "address|6310|指令"` — the app logs
every outgoing frame in cleartext. Extra aid: debug password **`66`** in
the app's parameter view exposes raw registers (H70/H71/H72…).

## 8. Troubleshooting quick table

| Symptom | Cause / fix |
|---|---|
| Entities unavailable | vendor app holds the single BLE connection — close it |
| Setup fails `cannot_connect` | unit asleep / out of range / address changed → re-configure |
| MAC changed after power cycle | name-based fallback reconnects, but re-configure for a clean unique_id |
| Weird temperature values | byte order — runbook step 2 (`REG_DATA_LITTLE_ENDIAN`) |
| No reaction to writes | write layout 16/32-bit — runbook step 5 (`WRITE_ECHO_EXPECTED`, logcat capture) |
| Verbose chunk logs | `DEBUG_RAW = True` in `const.py` |