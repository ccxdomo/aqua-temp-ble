# Plan 006 — Implementation plan

Maps `spec.md` FRs to exact file edits. Normative for the Coder; proofs in
`research.md` (§ refs). Files outside NFR-1's list are untouched.

---

## 1. `custom_components/aqua_temp_ble/const.py`

**1.1 New constant (FR-1):**

```python
# Seconds to wait after subscribing to ff02 before the first request.
# Vendor parity: the app's session manager holds a 2 s Duration in the
# subscribe→read path (research 006 §2 step 4, §7).
SETTLE_AFTER_SUBSCRIBE = 2.0
```

**1.2 `NAME_PREFIXES` (FR-4):** append `"wt-"`:

```python
# BLE name prefixes the vendor app itself filters on (Dart pool). [P]
# Operating channel: "BlueNRG-" + "WT-" (research 006 §3); "prov_" is the
# WiFi-models provisioning-mode name (harmless to keep).
NAME_PREFIXES = ("bluenrg-", "hiflying-", "prov_", "wt-")
```

Comment update: cite research 006 §3 for the operating-channel filter.

## 2. `custom_components/aqua_temp_ble/aqua_client.py`

**2.1 `connect()` — settle delay (FR-1).** After the
`await client.start_notify(...)` + `_resolve_write_characteristic(...)`
try-block succeeds and BEFORE the final `_LOGGER.info("Connected …")`:

```python
# Vendor parity (research 006 §2 step 4): give the module a beat to route
# ff02 notifications after the CCCD write before the first Modbus frame.
await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)
```

Notes:
- The sleep is INSIDE the `try` that tears down on failure — a cancelled
  sleep must not leak a half-open client. `asyncio.CancelledError` is a
  `BaseException`: it bypasses the `except Exception` handlers, but the
  config flow's `asyncio.timeout` wraps the whole test and HA tears the
  task down — behaviour identical to spec 005's cancellation contract.
- Keep `is_connected` semantics unchanged: the client reports connected
  immediately after bleak connects; the settle only delays the first
  REQUEST. (`connect()` simply returns 2 s later; `_pending` is not
  armed yet, so no request can be affected.)

**2.2 `_resolve_write_characteristic()` — write mode (FR-2).** Replace the
single preference line with the ordered rule:

```python
props = list(getattr(char, "properties", []) or [])
if "write-without-response" in props:
    # Vendor parity: the app writes WITHOUT response
    # (WriteType.withoutResponse in its write path, research 006 §2.1).
    self._write_with_response = False
elif "write" in props:
    self._write_with_response = True
else:
    raise AquaTempConnectionError(
        f"write characteristic {const.UUID_WRITE} has no usable write property"
    )
```

Keep/extend the existing DEBUG log to print `props` and the decision
(`write_with_response=…`).

**2.3 Budget arithmetic comment in `connect()`** — the existing comment
mentions "ONE attempt can itself take up to 20 s" (spec 005 §3.2): extend
with "…and `connect()` now additionally sleeps
`SETTLE_AFTER_SUBSCRIBE` after subscribing (spec 006 FR-1)" so callers'
budget math stays honest.

## 3. `custom_components/aqua_temp_ble/config_flow.py`

**3.1 `TEST_TIMEOUT` (FR-3):** `40.0` → `70.0`. Replace the comment with
the new arithmetic:

```python
# Budget for the connection test (spec 006 FR-3 supersedes 005 §3):
#   2 × 20 s bleak-retry-connector-hardcoded connect attempts (brc's
#   BLEAK_TIMEOUT, research 005 §3.2) + brc inter-attempt backoff
# + 2 s settle after subscribe (spec 006 FR-1)
# + 8 s REQUEST_TIMEOUT for the base read
# + margin. The vendor app itself retries connects before giving up
#   (research 006 §2.2) — attempt 2 catches transient refusals (phone
#   releasing the single BLE link, stale bluez state).
TEST_TIMEOUT = 70.0
```

**3.2 `_async_test()` (FR-3):**
- `await client.connect(max_attempts=1)` → `max_attempts=2`; update the
  inline comment (was: "a second 20 s connect attempt never could [fit]" —
  now it does, inside 70 s).
- `TimeoutError` branch log: update the sentence to the 2-attempt reality
  ("exceeded the %.0f s budget — two connect attempts … the unit is
  likely not accepting the connection (single BLE link held by the vendor
  app?) or is out of range."). Keep the key `cannot_connect`.
- The progress step's `description_placeholders`/description text gains
  "(this can take about a minute)" — reuse the existing step description
  constant/translation entry for the test step (add the sentence to the
  description string in `strings.json` + `translations/en.json` +
  `translations/fr.json` **only if** the step description lives there;
  if it is inline Python, edit inline. Do not invent new keys beyond what
  the existing step uses).

## 4. Tests (`tests/`)

**4.1 `test_reassembly.py` (fake client):**
- The fake client's `start_notify`/connect methods must tolerate the new
  sleep — either patch `asyncio.sleep` in connect tests, or add a
  `SETTLE_AFTER_SUBSCRIBE = 0` override via monkeypatching `const` in the
  test fixture (preferred: monkeypatch, no wall-clock cost).
- New test (AC-2): fake characteristic properties
  `["write-without-response", "write"]` → `client._write_with_response`
  is False; `["write"]` → True; `["notify"]` → AquaTempConnectionError.

**4.2 New `tests/test_connect_parity.py` (AC-1):**
- Ordering test: record call sequence (`connect`, `start_notify`,
  `sleep`, `read_registers`) with patched `asyncio.sleep`; assert sleep
  happens exactly once, after `start_notify`, before the first read.

**4.3 `test_config_flow_walkthrough.py` (AC-3):**
- Existing walkthrough stays green (monkeypatch the budget constant if a
  test asserts 40.0; update any literal).
- Add: `_async_test` passes `max_attempts=2` to the client (assert via
  fake client recording kwargs).

**4.4 Discovery tier test (AC-4):** `test_resolve_device.py` /
`test_config_flow_list.py`: add a `WT-AB12` named entry and assert it is
listed by the prefix tier.

## 5. Execution order & gates

1. Block A (const + client, FR-1/FR-2 + tests) → gate: pytest green.
2. Block B (config_flow budget, FR-3 + tests) → gate: pytest green,
   py_compile clean.
3. Block C (NAME_PREFIXES, FR-4 + tests) → gate: pytest green.
4. Final gate: full `pytest tests/`, `python3 -m py_compile` on all
   touched files, `git diff --stat` confined to NFR-1's file list.