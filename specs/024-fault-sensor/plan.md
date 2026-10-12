# Spec 024 — Plan: the design

Date: 2026-10-12
The evidence record is `research.md` (the latch proof, the six
decisions); the normative rules are
`contracts/fault-code-sensor-contract.md` (which extends the active
contract 023). This file is the WHAT and the HOW for the Coder: the
change map, file by file, with the exact code shapes. The
untouchables are §5.

---

## 1. The shape of the change

Four production files change (`registers.py`, `sensor.py`,
`README.md`, `docs/CALIBRATION.md`, plus the manifest version line),
zero test-only regressions elsewhere, one new test suite. No option,
no flow, no service, no schema change anywhere. Total new production
code: ~80 lines.

```
custom_components/aqua_temp_ble/
  registers.py        + PUSHED_FAULT_CLEAR_STATE, fault_code_state()
  sensor.py           + SENSOR_NAMES entry, the literal entity class,
                       one append in the push branch (the classic
                       branch untouched)
  manifest.json       ~ "version": "0.18.0"
README.md             ~ 3 spots (features row, entities rows,
                        troubleshooting rows)
docs/CALIBRATION.md   ~ §3's stale fault bullet (stale since 023)
tests/
  test_fault_code_024.py  NEW — the suite (AC-1..AC-10)
```

**`binary_sensor.py` is NOT in the list** — it stays byte-identical
(FR-5, AC-7): the sensor is an ADDITION, not a replacement. The
binary remains the automation face; the sensor is the display face.

## 2. The design, file by file

### 2.1 registers.py — the state helper (FR-4, AC-5)

Placement: inside the pushed-fault section, directly after
`fault_set_bits` (before the `PUSH_CONTROL_CANDIDATES` block). Exact
content:

```python
# Spec 024 (FR-4): the fault-code SENSOR state — the display face of
# the SAME fault word (contract 024 §3). "No fault" is a decoded
# truth, not a guess: raw 0 = clear is proven twice (the 023
# diff-revert; the 2026-10-12 post-power-cut capture), and the latch
# gives it an exact meaning — the fault memory is empty since the
# last power-up. THE LATCH (owner-proven 2026-10-12, research 024
# §1): a fault SETS the word; the value persists through the
# machine's logical off and BLE link restarts; ONLY cutting the
# unit's power supply clears it. A non-zero state therefore reports
# a MEMORIZED fault, not necessarily an active one.
PUSHED_FAULT_CLEAR_STATE = "No fault"


def fault_code_state(raw: int | None) -> str | None:
    """The fault word -> the fault-code sensor state (spec 024).

    None -> None (register absent -> the entity's `unknown`, never a
    guessed "No fault"); 0 -> PUSHED_FAULT_CLEAR_STATE (the latched
    memory is empty since the last power-up); non-zero -> the PROVEN
    panel code when the raw is mapped (PUSHED_FAULT_CODE_MAP), else
    'unknown code (raw N)' (the contract 022 §3 honesty rule, carried
    into the STATE). The fault word is LATCHED (research 024 §1): the
    non-zero states report a memorized fault, not necessarily an
    active one — the docs carry that semantics, the entity reports
    the register faithfully.
    """
    if raw is None:
        return None
    if raw == 0:
        return PUSHED_FAULT_CLEAR_STATE
    return PUSHED_FAULT_CODE_MAP.get(raw, f"unknown code (raw {raw})")
```

NOT touched: `REG_PUSHED_FAULT`, `PUSHED_FAULT_CODE_MAP` (exactly one
row — 256: "E03"), `PUSHED_FAULT_CODE_APP_LABELS`,
`decode_fault_flag`, `fault_code`, `fault_code_app_label`,
`fault_set_bits`, `PUSH_CONTROL_CANDIDATES`, `KNOWN_LABELS`, the
registries, the frames, the allowlists. (The 023 helpers stay the
binary's and the attributes' single source; the new helper is the
SENSOR's single source — one state rule, one function.)

### 2.2 sensor.py — the literal entity (FR-1/2/3, AC-1..AC-4)

Three edits, all in the push-mode path — the classic branch is
untouched (law L1: register 47 is unreachable; the classic
`fault_code` RegDef keeps its own surface).

**(a) `SENSOR_NAMES` gains the display name (the `push_stream`
precedent — one source of truth for names):**

```python
    # Spec 024 (FR-1): the fault-code display face (push-mode entries)
    # — the code the panel shows, on the tile.
    "pushed_fault_code": "Fault code",
```

**(b) The push branch appends the literal entity** (after the
`push_stream` append; the list's type annotation gains the class):

```python
        entities: list[
            AquaTempSensorEntity
            | AquaTempPushStreamEntity
            | AquaTempFaultCodeSensorEntity
        ] = []
        ...
        entities.append(AquaTempPushStreamEntity(coordinator, entry))
        # Spec 024 (FR-1): the fault-code display face — the code on
        # the tile (a problem binary renders localized on/off words
        # ("Problematique"/"OK"); a code cannot ride a binary_sensor
        # state — contract 024 §4).
        entities.append(AquaTempFaultCodeSensorEntity(coordinator, entry))
        async_add_entities(entities)
        return
```

**(c) The class**, placed after `AquaTempPushStreamEntity` (the
literal-entity precedent; no registry, no RegDef loop, no
placeholder):

```python
class AquaTempFaultCodeSensorEntity(AquaTempEntity, SensorEntity):
    """The fault word 2074 as a code string (spec 024, contract 024 §3).

    The DISPLAY face of the same register the Fault binary reads
    (one register, one source, two faces): "No fault" when the
    latched fault memory is empty since the last power-up, the
    PROVEN panel code when the raw maps (E03 = 256), "unknown code
    (raw N)" otherwise — never a guessed code, never a bit name
    (contract 023 §3). The fault word is LATCHED (research 024 §1 —
    the owner's 2026-10-12 power-cut proof): a non-zero state reports
    a MEMORIZED fault, not necessarily an active one; the README
    carries that semantics. `unknown` when the register is absent;
    unavailable with every entity while the stream is silent (the
    SHARED freshness gate — no fault-specific staleness code exists,
    contract 024 §5). NO device_class (a free-form string state; an
    enum class would demand a closed options list the open-ended
    unknown-code family violates); NO timing attributes (contract
    024 §6 — the store's first_seen/last_seen date the listening
    window, never the fault; the recorder is the honest timeline).
    """

    _attr_icon = "mdi:alert-circle"  # the classic fault_code icon

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "pushed_fault_code")
        self._attr_name = SENSOR_NAMES["pushed_fault_code"]

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return None
        # The same fresh 2046-block capture the binary reads (one
        # register, one source — the two faces can never disagree).
        return registers.fault_code_state(
            data.registers.get(registers.REG_PUSHED_FAULT)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return {}
        raw = data.registers.get(registers.REG_PUSHED_FAULT)
        attrs: dict[str, Any] = {"register": registers.REG_PUSHED_FAULT}
        if raw is not None:
            attrs["raw"] = raw
            attrs["hex"] = f"0x{raw & 0xFFFF:04X}"
            attrs["set_bits"] = registers.fault_set_bits(raw)
            code = registers.fault_code(raw)
            if code is not None:
                attrs["code"] = code
                label = registers.fault_code_app_label(raw)
                if label is not None:
                    attrs["code_label"] = label
        return attrs
```

Design notes (the WHY, for the Coder):

- **Key `pushed_fault_code`, not `fault_code`.** The classic plane's
  RegDef already owns the `fault_code` key (unique_id
  `<ADDR>_fault_code`, register 47). Entity keys stay disjoint across
  planes (data-model 023 §6): a unit that ever ran classic mode keeps
  its orphaned classic entity, and the pushed sensor must never
  collide with it. The entity_id still renders
  `sensor.<device>_fault_code` from the NAME (`has_entity_name`) —
  what the owner asked for is the DASHBOARD name; the unique_id
  namespace is plumbing.
- **NO device_class.** The state vocabulary is open-ended ("unknown
  code (raw N)" for any new raw): a `SensorDeviceClass.ENUM` would
  demand a closed `options` list and HA would log state errors on
  every unmapped fault. No unit, no state_class (not a measurement).
- **The attributes mirror the Fault binary's set verbatim** (contract
  023 §2) — the banking protocol (quickstart 023 §4) works unchanged
  from either entity. The duplication of the ~12 attribute lines is
  deliberate: `binary_sensor.py` stays byte-identical (FR-5), and the
  decode rules themselves stay single-sourced in `registers.py`.

### 2.3 manifest.json — the version line (FR-10, AC-10)

`"version": "0.17.0"` → `"version": "0.18.0"` (the 023→0.17.0
precedent; HACS sees the change). Nothing else in the manifest.

### 2.4 README.md — three spots (FR-7, AC-8)

1. **Features table** — replace the 023 fault row:

   > | Fault indicator (code when proven) | **proven** (owner E03 diff-revert) | 1 binary sensor |

   with:

   > | Fault indicator + fault code | **proven** (owner E03 diff-revert, two independent runs; the fault word is latched — see Troubleshooting) | 1 binary sensor + 1 sensor |

2. **"Entities (push mode)" table** — two rows after the "Suction
   temperature" row (the binary row was missed by 023; this spec
   adds both faces):

   > | Fault (binary) | pushed 2074 (the latched fault word) | `on` = a fault is on record (memorized, not necessarily active); attributes: raw / hex / set_bits / code / code_label |
   > | Fault code | pushed 2074 (the latched fault word) | `No fault` / the panel code (`E03`) / `unknown code (raw N)`; a code stays displayed while the unit is powered — cleared only by cutting the unit's supply (latch, owner-proven) |

3. **Troubleshooting** — update the 023 Fault row and add the latch
   row:

   > | Fault / fault code (the "Fault" binary + "Fault code" sensor) | ON / the code when the fault word (register 2074) is non-zero; E03 = raw 256 (the vendor app itself labels it "water flow problem"); any other raw reads `unknown code (raw N)` until recorded live. Firmware: still not displayed (no home identified) |
   > | Fault code shows while the unit is off / healthy | **Normal — the fault word is latched**: the unit memorizes a fault until its power supply is cut (owner-proven 2026-10-12); turning the unit off does NOT clear it. Check the panel: if the machine is fault-free, cut the unit's supply (unplug/replug) to clear the code |

   Grep gate: no sentence anywhere in the README may claim that
   turning the unit off clears a fault.

### 2.5 docs/CALIBRATION.md — §3's stale bullet (FR-8, AC-8)

Replace:

> - **Fault code & firmware**: no home was ever identified in the pushed
>   blocks — no entity is created (never guessed).

with:

> - **Firmware**: no home was ever identified in the pushed blocks — no
>   entity is created (never guessed). **The fault word** (pushed
>   register 2074) IS exposed — a "Fault" binary sensor (spec 023) and
>   a "Fault code" sensor (spec 024); it is **latched**: the unit
>   memorizes a fault until its supply is cut (owner-proven
>   2026-10-12), so a displayed code may outlive the fault itself.

### 2.6 tests/test_fault_code_024.py — the new suite (AC-1..AC-10)

The synthetic-package harness pattern of `test_fault_023.py`
(fake homeassistant modules, fake bleak, the stubbed coordinator) —
copy that file's `_install_stubs` / `_make_platform` scaffolding and
extend the sensor stub with the new class. The fixtures, byte-exact,
in the header with their provenance comments (they mirror
`test_fault_023.py`'s — the same owner's evidence):

```python
# The owner's E03 capture of block 2046, byte-exact (research 023
# §1.2; re-served identically by the 2026-10-12 follow-up run,
# research 024 §1.1) — CRC 0x1D55, 2074 = 0x0100 = 256 (bit 8).
FRAME_2046_E03 = (
    "631007fe002d5a"
    + "7ffd"
    + "00910091009600960096"
    + "00" * 44
    + "0100"                  # 2074: THE FAULT (256 = bit 8)
    + "00" * 32
    + "551d"
)
# The healthy twin (2074 = 0) — also the post-power-cut clear state
# (the latch: only cutting the unit's supply returns this,
# owner-proven 2026-10-12).
FRAME_2046_E03_CLEAR = (
    "631007fe002d5a7ffd00910091009600960096"
    + "00" * 78 + "54f0"
)
```

The cases (in order):

1. **AC-2/AC-3 — the state + attributes table** (pure, no HA
   platform): coordinator snapshots from captures carrying 2074 = 0 /
   256 / 4 / absent → states `"No fault"` / `"E03"` /
   `"unknown code (raw 4)"` / `None`; attributes: 256 → `code: "E03"`,
   `code_label: "water flow problem"`, `set_bits: [8]`, `raw: 256`,
   `hex: "0x0100"`; 0 → no `code`/`code_label`, `set_bits: []`; 4 →
   `code: "unknown code (raw 4)"`, `set_bits: [2]`, no `code_label`;
   NO timing key in any attributes dict.
2. **AC-4 — the evidence fixtures**: both 99-byte frames through the
   real store (`client._on_notify`) → the coordinator snapshot → the
   sensor reads `"E03"` (and `registers.decode_fault_flag` → ON) /
   `"No fault"` (OFF). The owner's evidence stays pinned byte-exact
   in CI, now against the second face.
3. **AC-1 — creation**: push entry setup → exactly ONE
   `AquaTempFaultCodeSensorEntity` (name "Fault code", unique_id
   `<ADDRESS>_pushed_fault_code`, icon `mdi:alert-circle`, NO
   device_class / entity_category / unit / state_class, the entry's
   device); classic entry → NONE; and the six temps + push_stream
   still created (the key-filtered 013/022 assertions keep passing).
4. **AC-5 — registers**: `PUSHED_FAULT_CLEAR_STATE == "No fault"`;
   `fault_code_state` total over {None, 0, 256, 4, 8192} (the pure
   table); `PUSHED_FAULT_CODE_MAP == {256: "E03"}`; the forbidden
   bit-names grep over the component (the 023 `FORBIDDEN_BIT_NAMES`
   tuple, copied).
5. **AC-6 — the freshness NON-change (grep)**: the fault paths contain
   no fault-specific freshness/staleness code (`fault` + `fresh` /
   `stale` co-occurrence grep on the component sources returns
   nothing new beyond the shared gate).
6. **AC-7 — the binary untouched**: `binary_sensor.py` contains no
   `fault_code_state` reference; the 023 suite passes unmodified
   (the full-suite gate).
7. **AC-8 — docs greps**: the README carries the features row, both
   entity rows and BOTH troubleshooting rows (the latch sentence
   verbatim: "latched"); CALIBRATION.md §3 carries the new truth; no
   "turning the unit off clears" claim anywhere; the three
   translation JSONs untouched (key parity test green as-is).
8. **AC-9 — the read-only guarantee**: the push options schema stays
   exactly `poll_interval` + `temperature_unit`;
   `registers.ensure_writable(2074, 0)` raises (the funnel refuses
   the fault word forever).
9. **AC-10 — the manifest**: version `0.18.0`.

### 2.7 What does NOT change (verify by reading, do not edit)

- **`binary_sensor.py`** — byte-identical (FR-5; AC-7's git-diff gate).
- **`coordinator.py`** — nothing: `from_pushes` already exposes the
  2046-block raws on `data.registers`; 2074 rides it like the temps.
- **`const.py`** — nothing: `PLATFORMS` already contains `sensor`.
- **`config_flow.py`, `climate.py`, `button.py`, `aqua_client.py`,
  `modbus.py`, `strings.json`, `translations/`** — untouched (the
  translation JSONs carry no fault sentence; key parity green).
- **The 023 suite** — unmodified and green (the binary's contract is
  the 023 file; this spec adds to the sensor platform only).
- No existing suite pins the push-mode sensor platform's TOTAL entity
  count (verified: `test_registers_push_013.py` selects by
  `AquaTempSensorEntity` keys and `any(PushStreamEntity)`; the
  `len(added)` assertions in the repo pin the button/climate/binary
  platforms, not the sensor platform) — the literal addition is safe;
  the full-suite run is the proof.

## 3. The phases

- **Phase A — the Coder**: everything above (tasks.md T1-T8). No live
  unit needed: the evidence fixtures ARE the E03 run, pinned.
- **Phase B — the owner (verification, optional but recommended —
  quickstart 024 §2)**: the latch replay — E03 provoked → the tile
  reads `E03` → climate Off, the machine healthy → the tile KEEPS
  `E03` (the latch, correct) → supply cut → `No fault`. The full
  honest round trip `No fault → E03 → (off, still E03) → No fault`.
  Not a ship gate — AC-4 pins the frames — but the latch is a
  user-facing semantics change: one live walk-through is worth the
  fifteen minutes.
- **Phase C — future codes** (unchanged): the opportunistic banking
  protocol (quickstart 023 §4, referenced by quickstart 024 §4); the
  APK assets remain the static lead.

## 4. Risks and mitigations

- **"The tile says E03 but the machine is fine" (the user-confusion
  risk)** — the whole point of FR-7/FR-8: the latch is documented
  everywhere users read it, with the remedy (check the panel; cut
  the supply to clear). The confusion the owner himself hit becomes
  a documented, expected behavior.
- **A stale block showing an old fault** — impossible-by-construction
  (the 023 ruling, unchanged): the freshness gate fails the cycle when
  the T-block is stale; a successful cycle always carries a fresh
  2074. No fault-specific gate is added (FR-6) — and none is needed:
  the gate tests stream recency, never value movement (research 024
  §6).
- **The entity-registry collision** (classic `fault_code` vs the new
  sensor) — prevented by the plane-prefixed key `pushed_fault_code`
  (plan §2.2's design note).
- **The enum trap** (a device_class that breaks on unknown raws) —
  prevented by NO device_class (plan §2.2).
- **A fault that never moves 2074** — the honest gap, unchanged from
  023: only faults that move the word are visible; the 022
  watch-list instruments remain for that investigation.

## 5. The untouchables

- **`binary_sensor.py`** — byte-identical (this spec's own FR-5).
- The write plane: `ensure_writable`, the allowlists ({0,1} for 1011;
  raw 100..400 step 5 for 1046), the pinned frames.
- The read plane: sentinels, plausibility, the freshness gate, the
  probes, the capture line (contract 013 §2.1).
- The heat-only display rule (spec 018) and the mirror confirmation
  cycles (specs 016/017/019).
- The dormant classic plane.
- `PUSHED_FAULT_CODE_MAP` / `PUSHED_FAULT_CODE_APP_LABELS` — one row
  each, proven; extending needs a recorded observation.
- `specs/001-023` — never modified.