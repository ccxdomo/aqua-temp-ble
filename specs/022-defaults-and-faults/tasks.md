# Spec 022 tasks — ordered for the Coder

Working order is the task order. Every task cites spec.md's FR/AC and
research.md's section. **Phase A ships ALL of it** (VOLET 1 complete +
FR-8 + the addendum FR-13 — the machine is faulted NOW and the passive
capture instrument must return). Phase B is the OWNER's live E03 run
(quickstart.md — not Coder work); Phase C is the follow-up spec bound
by `contracts/fault-evidence-contract.md`. The untouchables are
plan.md §2.3 — the value rails, allowlists, confirmation cycles,
heat-only rule, sentinels and pinned frames are byte-identical when
this file is done.

- [x] **T1** — registers.py: the four switches die, the guards stay
      (FR-3, FR-4, AC-4, AC-5)
      - DELETE with their comment blocks: `PUSH_TEMP_CONFIRMED`,
        `PUSH_CONTROL_CONFIRMED`, `WRITE_CONTROL_PROVEN`,
        `WRITE_SETPOINT_PROVEN` (plan §1.1).
      - KEEP byte-identical: `ALLOWED_WRITE_BASE` /
        `ALLOWED_WRITE_CONTROL` / `ALLOWED_WRITE_SETPOINT`,
        `SETPOINT_MIN_RAW` / `MAX_RAW` / `STEP_RAW`, the
        `WRITE_FRAME_*` / `WRITE_REG_*` pins, `PUSHED_TEMPS`,
        `PUSHED_CONTROL`, `PUSHED_MIRRORS`, sentinels, plausibility,
        `KNOWN_LABELS`, the integrity check.
      - `ensure_writable(address, value=None)` — the signature LOSES
        the `control_allowed` / `setpoint_allowed` kwargs; the control
        branch enforces only {0, 1}; the setpoint branch only the
        range/step rail; the locked-gate ValueError texts are REPLACED
        by value-discipline refusals only (plan §1.1's wording); the
        refusal for every other register is unchanged (spec-003
        message).
      - Docstrings: the funnel is the single write path; allowlists and
        value disciplines are permanent (contracts 003 §6 / 016 §4 /
        019 §2.2); the human-gate paragraph is removed.

- [x] **T2** — const.py: the shipped defaults die (FR-3, AC-5)
      - DELETE `DEFAULT_WRITE_CONTROL` and `DEFAULT_WRITE_SETPOINT`
        with their comment blocks. Nothing else changes.

- [x] **T3** — coordinator.py: gate properties + `from_pushes`
      (FR-3, AC-5, AC-6)
      - DELETE the four gate properties (`push_temp_confirmed`,
        `push_control_confirmed`, `write_control_allowed`,
        `write_setpoint_allowed`) and their docstrings.
      - `AquaTempData.from_pushes(cls, captures)`: the `confirmed` /
        `control_confirmed` parameters and gate branches go; the
        T-block always feeds `registers` (when the capture exists);
        the control decode always runs (2001 capture); the setpoint
        decode always reads the 1046-block (research 019 §1 rule);
        absent block → None, sentinel/implausible → unknown,
        heat-only display and unmapped-mode INFO unchanged.
      - DELETE the vestigial `push_confirmed` field (no consumer).
      - `from_pushes` call sites (push cycle, write confirmations,
        probe paths) drop their gate arguments.

- [x] **T4** — coordinator.py: the write cycles lose the gate threads
      (FR-3, FR-4)
      - `async_control_write_power` / `…_setpoint`: drop the
        `control_allowed=self.write_control_allowed` /
        `setpoint_allowed=self.write_setpoint_allowed` kwargs at the
        `ensure_writable` call sites. Everything else in the cycles is
        UNCHANGED (validate → funnel → write → trigger → fresh-mirror
        confirmation → verdict; the unconfirmed-OFF persistent
        notification stays).

- [x] **T5** — climate.py: the unconditional surface (FR-1, FR-3,
      AC-1)
      - `async_setup_entry`: push entries ALWAYS create the climate
        entity (the `push_control_confirmed` check and its debug log
        go); the `read_only` concept is DELETED.
      - `hvac_modes` → `[OFF, HEAT]`; `supported_features` →
        `TURN_ON`/`TURN_OFF` + `TARGET_TEMPERATURE` — unconditional.
      - Write methods: the gate checks go; the push branches always
        write through the confirmation cycles; the non-OFF/HEAT hvac
        refusal stays (heat-only rule, spec 018); `_refuse_write` is
        deleted (unreachable).
      - `extra_state_attributes`: the `write_control`/`write_setpoint`
        markers go; keep `last_power_write` / `last_setpoint_write`
        unconditionally.
      - The classic (non-push) entity path is untouched.

- [x] **T6** — climate.py: the inlet pin, test-only (FR-12, AC-16)
      [addendum 2026-10-12]
      - NO production change — the audit verdict (research Part 3.1):
        `current_temperature` already reads 2047 (push) / base reg 26
        (classic); ambient (2050) never feeds the climate.
      - ADD the regression test: a canned 2047 raw (145 → 14.5 °C,
        display-unit converted) decodes as `current_temperature`;
        sentinel/absent 2047 → None; a canned 2050 value NEVER appears
        as `current_temperature`.

- [x] **T7** — sensor.py: the unconditional sensors (FR-1, FR-3)
      - Push entries: create ALL six T-sensors + the `push_stream`
        diagnostic unconditionally (the `confirmed` branch and its
        debug logs go).
      - The push `native_value` decode is unchanged (PUSHED_TEMPS
        lookup, sentinel/plausibility via the shared helper).
      - fault_code / firmware: STILL not created in push mode
        (no proven home — FR-10).

- [x] **T8** — config_flow.py: `confirm_push` + the live placeholders
      (FR-2, FR-7, AC-2, AC-7)
      - `async_step_confirm_push`: schema is exactly
        `{vol.Required("name", default=DEFAULT_NAME): str}`; the entry
        keeps `data = {address, name, mode: "push"}`; `options` =
        `{poll_interval, temperature_unit}` ONLY.
      - `_async_push_harvest` stashes the harvest captures
        (`self._push_caps = caps`) on success; the new pure helper
        builds the placeholders: inlet/outlet/ambient = raws
        2047/2048/2050 from the 2046-capture, /10 one decimal,
        sentinel → `sensor absent`, absent → `unknown`; power = raw
        2011 from the 2001-capture → `off`/`on`; setpoint = raw 1046
        from the 1046-capture → `x.x °C`; NO mode line.

- [x] **T9** — config_flow.py: the options flow (FR-3, AC-3)
      - Push branch: `poll_interval` + `temperature_unit` ONLY — the
        four gate schema keys are deleted. Classic entries keep their
        unchanged form. The unused `DEFAULT_WRITE_*` imports go.

- [x] **T10** — strings.json + translations/en.json + fr.json
      (FR-6, AC-8)
      - The new `config.step.confirm_push` copy (plan §1.7, verbatim);
        the options step keeps only `poll_interval` +
        `temperature_unit` labels; the four gate labels and BOTH
        `data_description` entries are deleted; the three files keep
        exact key parity; no text mentions gates, checkboxes,
        calibration confirmation or "stays absent until the unit
        answers".

- [x] **T11** — README.md + docs/CALIBRATION.md (FR-6, AC-9)
      - README per plan §1.8: the gates section → "What you get";
        the gate qualifiers go from the features/entities tables; the
        troubleshooting row states the honest fault/firmware reason.
      - CALIBRATION.md §2 → "Verifying a new unit (optional)": the
        four verifications as confidence checks, no gates to open;
        §1's pointer updated to spec 022.

- [x] **T12** — registers.py: 2017/2018 join the candidates
      (FR-8, AC-11, AC-12)
      - `PUSH_CONTROL_CANDIDATES` gains:
        `2017: "unknown [V] — candidate, spec 022 fault hunt"` and
        `2018: "unknown [V] — candidate, spec 022 fault hunt"`.
        The push_stream attributes and the probe labels consume the
        dict automatically — no other code. Diagnostics only: no
        entity, no decode, no label claim.

- [x] **T13** — aqua_client.py: the hex returns to WARNING
      (FR-13, AC-17) [addendum 2026-10-12]
      - `_store_push_frame`, first-capture branch (`cap is None`):
        restore the contract 013 §2.1 line byte-exactly —
        `"block push captured: start=%d qty=%d len=%d crc=OK hex=%s"`
        with `frame.hex()` — and DELETE the DEBUG twin (plan §3.2's
        exact before/after).
      - The comment block is rewritten (013 restored by 022 FR-13; the
        021 C2 demotion put the evidence below the operator's WARNING+
        panel — contract 012 §2; the live E03 reload proved the loss;
        flood bounded ≤ 4 lines per connect, the flaky-link concern
        overruled by the owner).
      - `tests/test_push_store_013.py`: the C2-era assertions invert —
        the four WARNING lines REQUIRE `hex=` byte-exact against the
        canned frames; the DEBUG hex twin count becomes 0; the
        pinned-grammar header comment updated.

- [x] **T14** — the test suites, cross-cutting (AC-1, AC-4, AC-5,
      AC-6, AC-11, AC-16, AC-17)
      - The ~18 gate-touching suites (plan §1.10's list): closed-gate
        assertions removed or INVERTED to the unconditional world;
        gate-opening fixtures simplify to no-options fixtures;
        `ensure_writable` tests → value-rail refusals, no kwargs.
      - NEW minimal coverage: AC-2/AC-3 (entry + options shape),
        AC-5 (the symbol-absence grep over `custom_components/`),
        AC-7 (live placeholders with canned captures), AC-11 (2017/
        2018 in `control_candidates`), AC-16 (T6), AC-17 (T13).
      - The `modbus.py` self-test and every pinned-frame assertion
        stay green UNTOUCHED.

- [x] **T15** — quickstart.md: fold the addendum into the EXISTING
      owner protocol (FR-9, FR-14, AC-14, AC-18) [addendum 2026-10-12]
      - The file EXISTS (2026-10-12: the step-by-step protocol, the
        evidence checklist, the unexpected-cases ladder). Its §3
        prerequisites assume a healthy unit ("OFF and no fault
        displayed") — ADD the already-faulted variant: the unit is
        in E03 NOW, so capture the fault state FIRST (panel code +
        probe response), clear, then capture healthy; the healthy
        baselines are recorded (research Part 3.3).
      - The prioritized watch list verbatim (research Part 3.3): P1
        block 2001 (the ~39 zeros, 2012 bit-watch — bit 2 the
        WaterFlowSwitch slot [V], the 2034 state enum, 2017/2018
        wildcards, 2010 self-ref, 2011/2019 consequence plane); P2
        block 2046 (2047/2048 no-flow drift, the 2046 sentinel, the
        2052-2090 zero tail); P3/P4 blocks 1001/1046 (static, low
        prior).
      - The telling patterns (0→non-zero; new bits in 2012;
        E-number-shaped or pool-numeric values [V]; sentinel moves; a
        frozen register) and the non-hits (consequences, physics,
        mirror noise).
      - The instruments BY NAME: `aqua_temp_ble.probe_registers`
        block `base` with return-response (all four blocks, `count=`
        freshness tell), the `Push stream` diagnostic's
        `control_candidates` attributes, the WARNING hex (T13) on
        every reload.
      - The burst-flows-during-fault fact [P — the owner's four
        hex-less capture lines during E03] with the caveat: those
        values were lost to the C2 regression — re-capture.

---

## Phase B — the owner's live E03 run (NOT Coder work)

The unit is un-plumbed and in E03 NOW (2026-10-12). Follow
quickstart.md's already-faulted entry; record the panel code + the
two probe responses. Phase C (the follow-up spec) consumes the result
per `contracts/fault-evidence-contract.md`: a pinned fault home →
the binary_sensor "Fault" exposure; none found → the honest record
(optionally after the APK assets re-extraction — research §2.1-c).