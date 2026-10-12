# Tasks 001 — Aqua Temp BLE integration

Status legend: ☑ done (implemented and reviewed) · ☐ open.
This is a **retroactive** task list: the work marked done was completed
before this spec kit was written (the integration was built and reviewed
first; these specs document it).

---

## 1. Reverse engineering (done, `FINDINGS.md`, `blutter/`)

- ☑ APK/XAPK extraction (Dart 3.12.2 AOT, obfuscated, arm64)
- ☑ blutter run: object pool `pp.txt` (~29k objects), class structure
- ☑ GATT plumbing recovered from the Java plugin (ff01 write, ff02 notify)
- ☑ BLE scan filters recovered (BlueNRG-, HIFLYING, PROV_)
- ☑ 7 Modbus frames recovered from the object pool, CRC-verified byte-exact
- ☑ Register label list (45 names) recovered → base block map ~80 %
- ☑ Temperature scaling (×10) and sentinel (−999.0 °C) established
- ☑ Limitation documented: function bodies not decompilable
  (`dedup_instructions`); remaining map requires one live session

## 2. Protocol layer (done, `modbus.py` + tests)

- ☑ CRC16-Modbus (little-endian wire order) + reference vectors
- ☑ Frame builders: FC03 read, FC10 write (H1), 8-byte block queries
- ☑ Parsers: FC03 response (signed, sentinel, endianness switch), write
  echo, Modbus exception; expected-length derivation
- ☑ Built-in self-test — 37 checks, gate for all recovered vectors
- ☑ `tests/test_modbus.py` — 11 tests (all frames byte-exact)

## 3. BLE transport (done, `aqua_client.py` + tests)

- ☑ bleak connect (2 attempts), ff02 subscribe, ff01 write-mode detection
- ☑ Single-flight request/response cycle (one pending future)
- ☑ 3-rule reassembly: expected length → silence gap (0.6 s) → deadline
  (8 s, accepts CRC-valid buffer)
- ☑ Stale-chunk / garbage-prefix / function-code filtering, buffer cap
- ☑ Write-echo policy (expected by default, switchable to fire-and-forget)
- ☑ Device resolution: HA cache → bleak by address → name-prefix scan;
  warn on MAC change, never rewrite config
- ☑ `tests/test_reassembly.py` — 15 tests (mocked bleak, chunk matrix)

## 4. Coordinator (done, `coordinator.py`)

- ☑ One 45-register FC03 read per poll cycle (default 30 s, 10–600 s)
- ☑ Writes serialized against reads (single `asyncio.Lock`) + debounced
  confirmation refresh (no optimistic state)
- ☑ `AquaTempData.from_regs` typed snapshot (power/mode/target/bounds/
  temperatures/fault/firmware/binaries)
- ☑ Probe path: base block + 6 experimental high blocks, deterministic log
  format, service response dict

## 5. Platforms & entities (done)

- ☑ Climate: on/off, heat/cool/auto, setpoint (0.5 steps, clamped),
  power-before-mode write order, °F conversion at the entity boundary
- ☑ Sensors (registry-driven): inlet/outlet/ambient temperatures (÷10,
  sentinel→unknown), fault code, firmware (diagnostics)
- ☑ Binary sensors: compressor/pump/valve — **not created** while
  `address=None` (no unverified data under a label)
- ☑ Probe button (diagnostic)
- ☑ DeviceInfo + unique ids (`ADDR_upper_key`), `sw_version` from firmware

## 6. Setup, config & options (done)

- ☑ Config flow: discovery (bluetooth cache) → manual MAC → live 20 s test
  progress step → confirm with read summary; unique_id = upper(address)
- ☑ Options flow: poll interval + display unit; both apply without reload
- ☑ Options listener: interval update + **immediate entity re-render**
  (`async_update_listeners()`) — unit switch visible at once
- ☑ Firmware→device-registry listener: `sw_version` published at the first
  cycle that reports it (and after unit firmware updates)
- ☑ `probe_registers` service: `SupportsResponse.OPTIONAL`, per-device or
  all devices, per-address error isolation
- ☑ Translations `en`/`fr`, strings.json, `hacs.json`, manifest

## 7. Tools, docs, review (done)

- ☑ `tools/smoke_client.py` — standalone read-only probe (bleak only)
- ☑ `README.md`, `docs/CALIBRATION.md` runbook
- ☑ Review verdict: VALIDATED — three non-blocking findings fixed:
  1. ☑ P2 — removed redundant `@callback` on `async_get_options_flow`
     (HA calls it synchronously; marker was a no-op)
  2. ☑ P3 — unit (°C/°F) change now applies immediately
  3. ☑ P3 — firmware version reaches the device registry from the first
     reporting cycle (coordinator listener)
- ☑ Full test gate re-run after fixes: py_compile clean, self-test 37/37,
  test_modbus 11/11, test_reassembly 15/15
- ☑ Spec kit (this directory) — retroactive: spec, plan, research,
  data-model, contracts/, quickstart, tasks

## 8. Open — live calibration (one session with the unit)

> Everything below flips one constant in `registers.py` +
restart HA (`docs/CALIBRATION.md`).

- ☐ C-1 Live base-block read: transport + fragmentation + CRC on the real
  unit (probe button or smoke_client)
- ☐ C-2 Confirm register-data endianness (`REG_DATA_LITTLE_ENDIAN`,
  default big-endian)
- ☐ C-3 Confirm mode value map (`MODE_VALUE_TO_NAME`, assumed
  {0: cool, 1: heat, 2: auto})
- ☐ C-4 Pin binary registers in 29..46: compressor, circulate pump,
  4-way valve (+ polarity `BINARY_ACTIVE_WHEN_NONZERO`) → entities appear
- ☐ C-5 Confirm write layout H1 vs H2 (logcat capture of the vendor app —
  it logs outgoing frames in cleartext)
- ☐ C-6 Confirm FC10 echo presence (`WRITE_ECHO_EXPECTED`)
- ☐ C-7 Confirm whether the app mirrors setpoint writes to regs 19/20/21
  (`WRITE_MODE_SETPOINT_TOO`)
- ☐ C-8 Confirm firmware join semantics for regs 48..51
- ☐ C-9 Confirm fault-code semantics (0 = no fault?)
- ☐ C-10 (optional, phase 2) Map high blocks 1001–2090 (Status/Control
  parameter pages) — read-only via probe

## 9. Deferred / out of scope

- ☐ Boost mode control (reg 11 is read-only for now)
- ☐ Timers/schedules from the vendor app
- ☐ Any writes to blocks 1001–2090 (never — control parameters page)