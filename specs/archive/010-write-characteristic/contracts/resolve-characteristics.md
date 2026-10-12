# Contract — characteristic resolution (app-parity, spec 010)

Normative for `aqua_client.py`'s notify/write resolution (FR-2/FR-3).
The same fake tables back the tests (plan §4) — contract, tests and code
must stay in lock-step. Source of truth for every row: the vendor app's
disassembled classifier (research 010 §1-2).

## Inputs

- `client` — a BleakClient whose GATT table is enumerated as
  `GattCharInfo(service_uuid, uuid, properties)` entries.
- `const.UUID_NOTIFY_BUCKETS` = (ff02, 2216, 2a00)
- `const.UUID_WRITE_BUCKETS`  = (ff03, 2215, 2aa6)

## Rules

1. **Bucket pass (property-blind).** For each role, walk the bucket
   tuple in order; the first UUID the device exposes as a
   characteristic wins. Advertised properties are advisory (logged,
   never decisive) — the app selects the same way (research §1.1).
2. **Advisory logging.**
   - notify char lacks `notify` → WARNING `notify characteristic …
     advertises no notify property — app parity: subscribing anyway`.
   - write char lacks both write properties → WARNING `write
     characteristic … advertises no write property — app parity:
     writing anyway`.
3. **Write mode (FR-3).**
   - `write-without-response` advertised → write WITHOUT response
     (app parity: the app's only WriteType).
   - only `write` advertised → write WITH response + WARNING (ATT
     forbids Write Commands on write-only chars).
   - no write properties at all → write WITHOUT response + WARNING
     (app parity — the app is property-blind; BlueZ may refuse, which
     is itself evidence, see FR-5).
4. **Fallback pass.** If NO bucket UUID exists in the table: keep the
   current adaptive scan (vendor-service property-gated candidates,
   then the listing) — unchanged safety net for unknown layouts.
5. **Failure.** No notify char and no write char after both passes →
   the current loud error, message unchanged.

## Truth table (fake tables ⇄ tests ⇄ behaviour)

| # | GATT table (svc {chars(props)}) | notify | write | write mode | advisory |
|---|---|---|---|---|---|
| T1 | ff01 {ff02(notify), ff03(write-wo-response, write)} | ff02 | ff03 | without-response | none |
| T2 | ff01 {ff02(notify), ff03(no write props)} | ff02 | ff03 | without-response | WARNING both (rules 2/3) |
| T3 | ff01 {ff02(notify), ff03(write only)} | ff02 | ff03 | **with-response** | WARNING (rule 3) |
| T4 | 2214 {2216(notify), 2215(write-wo-response)} | 2216 | 2215 | without-response | none |
| T5 | 1800 {2a00(read), 2aa6(read)} (GAP-only unit) | 2a00 | 2aa6 | without-response + WARNING | WARNING both |
| T6 | ff9x {charX(write-wo-response)} (unknown vendor) | — (adaptive: none → error path unchanged for notify-less tables) | charX via adaptive | without-response | INFO fallback line (existing format) |
| T7 | 1801 {2A05(indicate)} only | loud error | loud error | — | — |
| T8 | ff01 {ff02, ff03} + 2214 {2215} (collision) | ff02 | ff03 (bucket order = first-wins) | per ff03 props | — |

Note on T5: the app itself classifies 2a00/2aa6 into its buckets
(research §1.3, last-wins overwrite in its service loop). Our
first-wins bucket walk selects the same chars whenever a vendor
service exists — the only divergence is on GAP-only tables where the
app would do the same thing this contract specifies.

## Out of scope (guards)

- No property may ever *disqualify* a bucket char (rule 1).
- No change to the INFO census lines, the request frames, the
  settle/timeout constants, or the Modbus layer.