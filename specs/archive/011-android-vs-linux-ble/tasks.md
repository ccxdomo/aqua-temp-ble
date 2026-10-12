# Spec 011 — Tasks (ordered, for the Coder)

Status legend: blocks are ordered; land block-by-block, `python3 -m
pytest tests/ -q` green after each. Touch ONLY the files listed
(spec.md AC-7). `specs/001-010` are read-only. Normative details:
contracts/security-probe.md (log strings, taxonomy, budget) — copy
the exact strings from there, do not paraphrase.

Coordination: spec 010 may land before or after this spec (spec.md
FR-8). Do not couple the diffs: this spec never changes the resolver,
the write-mode logic or the session order; it only ADDS the
acknowledged-write seam, the pairing ladder, the probe stages and the
option.

## Block 1 — constants (spec.md FR-3/FR-6)

1. `custom_components/aqua_temp_ble/const.py`:
   - add `PAIR_TIMEOUT = 25.0` — seconds; bounds one D-Bus Pair
     attempt (contract §4; bleak docs advise generous pairing
     timeouts — research 011 §10.1). Comment cites
     `research 011 §10 / contract 011 §4`.
   - add `DEFAULT_PAIR_ON_CONNECT = False` — evidence-gated option
     (FR-6; quickstart §2 earns it).
   - DO NOT touch any existing constant (REQUEST_TIMEOUT, settle,
     re-send bounds — contract 009 §5 stays).
2. `manifest.json`: version `0.6.0` → `0.7.0`. Requirements stay
   `bleak>=0.22.0` / `bleak-retry-connector>=3.5.0` (the ladder
   capability-detects across API generations — research 011 §10.1).

## Block 2 — client transport seam (FR-1)

3. `aqua_client.py`:
   - `_write_frame(self, frame, *, force_response: bool = False)`:
     response policy becomes `response=True when force_response else
     self._write_with_response`. Byte payload untouched (AC-2).
   - `_request(..., force_response: bool = False)` threads it to
     `_write_frame`; `read_registers(..., force_response=False)` adds
     the kwarg (no other request kind exposes it — FC10 writes and
     query_blocks keep today's signatures).
   - In `_request`, wrap the `_write_frame` call: on
     `force_response=True` failures raise
     `AquaTempConnectionError(f"acknowledged write failed: {exc_chain(exc)}")`
     (the existing generic wrapper stays for the without-response path
     — two distinguishable messages, contract §5.1 depends on the
     chain text surviving).
   - Keep the `_write_frame` override seam test-friendly: tests that
     subclass and override `_write_frame` today must keep working
     (accept the new kwarg in signatures).
4. Pure verdict classifier (module-level function, unit-testable):
   `_classify_ack_write_failure(exc_chain_text: str) -> str` returning
   the verdict names of contract §5.1 (`att-error-security` /
   `att-error-permission` / `att-error-handle` / `local-failure` — the
   remaining §5.1 row, `att-accepted-silent`, is produced by the
   request-timeout path, not the classifier). Match the contract's
   string tables case-insensitively; unknown → `local-failure`.

## Block 3 — client pairing ladder (FR-3)

5. `aqua_client.py` — `async def pair(self) -> None`:
   - Rung A: `pair = getattr(self._client, "pair", None)`; if callable
     → `await asyncio.wait_for(pair(), const.PAIR_TIMEOUT)` (bleak
     <1.0 shape).
   - Rung B (only if rung A absent): disconnect cleanly; re-establish
     with pairing requested — `brc.establish_connection(..., pair=True)`
     after an `inspect.signature` capability check (log which
     backend/kwargs were accepted); raw fallback
     `BleakClient(device, pair=True)` when brc is absent (standalone
     path). Then re-run the existing post-connect setup (enumerate →
     notify → write-char → settle) exactly as `connect()` does —
     factor the post-connect block into a shared helper rather than
     duplicating it.
   - Rung C (agent fallback): module `agent.py` NEW under
     `custom_components/aqua_temp_ble/` — minimal `org.bluez.Agent1`
     (capability `NoInputNoOutput`; `Release`, `RequestPinCode`,
     `DisplayPinCode`, `RequestPasskey`, `DisplayPasskey`,
     `RequestConfirmation`/`RequestAuthorization` auto-approve,
     `AuthorizeService` auto-approve) over `dbus_fast.aio.MessageBus`;
     `register_agent()` / `unregister_agent()` context helpers;
     guarded imports (ImportError → rung C unavailable, DEBUG log).
     Trigger: pair-failure chains matching contract §5.2's agent
     signatures; retry rung A once after registering; always
     unregister in `finally`.
   - Bond-state logging (best-effort, contract §3): read the BlueZ
     `Paired` property via the backend's device object path when
     reachable (`getattr(self._client, "_backend", None)` →
     `_device_path` introspection wrapped in try/except; DEBUG on
     failure). Log `pairing: bond state before/after: …`.
   - Log every rung per contract §3; on success also
     `pairing: succeeded`; on `AlreadyExists`-class outcome
     `pairing: skipped: already bonded`.
6. `connect(..., pair: bool = False)`: thread `pair=True` into
   brc/raw establish calls (used by FR-6's option and rung B).

## Block 4 — config-flow probe stages (FR-2, FR-2.1, FR-4)

7. `config_flow.py` — `_async_test` state machine per contract §2:
   - stage labels: `security-probe`, `pair`, `first-read-retry`
     (007-style: stamp BEFORE the stage runs; failure lines report the
     running stage).
   - on `AquaTempTimeoutError` of the first read: probe
     `read_registers(BASE_BLOCK_START, BASE_BLOCK_COUNT,
     force_response=True)`; classify via Block 2's function; log the
     verdict line (contract §3.2).
   - `att-error-security` → `await client.pair()` → on success/skipped
     exactly one retry read (FR-4); log `answered after pairing` /
     `still silent after pairing` (contract §3.4).
   - other verdicts → log + route; NO pair attempt.
   - budget: keep the flow-level `asyncio.timeout(TEST_TIMEOUT)`; add
     the inner `PAIR_TIMEOUT` bound on the pair stage (contract §4
     arithmetic; AC-5).
   - UI keys unchanged (FR-2.5): every failure still maps to
     `no_response`/`cannot_connect`; the verdicts are log-only.
   - FR-2.1: the test progress step accepts a force-pair re-entry
     (`self._force_pair` stash set from the menu option) → when set,
     run the pair + retry stages even if the first read succeeded
     (X3 persistence retest, plan.md Phase 2.3).
8. Success-path non-regression (AC-4): a first-read SUCCESS runs no
   probe, no pair, emits none of the new INFO lines.

## Block 5 — the option (FR-6, FR-7)

9. `config_flow.py` options flow: boolean `pair_on_connect`
   (default `DEFAULT_PAIR_ON_CONNECT`); one-line EN description in
   `strings.json` + `translations/en.json` stating the evidence
   contract ("enable only after the connection test proved pairing
   was required" — quickstart §2).
10. `coordinator.py`: pass `pair=entry.options.get("pair_on_connect",
    False)` into `AquaTempBleClient.connect` (constructor or connect
   kwarg — pick one, keep the standalone default OFF).
11. Option OFF ⇒ zero behavioral delta on the poll path (AC-4 asserts
    no `pair()` call and no `force_response` write in a full fake
    poll cycle).

## Block 6 — tests (AC-1..AC-5, AC-8)

12. `tests/test_security_probe_011.py` (fake-client harness like the
    existing walkthrough tests):
    - classifier table (every contract §5.1 row incl. the BlueZ
      "Not paired" string; unknown → local-failure);
    - byte-equality: probe frame == `build_read_holding_registers(7,45)`
      == `63030007002d3c54`; `force_response` is the only seam delta;
    - ladder: rung A fake (has pair), rung B fake (no method; assert
      reconnect-with-pair attempted), rung C trigger on agent-signature
      failure (fake bus registration), AlreadyExists → skipped;
    - stage flow: timeout → probe → security verdict → pair success →
      retry answered (log lines asserted by exact prefix); retry
      still-silent path; non-security verdict skips pair (assert);
    - success path: first read answers ⇒ NO probe/pair calls (AC-4);
    - budget: stalled fake hits the flow timeout → cannot_connect
      (AC-5);
    - option gating (AC-4/FR-7);
    - import smoke: `python3 -c "import aqua_client"` standalone;
      guarded dbus_fast import (monkeypatch ImportError → rung C
      reports unavailable, no crash).
13. Regressions: full `pytest tests/` green; census log formats
    byte-identical (the 009 greps still match).

## Block 7 — probe-tool parity (FR-9, P2)

14. `tools/session_probe.py`: `--phase security` = acknowledged base
    read + pair ladder + retry, printing the same verdict strings
    (contract §3) — a developer with a terminal reproduces the HA test
    exactly. Reuse the client's seams; no logic duplication (import
    the client, don't copy it).

## Block 8 — docs & hand-off (FR-6, AC-7)

15. `README.md`: replace the next-steps paragraph with the pointer to
    `specs/011-android-vs-linux-ble/quickstart.md` (two sentences).
16. Completion report must include: the quickstart §0 grep list, the
    §5 report template, and the instruction that the operator's next
    run happens on THIS build with the log lines pasted back.

## Verification checklist (all blocks)

- [x] `grep -rn "force_response" custom_components/` — only the
      seam, `_request`, `read_registers` and the config test.
- [x] `grep -rn "pair" custom_components/aqua_temp_ble/aqua_client.py`
      — ladder + connect threading only; no pair call in coordinator
      polling path.
- [x] Frame bytes: probe path contains no hand-written frame constant
      (only `build_read_holding_registers`).
- [x] All new log lines match contracts/security-probe.md §3 verbatim.
- [x] `python3 -m pytest tests/ -q` green (197 passed: 161 baseline +
      34 spec-011 + 2 probe-tool tests);
      `python3 custom_components/aqua_temp_ble/modbus.py` self-test
      unchanged and passing.
- [x] specs 001-010 untouched; no new requirements lines; every new
      comment cites research 011 §…/contract 011 §….
      EXCEPTION (task perimeter, 2026-10-11 Coder run): manifest.json
      stays 0.6.0 — the version bump to 0.7.0 (AC-6) was OUTSIDE the
      authorized file list for this run and is deferred to the next
      landing; strings.json/translations/en.json (the option's EN
      description) and coordinator.py (the option's production
      threading) are deferred for the same reason — the option is
      stored in entry.options and the client supports connect(pair=True)
      already, so the follow-up is a one-line threading change.