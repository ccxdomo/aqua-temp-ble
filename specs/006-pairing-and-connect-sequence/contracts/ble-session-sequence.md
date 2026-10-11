# Contract 006 — BLE session sequence (vendor-verified)

The GATT/Modbus session contract between client and PAC, as the vendor app
performs it (research 006 §2 — all steps verbatim from the app's session
manager). The HA client (`aqua_client.py`) must match this shape; deviations
are bugs. Legend: **[P]** proven in the vendor app, **[V]** to-verify live.

---

## 1. Preconditions

| # | Condition | Status |
|---|---|---|
| P-1 | Unit powered (panel display active) | observable |
| P-2 | Unit advertising, name matching `BlueNRG-*` or `WT-*` (normal mode). Provisioning-mode names (`BLE_*`, `PROV_*`) belong to the WiFi models' config flow. | **[P]** research §3 |
| P-3 | Link free: a BLE peripheral accepts exactly ONE central. The vendor app auto-reconnects — it must be closed (and phone Bluetooth off) while another client connects. | **[P]** research §2.2, §9 |
| P-4 | No pairing/bonding/PIN, no unlock frame — none exists. Configuration mode (`bLE` display) is NOT a proven precondition. | **[P]** research §8, §1.3 |

## 2. Session establishment (exact order)

```
S1  connect
    ├── timeout: 20 s per attempt (vendor constant; HA: bleak-retry-
    │   connector hardcodes the same 20 s)
    ├── NO bonding request, NO MTU precondition, NO pre-connect frame
    └── retries: vendor retries failed connects before giving up
        (HA: max_attempts=2 in the config-flow test — spec 006 FR-3)

S2  discover services
    └── expect: 1800 (2a00, 2aa6), ff01 → char 2214,
        ff02 → char 2216, ff03 → char 2215 (ff03 unused by the client)

S3  subscribe ff02 notifications
    ├── CCCD 00002902-0000-1000-8000-00805f9b34fb
    ├── vendor logs "was the subscription successful" and treats
    │   failure as fatal for the session
    └── HA: start_notify(UUID_NOTIFY) — current behaviour, unchanged

S4  settle — 2 s after the subscribe before ANY request
    └── [P] vendor 2 s Duration in the subscribe→read path
        (HA: SETTLE_AFTER_SUBSCRIBE = 2.0 — spec 006 FR-1)

S5  first frame — FC03 base read: 63 03 0007 002D 3C54
    ├── slave 0x63, start register 7, count 45
    └── nothing precedes it in a fresh session
```

**Forbidden before S5:** any other frame, any token/password write, any
time-sync write (research §5 — the time-sync is a manual settings feature,
never a session opener).

## 3. Steady state

```
R1  Polling: FC03 base read; on-demand block queries
    (03E9/0416/0443/0470/07D1/07FE — status & control-parameter pages)
R2  Responses arrive on ff02 as notifications; MUST tolerate any
    chunking (20-byte chunks at default MTU; a single ~95-byte
    notification at higher MTU) — reassemble, then CRC16-Modbus (LE)
    validate, then function-code validate
R3  One request in flight at a time (single-flight request/response —
    the vendor sends no request while awaiting a response)
R4  Writes: FC16 (63 10 [reg] [qty] [bytes] [data] [CRC-LE]) written
    WITHOUT response when ff01 advertises the property
    (HA: prefer response=False — spec 006 FR-2)
R5  On abnormal disconnect: the vendor auto-reconnects (retry count
    [V]) and gives up with a user error after max retries
```

## 4. Failure classes & required client behaviour

| Class | Signal | Client behaviour |
|---|---|---|
| Connect refused / timeout | bleak connect error or 20 s timeout ×N | raise `AquaTempConnectionError` with cause chain; config flow shows `cannot_connect` + runbook (quickstart 006) |
| Device not advertising | resolution miss | `AquaTempDeviceNotFoundError` → `not_found` (spec 005) |
| Connected, no answer | read deadline (8 s) | `AquaTempTimeoutError` → `no_response` |
| Bad frame | CRC/FC/shape validation | `AquaTempResponseError` → `crc_error` |
| Link lost mid-request | disconnected_callback | fail pending request immediately; clean teardown for next cycle |

## 5. Out-of-contract observations (do not build on them)

- `ff03` (char 2215): role unknown — the vendor logs its presence but no
  read/write of it is evidenced. **[V]** — do not use (const.py note).
- Register 1267-1270 (0x04F3…): the manual time-sync area
  (`631004f30004080001` + 6-byte clock + CRC, layout **[V]**). Not a
  session element; a future optional service may use it after live
  verification (research §5.1).
- `requestMtu`: referenced in the vendor's plugin layer; caller & value
  **[V]** — the client must not depend on any negotiated MTU (R2).
- Configuration mode (`bLE` on the panel): a WiFi-provisioning gesture;
  not part of this contract. If a live experiment proves the owner's unit
  gates GATT behind it, this contract gains precondition P-5 and the
  quickstart step 3 becomes mandatory — until then it is a diagnostic
  experiment. **[V]**