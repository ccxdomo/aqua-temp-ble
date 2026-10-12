# Spec 023 tasks — ordered for the Coder

Working order is the task order. Every task cites spec.md's FR/AC and
the research/plan sections. The untouchables are plan.md §5 (the
value rails, allowlists, pinned frames, probes, the capture line,
the classic plane, specs 001-022 — byte-identical when this file is
done). No option, no flow step, no service anywhere: the exposure is
read-only (spec FR-8).

- [x] **T1** — registers.py: the fault plane (FR-5, AC-3, AC-6;
      plan §2.1)
      - ADD the pushed-fault section (after `PUSHED_MODE_VALUE_TO_NAME`,
        before `PUSH_CONTROL_CANDIDATES`): `REG_PUSHED_FAULT = 2074`
        with the [P owner diff-revert 2026-10-12] evidence comment
        block (the 0 -> 256 -> 0 run; 2074 read-only forever — in NO
        write allowlist).
      - ADD `PUSHED_FAULT_CODE_MAP: dict[int, str] = {256: "E03"}`
        with the contract §3 comment (DATA — one row per recorded
        live observation; bit-to-FLAG-name tables FORBIDDEN here).
      - ADD the three pure helpers: `decode_fault_flag(raw)` (None
        -> None; non-zero -> True; 0 -> False), `fault_code(raw)`
        (None/0 -> None; mapped -> the code; else
        `"unknown code (raw N)"`), `fault_set_bits(raw)` (the set-bit
        indices, arithmetic only).
      - `PUSH_CONTROL_CANDIDATES` gains
        `2074: "fault word [P owner diff-revert 2026-10-12 — spec 023]"`.
      - `KNOWN_LABELS.update({2074: "Fault word (bitfield) [P owner
        diff-revert 2026-10-12]"})`.
      - DO NOT touch: `PUSHED_CONTROL`, `PUSHED_TEMPS`,
        `PUSHED_MIRRORS`, `ALLOWED_WRITE_*`, `SETPOINT_*`, the
        frames, `ensure_writable`, `check_registry_integrity`
        (2074 collides with nothing — verify by reading).

- [x] **T2** — const.py: the platform list (FR-6, AC-5)
      - `PLATFORMS = ["climate", "sensor", "button", "binary_sensor"]`
        and the comment rewrite (plan §2.2 verbatim: the 021 prune
        removed registry placeholders; 023 returns the platform for
        the ONE diff-proven binary; no registry, no placeholders).

- [x] **T3** — binary_sensor.py: the NEW platform file (FR-1, FR-2,
      FR-3, FR-6; AC-1, AC-2, AC-4; plan §2.3)
      - The whole file per plan §2.3: docstring (the 021-prune
        context + the 023 return), `async_setup_entry` (push entries
        -> exactly one entity; classic entries -> return early,
        law L1), `AquaTempFaultBinarySensor(AquaTempEntity,
        BinarySensorEntity)` — `_attr_name = "Fault"`,
        `_attr_device_class = PROBLEM`, key `"fault"` (unique_id
        `<ADDRESS>_fault` via the base), NO entity_category.
      - `is_on`: `data is None or data.mode != "push"` -> None;
        else `registers.decode_fault_flag(data.registers.get(
        registers.REG_PUSHED_FAULT))`.
      - `extra_state_attributes`: `{"register": 2074}` + when the raw
        is present: `raw`, `hex` (0x%04X), `set_bits`, and `code`
        when `registers.fault_code(raw)` is not None.
      - NO registry, NO RegDef loop, NO placeholder entries — one
        literal entity class (the plan §2.3 design notes).

- [x] **T4** — README.md: three spots (FR-7, AC-7; plan §2.4)
      - Features table: the Fault row after "Setpoint control"
        (plan §2.4's verbatim row).
      - The "Not available — by design" paragraph: drop the
        fault-code clause; keep mode + firmware; reword the
        binary-sensors clause (the fault IS exposed since 023; the
        other binary surfaces stay pruned).
      - The troubleshooting row: replace the "Fault code / firmware
        not displayed" row with the new truth (plan §2.4's verbatim
        row).
      - Verify NO "not displayed" fault claim remains (grep).
      - The three translation JSONs: UNTOUCHED (they carry no fault
        sentence — plan §2.4; the key-parity test stays green).

- [x] **T5** — tests/test_fault_023.py: the new suite (AC-1..AC-8;
      plan §2.5)
      - The fixtures with their provenance comments:
        `FRAME_2046_E03` (the owner's byte-exact capture — plan §2.5's
        constructed string, CRC 551d verified) and
        `FRAME_2046_E03_CLEAR` (the healthy twin, 2074 = 0, CRC 54f0).
      - AC-2/AC-3: the decode + attributes table (raws 0 / 256 / 4 /
        absent -> off / on(E03) / on(unknown-code(raw 4)) / unknown;
        `set_bits` [8] / [2] / []; raw 0 -> no `code` attribute).
      - AC-4: both fixtures through the real store
        (`client._on_notify`) -> coordinator snapshot -> the entity
        ON (code E03, set_bits [8], raw 256) / OFF.
      - AC-1: push entry setup creates exactly one binary_sensor
        (name/class/unique_id); classic entry creates none.
      - AC-5/AC-8: `PLATFORMS` content; `binary_sensor.py` exists; no
        other binary entity keys in `custom_components/` (grep); the
        push options schema stays exactly `poll_interval` +
        `temperature_unit`.
      - AC-6: 2074 in `PUSH_CONTROL_CANDIDATES` -> the push_stream
        `control_candidates` attributes include it;
        `KNOWN_LABELS[2074]` present.
      - AC-7: the README greps (the Fault row present; no
        "not displayed" fault claim).

- [x] **T6** — the cross-cutting gate (AC-9)
      - The full suite green; the `modbus.py` self-test green
        (untouched); `py_compile` on the components; the value rails
        and pinned frames byte-asserted unchanged by the existing
        suites.

---

## Phase B — the owner's live verification (optional, NOT a ship gate)

quickstart.md §2: the E03 replay (off -> on with `code: E03` ->
off). AC-4 already pins the evidence frames in CI; the live run is
confidence, not a gate.

## Phase C — future codes (the opportunistic protocol)

quickstart.md §4: each fault that happens on its own banks one row
(panel code + the entity's `raw`/`set_bits`) -> a one-line
follow-up adds it to `PUSHED_FAULT_CODE_MAP`. The APK assets
(`assets/faults/faults_*.json`) remain the optional static shortcut.
No task here ships without a recorded observation.