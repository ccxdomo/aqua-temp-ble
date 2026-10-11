# Spec 013 tasks — ordered implementation blocks for the Coder

Work top-to-bottom; each block ends green (`pytest tests/` + the
modbus self-test). Normative refs: spec.md (FR/AC), plan.md (design),
contracts/push-harvest.md (rules), research.md (proofs),
data-model.md (mapping). Do not touch anything outside spec.md NFR-1's
file list. **Zero-TX invariant** (contract 013 §1): nothing you add may
ever transmit a frame — the grep gate in Block 7 enforces it.

## Block 0 — baseline (no code changes)

- [x] 0.1 `python3 custom_components/aqua_temp_ble/modbus.py` —
      self-test PASS as-is (the four pushed-frame vectors of §10 are
      this spec's decode oracles; output stays byte-identical at the
      end — NFR-4).
- [x] 0.2 `pytest tests/` — fully green on 1ac276d.
- [x] 0.3 Re-read research 013 §2 (the cartography), §5 (the code
      trace: census counts but discards), data-model §6 (the mapping
      you will transcribe) — every design choice below cites them.

## Block 1 — pushed-block store + full-hex capture (FR-1, AC-1)

- [x] 1.1 RED-first: `tests/test_push_store_013.py` NEW —
      - feed the four pinned frames (1001, 1046, 2046×2) + a mock
        2001 frame via fake notify → store holds the exact register
        tables (spot: 1021=1050, 1046=290, 1056=-70, 2046=32765,
        2047: 140 then 145 — latest-wins, count=2);
      - exactly 4 WARNING `block push captured:` lines (first per
        distinct start), full 198-hex present, repeat census lines
        byte-identical to the 008/009 grammar;
      - idle-window summary now WARNING (message byte-identical —
        re-pin the level in the updated assertion);
      - a 5×20-byte-chunked block frame arriving WHILE IDLE lands in
        the store (today's idle path would lose it — the boundary
        loop must move into the idle path).
- [x] 1.2 Implement per plan §2.1: `PushCapture` per-connect store
      (`{start: PushCapture}`), decode via `parse_block_response`
      (start/qty from the frame), resets in connect/disconnect with
      the census state; first-per-start WARNING hex line; idle
      summary level change; idle path gains the boundary loop
      (fragmented frames never lost). Strings byte-exact per
      contract 013 §2.

## Block 2 — listen-only harvest API (FR-2, AC-2)

- [x] 2.1 RED-first: `tests/test_harvest_013.py` NEW —
      - `harvest_pushes({2046}, 30)` with a scripted 2046 push at
        t+5 completes at t+5; the fake WRITE characteristic received
        ZERO writes (the zero-TX gate — AC-2's core);
      - no pushes → returns partials at the timeout, never raises;
      - all-four-starts default wanted set completes on the pinned
        frames.
- [x] 2.2 Implement per plan §2.1: `async harvest_pushes(wanted_starts,
      timeout)` — awaits store entries, no TX, partial results
      allowed; default `{1001, 1046, 2001, 2046}` (const-adjacent,
      comment cites research §5.4's ~16 s all-four observation).

## Block 3 — connection-test harvest stage (FR-3, AC-3)

- [x] 3.1 RED-first: `tests/test_flow_harvest_013.py` NEW (FakeProbeClient
      pattern of test_security_probe_011.py) —
      - world 1: first read times out, probe returns None, harvest
        sees 2046 ⇒ test SUCCEEDS; entry records mode "push"; the
        success WARNING summary lists starts + T-window raws;
      - world 2: no pushes at all ⇒ `no_response` failure,
        byte-identical message + key (012-pinned), stage label
        unchanged;
      - world 3: probe ANSWERS ⇒ harvest stage never runs (classic
        units untouched — US-6);
      - budget arithmetic: harvest timeout 30 s inside the 70 s
        budget (assert the constants, plan §2.2).
- [x] 3.2 Implement per plan §2.2: the `push-harvest` stage in
      `_async_test` (after probe-None, before the re-raise), stage
      label plumbing per the 007/011 conventions, WARNING evidence
      lines (contract §2.3), entry data `mode: "push"`, confirm-step
      push-mode description string (additive translation; existing
      keys untouched).

## Block 4 — coordinator push mode + diagnostic (FR-4/FR-6, AC-4)

- [x] 4.1 RED-first: `tests/test_coordinator_push_013.py` NEW —
      - push-mode cycle: ZERO `read_registers` calls (mock asserts),
        snapshot built from the store, fresh ⇒ values present;
      - stale store (faked clock past `max(2×interval, 60)`) ⇒
        `UpdateFailed("push stream silent")`;
      - classic mode: single-read cycle byte-identical (existing
        tests re-run green);
      - base answer arriving in push mode ⇒ WARNING anomaly log,
        otherwise ignored (contract §4.2).
- [x] 4.2 Implement per plan §2.3: mode branch in
      `_async_update_data`, `AquaTempData.from_pushes` (temps from
      mirrors, control keys None by design — contract §4.4,
      binaries empty), freshness gate, `push_stream` diagnostic
      sensor (state from freshness; attributes: per-start counts,
      last age, mode).

## Block 5 — pushed mapping + calibration gate + integrity (FR-5/FR-7, AC-5/AC-6)

- [x] 5.1 RED-first: `tests/test_registers_push_013.py` NEW —
      - `PUSHED_TEMPS`/mirrors transcribed from data-model §6;
        confirmed=False ⇒ push-only keys absent, base keys decode
        unknown in push mode; confirmed=True ⇒ keys present, 2047 →
        14.5 from the pinned vector;
      - sentinel 2046 → `unknown` (the vendor display bug must never
        replicate);
      - permuted-mapping test: swapping 2047/2048 in PUSHED_TEMPS
        swaps the sources (single source of truth);
      - integrity: duplicate pushed address raises; pushed address
        outside 2001-2090 raises; shipped registries pass.
- [x] 5.2 Implement per plan §2.4: registers constants
      (`PUSH_TEMP_BLOCK_START=2046`, `PUSHED_TEMPS`,
      `PUSH_TEMP_CONFIRMED=False`), `PUSHED_SENSORS` +
      `PUSHED_MIRRORS` registries, `check_registry_integrity`
      extension (per-plane windows/duplicates, mirror-pair rule),
      sensor-platform creation rules, `push_temp_confirmed` entry
      option (confirm-step checkbox, quickstart §2).

## Block 6 — user-visible honesty

- [x] 6.1 Push-mode UI note (confirm step): monitoring entities only,
      control/fault/firmware absent until the request path is revived
      (spec §6 wording — no promise of control).
- [x] 6.2 README/docs paragraph (durable, English): the two planes,
      push mode, the calibration gate, the 2001 capture.

## Block 7 — gates & ship

- [x] 7.1 `pytest tests/` fully green; `python3
      custom_components/aqua_temp_ble/modbus.py` self-test unchanged
      and passing (NFR-4).
- [x] 7.2 Zero-TX grep gate: the harvest/store/flow-stage/coordinator-
      push diff contains no `_write_frame`/`write_gatt_char`/
      `build_` call added on a harvest path (contract 013 §1.3).
- [x] 7.3 `git status` clean outside NFR-1's file list; version bump
      **0.9.0** (manifest + build marker together — 012 FR-3c
      pattern).
- [x] 7.4 Hand back to the Reviewer with: the AC checklist, the test
      names per block, and a note that the operator's next run
      (quickstart §1-§2) is the production proof of this spec.