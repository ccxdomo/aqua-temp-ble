# Plan 004 — Design of the progress-step fix

Status: **PLANNED**. This file describes the exact target design; the
Coder implements in the order of `tasks.md`. Normative state machine:
`contracts/progress-step-contract.md`. Proofs for every HA claim:
`research.md`.

Confidence legend: **[P] PROVEN**, **[A] ASSUMED**, **[V] TO-VERIFY**,
**[D] DECISION**.

---

## 1. What changes — and what does not

| File | Change |
|---|---|
| `config_flow.py` | `async_step_test`, `async_step_scan` follow the progress contract; `async_step_device`, `async_step_manual` consume the stashed test error; `__init__` swaps two booleans for two task references |
| `aqua_client.py` | `_on_disconnect` only: clean-vs-unexpected guard (the callback branch point) |
| `tests/test_config_flow_walkthrough.py` | manager-faithful driving + contract assertions (red-first) |

Untouched: `modbus.py`, `coordinator.py`, entity platforms, `const.py`,
`strings.json`, `translations/`, `manifest.json`, unique-id rules,
options flow, list builder, dormant bluetooth step.

## 2. Flow state after the fix

| attr | type | set by | consumed by |
|---|---|---|---|
| `_test_task` | `asyncio.Task \| None` | `async_step_test` (created once per attempt) | `async_step_test` re-entry (`.done()` check; cleared on routing) |
| `_scan_task` | `asyncio.Task \| None` | `async_step_scan` | `async_step_scan` re-entry (same) |
| `_test_error` | `str \| None` | the test task (every failure path / `None` on success); **reset to `None` when a new test starts** | the target form step (`manual`/`device`) — read **and clear** when rendering with `user_input=None`; `device` clears it on the user-menu fallback |
| `_address`, `_name`, `_regs`, `_origin`, `_device_labels` | unchanged | spec 002 | unchanged |

`_test_done` / `_scan_done` are **deleted** (the task object itself is
the done-flag; `.done()` is authoritative and cannot desynchronize —
the current boolean could be set by a stale task of a previous attempt).

## 3. The repaired steps

### 3.1 `async_step_test` (the matter pattern, minimal)

```python
async def async_step_test(self, user_input=None):
    """Progress step: live connection test (connect + read 45 registers)."""
    if self._test_task is None:
        # New attempt: reset the error stash so a stale error from a
        # previous attempt can never surface on the next form render.
        self._test_error = None
        self._test_task = self.hass.async_create_task(self._async_test())

    if not self._test_task.done():
        # Same task object on every re-entry while running: HA's manager
        # no-ops when the progress task is unchanged [P].
        return self.async_show_progress(
            step_id="test",
            progress_action="test",
            progress_task=self._test_task,
            description_placeholders={"address": self._address or ""},
        )

    # Progress task complete -> HA re-entered this step. Only
    # show_progress / show_progress_done are legal results here [P];
    # anything else raises ValueError in data_entry_flow.py.
    self._test_task = None
    if self._test_error is None:
        return self.async_show_progress_done(next_step_id="confirm")
    if self._origin == "manual":
        return self.async_show_progress_done(next_step_id="manual")
    return self.async_show_progress_done(next_step_id="device")
```

`_async_test` becomes a bound method; its body is **unchanged except**
that the `finally: self._test_done = True` disappears (task completion
is the signal). The `try/except` ladder (Connection/Timeout/Response →
`_test_error`, success → `None`) is kept byte-identical. The
`finally: await client.disconnect()` inside stays (proven safe after a
link drop: bleak's `disconnect()` is a no-op then — research §Q2).

**Routing note [P]**: `next_step_id` must name an existing step method —
HA validates with `_raise_if_step_does_not_exist`. `confirm`, `manual`,
`device` all exist. HA then calls `async_step_<next>(None)` itself
(`async_configure`'s while-loop), so the confirm form renders exactly as
today — no changes to `async_step_confirm`.

### 3.2 `async_step_scan`

```python
async def async_step_test(...)  # as above

async def async_step_scan(self, user_input=None):
    """Bounded rescan: grace delay for new advertisers, then back to user."""
    if self._scan_task is None:
        self._scan_task = self.hass.async_create_task(
            asyncio.sleep(SCAN_WAIT_SECONDS)
        )
    if not self._scan_task.done():
        return self.async_show_progress(
            step_id="scan",
            progress_action="scan",
            progress_task=self._scan_task,
            description_placeholders={"seconds": SCAN_WAIT_SECONDS},
        )
    self._scan_task = None
    return self.async_show_progress_done(next_step_id="user")
```

No error handling needed: a bare `asyncio.sleep` cannot fail except by
cancellation (flow deleted — the flow object dies with it). The old
`_async_wait` closure and `_scan_done` are deleted.

### 3.3 Error-stash consumption (`manual`, `device`)

The manager re-enters the target step with `user_input=None`; the
`errors=` kwarg is no longer passed by internal routing (the manager
cannot pass it). The steps read the stash on the form-render path:

```python
async def async_step_manual(self, user_input=None, errors=None):
    errors = errors or {}
    if user_input is None and self._test_error is not None:
        errors = {"base": self._test_error}
        self._test_error = None
    # ... rest unchanged (MAC validation on user_input, form render) ...

async def async_step_device(self, user_input=None, errors=None):
    if user_input is not None:
        # ... pick handling unchanged ...
    if errors is None and self._test_error is not None:
        errors = {"base": self._test_error}
        self._test_error = None
    self._device_labels, _counts = await self._async_build_device_labels()
    if not self._device_labels:
        # A menu cannot display errors; drop the stash so it never
        # leaks into a later, unrelated form render.
        self._test_error = None
        return await self.async_step_user()
    return self.async_show_form(step_id="device", data_schema=..., errors=errors)
```

The `errors` kwarg stays in both signatures (harmless, keeps direct-call
compatibility); internal flow routing no longer uses it.

### 3.4 `aqua_client._on_disconnect` (callback branch point only)

```python
def _on_disconnect(self, _client: BleakClient) -> None:
    """bleak disconnected_callback — runs in the event loop thread."""
    if self._client is None:
        # Self-initiated: our disconnect() clears _client before bleak
        # disconnects, and has already cancelled the silence timer and
        # failed any pending request. Expected close, not a failure.
        _LOGGER.debug("BLE connection to %s closed", self._address)
        return
    _LOGGER.warning("BLE connection to %s lost", self._address)
    self._cancel_silence_timer()
    self._fail_pending(AquaTempConnectionError(f"connection to {self._address} lost"))
```

Why the guard is exactly `self._client is None` **[P]**:
`disconnect()` (aqua_client.py L244-253) cancels the timer, fails the
pending request and clears `self._client` **before** awaiting bleak's
`client.disconnect()` — so by the time bleak invokes the callback for a
clean close, `_client` is `None` and the timer/pending work is already
done. On an unexpected drop, `_client` is still the live client → full
current handling. Side effect (wanted, FR-6): per-poll-cycle clean
disconnects stop logging warnings; genuine drops still do.

## 4. Sequencing inside HA (why this is correct)

For the Coder's understanding (proofs in `research.md` §3):

1. Step returns `async_show_progress(step_id="test", progress_task=T)`
   → manager stores `T` on the flow and registers a done-callback on it
   (`data_entry_flow.py` L533-547 @ 2026.10.1).
2. `T` completes → done-callback creates HA's internal
   `call_configure()` task → `_async_configure` → **re-enters
   `async_step_test(None)`**.
3. The re-entry returns `async_show_progress_done(next_step_id=…)` →
   transition `SHOW_PROGRESS → SHOW_PROGRESS_DONE` passes validation;
   the manager loops (`while … SHOW_PROGRESS_DONE`) and calls
   `async_step_<next_step_id>(None)`.
4. The next step's form/menu result is legal because the flow's current
   step is now `progress_done`, not `progress`.

## 5. Testing strategy

- **Red-first [D]**: update `tests/test_config_flow_walkthrough.py` to
  the manager-faithful sequence *before* touching `config_flow.py`;
  run it → the progress re-entry scenarios must FAIL (they reproduce
  exactly what HA punishes with the ValueError). Then implement and
  make them green. This proves the tests capture the bug.
- The stub `FlowHandler` gains an `async_show_progress_done(*,
  next_step_id)` helper returning
  `{"type": "progress_done", "step_id": next_step_id}` (mirroring HA's
  result shape — `research.md` §3.4).
- A shared driver mirrors the manager:

```python
async def _drive_progress(flow, result):
    """Mirror HA's FlowManager for SHOW_PROGRESS results."""
    assert result["type"] == "progress", result
    await result["progress_task"]                    # done-callback fires
    result = await getattr(flow, f"async_step_{result['step_id']}")(None)  # re-entry
    # THE contract: a progress re-entry may only yield progress again
    assert result["type"] in ("progress", "progress_done"), result
    if result["type"] == "progress_done":
        result = await getattr(flow, f"async_step_{result['step_id']}")(None)  # manager loop
    return result
```

- Scenario coverage: happy path (→ confirm form → entry), link-drop
  failure (→ manual/device form with `cannot_connect`, stash cleared),
  no-response, crc_error, timeout, scan loop (→ user menu), and the
  dormant bluetooth path (→ device form with error).
- Gates: `py_compile` on both touched source files; all five test files
  pass (walkthrough, list, modbus, reassembly, registers).

## 6. Implementation order

See `tasks.md` (tests first → test step → scan step → stash → client
callback → gates → review checklist).