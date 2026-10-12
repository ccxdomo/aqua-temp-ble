# Spec 024 tasks — ordered for the Coder

Working order is the task order. Every task cites spec.md's FR/AC and
the research/plan sections. The untouchables are plan.md §5 —
headlined: **`binary_sensor.py` stays BYTE-IDENTICAL** (FR-5), the
write plane, the freshness gate, the frames, the classic plane, the
code map (one proven row), specs 001-023. No option, no flow step, no
service anywhere: the sensor is read-only decoding, active by default
(FR-9). The normative record is contracts/fault-code-sensor-contract.md.

- [x] **T1** — registers.py: the sensor's state rule (FR-4, AC-5;
      research 024 §2.2; plan §2.1)
      - ADD inside the pushed-fault section, directly after
        `fault_set_bits` (before the `PUSH_CONTROL_CANDIDATES`
        comment block): `PUSHED_FAULT_CLEAR_STATE = "No fault"` with
        the latch evidence comment (plan §2.1 verbatim: raw 0 = clear
        proven twice; the latch — a fault sets the word, only cutting
        the unit's supply clears it, owner-proven 2026-10-12; a
        non-zero state reports a MEMORIZED fault, not necessarily an
        active one).
      - ADD the pure helper `fault_code_state(raw) -> str | None`
        (plan §2.1 verbatim): None → None; 0 →
        `PUSHED_FAULT_CLEAR_STATE`; non-zero mapped → the map's code;
        non-zero unmapped → `f"unknown code (raw {raw})"`. Stdlib-only,
        pure, no logging — mirroring the 023 helpers beside it.
      - DO NOT touch: `REG_PUSHED_FAULT`, `PUSHED_FAULT_CODE_MAP`
        (exactly `{256: "E03"}` — FR-4), `PUSHED_FAULT_CODE_APP_
        LABELS`, `decode_fault_flag`, `fault_code`,
        `fault_code_app_label`, `fault_set_bits`,
        `PUSH_CONTROL_CANDIDATES`, `KNOWN_LABELS`, the registries,
        the frames, the allowlists.

- [x] **T2** — sensor.py: the literal Fault code entity (FR-1, FR-2,
      FR-3; AC-1..AC-4; plan §2.2)
      - `SENSOR_NAMES` gains `"pushed_fault_code": "Fault code"`
        (plan §2.2 (a) verbatim, with its comment).
      - The push branch: the list's type annotation gains the class;
        append `AquaTempFaultCodeSensorEntity(coordinator, entry)`
        after the `push_stream` append (plan §2.2 (b) verbatim). The
        CLASSIC branch is untouched (law L1 — register 47 unreachable;
        the classic `fault_code` RegDef keeps its own surface).
      - ADD the class after `AquaTempPushStreamEntity` (plan §2.2 (c)
        verbatim): `AquaTempEntity, SensorEntity`; key
        `"pushed_fault_code"` (unique_id `<ADDRESS>_pushed_fault_code`
        via the base — DISJOINT from the classic `fault_code` key,
        data-model 024 §8); `self._attr_name =
        SENSOR_NAMES["pushed_fault_code"]`; `_attr_icon =
        "mdi:alert-circle"`; NO device_class (an enum class would
        demand a closed options list the unknown-code family
        violates), NO entity_category, NO state_class, NO unit.
      - `native_value`: `data is None or data.mode != "push"` → None;
        else `registers.fault_code_state(data.registers.get(
        registers.REG_PUSHED_FAULT))`.
      - `extra_state_attributes`: the binary's evidence set MIRRORED
        verbatim (plan §2.2 (c)): `{"register": 2074}` + when the raw
        is present: `raw`, `hex` (0x%04X), `set_bits`, `code` when
        `registers.fault_code(raw)` is not None, `code_label` when
        `registers.fault_code_app_label(raw)` is not None. NO timing
        attribute — contract 024 §6 (the store's first_seen/last_seen
        date the listening window, never the fault).
      - NO registry, NO RegDef, NO placeholder — one literal entity
        (the push_stream precedent).

- [x] **T3** — README.md: the latch + the new tile, three spots
      (FR-7, AC-8; plan §2.4)
      - Features table: replace the 023 fault row with plan §2.4's
        two-entity row (the latch pointer included).
      - "Entities (push mode)" table: add BOTH rows after the Suction
        temperature row (plan §2.4 verbatim — the Fault binary row
        was missed by 023; this spec adds both faces).
      - Troubleshooting: update the Fault row AND add the latch row
        (plan §2.4 verbatim — "Fault code shows while the unit is off
        / healthy" → the latched-memory explanation + the
        power-cycle remedy).
      - Grep gate: NO sentence anywhere in the README may claim that
        turning the unit off clears a fault (AC-8).

- [x] **T4** — docs/CALIBRATION.md §3: the stale bullet (FR-8, AC-8;
      plan §2.5)
      - Replace the "Fault code & firmware: no home was ever
        identified… no entity is created" bullet with plan §2.5's
        verbatim rewrite: the fault word (2074) IS exposed (the 023
        binary + the 024 code sensor); it is LATCHED (memorized until
        the supply is cut, owner-proven 2026-10-12); firmware stays
        unexposed. (The bullet is stale since 023 — research 024 §8;
        this task clears the 023 debt.)

- [x] **T5** — manifest.json: the version line (FR-10, AC-10; plan
      §2.3)
      - `"version": "0.17.0"` → `"version": "0.18.0"`. Nothing else
        in the manifest changes.

- [x] **T6** — tests/test_fault_code_024.py: the new suite (AC-1..AC-10;
      plan §2.6)
      - The scaffolding: copy `test_fault_023.py`'s synthetic-package
        harness (fake homeassistant modules, fake bleak, the stubbed
        coordinator) and extend the sensor stub with the new class.
      - The fixtures with their provenance comments (plan §2.6
        verbatim): `FRAME_2046_E03` (the owner's byte-exact capture —
        CRC 551d; re-served identically by the 2026-10-12 follow-up
        run) and `FRAME_2046_E03_CLEAR` (the healthy twin — also the
        post-power-cut clear state).
      - The cases, in order:
        1. AC-2/AC-3 — the state + attributes table: snapshots from
           captures carrying 2074 = 0 / 256 / 4 / absent → states
           `"No fault"` / `"E03"` / `"unknown code (raw 4)"` / None;
           the attributes table (code, code_label "water flow
           problem", set_bits [8]/[2]/[]); NO timing key in any
           attributes dict.
        2. AC-4 — both evidence frames through the REAL store
           (`client._on_notify`) → the coordinator snapshot → the
           sensor reads "E03" (binary ON) / "No fault" (binary OFF).
        3. AC-1 — creation: push entry → exactly ONE
           AquaTempFaultCodeSensorEntity (name "Fault code", unique_id
           `<ADDRESS>_pushed_fault_code`, icon mdi:alert-circle, NO
           device_class / entity_category / unit / state_class); the
           six temps + push_stream still created (the 013/022
           key-filtered assertions keep passing); classic entry →
           NONE.
        4. AC-5 — registers: `PUSHED_FAULT_CLEAR_STATE == "No fault"`;
           `fault_code_state` total over {None, 0, 256, 4, 8192};
           `PUSHED_FAULT_CODE_MAP == {256: "E03"}`; the forbidden
           bit-names grep (copy `FORBIDDEN_BIT_NAMES` from the 023
           suite).
        5. AC-6 — the freshness NON-change (grep): no fault-specific
           freshness/staleness code in the component sources (the
           fault paths reference only the shared coordinator gate).
        6. AC-7 — the binary untouched: `binary_sensor.py` contains
           no `fault_code_state` reference (the byte-identical proof
           rides T7's git gate).
        7. AC-8 — the docs greps: the README's features row, both
           entity rows, both troubleshooting rows (the word
           "latched" present); CALIBRATION.md §3's new truth; no
           "turning the unit off clears" claim; the three translation
           JSONs untouched (key parity green as-is).
        8. AC-9 — the read-only guarantee: the push options schema
           stays exactly `poll_interval` + `temperature_unit`;
           `registers.ensure_writable(2074, 0)` raises (the funnel
           refuses the fault word forever).
        9. AC-10 — the manifest: version `0.18.0`.

- [x] **T7** — the cross-cutting gate (AC-7, AC-9, AC-10)
      - The FULL suite green (including `test_fault_023.py`
        UNMODIFIED — the binary's contract outlives this spec); the
        `modbus.py` self-test green; `py_compile` on the component.
      - `git diff --exit-code custom_components/aqua_temp_ble/
        binary_sensor.py` — EMPTY (FR-5's byte-identical proof).
      - The untouchables byte-asserted unchanged by the existing
        suites (value rails, allowlists, pinned frames).

- [x] **T8** — the red-first / parity sanity pass (the reviewer's
      checklist, not a new file)
      - Confirm the sensor's states on a push entry match the binary
        exactly (both faces decode the same raw — a divergence is a
        bug, data-model 024 §2).
      - Confirm NO new option key, flow step, service or confirmation
        anywhere (the push options schema untouched).

---

## Phase B — the owner's live verification (recommended, NOT a ship gate)

quickstart 024 §2: the latch replay — `No fault → E03 → (climate
Off, the machine healthy, the tile KEEPS E03 — correct) → supply cut
→ No fault`. AC-4 pins the frames in CI; the live walk-through is
confidence in a user-facing semantics change (the latch), worth the
fifteen minutes.

## Phase C — future codes (the opportunistic protocol, unchanged)

quickstart 023 §4, referenced by quickstart 024 §4: each fault that
happens on its own banks one row (panel code + either entity's
`raw`/`set_bits`) → a one-line follow-up adds it to
`PUSHED_FAULT_CODE_MAP`. The APK assets
(`assets/faults/faults_*.json`) remain the optional static shortcut.
No task here ships without a recorded observation.