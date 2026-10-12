# Aqua Temp BLE

A Home Assistant custom integration for **PHNIX pool heat pumps** sold under
the "Aqua Temp" app (`com.phinx.poolheater4`, Piscina Temp, …). It talks to
the unit directly over **Bluetooth Low Energy** — no vendor cloud, no
account, no WiFi needed (the unit does not even have WiFi).

The protocol was recovered by static analysis of the Android app:
**Modbus RTU frames over a BLE GATT characteristic** (slave `0x63`,
CRC16-Modbus little-endian). Every recovered frame is CRC-verified against
the app's own constants — see the reverse-engineering notes in
[`FINDINGS.md`](../FINDINGS.md) and [`BLUEPRINT.md`](../BLUEPRINT.md) in the
parent directory.

> ⚠️ **Status: pre-calibration.** The register map is ~80% established from
> static analysis. Everything marked **[P]** below is proven; the few
> remaining assumptions are isolated in `registers.py` (the single calibration
> surface) and the built-in **probe tool** closes them in one live session
> (see `docs/CALIBRATION.md`). Unverified data is **never** shown under a
> label — entities for unverified registers are simply not created.

## Features

| What | Status | Entities |
|---|---|---|
| Inlet / outlet / ambient water temperatures | **[P] proven** | 3 sensors (°C, ÷10 scaling, sentinel-aware) |
| Suction / coil / exhaust temperatures | **[V] registers not pinned yet** | 3 sensors (appear after calibration) |
| Power on/off, mode heat/cool/auto, setpoint | **[P] read, [V] write to confirm live** | 1 climate entity |
| Fault code, firmware | **[P] address, [V] semantics** | 2 sensors (diagnostic) |
| Compressor / circulate pump / high fan / 4-way valve | **[V] registers not pinned yet** | 4 binary sensors (appear after calibration) |
| HP / LP / flow switch (safety inputs) | **[V] registers not pinned yet** | 3 binary sensors, diagnostic (appear after calibration) |
| Register probe / calibration tool | **working** | 1 button + 1 service |

Protocol facts baked into the code (all CRC-verified against the app):

- GATT write `0000ff01-…`, notify `0000ff02-…` (BlueNRG module)
- Base read: `63 03 0007 002D` → 45 holding registers (7..51), 95-byte response
- Writes: FC10 with the safe **16-bit H1 layout** (`63 10 addr 0001 02 value`)
- Temperatures: `value = int(degrees × 10)`; "sensor absent" sentinels
  `-9990` **and** `32765` (`0x7FFD`) — plus a −45…+150 °C plausibility guard —
  always decode to `unknown`, never to a number

## Requirements

- Home Assistant **2024.12 or newer** (config flow, `OptionsFlow` with
  `config_entry`, `SupportsResponse`).
- A working Bluetooth adapter on the HA host (HA OS/Supervised usually fine;
  for container installs make sure `/var/run/dbus` and bluez are available).
- `bleak` is provided by HA itself (`requirements` lists it for standalone use).

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

## Pairing / first connection

- The unit must have been paired once with the vendor app (hold the panel
  button ~5 s until the display shows **`bLE`** blinking 1×/s).
- **Close the vendor app completely before using this integration** — the
  unit accepts a **single BLE connection**. If the app holds it, HA will show
  the entities as unavailable and retry politely.
- The unit advertises as **`BlueNRG-…`** (other models: `HIFLYING…`, `PROV_…`).

## Configuration

Settings → Devices & Services → Add Integration → **Aqua Temp BLE**

The flow starts with a **menu** offering three choices:

- **Select a discovered device** — the full list of every Bluetooth device
  Home Assistant has seen, in two tiers:

  1. **Recognized Aqua Temp units first** — advertised name starting with
     `BlueNRG-`, `HIFLYING-` or `PROV_`, or the ff01/ff02 GATT service UUIDs;
  2. then **other nearby devices**, nearest first (RSSI sort).

  Already-configured units are excluded, and unnamed devices appear as
  `Unnamed device — <address> — RSSI <value>` — the live connection test
  decides what is really a heat pump, not the advertised name.

- **Scan again** — waits ~8 s so a unit you just put in pairing mode has
  time to advertise, then rebuilds the menu with fresh counts (put the unit
  in **pairing mode** first: hold the panel button ~5 s until the display
  shows **`bLE`** blinking 1×/s);
- **Enter the Bluetooth address manually** — type the MAC
  (`XX:XX:XX:XX:XX:XX`) yourself. This stays one click away for
  MAC-changed units.

1. Pick a device (or enter the address manually).
2. HA performs a live connection test and shows what it read
   (power, mode, temperatures). A wrong pick simply fails with a clear
   error and returns to the same list — retry or pick another device.
3. Name the device — done.

If nothing at all is discovered, the menu shows the full pairing
procedure (hold the panel button ~5 s until **`bLE`** blinks 1×/s, close
the vendor app — the unit accepts a single BLE connection) plus the two
remaining actions.

Options (Configure on the device card):

- **Poll interval**: 10..600 s (default 30). Exactly **one** 45-register read
  per cycle — the heat pump is never flooded with requests.
- **Temperature unit**: °C/°F display only. The wire is always ×10 Celsius;
  °F values are converted at the entity boundary.

## Entities

Entities marked **[V]** are prepared placeholders: the panel observation
(spec 003) confirmed their labels, but their register indices are not
proven yet, so **no entity is created until calibration pins them** — no
unconfirmed data can ever appear under a label.

| Entity | Register | Notes |
|---|---|---|
| Climate | 8/13/14 (+19-25, 26) | on/off, heat/cool/auto, setpoint (0.5 steps) |
| Inlet water temperature | 26 | ÷10, sentinel → unknown |
| Outlet water temperature | 27 | ÷10 |
| Ambient temperature | 28 | ÷10 |
| Suction temperature (T01) | **[V]** | appears after calibration |
| Coil temperature (T04) | **[V]** | appears after calibration |
| Exhaust temperature (T06) | **[V]** | appears after calibration |
| Fault code | 47 | 0 = no fault assumed (**[A]**, verify step 6) |
| Firmware | 48-51 | dot-joined (**[V]** semantics, verify) |
| Probe registers (button) | — | reads block 7..51 and logs it |
| Compressor (O01) / Circulate pump (O02) / High fan (O03) / 4-way valve (O04) | **[V]** 29..46 | binary sensors, appear after calibration |
| HP switch (S01) / LP switch (S02) / Flow switch (S03) | **[V]** | binary sensors, diagnostic; appear after calibration |

**Sensor-absent honesty.** The unit uses two "sensor absent" sentinel
conventions on the wire (`-9990` and `32765`/`0x7FFD`), and any temperature
raw outside −45…+150 °C is treated as absent. An absent sensor always shows
`unknown` — the vendor app's own display of an absent sensor as
**3276.5 °C** is a display bug this integration deliberately does not
replicate.

## Calibration (one live session)

Before trusting mode writes and enabling the binary sensors, run the
calibration runbook — it takes ~10 minutes with a phone:

1. Press **Probe registers** (or call `aqua_temp_ble.probe_registers`)
2. Compare the log (`home-assistant.log`) with the app's Status page
3. Pin the few remaining constants in `custom_components/aqua_temp_ble/registers.py`
4. Restart HA

Full runbook: **[`docs/CALIBRATION.md`](docs/CALIBRATION.md)**

```yaml
# service example (Developer Tools → Actions)
service: aqua_temp_ble.probe_registers
data:
  block: base
```

## Standalone tools (no HA needed)

- `tools/smoke_client.py` — connect + dump the 45 registers (read-only)
  ```bash
  pip install bleak
  python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX
  ```
- `tests/test_modbus.py`, `tests/test_reassembly.py` — protocol + reassembly
  unit tests (plain python, no extra deps)

## Next steps — make the unit's write refusal visible (specs 011/012)

The leading theory for the silent first read is a transport-level write
gate: the unit accepts notifications from anyone but only accepts *writes*
from a **paired** central — the owner's phone is paired, the Pi is not, and
the vendor's own write type (Write Command) has no error channel, so the
refusal is invisible by ATT design (`specs/011-android-vs-linux-ble/research.md`
§2.1/§3). The connection test therefore re-sends the base read as an
*acknowledged* write on a timeout (a security gate then surfaces as a named
ATT error in the log instead of silence), tries to **pair** the Pi when the
error says so, and re-asks once; the `pair_on_connect` option stays OFF
until that test proves the bond gate. The **0.8.0 build** (spec 012) makes
that evidence readable where you actually look: the `security probe:` /
`pairing:` verdict lines are **warnings** now, so they appear directly on
Settings → System → Logs, and every test starts with a
`connection test to <address>: build 0.8.0` marker line naming the running
build. The next no-terminal procedure is
[`specs/012-probe-not-firing/quickstart.md`](specs/012-probe-not-firing/quickstart.md):
its §0 recovers the verdict already sitting in your existing
`home-assistant.log` (the "Download full log" button — the Logs page keeps
only warnings, which is why the 011 build's verdicts never showed there),
then §1/§2 run the updated build and read the outcome from the Logs page
directly (the decision matrix of
`specs/011-android-vs-linux-ble/contracts/security-probe.md` §6 stays
binding for the follow-up; the "bLE" choreography and the persistence
retest remain documented in the 011 quickstart).

## Push mode — reading the unit's own data stream (specs 013-014)

Three production runs proved this unit **never answers** the byte-perfect
base read (the connection test's `security probe: verdict
att-accepted-silent` line): the GATT layer accepts our writes, the
Modbus engine stays silent. The unit, however, **answers every read
request**: each valid read triggers a burst of four CRC-valid
45-register blocks on the notify characteristic (0.2-5 s after the
request, ~2 frames/s — and a request-free window is silent,
research 014 §0.2): `1001-1045`, `1046-1090`,
`2001-2045`, `2046-2090`. These are two different data **planes**
with no register offset between them (research 013 §3):

- **Base plane** (registers 7-51): status/control — power, mode,
  setpoint, temperatures, fault, firmware. Read by FC03 requests —
  which this unit ignores.
- **Pushed plane** (registers 1001-2090): the app's parameter view —
  the **T-family live temperatures** at 2046-2051 (the absent-sensor
  sentinel `0x7FFD` at 2046 matches T01 Suction, and 2047 changed
  live between two pushes), the H-family settings at 1018-1023, the
  R-family thresholds at 1046-1060. The block `2001-2045` is still
  **undecoded** — its capture is this spec's main open evidence.

**What build 0.10.0 ships** (`specs/013-pushed-blocks-coverage/` +
`specs/014-query-high-blocks/`): the connection test ends with a
**push-harvest stage** — after the read timeout and the silent
probe, it listens for the pushes for 30 s while sending **zero
frames** (nothing the firmware's gate could drop). Seeing the
T-family block (start 2046) makes the test **succeed** with mode
`push` — the integration stops dead-ending `no_response` against a
unit that talks constantly. A `push` entry polls with **one read
request per cycle** — the trigger (the vendor's own base read
`63030007002d3c54`, the frame the unit demonstrably answers with its
block burst): the request goes out, its 8-second window logs the
burst census at INFO, and the cycle reads the unit's block answer
from the store (freshness-gated; the burst lands 0.2-5 s after the
trigger — within the cycle). **`push stream silent after trigger`**
means the proven trigger did not wake the unit within 8 + 30 s — a
real, diagnosable condition, distinct from 0.9.0's listen-only
`push stream silent`; the link recycles and the next cycle retries
with a fresh trigger. The first push of each block is logged at
WARNING with its **full hex** (`block push captured: start=… hex=…`,
≤ 4 lines per connection) — that `start=2001` line is the
never-decoded block's capture; paste it back for analysis. A
`push_stream` diagnostic sensor reports the per-start push counts
and ages.

**Honesty rules in push mode:** only monitoring entities exist. The
temperature sensors appear **only after the one-run calibration
check** — photograph the app's parameter view (password "66"), kill
the app, run the connection test within ~2 minutes, compare the
success summary's `T-window raws:` line with the photo, then check
"values matched" on the confirm step (or later in the options) and
reload. Until confirmed, the mirrored keys show `unknown`; a mismatch
is reported and pins nothing (the mapping is one editable line in
`registers.py`). The suction sensor stays `unknown` on this unit — its
T01 sensor is absent; that is the honest state. The control state has
pinned homes in the pushed 2001 block — power at register **2025**,
mode at **2034**, owner diff-verified (2026-10-12): check
**push_control_confirmed** after matching the app's display, reload,
and the climate appears as **read-only state** (`hvac_mode` from
power+mode; no target — no setpoint home is identified; every write
refused — the write allowlist is unchanged). The fault code and the
firmware version have **no identified home in the pushes** — those
entities are simply not created in push mode, and no control is
promised.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Unit not listed in the flow | not in pairing mode / out of range → pairing mode (`bLE` blinking 1×/s), then **Scan again** |
| Entities unavailable | vendor app holds the single BLE connection — close it |
| Setup fails `cannot_connect` | unit asleep / out of range / address changed → re-configure |
| MAC changed after power cycle | name-based fallback reconnects, but re-configure for a clean unique_id |
| Weird temperature values | byte-order — see CALIBRATION step 2 (`REG_DATA_LITTLE_ENDIAN`) |
| No reaction to writes | write layout 16/32-bit — see CALIBRATION step 5 (`WRITE_ECHO_EXPECTED`) |
| Verbose chunk logs | set `DEBUG_RAW = True` in `const.py` |

## Security / privacy

Local-only BLE; no cloud, no account, no credentials, no data leaves the LAN.
This integration sends writes only to registers 8 (power), 13 (mode) and 14
(setpoint) in the base block, and never writes to blocks 1001-2090.

**Installer settings are unreachable from HA** (spec 003 §6): the machine
settings (**H**), regulation thresholds (**R**) and filtration program
(**P**) families have **no entity and no service** — an erroneous write to
the H04 exhaust safety threshold (105.0 °C) or an R limit can damage the
heat pump or prevent it from starting. The single write funnel
(`async_write_register`) enforces an allowlist
(`registers.ensure_writable`): {8, 13, 14}, plus 19/20/21 only behind a
default-off calibration switch. There is deliberately **no generic
"write register" service**.

## License & disclaimer

Provided as-is for personal use. This controls a pool heat pump — always
verify temperatures and behaviour on the device panel after installation,
especially before the calibration runbook is complete.
