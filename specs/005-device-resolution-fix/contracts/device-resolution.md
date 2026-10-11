# Contract 005 — Device resolution & connection-test failure reporting

Normative for the Coder; this file is the single source of truth for the
resolution order and the failure-routing behaviour. Provenance labels
**[P]/[D]** refer to `research.md` / `spec.md`.

---

## 1. Inputs / outputs

```
_resolve_device(address: str, hass: HomeAssistant|None) -> BLEDevice|None
```

- **Inputs**: the configured address (string, any case — lookups are
  dict-key exact, so the address must be the one the flow captured
  verbatim from the list / manual entry), the optional `hass` instance,
  `const.NAME_PREFIXES`.
- **Outputs**: a `BLEDevice` plus its provenance (log line), or `None`.
- **Invariants**:
  - Inside HA the function performs **no scans and no I/O** — every tier
    is a synchronous cache read **[P]**; total time ≈ 0 s.
  - The result is consumed by `establish_connection`, which inside HA uses
    only its address (the HA wrapper re-resolves the live scanner path at
    connect time) **[P]** — so resolution is a *visibility check with an
    address carrier*, not a routing decision.
  - A `None` result means: the address is in neither manager-history tier
    and no cached connectable device name-matches a prefix.

## 2. State machine (HA mode)

```
        ┌────────────────────────────────────────────┐
        │ connectable_history[address]?              │ yes → return (tier 1, DEBUG)
        └──────────────┬─────────────────────────────┘
                       │ no
        ┌──────────────▼─────────────────────────────┐
        │ all_history[address]? (non-connectable)    │ yes → return (tier 2, WARNING
        └──────────────┬─────────────────────────────┘      "connection may fail")
                       │ no
        ┌──────────────▼─────────────────────────────┐
        │ name-prefix match over discovered         │ enter: WARNING (truthful text)
        │ service info (connectable=True)            │ match → return (tier 3; keep
        └──────────────┬─────────────────────────────┘ existing multi-match /
                       │ no match                      different-address warnings)
                       ▼
                 return None ──▶ connect() raises AquaTempDeviceNotFoundError
```

Standalone mode (`hass is None`): `find_device_by_address(5 s)` →
`discover(5 s)` + prefix filter → `None`; identical to `5aa887b` (NFR-4).

## 3. Failure-reporting contract (`_async_test`)

1. **No silent failures**: every exception that sets a `_test_error` key
   produces exactly one log line (WARNING or higher) containing the
   address and the real exception (chain rendered via `exc_chain` for
   wrapper exceptions). AC-1.
2. **Keys are stable**: `not_found` (new), `cannot_connect`,
   `no_response`, `crc_error` — the routing table in `data-model.md` §2 is
   exhaustive.
3. **Budget expiry is reported as itself**: the `TimeoutError` branch
   names the `TEST_TIMEOUT` value and the likely causes in the log — even
   though the user-facing key remains `cannot_connect` (its advice is the
   correct action).
4. **Routing is spec-004's**: errors surface on the origin form
   (`manual` for manual entry, `device` otherwise) through
   `progress_done`; the `_test_error` stash-and-clear mechanics are
   unchanged.

## 4. Version boundary

- HA APIs used: `async_ble_device_from_address`, (new, explicit)
  `async_discovered_service_info` — both present and identical at
  **2024.12.0** (api.py L70/L86) and **2026.10.1** (L95/L111) **[P]**.
- 2026-era APIs (`async_request_active_scan`,
  `async_address_reachability_diagnostics`) are forbidden (NFR-3).
- No reliance on the bleak monkeypatch (it exists — research §3.1 — but
  the explicit APIs make the contract independent of it).

## 5. Acceptance hooks (what the Reviewer checks)

- `grep`-level: no `self._test_error = "..."` assignment without a log
  statement in the same branch; no "scanning" wording in HA-mode log
  texts; `max_attempts=1` present exactly in the flow path; `TEST_TIMEOUT
  == 40.0`.
- Behavioural: walkthrough cases `not_found`, budget-expiry,
  `cannot_connect` (existing), success, already-configured; resolution
  unit tests for the four HA tiers + standalone passthrough.
- On the live instance: the quickstart decision table maps every new log
  line to its meaning and next action.