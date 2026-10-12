# Aqua Temp BLE

A Home Assistant custom integration for **PHNIX pool heat pumps** sold under
the "Aqua Temp" app (`com.phinx.poolheater4`, Piscina Temp, …). It talks to
the unit directly over **Bluetooth Low Energy** — no vendor cloud, no
account, no WiFi needed (the unit does not even have WiFi).

## How it reads: trigger + listen

This firmware never answers a classic Modbus read with classic data, and it
never pushes spontaneously. But it **answers every request** — any valid
frame — with a burst of four 45-register blocks (1001, 1046, 2001, 2046) on
the notify characteristic. So the integration sends **one** vendor read
frame per poll cycle as a *trigger*, listens while the burst arrives, and
decodes its snapshot from the stored blocks. The 8-second read timeout each
cycle is the **expected** world, not an error.

## Features (all verified live on the production unit)

| What | Status | Entities |
|---|---|---|
| Inlet / outlet water temperatures | **proven** (photo-calibrated) | 2 sensors |
| Coil / ambient / exhaust temperatures | **proven** (photo-calibrated) | 3 sensors |
| Suction temperature (T01) | sensor absent on this unit — shows `unknown` | 1 sensor |
| Power state (OFF/HEAT display, heat-only rule) | **proven** (app diff-revert) | climate tile |
| Heat setpoint display | **proven** (panel diff) | climate target |
| Power control (on/off) | **proven** (pump heard) | climate Heat/Off buttons |
| Setpoint control (10.0-40.0 °C, 0.5 steps) | **proven** (mirror followed) | climate target slider |
| Fault indicator (code when proven) | **proven** (owner E03 diff-revert) | 1 binary sensor |
| Push stream health | diagnostic | 1 sensor (diagnostic) |
| Register / power / setpoint probes | calibration instruments | 3 buttons + 3 services |

**Not available — by design, not by omission:** the **mode** is not
pilotable (heat-only unit; the vendor app locks it, and its write would
clobber an unpinned pair register); the **firmware version** has no
identified home in the data report; the **other** binary surfaces
(compressor/pump/fan/valves/switches) were never pinned and stay pruned —
the fault IS exposed since spec 023 (register 2074, the E03 diff-revert).

Protocol facts baked into the code (all CRC-verified against the app's own
frames or proven live):

- GATT: vendor data service `ff01`, **write** `ff03` (without response,
  app parity), **notify** `ff02`
- Trigger: the vendor's own base read `63030007002d3c54`; the answer is the
  four-block burst (99-byte CRC-valid frames, possibly fragmented)
- Control writes: single-register frames (`63 10 addr 0001 02 value crc`)
  to register **1011** (power, values 0/1) and **1046** (heat setpoint,
  raw 100-400 in steps of 5 = 10.0-40.0 °C)
- Temperature mirror registers: **2046-2051** (T01-T06), power mirror
  **2011**, mode mirror **2019** (diagnostic only), setpoint **1046**
- Temperatures: `value = int(degrees × 10)`; "sensor absent" sentinels
  `-9990` **and** `32765` (`0x7FFD`) — plus a −45…+150 °C plausibility
  guard — always decode to `unknown`, never to a number

## Requirements

- Home Assistant **2024.12 or newer** (config flow, `OptionsFlow` with
  `config_entry`, `SupportsResponse`).
- A working Bluetooth adapter on the HA host (HA OS/Supervised usually fine;
  for container installs make sure `/var/run/dbus` and bluez are available).
- `bleak` is provided by HA itself.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories*
2. Add this repository, category **Integration**
3. Install **Aqua Temp BLE**, restart Home Assistant

### Manual

```bash
cd config/custom_components
git clone <this-repo-url> aqua-temp-ble-tmp
cp -r aqua-temp-ble-tmp/custom_components/aqua_temp_ble .
rm -rf aqua-temp-ble-tmp
```

then restart Home Assistant.

## First connection

- **No pairing is needed.** The production verdict (`att-accepted-silent`)
  proved the unit accepts writes at the ATT layer without any bond — the
  old pairing lever was removed. Just connect.
- **Close the vendor app completely before using this integration** — the
  unit accepts a **single BLE connection**. If the app holds it, HA will
  show the entities as unavailable and retry politely.
- The unit advertises as **`BlueNRG-…`** (other models: `WT-…`,
  `HIFLYING-…`, `PROV_…`). If it does not appear at all, power it on and
  stay in range; as a last resort, some units can be made to advertise by
  holding the panel button ~5 s until the display shows **`bLE`** (on some
  models this is the WiFi-provisioning gesture — it was not needed on the
  production unit).

## Configuration

Settings → Devices & Services → Add Integration → **Aqua Temp BLE**

The flow starts with a **menu** offering three choices:

- **Select a discovered device** — the full list of every Bluetooth device
  Home Assistant has seen, in two tiers:

  1. **Recognized Aqua Temp units first** — advertised name starting with
     `BlueNRG-`, `WT-`, `HIFLYING-` or `PROV_`, or the ff03/ff02 pipe UUIDs;
  2. then **other nearby devices**, nearest first (RSSI sort).

  Already-configured units are excluded, and unnamed devices appear as
  `Unnamed device — <address> — RSSI <value>` — the live connection test
  decides what is really a heat pump, not the advertised name.

- **Scan again** — waits ~8 s so a unit you just powered on has time to
  advertise, then rebuilds the menu with fresh counts;
- **Enter the Bluetooth address manually** — type the MAC
  (`XX:XX:XX:XX:XX:XX`) yourself. This stays one click away for
  MAC-changed units.

1. Pick a device (or enter the address manually).
2. HA performs a live connection test: connect → request → listen for the
   unit's data report. A wrong pick simply fails with a clear error and
   returns to the same list — retry or pick another device.
3. Name the device — the confirm screen shows what the test captured
   (temperatures, power state, heat target). Done: the sensors, the
   climate tile and both controls work immediately.

### Options (Configure on the device card)

- **Poll interval**: 10..600 s (default 30). Exactly **one** request per
  cycle — the heat pump is never flooded.
- **Temperature unit**: °C/°F display only. The wire is always ×10 Celsius;
  °F values are converted at the entity boundary.

## What you get (and what protects the machine)

Everything works immediately after you add the device: the six
temperature sensors, the climate tile (OFF/HEAT display + heat target),
power control (Heat/Off buttons) and setpoint control (the target
slider). There is nothing to confirm, no checkbox, no options toggle.

What still protects the machine — automatically, all the time:

- **Write allowlists**: writes go only to the two proven registers (power
  **1011**, heat setpoint **1046**); every other register — the mode pair,
  the mute, the installer families — is refused, forever.
- **Value rails**: power accepts only on/off; the setpoint only 10.0-40.0 °C
  in 0.5 steps. Out-of-range requests are refused loudly, never rounded or
  clamped.
- **Mirror confirmation**: a write is confirmed by a fresh data report
  that postdates it; until then the tile does not change, and an
  unconfirmed OFF raises a persistent notification.
- **Read honesty**: absent sensors decode to `unknown`, never to a number;
  a stale stream fails the cycle honestly.

The probes are **diagnostic buttons** on the device — measurement
instruments, not control surfaces. They always start from a verified
baseline and always aim to leave the unit unchanged; their verdict line
(at WARNING, visible on Settings → System → Logs) names the required
remediation when something could not be verified. Since spec 022 they are
informational — the controls are already active.

## Entities (push mode)

| Entity | Source | Notes |
|---|---|---|
| Climate | mirrors 2011 / 1046 / 2047 | `hvac_mode` OFF/HEAT follows the power mirror alone (heat-only unit); `target_temperature` is the setpoint mirror; `current_temperature` is the inlet water; bounds 10.0-40.0 °C, step 0.5 |
| Inlet water temperature (T02) | pushed 2047 | ÷10, sentinel → `unknown` |
| Outlet water temperature (T03) | pushed 2048 | ÷10 |
| Coil temperature (T04) | pushed 2049 | ÷10 |
| Ambient temperature (T05) | pushed 2050 | ÷10 |
| Exhaust temperature (T06) | pushed 2051 | ÷10 |
| Suction temperature (T01) | pushed 2046 | `0x7FFD` sentinel — sensor absent → `unknown` |
| Push stream (diagnostic) | the block store | `on`/`off` + per-block counts, ages, candidate raws |
| Probe registers / Power write probe / Setpoint write probe (diagnostic) | — | the calibration instruments |

**Write confirmation, never optimism.** A write from HA is confirmed by a
fresh data report that **postdates** it (the mirror must match the request);
until then the tile does not change. An unconfirmed OFF raises a persistent
notification — a possibly-running pump is never a silent end state.

**Sensor-absent honesty.** An absent sensor always shows `unknown` — the
vendor app's own display of an absent sensor as **3276.5 °C** is a display
bug this integration deliberately does not replicate.

## Verifying a new unit (optional)

The full push-world runbook (photo comparison, diff-revert, the two write
probes) lives in **[`docs/CALIBRATION.md`](docs/CALIBRATION.md)** — optional
confidence checks for a new or unknown unit; the authoritative reference is
[`specs/020-integration-reference/quickstart.md`](specs/020-integration-reference/quickstart.md)
§4.

```yaml
# probe service example (Developer Tools → Actions)
service: aqua_temp_ble.probe_registers
data:
  block: base        # trigger + dump of every captured block
```

## Standalone tools (no HA needed)

- `tools/smoke_client.py` — connect, trigger the data report, dump the
  captured blocks (read-only, requires only `pip install bleak`)
  ```bash
  python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX
  ```
- `tests/` — the full suite (protocol, reassembly, cycles, probes, flow);
  `python3 -m pytest tests/ -q`

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Unit not listed in the flow | powered off / out of range → power on, stay in range, **Scan again** |
| Entities unavailable; `cannot_connect` | the vendor app holds the single BLE link — close it completely |
| `push stream silent after trigger` | no data report within the window (rebooting unit? radio) — the cycle retries; check distance |
| Fault: the "Fault" binary sensor | ON when the fault word (register 2074) is non-zero — proven by the owner's E03 run; the `code` attribute shows the panel code when the raw↔code pair was recorded live (E03 = raw 256 — the vendor app itself labels it "water flow problem"), else "unknown code (raw N)". Firmware: still not displayed (no reliable source identified in the data report; the vendor's own fault table lives in the APK) |
| `unconfirmed` write, persistent notification | the fresh report never came back — check the panel; the notification says what to do |
| Log lines at WARNING | the deliberate evidence lines (probe verdicts, block captures, test markers) — read them, they are the diagnostics |

## Security / privacy

Local-only BLE; no cloud, no account, no credentials, no data leaves the LAN.

Writes go **only** to the two proven registers, each within its permanent
value guard: **1011** (power, values 0/1 only) and **1046** (heat setpoint,
raw 100-400 in steps of 5 — out-of-range or off-step requests are
**refused loudly, never rounded, never clamped**). The single write funnel
(`registers.ensure_writable`) enforces the allowlists at every layer.

**Everything else is refused, forever:** the mode write (1012/1013 — a pair
write that would clobber an unpinned register; the vendor app itself locks
the mode on this heat-only machine), the mute (1076), and the installer
families (**H** machine settings, **R** regulation thresholds, **P**
filtration program) which have **no entity and no service** — an erroneous
H04 exhaust safety threshold or R-limit write can damage the heat pump.
There is deliberately **no generic "write register" service**.

## License & disclaimer

Provided as-is for personal use. This controls a pool heat pump — always
verify temperatures and behaviour on the device panel after installation;
the optional verification runbook lives in docs/CALIBRATION.md.