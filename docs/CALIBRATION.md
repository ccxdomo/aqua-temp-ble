# Calibration runbook — closing the last 20% of the register map

The label→register map was recovered from static analysis of the vendor APK
(FINDINGS.md §11.4/§12) and is ~80% certain. The indices are built at runtime
inside the app, so the remaining items **cannot be known without one live
session with the unit**. This runbook converts every `[V]`/`[A]` item into a
verified fact; every correction lands in
`custom_components/aqua_temp_ble/registers.py` **only** — edit one entry and
restart Home Assistant.

Status legend: **[P]** proven (static), **[A]** assumed default,
**[V]** to-verify live.

## 0. Prerequisites

- The unit paired once with the vendor app (hold the panel button ~5 s until
  the display shows `bLE` blinking 1×/s).
- **Close the vendor app completely** — the unit accepts a single BLE
  connection. HA will politely wait and show unavailable otherwise.
- Enable file logging if needed:
  `logger.set_level` → `custom_components.aqua_temp_ble: debug`
  (`homeassistant.log` gets the probe output).

## The probe surfaces

- **Button** `Probe registers` (diagnostic entity on the device) — reads the
  base block (registers 7..51) and logs it.
- **Service** `aqua_temp_ble.probe_registers` — also reads the experimental
  high blocks; optionally returns the values as a service response:

```yaml
service: aqua_temp_ble.probe_registers
data:
  block: base        # base | 1001 | 1046 | 1091 | 1136 | 2001 | 2046
```

Log format (one line per register — RAW is the signed int16, hex is the
unsigned 16-bit view):

```
AquaTemp probe: XX:XX:XX:XX:XX:XX block=base (FC03 start=7 count=45) len=95 CRC=OK
  reg   7 =      0 (0x0000) | /10 =     0.0
  reg   8 =      1 (0x0001) | /10 =     0.1  # Power [P]
  reg  13 =      1 (0x0001) | /10 =     0.1  # Mode [P]
  reg  26 =    325 (0x0145) | /10 =    32.5  # InletWaterTemp [P]
  ...
```

Standalone (no HA): `python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX`
(read-only, requires only `pip install bleak`).

## 1. Live read

Press the probe button. Expect the `base` block log. If nothing arrives
within `REQUEST_TIMEOUT` (8 s) → check the phone app is disconnected
(single-connection device), raise the timeout in `const.py`, retry.
This one step validates transport, fragmentation handling and endianness.

## 2. Endianness

Compare a known temperature (e.g. the app shows inlet 24.3 °C):
`reg 26 = 243 (0x00F3)` → big-endian confirmed (default). If you see
`0xF300` instead, set in `registers.py`:

```python
REG_DATA_LITTLE_ENDIAN = True   # one constant; the parse flips
```

## 3. Mode map (step M)

In the vendor app, switch mode Cool → Heat → Auto, pressing probe each
time; note reg 13's raw value for each. Update in `registers.py`:

```python
MODE_VALUE_TO_NAME = {0: "cool", 1: "heat", 2: "auto"}   # adjust order if needed
```

Current order is the [A] default: the sequential register allocation follows
Cool→Heat→Auto (regs 19/20/21), so the Mode enum likely shares that order.

## 4. Binary registers

While the app Status page shows Compressor / Circulate pump / 4-way valve:

- run the heat pump: compressor + pump active → probe, note which regs
  29..46 are `1`
- switch heat ↔ cool to flip the 4-way valve → probe again

Set in `registers.py` (None = entity not created yet):

```python
REG_COMPRESSOR = 29        # example — replace with the verified value
REG_CIRCULATE_PUMP = 30
REG_FOUR_WAY_VALVE = 31
```

Verify polarity; if inverted set `BINARY_ACTIVE_WHEN_NONZERO = False`.
Restart HA → the three binary_sensor entities appear.

## 5. Write frames (step W) — two options, cheapest first

### a. logcat capture (zero risk)

With the vendor app connected to the unit, run on a paired Android phone:

```bash
adb logcat -c
adb logcat | grep -aE "address|6310|指令"
```

then toggle power / mode / setpoint **in the app** — it logs every outgoing
frame in cleartext (`日志:指令 6310 …`) and the target register
(`参数选择修改 ----- index = X address = Y`). Paste the observed frames in
your notes. This pins:

- the **write layout**: H1 16-bit (`…0001 02 value`) vs H2 32-bit
  (`…0002 04 value…`) — the integration ships H1 (safe default: an
  under-length frame can at worst be ignored, never clobber a neighbour)
- the real register addresses for power/mode/setpoint
- whether the app also writes the per-mode setpoint (regs 19/20/21) on a
  setpoint change → flip `WRITE_MODE_SETPOINT_TOO = True`

### b. HA write test

With the device already ON (so a reg 8 write is idempotent):

1. HA: turn_on the climate → probe → confirm `reg 8 == 1` and nothing else moved.
2. One real mode switch → probe → confirm reg 13 only.
3. One setpoint change → probe → check whether reg 19/20/21 mirror it.

If no FC10 echo ever arrives on ff02 (write errors in HA), set:

```python
WRITE_ECHO_EXPECTED = False   # writes become fire-and-forget
```

## 6. Firmware & fault

- Probe hex of regs 48-51: decimal groups (`2.2.1.2040`)? ASCII? Adjust the
  `firmware` formatting in `sensor.py` once.
- Note reg 47 with a fault active vs none (0 = OK assumed [A]).

## 7. High blocks (optional, phase 2)

Probe blocks `1001`…`2046` to map the Status/Control parameter pages
(5×45 + 2×45 registers). Expected homes: Suction/Coil/Exhaust Temp., fans,
switches (1001-1180), control parameters like Cooling/Heating/Auto Set,
Defrost, EEV Mode (2001-2090). Response shapes are unknown [V] — the probe
logs raw hex and never crashes on odd answers. **No writes to 2001-2090.**

## Extra aid: debug password "66"

In the vendor app, entering **`66`** in the settings/parameter view switches
the UI from *status parameters* to the **control parameters** view
(`查看控制参数` vs `查看状态参数`), exposing raw values including `H70/H71/H72`.
This gives direct read access to raw registers from inside the official app,
without any instrumentation — handy to correlate with probe logs.

## Appendix: verified frames (regression vectors)

| Purpose | Frame (hex) |
|---|---|
| Read base block 7..51 | `63030007002D3C54` |
| Query block 1001-1045 | `631003E9002DD9E6` |
| Query block 1046-1090 | `63100416002DE8A2` |
| Query block 1091-1135 | `63100443002DF8B2` |
| Query block 1136-1180 | `63100470002D08BD` |
| Query block 2001-2045 | `631007D1002D591B` |
| Query block 2046-2090 | `631007FE002D68D2` |
| Write reg 8 = 1 (power on) | `631000080001020001D7BA` |
| Write reg 8 = 0 (power off) | `631000080001020000167A` |
| Write reg 13 = 0/1/2 (mode) | `6310000D0001020000162F` / `…01D7EF` / `…0297EE` |
| Write reg 14 = 325 (32.5 °C) | `6310000E0001020145D67F` |

All of these are asserted byte-exact in `tests/test_modbus.py` and
`modbus.py`'s built-in self-test
(`python3 custom_components/aqua_temp_ble/modbus.py`).