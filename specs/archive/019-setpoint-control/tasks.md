# Spec 019 tasks — ordered for the Coder

Working order is the task order. Every task cites spec.md's FR/AC
and research.md's section. **Phase 1 ships everything inert and
the honest read; phase 2 (T8-T10) ships behind the CLOSED setpoint
gate** — the owner's probe verdict is the precondition (contract
019 §4), the no-regression suite proves the inert state (T11).

- [x] **T1** — the read home wiring (FR-1, AC-4)
      - `registers.py`: `PUSHED_CONTROL["setpoint"] = 1046` with
        the [P owner] tag comment (research §1);
        `READ_MIRROR_SETPOINT = PUSHED_CONTROL["setpoint"]`
        (placed with the WRITE_REG_* block in T2);
        `KNOWN_LABELS` corrections — 1046 `"setpoint home (heat)
        [P owner diff 2026-10-12]"`, 1051 `"cool setpoint candidate
        [A-strong 019]"`, 1068 `"auto setpoint candidate
        [A-strong 019]"` (research §1.1);
        `PUSH_CONTROL_CANDIDATES[1046]`'s tag updated to the
        proven wording.
      - `coordinator.py` `AquaTempData.from_pushes`: the setpoint
        decode reads the capture of the block that CONTAINS the
        home — `block = registers.pushed_block_start(s_addr)` →
        `cap = captures.get(block)` → `target_temp =
        decode_temp_logged(s_addr, cap.registers.get(s_addr) if
        cap else None)` — NOT from the 2001 capture (research §1;
        today's code reads every key from `cregs`). The
        control_confirmed gate semantics UNCHANGED (the setpoint
        rides the existing 015 gate).
      - Tests: a store with ONLY a fresh 1046-block → target
        decodes; a store with only the 2001 block → target None;
        the existing `check_registry_integrity()` passes (1046 is
        in-window, collision-free — run it).

- [x] **T2** — the write-plane constants (FR-2, NFR-3)
      - `registers.py`: `WRITE_REG_SETPOINT = 1046`
        [A-strong — research §5]; `SETPOINT_MIN_RAW = 100`,
        `SETPOINT_MAX_RAW = 400`, `SETPOINT_STEP_RAW = 5`
        [D — research §7];
        `WRITE_SETPOINT_FAMILY_MAP = {1: (1135, 1136, 1137),
        2: (1051, 1046, 1068), 3: (1073, 1074, 1075)}` —
        mode-indexed (cool/heat/auto), DOCUMENTATION-ONLY
        [P — research §4.1], its comment naming the [V] family key
        and the never-write-unproven-slots rule (contract 019 §7).
        No consumer may ever read the map for a write target.

- [x] **T3** — the guard extension (FR-4, AC-2/AC-3)
      - `registers.py`: `ALLOWED_WRITE_SETPOINT = {1046:
        frozenset(range(SETPOINT_MIN_RAW, SETPOINT_MAX_RAW + 1,
        SETPOINT_STEP_RAW))}`; `WRITE_SETPOINT_PROVEN = False`
        (the gate comment: flip ONLY after the probe's
        `EXECUTED + REVERSIBLE` verdict — contract 019 §4).
      - `ensure_writable(address, value=None, *,
        control_allowed=None, setpoint_allowed=None)` — the
        signature gains the `setpoint_allowed` thread (the 017
        `control_allowed` pattern); the base rules and the power
        rules UNTOUCHED (byte-identical messages); the setpoint
        branch: gate closed → the locked message (naming the
        probe verdict + `WRITE_SETPOINT_PROVEN`); gate open +
        value outside the allowlist → the value-discipline message
        (the range and the step in the text); else pass.
      - Tests: the AC-3 matrix (gate-closed refusal; open:
        100/400/295 pass, 99/401/297/1045/1047 raise; the power
        and base rules unchanged).

- [x] **T4** — the parity vectors (FR-3, AC-1, NFR-2)
      - `modbus.py` self-test: assert
        `build_write_control(1046, 300) ==
        bytes.fromhex("63100416000102012c5049")`,
        `build_write_control(1046, 295) ==
        bytes.fromhex("631004160001020127118e")`, and
        `build_write_registers(1046, [300]) ==
        build_write_control(1046, 300)` (research §6; the 017
        AC-1 cross-check precedent).

- [x] **T5** — the fresh-block wait, generalized (FR-12, AC-11)
      - `coordinator.py`: `_wait_fresh_control(before)` →
        `_wait_fresh_block(start, before)` (a pure
        parameterization; the existing call sites pass
        `registers.PUSH_CONTROL_BLOCK_START`; the 016 probe's
        log strings UNCHANGED — its suite must pass as-is).
      - No behavior change; the 016/017 suites re-run green.

- [x] **T6** — the client seam + the probe (FR-5, FR-6, AC-5/6/7/8)
      - `aqua_client.py`: `async write_setpoint_probe_frame(self,
        value_raw: int)` — the rail FIRST (int in
        [SETPOINT_MIN_RAW, SETPOINT_MAX_RAW] and
        `value_raw % SETPOINT_STEP_RAW == 0`, else ValueError
        BEFORE any byte; the 016 `write_probe_frame` rail
        precedent); frame = `modbus.build_write_control(
        WRITE_REG_SETPOINT, value_raw)` (assert its address field
        == 1046 — the shape rail); send fire-and-forget via
        `_write_frame` (never `write_registers` — contract 017
        §4); the connected precondition check.
      - `coordinator.py` `async_setpoint_write_probe() -> dict` —
        FR-5's exact protocol: baseline (trigger →
        `_wait_fresh_block(1046-block, t_mark)` → S with the
        four baseline gates) → phase A (write S; trigger; fresh
        harvest; `1046 == S` AND the whole-block diff empty →
        else `no-op-neighbor-changed` naming the moved
        registers) → phase B (T = S−5 or S+5 toward the interior;
        write T; fresh; `1046 == T` else `not-executed`; write S;
        fresh; `1046 == S` else `not-reversible` with the
        CURRENT-value + remediation text) → the ONE verdict line
        at WARNING (`setpoint write probe: EXECUTED +
        REVERSIBLE (mirror 1046 followed <S>→<T>→<S>) — evidence
        for WRITE_SETPOINT_PROVEN`); single-flight `_io_lock`;
        disconnect in the finally; the stage dict return.
        The probe NEVER calls `ensure_writable` (contract 016 §5's
        pattern — its rail IS the guard) and writes ONLY via T6's
        seam.
      - Tests (the 016 fake-client precedent): the AC-5
        happy path (the full verdict + the exact line);
        AC-6 (the scripted neighbor change → abort, exactly ONE
        frame sent); AC-7 (all failure verdicts + their
        remediation texts + `refused-baseline` sends ZERO
        frames); AC-8 (every recorded frame is 11 bytes,
        `build_write_control(1046, ·)`-shaped, values from
        {S, T}).

- [x] **T7** — the service + button (FR-7, AC-9-of-016's
      `probe_registers` precedent)
      - The service `aqua_temp_ble.setpoint_write_probe` (the
        `power_write_probe` service's schema twin) +
        `button.py`: `AquaTempSetpointProbeButtonEntity` —
        "Setpoint write probe", `EntityCategory.DIAGNOSTIC`,
        icon `mdi:test-tube`, routes to FR-5.
      - `strings.json`/translations: the button's description
        names the physical effect (a 0.5 °C dip for ~2 min,
        reverted) — quickstart §0's promise, in the UI.

- [x] **T8** — the activation surfaces (FR-9, AC-10)
      - `const.py`: `DEFAULT_WRITE_SETPOINT = False`.
      - `config_flow.py` (the `write_control` precedent): the
        `write_setpoint` checkbox (default False) + the save
        merge; the description names the required verdict line.
      - `coordinator.py`: `write_setpoint_allowed` (the OR of the
        option and `registers.WRITE_SETPOINT_PROVEN`, live-read —
        the 017 `write_control_allowed` pattern).
      - Tests: both surfaces' OR matrix; no setter exists.

- [x] **T9** — the control write path (FR-10, FR-12 consumer,
      AC-9 — MERGE BEHIND THE CLOSED GATE)
      - `aqua_client.py`: `async write_setpoint(self, value_raw:
        int)` — the control twin of T6's rail (the same value
        rail; address 1046; fire-and-forget on `_write_frame`).
      - `coordinator.py` `async_control_write_setpoint(self,
        celsius: float) -> dict` — FR-10's exact order (validate:
        finite, 0.5-multiple, in range — NEVER clamp; raw =
        `int(round(celsius * TEMP_SCALE))`; `ensure_writable(
        WRITE_REG_SETPOINT, raw, setpoint_allowed=…
        write_setpoint_allowed)` FIRST → connect → audit INFO
        (`setpoint write via 1046 = <raw> — mirror should follow
        in the next report`) → `_io_lock`: write, t_write, ONE
        `_trigger_report("setpoint write confirm")`,
        `_wait_fresh_block(1046-block, t_write)` → the three
        verdicts (017 FR-4's strings, setpoint-flavored;
        `unconfirmed`/`not-confirmed` → WARNING, NO persistent
        notification — contract 019 §5.3) → the snapshot push on
        confirmed/not-confirmed ONLY (the mirror's truth; never
        the request) → the stage dict → `last_setpoint_write`.
      - Tests: the AC-9 matrix (the happy path's frame bytes + the
        mirror-follows assertion; the mismatch WARNING naming
        both values + the tile shows the MIRROR; unconfirmed → no
        snapshot; step/range/gate-closed refusals; the write
        works with power OFF in the snapshot — the state is the
        register's, not the machine's).

- [x] **T10** — the climate surface (FR-8, FR-11, AC-2, AC-9)
      - `climate.py` (push mode only; the classic entity
        byte-identical):
        - `target_temperature`: the snapshot's `target_temp` (the
          FR-1 decode) — the READ needs no new gate (it rides
          `push_control_confirmed` via the snapshot; the entity
          is only created behind that gate today).
        - `min_temp`/`max_temp`/`target_temperature_step` read
          `registers`' constants (10.0/40.0/0.5 — one source of
          truth).
        - `supported_features`: `TARGET_TEMPERATURE` ONLY while
          `coordinator.write_setpoint_allowed` (composed with
          the existing TURN_ON/TURN_OFF features; gate closed →
          the exact 0.15-era surface, byte-identical).
        - `async_set_temperature`: gate open → validate +
          `async_control_write_setpoint`; gate closed → the exact
          `_refuse_write` message.
        - `extra_state_attributes`: `last_setpoint_write` (the
          stage dict) beside `last_power_write`.
      - Tests: the gate matrix (closed: no TARGET feature, the
        015 refusal, the 017 suite green; open: the feature, the
        write, the read display independent of the write gate).

- [x] **T11** — the suite + no-regression (AC-2/AC-11)
      - New `tests/test_setpoint_019.py` (the fake-module +
        RecordingBleakClient precedent): the full AC matrix of
        T1/T3/T6/T8/T9/T10.
      - The WHOLE existing suite green with both setpoint gate
        surfaces off: 013/014 (the cycle), 015 (the read-only
        climate — now with the target display behind the control
        gate: the 015 suite's expectations on `target_temp`
        None-updates may need the scripted-store review — any
        change must keep the gate semantics, not weaken them),
        016/017 (the power path + the probe after T5's refactor),
        018 (the display).

- [x] **T12** — release + handoff
      - `manifest.json`: version `0.14.0` (a new capability
        surface).
      - The modbus self-test green with the new parity vectors.
      - The handoff note: quickstart 019 §1 (the probe) → §3 (the
        activation + the one-press verification). Until §1's
        verdict is recorded, the write feature is "shipped but
        locked" (contract 019 §4); the READ display is live
        immediately behind the existing control gate.

## Verification (the whole spec)

```bash
python3 custom_components/aqua_temp_ble/modbus.py     # self-test incl. the 1046 parity vectors (AC-1)
python3 -m pytest tests/ -q                          # the full suite (019 included, zero regression)
```

The owner then runs quickstart 019 §1 (the probe — the proof
instrument), and — only on the `EXECUTED + REVERSIBLE` verdict —
§3 (the activation + the first live verification, recorded in
the spec's decision log / the PR description).