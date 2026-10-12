# Plan 005 — Exact design: resolution order, logging points, budget, error keys

All facts referenced below are **[P]** in `research.md` (§ refs). Design
choices are **[D]**. The implementation is confined to the files in NFR-1.

---

## 1. The new resolution contract (`aqua_client._resolve_device`)

### 1.1 HA mode (`self._hass is not None`) — explicit HA APIs, no monkeypatch reliance

```
resolve(address):
  tier 1: async_ble_device_from_address(hass, address, connectable=True)
          hit  -> DEBUG "%s: resolved from HA connectable cache" -> return device
  tier 2: async_ble_device_from_address(hass, address, connectable=False)
          hit  -> WARNING "%s: only visible via a non-connectable scanner;
                           the connection may fail" -> return device
  tier 3: WARNING "%s: not found in the HA bluetooth cache; looking for a
                   name-prefix match (the MAC may have changed)"
          for info in async_discovered_service_info(hass, connectable=True):
              if (info.name or "").lower().startswith(NAME_PREFIXES): matches.append(info)
          0 matches -> return None
          >1 matches -> WARNING (existing multi-match text, listing name+address)
          chosen = matches[0]
          chosen.address != address (case-insensitive) -> WARNING (existing
              "DIFFERENT address" text — the MAC-change case)
          return chosen.device
```

- Tier order is HA's own sanctioned order (`HaBleakScannerWrapper.
  find_device_by_address` does connectable→non-connectable — research
  §3.1). Tier 3 is the same effective data as today's patched
  `BleakScanner.discover()` (manager connectable history) — behaviour-
  identical to `5aa887b`, only explicit **[P]**.
- The HA import stays inside the method with the existing
  `try/except ImportError` + DEBUG fallback so standalone imports keep
  working (`aqua_client` docstring contract).
- Log texts never claim a "scan" inside HA — every tier is a cache read
  (research §3.1 proves the old texts lied).

### 1.2 Standalone mode (`self._hass is None`) — byte-identical to today

Steps 2-3 of the current implementation move verbatim into this branch:
`BleakScanner.find_device_by_address(address, timeout=5.0)` and
`BleakScanner.discover(timeout=5.0)` + name-prefix filter, same log texts,
same warnings, same 5 s timeouts. Standalone is the ONLY mode where raw
scans run (research §3.1).

### 1.3 The `None` outcome

`connect()` raises the new `AquaTempDeviceNotFoundError` (subclass of
`AquaTempError`) instead of the old unlogged
`AquaTempConnectionError("no Aqua Temp device found…")`:

```python
device = await self._resolve_device()
if device is None:
    raise AquaTempDeviceNotFoundError(
        f"no Aqua Temp device found for address {self._address}"
    )
```

- `AquaTempError` remains the base → the coordinator's
  `except AquaTempError` catch is unchanged (repo `coordinator.py`
  `_async_update_data`).
- `AquaTempConnectionError` keeps its meaning: *the device was found but
  the connection could not be established* — now cleanly separable from
  *the device was not found*.

## 2. The new test budget (`config_flow._async_test`)

| Constant | Old | New | Rationale |
|---|---|---|---|
| `TEST_TIMEOUT` | 20.0 | **40.0** | one honest attempt = ≤20 s connect (brc hardcodes `BLEAK_TIMEOUT=20.0` — research §3.2) + ~2 s notify setup + ≤8 s read + margin |
| `max_attempts` (flow test) | 2 | **1** | a probe must be bounded; the budget cannot honestly cover a second 20 s attempt; the coordinator keeps 2 (no budget there) |
| `const.CONNECT_TIMEOUT` | 15.0 | 15.0 (comment fixed) | dead inside HA (brc overrides); bounds only the standalone raw path |

`_async_test` body (annotated with the new failure routing):

```python
try:
    async with asyncio.timeout(TEST_TIMEOUT):
        client = AquaTempBleClient(self._address, name=self._name, hass=self.hass)
        await client.connect()
        try:
            self._regs = await client.read_registers(...)
        finally:
            await client.disconnect()
except AquaTempDeviceNotFoundError as exc:
    self._test_error = "not_found"
    _LOGGER.warning("connection test to %s: device not found: %s",
                    self._address, exc)
except AquaTempConnectionError as exc:
    self._test_error = "cannot_connect"
    _LOGGER.warning("connection test to %s failed: %s",
                    self._address, exc_chain(exc))
except AquaTempTimeoutError as exc:
    self._test_error = "no_response"
    _LOGGER.warning("connection test to %s: no response: %s",
                    self._address, exc_chain(exc))
except AquaTempResponseError as exc:
    self._test_error = "crc_error"
    _LOGGER.warning("connection test to %s: bad response: %s",
                    self._address, exc_chain(exc))
except TimeoutError:
    self._test_error = "cannot_connect"
    _LOGGER.warning(
        "connection test to %s exceeded the %.0f s budget — the connect or "
        "read did not complete in time. The unit is likely not accepting "
        "the connection (single BLE link held by the vendor app?) or is "
        "out of range.",
        self._address, TEST_TIMEOUT)
except Exception:  # noqa: BLE001
    _LOGGER.exception("unexpected error during connection test")
    self._test_error = "cannot_connect"
else:
    self._test_error = None
```

Notes:
- `exc_chain` is the public rename of `_exc_chain` (FR-1); config_flow
  imports it from `.aqua_client`. The private alias stays for any external
  callers.
- Order matters: `AquaTempDeviceNotFoundError` is an `AquaTempError` but
  NOT an `AquaTempConnectionError`; catching it first keeps the mapping
  exact. `AquaTempTimeoutError`/`AquaTempResponseError` are also
  `AquaTempError` subclasses and are caught before the generic
  `TimeoutError` (they are NOT `TimeoutError` subclasses — no overlap).
- The budget-expiry branch logs the budget value and its advice; the
  `except TimeoutError` placement is unchanged relative to spec 004
  (budget expiry still routes `cannot_connect`).

## 3. `establish_connection` call — explicit attempt count, honest comments

`AquaTempBleClient.connect()` gains a `max_attempts: int = 2` parameter
(default preserves the coordinator's behaviour byte-for-byte) forwarded to
`brc.establish_connection`. The config flow passes `max_attempts=1`:

```python
# _async_test (config_flow.py)
await client.connect(max_attempts=1)   # bounded probe: one honest attempt

# coordinator (unchanged call, unchanged default)
await self.client.connect()           # max_attempts=2
```

```python
# aqua_client.connect() — annotated call site
client: BleakClient = await brc.establish_connection(
    BleakClient,
    device,
    name=self._name or getattr(device, "name", "") or self._address,
    disconnected_callback=self._on_disconnect,
    max_attempts=max_attempts,      # 2 (coordinator default) or 1 (flow probe)
    timeout=const.CONNECT_TIMEOUT,  # constructor-only kwarg: brc hardcodes
                                    # the connect attempt to BLEAK_TIMEOUT=20.0
                                    # (research §3.2) — this value bounds only
                                    # the standalone raw-client path
)
```

Why 1 attempt in the flow **[D]**: a probe must be bounded; the 40 s budget
cannot honestly cover a second 20 s attempt; transient errors still get
brc-internal retries (up to `MAX_TRANSIENT_ERRORS = 9` with short backoffs,
research §3.3), so `max_attempts=1` caps only slow *timeouts/connect
errors*. The coordinator keeps 2 — no budget wraps steady-state polling,
and its failures were never silent.

## 4. Translations

`strings.json` + `translations/en.json` + `translations/fr.json` gain
under `config.error`:

```json
"not_found": "Home Assistant can no longer see this device nearby. Put the unit in pairing mode (hold the panel button for about 5 seconds until the display shows \"bLE\"), make sure it is powered on, then scan again."
```

(fr.json carries the French text from spec FR-10.) `cannot_connect`,
`no_response`, `crc_error`, `invalid_address` unchanged. The `test` step
description is unchanged (no duration is promised to the user).

## 5. Test design (red-first)

- `FakeClient` gains `fault` support for `AquaTempDeviceNotFoundError`
  → asserts origin form renders with `errors == {"base": "not_found"}` and
  caplog contains the address + "not found".
- Budget case: `monkeypatch.setattr(cf, "TEST_TIMEOUT", 0.2)` + a
  `HangingFakeClient` whose `connect()` sleeps 0.5 s → `cannot_connect`
  + caplog contains "exceeded the … budget".
- Existing routing matrix (cannot_connect / no_response / crc_error /
  success / already-configured) untouched.
- New `tests/test_resolve_device.py`: fake `homeassistant.components.
  bluetooth` module injected via `sys.modules` (the same technique the
  walkthrough suite uses for the component import) exercising the four
  HA-tier outcomes + provenance log assertions + standalone passthrough
  (raw scanner fakes).

## 6. What deliberately does NOT change

- Spec 002's list building, menu, tiering, `SCAN_WAIT_SECONDS`,
  unique-id guards; spec 004's progress contract and `_on_disconnect`
  fix; spec 003's write guard and sentinel handling.
- The coordinator: still `max_attempts=2`, same connect/read/disconnect
  cycle, same logging (its failures were never silent — research §4.1
  applies to the *flow's* budget only).
- Standalone resolution (raw scans) — NFR-4.
- `const.CONNECT_TIMEOUT` value (standalone semantics).

## 7. Sequencing (why this order)

1. **Logging first** (Block A): even if everything else slipped, the next
   failed test on the live instance becomes diagnosable — the user's
   explicit priority.
2. **Budget + attempt count** (Block B): removes the structural
   "always-cut-off-at-20 s" defect; with A in place, any remaining
   cut-off is visible.
3. **Explicit resolution** (Block C): same behaviour, truthful logs, no
   monkeypatch reliance; also ships the `not_found` key.
4. **Tests** (Block E) red-first where the behaviour changes.