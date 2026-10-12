# Plan 007 — Measurement-first design (instrument the connect path)

Maps `spec.md` FRs to exact file edits. Normative for the Coder; proofs in
`research.md` (§ refs). Files outside NFR-1's list are untouched.

**Design stance (research §9):** no fix is identifiable from the artifacts —
the leading root causes (H-A discovery stall, H-B HCI create-connection
never completing, H-C MTU-exchange quirk) are host/stack-level and
indistinguishable without one instrumented failing run plus an HCI trace.
This plan therefore implements **observation only**: stage timings, window
arithmetic, and the runbook. The *fix design* is the decision table
(quickstart §7 + spec FR-5) — it is filled by measurements, not by us.

---

## 1. `custom_components/aqua_temp_ble/aqua_client.py`

**1.1 Stage-timing seam (FR-1).** Module-level, next to `_exc_chain`:

```python
def _now() -> float:
    """Time seam — tests patch this to fake the clock (plan 007 §1.1)."""
    return time.monotonic()
```

Add `import time` to the imports.

**1.2 `connect()` — record stages (FR-1).** Restructure the existing body
**without changing any call or error semantic**; only timestamps and log
lines are added. Shape (pseudocode preserving the current structure —
the Coder keeps the existing comments/docstrings intact and adds the
marked lines):

```python
async def connect(self, max_attempts: int = 2) -> None:
    if self.is_connected:
        return
    t0 = _now()                                   # NEW
    t = t0
    device = await self._resolve_device()
    t_resolve = _now(); t = t_resolve             # NEW: stage "resolve"
    if device is None:
        ...existing AquaTempDeviceNotFoundError path unchanged...
    try:
        brc = self._load_retry_connector()
        if brc is not None:
            _LOGGER.info(                          # NEW (FR-2)
                "connecting to %s: up to %d connect attempt(s) of %.0f s "
                "each (bleak-retry-connector BLEAK_TIMEOUT, research 007 §4)",
                self._address, max_attempts, 20.0,
            )
            client = await brc.establish_connection(...)  # unchanged args
        else:
            client = await self._connect_with_raw_client(device)  # raw path:
            # NEW (FR-2, standalone): same INFO line, "up to 2 attempt(s)
            # of %.0f s (raw bleak, const.CONNECT_TIMEOUT)"
            ...
    except Exception as exc:
        elapsed = _now() - t0                      # NEW
        _LOGGER.error(                             # EXTENDED (FR-1)
            "connect to %s failed at stage connect after %.1f s: %s",
            self._address, elapsed, exc_chain(exc),
        )
        raise AquaTempConnectionError(...) from exc   # unchanged message shape
    t_connect = _now(); t = t_connect              # NEW: stage "connect"
    self._client = client
    try:
        await client.start_notify(const.UUID_NOTIFY, self._on_notify)
        t_notify = _now()                          # NEW: stage "notify"
        self._resolve_write_characteristic(client)
        t_write_char = _now()                      # NEW: stage "write-char"
        await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)
        t_settle = _now()                          # NEW: stage "settle"
    except ... unchanged handlers (add stage/elapsed to the post-connect
    ERROR line the same way: "post-connect setup (ff02 notify / ff01
    lookup) on %s failed at stage %s after %.1f s: %s" — the stage name is
    "notify" or "write-char" depending on the last completed timestamp) ...
    _LOGGER.info(                                  # REPLACES the old line (FR-1)
        "connect summary %s: total %.1f s (resolve %.2f, connect %.1f, "
        "notify %.2f, write-char %.2f, settle %.1f)",
        self._address, t_settle - t0,
        t_resolve - t0, t_connect - t_resolve, t_notify - t_connect,
        t_write_char - t_notify, t_settle - t_write_char,
    )
    _LOGGER.info("Connected to %s (%s)", ...)     # keep — name/address parity
```

Notes:
- The old final INFO line `Connected to %s (%s)` is **kept** (other code
  greps it); the summary line is ADDITIONAL, emitted just before it.
- The failure paths must never compute a negative duration: if a stage
  timestamp is missing, report the last completed stage name only.
- The summary format is normative — `contracts/connect-stage-timing.md`.

**1.3 No other method changes.** `_resolve_device`, `_connect_with_raw_client`,
notify/write/read logic: untouched (NFR-2).

## 2. `custom_components/aqua_temp_ble/config_flow.py`

**2.1 `_async_test()` — test-level stage/elapsed (FR-3).** Record
`t_test = time.monotonic()` at entry. Define the stage label as a local:
`stage = "connect"` before the read, switch to `"first-read"` right before
`read_registers`, `"disconnect"` before the finally-disconnect. Append
`" at stage %s after %.1f s (budget %.0f s)"` to every existing
`_LOGGER.warning(...)` failure branch (spec 005 FR-2 branches — order and
UI keys unchanged). The `TimeoutError` branch's message already explains
the budget; it gains the elapsed value.

No changes to: `TEST_TIMEOUT`, `max_attempts`, error-key mapping, step
methods, schemas, translations.

## 3. `tools/smoke_client.py`

**3.1 Make the stage lines visible standalone (FR-1 corollary).** Add at
`main()` entry:

```python
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
```

so the connect summary + failure lines (INFO/ERROR from `aqua_client`)
appear on stderr without HA. Add nothing else; the readout format stays
byte-identical.

## 4. Tests

**4.1 `tests/test_connect_parity.py` (extend) or new
`tests/test_connect_timing.py`:**
- fake clock: monkeypatch `aqua_client._now` with a counter advancing
  deterministic values per call;
- AC-1: successful connect → capture INFO records → exactly one
  `connect summary` line; parse it, assert the five stage tokens with the
  expected fake durations and the total;
- AC-2: `establish_connection` fake raises
  `BleakNotFoundError("...Failed to connect after 2 attempt(s)") from
  TimeoutError()` → assert the ERROR line contains `at stage connect`,
  the fake elapsed, and the chain (`TimeoutError`);
- AC-3: INFO attempt-window line emitted with `max_attempts` value before
  the connect call (order asserted via record indices);
- AC-4: config-flow fake run: each failure key's warning line contains
  `at stage` and `after` and `budget`.

**4.2 Existing tests stay green untouched** (NFR-6): the fake clients in
`test_config_flow_walkthrough.py` / `test_connect_parity.py` must keep
passing without modification — the instrumentation is additive. If a fake
does not implement something the logging needs, adapt the *test fake*,
never the production path.

## 5. Explicitly NOT doing (guards for the Reviewer)

- `const.py`: **untouched** — `SETTLE_AFTER_SUBSCRIBE`, `CONNECT_TIMEOUT`,
  `REQUEST_TIMEOUT`, `TEST_TIMEOUT` (in config_flow.py),
  `NAME_PREFIXES`, `MAX_RX_BUFFER` all byte-identical.
- `max_attempts` call values: unchanged (2 config flow / 2 coordinator).
- No new frames, no changed write mode, no changed notify flow.
- No translation files (NFR-2: no new UI text).
- Reviewer grep gate: `git diff --stat` must show exactly
  `aqua_client.py`, `config_flow.py`, `tools/smoke_client.py`,
  `tests/…`, and nothing else.

## 6. What the fix will look like (post-measurement sketch — NOT this spec)

Recorded so the follow-up spec has a starting point; none of this is
authorized by 007 (research §9 decision table):
- **H-A confirmed (discovery stall)** → integration cannot skip BlueZ
  discovery; the mitigation is host-side (adapter quality/USB dongle,
  proximity). Possibly warm-cache seeding via one successful
  `bluetoothctl` connect, after which BlueZ/bleak caches make every
  later connect fast — documented in the runbook as an outcome, not a
  code change.
- **H-B confirmed (HCI/link)** → host-level (adapter placement, USB
  extension, address-type verification); integration unchanged.
- **H-C confirmed (MTU)** → vendor firmware report; integration unchanged.
- **Post-connect stage confirmed (subscribe/read failure)** → that is a
  different bug class and gets its own spec with the captured evidence.
- **Wedge (H-D)** → runbook procedure (power-cycle + `bluetoothctl
  remove`), no code.

## 7. Sequencing

1. Block A (aqua_client instrumentation) → gate.
2. Block B (config_flow elapsed/stage) → gate.
3. Block C (smoke_client logging) → gate.
4. Block D (verify kit docs present, no code) → final gate
   (`pytest tests/ -q`, `python3 -m py_compile` on touched files,
   diff-stat audit).