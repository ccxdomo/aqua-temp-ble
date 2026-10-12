# Spec 017 tasks — ordered for the Coder

Working order is the task order. Every task cites spec.md's FR/AC
and research.md's section. **The phase-2 gate is OPEN** (the proof
is recorded — research §1); the code still ships INERT by default
(the owner's activation — contract 017 §2) and the no-regression
suite proves it (T5).

The parked **T7 of spec 016 is superseded by this spec** (its
"BLOCKED on the owner's live probe verdict" precondition is
satisfied — research §1 records it): do NOT resurrect 016's tasks;
implement 017 below.

- [x] **T1** — the activation surfaces (FR-1, AC-3/4/5)
      - `const.py`: `DEFAULT_WRITE_CONTROL = False`.
      - `config_flow.py` (the OptionsFlow, the `push_control_confirmed`
        precedent): the `write_control` checkbox (default False) in
        the schema + the save merge; `strings.json` + translations
        (en/fr): label "Allow power control (write)", description
        naming the required verdict line (`power write probe:
        EXECUTED + REVERSIBLE …` — contract 016 §4's discipline,
        surfaced to the UI).
      - `coordinator.py`: the property `write_control_allowed` (the
        `push_temp_confirmed` pattern, ONE line):
        `registers.WRITE_CONTROL_PROVEN or bool(self.config_entry.
        options.get("write_control", False))`.
      - Tests: both activation paths (option True → allowed;
        registers switch True (test-patched) → allowed; both False
        → locked).

- [x] **T2** — the control send (FR-2, AC-1/2, NFR-2)
      - `aqua_client.py`: `async write_control(self, address: int,
        value: int) -> None` — the rail (address ==
        `registers.WRITE_REG_POWER` and value in {0, 1}, else
        ValueError BEFORE any byte, message naming the rule — the
        `write_probe_frame` rail precedent); frame =
        `modbus.build_write_control(address, value)`; send
        fire-and-forget via the SAME `_write_frame` seam
        (`not self.is_connected` → `AquaTempConnectionError`,
        the existing precondition). NEVER `write_registers` (the
        echo trap — research §6, contract 017 §4). The probe's
        seam is untouched.
      - `modbus.py` self-test (the §10 precedent): assert
        `build_write_registers(1011, [1]) == build_write_control(
        1011, 1) == bytes.fromhex(registers.WRITE_FRAME_POWER_ON)`
        and the 0/OFF twin (AC-1's cross-check — one source of
        truth).
      - Tests: the rail refuses `(1011, 2)` and `(1012, 1)` with
        ZERO wire writes (the RecordingBleakClient fake); the
        accepted `(1011, 0|1)` record EXACTLY the pinned frame
        bytes, withoutResponse (the 016 suite's fake-bleak
        pattern).

- [x] **T3** — the confirmation cycle (FR-3/FR-4, AC-6/7/8/9,
      NFR-3)
      - `coordinator.py`:
        1. Refactor `_probe_trigger` → `async _trigger_report(
           context: str)` — a PURE parameterization (the probe
           calls it with "power write probe"; its log lines keep
           their exact strings so the 016 suite passes unchanged).
        2. `async_control_write_power(self, value: int) ->
           dict[str, Any]` — FR-3's exact order:
           `ensure_writable(WRITE_REG_POWER, value)` first →
           connect if needed (the pair option) → audit INFO
           (`power write via 1011 = <v> — mirror should follow in
           the next report`) → under `_io_lock`: `client.
           write_control(1011, value)`, `t_write = _now()`, ONE
           `_trigger_report("power write confirm")`,
           `await self._wait_fresh_control(t_write)` (the probe's
           helper, UNCHANGED) → the three verdicts (FR-4's exact
           strings) → on confirmed/mismatch: snapshot
           `AquaTempData.from_pushes(dict(self.client.pushed_blocks),
           self.push_temp_confirmed, self.push_control_confirmed)`
           → `await self.async_set_updated_data(data)`; on
           no-report: NO snapshot push → the unconfirmed-OFF
           persistent notification (`persistent_notification.
           async_create`, id
           `aqua_temp_ble_power_unconfirmed_<address>`, FR-3
           step 7's message; a later confirmed cycle
           `async_dismiss`es it) → NO disconnect on completion →
           return the stage dict (the written value, the capture's
           raws + relative times, the verdict).
        3. `async_write_register` and `_async_update_push` are
           UNCHANGED (NFR-3 — the poll cycle is not touched).
      - Tests (the 016-suite fake-client precedent): confirmed
        (fresh capture `last_seen > t_write` → INFO verdict + the
        snapshot pushed — assert the coordinator data reflects the
        MIRROR); a STALE capture never satisfies the wait (AC-6's
        freshness gate); mismatch (WARNING names both values; the
        tile shows the mirror's state); no-report (WARNING; NO
        data change; for value==0 the notification is created and
        a later confirmed cycle dismisses it); write-raise (the
        error propagates; ZERO post-write reads on the fake).

- [x] **T4** — the climate wiring (FR-5/FR-6/FR-8, AC-3/4)
      - `climate.py` (push mode only; the classic entity
        byte-identical):
        - when `coordinator.write_control_allowed`:
          `_attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]`; the
          turn-on/turn-off features per the installed HA climate
          API; `async_turn_on` → `async_control_write_power(1)`;
          `async_turn_off` → `(0)`; `async_set_hvac_mode(OFF|HEAT)`
          → `(0|1)`, any other mode → `HomeAssistantError`;
          `async_set_temperature` keeps `_refuse_write`;
          `TARGET_TEMPERATURE` is never advertised;
          `extra_state_attributes`: `write_control`
          ("allowed"/"locked") + the last verdict dict.
        - when NOT allowed: the 015 read-only surface
          byte-identical (`hvac_modes=[]`, `_refuse_write`'s exact
          message — the 015 suite passes unchanged).
        - `hvac_action` UNCHANGED (OFF when power False, None
          otherwise — FR-5).
      - Tests: the AC-4 matrix (option on → hvac_modes, the exact
        pinned frame bytes recorded per command, COOL/AUTO raise,
        set_temperature raises); AC-3 (option off → the 015
        behavior).

- [x] **T5** — the spec-017 suite + no-regression (AC-1..AC-11)
      - New `tests/test_climate_control_017.py` (the fake-module +
        RecordingBleakClient precedent of the 016 suite): the full
        AC matrix of T2-T4.
      - The FULL existing suite must pass with both activation
        surfaces off (AC-10/AC-3): the 015 suite (the read-only
        climate), the 016 suite (the guard defaults, the probe —
        including after T3's trigger refactor), the 013/014
        suites (the poll cycle — NFR-3).

- [x] **T6** — release + handoff
      - `manifest.json`: version `0.13.0` (a new capability
        surface).
      - The self-test (`python3 custom_components/aqua_temp_ble/
        modbus.py`) green with the new parity vectors.
      - The handoff note to the owner: quickstart 017 §1 (enable)
        + §2 (the live verification = AC-12 — the shipped surface's
        first live proof; record the observation in this spec's
        research §1 addendum or the PR description).

## Verification (the whole spec)

```bash
python3 custom_components/aqua_temp_ble/modbus.py     # self-test incl. AC-1's parity vectors
python3 -m pytest tests/ -q                          # the full suite (017 included, zero regression)
```

The owner then runs quickstart §1 (enable) + §2 (the live HEAT/OFF
verification — AC-12). Until §2's observation is recorded, the
feature is "enabled but unverified" (contract 017 §8).