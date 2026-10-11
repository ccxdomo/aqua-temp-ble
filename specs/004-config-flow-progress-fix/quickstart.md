# Quickstart 004 — Reproduce the bug, verify the fix

Two reproduction paths: **A** without hardware (the walkthrough tests,
red-first), **B** on the live Home Assistant instance (the original
incident). Then the fix-verification checklist (V-1..V-5 of
`research.md` §10).

---

## A. Reproduce without hardware (red-first tests)

The existing `tests/test_config_flow_walkthrough.py` (10/10 passing at
bf66431) drives the progress steps exactly like HA's manager and
asserts the re-entry returns a **form** directly — the very behaviour
HA rejects with the ValueError. The updated tests must therefore:

1. Add `async_show_progress_done(*, next_step_id)` to the stub
   `FlowHandler`, returning `{"type": "progress_done",
   "step_id": next_step_id}` (HA's result shape — `research.md` §3.3).
2. Replace every "re-entry then assert form" sequence with the
   manager-faithful driver (`plan.md` §5):

   ```python
   await result["progress_task"]                       # task completes
   result = await flow.async_step_test()               # HA re-enters the step
   assert result["type"] == "progress_done"            # THE contract
   assert result["step_id"] == "confirm"               # next_step_id
   result = await flow.async_step_confirm()            # HA advances
   assert result["type"] == "form"
   ```

3. Run: `python3 tests/test_config_flow_walkthrough.py`

   **Expected before the fix**: the `progress_done` assertion fails —
   the re-entry returns `form` (what HA turns into
   `ValueError: Show progress can only transition to show progress or
   show progress done.`). **That failing run is the reproduction of
   the production incident.**

4. Implement the fix (`plan.md` §3), re-run: all walkthrough scenarios
   green.

Failure-routing scenario (link drop): the FakeClient seam
(`cf.AquaTempBleClient`) already lets a test simulate a read failure —
after the fix, the re-entry must yield `progress_done` with
`step_id == "manual"` (or `"device"`), and driving that step must
render the form with `errors == {"base": "cannot_connect"}` **and**
clear the stash (a second render of the same step shows no error).

## B. Reproduce on the live instance (the original incident)

Preconditions: HA 2026.9/2026.10 era (any version ≥ 2024.12 shows the
same contract), the unit in pairing mode or already advertising, the
vendor app closed.

1. Settings → Devices & Services → Add Integration → **Aqua Temp BLE**.
2. Pick the unit from the device list (or enter its MAC manually).
3. Watch the *Testing the connection* spinner, then HA's logs
   (Settings → System → Logs):

```
ERROR   homeassistant (data_entry_flow.py:411)
        ValueError: Show progress can only transition to show progress
        or show progress done.
        Error doing job: Task exception was never retrieved (task: None)
WARNING custom_components.aqua_temp_ble.aqua_client (aqua_client.py:259)
        BLE connection to 08:06:A6:C0:94:1C lost
```

4. Observed: the spinner never resolves; the dialog must be closed; no
   entry is created — **even when `Connected to …` (INFO) shows the
   GATT connection succeeded**. The pair appears once per attempt.
   *Scan again* produces the same ValueError pair (menu return).

What each line means (post-mortem table):

| line | real meaning |
|---|---|
| `ValueError (data_entry_flow.py:411)` | our `async_step_test` re-entry returned a form; HA rejects it |
| `Task exception was never retrieved (task: None)` | the same ValueError died inside HA's internal re-entry task (Python 3.13 context has no task key) |
| `BLE connection to … lost` (aqua_client.py:259) | bleak's disconnect callback fired — on a successful test this is **our own clean disconnect** being mislabelled; on a real drop it fired earlier and failed the read |

## C. Verify the fix on the live instance (V-1..V-5)

- **V-1 happy path**: pick the unit → spinner resolves (~2-5 s) →
  *Name your device* form with the probe values → submit → entry
  created, entities appear. Logs: `Connected to …` INFO, `BLE
  connection to … closed` DEBUG (default log level hides it), **no
  ValueError / unretrieved task / lost warning**.
- **V-2 link drop**: open the vendor app while the test reads (or move
  the unit out of range) → one WARNING `BLE connection to … lost`
  (genuine loss), origin form returns with `cannot_connect`, no
  ValueError, retry works.
- **V-3 rescan**: menu → *Scan again* → menu re-renders after ~8 s with
  fresh counts; no ValueError in the log.
- **V-4 steady state**: with the entry polling, per-cycle clean
  disconnects log at DEBUG; only genuine drops WARN.
- **V-5 pairing**: repeat the user's exact failing scenario (test the
  connection to 08:06:A6:C0:94:1C) — the former error pair can no
  longer co-occur on the clean path.

## D. Gates (all runs from the repo root)

```
python3 -m py_compile custom_components/aqua_temp_ble/config_flow.py \
                        custom_components/aqua_temp_ble/aqua_client.py
python3 tests/test_config_flow_walkthrough.py
python3 tests/test_config_flow_list.py
python3 tests/test_modbus.py
python3 tests/test_reassembly.py
python3 tests/test_registers.py
```

All must pass. The walkthrough suite is the contract gate for this
spec (AC-9).