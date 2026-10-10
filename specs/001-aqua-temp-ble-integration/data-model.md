# Data model 001 — Registers, entities and decoded values

Status: **IMPLEMENTED (pre-calibration)**. This is the register→quantity
mapping the integration ships today, with the honest confidence status of
every row. Source of truth in code: `custom_components/aqua_temp_ble/registers.py`.

Legend — **[P] PROVEN** (recovered from the APK, CRC-verified frames;
address/meaning corroborated by the pool string list),
**[A] ASSUMED** (default chosen to be safe to flip),
**[V] UNVERIFIED** (needs one live session, `docs/CALIBRATION.md`).

---

## 1. Wire encoding (all base-block registers)

| Property | Value | Status |
|---|---|---|
| Transport | Modbus RTU frame inside a GATT notification (ff02) | [P] |
| Base block | FC03 read, start 7, count 45 → registers 7..51 | [P] |
| Register width | 16-bit signed int (`>h` big-endian; `REG_DATA_LITTLE_ENDIAN` flip) | [P] framing / [A] endianness |
| Temperature scale | `value = int(degrees_C × 10)` | [P] |
| Sensor-absent sentinel | raw `-9990` (= −999.0 °C) → state `unknown` | [P] |
| Slave address | `0x63` | [P] |

## 2. Register map (base block, regs 7..51)

| Reg | Name | Type / scale | Entity | Status |
|---|---|---|---|---|
| 7–10 | *(unmapped)* | — | — | [V] |
| 8 | **Power** | flag: 0=off, 1=on | climate `hvac_mode` (read+write) | **[P]** address+meaning |
| 11 | **EnBoostMode** | flag | *(read only, no entity today)* | [P] address; future use |
| 12 | *(unmapped)* | — | — | [V] |
| 13 | **Mode** | enum cool/heat/auto | climate mode (read+write); values `{0: cool, 1: heat, 2: auto}` | [P] address / **[A]** value map |
| 14 | **SetCurrentTemp** | temp ×10 | climate `target_temperature` (read+write) | **[P]** |
| 15–18 | *(unmapped)* | — | — | [V] |
| 19 | SetCoolTargetTemp | temp ×10 | part of min/max derivation (read) | [P] |
| 20 | SetHeatTargetTemp | temp ×10 | read; written iff `WRITE_MODE_SETPOINT_TOO` | [P] / **[A]** mirror |
| 21 | SetAutoTargetTemp | temp ×10 | idem | [P] / [A] |
| 22 | SetCoolMinTargetTemp | temp ×10 | climate `min_temp` (min of 22/24) | [P] |
| 23 | SetCoolMaxTargetTemp | temp ×10 | climate `max_temp` (max of 23/25) | [P] |
| 24 | SetHeatMinTargetTemp | temp ×10 | climate `min_temp` | [P] |
| 25 | SetHeatMaxTargetTemp | temp ×10 | climate `max_temp` | [P] |
| 26 | **InletWaterTemp** | temp ×10, signed, sentinel-aware | sensor `Inlet water temperature` (°C) | **[P]** |
| 27 | **OutletWaterTemp** | temp ×10, signed, sentinel-aware | sensor `Outlet water temperature` (°C) | **[P]** |
| 28 | **AT** (ambient) | temp ×10, signed, sentinel-aware | sensor `Ambient temperature` (°C) | **[P]** |
| 29–46 | *(unmapped — expected home of compressor / circulate pump / 4-way valve)* | flag (polarity [A]) | binary sensors — **not created** while `address=None` | **[V]** |
| 47 | **Fault1** | int code | sensor `Fault code` (diagnostic); `0` = no fault | [P] address / **[A]** semantics |
| 48–51 | Firmware version | 4× int | sensor `Firmware` (diagnostic), dot-joined; `sw_version` in device registry | [P] group address / **[V]** join semantics |

Write policy: only regs **8, 13, 14** (and optionally 19–21 via the
`WRITE_MODE_SETPOINT_TOO` switch). Nothing else is ever written; blocks
1001–2090 are read-only experimental.

## 3. High blocks (probe-only, experimental)

| Block start | Registers | Recovered query frame | Status |
|---|---|---|---|
| 1001 | 1001–1045 | `631003E9002DD9E6` | [P] frame / [V] response shape |
| 1046 | 1046–1090 | `63100416002DE8A2` | [P] / [V] |
| 1091 | 1091–1135 | `63100443002DF8B2` | [P] / [V] |
| 1136 | 1136–1180 | `63100470002D08BD` | [P] / [V] |
| 2001 | 2001–2045 | `631007D1002D591B` | [P] / [V] |
| 2046 | 2046–2090 | `631007FE002D68D2` | [P] / [V] |

These map (per the pool strings) to the app's "Status parameters" and
"Control parameters" pages (suction/coil/exhaust temps, valves, fans,
cooling/heating/auto set, defrost, EEV mode…). Read-only through the probe;
logged as raw hex unless they parse as FC03.

## 4. Decoded snapshot — `AquaTempData` (coordinator)

One FC03 read produces `{register: raw int16}`; `from_regs` derives the
typed snapshot every entity reads:

| Field | Derivation | Notes |
|---|---|---|
| `registers` | raw dict | raw int16 per address (probe/sensors) |
| `power` | reg 8 ≠ 0 | `None` when register missing |
| `boost` | reg 11 ≠ 0 | read-only, future use |
| `mode_value` / `hvac_mode` | reg 13 → `MODE_VALUE_TO_NAME` → `HVACMode` | OFF when power off; unknown values logged once |
| `target_temp` | reg 14 ÷10 | °C on the wire |
| `min_target` / `max_target` | min(22,24) / max(23,25) ÷10 | fallback 5/40 °C [A] |
| `inlet_temp` / `outlet_temp` / `ambient_temp` | regs 26/27/28 ÷10 | `None` on sentinel |
| `fault_code` | reg 47 | raw int |
| `firmware` | regs 48–51 dot-joined | `None` if any missing/sentinel |
| `binaries` | per `BINARIES` with address ≠ None | empty until calibrated |

## 5. Home Assistant entities

| Entity | Key | Unique id suffix | Register(s) | Created? |
|---|---|---|---|---|
| Climate `Aqua Temp` | `climate` | `_climate` | 8/13/14 (+19–25, 26) | always |
| Sensor Inlet water temperature | `inlet_water_temp` | `_inlet_water_temp` | 26 | always |
| Sensor Outlet water temperature | `outlet_water_temp` | `_outlet_water_temp` | 27 | always |
| Sensor Ambient temperature | `ambient_temp` | `_ambient_temp` | 28 | always |
| Sensor Fault code (diagnostic) | `fault_code` | `_fault_code` | 47 | always |
| Sensor Firmware (diagnostic) | `firmware` | `_firmware` | 48–51 | always |
| Binary sensor Compressor | `compressor` | `_compressor` | [V] 29..46 | **not until calibrated** |
| Binary sensor Circulate pump | `circulate_pump` | `_circulate_pump` | [V] 29..46 | **not until calibrated** |
| Binary sensor 4-way valve | `four_way_valve` | `_four_way_valve` | [V] 29..46 | **not until calibrated** |
| Button Probe registers (diagnostic) | `probe_registers` | `_probe_registers` | base block | always |

Unique id = `{ADDRESS_UPPER}_{key}`; device identifiers = `(aqua_temp_ble,
address)`; `sw_version` = firmware string once known (device registry is
patched the moment the firmware is first reported, and on later changes).

## 6. Calibration switches (all in `registers.py`)

| Switch | Default | Meaning / when to flip |
|---|---|---|
| `MODE_VALUE_TO_NAME` | `{0: cool, 1: heat, 2: auto}` | reg 13 value map — runbook step 3 (M) |
| `REG_DATA_LITTLE_ENDIAN` | `False` | byte-swapped temperatures — step 2 |
| `REG_COMPRESSOR/_CIRCULATE_PUMP/_FOUR_WAY_VALVE` | `None` | pin addresses in 29..46 — step 4 |
| `BINARY_ACTIVE_WHEN_NONZERO` | `True` | inverted binary polarity — step 4 |
| `WRITE_ECHO_EXPECTED` | `True` | no FC10 echo on ff02 → fire-and-forget — step 5 (W) |
| `WRITE_MODE_SETPOINT_TOO` | `False` | app also writes reg 19/20/21 on setpoint change — step 5 |
| `FALLBACK_MIN/MAX_TARGET` | 5.0 / 40.0 | used when bound registers are sentinel — step S |

`const.py` tuning knobs (transport, not calibration): timeouts
(connect 15 s, request 8 s, silence 0.6 s, echo 3 s), `MAX_RX_BUFFER` 512,
`DEBUG_RAW`.