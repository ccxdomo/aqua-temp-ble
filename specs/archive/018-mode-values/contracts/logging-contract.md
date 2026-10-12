# Contract 018-B — The block-census logging amendment

Normative level change for the per-frame block census in
`aqua_client._census_frame`. This contract AMENDS contract 008-B §5's
level rule for the BLOCK line only; every other rule of 008-B (the
counters, the census-in-timeout-message, the never-fatal discipline)
and of 009 §3 (the census timing/offsets) stands unchanged.

> A census line designed for a rare anomaly became a per-cycle INFO
> firehose the moment push mode made block bursts the NORMAL answer.
> The evidence survives in the store, the capture WARNING and the
  timeout summary — the per-frame line does not need INFO to be kept.

## §1 The change (normative)

The per-frame block census line — `block push observed: start=%d
qty=%d len=%d crc=%s (waiting for start %s; t+%.2fs)` and its
CRC-invalid twin — logs at **DEBUG**, not INFO, in every mode.

Unchanged:
- the request counters (`_req_frame_count`, `_req_block_starts`,
  `_req_first_seen`, `_req_last_seen`) — the timeout message's census
  summary is computed from them (008-B §6 / 009 §3) and MUST keep
  working;
- the once-per-connect `block push captured: …` WARNING (contract
  013 §2.1 — the capture mechanism; ≤ 4 lines per connect);
- the idle-window summary WARNING (contract 009 §3 / 013 §2.2);
- the first-idle block push INFO (once per connect, bounded);
- the generic non-block `frame observed: fc=…` line (INFO, rare);
- the store feed itself (contract 013 §3.1 — data, not logs).

## §2 Why (the production observation)

The owner's log page shows Home Assistant's own rate-limit verdict:

```
Module custom_components.aqua_temp_ble.aqua_temp_ble.aqua_client is
logging too frequently: 200 messages since last count
```

(that message is HA's own filter, not ours: when a module logger's
record volume passes its threshold, HA logs that line and then
suppresses the module's records for a mute window — HA internals; the
threshold constants are not restated here.)

Our steady-state volume in push mode, per 30 s poll cycle from the
`aqua_client` logger: ≥ 4 INFO block-census lines (the trigger's
burst: 1001/1046/2001/2046, none matching the pending base read) plus
4 more per button-press confirmation trigger — plus, on every
reconnect, the per-connect set (the GATT enumeration lines, the
connect summary, `connecting to`/`Connected to`, the 4 capture
WARNINGs, the idle summary). That is the driver of the rate limit.

The harm is not cosmetic: the mute window swallows records from the
SAME logger that carries contract-mandated evidence — `block push
captured` (contract 013 §2.1's capture mechanism, the U-1 evidence
line), `BLE connection lost`, the write-mode advisories. Evidence
discipline (contract 012 §2) is why those lines are WARNING; the INFO
noise must not be allowed to mute them.

## §3 What the census information loses at DEBUG: nothing

- The DATA is in the pushed-block store (every CRC-valid block frame,
  idle or in-flight — contract 013 §3.1) and surfaces in the
  `push_stream` diagnostic (per-start counts, first/last ages).
- The per-REQUEST summary rides in the `AquaTempTimeoutError` message
  (`received N frame(s) / M bytes; blocks seen: 1001x1, …; first t+…,
  last t+…`), which the coordinator logs once per cycle at INFO
  (`push cycle: trigger read timed out as expected (census: …)`) —
  one line per cycle instead of 4+.
- The per-CONNECT capture evidence stays at WARNING (013 §2.1).

An integrator or owner who wants the per-frame lines enables DEBUG
for `custom_components.aqua_temp_ble.aqua_client` (the calibration
context — where per-frame visibility matters, DEBUG is the right
lens).

## §4 Test obligations

The census level is pinned by tests: a block frame received while a
request is pending produces NO `block push observed` record at INFO
or above; the DEBUG record still exists; the timeout census summary
still counts the frames and block starts. The suites that pinned the
old INFO level (009's census-offset tests, 013's store tests, the
reassembly-matching census assertions) are updated with this
contract, not deleted.