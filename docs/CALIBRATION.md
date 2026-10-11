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

## 8. Spec 003 — the seven families (T/H/R/P/S/O/D)

The vendor app's parameter view (debug password `66`, see the aid below)
exposes the complete parameter table in seven families. The panel
observation (2026-10, `specs/003-sensor-coverage/data-model.md`) proves the
labels and displayed values — **never the numeric register indices**. New
entities appear **only** after pinning; never guess an index
(`address=None` ⇒ no entity).

| Family | Meaning | HA exposure |
|---|---|---|
| T (T01–T06) | live temperatures | temperature sensors — 26/27/28 pinned [P]; T01/T04/T06 placeholders |
| H (H01–H06, H70–H72) | machine settings | **none — never writable from HA** |
| R (R01–R11) | regulation thresholds (installer) | **none — never writable from HA** |
| P (P01–P04) | filtration program | **none — never writable from HA** |
| S (S01–S06) | safety inputs | binary sensors, DIAGNOSTIC (placeholders) |
| O (O01–O04) | outputs | binary sensors (placeholders) |
| D (D01–D06) | digital states | **none** — page not photographed, no speculation |

> 🔒 **Read-only policy for H/R/P (spec 003 §6).** H04 Exhaust Temp. High
> (105.0 °C) is a **safety shutdown threshold**; R01–R11 are installer
> regulation limits; P01–P04 drive the filtration program. They are
> **never writable from Home Assistant**: no entity, no service, and the
> single write funnel (`coordinator.async_write_register`) starts with
> `registers.ensure_writable()`, which raises `ValueError` for anything
> outside {8, 13, 14} (+19/20/21 behind the default-off
> `WRITE_MODE_SETPOINT_TOO` switch). Record their addresses in your notes if
> the probe reveals them — do not pin them as entities, never write them.

### 8.1 Panel worksheet (calibration anchors)

With the vendor app open in the parameter view, photograph every family
page (including **D** — still missing) and note the time; the unit state
must match during the probe. Idle-state predicted raws (displayed ×10 [A]):

| Member | Displayed | Predicted raw (hex) |
|---|---|---|
| T01 Suction | 3276.5 (absent) | `32765 / 0x7FFD` **or** `−9990 / 0xD8FA` — whichever appears is this register's sentinel convention |
| T02 Inlet | 17.0 | `170 / 0x00AA` (expect reg 26) |
| T03 Outlet | 19.5 | `195 / 0x00C3` (expect reg 27) |
| T04 Coil | 19.5 | `195 / 0x00C3` (the other one) |
| T05 Ambient | 18.0 | `180 / 0x00B4` (expect reg 28) |
| T06 Exhaust | 17.5 | `175 / 0x00AF` |
| H03 Comp. stop | 0.0 | `0` (mapping note only) |
| H04 Exhaust high | 105.0 | `1050 / 0x041A` (mapping note only) |
| S01 / S02 / S03 | OFF / OFF / ON | `0` / `0` / `1` [A] |
| O01–O04 | all OFF | `0` [A] |
| P02 / P03 / P04 | 0 / 3 / 0 | `0` / `3` / `0` [A] |
| R01–R11 | 0.5–40.0 range | `5–400` (mapping note only) |

Temperatures drift — re-photograph the T page at probe time if the session
is not back-to-back (a 0.1–0.2 °C drift is fine; a different *pattern* is
not).

### 8.2 Pinning the T family

Probe `base` + all six high blocks within a few minutes of the photos
(§1/§7 above), then:

1. **Confirm the scale + existing identities**: regs 26/27/28 must read
   `170 / 195 / 180` if the panel showed T02=17.0, T03=19.5, T05=18.0 at
   probe time → T02≡26, T03≡27, T05≡28 confirmed. Byte-swapped
   (`0xAA00`-style)? → step 2 (endianness) first.
2. **T06 Exhaust**: look for raw `175`. Prefer a base-block (7..51) match —
   entities are only created for the polled base block (FR-7).
3. **T04 Coil**: the remaining `195` (T03 is reg 27). Disambiguate with a
   state change: probe again while heating — coil and outlet diverge.
4. **T01 Suction**: look for the sentinel — `32765` (`0x7FFD`) or `−9990`
   (`0xD8FA`). Record **which convention** this register uses. The suction
   sensor is absent on this unit: the entity will honestly show `unknown` —
   HA never displays 3276.5 °C.
5. Pin in `registers.py` (new constants next to the existing placeholders):

```python
REG_SUCTION_TEMP = <addr>   # T01
REG_COIL_TEMP = <addr>      # T04
REG_EXHAUST_TEMP = <addr>   # T06
```

### 8.3 Pinning the S and O families — the two-state method

One idle dump is **not** enough (~25 unmapped base registers read 0):

1. **Idle probe**: O candidates = registers reading `0`; S01/S02
   candidates = `0`; S03 candidates = `1` [A].
2. **Heating probe (required)**: probe again with the compressor + pump +
   fan running. Registers flipping `0 → nonzero` map to the O family;
   correlate fan vs pump vs 4-way valve (the valve changes only on a mode
   change, if ever). A flow-switch candidate flips when pool circulation
   stops/starts.
3. **Polarity**: set each entry's `active_when_nonzero` so the entity
   matches the **panel** OFF/ON text in both observed states (S03 Flow
   reading ON at idle is expected — keep the panel semantics, not a guess).
4. Pin in `registers.py` (+ per-entry polarity where inverted):

```python
REG_COMPRESSOR = <addr>      # O01
REG_CIRCULATE_PUMP = <addr>  # O02
REG_HIGH_FAN = <addr>        # O03
REG_FOUR_WAY_VALVE = <addr>  # O04
REG_HP_SWITCH = <addr>       # S01
REG_LP_SWITCH = <addr>       # S02
REG_FLOW_SWITCH = <addr>     # S03
```

### 8.4 Restart & verify

Restart HA. New entities appear for every member pinned inside 7..51
(exact names per `specs/003-sensor-coverage/contracts/entity-contract.md`).
A member found **only in a high block** stays absent — high-block polling
is **phase B**, a future spec; note the block in your runbook notes and
leave the register unpinned (pinning it fails the import-time integrity
check loudly). Cross-check each new entity against a **fresh panel photo**:
values within one poll interval, sentinels shown as `unknown` (never
3276.5), S/O states matching the panel text.

Failure modes: two labels pinned to one register → the import-time
integrity check raises at startup (re-run the disambiguation); a predicted
raw matching many registers → use the two-state method, never pin on a
single coincidence; binary states inverted vs the panel → flip that
entry's `active_when_nonzero`, no code change.

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