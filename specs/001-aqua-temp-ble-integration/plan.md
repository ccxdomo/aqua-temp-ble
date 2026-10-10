# Plan 001 — Technical architecture of the Aqua Temp BLE integration

Status: **IMPLEMENTED** — retroactive plan describing what was built.
Layering, interfaces and patterns; the register map itself lives in
`data-model.md`, frame bytes in `contracts/`.

---

## 1. Layering

```
┌────────────────────────────────────────────────────────────┐
│ HA platforms                                               │
│   climate.py   sensor.py   binary_sensor.py   button.py   │
│   (registry-driven entities, display-unit conversion)      │
├────────────────────────────────────────────────────────────┤
│ coordinator.py   AquaTempCoordinator (DataUpdateCoordinator)│
│   one FC03 read/cycle · write serialization · probe ·      │
│   AquaTempData.from_regs (raw regs → typed snapshot)       │
├────────────────────────────────────────────────────────────┤
│ aqua_client.py   AquaTempBleClient                          │
│   bleak connection · single-flight request/response ·      │
│   notification reassembly · lazy reconnect · name fallback │
├────────────────────────────────────────────────────────────┤
│ modbus.py        pure protocol: CRC16-Modbus, frame        │
│                  build/parse, expected-length. NO I/O.     │
├────────────────────────────────────────────────────────────┤
│ registers.py / const.py   stdlib-only constants: the       │
│   register map, value maps, calibration switches, GATT UUIDs│
└────────────────────────────────────────────────────────────┘
config_flow.py   discovery → manual MAC → live test → confirm
__init__.py      setup/unload, options listener, probe service
entity.py        shared base: DeviceInfo + unique_id
```

Key property: the bottom two layers (`modbus.py`, `registers.py`,
`const.py`) are importable with plain Python — the unit tests and
`tools/smoke_client.py` run without Home Assistant installed.

## 2. Module inventory

| File | Lines (≈) | Role |
|---|---|---|
| `custom_components/aqua_temp_ble/manifest.json` | 14 | domain, `bleak>=0.22.0`, depends on `bluetooth` |
| `const.py` | 30 | stdlib-only constants: UUIDs ff01/ff02, name prefixes, timeouts, poll defaults |
| `registers.py` | 135 | **single calibration surface**: register map, `[P]/[A]/[V]` tags, mode map, sentinel, calibration switches, `RegDef` registries |
| `modbus.py` | 307 | CRC16-Modbus, `build_read_holding_registers`, `build_query_block`, `build_write_registers` (H1), response/echo parsers, expected-length, built-in self-test |
| `aqua_client.py` | 424 | bleak transport: connect (2 attempts), ff02 notify subscription, single-flight `_request`, 3-rule reassembly, stale-chunk dropping, write-echo policy, MAC-changed name fallback |
| `coordinator.py` | 338 | `AquaTempData` snapshot + `DataUpdateCoordinator`: one read/cycle, `asyncio.Lock` write serialization, debounced confirm refresh, `async_probe_block` + probe logging |
| `config_flow.py` | ~250 | discovery (bluetooth cache) → manual MAC → progress-step live test (20 s) → confirm; options flow (poll 10–600 s, unit C/F) |
| `entity.py` | 37 | `CoordinatorEntity` base: unique_id `ADDR_key`, DeviceInfo (PHNIX, sw_version when known) |
| `climate.py` | 161 | on/off + mode + setpoint, °F conversion at the boundary, power-before-mode write order |
| `sensor.py` | 83 | registry-driven sensors (skip `address=None`), sentinel-aware |
| `binary_sensor.py` | 66 | registry-driven; all three currently `address=None` → not created |
| `button.py` | 40 | `Probe registers` diagnostic button |
| `__init__.py` | ~170 | setup/unload, options listener (interval + immediate re-render), firmware→device-registry listener, `probe_registers` service |
| `tests/test_modbus.py` | 220 | 11 tests: all recovered frames byte-exact, parse/echo/exception paths |
| `tests/test_reassembly.py` | 382 | 15 tests: mocked-bleak reassembly matrix |
| `tools/smoke_client.py` | ~70 | standalone read-only probe (needs only bleak) |

## 3. Runtime flows

### 3.1 Setup
1. `async_setup_entry`: client + coordinator → `async_config_entry_first_refresh()`
   (failure → `ConfigEntryNotReady`, HA retries with backoff — entities never
   exist without a first successful read).
2. Firmware listener registered (pushes `sw_version` to the device registry
   at the first cycle that reports it — see §5, Fix-3).
3. Forward platforms; options update listener; domain service registered once.

### 3.2 Poll cycle
`_async_update_data` takes the I/O lock → lazy reconnect if needed → **one**
`read_registers(7, 45)` → `AquaTempData.from_regs` → listeners. Read failure
disconnects the client (clean slate) and raises `UpdateFailed`.

### 3.3 Write path (climate entity)
`async_write_register(addr, value)`: I/O lock → FC10 write (H1) → optional
echo wait → `async_request_refresh()` (debounced device-confirmed state).
HVAC on: power (reg 8 = 1) **before** mode — a mode write while powered off
may be ignored by the controller.

### 3.4 Options change
Update listener sets `coordinator.update_interval` and calls
`coordinator.async_update_listeners()` — entities re-render immediately, so
the °C/°F switch is visible at once (the unit is a property read at
state-write time). No reload, no extra device I/O.

## 4. Interfaces (internal contracts)

- `modbus.py` ← pure functions on `bytes`; raises `ModbusError` subtypes.
- `aqua_client.py` ← `read_registers(start, count) -> {addr: int}`,
  `write_registers(start, values)`, `query_block(start, count) -> bytes`;
  raises `AquaTempError` subtypes. `_write_frame` is the transport seam
  (overridden in tests).
- `coordinator.py` ← `AquaTempData` dataclass (typed snapshot: power, mode,
  target, min/max, three temperatures, fault, firmware, binaries) — platforms
  only ever read this, never raw registers (except the sensor raw path).
- Platforms ← entity constructors take `(coordinator, entry, regdef)`;
  entity lists come from `registers.SENSORS` / `registers.BINARIES`.

## 5. Patterns applied

- **Single calibration surface** — every mapping decision lives in
  `registers.py`; correcting a label = editing one entry + restarting HA.
  Unknown registers are `address=None` placeholders → the matching entity is
  **not created** ("no unverified data under a label").
- **Registry-driven entities** — sensors/binary sensors are generated from
  `RegDef` tuples, not hand-written.
- **Single-flight request/response** — at most one Modbus frame in flight;
  concurrent callers fail fast instead of interleaving on the notify pipe.
- **3-rule reassembly** (see `contracts/ble-response.md`): expected length
  first, silence-gap second, request deadline third (accepts a CRC-valid
  buffer if one arrived).
- **No optimistic state** — every write is confirmed by a debounced
  re-read; the UI shows device truth.
- **Display-unit conversion at the boundary** — wire is always ×10 °C;
  °F is a property-level concern of the climate entity.
- **Post-review fixes (this repo's history)**:
  1. P2 — the `@callback` marker on `async_get_options_flow` was removed
     (HA calls that getter synchronously; the marker was a no-op).
  2. P3 — options changes now re-render entities immediately
     (`async_update_listeners()` in the options listener).
  3. P3 — a coordinator listener publishes the firmware string to the
     device registry the moment it is first reported (and after unit
     firmware updates), because HA merges `DeviceInfo` only at entity-add
     time.

## 6. Config flow state machine

```
user (discovered list) ──┐
manual (MAC) ────────────┤→ test (progress, live read, 20 s budget)
bluetooth (discovery) ───┘        │ ok                │ fail
                                 ▼                   ▼
                          confirm (summary + name)   back to origin step
```
- Errors mapped: connect → `cannot_connect`, no answer → `no_response`,
  CRC → `crc_error`, bad MAC → `invalid_address`.
- Entry data: `{address, name}`; options seeded: poll 30 s, unit C.
- Unique id: `address.upper()`.

## 7. Testing strategy

- **Vectors over mocks**: the 7 recovered APK frames + 6 H1 write vectors are
  asserted byte-exact (`modbus.py` self-test, `test_modbus.py`) — a regression
  in CRC/framing cannot pass silently.
- **Reassembly matrix with a fake bleak transport**: chunk sizes 20/2/1,
  garbage prefix, CRC corruption, silence completion, deadline acceptance,
  write-echo (incl. 5-byte Modbus exception echo), single-flight guard.
- **No HA in tests**: HA-dependent modules are covered by `py_compile` +
  review; protocol logic is fully covered outside HA.
- Gate: `py_compile` all files; `modbus.py` self-test; both test files
  (11 + 15 tests, 37 self-test checks — all green).

## 8. Implementation order (as actually followed)

1. `registers.py`/`const.py` constants → 2. `modbus.py` + self-test →
3. `aqua_client.py` + reassembly tests → 4. `coordinator.py` →
5. `entity.py` + platforms → 6. `__init__.py` service →
7. `config_flow.py` + strings/translations → 8. `tools/smoke_client.py` →
9. `README.md` + `docs/CALIBRATION.md` + `hacs.json` → 10. review fixes
(this plan, data-model, contracts, quickstart, tasks).