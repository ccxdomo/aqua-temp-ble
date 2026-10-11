# Contract — Decode rules (003)

Status: **PLANNED** — normative for the raw→state rules this spec adds
to `registers.py` (the single calibration surface). The helpers are
**stdlib-only and pure** (no logging, no HA imports); consumers
(coordinator, sensor platform) own logging and state mapping. Test
vectors live in §6 and are asserted in `tests/test_registers.py`.

Confidence: sentinel values **[P]** (both conventions evidenced — §2),
plausibility bounds **[D]**, binary encoding **[A]**.

---

## 1. Function signatures (registers.py)

```python
SENTINEL_RAWS: frozenset[int]            # {-9990, 32765}
SENTINEL_RAW: int                        # -9990 (legacy alias, member of the set)
TEMP_PLAUSIBLE_MIN_RAW: int             # -450  -> -45.0 degC
TEMP_PLAUSIBLE_MAX_RAW: int             # 1500  -> +150.0 degC

def is_sentinel(raw: int | None) -> bool: ...
def is_plausible_temp(raw: int | None) -> bool: ...
def decode_temp(raw: int | None) -> float | None: ...
def decode_flag(raw: int | None, active_when_nonzero: bool) -> bool | None: ...

ALLOWED_WRITE_BASE: frozenset[int]       # {REG_POWER, REG_MODE, REG_SET_CURRENT_TEMP}
def ensure_writable(address: int) -> None: ...   # raises ValueError
```

`decode_temp`/`decode_flag`/`is_sentinel` accept `None` (register absent
from the snapshot) and return `None`/`None`/`False` respectively — the
snapshot can never contain `None` values for present registers, but the
helpers stay total for testability.

## 2. Temperature decode (`decode_temp`)

Priority order — first match wins:

| # | Condition | Result |
|---|---|---|
| 1 | `raw is None` | `None` (register absent) |
| 2 | `raw ∈ SENTINEL_RAWS` (`{-9990, 32765}`) | `None` — **sensor absent** → entity state `unknown` |
| 3 | `raw < TEMP_PLAUSIBLE_MIN_RAW` or `raw > TEMP_PLAUSIBLE_MAX_RAW` | `None` — **implausible** → `unknown` + consumer logs once per address |
| 4 | otherwise | `round(raw / TEMP_SCALE, 1)` |

Sentinel evidence:

- `−9990` = −999.0 °C — the app's own "sensor absent" definition
  [P apk — FINDINGS.md §12].
- `32765` = `0x7FFD` — displayed as 3276.5 °C for the absent T01 Suction
  sensor on the owner's unit [P panel, 2026-10]; two below the int16
  maximum (`0x7FFF`).
- Which convention each register uses on the wire: [V] — the set makes
  both safe; the calibration session records which one each family uses.

Plausibility bounds [D]: floor −450 (−45.0 °C — far below any ambient a
pool PAC survives; real winter negatives like raw −100 → −10.0 °C always
display); ceiling 1500 (+150.0 °C — the highest legitimate system
temperature observed is the H04 exhaust-high **safety threshold** at
105.0 °C [P panel], so 150.0 °C leaves headroom while catching any
unknown `0x7FFx`-style sentinel and endianness garbage like `0xAA00`).

**Non-goal**: the vendor app displays an absent sensor as `3276.5 °C`
[P panel] — this integration must never display a sentinel, under any
label.

## 3. Where decode_temp applies

- `coordinator._scaled` (snapshot fields: target/min/max/inlet/outlet/
  ambient) — same None-contract as today, new sentinel-set + plausibility
  behaviour.
- `sensor.native_value` for every `device_class == "temperature"` RegDef
  (existing three + new placeholders).
- The firmware join (regs 48–51): membership test switches from
  `!= SENTINEL_RAW` to `not is_sentinel(r)` — same semantics for good
  values, both conventions guarded.
- **Not** applied to: fault code (raw int display), register dumps (the
  probe always shows RAW + hex + `/10` hypothesis — it must reveal
  sentinels, not hide them).

## 4. Binary decode (`decode_flag`)

```
raw is None                     -> None
active_when_nonzero is True     -> raw != 0
active_when_nonzero is False    -> raw == 0
```

- Polarity resolution order (consumer-side): `RegDef.active_when_nonzero`
  if not None, else the global `BINARY_ACTIVE_WHEN_NONZERO` (default
  True) — one calibration switch per entity, global default preserved
  (FR-4).
- Rationale: S03 Flow reads ON while the unit is idle [P panel] while
  O01 Compressor reads OFF — the families may use opposite conventions
  [V]; per-RegDef polarity lets calibration fix one without code changes.
- Raw encoding 0/1 is itself [A] — the probe dump decides.

## 5. Write guard (`ensure_writable`)

```
allowed = ALLOWED_WRITE_BASE ∪ ({REG_SET_COOL_TARGET, REG_SET_HEAT_TARGET,
                                 REG_SET_AUTO_TARGET} if WRITE_MODE_SETPOINT_TOO else set())
address not in allowed -> raise ValueError(f"register {address} is not writable (spec 003 §6)")
```

- `ALLOWED_WRITE_BASE = {REG_POWER (8), REG_MODE (13),
  REG_SET_CURRENT_TEMP (14)}` [P apk — the only proven user-operational
  writes].
- `coordinator.async_write_register` calls `ensure_writable` as its
  **first statement** — before any lock acquisition or I/O.
- High blocks 1001–2090 are never writable (guard raises by
  construction — they are not in any allowlist); the probe is read-only.
- This is the enforcement point of spec §6 (security policy): H/R/P
  members can never be written even by future code that bypasses the
  climate entity.

## 6. Test vectors (asserted in tests/test_registers.py)

| Input | Expected |
|---|---|
| `decode_temp(None)` | `None` |
| `decode_temp(-9990)` | `None` (sentinel, apk convention) |
| `decode_temp(32765)` | `None` (sentinel, panel convention) |
| `decode_temp(170)` | `17.0` |
| `decode_temp(195)` | `19.5` |
| `decode_temp(175)` | `17.5` |
| `decode_temp(-100)` | `-10.0` (winter ambient passes) |
| `decode_temp(1050)` | `105.0` (H04-scale value passes) |
| `decode_temp(2000)` / `decode_temp(-5000)` / `decode_temp(32767)` | `None` (implausible) |
| `decode_flag(None, True)` | `None` |
| `decode_flag(0, True)` / `decode_flag(1, True)` | `False` / `True` |
| `decode_flag(0, False)` / `decode_flag(1, False)` | `True` / `False` (inverted) |
| `ensure_writable(8)` / `(13)` / `(14)` | no raise |
| `ensure_writable(15)` / `ensure_writable(1004)` | raises `ValueError` |
| `WRITE_MODE_SETPOINT_TOO=True` → `ensure_writable(20)` | no raise |
| `is_sentinel(-9990)` / `is_sentinel(32765)` / `is_sentinel(0)` | `True` / `True` / `False` |

## 7. Logging contract (consumer side)

- Implausible temperature: **one** `WARNING` per address per HA session
  (`"register N reports implausible raw M — treated as absent"`),
  module-level once-set, pattern of `_logged_unknown_modes`.
- Sentinels are **not** logged (they are normal "sensor absent" states,
  not errors) — they appear in the probe dump as raw values, which is
  exactly where they are useful.