# Research 004 — The `async_show_progress` contract, verified in Home Assistant source

Status: **VERIFIED** — every HA API this spec relies on was verified
against the exact sources at our minimum version **tag 2024.12.0** AND at
**tag 2026.10.1** (the era of the affected production instance), plus
bleak **0.22.3** (pinned by HA 2024.12.0) and **3.0.2** (pinned by HA
2026.10.1), plus the matter integration at 2024.12.0 as the canonical
pattern. Sources were fetched and read during spec writing
(2026-10-11); file/line references below. Anything not covered here is
labelled **[H] HYPOTHESIS** or **[V] TO-VERIFY**.

Legend: **[P] PROVEN** (read in the sources, reference below) ·
**[H] HYPOTHESIS** (plausible, not fully verifiable statically) ·
**[D] DECISION** (design choice) · **[V] TO-VERIFY** (needs the live
instance / real unit — checklist in `quickstart.md`).

---

## 1. The incident, decoded

Production logs (owner's instance, 3 occurrences at each of two
timestamps — i.e. one pair per attempt):

```
ERROR   homeassistant — data_entry_flow.py:411
        ValueError: Show progress can only transition to show progress
        or show progress done.
        Error doing job: Task exception was never retrieved (task: None)
WARNING custom_components.aqua_temp_ble.aqua_client — aqua_client.py:259
        BLE connection to 08:06:A6:C0:94:1C lost
```

Both lines are produced by **one event**: the progress task of
`async_step_test` finishing.

- The **ValueError** is HA punishing an illegal transition: our
  `async_step_test`, re-entered by the manager after the task completes,
  returns a **form** (`confirm` / `device` / `manual`) instead of
  `async_show_progress` / `async_show_progress_done` **[P §3.2]**.
- The **"Task exception was never retrieved (task: None)"** is the same
  ValueError: it is raised inside HA's internal re-entry task, whose
  exception nobody retrieves **[P §3.3 + §6]**.
- The **"BLE connection … lost"** warning fires because bleak calls the
  disconnected callback on **any** disconnect — **including the clean
  `client.disconnect()` that `_async_test` itself runs in its
  `finally`** **[P §5]**. It fires at the exact moment the test task
  ends, hence the perfect timestamp correlation. A genuine mid-read
  drop fires it earlier (via `_fail_pending`) and produces the *same*
  final pair of lines; today's logs cannot distinguish the two cases.
- **Consequence**: the flow's current step stays `SHOW_PROGRESS` (the
  illegal result is discarded with the exception), the refresh event
  never fires (it fires only after validation passes **[P §3.2]**), the
  frontend spinner never resolves, and **no entry can ever be created —
  even when the GATT connection and the Modbus read succeed.**

The BLE link dropping is therefore **not** the cause of the crash. The
drop (when real) is handled cleanly as `cannot_connect`. The blocker is
the flow engine contract violation. The warning is a diagnostic defect
of our own making.

## 2. Root cause, one paragraph

`config_flow.async_step_test` (L347-395) and `async_step_scan`
(L286-311) use the "flag" pattern: the task sets `_test_done` /
`_scan_done`, and the step re-entry returns the *next form/menu
directly*. HA's contract instead requires the re-entered progress step
to return `async_show_progress_done(next_step_id=…)` and lets the
manager call the next step **[P §3.1-§3.3]**. Every test completion and
every rescan therefore raises the ValueError, kills HA's re-entry task,
and leaves the flow stuck. Fix = matter's task-reference pattern
(`research.md` §3.5) in both steps.

## 3. Verified HA API facts

### 3.1 Who re-enters the step, and when **[P]**

`homeassistant/data_entry_flow.py`, `FlowManager._async_handle_step`
(2024.12.0 L533-558 / 2026.10.1 ≈L527-550):

```python
if (
    result["type"] is FlowResultType.SHOW_PROGRESS
    and (progress_task := result.pop("progress_task", None))
    and progress_task != flow.async_get_progress_task()
):
    async def call_configure() -> None:
        with suppress(UnknownFlow):
            await self._async_configure(flow.flow_id)

    def schedule_configure(_: asyncio.Task) -> None:
        self.hass.async_create_task(call_configure())

    progress_task.add_done_callback(schedule_configure)
    flow.async_set_progress_task(progress_task)
elif result["type"] is not FlowResultType.SHOW_PROGRESS:
    flow.async_cancel_progress_task()
```

- The manager **pops** `progress_task` from the SHOW_PROGRESS result and
  registers a **done-callback on the task**. When the task completes,
  the callback creates a task that calls `_async_configure(flow_id)`
  → `_async_handle_step(flow, cur_step["step_id"], None)` → **the step
  method is called again with `user_input=None`**.
- Re-registering happens **only when the task object changes**
  (`progress_task != flow.async_get_progress_task()`): returning the
  *same* task from a re-entry while it still runs is a no-op for the
  manager — this makes the matter pattern idempotent under frontend
  refreshes **[P]**.
- Any non-`SHOW_PROGRESS` result **cancels** the flow's stored progress
  task (`async_cancel_progress_task`) — also on flow removal/abort
  (`_async_remove_flow_progress`, 2024.12.0 L494-500).

### 3.2 What the re-entry may return **[P]**

`FlowManager._async_configure` (2024.12.0 L404-458 / 2026.10.1
L395-430), right after `_async_handle_step` returns:

```python
if cur_step["type"] is FlowResultType.SHOW_PROGRESS and result["type"] not in (
    FlowResultType.SHOW_PROGRESS,
    FlowResultType.SHOW_PROGRESS_DONE,
):
    raise ValueError(
        "Show progress can only transition to show progress or show"
        " progress done."
    )   # 2026.10.1: raise at L411, string at L412 — matches the log
        # 2024.12.0: raise at L438, string at L439
```

- The check applies to **every** result type a step can return: FORM,
  MENU, ABORT, CREATE_ENTRY, EXTERNAL_STEP are all illegal from a
  `SHOW_PROGRESS` step. Aborting must also go through
  `show_progress_done(next_step_id=…)` to a step that aborts.
- The **refresh event** (`EVENT_DATA_ENTRY_FLOW_PROGRESSED`,
  `{"refresh": True}`; `async_notify_flow_changed()` in 2026) fires
  **after** this validation passes (2026.10.1 L426-429; handler at
  L857-864). On the ValueError path it never fires → the frontend
  spinner never resolves → the observed hang.

### 3.3 `async_show_progress_done` and the next step **[P]**

- Definition (`FlowHandler`): 2024.12.0 L844-850 / 2026.10.1 L868-874:

```python
@callback
def async_show_progress_done(self, *, next_step_id: str) -> _FlowResultT:
    """Mark the progress done."""
    return self._flow_result(
        type=FlowResultType.SHOW_PROGRESS_DONE,
        flow_id=self.flow_id,
        handler=self.handler,
        step_id=next_step_id,
    )
```

  Note the kwarg: **`next_step_id`** (not `step_id`).
- `async_show_progress`: 2024.12.0 L801-838 / 2026.10.1 L809-849 —
  signature `(step_id=None, *, progress_action,
  description_placeholders=None, progress_task=None)`; passing
  `progress_task=None` triggers a warning ("this is not valid and will
  break in Home Assistant Core 2024.8") and the
  `deprecated_show_progress` workaround path in `async_configure`
  (2024.12.0 L356-373 / 2026.10.1 L326-343). We always pass a task.
- The manager turns a `SHOW_PROGRESS_DONE` result into the next step
  via `async_configure`'s loop (2024.12.0 L364-373 / 2026.10.1
  L321-343):

```python
while not result or result["type"] is FlowResultType.SHOW_PROGRESS_DONE:
    result = await self._async_configure(flow_id, user_input)
```

  The second `_async_configure` reads `cur_step` = the PROGRESS_DONE
  result (`step_id = next_step_id`) and calls
  `async_step_<next_step_id>(None)` — i.e. **the next step renders
  exactly as if the user had submitted nothing**, which is precisely how
  our `confirm`/`device`/`manual` steps behave with `user_input=None`.
- The manager validates `next_step_id` against the step methods
  (`_raise_if_step_does_not_exist`, reached via
  `FLOW_NOT_COMPLETE_STEPS` — 2024.12.0 L72-78 / 2026.10.1 L44-51,
  which contains both SHOW_PROGRESS and SHOW_PROGRESS_DONE).

### 3.4 `step_id` is backfilled — so we must keep passing it **[P]**

`STEP_ID_OPTIONAL_STEPS` contains `SHOW_PROGRESS` (2024.12.0 L82-87 /
2026.10.1 L54-59): if a SHOW_PROGRESS result omits `step_id`, the
manager backfills it with the **currently handled** step's id. Our first
entry into `async_step_test` is a **direct coroutine call** from
`async_step_device`/`async_step_manual`/`async_step_bluetooth`
(`return await self.async_step_test()`), so the "currently handled" id
would be `device`/`manual`/`bluetooth` — the manager would then re-enter
the **wrong** step on task completion. Therefore `step_id="test"` /
`step_id="scan"` must stay explicit in our `async_show_progress` calls
(the kwarg is accepted at both verified versions; matter uses it too).
The walkthrough-test stub records it as `result["step_id"]` — unchanged.

### 3.5 The canonical pattern (matter, 2024.12.0) **[P]**

`homeassistant/components/matter/config_flow.py` L70-105
(`async_step_install_addon`):

```python
if not self.install_task:
    self.install_task = self.hass.async_create_task(self._async_install_addon())
if not self._running_in_background and not self.install_task.done():
    return self.async_show_progress(
        step_id="install_addon", progress_action="install_addon",
        progress_task=self.install_task,
    )
try:
    await self.install_task
except AddonError as err:
    ...
    return self.async_show_progress_done(next_step_id="install_failed")
finally:
    self.install_task = None
...
return self.async_show_progress_done(next_step_id="start_addon")
```

Three properties our fix adopts: **(1)** the task is created once and
kept as a reference, **(2)** re-entries while it runs return the same
task, **(3)** the re-entry after completion routes via
`async_show_progress_done(next_step_id=…)`. Our variant reads the
stashed `_test_error` instead of `await`ing the task (our task body
already catches every `Exception` and stores the error — awaiting it
again would only re-raise a `CancelledError` on the abandoned-flow
path, so we deliberately do not).

### 3.6 `async_set_unique_id(..., raise_on_progress=False)` **[P]**

`homeassistant/config_entries.py`, `ConfigFlow.async_set_unique_id`
(2024.12.0 L2670-2681 / 2026.10.1 L3318-3329): with
`raise_on_progress=True` (default) it raises
`AbortFlow("already_in_progress")` when **another flow** with the same
`unique_id` is in progress; `False` skips only that check (the
`unique_id` is still set in the flow context). The name refers to
*flows in progress*, not to the progress-step machinery — no link
whatsoever to the ValueError. Our usage is correct and stays.

## 4. The six questions

**Q1 — The exact contract.** Proven above (§3.1-§3.3): the progress
**task never returns anything**; when it completes, HA re-enters
`async_step_<id>` with `user_input=None`; the **step** must then return
`async_show_progress(...)` (same or new task) or
`async_show_progress_done(next_step_id=…)`. The manager — not the step
— calls `async_step_<next_step_id>(None)`. Identical at 2024.12.0 and
2026.10.1. **Our code does not do this**: the re-entry returns
`async_step_confirm()` (FORM), `async_step_device/manual(errors=…)`
(FORM), or `async_step_user()` (MENU) → ValueError on every completion.

**Q2 — `await client.disconnect()` in the `finally` after `_on_disconnect`
already ran.** Safe no-op, proven in bleak sources (§5): after a link
drop, `_cleanup_all()` has already closed the D-Bus bus, so bleak's
`disconnect()` returns immediately (`if self._bus is None: return
True` in 0.22.3 L385-390; `return` in 3.0.2 L387-390). It cannot raise
(aqua_client swallows disconnect errors at DEBUG anyway), it does **not**
re-fire the callback (the callback fires once per Connected→False
transition), and it cannot desynchronize the progress task: the task
completes normally and the crash comes afterwards, from the step's
illegal return. The BLE teardown is innocent; the routing is guilty.

**Q3 — Should `_on_disconnect` be armed during the test?** Yes — but
it must stop lying. Keep it armed: on a genuine mid-read drop it fails
the pending request **immediately** (`_fail_pending` → read raises
`AquaTempConnectionError` → clean `cannot_connect` routing) instead of
burning the full 8 s request timeout. Fix the reporting: a
self-initiated disconnect is detectable because our own `disconnect()`
sets `self._client = None` **before** awaiting bleak's disconnect
(aqua_client.py L249), so the callback sees `_client is None` exactly on
the clean path → DEBUG "closed" + early return; everything else keeps
the WARNING + timer/pending handling. No test-mode client parameter
(rejected — §8), no change to connect/read logic. Bonus: the
coordinator's per-poll clean disconnect (coordinator.py `_async_update_data`
→ `disconnect()` each cycle) stops spamming a WARNING every interval.

**Q4 — `raise_on_progress=False`.** Correct as-is, unrelated (§3.6).
The "in progress" in the HA error message is about flow *results* of
type progress, not about unique-id progress checking. Red herring.

**Q5 — `task: None` in "Task exception was never retrieved (task: None)".**
Decoded (proven): the ValueError is raised inside the internal
`call_configure()` task that `schedule_configure` creates via
`hass.async_create_task` (§3.1). Nothing ever retrieves that task's
exception; when it is garbage-collected, CPython calls the loop's
exception handler with context `{"message": "Task exception was never
retrieved", "exception": <the ValueError>, …}` — **without a `"task"`
key on Python 3.13** (verified experimentally on 3.13.5, §6). HA's loop
exception handler (`homeassistant/runner.py`, 2026.10.1 L203-231) logs
`"Error doing job: %s (task: %s)"` with `context.get("task")` → `None`,
with the exception's traceback → `Source: data_entry_flow.py:411`. It
says: *HA's own re-entry job died on our contract violation, and the
flow is still parked on the spinner.* The only way to avoid it is to
stop raising it — the fix. (2024.12's handler used the format
`"Error doing job: %s (%s)"` — the `(task: …)` wording pins the
instance to the 2026-era runner.)

**Q6 — Does `async_step_scan` have the same bug?** Yes, identically:
the re-entry returns `await self.async_step_user()` (MENU) → the same
ValueError on every rescan. Both steps are fixed by the same pattern.

## 5. Verified bleak facts (the "BLE connection lost" warning)

bleak **0.22.3** (pinned by HA 2024.12.0) and **3.0.2** (pinned by HA
2026.10.1 — `package_constraints.txt` at both tags), BlueZ backend
`bleak/backends/bluezdbus/client.py`:

- **L154-178 (0.22.3) / L170-190 (3.0.2)** — `on_connected_changed`:
  on any `Connected → False` D-Bus transition it runs `_cleanup_all()`
  and then `if self._disconnected_callback is not None:
  self._disconnected_callback()` — **unconditionally**, i.e. for
  remote-initiated drops **and for our own `disconnect()`** (the D-Bus
  `Disconnect` call produces the same PropertiesChanged signal). The
  base-class docstring ("only be called on unsolicited disconnect
  event", `bleak/backends/client.py` L66-78 in 0.22.3 / L63-75 in 3.0.2)
  **contradicts the implementation**; the implementation is what runs.
  → On a **successful** test, our own clean disconnect fires
  `_on_disconnect` → the scary WARNING at aqua_client.py:259. Same
  warning, one per polling cycle, in steady state.
- **L381-426 (0.22.3) / L383-425 (3.0.2)** — `disconnect()`:
  `if self._bus is None: return True / return` — a no-op when the link
  already dropped (bus closed by `_cleanup_all`). Idempotent, cannot
  re-fire the callback, cannot raise into our `finally`.
- **`_cleanup_all`** (0.22.3 L348-… / 3.0.2 L364-…) closes the D-Bus
  bus — which is why the follow-up `disconnect()` is the no-op above.

## 6. Experiment: the `(task: None)` context on Python 3.13 **[P]**

Run locally on this machine (Python 3.13.5 — the major version HA
2026.10 requires): create a task whose coroutine raises; never retrieve
the exception; install an exception handler mimicking HA's
(`context.get("task")`):

```
MESSAGE: 'Task exception was never retrieved'
TASK KEY PRESENT: False -> value type: NoneType
EXC: <the raised exception>
```

On Python ≤ 3.11 the same context carried `"task": self` (older HA logs
showed the task repr); on 3.12+/3.13 it does not, which is exactly why
the production log prints `(task: None)` while the traceback below it
is our ValueError's.

## 7. Version forensics — which HA produced the log?

`raise ValueError("Show progress can only transition…")` sits at:

| tag | raise / string line |
|---|---|
| 2024.12.0 | 438 / 439 |
| 2025.12.0 | 417 / 418 |
| 2026.9.0, 2026.9.4, 2026.10.0, 2026.10.1 | **411 / 412** |

The production log (`data_entry_flow.py:411`) plus the
`(task: None)` runner format (2026-era only, §4-Q5) pins the affected
instance to **HA 2026.9.x–2026.10.x on Python 3.13**. The contract is
**identical** across every version checked (2024.12.0 → 2026.10.1), so
one fix covers all versions, including our 2024.12 minimum.

## 8. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Fix only `async_step_test`, keep the boolean pattern for `scan` | Same contract violation, same crash on every rescan; one pattern, both steps **[D]** |
| `await self._test_task` in the re-entry (matter awaits) | Our task body already converts every `Exception` into `_test_error`; re-awaiting would only re-raise `CancelledError` on the abandoned-flow edge and complicate the step **[D]** |
| Test-mode client parameter to skip the callback during the test | Loses the instant fail-fast of `_fail_pending` on a real mid-read drop; adds API surface; the `_client is None` guard fixes the reporting for test *and* steady state **[D]** |
| Keep `_test_done`/`_scan_done` alongside the task references | Two sources of truth; a stale task of a previous attempt could set the flag late and confuse routing; the task reference alone is authoritative **[D]** |
| Route failures to dedicated `test_failed`-style step_ids | New steps + translations for no UX gain; origin-form retry is spec 002's contract **[D]** |
| Add `config.progress.*` translation keys | Real gap (matter defines them) but orthogonal to the crash; frontend rendering of the missing key is **[V]**; out of scope — noted in spec.md §7 |

## 9. What this repo's tests encode today (the bug is enshrined)

`tests/test_config_flow_walkthrough.py` (10/10 passing at bf66431)
drives the progress steps manager-style — `await
result["progress_task"]` then re-entry — and asserts the re-entry
returns a **form** directly (e.g. happy path: `result = await
flow.async_step_test()` → assert confirm form). That is precisely the
behaviour HA rejects. The updated tests must assert
`progress_done → next step` instead, and must fail on the current code
(red-first) — the reproduction of the incident without hardware.

## 10. Remaining [V] items (live instance checklist — quickstart.md)

- **V-1** End-to-end on the live HA: successful test → confirm form →
  entry created; logs clean (no ValueError, no unretrieved task, no
  "lost" warning on the clean disconnect).
- **V-2** Genuine mid-test link drop (vendor app grabs the unit):
  WARNING "lost" fires once, origin form shows `cannot_connect`, no
  ValueError.
- **V-3** *Scan again* → menu re-renders after ~8 s; no ValueError.
- **V-4** Steady-state polling logs: per-cycle disconnects at DEBUG.
- **V-5** The exact timestamp pairing observed in production: after
  the fix, the warning and the (former) crash line can no longer
  co-occur on the clean path.
- **[H]** Whether the frontend can re-enter a progress step *while the
  task runs* (a mid-progress refresh): unproven either way; the fix is
  idempotent under it (§3.1, same-task no-op), so it does not matter.

## 11. Sources read (method)

Fetched and read on 2026-10-11: HA core tags 2024.12.0 and 2026.10.1 —
`homeassistant/data_entry_flow.py`, `homeassistant/config_entries.py`,
`homeassistant/runner.py`, `homeassistant/core.py`,
`homeassistant/package_constraints.txt`,
`components/matter/config_flow.py`, `components/matter/strings.json`,
`components/config/config_entries.py` (API layer — file contained
anomalies, not relied upon); tags 2026.9.0/2026.9.4/2026.10.0/2025.12.0 —
`data_entry_flow.py` (line-number forensics only); bleak v0.22.3 and
v3.0.2 — `bleak/backends/bluezdbus/client.py`,
`bleak/backends/client.py`; local Python 3.13.5 experiment for the
unretrieved-exception context; this repo at bf66431 —
`config_flow.py`, `aqua_client.py`, `coordinator.py`, `const.py`,
`strings.json`, `translations/`, `tests/` (all 5 suites run:
10/10 walkthrough, list/modbus/reassembly/registers green).