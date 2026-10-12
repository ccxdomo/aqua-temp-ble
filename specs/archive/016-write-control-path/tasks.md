# Spec 016 tasks — ordered for the Coder

- [x] **T1** Done — registers.py: WRITE_REG_POWER/MODE/MODE_PAIR/MUTE, the
      pinned frames, READ_MIRROR_POWER, the KNOWN_LABELS tags (data only).
- [x] **T2** Done — modbus.py: build_write_control + the self-test's four
      pinned vectors + the NFR-2 cross-check (builder == registers' strings).
- [x] **T3** Done — registers.py: ALLOWED_WRITE_CONTROL {1011: {0,1}} +
      WRITE_CONTROL_PROVEN=False + ensure_writable(address, value=None)
      (base rules untouched, AC-4 pinned); coordinator threads the value.
- [x] **T4** Done — coordinator.async_power_write_probe (the diff-revert
      protocol, verdicts at WARNING, single-flight, disconnect-on-completion)
      + aqua_client.write_probe_frame (the pinned-frame seam + rail).
- [x] **T5** Done — button.py "Power write probe" (diagnostic), the
      aqua_temp_ble.power_write_probe service (services.yaml, strings.json,
      translations en/fr, __init__.py registration alongside probe_registers).
- [x] **T6** Done — tests/test_write_control_016.py (25 tests, red-first:
      24/25 failed on 0.11.0 before the implementation): pinned vectors,
      guard matrix, the probe's verdict paths, wire parity, the surfaces;
      full suite 298 passed (273 + 25, zero regression).
- [ ] **T7** BLOCKED on the owner's live probe verdict (contract 016 §4) —
      NOT implemented, by design: the phase-2 climate wiring stays parked
      until the EXECUTED + REVERSIBLE verdict line is recorded; with the
      shipped WRITE_CONTROL_PROVEN=False the funnel refuses 1011 exactly
      as 0.11.0 did (pinned by the 016 suite + the unchanged 015 suite).

Working order is the task order. Every task cites spec.md's FR/AC
and research.md's section. The phase-2 task (T7) is BLOCKED on the
owner's live proof — do NOT merge it without the recorded verdict
(contract 016 §4).

## Block 1 — data + builder (zero behavior change)

### T1 — write-plane constants (FR-1, NFR-3)
`registers.py`: add, with evidence-tag comments, as pure data
(unreachable by any write path):
- `WRITE_REG_POWER = 1011` — [P research §2, disassembly]
- `WRITE_REG_MUTE = 1076` — [P frame / V semantics, research §5]
- `WRITE_REG_MODE = 1012` (+ `WRITE_REG_MODE_PAIR = 1013`) — [P shape / A semantics]
- `WRITE_FRAME_POWER_ON = "631003f30001020001f131"` / `WRITE_FRAME_POWER_OFF = "631003f3000102000030f1"` — [P research §2.1]
- `READ_MIRROR_POWER = PUSHED_CONTROL["power"]` (= 2011 — the existing pinned read home)
Extend `KNOWN_LABELS` with `1011: "Power (WRITE plane) [P disassembly 016]"` and
`1012/1013/1076` tags (the probe-log annotation convention). Do not
modify `PUSHED_CONTROL` or any read mapping.

### T2 — the control-frame builder + self-test (FR-2, AC-1, NFR-2)
`modbus.py`: add
```python
def build_write_control(address: int, value: int) -> bytes
    # 63 10 <addr:2> 0001 02 <val:2> <crc:2>  (11 bytes) — spec 016 FR-2
```
reusing `_frame`. Extend the self-test (the §10 precedent) with the
four pinned vectors of research §2.1 (power ON/OFF, mute ON/OFF) and
an assertion that `build_write_control(1011, 1)` equals the bytes of
`WRITE_FRAME_POWER_ON` (the two sources of truth cross-check).

## Block 2 — the locked guard

### T3 — the control guard, default-locked (FR-3, AC-2/3/4)
`registers.py`:
- `ALLOWED_WRITE_CONTROL: dict[int, frozenset[int]] = {1011: frozenset({0, 1})}`
- `WRITE_CONTROL_PROVEN = False` (comment: the required verdict string
  from quickstart §2 + "flip only after the live proof, then restart")
- extend `ensure_writable(address, value=None)`:
  - base-block rules UNTOUCHED (spec 003 §6 — {8,13,14} +19-21);
  - `address in ALLOWED_WRITE_CONTROL` → allowed only if
    `WRITE_CONTROL_PROVEN` and the value is in the entry's set (else
    `ValueError` naming the rule);
  - keep the signature backward-compatible for the existing callers
    (`value=None` = the old semantics for the base block).
Update `coordinator.async_write_register` to pass the value through
to the guard. AC-4: the existing guard tests pass UNCHANGED.

## Block 3 — the probe + its surfaces

### T4 — the power-write probe (FR-4/FR-6, AC-5..8)
`coordinator.py`: `async_power_write_probe() -> dict[str, Any]`
implementing contract 016 §3's protocol verbatim:
- trigger + harvest (reuse the push-mode helpers; `harvest_pushes`),
  read mirror 2011 from the 2001-block capture;
- baseline gate 2011 == 0 (else `refused-baseline-not-off`, NO write);
- `WRITE_FRAME_POWER_ON` via the existing `_write_frame` seam
  (withoutResponse — app parity; do NOT use the acknowledged path);
- harvest again (if no fresh 2001: ONE base-read trigger, then
  harvest);
- `WRITE_FRAME_POWER_OFF`, harvest, check revert;
- verdict at WARNING with the exact strings of quickstart §2
  (`executed+reversible` / `not-executed` / `not-reversible` +
  remediation / `att-error` + chain / `no-report`);
- return the stage dict (mirror raws + times); disconnect on
  completion/failure; single-flight via `_io_lock`; log the resolved
  write UUID in the verdict line (FR-6).

### T5 — the service + diagnostic button (FR-5, AC-9)
- `services.yaml` + `strings.json`/translations: the
  `aqua_temp_ble.power_write_probe` service (schema follows the
  existing `probe_registers` precedent).
- `button.py`: the diagnostic "Power write probe" button (category
  `diagnostic` — the probe-registers precedent), routing to T4.
- Register the service in `__init__.py` alongside the existing ones.

## Block 4 — tests + no-regression

### T6 — the spec-016 test suite (AC-1..AC-10)
New tests (the 011-suite fake-client precedent):
- `tests/test_write_control_016.py`:
  - the four pinned frame vectors (CRC + length + shape);
  - the guard matrix (AC-2/3/4: proven/unproven × value allowlist ×
    1012 refused × base block unchanged);
  - the probe's five verdict paths (AC-5/6/7): scripted mirrors
    0→1→0, stuck-0, stuck-1, baseline≠0 (assert ZERO writes), att-error;
  - AC-8: exactly two writes per successful run, byte-equal to the
    pinned vectors;
  - AC-10: the climate still refuses writes (the 015 suite unchanged).
- Run the whole existing suite — NFR-1 (zero behavior change with the
  switch off).

## Block 5 — phase 2 (BLOCKED on the owner's live proof)

### T7 — the proven control surface (FR-8/FR-9, AC-11) — DO NOT MERGE
without the recorded verdict
- `climate.py` (push mode): replace `_refuse_write` for
  `async_set_hvac_mode` ONLY, behind `WRITE_CONTROL_PROVEN`:
  OFF → `async_write_register(1011, 0)`; any on-mode →
  `async_write_register(1011, 1)` (heat-only unit — no mode write);
  `_attr_hvac_modes` reflects the capability only when proven.
- FR-9: INFO per write + the next-refresh mirror cross-check
  (`power write not confirmed by mirror` WARNING on mismatch); never
  optimistic state (contract 016 §6).
- Tests: AC-11 (proven ON/OFF writes + the cross-check paths).
- **Merge precondition (contract 016 §4): the owner's probe verdict
  line, quoted in the PR. Until then this block stays a parked
  branch.**

## Verification (the whole spec)

```bash
python3 custom_components/aqua_temp_ble/modbus.py     # self-test incl. the 016 vectors
python3 -m pytest tests/ -q                          # the full suite (016 included)
```
The owner then runs quickstart §1 (the live gate) before T7 merges.