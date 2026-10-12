# Data model 004 — Flow state of the progress steps

Status: **PLANNED**. Describes the flow handler's state after this
spec: attributes, their writers/readers, and the transitions around
the two progress steps. Normative rules: `contracts/progress-step-contract.md`.

---

## 1. Instance state (delta vs spec 002)

| attr | before (bf66431) | after | notes |
|---|---|---|---|
| `_test_done` | `bool`, set by the test task's `finally` | **deleted** | the task reference is the done-flag; a stale task could set the boolean late and desynchronize routing |
| `_scan_done` | `bool`, set by the wait task's `finally` | **deleted** | same |
| `_test_task` | — | `asyncio.Task \| None` | created once per attempt by `async_step_test`; cleared on routing; `.done()` drives the branch |
| `_scan_task` | — | `asyncio.Task \| None` | same in `async_step_scan` |
| `_test_error` | `str \| None`, written by the task, consumed by `async_step_test` re-entry | same type, **consumed by the target form step**; additionally **reset to `None` when a new test starts**, cleared by `device`'s menu fallback | transport between the task and the origin form |
| `_address`, `_name`, `_regs`, `_origin`, `_device_labels` | — | unchanged | spec 002 §3 |

Invariants:
- `_test_task is None` ⇔ no test attempt is in flight **or** the last
  attempt has been routed (cleared on routing).
- `_test_error is not None` ⇒ a failed test was routed and its error
  has not yet been displayed; the next form render of `manual`/`device`
  consumes it; a new test start resets it.
- At most one task per progress step exists at any time (the unit
  accepts a single BLE connection).

## 2. Error-stash lifecycle (`_test_error`)

```
                 (new attempt starts)
                        │  _test_error = None
                        ▼
              ┌─── test task runs ───┐
              │  success: = None     │  failure: = <error key>
              └──────────┬───────────┘
                         ▼ task completes → HA re-enters async_step_test
              read (no clear) to choose next_step_id
                         ▼
        confirm (success)        manual/device (failure)
        (stash is None anyway)   form renders → read AND clear
                                 └─ device falls back to the user
                                    menu → clear without display
```

Single writer (the test task, then the reset at new-test start), single
reader-consumer (the first form render after routing). The value is
always one of the existing error keys: `cannot_connect`, `no_response`,
`crc_error` (never `invalid_address` — that one comes from MAC
validation and is unaffected).

## 3. State machine around the progress steps (after the fix)

Notation: `SP` = `async_show_progress`, `SPD` =
`async_show_progress_done`; `Mgr` = HA's flow manager (not our code).

```
device form (submit pick) ─┐
manual form (valid MAC)  ──┼─► async_step_test (entry):
bluetooth (dormant) ───────┘    _test_task = create(_async_test)
                                return SP(test, task=T)
                                        │
                 Mgr stores T, adds done-callback on T
                                        │
                ┌── re-entry while T running (refresh) ──┐
                │        return SP(test, task=T)           │ (same T → Mgr no-op)
                └───────────────────────────────────────────┘
                                        │ T completes
                                 Mgr re-enters async_step_test(None)
                                        │
                    ┌─── _test_error is None ───┬─── else ───┐
                    ▼                            ▼            │
          SPD(next_step_id="confirm")   _origin == "manual"?    │
                    │                     │            │        │
                    │             SPD("manual")   SPD("device")│
                    │                     │            │        │
                    └─────────────────────┴────────────┴────────┘
                                        │ Mgr while-loop: calls async_step_<next>(None)
                    ┌────────────────────┴───────────────────────┐
                    ▼                          ▼                 ▼
          async_step_confirm(None)   async_step_manual(None)  async_step_device(None)
          confirm form               manual form + errors     device form + errors
          (entry on submit)           (stash consumed)         (stash consumed;
                                                                empty history → user
                                                                menu, stash cleared)

scan (menu pick) ► async_step_scan (entry):
        _scan_task = create(sleep(SCAN_WAIT_SECONDS)); return SP(scan, task=T)
                │ T completes → Mgr re-enters async_step_scan(None)
                ▼
        SPD(next_step_id="user") → Mgr → async_step_user(None) → menu
```

Flow exits handled by HA, not by us: flow removed/aborted →
`Mgr` cancels the stored progress task (E-5); the done-callback's
re-configure after removal raises `UnknownFlow` which HA suppresses
(E-9).

## 4. Result-type legality (what the manager accepts)

| current step type | allowed result types | anything else |
|---|---|---|
| `progress` | `progress` (same or new task) · `progress_done` | **ValueError** (data_entry_flow.py:411 @ 2026.10.1, :438 @ 2024.12.0) — form, menu, abort, create-entry all illegal |
| `progress_done` (transient, manager-driven) | anything a normal step may return | validated as a normal step |
| any non-progress step | anything a normal step may return | — |

This table is the contract the walkthrough tests assert
(`tests`, `plan.md` §5).