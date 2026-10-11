# Tasks 003 — Sensor-coverage implementation

Status legend: ☐ open · ☑ done. All tasks are open at publication time
(this spec is prospective — the Coder implements, the Reviewer validates
against `spec.md` §5 and `quickstart.md`). Order matters within each
section; sections are ordered by dependency.

Repo rule for the Coder: **English-only** code comments, docs and commit
messages. No commit/push/deploy (the orchestrator handles git).

---

## 1. `registers.py` — decode layer & constants (spec §3 FR-1..FR-4)

- ☑ T-1 Add `SENTINEL_RAWS = frozenset({-9990, 32765})`; keep
  `SENTINEL_RAW = -9990` as the documented legacy alias (FR-14 —
  `tests/test_config_flow_walkthrough.py` imports it).
- ☑ T-2 Add `TEMP_PLAUSIBLE_MIN_RAW = -450`,
  `TEMP_PLAUSIBLE_MAX_RAW = 1500` with the rationale comment (H04 =
  105.0 °C safety threshold observed on the panel).
- ☑ T-3 Add the pure helpers (stdlib-only, no logging):
  `is_sentinel`, `is_plausible_temp`, `decode_temp`, `decode_flag` —
  exact semantics per `contracts/decode-contract.md` §1/§2/§4.
- ☑ T-4 Add `ALLOWED_WRITE_BASE = {REG_POWER, REG_MODE,
  REG_SET_CURRENT_TEMP}` and `ensure_writable(address)` raising
  `ValueError` outside the base set ∪ {19, 20, 21} iff
  `WRITE_MODE_SETPOINT_TOO` (FR-9, `decode-contract.md` §5).

## 2. `registers.py` — registries & integrity (FR-5..FR-8, FR-11)

- ☑ T-5 Extend `RegDef` with `active_when_nonzero: bool | None = None`
  and `family: str | None = None` (fields **appended** — existing
  positional tuples keep working).
- ☑ T-6 Add the three sensor placeholders to `SENSORS` (exact keys and
  attributes per `contracts/entity-contract.md` §2):
  `suction_temp` (T01), `coil_temp` (T04), `exhaust_temp` (T06) —
  `address=None`, temperature class, °C, measurement, no icon.
- ☑ T-7 Add the four binary placeholders to `BINARIES`
  (`entity-contract.md` §3): `high_fan` (O03, `mdi:fan`),
  `hp_switch` (S01, `mdi:alert-circle`, diagnostic), `lp_switch` (S02,
  `mdi:alert-circle`, diagnostic), `flow_switch` (S03, `mdi:water`,
  diagnostic) — all `address=None`. **Do not touch** the existing
  `compressor`/`circulate_pump`/`four_way_valve` keys, icons or
  addresses (label panel-confirmed; keys are stable ids).
- ☑ T-8 Family-tag the existing entries (`T02/T03/T05`,
  `O01/O02/O04`) and set the placeholder labels used by the probe
  annotation loop (`"SuctionTemp [V] (T01)"` style, following the
  existing `KNOWN_LABELS` convention).
- ☑ T-9 Add the import-time integrity check (FR-11 /
  `entity-contract.md` §6): duplicate keys, duplicate non-None
  addresses, out-of-base-block pins, temperature/binary class mismatch →
  `ValueError` at import; the shipped registries must pass it.
- ☑ T-10 Optionally add the `FAMILIES` documentation mapping (family →
  observed labels/values) as **comments or a plain dict with no
  consumers** (`plan.md` §5) — mirror `data-model.md`; no logic may
  depend on it.

## 3. `coordinator.py` — wiring (spec §3, plan §6)

- ☑ T-11 `_scaled` → `registers.decode_temp(regs.get(address))`
  (same None-contract; no other change to `from_regs`).
- ☑ T-12 Firmware join: `r != registers.SENTINEL_RAW` →
  `not registers.is_sentinel(r)`.
- ☑ T-13 Binaries loop → per-RegDef polarity via
  `registers.decode_flag` (RegDef override, else global
  `BINARY_ACTIVE_WHEN_NONZERO`).
- ☑ T-14 `async_write_register`: first statement
  `registers.ensure_writable(address)` (before lock/IO) — the FR-9
  enforcement point.
- ☑ T-15 Implausible-value warning: module-level once-set keyed by
  address, one `WARNING` per address per session, logged where the
  decode result is consumed (`decode-contract.md` §7). Sentinels are
  NOT logged.

## 4. Platforms — wiring (FR-5..FR-7 consumers)

- ☑ T-16 `sensor.py`: for `device_class == "temperature"` RegDefs,
  `native_value` returns `registers.decode_temp(raw)` (replaces the
  local sentinel/scale logic); `fault_code`/`firmware` paths unchanged.
- ☑ T-17 `binary_sensor.py`: extend `BINARY_NAMES` with the four new
  keys; map `regdef.diagnostic` → `EntityCategory.DIAGNOSTIC` (same as
  `sensor.py`); `is_on` unchanged.
- ☑ T-18 **Do not touch** `climate.py`, `modbus.py`, `aqua_client.py`,
  `config_flow.py`, `entity.py`, `__init__.py`, strings/translations
  (NFR-4, FR-12/FR-13).

## 5. Tests (NFR-1, AC-1..AC-9)

- ☑ T-19 New `tests/test_registers.py` (stdlib-only, style of
  `test_modbus.py`): every vector of `decode-contract.md` §6 —
  sentinel set (both conventions), plausibility (incl. `32767`,
  `-100`, `1050`), `decode_flag` matrix with inversion, `ensure_writable`
  matrix (8/13/14 pass; 15/1004 raise; 19–21 conditional), integrity
  check raising on a broken registry, shipped-table assertions (new
  keys present, all new addresses `None`, existing keys/icons/addresses
  unchanged, families per `entity-contract.md`).
- ☑ T-20 Re-run all existing tests green (py_compile, modbus self-test,
  `test_modbus.py`, `test_reassembly.py`, `test_config_flow_list.py`,
  `test_config_flow_walkthrough.py`) — no existing test file may be
  modified except where an alias docstring demands it (none expected —
  `SENTINEL_RAW` stays).

## 6. Docs (AC-10)

- ☑ T-21 `docs/CALIBRATION.md`: add the spec-003 section — panel
  worksheet (`data-model.md` §13 / `calibration-contract.md` §7),
  T-family pinning steps, the **two-state method** for S/O, the
  H/R/P **read-only warning** (never write; `ensure_writable` raises),
  the phase-B note for high-block members. Keep the existing steps
  (base-block runbook) intact and cross-reference them.
- ☑ T-22 `README.md`: entity table incl. the not-yet-created
  placeholders ("appear after calibration"), the sentinel honesty note
  (absent sensor → `unknown`, never `3276.5 °C`), and the security
  note (H/R/P never writable from HA).

## 7. Gates & QA

- ☑ T-23 Run the full gate list (quickstart §0) — all green.
- ☐ T-24 Walk `quickstart.md` V1/V2/V6 on the dev instance; V3–V5 are
  the owner's live session (panel photos + probe) — document the
  results in the runbook.
  - Coder note (2026-10-11): V1 walked via `tests/test_registers.py`
    (AC-1/AC-2 vectors green); V6 walked as a grep-level review (single
    write funnel starts with `ensure_writable`; no service writes; probe
    path read-only). V2's entity-list check needs a dev HA instance (not
    available in this environment) — statically covered by the
    shipped-registry assertions (all new addresses `None`, AC-3) and the
    untouched-platform regression gates; to be confirmed on a live
    instance. V3–V5 = the owner's calibration session.
- ☑ T-25 Reviewer: validate against `spec.md` §5 (AC-1..AC-12) and the
  three contracts; explicit checks — no H/R/P entity or write surface
  anywhere (AC-12), climate/flow byte-identical behaviour (AC-7),
  probe format unchanged (FR-13), integrity check present (AC-9).

## 8. Explicitly out of scope (do not do)

- ☒ No writes to H/R/P registers — in any form, behind any switch
  (spec §6; `ensure_writable` is the guard and must keep raising).
- ☒ No generic "write register" service (research.md §7).
- ☒ No high-block polling / coordinator multi-block reads
  (`research.md` §10 — phase B, future spec).
- ☒ No D-family placeholders, entities or speculation (labels unknown —
  `data-model.md` §9).
- ☒ No `disabled_by_default` entities with guessed addresses
  (`research.md` §6 — `address=None` ⇒ not created is the rule).
- ☒ No climate changes (incl. `hvac_action` from O01 — future work).
- ☒ No changes to spec 001/002 artefacts, FINDINGS.md, or the probe
  log/response format.
- ☒ No commit/push/deploy (the orchestrator handles git).