# Spec 004 — Fix the config-flow progress steps (flow crashes when the test completes)

Status: **PLANNED** — written after a production incident on a live Home
Assistant instance. The Coder implements from this kit; the Reviewer
validates against the acceptance criteria.

Scope: the **progress steps** of the `custom_components/aqua_temp_ble`
config flow (`test`, `scan`) and **one** branch point in the BLE client's
disconnect callback. Nothing else changes. The flow's overall design
(steps, texts, unique-id rules, tiering) is spec 002's and is preserved
byte-for-byte in observable behaviour — this spec only repairs *how* the
progress steps hand control back to Home Assistant.

Confidence legend (same as specs 001–003):
- **[P] PROVEN** — verified in the HA sources at our minimum version
  **2024.12.0** AND at **2026.10.1** (the era the affected instance runs
  on), in bleak 0.22.3 / 3.0.2, or in this repo's reviewed code. Exact
  references in `research.md` §5.
- **[A] ASSUMED** — reasonable default, cheap to correct.
- **[V] TO-VERIFY** — needs a live HA instance / real unit; listed in
  `quickstart.md`.
- **[D] DECISION** — design choice (nothing external to verify).

---

## 1. Background — the production incident

On the owner's live instance (HA 2026.9/2026.10 era — `research.md` §6),
every attempt to add the heat pump produces **two log entries with
identical timestamps**:

1. `homeassistant` / `data_entry_flow.py:411` —
   `ValueError: Show progress can only transition to show progress or
   show progress done.` + `Error doing job: Task exception was never
   retrieved (task: None)`
2. `custom_components.aqua_temp_ble.aqua_client` / `aqua_client.py:259` —
   `BLE connection to 08:06:A6:C0:94:1C lost`

The integration's device is **never created**, even when the GATT
connection and the Modbus read succeed. The frontend dialog stays on the
progress spinner forever.

**Root cause [P]** — both log lines are symptoms of ONE event: the
progress task finishing.

- HA re-enters `async_step_test`/`async_step_scan` when the progress
  task completes, and from that re-entry the step may only return
  `async_show_progress(...)` or `async_show_progress_done(...)`.
  Our steps instead return a **form** (`confirm`/`device`/`manual`) or a
  **menu** (`user`) — HA raises the `ValueError`, the internal re-entry
  task dies with it ("Task exception was never retrieved"), and the flow
  stays stuck on the spinner. This happens on **every** test completion,
  **success or failure, BLE link lost or not**. It is the blocker.
- bleak fires the `disconnected_callback` on **any** disconnect
  transition, **including the clean disconnect `_async_test` itself
  performs** [P, bleak sources] — so `aqua_client.py:259` logs
  "connection lost" as a warning at the exact moment the test ends,
  perfectly correlated with the crash and deeply misleading. A genuine
  mid-read link drop produces the same warning (earlier, via
  `_fail_pending`) — the two cases are indistinguishable in today's logs.

The BLE connection dropping is therefore **not** what breaks the flow:
the drop (when real) is already handled cleanly as `cannot_connect`. The
flow engine crash is caused by our own illegal result types. Details,
proofs and line references: `research.md` §1–§4.

## 2. User stories

- **US-1** As a pool owner, when the connection test succeeds, I want
  the *Name your device* form to appear so I can finally create the
  entry (today the dialog hangs forever and the device is never
  created, even on a successful read).
- **US-2** As a pool owner, when the test fails (link drop, vendor app
  holding the unit, timeout), I want to return to the form I came from
  with the same clear error as designed in spec 002, so I can retry.
- **US-3** As a pool owner, I want *Scan again* to bring me back to the
  device menu after the wait (today every rescan crashes the flow the
  same way).
- **US-4** As the owner, I want the logs to tell the truth: our own
  clean disconnects must not be logged as `BLE connection … lost`
  warnings; a genuine unexpected drop must still warn and still fails
  the in-flight read immediately.
- **US-5** As the owner, I want at most one live connection test at a
  time (the unit accepts a single BLE connection).
- **US-6** As the developer, I want the flow's progress-step contract
  encoded in the walkthrough tests so this class of bug can never come
  back silently.

## 3. Functional requirements

- **FR-1** `async_step_test` re-entry (after the progress task
  completes) returns **only** `async_show_progress_done(next_step_id=…)`
  — `confirm` when the test succeeded, the origin form's step id
  (`manual` for origin `"manual"`, `device` otherwise, including the
  dormant bluetooth path) when it failed **[P — HA raises ValueError
  otherwise]**. It never returns a form/menu/abort/create-entry result
  directly.
- **FR-2** `async_step_scan` re-entry returns
  `async_show_progress_done(next_step_id="user")`. Same prohibition.
- **FR-3** Task lifecycle per progress step **[D, matter pattern]**:
  - the task is created once per attempt (`self._test_task`/
    `self._scan_task` is `None` → create and keep the reference);
  - while the task runs, every re-entry returns
    `async_show_progress(...)` with **the same task object** (HA's
    manager treats an unchanged task as a no-op — **[P]**);
  - on completion, the reference is cleared before routing, so the next
    attempt starts fresh;
  - `self._test_error` is reset when a **new** test starts.
- **FR-4** Failure routing behaviour is preserved from spec 002: the
  same error keys (`cannot_connect`, `no_response`, `crc_error`) are
  displayed on the same origin form. The error travels in
  `self._test_error`: the test task writes it; the target step
  (`manual`/`device`) reads **and clears** it when rendering its form
  with `user_input=None`; the `device` step clears it if it falls back
  to the user menu (a menu cannot display errors) **[D]**.
- **FR-5** `async_step_confirm` is unchanged; it is now reached through
  `next_step_id="confirm"` (HA calls it with `user_input=None` — same
  render as today's direct call).
- **FR-6** `aqua_client._on_disconnect` distinguishes self-initiated
  disconnects from unexpected link loss **[D]**: when
  `self._client is None` (cleared by our own `disconnect()` before
  bleak disconnects — **[P]**) → log at DEBUG ("connection closed") and
  return; otherwise keep today's behaviour exactly (WARNING
  "connection lost", cancel silence timer, fail pending). This also
  stops the per-poll-cycle "lost" warning in steady state.
- **FR-7** No behaviour change to: unique-id guards
  (`async_set_unique_id(..., raise_on_progress=False)` stays — see
  `research.md` Q4), the dormant `async_step_bluetooth` semantics, the
  options flow, entry data/options, error-key wording, and the
  20 s test budget.
- **FR-8** `tests/test_config_flow_walkthrough.py` is updated to drive
  the steps the way HA's manager does (task done → step re-entry →
  `progress_done` → next step), and asserts the contract: a progress
  re-entry result type is always `progress` or `progress_done`. The
  updated tests must **fail against the current code** before the fix
  (red-first) and pass after.
- **FR-9** All gates green: `python3 -m py_compile` on touched files;
  `test_config_flow_walkthrough.py`, `test_config_flow_list.py`,
  `test_modbus.py`, `test_reassembly.py`, `test_registers.py`.
- **FR-10** No new user-visible strings are required (no translation
  changes). Durable artifacts in English (repo rule).

## 4. Non-functional requirements

- **NFR-1 Minimal & targeted**: changes are confined to
  `config_flow.py` (two progress steps + the two error-stash
  consumption points + `__init__` state), `aqua_client.py`
  (`_on_disconnect` only — the callback branch point), and
  `tests/test_config_flow_walkthrough.py`.
- **NFR-2 Untouched**: `modbus.py`, `coordinator.py`, all entity
  platforms, `const.py`, `strings.json`, `translations/`, `manifest.json`,
  sentinels/write-guard (spec 003), scanning UX (spec 002).
- **NFR-3** No HA API newer than 2024.12.0 is used:
  `async_show_progress_done(next_step_id=…)` exists with identical
  semantics at 2024.12.0 and 2026.10.1 **[P]**.
- **NFR-4** The fix must not depend on the HA version: the contract is
  identical in every version checked (2024.12.0 → 2026.10.1) **[P]**.

## 5. Acceptance criteria

- **AC-1 (the blocker)** A successful test (connect + 45-register read)
  leads to the *confirm* form and an entry can be created end-to-end.
  No `ValueError`, no "Task exception was never retrieved", and no
  "BLE connection … lost" warning on the clean disconnect.
- **AC-2** A link drop during the read returns to the origin form with
  `cannot_connect`; the unexpected-drop WARNING still fires (genuine
  loss); no `ValueError`.
- **AC-3** A refused connect (vendor app holding the single connection)
  returns to the origin form with `cannot_connect`; no `ValueError`.
- **AC-4** A test that exceeds the 20 s budget routes with the same
  key as today (`cannot_connect` via `TimeoutError`); no `ValueError`.
- **AC-5** *Scan again* re-renders the user menu after
  `SCAN_WAIT_SECONDS`; no `ValueError`; repeated clicks harmless.
- **AC-6** Closing the dialog while the test runs leaves no
  unretrieved-exception log (the manager cancels the progress task;
  our `finally` best-effort disconnect runs).
- **AC-7** Rapid retry (test fails → resubmit the pick) starts exactly
  one new task; no duplicate concurrent tests.
- **AC-8** In steady state (coordinator polling), a clean per-cycle
  disconnect logs at DEBUG, not WARNING; a real drop still warns.
- **AC-9** The updated walkthrough tests encode the manager-faithful
  sequence, fail on the pre-fix code, and pass after; all other gates
  stay green.
- **AC-10** Unique-id / already-configured guards behave exactly as
  before (walkthrough covers the already-configured abort).

## 6. Edge cases

| # | Case | Behaviour |
|---|---|---|
| E-1 | Test succeeds | confirm form via `progress_done → confirm` (AC-1) |
| E-2 | Link lost mid-read | `_fail_pending` → `cannot_connect`; WARNING fires (unexpected drop); origin form |
| E-3 | Connect refused | `cannot_connect`; origin form |
| E-4 | 20 s budget expires | `TimeoutError` → `cannot_connect`; origin form |
| E-5 | User closes dialog mid-test | manager deletes the flow → progress task cancelled; `finally` disconnect best-effort; CancelledError unwinds silently (no unretrieved-exception log for cancelled tasks) |
| E-6 | Discovery history drained when routing back to `device` | device step falls back to the user menu and **clears** the stashed error (menus cannot show it) — same visible behaviour as today, now documented |
| E-7 | Re-entry while the task still runs (frontend refresh) **[A]** | same task object returned → manager no-ops **[P: `progress_task != flow.async_get_progress_task()` condition]** |
| E-8 | Rapid retry after failure | old task reference cleared on routing; new task created; stale `_test_error` reset at new-test start |
| E-9 | Flow deleted between task creation and completion | done-callback's `_async_configure` raises `UnknownFlow` → suppressed by HA's `call_configure` **[P]**; nothing logged |
| E-10 | Task ends cancelled, not exceptional | no re-entry happens (flow already gone); no "Task exception was never retrieved" (cancelled tasks are exempt) |

## 7. Out of scope

- Adding `config.progress.*` translation keys (the spinner label) —
  pre-existing gap, frontend rendering unverified **[V]**; the step
  title/description already display. Revisit separately if wanted.
- Any change to `async_step_bluetooth` activation, manifest matchers,
  list building, options flow, or the client's connect/read logic
  beyond `_on_disconnect`.
- Replacing the boolean task-done flags with anything fancier than the
  task reference itself (no events, no callbacks, no locks).