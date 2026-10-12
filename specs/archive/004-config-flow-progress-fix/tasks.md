# Tasks 004 — Ordered implementation for the Coder

Work top-down; each block ends with its own verification. Do not
touch anything outside the files listed in NFR-1 (spec.md). All durable
artifacts (code comments included) in English.

Status legend: ☐ open · ☑ done (specs 001-003 convention). Coder
session 2026-10-11: items 1-14 done; item 15 (live QA) is the
orchestrator's scope and stays open.

---

## Block 1 — Tests first (red) — `tests/test_config_flow_walkthrough.py`

- ☑ 1. Add to the stub `FlowHandler` an `async_show_progress_done(self, *,
  next_step_id)` helper returning `{"type": "progress_done",
  "step_id": next_step_id, "handler": <domain>}` (mirror of HA's
  result shape — research.md §3.3).
- ☑ 2. Add the shared driver `_drive_progress(flow, result)` (plan.md §5):
  await `result["progress_task"]`, re-enter
  `async_step_{result["step_id"]}(None)`, assert the result type is
  `"progress"` or `"progress_done"` (the contract), and if
  `progress_done` advance to `async_step_{result["step_id"]}(None)`.
- ☑ 3. Rewrite the progress scenarios with the driver:
  - happy path: `device → test → progress_done(confirm) → confirm
    form → submit → entry` (assert probe placeholders, entry data,
    unique-id upper-casing as today);
  - link-drop / no-response / crc_error failures from `device` and
    from `manual` origins: re-entry yields `progress_done` with the
    right `next_step_id`; driving the next step renders the form with
    `errors == {"base": <key>}` **and clears the stash** (a second
    render of that step shows no error);
  - scan: `progress_done(step_id="user")` → user menu with fresh
    counts;
  - dormant bluetooth path: failure routes to the `device` step.
- ☑ 4. Keep the existing non-progress assertions (menu labels, counts,
  tiering, already-configured abort, adapter-less abort) untouched.

**Verify**: `python3 tests/test_config_flow_walkthrough.py` —
progress scenarios FAIL (re-entry returns `form`), everything else
passes. This red run reproduces the incident (quickstart §A).
**Result 2026-10-11**: red run confirmed — 9/13 failed exactly at the
contract assertion ("progress re-entry ... got 'form'/'menu'"), 4/13
non-progress scenarios passed. Two extra scenarios added beyond the
brief: E-7 same-task re-entry and E-6 drained-history stash drop
(plus a crc_error origin-form case).

## Block 2 — `async_step_test` (the blocker) — `config_flow.py`

- ☑ 5. `__init__`: replace `_test_done`/`_scan_done` with
  `_test_task: asyncio.Task | None = None` and `_scan_task:
  asyncio.Task | None = None` (delete the booleans everywhere).
- ☑ 6. Rewrite `async_step_test` per plan.md §3.1: create-once →
  show-progress-while-running (same task) → on done: clear the
  reference, route `async_show_progress_done(next_step_id=…)` using
  the table in contracts/progress-step-contract.md §4. Keep
  `step_id="test"`, `progress_action="test"`,
  `description_placeholders={"address": …}`.
- ☑ 7. Turn `_async_test` into a bound method; body unchanged except:
  delete the `finally: self._test_done = True` (keep the inner
  `finally: await client.disconnect()` and the whole try/except
  ladder byte-identical).

**Verify**: walkthrough happy path + failure scenarios green.
**Result 2026-10-11**: walkthrough 13/13 after the fix.

## Block 3 — `async_step_scan` — `config_flow.py`

- ☑ 8. Same pattern (plan.md §3.2): `_scan_task` created once
  (`asyncio.sleep(SCAN_WAIT_SECONDS)` is the task body — the
  `_async_wait` closure is deleted), progress while running, on done:
  clear + `async_show_progress_done(next_step_id="user")`.

**Verify**: walkthrough scan scenario green; repeated `scan` entries
harmless.
**Result 2026-10-11**: scan scenario green (fresh-counts menu + E-11
second scan); the same-task invariant is covered by the dedicated
E-7 test for `test` (the `scan` step shares the identical pattern).

## Block 4 — Error stash consumption — `config_flow.py`

- ☑ 9. `async_step_manual` / `async_step_device`: on the form-render path
  (plan.md §3.3), read-and-clear `self._test_error` into
  `errors={"base": …}` when entering with `user_input=None`; the
  `device` step clears the stash on its user-menu fallback (a menu
  cannot display it). Keep the `errors` kwarg signatures; internal
  routing no longer passes `errors=`.
- ☑ 10. `async_step_test` first-entry resets `self._test_error = None`
  (a new attempt starts clean).

**Verify**: failure scenarios show the right error once, then clear;
happy path shows no stale error.
**Result 2026-10-11**: second-render assertions in the failure
scenarios (device / manual / crc_error / dormant bluetooth) prove the
read-and-clear; the E-6 drained-history scenario proves the menu
fallback drops the stash.

## Block 5 — `_on_disconnect` guard — `aqua_client.py`

- ☑ 11. Only the callback branch point (plan.md §3.4 / contract §7):
  `self._client is None` → DEBUG `BLE connection to %s closed` +
  return; else keep the WARNING + `_cancel_silence_timer()` +
  `_fail_pending(AquaTempConnectionError(…))` exactly as today.
  No other line of aqua_client.py / modbus.py changes.

**Verify**: existing unit tests still pass (no client behaviour tests
assert the warning on clean disconnects — if one does, update it to
the new DEBUG expectation and note it in the PR description).
**Result 2026-10-11**: no test asserted the warning (grep over
tests/); all five suites green; the diff is confined to
`_on_disconnect` (docstring + the one guard branch).

## Block 6 — Gates & review

- ☑ 12. `python3 -m py_compile` on `config_flow.py` + `aqua_client.py`.
- ☑ 13. Run all five suites (quickstart §D) — everything green.
- ☑ 14. Self-review checklist:
  - [x] No step returns form/menu/abort/create-entry from a progress
    re-entry (grep the two steps for `return await self.async_step_`
    inside the done-branch — there must be none).
  - [x] `_test_task`/`_scan_task` cleared before every
    `async_show_progress_done` return.
  - [x] Same task object returned while running (no re-creation).
  - [x] `step_id=` explicit in every `async_show_progress` call.
  - [x] Error keys unchanged; no translation files touched; no
    changes to coordinator/entity/sentinel code (spec 003).
  - [x] English-only comments/docstrings; no credentials; no debug
    prints left.
- ☐ 15. Manual QA on the live instance by the orchestrator (quickstart §C,
  V-1..V-5) — tracked separately from the Coder's scope.

**Result 2026-10-11 (gates)**: py_compile OK; modbus.py self-test OK;
test_registers.py 11/11; test_modbus.py 11/11; test_reassembly.py
15/15; test_config_flow_list.py 6/6; test_config_flow_walkthrough.py
13/13 (10 pre-existing scenarios, none deleted, 3 added). Working
tree touches exactly config_flow.py, aqua_client.py and
test_config_flow_walkthrough.py (git status); specs 001-003 code
untouched.