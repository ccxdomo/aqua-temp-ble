# Contract — Entity coverage (003)

Status: **PLANNED** — normative for the entity layer this spec adds to
`registers.py` (registries), `sensor.py`, `binary_sensor.py`. Every key,
name, unique-id suffix, device class, unit and category below is exact;
the Reviewer checks the shipped registries against this table.

Confidence: register addresses are **[P]** (base block, spec 001) or
**[V]** placeholders (`address=None` — entity not created until
calibration pins it, `calibration-contract.md`). Family codes are
documentation tags from the panel observation (`data-model.md`).

---

## 1. General rules (unchanged from spec 001 unless stated)

- Entities are **registry-driven** from `registers.SENSORS` /
  `registers.BINARIES`; a `RegDef` with `address=None` is skipped at
  setup — no entity, no data under its name.
- **Creation gating (new, FR-7)**: a pinned address must also lie in the
  polled base block **7…51**; outside ⇒ skip + one loud warning
  (`"… register N is outside the polled base block; high-block polling is
  not implemented (spec 003 §8) …"`).
- Unique id = `{ADDRESS_UPPER}_{key}`; device identifiers =
  `(aqua_temp_ble, address)`; `_attr_has_entity_name = True`.
- Display unit: sensors are native °C (HA converts for °F users);
  the climate entity's °C/°F option behaviour is unchanged.
- Refresh: the coordinator snapshot (one 45-register FC03 read per
  cycle). Availability: `CoordinatorEntity` semantics (unavailable while
  updates fail).
- `unknown` state: register missing from the snapshot, sentinel
  (`decode-contract.md` §2), or implausible value. Never a raw sentinel
  rendered as a temperature.

## 2. Sensors (platform `sensor`)

| key | Name | unique_id suffix | Reg | Family | device_class | native unit | state_class | category | Status |
|---|---|---|---|---|---|---|---|---|---|
| `inlet_water_temp` | Inlet water temperature | `_inlet_water_temp` | 26 | T02 | temperature | °C | measurement | — | existing [P], unchanged |
| `outlet_water_temp` | Outlet water temperature | `_outlet_water_temp` | 27 | T03 | temperature | °C | measurement | — | existing [P], unchanged |
| `ambient_temp` | Ambient temperature | `_ambient_temp` | 28 | T05 | temperature | °C | measurement | — | existing [P], unchanged |
| `suction_temp` | Suction temperature | `_suction_temp` | None | T01 | temperature | °C | measurement | — | **new placeholder** [V] |
| `coil_temp` | Coil temperature | `_coil_temp` | None | T04 | temperature | °C | measurement | — | **new placeholder** [V] |
| `exhaust_temp` | Exhaust temperature | `_exhaust_temp` | None | T06 | temperature | °C | measurement | — | **new placeholder** [V] |
| `fault_code` | Fault code | `_fault_code` | 47 | — | — | — | — | diagnostic | existing, unchanged |
| `firmware` | Firmware | `_firmware` | 48–51 | — | — | — | — | diagnostic | existing, unchanged |

Rules for the three new placeholders:

- `scale=TEMP_SCALE` (10.0), `signed=True`, exactly like the existing
  temperature RegDefs.
- **No icon** (the temperature device class supplies one).
- `native_value` path = `registers.decode_temp` for **every**
  temperature-class RegDef (including the existing three — behaviour for
  valid values is unchanged; the 32765 sentinel and implausible values
  now also yield `unknown`).
- Probe annotation: once pinned, `KNOWN_LABELS[addr]` =
  `"<Label> [V] (<family>)"` — e.g. `"SuctionTemp [V] (T01)"`.

## 3. Binary sensors (platform `binary_sensor`)

| key | Name | unique_id suffix | Reg | Family | category | Icon | Polarity | Status |
|---|---|---|---|---|---|---|---|---|
| `compressor` | Compressor | `_compressor` | None | O01 | — | `mdi:engine` | `active_when_nonzero=None` (global default) | existing placeholder, **key/icon unchanged**, label panel-confirmed |
| `circulate_pump` | Circulate pump | `_circulate_pump` | None | O02 | — | `mdi:pump` | `None` (global default) | existing placeholder, unchanged |
| `high_fan` | High fan | `_high_fan` | None | O03 | — | `mdi:fan` | `None` (global default) | **new placeholder** [V] |
| `four_way_valve` | 4-way valve | `_four_way_valve` | None | O04 | — | `mdi:swap-horizontal` | `None` (global default) | existing placeholder, unchanged |
| `hp_switch` | HP switch | `_hp_switch` | None | S01 | **diagnostic** | `mdi:alert-circle` | `None` (calibrate live) | **new placeholder** [V] |
| `lp_switch` | LP switch | `_lp_switch` | None | S02 | **diagnostic** | `mdi:alert-circle` | `None` (calibrate live) | **new placeholder** [V] |
| `flow_switch` | Flow switch | `_flow_switch` | None | S03 | **diagnostic** | `mdi:water` | `None` (calibrate live) | **new placeholder** [V] |

Rules:

- **No device_class** on any of them (polarity semantics unproven —
  `research.md` §9; revisit only after live calibration).
- Entity category: `RegDef.diagnostic=True` ⇒ `EntityCategory.DIAGNOSTIC`
  (S family only). `binary_sensor.py` gains the same mapping `sensor.py`
  already has.
- `is_on` reads `data.binaries[key]` (existing snapshot path); polarity
  resolution per `decode-contract.md` §4.
- Names mirror the panel labels exactly ("HP switch", "LP switch",
  "Flow switch", "High fan") for easy panel↔HA cross-checking.

## 4. Explicitly NOT entities

No entity of **any** platform may be created from: H01–H06, H70–H72,
R01–R11, P01–P04, D01–D06, E04/E08/F13 (spec §6 policy;
`data-model.md` §15). In particular no number/select/switch entity —
these families are **never writable and not displayed**; read-only
inspection is the probe service's job.

## 5. Climate, button, service (unchanged)

- `climate` entity: byte-identical behaviour (spec 001; FR-12).
- `probe_registers` button + service: unchanged surface and log/response
  format (FR-13); new labels appear via `KNOWN_LABELS` only after
  pinning — no format change.

## 6. Integrity invariants (enforced at import, FR-11)

1. All keys in `SENSORS ∪ BINARIES` are unique.
2. All non-None addresses in `SENSORS ∪ BINARIES` are unique across both
   registries.
3. All non-None addresses lie within the base block (7…51) — an
   out-of-block pinning violates this check loudly at import.
4. `is_binary` RegDefs never carry a temperature device_class and vice
   versa.
5. The shipped table matches this contract key-for-key (AC-3/AC-4).