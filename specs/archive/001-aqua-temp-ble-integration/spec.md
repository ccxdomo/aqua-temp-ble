# Spec 001 — Aqua Temp BLE Home Assistant integration

Status: **IMPLEMENTED (pre-calibration)** — retroactive specification, written
after the code was built and reviewed. It describes what exists today.

Scope: a Home Assistant custom integration (`custom_components/aqua_temp_ble`)
for PHNIX pool heat pumps sold under the *Aqua Temp* Android app
(`com.phinx.poolheater4`), driven directly over Bluetooth Low Energy — no
vendor cloud, no account, no WiFi (the unit has none).

Confidence legend used throughout this spec kit (same as the code):
- **[P] PROVEN** — recovered from the vendor APK and CRC-verified byte-exact.
- **[A] ASSUMED** — reasonable default, one constant to flip after
  calibration.
- **[V] TO-VERIFY** — cannot be known without one live session with the unit.

---

## 1. Background

The vendor app is a Flutter app whose BLE traffic is Modbus RTU framed.
Static analysis of the APK (see `FINDINGS.md`, `research.md` in this spec
kit) recovered the full transport, the frame format, the scaling rules and
~80 % of the base register map. The owner's unit has no WiFi: BLE is the
only operating channel, so a local HA integration is the only way to keep
the pump out of the vendor cloud.

## 2. User stories

### US-1 — Read water temperatures
As a pool owner, I want the **inlet** and **outlet** water temperatures in
Home Assistant so that I can monitor the heat pump without opening the
vendor app.
→ Sensors `Inlet water temperature` (reg 26), `Outlet water temperature`
(reg 27), scaled ÷10, sentinel `-9990` mapped to `unknown` (sensor absent).
**[P]**

### US-2 — Read ambient temperature
As a pool owner, I want the **ambient temperature** around the unit.
→ Sensor `Ambient temperature` (reg 28). **[P]**

### US-3 — Set the target temperature
As a pool owner, I want to set the water setpoint from HA in 0.5 ° steps,
clamped to the bounds the device reports.
→ Climate entity `async_set_temperature` writes reg 14 (×10, Celsius on the
wire). Write is confirmed by a debounced re-read — no optimistic state.
Address **[P]**, write layout **[A]** (H1, see UNVERIFIED).

### US-4 — Power on / off
As a pool owner, I want to turn the heat pump on and off from HA.
→ Climate entity `async_set_hvac_mode(OFF/…)`, writes reg 8 (`0`/`1`).
Address **[P]**, write layout **[A]**.

### US-5 — Select operating mode
As a pool owner, I want to switch **heat / cool / auto**.
→ Climate entity writes reg 13 with the value mapped from
`registers.MODE_VALUE_TO_NAME`. Address **[P]**, value map **[A]** (see
UNVERIFIED).

### US-6 — Local only
As the owner, I want **zero** cloud dependency: no vendor account, no
credentials, no data leaving the LAN.
→ The integration speaks BLE only; no outbound traffic of any kind.

### US-7 — Diagnostics
As a pool owner, I want the **fault code** and **firmware version** visible
as diagnostic entities, and the firmware string in the device registry.
→ Sensor `Fault code` (reg 47), sensor `Firmware` (regs 48–51 joined,
**[V]** semantics), `sw_version` pushed to the device registry as soon as
the firmware is first reported.

### US-8 — Calibrate the unknowns
As a power user, I want a **register probe** (button + service) that dumps
every raw register so the remaining `[V]`/`[A]` items can be pinned in one
live session, by editing `registers.py` only.
→ Button `Probe registers` (base block) and service
`aqua_temp_ble.probe_registers` (base + 6 experimental high blocks),
response returned to the caller, human-readable log in
`home-assistant.log`.

### US-9 — Display unit and poll rate
As a user, I want to choose the **display unit** (°C/°F) and the **poll
interval** (10–600 s) without reloading the integration, and I want a unit
change to apply **immediately**.
→ Options flow; the unit is converted at the entity boundary (wire stays
×10 Celsius) and entities re-render as soon as options are saved.

### US-10 — No unverified data under a label
As the owner, I want the integration to **refuse to display** any value
whose register is not confirmed.
→ Entities whose register is `None` are simply **not created**; binary
sensors (compressor, circulate pump, 4-way valve) stay absent until their
registers are pinned in `registers.py`.

## 3. Requirements

### Functional
- FR-1 Poll exactly **one** 45-register FC03 read per cycle (default 30 s,
  configurable 10–600 s). No per-register reads, no retry storms.
- FR-2 Writes go only to registers **8, 13, 14** in the base block.
  Blocks 1001–2090 are **never written**.
- FR-3 Writes are serialized against reads (single `asyncio.Lock`) and
  confirmed by one debounced refresh — state shown is device-confirmed.
- FR-4 Fragmented GATT notifications are reassembled (expected length →
  silence gap → request deadline), CRC-validated before use.
- FR-5 Config flow: discovery from the HA Bluetooth cache → manual MAC
  fallback → live 20 s connection test showing what was read → confirm.
  Unique id = upper-cased BLE address.
- FR-6 Options apply without entry reload; a unit change re-renders all
  entities immediately.
- FR-7 Firmware string reaches the device registry `sw_version` at the first
  cycle that reports it (and after a unit firmware update).
- FR-8 Single BLE connection reality: lazy reconnect, polite wait when the
  vendor app holds the connection; name-prefix fallback when the MAC
  changed (warn, never rewrite the config).

### Non-functional
- NFR-1 Min supported Home Assistant: **2024.12**; `bleak>=0.22.0`
  (provided by HA; listed for standalone tools).
- NFR-2 `modbus.py`, `registers.py`, `const.py` are stdlib-only so tests
  and standalone tools run without HA installed.
- NFR-3 All recovered protocol frames are asserted byte-exact in tests
  (regression vectors).
- NFR-4 English-only durable artifacts; no secrets in code.

## 4. Acceptance criteria

- AC-1 `python3 custom_components/aqua_temp_ble/modbus.py` self-test passes:
  all **7 recovered frames CRC-verified byte-exact** and the 6 write vectors.
- AC-2 `python3 tests/test_modbus.py` — 11/11 pass; includes every frame of
  `docs/CALIBRATION.md`'s verified-frames appendix.
- AC-3 `python3 tests/test_reassembly.py` — 15/15 pass (mocked bleak:
  20-byte/2-chunk/1-chunk reassembly, garbage prefix, CRC corruption,
  timeouts, silence completion, deadline acceptance, echo paths,
  single-flight guard).
- AC-4 `python3 -m py_compile` on every Python file — clean.
- AC-5 With a live unit and the vendor app closed: config flow test reads
  the base block and shows power/mode/temperatures before entry creation.
- AC-6 After setup, the climate entity reflects the device (power, mode,
  setpoint, min/max bounds), and sensors show temperatures ÷10 with
  sentinel → `unknown`.
- AC-7 Changing the display unit in options re-renders entities
  **immediately** (no wait for the next poll).
- AC-8 The firmware string appears in the device registry from the first
  cycle that reports it.
- AC-9 No entity exists for any register with `address=None`.

## 5. UNVERIFIED — explicit list of not-yet-confirmed items

> **Everything in this section is UNVERIFIED.** None of it is displayed as
> fact anywhere in the UI; each item is isolated behind one switch or
> constant in `registers.py` (`docs/CALIBRATION.md` closes them in one live
> session).

| # | Item | Current default | Where to flip |
|---|---|---|---|
| U-1 | Mode value map (reg 13) | `{0: cool, 1: heat, 2: auto}` **[A]** | `MODE_VALUE_TO_NAME` |
| U-2 | Write layout H1 (16-bit) vs H2 (32-bit) | H1 **[A]** — safe: an under-length frame can at worst be ignored, never clobber a neighbour | `modbus.build_write_registers` |
| U-3 | FC10 write echo on ff02 | expected **[A]** | `WRITE_ECHO_EXPECTED` |
| U-4 | Per-mode setpoint mirroring (regs 19/20/21) on setpoint change | off **[A]** | `WRITE_MODE_SETPOINT_TOO` |
| U-5 | Firmware join semantics (regs 48–51) | `"."`-joined decimal **[V]** | `coordinator.AquaTempData.from_regs` |
| U-6 | Fault code semantics (reg 47) | `0` = no fault **[A]**; code table unknown | none (diagnostic display only) |
| U-7 | Binary registers (compressor, circulate pump, 4-way valve) | unknown, somewhere in 29–46 **[V]** | `REG_COMPRESSOR`, `REG_CIRCULATE_PUMP`, `REG_FOUR_WAY_VALVE`, `BINARY_ACTIVE_WHEN_NONZERO` |
| U-8 | Register data endianness | big-endian (standard Modbus) **[A]** | `REG_DATA_LITTLE_ENDIAN` |
| U-9 | High-block query response shapes (1001–2090) | unknown **[V]** — probe logs raw hex, never crashes | probe tool only (read-only) |
| U-10 | ff03 GATT characteristic role | unknown **[V]** — not used | none |
| U-11 | Fragmentation behaviour of real notifications | design covers 1–95-byte frames **[V]** | `const.py` timeouts |

## 6. Out of scope

- Vendor cloud (REST/MQTT) — the owner's unit cannot reach it.
- Timer/schedule features of the app; boost mode (reg 11 read-only).
- Writing high blocks 1001–2090 (control parameters page).
- Multi-unit coordination beyond what HA natively does (one entry per unit
  is supported).