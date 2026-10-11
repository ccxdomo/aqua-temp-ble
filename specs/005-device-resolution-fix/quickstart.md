# Quickstart 005 — Diagnosing the connection test with the new logging

For the owner and the Reviewer: how to reproduce, read, and act on the new
diagnostics, and what remains to verify on the live instance.

---

## 1. Enable the integration's logs (live HA)

Settings → System → Logs, or `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.aqua_temp_ble: debug
```

For a deep-dive session only (noisy), optionally add:

```yaml
    habluetooth: debug
    bleak_retry_connector: debug
```

- `habluetooth` debug reveals the connect path the wrapper chose
  ("Connecting via <scanner> (last rssi: …)" — habluetooth wrappers) and
  its per-address connection-path list (7.1.2 logs "Found N connection
  path(s), preferred order: …" at INFO).
- `bleak_retry_connector` debug reveals each connect attempt and its
  per-attempt error (all brc attempt logs are DEBUG-only — research §3.3).

Then reload the logger (`logger.reload` service or restart) and rerun the
config flow test.

## 2. The decision table — what each new log line means

| Log line (aqua_temp_ble) | Meaning | Next action |
|---|---|---|
| `resolved … from HA connectable cache` (DEBUG) | tier-1 hit; the unit is currently visible to a connectable scanner | none — the connect attempt got a live path |
| `only visible via a non-connectable scanner; the connection may fail` (WARNING) | tier-2 hit; no connectable scanner currently sees it | expect `No backend…` connect failure; add a local adapter or connectable proxy near the unit |
| `not found in the HA bluetooth cache; looking for a name-prefix match` (WARNING) | tier-3 entered; address is stale (MAC change or unit long gone) | if followed by `device not found` → unit stopped advertising: pairing mode + rescan |
| `connection test to …: device not found: …` (WARNING, key `not_found`) | resolution missed everywhere | unit off / out of range / not in pairing mode; form already shows the pairing-mode advice |
| `connection test to … failed: …` (WARNING, key `cannot_connect`) | terminal connect failure; the chain names the real error (e.g. `BleakConnectionError … No backend with an available connection slot …` = listed but no live scanner path, research §2.3; or refused/aborted errors) | follow the chain: "No backend" → scanner gap (rescan later or add a proxy); abort errors → vendor app / range |
| `connection test to …: no response: …` (WARNING, key `no_response`) | connected, read timed out (8 s) | move closer; unit accepted the link — vendor app is NOT holding it |
| `connection test to …: bad response: …` (WARNING, key `crc_error`) | reply corrupted | retry; if persistent, capture the log for spec follow-up |
| `connection test to … exceeded the 40 s budget …` (WARNING, key `cannot_connect`) | the old silent path — connect or read never finished in time | the unit did not accept the connection in time: close the vendor app completely (single BLE link — FINDINGS §3), power-cycle the unit, retry; if it recurs, grab `bleak_retry_connector` debug on the next run |
| `unexpected error during connection test` (ERROR + traceback) | anything else | file the traceback |

Reading order on a failed test: the WARNING from `_async_test` names the
stage; any preceding resolution WARNING tells you how far visibility got;
`bleak_retry_connector` debug (if enabled) shows each attempt's raw error.

## 3. Reproducing the old silent failure (pre-fix baseline)

On the current (`5aa887b`) code the failure reproduces as: pick the listed
unit → spinner ~20 s → "Failed to connect…" → **no log**. After this spec
ships, the same scenario must produce one of the lines above (AC-1/AC-3)
— and if the connect was merely slow (≤ 40 s), the test may now simply
succeed (AC-4).

## 4. Standalone smoke (NFR-4, no HA)

```bash
python3 tools/smoke_client.py <ADDRESS>   # raw scans, 15 s connect attempts
```

Behaviour and log texts are unchanged from `5aa887b` (raw
`find_device_by_address` / `discover`, 5 s scan windows, 2×15 s connect
attempts). This path is the only one that still starts real scans.

## 5. [V] checklist — needs the live instance / real unit

- **V-1** Which failure actually occurs on the owner's next test (H1
  vendor app / H2 late-connect / H3 stale link — research §6). The
  decision table converts it into one run.
- **V-2** Whether the unit stops advertising while the vendor app holds
  it (would later produce the `No backend…` chain after the 195 s scanner
  expiry — research §2.1/§2.3).
- **V-3** AC-4 end-to-end: a previously-failing-but-slow connect now
  completes within 40 s and reaches the confirm form.
- **V-4** Frontend rendering of the `not_found` key on both origin forms
  (device list, manual) — same mechanism as existing keys, low risk.
- **V-5** UX of a longer spinner worst case (~40 s on a hanging connect;
  successful tests are expected ≪ 40 s).

## 6. Rollback

All changes are in-component (no migrations, no state): reverting the
commit restores the `5aa887b` behaviour exactly. `TEST_TIMEOUT`,
`max_attempts=1`, the `not_found` key and the log lines are independent —
any subset can be kept if a hotfix must be partial.