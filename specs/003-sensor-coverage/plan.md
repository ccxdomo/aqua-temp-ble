# Plan 003 — Design of the sensor-coverage extension

Status: **PLANNED**. This file describes the target design; the Coder
implements in the order of `tasks.md`. Normative behaviour lives in the
contracts: `entity-contract.md` (entity table), `decode-contract.md`
(raw→state rules), `calibration-contract.md` (pinning procedure).

Confidence legend: **[P]** proven (source-tagged as in `spec.md`) ·
**[A]** assumed · **[V]** to-verify · **[D]** decision.

---

## 1. What changes, what does not

| Module | Change | Rationale |
|---|---|---|
| `registers.py` | sentinel set + plausibility constants + decode helpers + write allowlist + new RegDefs + family tags + import-time integrity | single calibration surface (spec 001 §5); stdlib-only (NFR-1) |
| `coordinator.py` | `_scaled` → `registers.decode_temp`; binaries → per-RegDef `decode_flag`; `async_write_register` → `ensure_writable` guard; implausible-value warning (once per address) | decode logic leaves HA-dependent code; security invariant enforced at the single write funnel |
| `sensor.py` | temperature path via `decode_temp` (replaces the local sentinel check); no other change | one decode implementation everywhere |
| `binary_sensor.py` | new `BINARY_NAMES`; DIAGNOSTIC category mapping from `RegDef.diagnostic` (today only `sensor.py` maps it) | S-family visibility |
| `tests/test_registers.py` | NEW — stdlib-only tests: sentinel set, plausibility, polarity, allowlist, integrity, RegDef table shape | NFR-1 |
| `docs/CALIBRATION.md`, `README.md` | panel worksheet, pinning procedure, entity table, security note | AC-10 |
| `climate.py`, `modbus.py`, `aqua_client.py`, `config_flow.py`, `entity.py`, `__init__.py`, translations | **no change** (NFR-4) | regression safety |

## 2. Entity design

All entities are registry-driven from `registers.SENSORS` /
`registers.BINARIES` (existing pattern); unique id = `{ADDRESS_UPPER}_{key}`
(existing pattern); device identifiers unchanged (`(aqua_temp_ble, address)`).

### 2.1 Sensors (platform `sensor`)

| key | Name | Family | Register | device_class | unit | state_class | category | Created today? |
|---|---|---|---|---|---|---|---|---|
| `inlet_water_temp` | Inlet water temperature | T02 | 26 **[P]** | temperature | °C | measurement | — | yes (existing) |
| `outlet_water_temp` | Outlet water temperature | T03 | 27 **[P]** | temperature | °C | measurement | — | yes (existing) |
| `ambient_temp` | Ambient temperature | T05 | 28 **[P]** | temperature | °C | measurement | — | yes (existing) |
| `suction_temp` | Suction temperature | T01 | None **[V]** | temperature | °C | measurement | — | **no** — after pinning |
| `coil_temp` | Coil temperature | T04 | None **[V]** | temperature | °C | measurement | — | **no** — after pinning |
| `exhaust_temp` | Exhaust temperature | T06 | None **[V]** | temperature | °C | measurement | — | **no** — after pinning |
| `fault_code` | Fault code | — | 47 **[P]** | — | — | — | diagnostic | yes (existing) |
| `firmware` | Firmware | — | 48–51 **[P/V]** | — | — | — | diagnostic | yes (existing) |

- The family tag (`T01…T06`) is **documentation + probe annotation only**;
  it never changes entity behaviour.
- New temperature sensors get no icon (device class provides one), like
  the existing three.
- Names mirror the panel labels ("Suction Temp." → "Suction temperature")
  for an easy panel↔HA cross-check.

### 2.2 Binary sensors (platform `binary_sensor`)

| key | Name | Family | Register | category | Icon | Created today? |
|---|---|---|---|---|---|---|
| `compressor` | Compressor | O01 | None **[V]** | — | `mdi:engine` | no (existing placeholder, label panel-confirmed) |
| `circulate_pump` | Circulate pump | O02 | None **[V]** | — | `mdi:pump` | no (existing placeholder, label panel-confirmed) |
| `high_fan` | High fan | O03 | None **[V]** | — | `mdi:fan` | **no** — new placeholder |
| `four_way_valve` | 4-way valve | O04 | None **[V]** | — | `mdi:swap-horizontal` | no (existing placeholder, label panel-confirmed) |
| `hp_switch` | HP switch | S01 | None **[V]** | diagnostic | `mdi:alert-circle` | **no** — new placeholder |
| `lp_switch` | LP switch | S02 | None **[V]** | diagnostic | `mdi:alert-circle` | **no** — new placeholder |
| `flow_switch` | Flow switch | S03 | None **[V]** | diagnostic | `mdi:water` | **no** — new placeholder |

- **No device_class** on any binary sensor until polarity semantics are
  proven live [D] — a `safety` device class would assert "on = unsafe",
  which is exactly the unproven part (spec §7 E-4).
- Existing keys/icons are **unchanged** so a future calibration (and any
  user automations written after it) sees stable ids.

### 2.3 Not entities [D]

- **H / R / P / D families**: no entities of any platform
  (spec §6 policy table). Diagnostics for these families remain available
  through the read-only probe once their blocks are mapped.
- Climate: unchanged (one entity, `climate` key, spec 001).

## 3. Decode pipeline

One implementation, three consumers (coordinator snapshot, sensor
platform, binary platform):

```
raw int16 (signed, big-endian unless REG_DATA_LITTLE_ENDIAN [A])
   │
   ├─ temperature (any temperature-class RegDef / coordinator _scaled)
   │     registers.decode_temp(raw)
   │       raw is None                → None            (register absent)
   │       raw ∈ SENTINEL_RAWS        → None            (sensor absent)
   │       raw outside [-450, +1500]  → None + warn-once (implausible)
   │       else                       → round(raw / 10, 1)
   │
   └─ binary flag (RegDef.is_binary)
         registers.decode_flag(raw, active_when_nonzero)
           raw is None  → None
           else         → (raw != 0) if active_when_nonzero else (raw == 0)
```

- Sentinel set: `{-9990, 32765}` — see `decode-contract.md` §2 for the
  evidence and [V] items.
- Plausibility bounds: `TEMP_PLAUSIBLE_MIN_RAW = -450`,
  `TEMP_PLAUSIBLE_MAX_RAW = 1500` [D] — spec §3 FR-2.
- The **warning** for implausible values is logged by the consumer that
  has a logger (coordinator/sensor), once per address per HA session —
  the stdlib helpers stay pure (no logging inside `registers.py`).
- Non-temperature sensors (fault code, firmware join) keep their existing
  paths; only the sentinel comparison in the firmware join switches to
  `is_sentinel` (same semantics, both conventions).

## 4. Availability, refresh, unknown

- **Refresh**: unchanged coordinator cycle — one 45-register FC03 read per
  poll (default 30 s, options 10–600 s). New entities ride the same
  snapshot; no extra BLE traffic is introduced by this spec [D].
- **Availability**: `available` comes from `CoordinatorEntity` (unavailable
  while the coordinator update fails — vendor app holding the connection,
  out of range). Unchanged.
- **`unknown` (state)**: register absent from the snapshot, sentinel, or
  implausible value (§3). Three honest reasons, one state.
- **Creation gating** (FR-7): entity exists ⇔ RegDef address pinned ∧
  address ∈ [7, 51]. Out-of-block pin → warning + not created. This is
  what keeps "no unconfirmed data under a label" true even if a future
  contributor pins a high-block address by mistake.

## 5. Registries structure (`registers.py`)

```python
# --- sentinels & plausibility (decode-contract.md) ---
SENTINEL_RAWS: frozenset[int] = frozenset({-9990, 32765})
SENTINEL_RAW = -9990                       # legacy alias (FR-14), member of the set
TEMP_PLAUSIBLE_MIN_RAW = -450              # -45.0 degC
TEMP_PLAUSIBLE_MAX_RAW = 1500              # +150.0 degC
def is_sentinel(raw): ...
def is_plausible_temp(raw): ...
def decode_temp(raw) -> float | None: ...
def decode_flag(raw, active_when_nonzero) -> bool | None: ...

# --- write security (spec §6) ---
ALLOWED_WRITE_BASE = frozenset({REG_POWER, REG_MODE, REG_SET_CURRENT_TEMP})
def ensure_writable(address) -> None:      # + {19,20,21} iff WRITE_MODE_SETPOINT_TOO

# --- entity registries (existing RegDef, two fields appended) ---
@dataclass(frozen=True)
class RegDef:
    ...                                    # existing fields, unchanged order
    active_when_nonzero: bool | None = None  # None -> BINARY_ACTIVE_WHEN_NONZERO
    family: str | None = None                # "T01", "S03", ... (doc/probe only)

SENSORS = (..., RegDef("suction_temp", None, "SuctionTemp", TEMP_SCALE, True,
                       "temperature", "°C", "measurement", family="T01"), ...)
BINARIES = (..., RegDef("high_fan", None, "High fan", is_binary=True,
                        icon="mdi:fan", family="O03"), ...)

# --- import-time integrity (FR-11) ---
def _check_registries() -> None: ...       # duplicate addr/key -> ValueError
_check_registries()
```

- `KNOWN_LABELS` gains the new entries automatically when addresses get
  pinned (the existing `if _reg is not None` loop) — probe annotation for
  free; family codes go into the label text (e.g. `"SuctionTemp [V] (T01)"`).
- A new documentation-only `FAMILIES` mapping (family code → observed
  label/values/status, mirroring `data-model.md`) may be added as plain
  comments or a dict — it is **not** used by any platform and must not
  grow logic [D].

## 6. Coordinator wiring

- `_scaled(regs, address)` → `registers.decode_temp(regs.get(address))`
  (same None-contract; adds sentinel-set + plausibility).
- The `binaries` loop → `registers.decode_flag(raw, d.active_when_nonzero
  if d.active_when_nonzero is not None else registers.BINARY_ACTIVE_WHEN_NONZERO)`.
- `async_write_register(address, value)` → first statement:
  `registers.ensure_writable(address)` [raises before any I/O].
- Implausible-value warning: a module-level `_logged_implausible: set[int]`
  keyed by address (pattern of `_logged_unknown_modes`); logged where the
  decode result is consumed (coordinator + sensor native_value path share
  the set — coordinator-owned, sensor reads `data` fields so the
  coordinator path suffices; see `tasks.md` T-9 note).

## 7. Platform wiring

- `sensor.py`: for `device_class == "temperature"` RegDefs, `native_value`
  returns `registers.decode_temp(raw)` (replaces the local
  `SENTINEL_RAW`/scale logic — same result for good values, new behaviour
  for the 32765 sentinel + implausible values). `fault_code`/`firmware`
  paths unchanged except `is_sentinel` in the firmware join.
- `binary_sensor.py`: `BINARY_NAMES` gains the four new keys; `__init__`
  maps `regdef.diagnostic` → `EntityCategory.DIAGNOSTIC` (same as
  `sensor.py` does today); `is_on` unchanged (reads `data.binaries`).

## 8. Testing strategy

- **New** `tests/test_registers.py` (stdlib-only, style of
  `test_modbus.py`): sentinel set membership; `decode_temp` truth table
  (AC-1/AC-2 vectors: −9990, 32765, 170, 195, −100, 2000, −5000, None);
  `decode_flag` truth table incl. inversion (AC-8); `ensure_writable`
  matrix (AC-6); integrity check raising on duplicates (AC-9);
  shipped-registry assertions: new keys present, all new addresses None,
  families as per `data-model.md`, existing keys/icons unchanged (AC-3).
- **Regression**: the four existing test files untouched-green; the
  walkthrough test keeps passing because `SENTINEL_RAW` still exists
  (FR-14) and entity behaviour for valid values is unchanged.
- **Live validation**: `quickstart.md` (calibration session scenarios).
- **Review gate**: AC-12 checklist (no H/R/P surface, climate untouched).

## 9. Implementation order

Dependency-ordered in `tasks.md`: constants/helpers & registries
(`registers.py`) → coordinator wiring → platform wiring → tests → docs →
gates → review. The Coder never touches `climate.py`/`modbus.py`/
`aqua_client.py`/`config_flow.py`.