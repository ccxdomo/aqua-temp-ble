# Data Model 005 — Resolution provenance, error keys, and the timeout arithmetic

Companion to `spec.md` / `plan.md`. Everything here is either **[P]**
(proven — research.md) or **[D]** (design). No new persisted state; the only
runnable-state changes are the resolution provenance logs, the new error
key, and the budget constant.

---

## 1. Device-resolution outcome space (`_resolve_device`)

| Outcome | HA-mode source | Standalone source | Log level on hit | Consumer |
|---|---|---|---|---|
| `RESOLVED_CONNECTABLE` | manager `_connectable_history[address]` via `async_ble_device_from_address(…, connectable=True)` **[P]** | n/a | DEBUG | passed to `establish_connection` (address only, inside HA **[P]**) |
| `RESOLVED_NON_CONNECTABLE` | manager `_all_history[address]` via `connectable=False` (HA's own fallback order **[P]**) | n/a | WARNING ("connection may fail") | same |
| `RESOLVED_NAME_MATCH` | first `async_discovered_service_info(connectable=True)` entry whose name starts with a `NAME_PREFIXES` entry (MAC-change case) **[P]** | first raw `BleakScanner.discover()` match | WARNING when the tier is entered; WARNING on different-address / multi-match | same |
| `RESOLVED_BY_ADDRESS` | n/a | raw `BleakScanner.find_device_by_address(address, timeout=5.0)` | — | same |
| `NOT_FOUND` | all tiers miss | both scans miss | (the raised exception is logged by `_async_test`) | `connect()` raises `AquaTempDeviceNotFoundError` |

Key invariants **[P]**:
- In HA mode every tier is an **instant cache read** (the old bleak
  monkeypatch made the "raw" steps cache reads too — research §3.1); no
  scan is ever started inside HA by our code.
- Manager history retention ≈ 195 s (scanner-side expiry) + ≤300 s
  (manager watchdog) after the last advertisement; scanner-side data
  (what the connect path reads) ≈ 195 s — the temporal gap behind edge
  cases E-2/E-3.
- The `BLEDevice` passed to `establish_connection` is used **only for its
  address** inside HA (the wrapper re-resolves from connectable scanner
  data at connect time) — research §3.2.

## 2. Test-failure routing matrix (`_async_test`)

| Exception (source) | `_test_error` key | Log (level, content) | User message (fr/en json) |
|---|---|---|---|
| `AquaTempDeviceNotFoundError` (resolution `NOT_FOUND`) | `not_found` **[D — new]** | WARNING: address + "device not found" | pairing-mode + rescan advice **[D]** |
| `AquaTempConnectionError` (terminal connect failure: refused, no backend, post-connect setup, GATT write) | `cannot_connect` | WARNING: address + `exc_chain` | vendor app closed / range (unchanged) |
| `AquaTempTimeoutError` (register read deadline, silence+request rules) | `no_response` | WARNING: address + chain | accepted but did not answer (unchanged) |
| `AquaTempResponseError` (CRC/function-code/shape) | `crc_error` | WARNING: address + chain | corrupted response (unchanged) |
| `TimeoutError` (the `TEST_TIMEOUT` budget expired — the old silent path **[P]**) | `cannot_connect` | WARNING: address + budget value + likely-cause advice | vendor app closed / range (unchanged) |
| any other `Exception` | `cannot_connect` | ERROR via `_LOGGER.exception` (unchanged) | vendor app closed / range (unchanged) |
| *(success)* | `None` | INFO "Connected to …" from `aqua_client` (unchanged, visible only at INFO) | confirm form |

Rule: **no row maps to a UI key without a log line** (AC-1). The budget
row keeps the `cannot_connect` key deliberately **[D]** — the unit did not
accept the connection in time, and the existing advice (close the vendor
app, check range) is the correct user action; the *log* now carries the
differentiation.

## 3. Timeout/budget arithmetic (verified vs dead) **[P — research §3.2, §5]

| Constant | Scope | Effective? | Actual bound |
|---|---|---|---|
| `TEST_TIMEOUT = 40.0` (was 20.0) **[D]** | `_async_test` whole budget (`asyncio.timeout`) | YES | covers resolution (~0 s) + one brc attempt (≤20 s) + notify setup (~0.5-2 s) + read (≤8 s) + margin |
| brc `BLEAK_TIMEOUT = 20.0` | per connect attempt | YES — **hardcoded in brc**, not settable by us | the real per-attempt wall |
| brc `BLEAK_SAFETY_TIMEOUT = 60.0` | per attempt wrapper | YES (never reached under our budget) | 60 s |
| `CONNECT_TIMEOUT = 15.0` | constructor kwarg in HA | **DEAD for connects** (brc passes `timeout=20.0` to `connect()`) | only bounds bleak's implicit discovery for address-string clients (never our path) |
| `CONNECT_TIMEOUT = 15.0` | standalone raw path | YES | 15 s per attempt, 2 attempts (`_connect_with_raw_client`) |
| `REQUEST_TIMEOUT = 8.0` | register read | YES | unchanged |
| `SCAN_WAIT_SECONDS = 8.0` | rescan progress step | YES | unchanged (spec 002) |

Why 40 s is the honest budget: a single attempt's worst case is
20 (connect) + ~2 (notify+char lookup) + 8 (read) ≈ 30 s; 40 s leaves
margin without a UX-hostile spinner. With `max_attempts=1` in the flow the
budget can always decide *before* a second attempt would start **[D]**.

## 4. `AquaTempBleClient` interface deltas

```python
class AquaTempDeviceNotFoundError(AquaTempError): ...   # new [D]

class AquaTempBleClient:
    async def connect(self, max_attempts: int = 2) -> None: ...  # forwarded to brc;
        # coordinator call sites unchanged (default 2); the flow passes 1

def exc_chain(exc) -> str   # public rename of _exc_chain (FR-1); private alias kept
```

- `AquaTempDeviceNotFoundError` is deliberately NOT a subclass of
  `AquaTempConnectionError` (the found/couldn't-connect distinction is
  the whole point) but IS an `AquaTempError` → the coordinator's
  `except AquaTempError` (UpdateFailed) catches it unchanged **[P — repo]**.
- No signature changes to `read_registers` / `write_registers` /
  `disconnect` / `_on_disconnect` (spec 004 contract preserved).

## 5. Translation keys (delta)

| Key | File(s) | Text |
|---|---|---|
| `config.error.not_found` **[new]** | strings.json, en.json, fr.json | see plan §4 / spec FR-10 |
| all other `config.error.*` | unchanged | — |

The flow's step texts, menu labels, descriptions: unchanged (no duration
promises, so the budget change needs no text change).