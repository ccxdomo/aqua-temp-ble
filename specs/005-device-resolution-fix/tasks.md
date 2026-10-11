# Tasks 005 — Ordered implementation blocks for the Coder

Work top-down; each block ends with its gates. Repo rule: durable
artifacts (code comments, commit messages) in **English**. Do not touch
anything outside NFR-1's file list. The normative design is
`plan.md` + `contracts/device-resolution.md`; proofs are in `research.md`
(§ refs below).

---

## Block A — Failure logging (P0 — ships the diagnosis; implement FIRST)

**A1. `aqua_client.py`: public `exc_chain`** *(FR-1)*
- Rename `_exc_chain` → `exc_chain` (public), keep `_exc_chain =
  exc_chain` alias for backwards compatibility.
- Update the two internal call sites (`connect()` error branches) to use
  the public name (or leave aliasing to handle it — pick one, be
  consistent).

**A2. `aqua_client.py`: new exception** *(FR-9)*
- Add `class AquaTempDeviceNotFoundError(AquaTempError)` with docstring
  "The device could not be found in any discovery source."
- In `connect()`: the `device is None` branch raises
  `AquaTempDeviceNotFoundError(f"no Aqua Temp device found for address
  {self._address}")` instead of `AquaTempConnectionError`.

**A3. `config_flow.py`: log every failure branch of `_async_test`**
  *(FR-2 — plan §2 is the exact code)*
- Import `exc_chain` (and `AquaTempDeviceNotFoundError`) from `.aqua_client`.
- Add the six-branch structure from plan §2: `AquaTempDeviceNotFoundError`
  first (`not_found`), then the existing three with WARNING logs + chains,
  `TimeoutError` with the budget-expiry WARNING (naming `TEST_TIMEOUT`
  and the vendor-app/single-link advice), unchanged `except Exception`.
- Every line logs `self._address`.

**Gate A:** `python3 -m py_compile` on both files;
`test_config_flow_walkthrough.py` still green (existing cases — the new
`not_found` branch is unreachable until Block C/D? NO — it is reachable
once A2 exists: any `AquaTempDeviceNotFoundError` from a fake client hits
it; but no test raises it yet. All existing suites green; no behaviour
change for old keys except added logs.)

## Block B — Budget arithmetic (P1)

**B1. `config_flow.py`: `TEST_TIMEOUT = 40.0`** *(FR-5)* — comment cites
research §5 (one honest attempt = ≤20 s brc-hardcoded connect + ~2 s
notify + 8 s read + margin). Supersedes spec 004 AC-4's value (routing key
unchanged).

**B2. `aqua_client.py`: `connect(max_attempts=2)` parameter** *(FR-4)*
- `async def connect(self, max_attempts: int = 2) -> None:` forwarded to
  `brc.establish_connection(..., max_attempts=max_attempts, ...)`.
- Fix the call-site comment: `timeout=const.CONNECT_TIMEOUT` is
  constructor-only; brc hardcodes the connect attempt to 20.0 s
  (research §3.2); the constant bounds the standalone raw path only.
- `config_flow._async_test` calls `await client.connect(max_attempts=1)`
  (bounded probe — plan §3). Coordinator call sites stay untouched
  (default 2).

**Gate B:** py_compile; walkthrough green (budget not exercised at 40 s in
fast fakes); grep: `max_attempts=1` exactly once (config_flow), default
`max_attempts: int = 2` in the client signature.

## Block C — Explicit HA resolution (P1)

**C1. `aqua_client.py`: rewrite `_resolve_device` HA branch** *(FR-7,
FR-3; plan §1.1 + contract §2 are normative)*
- `if self._hass is not None:` → tier 1
  `async_ble_device_from_address(hass, address, connectable=True)` →
  tier 2 `connectable=False` (WARNING "only visible via a
  non-connectable scanner; the connection may fail") → tier 3 name-prefix
  walk over `async_discovered_service_info(hass, connectable=True)`
  (WARNING on entry: "not found in the HA bluetooth cache; looking for a
  name-prefix match (the MAC may have changed)"), keeping today's
  multi-match and different-address WARNING texts verbatim.
- Keep the lazy `try/except ImportError` + DEBUG standalone guard around
  the HA import (both symbols from one import statement).
- DEBUG line on tier-1 hit: `"%s: resolved from HA connectable cache"`.
- **C2. Standalone branch** *(FR-8)*: move today's steps 2-3
  (`BleakScanner.find_device_by_address(address, timeout=5.0)`,
  `BleakScanner.discover(timeout=5.0)` + filter) verbatim under
  `else:` — same texts, same timeouts, same warning behaviours.

**Gate C:** py_compile; `tools/smoke_client.py --help` imports cleanly;
test suites green (existing tests exercise the standalone/test paths via
fakes).

## Block D — `not_found` user-facing key (P2)

**D1. Translations** *(FR-10; exact texts in plan §4)*
- `strings.json` + `translations/en.json` + `translations/fr.json`:
  add `config.error.not_found` with the approved EN/FR texts.
- No other key, step text, menu label, or description changes.

**Gate D:** JSON valid (`python3 -m json.tool` on all three); fr/en
key sets identical.

## Block E — Tests (P2, red-first where behaviour changed)

**E1. Walkthrough additions** *(FR-12 — `tests/test_config_flow_walkthrough.py`)*
- `FakeClient.fault = AquaTempDeviceNotFoundError` case → origin form
  renders, `errors == {"base": "not_found"}`, caplog contains the address
  and "not found".
- Budget case: `monkeypatch.setattr(cf, "TEST_TIMEOUT", 0.2)` (or a
  module-level constant read at call time — match how `_async_test`
  reads it) + `HangingFakeClient.connect` sleeping 0.5 s →
  `errors == {"base": "cannot_connect"}`, caplog contains "budget".
- Caplog assertions on the existing `cannot_connect` case (address +
  "failed").
- Red-first check: budget case must FAIL on pre-B1 code (20 s constant —
  patching makes the hang survive... note: on old code the monkeypatched
  constant also works, so red-first applies to the *log* assertion and
  the `not_found` case only; document which assertions are red-first).

**E2. Resolution unit tests** *(FR-13 — new
`tests/test_resolve_device.py`)*
- Fake `homeassistant.components.bluetooth` in `sys.modules` (same import
  technique as the walkthrough suite) exposing
  `async_ble_device_from_address` / `async_discovered_service_info` with
  scriptable results.
- Cases: tier-1 hit (device returned, DEBUG logged); tier-1 miss + tier-2
  hit (WARNING logged); both miss + tier-3 single match (returned);
  tier-3 different-address match (different-address WARNING); tier-3
  multi-match WARNING; all miss → `connect()` raises
  `AquaTempDeviceNotFoundError` (and `_async_test` maps it to
  `not_found` — covered in E1).
- Standalone passthrough: `hass=None` with faked `BleakScanner` returning
  a device → returned; both scans empty → `None`.

**Gate E:** all five suites green: walkthrough, list, modbus, reassembly,
registers (+ the new file). Full run command:

```bash
cd <repo> && python3 -m pytest tests/ -q
```

## Block F — Docs & final sweep

- **F1.** `custom_components` docstrings/comments touched above stay
  English; no stray TODOs; no new dependencies.
- **F2.** Confirm untouched (diff-grep): `modbus.py`, `coordinator.py`,
  entity platforms, `manifest.json`, spec 003 write-guard paths.
- **F3.** Commit message (English), e.g.:
  `Fix device resolution visibility: log test failures, honest 40s budget, not_found error (spec 005)`.

## Verification summary for the Reviewer

| AC | Where proven |
|---|---|
| AC-1 no silent failure | Block A + E1 caplog assertions; contract §3 |
| AC-2 not_found routing | A2/A3/D1 + E1 |
| AC-3 budget expiry logged | B1 + A3 TimeoutError branch + E1 |
| AC-4 slow connect can succeed | B1/B2 arithmetic (research §5); live [V-3] |
| AC-5 non-connectable fallback preserved | C1 tier 2 + E2 |
| AC-6 loud failures stay loud/keyed | A3 table + E1 existing cases |
| AC-7 standalone identical | C2 + E2 passthrough |
| AC-8 tests red-first & green | E1/E2 |
| AC-9 spec 004 progress contract intact | walkthrough suite unchanged cases |
| AC-10 coordinator unchanged | B2 default + F2 diff-grep |

Estimated size: ~120 lines of code+comments across 4 source files, 3
translation files, 1 test file modified, 1 test file added.

---

## Execution log — 2026-10-11 (Coder)

Red-first honoured (Block E before A-D): the new/extended tests were
written and run against the unmodified `5aa887b` code first.

- ☑ **Block A — failure logging** (A1/A2/A3): `exc_chain` public with
  `_exc_chain = exc_chain` alias; `AquaTempDeviceNotFoundError`
  added; all six `_async_test` branches log (see FR-2 table below).
- ☑ **Block B — budget arithmetic** (B1/B2): `TEST_TIMEOUT = 40.0`
  (comment cites research §5); `connect(max_attempts: int = 2)`
  forwarded to `brc.establish_connection`; the flow passes
  `max_attempts=1` (grep gate: exactly once, config_flow.py L440;
  default 2 in the client signature, aqua_client.py L147); the
  misleading CONNECT_TIMEOUT call-site comment replaced by the
  constructor-only/dead-inside-HA explanation (research §3.2), plus a
  comment-only clarification at the constant's definition in const.py.
- ☑ **Block C — explicit HA resolution** (C1/C2): `_resolve_device` HA
  branch is now tier 1 `connectable=True` (DEBUG provenance) → tier 2
  `connectable=False` (WARNING "connection may fail") → tier 3
  name-prefix walk over `async_discovered_service_info(hass,
  connectable=True)` with the existing multi-match and
  DIFFERENT-address warnings verbatim; the raw `BleakScanner` steps
  moved verbatim under the `hass is None` / ImportError-miss path
  (same texts, same 5 s timeouts). The lazy `try/except ImportError`
  + DEBUG guard is kept; both HA symbols come from one import.
- ☑ **Block D — `not_found` key**: added to strings.json,
  translations/en.json, translations/fr.json with the approved EN/FR
  texts (FR-10). `python3 -m json.tool` OK on all three; fr/en key
  sets identical (checked programmatically).
- ☑ **Block E — tests**: walkthrough gains `not_found` and
  budget-expiry cases + caplog (handler-based, no pytest API) asserts
  on the cannot_connect/no_response/crc_error branches; new
  `tests/test_resolve_device.py` (8 cases: tier 1/2/3, multi-match,
  all-miss → `AquaTempDeviceNotFoundError`, standalone passthrough ×2,
  `exc_chain` public+alias).
- ☑ **Block F1/F2**: all touched docstrings/comments English; git
  diff touches exactly config_flow.py, aqua_client.py, const.py
  (comment only), strings.json, translations/{en,fr}.json,
  test_config_flow_walkthrough.py (+ untracked
  tests/test_resolve_device.py). modbus.py, coordinator.py, entity
  platforms, manifest.json, write-guard/sentinel paths untouched
  (diff-grep).
- ☐ **Block F3 — commit**: NOT executed (Coder mission forbids
  commits/pushes; the orchestrator handles git). Suggested message
  kept for the orchestrator:
  `Fix device resolution visibility: log test failures, honest 40s budget, not_found error (spec 005)`

### Red-first evidence (tests run against unmodified 5aa887b first)

- `test_config_flow_walkthrough.py`: **5/15 FAIL** pre-fix — the three
  caplog assertions on existing failure cases (cannot_connect,
  no_response, crc_error: no log line existed), the `not_found` case
  (`module ... has no attribute 'AquaTempDeviceNotFoundError'`) and the
  budget-expiry **log** assertion (routing already passed pre-fix —
  documented in the test docstring). Post-fix: **15/15 PASS**.
- `test_resolve_device.py`: **7/8 FAIL** pre-fix (all HA-tier cases,
  device-not-found, `exc_chain` public). The single passing case
  (`test_standalone_find_by_address_passthrough`) is an NFR-4
  regression guard, not a red-first case. Post-fix: **8/8 PASS**.

### Gate results (real outputs)

- `py_compile` on config_flow.py + aqua_client.py + const.py: OK.
- `python3 custom_components/aqua_temp_ble/modbus.py`: SELF-TEST
  PASSED (all vectors).
- test_registers.py 11/11; test_modbus.py 11/11; test_reassembly.py
  15/15; test_config_flow_list.py 6/6; test_config_flow_walkthrough.py
  15/15; test_resolve_device.py 8/8.
- `python3 -m pytest tests/ -q`: 51 passed, 15 failed — the 15 failures
  are ALL the walkthrough's async test functions (pytest 9.1.1 has no
  async plugin installed: "async def functions are not natively
  supported"); pre-existing environment condition, identical class
  of failures on the 5aa887b baseline (13 async tests there). The
  walkthrough suite's documented runner is plain python (its
  docstring), where it is 15/15 green. test_resolve_device.py uses
  sync wrappers (asyncio.run inside), so its 8 tests pass under
  pytest too.
- `tools/smoke_client.py --help`: fails with ModuleNotFoundError: bleak
  — bleak is not installed on the dev box; verified IDENTICAL failure
  on the 5aa887b baseline (pre-existing). The modified aqua_client
  imports cleanly via the stubbed-bleak path (test_resolve_device.py
  exercises it), and the standalone path's behaviour is unchanged
  (FR-8: raw scan steps moved verbatim).

### FR-2 log table (what each failure branch logs — WARNING unless noted)

| Exception | UI key | Log line |
|---|---|---|
| AquaTempDeviceNotFoundError | `not_found` | `connection test to {addr}: device not found: {exc}` |
| AquaTempConnectionError | `cannot_connect` | `connection test to {addr} failed: {exc_chain}` |
| AquaTempTimeoutError | `no_response` | `connection test to {addr}: no response: {exc_chain}` |
| AquaTempResponseError | `crc_error` | `connection test to {addr}: bad response: {exc_chain}` |
| TimeoutError (budget) | `cannot_connect` | `connection test to {addr} exceeded the {TEST_TIMEOUT} s budget — ... likely not accepting the connection (single BLE link held by the vendor app?) or is out of range.` |
| other Exception | `cannot_connect` | `_LOGGER.exception` (ERROR, unchanged) |

Remaining [V] items (live instance): V-1..V-5 in quickstart.md —
untouched by the Coder, for the orchestrator/live QA.