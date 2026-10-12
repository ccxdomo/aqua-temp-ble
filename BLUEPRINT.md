# Aqua Temp BLE — Home Assistant Custom Integration Blueprint

> **⚠️ SUPERSEDED — read [`specs/020-integration-reference/`](specs/020-integration-reference/)
> for the current, live-proven reference.** This blueprint is the original
> 2026-10-10 design document, kept as the historical record; its GATT table
> ("write ff01", "ff03 role unknown — do not use") was **refuted** by the
> spec-010 disassembly re-read (service `ff01`, write `ff03`, notify `ff02`),
> and the read/write plane it assumes (classic FC03 base reads, reg 8/13/14
> writes) was replaced live by the pushed-block read plane and the 1011/1046
> control writes (specs 013-019). The body below is deliberately untouched.

Date: 2026-10-10 · Author: Architect (pipeline) · Status: **READY FOR CODER**
Target: custom integration `aqua_temp_ble` controlling a PHNIX pool heat pump
("Aqua Temp" app, `com.phinx.poolheater4`) over BLE, without the vendor cloud.

Evidence base: `aqua-temp-ble/FINDINGS.md` (APK static analysis, 2026-10-10).
All 7 recovered frames were re-verified against the reference CRC
implementation in this blueprint (7/7 byte-exact).

## Evidence legend

- **[P] PROVEN (static)** — recovered from the APK object pool / DEX and
  CRC-verified. Frame bytes, UUIDs, transport layout, ×10 scale.
- **[A] ASSUMED** — reasonable default, isolated in `registers.py` as a single
  constant, cheap to correct after calibration. Each has a verification step.
- **[V] TO-VERIFY (live)** — cannot be known without one live session with the
  unit. The integration ships a probe tool (§4) that closes every [V] item.

Key [V] facts: no live BLE read has been performed yet (frames were recovered
from app constants, not from a capture). Response chunking, write echo,
register-value endianness, mode value mapping, and the binary-sensor register
indices are all live-verified via the runbook (§4.4).

---

## 0. Protocol summary (fixed facts the Coder must not re-derive)

| Fact | Value | Status |
|---|---|---|
| Transport | Modbus RTU frames written as raw bytes to a BLE GATT characteristic; responses arrive as notifications on a second characteristic | **[P]** |
| GATT write | `0000ff01-0000-1000-8000-00805f9b34fb` (handle 2214) | **[P]** |
| GATT notify | `0000ff02-0000-1000-8000-00805f9b34fb` (handle 2216) | **[P]** |
| Third char `ff03` (handle 2215) | role unknown — do **not** use | **[V]** |
| Modbus slave | `0x63` | **[P]** |
| CRC | CRC16-Modbus (poly 0xA001, init 0xFFFF), appended **little-endian** (low byte first) | **[P]** |
| Base read | `63 03 0007 002D CRC` → 45 registers (7..51), response 95 bytes: `63 03 5A <90 data bytes> CRC` | **[P]** |
| High-block queries | **8-byte** frames `63 10 [start:2] [qty:2] CRC` — FC10 shape **without** bytecount/data, used as "read block" selector. Response format unknown | **[P]** (frames) / **[V]** (response) |
| Writes | FC10: `63 10 [addr:2] [qty:2] [bytecount] [data…] [CRC]` | **[P]** (format) |
| Temperature scale | `value = int(degrees_C × 10)` (32.5 °C → 325) | **[P]** |
| Sentinel | register raw `-9990` (signed) = −999.0 °C = sensor absent | **[P]** |
| Device name | advertises as `BlueNRG-…` (owner-confirmed). Other models: `HIFLYING…`, `PROV_…` | **[P]** |
| Register data byte order | assumed big-endian (standard Modbus) | **[A]** |

Verified frame vectors (regression tests for `modbus.py`):

| Purpose | Frame (hex) |
|---|---|
| Read base block 7..51 | `63030007002d3c54` |
| Query block 1001-1045 | `631003e9002dd9e6` |
| Query block 1046-1090 | `63100416002de8a2` |
| Query block 1091-1135 | `63100443002df8b2` |
| Query block 1136-1180 | `63100470002d08bd` |
| Query block 2001-2045 | `631007d1002d591b` |
| Query block 2046-2090 | `631007fe002d68d2` |
| Write H1: Power on (reg 8=1) | `631000080001020001d7ba` |
| Write H1: Power off (reg 8=0) | `631000080001020000167a` |
| Write H1: Mode=0/1/2 (reg 13) | `6310000d0001020000162f` / `6310000d0001020001d7ef` / `6310000d000102000297ee` |
| Write H1: target 32.5 °C (reg 14=325) | `6310000e0001020145d67f` |

**Write-layout ambiguity [V].** FINDINGS §12.1 contains two conflicting hints
about single-parameter writes:
- H1 (standard): qty=1, bytecount=2, 16-bit value → `63 10 [addr] 0001 02 [val:2] CRC`
- H2 (reconstructed from a Dart call): qty=2, bytecount=4, 32-bit value → `63 10 [addr] 0002 04 [val:4] CRC`

**Decision: implement H1.** Rationale: if H2 is the truth and we send H1, the
worst case is the device ignores an under-length frame (no side effect). If we
defaulted to H2 and H1 were true, we would zero a neighbouring register
(clobbering). H1 is the safe failure mode. The runbook (§4.4 step W) pins the
real layout via the app's own logcat logging (`"日志:指令 6310 …"` prints every
outgoing frame in cleartext), then one constant in `registers.py` switches to
H2 if needed.

---

## 1. Project structure

Workspace scaffold: `aqua-temp-ble/custom_components/aqua_temp_ble/`
(repo to be created later in `ccxdomo` with this as HACS layout, plus
`README.md` and `docs/CALIBRATION.md` at repo root).

```
custom_components/aqua_temp_ble/
├── manifest.json          # domain, version, requirements=[bleak], iot_class=local_polling
├── __init__.py            # entry setup/unload, hass.data registry, options listener,
│                          #   domain service registration (probe_registers)
├── const.py               # DOMAIN, UUIDs, name prefixes, platform list, timeout consts
├── registers.py           # ★ SINGLE SOURCE OF TRUTH: register addresses, mode map,
│                          #   sensor/binary registries, calibration switches
├── modbus.py              # pure protocol: CRC16, frame build/parse, expected-length
│                          #   (no I/O — unit-testable standalone)
├── aqua_client.py         # AquaTempBleClient: bleak connect/scan/notify/write,
│                          #   response reassembly, reconnection
├── coordinator.py         # AquaTempCoordinator (DataUpdateCoordinator) + AquaTempData
├── entity.py              # AquaTempEntity base: CoordinatorEntity + DeviceInfo + unique_id
├── climate.py             # climate platform (reads 8/13/14, writes 8/13/14)
├── sensor.py              # temperature/fault/firmware sensors (registry-driven)
├── binary_sensor.py       # compressor/pump/valve (registry-driven, gated on calibration)
├── button.py              # "Probe registers" diagnostic button
├── config_flow.py         # config flow + options flow
├── services.yaml          # probe_registers service schema
├── strings.json           # config/options/services strings
└── translations/en.json    # same, for translations
```

Existing RE assets in the parent directory are **standalone tools**, kept as-is
and NOT imported by the integration: `modbus_ble.py` (CLI client), `ble_probe.py`
(raw observer). The integration ports their logic; the tools stay usable
side-by-side during calibration.

### manifest.json (exact content)

```json
{
  "domain": "aqua_temp_ble",
  "name": "Aqua Temp BLE",
  "codeowners": ["@ccxdomo"],
  "config_flow": true,
  "dependencies": ["bluetooth"],
  "documentation": "https://github.com/ccxdomo/aqua-temp-ble",
  "integration_type": "device",
  "iot_class": "local_polling",
  "loggers": ["custom_components.aqua_temp_ble"],
  "requirements": ["bleak>=0.22.0"],
  "version": "0.1.0"
}
```

(`bleak` is bundled by HA; listing the floor version is harmless and makes the
dependency explicit for standalone use. `documentation` URL is a placeholder
until the repo is created.)

### const.py (key constants)

```python
DOMAIN = "aqua_temp_ble"
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

UUID_WRITE  = "0000ff01-0000-1000-8000-00805f9b34fb"   # [P] handle 2214
UUID_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"   # [P] handle 2216

NAME_PREFIXES = ("bluerng-", "hiflying-", "prov_")     # [P] app's own scan filters

CONNECT_TIMEOUT   = 15.0   # seconds
REQUEST_TIMEOUT   = 8.0    # max wait for full Modbus response
SILENCE_TIMEOUT   = 0.6     # gap that ends a fragmented response
WRITE_ECHO_TIMEOUT = 3.0    # max wait for FC10 echo; 0 = fire-and-forget
MAX_RX_BUFFER     = 512

DEFAULT_POLL_INTERVAL = 30  # seconds (options: 10..600)
DEFAULT_TEMPERATURE_UNIT = "C"  # display only; wire is always ×10 Celsius [P]
```

### registers.py (★ the calibration surface — exact content)

Every label↔register or value↔meaning decision lives here and nowhere else.
Correcting a mis-mapped label after calibration = editing one entry + restart.

```python
from dataclasses import dataclass

BASE_BLOCK_START = 7          # [P]
BASE_BLOCK_COUNT = 45         # [P] -> registers 7..51

# --- [P] verified base-block registers ---
REG_POWER            = 8    # 0=off, 1=on
REG_BOOST            = 11   # future use, read-only here
REG_MODE             = 13   # cool/heat/auto, values below [A]
REG_SET_CURRENT_TEMP = 14   # current setpoint, ×10
REG_SET_COOL_TARGET  = 19   # ×10
REG_SET_HEAT_TARGET  = 20   # ×10
REG_SET_AUTO_TARGET  = 21   # ×10
REG_COOL_MIN_TARGET  = 22   # ×10
REG_COOL_MAX_TARGET  = 23   # ×10
REG_HEAT_MIN_TARGET  = 24   # ×10
REG_HEAT_MAX_TARGET  = 25   # ×10
REG_INLET_WATER_TEMP = 26   # ×10, signed
REG_OUTLET_WATER_TEMP = 27  # ×10, signed
REG_AMBIENT_TEMP     = 28   # ×10, signed
REG_FAULT1           = 47
REG_FIRMWARE_FIRST   = 48   # 48..51 [V] semantic
REG_FIRMWARE_LAST    = 51

# --- [V] TO-VERIFY placeholders: set the register number after calibration
# (docs/CALIBRATION.md §4.4). None = entity not created yet.
REG_COMPRESSOR: int | None = None      # expected somewhere in 29..46
REG_CIRCULATE_PUMP: int | None = None   # expected somewhere in 29..46
REG_FOUR_WAY_VALVE: int | None = None  # expected somewhere in 29..46

# --- [A] value mappings (verify via runbook §4.4 step M) ---
MODE_VALUE_TO_NAME = {0: "cool", 1: "heat", 2: "auto"}
# Rationale for this order: the sequential register allocation follows
# Cool→Heat→Auto (regs 19/20/21), so the Mode enum likely shares that order.

SENTINEL_RAW = -9990        # [P] -999.0 °C × 10 = sensor absent
TEMP_SCALE = 10.0           # [P]
FALLBACK_MIN_TARGET = 5.0   # [A] if bound registers are missing/sentinel
FALLBACK_MAX_TARGET = 40.0  # [A]

# --- calibration switches (flip after live verification) ---
WRITE_ECHO_EXPECTED = True     # False if device sends no FC10 echo (runbook W)
WRITE_MODE_SETPOINT_TOO = False  # True if app also writes reg 19/20/21 on setpoint change
BINARY_ACTIVE_WHEN_NONZERO = True  # [A] polarity of binary sensors

# --- sensor/binary registries (drive the platforms; address=None -> skipped) ---

@dataclass(frozen=True)
class RegDef:
    key: str              # entity key + translation key + unique_id suffix
    address: int | None   # None = unverified -> entity not added
    label: str            # probe-log annotation
    scale: float = 1.0
    signed: bool = False
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    icon: str | None = None
    diagnostic: bool = False
    is_binary: bool = False

SENSORS: tuple[RegDef, ...] = (
    RegDef("inlet_water_temp",  REG_INLET_WATER_TEMP,  "InletWaterTemp",  TEMP_SCALE, True,
           "temperature", "°C", "measurement"),
    RegDef("outlet_water_temp", REG_OUTLET_WATER_TEMP, "OutletWaterTemp", TEMP_SCALE, True,
           "temperature", "°C", "measurement"),
    RegDef("ambient_temp",      REG_AMBIENT_TEMP,      "AT",              TEMP_SCALE, True,
           "temperature", "°C", "measurement"),
    RegDef("fault_code",       REG_FAULT1,            "Fault1", icon="mdi:alert-circle"),
    RegDef("firmware",         REG_FIRMWARE_FIRST,    "Firmware", icon="mdi:information",
           diagnostic=True),  # special: joins regs 48..51
)

BINARIES: tuple[RegDef, ...] = (
    RegDef("compressor",     REG_COMPRESSOR,     "Compressor",     is_binary=True, icon="mdi:engine"),
    RegDef("circulate_pump", REG_CIRCULATE_PUMP, "Circulate pump", is_binary=True, icon="mdi:pump"),
    RegDef("four_way_valve", REG_FOUR_WAY_VALVE, "4-way valve",    is_binary=True, icon="mdi:swap-horizontal"),
)
```

---

## 2. BLE/Modbus module design

### 2.1 modbus.py — pure protocol (no I/O)

```python
"""Modbus RTU frame build/parse for Aqua Temp over BLE. No I/O here."""
import struct

SLAVE_ID = 0x63
FC_READ_HOLDING = 0x03
FC_WRITE_MULTI = 0x10


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus: poly 0xA001, init 0xFFFF. Wire order: low byte first."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _frame(fc: int, payload: bytes) -> bytes:
    adu = struct.pack(">BB", SLAVE_ID, fc) + payload
    return adu + struct.pack("<H", crc16_modbus(adu))


def build_read_holding_registers(start: int, count: int) -> bytes:
    """FC03. start=7,count=45 -> 63030007002d3c54  [P]"""
    return _frame(FC_READ_HOLDING, struct.pack(">HH", start, count))


def build_query_block(start: int, count: int) -> bytes:
    """Proprietary 8-byte block query: 63 10 start qty CRC.
    start=1001,count=45 -> 631003e9002dd9e6  [P] (response format [V])"""
    return _frame(FC_WRITE_MULTI, struct.pack(">HH", start, count))


def build_write_registers(start: int, values: list[int]) -> bytes:
    """FC10. H1 layout: qty=len(values), bytecount=2*qty, values big-endian.
    start=14,values=[325] -> 6310000e0001020145d67f"""
    payload = struct.pack(">HHB", start, len(values), 2 * len(values))
    payload += struct.pack(">" + "H" * len(values), *[v & 0xFFFF for v in values])
    return _frame(FC_WRITE_MULTI, payload)


def expected_response_length(buf: bytes) -> int | None:
    """Total Modbus frame length implied by the first bytes, or None if unknown.
    FC03 response: addr,fc,bytecount + bytecount data + 2 CRC = bytecount+5.
    FC10 echo:     addr,fc,start:2,qty:2 + 2 CRC = 8.
    Exception:      addr,fc|0x80,errcode + 2 CRC = 5."""
    if len(buf) < 2 or buf[0] != SLAVE_ID:
        return None
    fc = buf[1]
    if fc & 0x80:
        return 5
    if fc == FC_READ_HOLDING:
        return buf[2] + 5 if len(buf) >= 3 else None
    if fc == FC_WRITE_MULTI:
        return 8
    return None


class ModbusError(Exception): ...
class ModbusCRCError(ModbusError): ...
class ModbusExceptionError(ModbusError):
    def __init__(self, code: int): self.code = code


def parse_read_response(frame: bytes, start: int, count: int) -> dict[int, int]:
    """Validate + decode an FC03 response to {register: raw signed int16}."""
    if len(frame) < 5 or not validate_crc(frame):
        raise ModbusCRCError(f"bad CRC: {frame.hex()}")
    if frame[1] == FC_READ_HOLDING | 0x80:
        raise ModbusExceptionError(frame[2])
    if frame[1] != FC_READ_HOLDING or frame[2] != 2 * count or len(frame) != 2 * count + 5:
        raise ModbusError(f"malformed response: {frame.hex()}")
    regs: dict[int, int] = {}
    for i in range(count):
        raw = struct.unpack(">h", frame[3 + 2 * i : 5 + 2 * i])[0]  # big-endian [A]
        regs[start + i] = raw
    return regs


def parse_write_echo(frame: bytes, start: int, qty: int) -> None:
    """Validate an FC10 echo (addr,fc,start:2,qty:2,CRC). Raises on mismatch."""
    if len(frame) != 8 or not validate_crc(frame):
        raise ModbusError(f"bad write echo: {frame.hex()}")
    if frame[1] == FC_WRITE_MULTI | 0x80:
        raise ModbusExceptionError(frame[2])
    if struct.unpack(">HH", frame[2:6]) != (start, qty):
        raise ModbusError(f"echo mismatch: {frame.hex()}")


def validate_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16_modbus(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]
```

Self-test (module `__main__` or unit test): the 7 vectors of §0 must
byte-match, plus H1 write vectors. This is the Coder's first acceptance gate.

### 2.2 aqua_client.py — AquaTempBleClient

```python
class AquaTempBleClient:
    """Owns the bleak connection and one-at-a-time request/response cycle."""

    def __init__(self, address: str, name: str | None = None): ...
    @property
    def is_connected(self) -> bool: ...

    async def connect(self) -> None: ...        # resolve + connect + start_notify
    async def disconnect(self) -> None: ...

    async def read_registers(self, start: int, count: int) -> dict[int, int]: ...
    async def query_block(self, start: int, count: int) -> bytes: ...  # experimental [V]
    async def write_registers(self, start: int, values: list[int]) -> None: ...
```

Responsibilities and exact behaviour:

**a) Device resolution (scan/filter by name).**
1. `bluetooth.async_ble_device_from_address(hass, address, connectable=True)`
   (HA bluetooth manager cache — no scan needed).
2. Fallback: `await BleakScanner.find_device_by_address(address, timeout=5)`.
3. Fallback: `await BleakScanner.discover(timeout=5)` and match
   `name.lower().startswith(NAME_PREFIXES)`; if found at a **different**
   address, log a warning telling the user to re-configure (MAC changed) —
   do NOT silently rewrite the config entry.

**b) Connect.** `BleakClient(device, disconnected_callback=self._on_disconnect)`;
`await client.connect(timeout=CONNECT_TIMEOUT)`; then
`await client.start_notify(UUID_NOTIFY, self._on_notify)`. Two connect
attempts before raising `AquaTempConnectionError`. On connect, resolve the
write characteristic: `char = client.services.get_characteristic(UUID_WRITE)`
and remember whether `"write"` is in `char.properties` → write-with-response,
else write-without-response. (The app's Dart call hints at write type "02"
= Android WRITE_TYPE_DEFAULT = with response; detection makes us robust
either way.)

**c) Request/response cycle.** Exactly one request in flight (enforced by the
coordinator's `asyncio.Lock`; the client also refuses a second concurrent
`_request`). `_request(frame, expected_fc, timeout)`:

1. Create `asyncio.Future`; clear `_rx`; set `_expected_fc`.
2. `await client.write_gatt_char(UUID_WRITE, frame, response=…)` (type from b).
3. Await the future with `asyncio.timeout(timeout)`.
4. Return the complete validated frame bytes.

**d) Notification reassembly (THE critical piece).** BLE notifications for the
95-byte FC03 response WILL arrive fragmented (typical chunks 20 bytes at
default MTU; possibly 1 chunk if MTU is negotiated higher; chunking is **[V]**
— the algorithm below handles every case):

```python
def _on_notify(self, _char, data: bytearray) -> None:
    if const.DEBUG_RAW:                       # const flag, default False
        _LOGGER.debug("ff02 chunk (%d): %s", len(data), bytes(data).hex())
    if self._pending is None or self._pending.done():
        _rx_reset(self._rx); return           # stale push: drop
    self._rx.extend(data)
    if len(self._rx) >= 1 and self._rx[0] != SLAVE_ID:
        _rx_reset(self._rx); _LOGGER.warning("dropping non-Modbus notify: %s", self._rx.hex()); return
    if len(self._rx) > MAX_RX_BUFFER:
        _rx_reset(self._rx); _fail_pending("response too long"); return
    expected = expected_response_length(self._rx)
    if expected is not None and expected_fc_matches(self._rx, self._expected_fc):
        if len(self._rx) >= expected:
            self._complete(bytes(self._rx[:expected])); return
    self._arm_silence_timer(SILENCE_TIMEOUT)  # (re)armed after every chunk
```

Completion rules, in order:
1. **Expected-length** (primary): `len(_rx) >= expected_response_length(_rx)`
   → complete with `_rx[:expected]` (never more; remainder dropped).
2. **Silence-timeout** (fallback, 0.6 s without a new chunk): if `len(_rx) >= 5`
   and `validate_crc(_rx)` → complete with the whole buffer (covers unknown
   response shapes, e.g. high-block query responses **[V]**).
3. **Request timeout** (REQUEST_TIMEOUT): overall deadline; on expiry, if the
   buffer holds a CRC-valid frame accept it, else raise `AquaTempTimeoutError`.

`_complete()` validates CRC and expected FC (request FC or FC|0x80), extracts
exception code if `fc & 0x80`, then resolves the future. Every completed frame
is debug-logged in hex.

**e) Writes.** `write_registers(start, values)`:
frame = `build_write_registers`; if `WRITE_ECHO_EXPECTED` (registers.py) →
`_request(frame, expected_fc=0x10, timeout=WRITE_ECHO_TIMEOUT)` +
`parse_write_echo`; else fire-and-forget (return after the GATT write).
On echo timeout/absence when echo was expected → raise (surfaced to the user;
runbook step W decides the switch).

**f) High-block query (experimental).** `query_block(start, count)` sends the
8-byte query and returns the raw response bytes after silence-based
completion (**[V]** — response shape unknown). Used only by the probe service.
Never used by polling.

**g) Reconnection.** `_on_disconnect` sets `is_connected=False`, logs, and
cancels any pending future. The client never auto-reconnects in a loop; the
coordinator reconnects on the next poll (backoff handled by HA). A single
`disconnect()` is idempotent and stops nothing else (notify ends with the
connection).

**h) Error taxonomy** (all in `aqua_client.py`): `AquaTempError(Exception)`
base → `AquaTempConnectionError`, `AquaTempTimeoutError`,
`AquaTempResponseError` (wraps ModbusError from modbus.py).

### 2.3 What is deliberately NOT in the client

No high-block polling, no time-sync writes (reg 1267 frame exists in the app
but the device has its own panel clock — out of scope), no ff03 usage, no
bonding/pairing handling (the unit advertises connectable without pairing;
app pairs by holding a button until the panel shows `bLE` — that is a one-time
user action, documented in README, not integration code).

---

## 3. Home Assistant entities

All entities: `CoordinatorEntity[AquaTempCoordinator]`, `_attr_has_entity_name
= True`, `unique_id = f"{address}_{key}"` (address upper-cased), device =
`DeviceInfo(identifiers={(DOMAIN, address)}, name=entry.title, manufacturer=
"PHNIX", model="Aqua Temp (BlueNRG)")`, `sw_version` from firmware regs when
decodable.

Everything reads from the single FC03 poll of registers 7..51. Writes go to
regs 8 / 13 / 14.

| # | Platform | key | Register | Scale | device_class | Unit | State class | Category |
|---|---|---|---|---|---|---|---|---|
| 1 | climate | `climate` | reads 8,13,14,19-25,26 · writes 8,13,14 | — | climate | opt C/F | — | — |
| 2 | sensor | `inlet_water_temp` | 26 | ÷10 | temperature | °C | measurement | — |
| 3 | sensor | `outlet_water_temp` | 27 | ÷10 | temperature | °C | measurement | — |
| 4 | sensor | `ambient_temp` | 28 | ÷10 | temperature | °C | measurement | — |
| 5 | sensor | `fault_code` | 47 | 1 | — | — | — | — |
| 6 | sensor | `firmware` | 48-51 (joined) | 1 | — | — | — | diagnostic |
| 7 | binary_sensor | `compressor` | **None [V]** | — | — | — | — | — |
| 8 | binary_sensor | `circulate_pump` | **None [V]** | — | — | — | — | — |
| 9 | binary_sensor | `four_way_valve` | **None [V]** | — | — | — | — | — |
| 10 | button | `probe_registers` | — | — | — | — | — | diagnostic |

Rows 7-9: `RegDef.address is None` in `registers.py` → the platform **does not
create the entity**. After calibration (§4.4) the user sets the real register
number, restarts HA, entities appear. This is the mechanism that makes the
80%-confidence label map safe: nothing named "compressor" can show wrong data.

### 3.1 climate.py

```python
class AquaTempClimateEntity(ClimateEntity, AquaTempEntity):
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step = 0.5

    # read path (all from coordinator.data):
    #   hvac_mode      = OFF if reg8==0 else MODE_VALUE_TO_NAME.get(reg13) -> HVACMode
    #                    (unknown mode value -> None, log once)
    #   current_temperature = reg26 / 10 (None if sentinel)
    #   target_temperature = reg14 / 10 (None if sentinel)
    #   min_temp = min(reg22, reg24)/10 ; max_temp = max(reg23, reg25)/10
    #              fallback FALLBACK_MIN/MAX_TARGET if any is None/sentinel
    #   temperature_unit = from options (display only; wire is ×10 °C [P])

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_write_register(REG_POWER, 0)
        else:
            if self.coordinator.data.power is not True:      # currently off
                await self.coordinator.async_write_register(REG_POWER, 1)
            await self.coordinator.async_write_register(REG_MODE, HVAC_TO_MODE_VALUE[hvac_mode])

    async def async_set_temperature(self, **kwargs):
        temp = kwargs[ATTR_TEMPERATURE]
        clamp to [self.min_temp, self.max_temp]
        await self.coordinator.async_write_register(
            REG_SET_CURRENT_TEMP, int(round(temp * TEMP_SCALE)))
```

Write order for power-on+mode: **power first, then mode** (a mode write while
powered off may be ignored by the controller **[V]**). Writes are serialized
by the coordinator; after the last write the coordinator refreshes once (see
§6), so the entity state reflects the *device-confirmed* value, never an
optimistic guess.

Notes:
- `MODE_VALUE_TO_NAME` is the single place where mode ints live **[A]**; runbook
  step M verifies by switching modes in the app and probing reg 13.
- `WRITE_MODE_SETPOINT_TOO` (default False): if runbook step S shows the app
  also writes the active per-mode setpoint (reg 19/20/21) when changing the
  target, flip it True → `async_set_temperature` additionally writes the
  active mode's setpoint register.

### 3.2 sensor.py / binary_sensor.py / button.py

- `sensor.py`: iterates `registers.SENSORS`; skips `address=None`. State =
  `raw / scale` (rounded 0.1 for temperatures); sentinel → `STATE_UNKNOWN`/
  None. `firmware` special-case: `f"{r48}.{r49}.{r50}.{r51}"` from raw ints
  (**[V]** semantics — probe hex reveals whether ASCII).
- `binary_sensor.py`: iterates `registers.BINARIES`; skips `address=None`;
  `is_on = (raw != 0) if BINARY_ACTIVE_WHEN_NONZERO else (raw == 0)` **[A]**.
- `button.py`: `Probe registers` — on press:
  `await self.coordinator.async_probe_block("base")` (§4). Icon `mdi:tune`,
  entity_category diagnostic.

---

## 4. Verification tool (label→register calibration)

Purpose: the label→register map is only ~80% established by static analysis
(indices are built at runtime in the app); the probe tool closes the gap by
reading the whole base block and logging **every** register raw, so the user
compares the log against the app's Status page and pins each label.

### 4.1 Surfaces

- **Button** `button.<device>_probe_registers` (always available) — presses
  run the probe for the base block and log it.
- **Service** `aqua_temp_ble.probe_registers` — for scripts/automations and
  for reading **high blocks**. Fields (both optional):
  - `device_id` (selector: `device`, filter `integration: aqua_temp_ble`);
    omitted → all configured devices are probed.
  - `block`: `select` with options `base | 1001 | 1046 | 1091 | 1136 | 2001 | 2046`,
    default `base`.
  - `supports_response = OPTIONAL`.

### 4.2 Probe procedure (coordinator method `async_probe_block`)

Same lock and connection handling as polling; one request total:
- `base` → FC03 `build_read_holding_registers(7, 45)` (proven path), or
- `1001` … `2046` → `client.query_block(start, 45)` (8-byte verified query;
  response shape **[V]**, best-effort silence completion + CRC check).

### 4.3 Log format (exact)

Header, then one line per register, `_LOGGER.info` (visible in
`home-assistant.log`):

```
AquaTemp probe: XX:XX:XX:XX:XX:XX block=base (FC03 start=7 count=45) len=95 CRC=OK
  reg   7 =      0 (0x0000) | /10 =     0.0
  reg   8 =      1 (0x0001) | /10 =     0.1  # Power [P]
  reg  13 =      1 (0x0001) | /10 =     0.1  # Mode [P]
  reg  26 =    325 (0x0145) | /10 =    32.5  # InletWaterTemp [P]
  reg  29 =      1 (0x0001) | /10 =     0.1
  ...
```

Exact f-string (RAW is the **signed** int16; hex is the unsigned 16-bit view):

```python
line = f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) | /10 = {raw / 10:7.1f}"
if label := KNOWN_LABELS.get(addr):           # reverse map built from registers.py
    line += f"  # {label}"
```

Service response (also returned to the caller):

```json
{"XX:XX:XX:XX:XX:XX": {"block": "base", "crc": "OK",
  "registers": {"26": {"raw": 325, "hex": "0x0145", "tenths": 32.5, "label": "InletWaterTemp"}}}}
```

### 4.4 Calibration runbook (docs/CALIBRATION.md — user-facing)

Ordered steps; each one converts a **[V]/[A]** into verified fact, and every
correction lands in `registers.py` only.

1. **Live read.** Press the probe button. Expect the `base` block log. If
   nothing arrives within `REQUEST_TIMEOUT` → check phone app is disconnected
   (single-connection device), raise timeout, retry. This validates
   transport, chunking and endianness in one shot.
2. **Endianness.** Compare a known temperature (e.g. app shows inlet 24.3 °C):
   `reg 26 = 243 (0x00F3)` → big-endian confirmed. If you see `0xF300`, set
   `REG_DATA_LITTLE_ENDIAN = True` in registers.py (one constant; parse flips).
3. **Mode map (step M).** In the app, switch mode Cool → Heat → Auto, pressing
   probe each time; note reg 13's raw for each. Update `MODE_VALUE_TO_NAME`.
4. **Binary registers.** While the app Status page shows Compressor /
   Circulate pump / 4-way valve states: probe; identify which regs 29..46
   match (run the heat pump: compressor+pump active; switch heat↔cool to flip
   the 4-way valve). Set `REG_COMPRESSOR / REG_CIRCULATE_PUMP /
   REG_FOUR_WAY_VALVE`. Verify polarity; flip `BINARY_ACTIVE_WHEN_NONZERO`
   if inverted.
5. **Write frames (step W) — two options, cheapest first.**
   a. *logcat capture (zero risk):* with the app connected, run
      `adb logcat | grep -aE "address|6310|指令"` and toggle power / mode /
      setpoint in the app — it logs every outgoing frame in cleartext and the
      target address (`参数选择修改 ----- index = X address = Y`). Paste the
      observed frames into the runbook appendix; confirm H1 vs H2 layout.
   b. *HA write test:* with the device already ON, call climate turn_on (reg 8
      write is idempotent) and probe to confirm reg 8 == 1 and nothing else
      moved. Then a real mode switch; then a setpoint change (step S: probe
      whether reg 19/20/21 mirror the change → `WRITE_MODE_SETPOINT_TOO`).
   If no FC10 echo ever arrives on ff02, set `WRITE_ECHO_EXPECTED = False`.
6. **Firmware & fault.** Probe hex of regs 48-51 (ASCII? decimal groups?) →
   adjust the `firmware` formatter once. Note reg 47 behaviour when a fault is
   active vs none (0 = OK assumed **[A]**).
7. **High blocks (optional, phase 2).** Probe `1001`…`2046` blocks to map the
   Status/Control parameter pages (5×45 + 2×45 registers). Expected homes for
   `Suction/Coil/Exhaust Temp.`, fans, switches. No writes to 2001-2090.

---

## 5. Config flow

`config_flow.py` — `ConfigFlow`, `VERSION = 1`, `MINOR_VERSION = 0`,
`unique_id = address.upper()`, abort `already_configured` on duplicates.

1. **`async_step_user`** — list discovered devices from the HA bluetooth
   integration (`bluetooth.async_discovered_service_info(hass, connectable=True)`),
   dedup by address, filter `name.lower().startswith(NAME_PREFIXES)`; show
   radio list "name — address — RSSI". If empty → offer the manual-address
   step. Also `async_step_bluetooth` (passive discovery when HA sees a
   matching advertiser → same confirm path).
2. **`async_step_manual`** — form with one field `address` (text, validated
   `xx:xx:xx:xx:xx:xx`).
3. **`async_step_test`** (shown as progress step) — connection test:
   `client.connect()` → `client.read_registers(7, 45)` → validate slave/CRC/
   length. Budget ≤ 20 s. Failure → form error
   (`cannot_connect` / `no_response` / `crc_error`) with retry. Success →
   cache the register block.
4. **`async_step_confirm`** — summary of what was read (power, mode, inlet /
   outlet / ambient temps) + one field `name` (device friendly name, default
   `"Aqua Temp heat pump"`). Create the entry:
   `data = {"address": …, "name": …}`,
   `options = {"poll_interval": 30, "temperature_unit": "C"}`.

**Options flow** (`OptionsFlowHandler`): form with
`poll_interval` (int 10..600, default from options; selector number) and
`temperature_unit` (select C/F — display-only; wire is always ×10 Celsius
**[P]**). On submit: `async_update_entry(entry, options=…)`; the options
update listener in `__init__.py` adjusts `coordinator.update_interval` and
the climate unit — no entry reload needed.

`__init__.py` flow: create client+coordinator → `await
coordinator.async_config_entry_first_refresh()` (failure →
`ConfigEntryNotReady`) → forward platforms → register the domain service
(once). Unload reverses all of it; last entry unloaded → unregister service.

---

## 6. Coordinator

```python
@dataclass
class AquaTempData:
    registers: dict[int, int]              # address -> raw signed
    power: bool | None                     # reg 8 != 0
    boost: bool | None                     # reg 11 != 0 (future use)
    mode_value: int | None                 # reg 13
    hvac_mode: HVACMode | None             # derived via MODE_VALUE_TO_NAME
    target_temp: float | None              # reg 14 /10
    min_target: float | None               # min(reg22, reg24)/10
    max_target: float | None               # max(reg23, reg25)/10
    inlet_temp: float | None               # reg 26 /10, None if sentinel
    outlet_temp: float | None              # reg 27 /10
    ambient_temp: float | None             # reg 28 /10
    fault_code: int | None                 # reg 47
    firmware: str | None                   # regs 48..51 joined
    binaries: dict[str, bool | None]       # verified binary regs only

    @classmethod
    def from_regs(cls, regs: dict[int, int]) -> "AquaTempData": ...


class AquaTempCoordinator(DataUpdateCoordinator[AquaTempData]):
    """One FC03 read per cycle. Writes are serialized against reads."""

    def __init__(self, hass, entry, client):
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=entry.options["poll_interval"]))
        self.client = client
        self._io_lock = asyncio.Lock()

    async def _async_update_data(self) -> AquaTempData:
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()          # retries inside client
            try:
                regs = await self.client.read_registers(BASE_BLOCK_START, BASE_BLOCK_COUNT)
            except AquaTempError:
                await self.client.disconnect()       # clean slate for next cycle
                raise
            return AquaTempData.from_regs(regs)

    async def async_write_register(self, address: int, value: int) -> None:
        async with self._io_lock:
            await self.client.write_registers(address, [value])
        await self.async_request_refresh()           # debounced confirmation read

    async def async_probe_block(self, block: str) -> dict: ...   # §4
```

Policies (the "don't saturate the PAC" strategy):

- **Exactly one request per poll cycle** — the 45-register FC03 read; never
  per-register reads, no in-cycle retries. Failure → `UpdateFailed` → HA's
  built-in exponential backoff till `update_interval`.
- **Writes never interleave reads or each other** (shared `asyncio.Lock`).
  After the write completes, one debounced refresh confirms state from the
  device. Repeated rapid UI changes coalesce in `async_request_refresh`.
- **Reconnect lazily** — only on the next update cycle after a failure; no
  busy reconnect loop (the unit accepts one connection; if the phone app
  holds it, HA waits politely and entities show unavailable).
- Interval bounds enforced in the options flow (min 10 s).

---

## 7. Implementation order (for the Coder)

Fenceposts: each step must pass before the next; run
`python -m custom_components.aqua_temp_ble.modbus` at step 3.

1. **Scaffold** — `manifest.json`, `const.py`, `strings.json`,
   `translations/en.json`, empty `services.yaml`, `__init__.py` with
   setup/unload skeleton. Gate: HA loads the integration, no platforms, no
   errors in log.
2. **`registers.py`** — full content per §1 (constants, RegDefs, mode map,
   calibration switches). Gate: imports clean, no HA imports (HA-agnostic
   module).
3. **`modbus.py`** — CRC/build/parse per §2.1 + `__main__` self-test asserting
   the 12 vectors of §0. Gate: `7/7` (read+query) vectors byte-exact, H1
   write vectors byte-exact, `parse_read_response` round-trips a synthetic
   95-byte response (incl. sentinel and negative temps), CRC error path
   raises.
4. **`aqua_client.py`** — `AquaTempBleClient` per §2.2 (resolution, connect,
   notify, reassembly, request cycle, write echo policy, errors). Gate:
   `ble_probe.py`-style standalone smoke script using the client connects to
   the unit and returns 45 registers (live check; if the device is not
   reachable during dev, gate on a mocked bleak client test instead —
   reassembly unit test: 95-byte response split into 20-byte chunks, into
   2 chunks, and into 1 chunk all complete correctly; garbage prefix and
   CRC-corrupt paths raise).
5. **`coordinator.py` + full `__init__.py`** — dataclass, coordinator,
   first-refresh, options listener, service registration. Gate: with a real
   device configured manually via `options`-less dev entry (or after step 6),
   `hass.data[DOMAIN][entry_id].coordinator.data` populates on schedule.
6. **`config_flow.py` + options flow + strings** — steps per §5. Gate:
   configure through the HA UI, connection test shows live values; options
   change updates the poll interval without reload.
7. **`entity.py` + `sensor.py`** — registry-driven sensors. Gate: 5 sensor
   entities (temps, fault, firmware) with correct values vs the app.
8. **`climate.py`** — read-only first (hvac_mode/target/current/min/max),
   then `async_set_hvac_mode` / `async_set_temperature` per §3.1. Gate:
   state matches device; write path performs H1 frames; after runbook step W
   is done, writes verified live.
9. **`binary_sensor.py`** — registry-driven, placeholders skipped. Gate:
   zero binary entities appear until calibration sets registers.
10. **`button.py` + final `services.yaml`** — probe surfaces per §4. Gate:
    button press produces the §4.3 log block; service returns the response
    dict; a scripted probe of block `1001` logs whatever the unit answers
    without crashing (silence fallback).
11. **Docs** — `docs/CALIBRATION.md` (the runbook §4.4 verbatim, plus the
    logcat capture commands and the `"66"` debug-password trick from FINDINGS
    §10 as an extra correlation aid), `README.md` (install, pairing note:
    hold panel button until display shows `bLE`, single-connection warning,
    options). Gate: Reviewer pass.

Parallel-safe: steps 1-4 are independent of HA runtime (only step 5+ needs a
running HA).

---

## 8. Risk register (condensed)

| Risk | Impact | Mitigation |
|---|---|---|
| Phone app holds the single BLE connection | HA cannot connect | Documented; coordinator waits; entities go unavailable; user closes app |
| Write layout H1 vs H2 | write ignored (H1) — safe; or clobber (H2) | H1 default (safe failure); runbook step W pins it via app's own logcat frames |
| Mode value mapping wrong | wrong hvac label / wrong mode on switch | Isolated in `MODE_VALUE_TO_NAME`; runbook step M before trusting mode writes |
| Register data little-endian | garbage temps | Probe detects (`0x00F3` vs `0xF300`); one constant flips decode |
| Response fragmentation unknown | hangs | expected-length primary + 0.6 s silence fallback + 8 s request timeout + `DEBUG_RAW` chunk logging |
| Write echo absent | spurious write errors | `WRITE_ECHO_EXPECTED` switch; echo wait bounded at 3 s |
| MAC changes across reboots | cannot reconnect | Name-based re-resolution + warning to re-configure; manual-address path in flow |
| Polling starves/saturates PAC | device busy | Exactly one 45-reg read per cycle; min interval 10 s; no retry storms |
| Label map ~80% | wrong data under a name | Unverified registers = entity not created (address=None); probe + runbook to enable |

Out of scope (phase 2+, listed so the Coder does not improvise them): boost
preset (reg 11), fan/high-low control, high-block 1001-2090 entities,
time-sync write (reg 1267), fault-code translation table, cloud/MQTT path
(unit has no WiFi).