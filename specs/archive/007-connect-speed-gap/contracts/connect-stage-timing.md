# Contract 007 — Connect stage-timing log format

Normative for `AquaTempBleClient.connect()`, `config_flow._async_test()`
and their tests. Greppability first: every line below is designed so a
one-line `grep` answers a runbook question. All identifiers are
ASCII-lowercase; durations are seconds with one decimal for totals and
two for short stages.

## 1. Stage names (closed set)

| Stage | Meaning | Owned by |
|---|---|---|
| `resolve` | `_resolve_device()` (cache tiers inside HA, raw lookups standalone) | aqua_client |
| `connect` | `establish_connection()` / raw-client loop — link + GATT discovery inside bleak's window (research 007 §4) | aqua_client |
| `notify` | `start_notify(ff02)` — CCCD write | aqua_client |
| `write-char` | `_resolve_write_characteristic()` — ff01 lookup + property decision | aqua_client |
| `settle` | `SETTLE_AFTER_SUBSCRIBE` sleep (spec 006 FR-1) | aqua_client |
| `first-read` | first `read_registers()` of a session (test/poll) | caller (config_flow / coordinator / smoke tool) |
| `disconnect` | teardown | caller |

## 2. INFO lines (aqua_client)

### 2.1 Attempt-window line (FR-2) — emitted before the connect call

```
connecting to <address>: up to <N> connect attempt(s) of 20 s each (bleak-retry-connector BLEAK_TIMEOUT, research 007 §4)
```

Standalone path variant (brc absent): `… of 15 s each (raw bleak, const.CONNECT_TIMEOUT)`.
Greps: `connecting to` (count attempts runs), `attempt(s)`.

### 2.2 Success summary (FR-1) — emitted at the end of a successful `connect()`

```
connect summary <address>: total <T.1f> s (resolve <r.2f>, connect <c.1f>, notify <n.2f>, write-char <w.2f>, settle <s.1f>)
```

Invariants:
- Exactly one line per successful connect.
- Stage order in the line is fixed (§1 order).
- Every duration ≥ 0.0; the total is the sum of the stages (fake-clock
  tests assert this).
- Followed (not preceded) by the existing `Connected to <address> (<name>)`
  line, which is kept verbatim for compatibility.

Grep recipes (runbook step 1):
- `grep "connect summary" home-assistant.log` — the stage table of every
  successful session.
- Post-connect stall check: `connect` duration > 15 s on a success ⇒ the
  link/discovery itself is slow ⇒ H-A/H-B territory (research §9).

## 3. Failure lines

### 3.1 aqua_client — connect-stage failure (extends spec 005 FR-2, chain verbatim)

```
connect to <address> failed at stage connect after <e.1f> s: <exc_chain>
```

- `<exc_chain>` is unchanged (`exc_chain(exc)`) — for the observed stall it
  reads `BleakNotFoundError: <name> - <address>: Failed to connect after 2
  attempt(s) … <- TimeoutError` (research 007 §4/§8). No word of the chain
  may be dropped, it is the stage proof.

### 3.2 aqua_client — post-connect setup failure (extends the existing line)

```
post-connect setup (ff02 notify / ff01 lookup) on <address> failed at stage <notify|write-char> after <e.1f> s: <exc_chain>
```

### 3.3 config_flow `_async_test` — every failure branch (FR-3)

```
connection test to <address> failed at stage <stage> after <e.1f> s (budget 70 s): <exc_chain>
```

Existing message heads (`device not found`, `no response`, `bad response`,
`exceeded the … budget`) are preserved; the suffix
`at stage <stage> after <e.1f> s (budget 70 s)` is appended to each.
UI error keys are unchanged (spec 007 NFR-2) — the suffix is log-only.

Stage value by branch:
| Branch (spec 005 FR-4 mapping) | stage |
|---|---|
| `AquaTempDeviceNotFoundError` | `resolve` |
| `AquaTempConnectionError` (connect raised) | `connect` |
| `AquaTempConnectionError` (raised from connect()'s post-setup) | `notify` or `write-char` (from the chain) |
| `AquaTempTimeoutError` | `first-read` |
| `AquaTempResponseError` | `first-read` |
| `TimeoutError` (test budget) | the stage the cancellation hit (`connect` or `first-read`) |
| unexpected `Exception` | `unknown` |

## 4. Time seam

`aqua_client._now()` (module-level, wraps `time.monotonic()`) is the only
clock the instrumentation reads. Tests patch `aqua_client._now`, never
`time.monotonic` globally. The config flow uses `time.monotonic()`
directly for its own elapsed (single value, no seam needed) — tests may
patch `config_flow`'s local via the module attribute it reads from
(`time.monotonic` monkeypatched on the `config_flow` module namespace).

## 5. What the next failing run must produce (runbook hand-off)

A runbook-ready capture of ONE failed test contains, in this order:

1. `connecting to 08:06:A6:C0:94:1C: up to 2 connect attempt(s) …`
2. `connect to 08:06:A6:C0:94:1C failed at stage connect after ~40.5 s:
   BleakNotFoundError: … <- TimeoutError`
3. `connection test to 08:06:A6:C0:94:1C failed at stage connect after …`

If (2) shows `after ~40.5 s` with the `TimeoutError` chain, the connect-stage
stall (research §8) is CONFIRMED and the runbook proceeds to the HCI
differential (steps 3-4). If instead the summary shows a successful
`connect` followed by a `first-read` failure, the diagnosis pivots to the
post-connect class (research §9 decision table) — a different, fixable bug
class that no artifact to date exhibits.