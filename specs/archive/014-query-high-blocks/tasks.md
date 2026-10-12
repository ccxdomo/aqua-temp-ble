# Tasks 014 — implementation order for the Coder

Discipline: red-first tests for every behavioral change; every new
comment cites `research 014 §…` / `contract 014 §…` / `data-model 014
§…`; durable artifacts in English; `pytest tests/` fully green +
`python3 custom_components/aqua_temp_ble/modbus.py` self-test green
before finishing; `git status` clean outside spec 014 NFR-1's list
(spec.md §3). The ONLY production file touched is `coordinator.py`
(plus strings/README/manifest) — if the diff shows any other
production file, stop and re-read plan 014 §2.3.

## Block 1 — Read the evidence (no code)

- [x] 1.1 Read `specs/014-query-high-blocks/research.md` (the six-run
      response model), `spec.md` (FR/AC), `plan.md` §2 (the cycle
      sketch + `from_merged`), `contracts/query-cycle.md` (normative).
- [x] 1.2 Re-read `coordinator.py` `_async_update_push`,
      `AquaTempData.from_regs/from_pushes`, and
      `tests/test_coordinator_push_013.py` (the two assertions this
      spec re-pins — find the failure literal and the zero-`read_registers`
      assertion).

## Block 2 — The coordinator cycle (FR-1) — red-first

- [x] 2.1 Write `tests/test_coordinator_query_014.py` FIRST, red:
      AC-1 (timeout-trigger + store feed ⇒ `from_pushes` snapshot,
      exactly one `read_registers(7,45)` call, no other writes, INFO
      expected-timeout line asserted); AC-2 (no feed + faked short
      harvest ⇒ `UpdateFailed("push stream silent after trigger")` +
      disconnect); AC-3 (`AquaTempConnectionError` ⇒
      `UpdateFailed("trigger read failed: …")` + disconnect). Fake
      patterns + `SimpleNamespace` shape rules: the 013 suite's
      lessons (memory/2026-10-11-session.md).
- [x] 2.2 Implement the cycle in `coordinator.py` per plan 014 §2.1
      (trigger → revival check → listen → freshness → fail/snapshot),
      comments citing contract 014 §2/§3/§4. Keep the unchanged
      pieces byte-identical: the `pair_on_connect` threading, the
      `_log_push_mode_base_anomaly` call, the freshness window.
- [x] 2.3 AC-1's INFO line: `push cycle: trigger read timed out as
      expected (census: %s)` — the census rides verbatim in the
      exception message (contract 014 §7.1).

## Block 3 — The revival path + `from_merged` (FR-2) — red-first

- [x] 3.1 Extend the 014 suite, red: AC-4 (answered trigger ⇒
      `from_merged` snapshot: typed fields from the base vector; the
      confirmed T-block raws present in `registers`; unconfirmed ⇒
      withheld; the revival WARNING logged ONCE per session — second
      cycle does not re-log; mode `"push"` preserved) + the cycle-order
      guard (the revival branch runs BEFORE the freshness failure — an
      answered trigger with an empty store still succeeds).
- [x] 3.2 Implement `_log_trigger_answered_once()` (module-set
      pattern, contract 014 §5.1 literal) and
      `AquaTempData.from_merged` per plan 014 §2.2 (stdlib-only;
      disjoint-window merge; the honesty gate applies to pushed raws
      only).

## Block 4 — The 013-suite re-pins (FR-3)

- [x] 4.1 In `tests/test_coordinator_push_013.py`: re-pin the failure
      literal to `"push stream silent after trigger"` and INVERT the
      zero-`read_registers` assertion to "exactly ONE call per cycle
      with `(7, 45)`" — each with a one-line comment citing
      `contract 014 §4`. Every other assertion in the file stays
      untouched and green.
- [x] 4.2 Re-run the whole 013 suite: only the two re-pinned
      assertions changed meaning; anything else failing = a
      regression in the implementation, not the test.

## Block 5 — Strings / README honesty (FR-4)

- [x] 5.1 `strings.json` + `translations/en.json` +
      `translations/fr.json`: update the `confirm_push` description —
      drop "reads … without sending anything"; state that push mode
      sends one read request per poll cycle and reads the unit's block
      answer (plan 014 §2.4's EN draft; keep the same keys, update the
      content in both languages; no key added/removed).
- [x] 5.2 `README.md` push-mode section: the trigger sentence + the
      0.10.0 paragraph (behavior change summary, the new failure
      literal's meaning).

## Block 6 — Version + full gate (FR-6, AC-7)

- [x] 6.1 `manifest.json` → `0.10.0` (the flow's build marker picks
      it up automatically — verify with a test or the manifest-equality
      pin).
- [x] 6.2 `pytest tests/` fully green;
      `python3 custom_components/aqua_temp_ble/modbus.py` self-test
      green and byte-identical (no vector edits).
- [x] 6.3 `git status`: only `coordinator.py`,
      `tests/test_coordinator_query_014.py`,
      `tests/test_coordinator_push_013.py`, `strings.json`,
      `translations/en.json`, `translations/fr.json`, `README.md`,
      `manifest.json`, `specs/014-query-high-blocks/**` changed.

## Block 7 — Session capture (repo rule, AGENTS.md)

- [x] 7.1 Append the session note to `memory/2026-10-<current-date>
      -session.md` (create if absent): the 014 change (trigger+listen
      cycle, the two re-pins, `from_merged`), the evidence trail
      (research 014 §0), decisions + lessons.

## Verification checklist (maps to ACs)

| AC | Check |
|---|---|
| AC-1 | the 014 suite's AC-1 vector: one trigger, snapshot from the store, no UpdateFailed |
| AC-2/AC-3 | the failure literals exact; disconnect asserted |
| AC-4 | `from_merged` fields; log-once; gate respected |
| AC-5 | the two 013 re-pins commented + green |
| AC-6 | `git diff` EMPTY for `aqua_client.py`, `config_flow.py`, `registers.py`, `sensor.py`, `const.py`, `modbus.py`; classic/flow/store suites green unmodified |
| AC-7 | full pytest + self-test + manifest 0.10.0 + clean status |