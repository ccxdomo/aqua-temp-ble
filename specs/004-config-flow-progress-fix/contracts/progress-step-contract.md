# Contract — Progress steps of the config flow (004)

Status: **PLANNED** — normative for the implementation. Amends
`specs/002-config-flow-scanning/contracts/config-flow-states.md` §2
(observable transitions stay identical; the *mechanism* of the two
progress steps changes). Anything not listed here is a bug.

Confidence legend: **[P]** proven (HA 2024.12.0 **and** 2026.10.1
sources / bleak 0.22.3 and 3.0.2 / this repo — proofs in
`../research.md`) · **[D]** decision of this spec.

---

## 1. Scope

The two progress steps of the flow: `test` (live connection test) and
`scan` (bounded rescan wait). Everything else keeps spec 002's
contract.

## 2. Result-type rule (the core invariant) **[P]**

A progress step is any step invoked while the flow's current result
type is `SHOW_PROGRESS`. Such a step may return **only**:

1. `async_show_progress(step_id=<own id>, progress_action=<own id>,
   progress_task=<task>, …)` — re-entering the same progress state
   (identical or new task), or
2. `async_show_progress_done(next_step_id=<existing step id>)` —
   leaving the progress state via the manager.

**Forbidden from a progress step** (HA raises
`ValueError: Show progress can only transition to show progress or
show progress done.` — `data_entry_flow.py`):
`async_show_form`, `async_show_menu`, `async_abort`,
`async_create_entry`, `async_external_step`. This includes returning
them by *calling another step* (`return await self.async_step_x()`).

`next_step_id` **must** name an existing `async_step_<id>` method
(the manager validates it). The manager — not the step — calls
`async_step_<next_step_id>(None)`.

## 3. Task lifecycle **[D — matter pattern]**

| phase | rule |
|---|---|
| entry, no task | create exactly one task via `self.hass.async_create_task(...)`, keep the reference on the handler (`_test_task` / `_scan_task`), return `async_show_progress` with it |
| re-entry, task running | return `async_show_progress` with the **same** task object (the manager no-ops on an unchanged task **[P]**) |
| re-entry, task done | read the outcome from the stash (`_test_error`), **clear the task reference**, return `async_show_progress_done(next_step_id=…)` |
| new attempt | task reference is `None` again → fresh task; `_test_error` is reset to `None` first |

Invariants:
- At most one task per progress step exists at any time.
- The task reference is cleared **before** returning
  `show_progress_done` (never inside a later branch).
- The task body (`_async_test`) never raises: every `Exception` is
  converted into an `_test_error` key; success sets it to `None`.
- The step never awaits the task on the done path (see
  `../research.md` §8 for why matter's `await` is deliberately not
  copied).

## 4. Routing table (test) **[D]**

| outcome of `_async_test` | `_origin` | `next_step_id` | next step renders |
|---|---|---|---|
| success (`_test_error is None`) | any | `confirm` | confirm form (`user_input=None`) |
| failure | `manual` | `manual` | manual form, `errors={"base": _test_error}`, stash cleared |
| failure | `device` or `user` (dormant bluetooth path) | `device` | device form, `errors={"base": _test_error}`, stash cleared; empty history → user menu and stash cleared silently |

Routing table (scan): completion → `next_step_id="user"` (always;
the wait cannot fail).

## 5. Error-stash rules (`_test_error`) **[D]**

- **Writers**: the test task (failure path / success reset) and the
  start of a new test attempt (reset to `None`).
- **Reader-consumer**: the first form render of `manual`/`device`
  entered with `user_input=None` **after** a routed failure — it
  reads, displays as `errors={"base": <key>}` and clears the stash.
- The `device` step's user-menu fallback (empty discovery history)
  clears the stash without displaying it (menus cannot show errors).
- Values are exactly the existing keys `cannot_connect`, `no_response`,
  `crc_error`. No new keys are introduced.
- The stash is never consulted on `confirm` (it is `None` there by
  construction).

## 6. Cancellation & abandonment **[P]**

- When the flow is removed (user closes the dialog → frontend deletes
  the flow, or the flow aborts/finishes), HA's manager cancels the
  stored progress task (`async_cancel_progress_task`). Our task's
  `finally` blocks then run best-effort (client disconnect attempt);
  `CancelledError` unwinds without any unretrieved-exception log.
- If the task completes *after* removal, HA's re-configure task hits
  `UnknownFlow`, which HA suppresses — nothing logs.
- Our handler holds the same task reference as the manager while the
  progress state is active; after routing we hold none. No leak.

## 7. BLE disconnect callback (`aqua_client._on_disconnect`) **[D]**

| situation at callback time | `self._client` | log | side effects |
|---|---|---|---|
| self-initiated disconnect (our `disconnect()` clears `_client` before awaiting bleak) | `None` | DEBUG `BLE connection to <addr> closed` | none (timer/pending already handled by `disconnect()`) |
| unexpected link loss | live client | WARNING `BLE connection to <addr> lost` (unchanged) | cancel silence timer + `_fail_pending(AquaTempConnectionError)` (unchanged) |

Applies to the config-flow test **and** to steady-state coordinator
polling (no test-mode special case).

## 8. What must not change

- Step ids, `progress_action` values (`test`, `scan`), the explicit
  `step_id=` kwarg in `async_show_progress` (the manager would
  backfill the *calling* step's id otherwise **[P]**), texts,
  translation keys, error wording, unique-id guards, entry data and
  options, the dormant `bluetooth` step's semantics, the 20 s test
  budget, and all BLE/Modbus logic beyond `_on_disconnect`.