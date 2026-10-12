# Contract — Write frame (FC10, H1 layout)

Status: **[A] layout, [P] framing** — the FC10 frame shape and CRC are
recovered from the APK; the single-register 16-bit **H1 layout** is the
chosen safe default (see `research.md` §7). All example frames below are
CRC-verified and asserted byte-exact in the self-test.

Transport: written to GATT characteristic `ff01`. The device may echo the
8-byte header on `ff02` (expected by default; switch
`WRITE_ECHO_EXPECTED`).

---

## 1. Frame format (H1 — one 16-bit register)

```
63 10 [addr:2 BE] [qty=0001] [bc=02] [value:2 BE] [crc_lo] [crc_hi]
```

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 1 | slave address | `0x63` |
| 1 | 1 | function code | `0x10` (write multiple registers) |
| 2–3 | 2 | register address (BE) | 8 / 13 / 14 |
| 4–5 | 2 | register quantity | `0x0001` (H1) |
| 6 | 1 | byte count | `0x02` (H1) |
| 7–8 | 2 | value (BE, `& 0xFFFF`) | e.g. `0x0001`, `0x0145` |
| 9–10 | 2 | CRC16-Modbus, little-endian | computed over bytes 0–8 |

H2 variant (32-bit, qty=2/bc=4) exists as an APK hint; if the device
expects H2, an H1 frame is at worst ignored — it can never clobber a
neighbouring register. Flip via calibration (logcat capture, runbook §5a).

## 2. Verified example frames (regression vectors)

| Operation | Frame (hex) | CRC check |
|---|---|---|
| Power ON (reg 8 = 1) | `631000080001020001D7BA` | `0xBAD7` → wire `D7 BA` ✓ |
| Power OFF (reg 8 = 0) | `631000080001020000167A` | `0x7A16` → wire `16 7A` ✓ |
| Mode cool (reg 13 = 0) | `6310000D0001020000162F` | `0x2F16` → wire `16 2F` ✓ |
| Mode heat (reg 13 = 1) | `6310000D0001020001D7EF` | `0xEFD7` → wire `D7 EF` ✓ |
| Mode auto (reg 13 = 2) | `6310000D000102000297EE` | `0xEE97` → wire `97 EE` ✓ |
| Setpoint 32.5 °C (reg 14 = 325) | `6310000E0001020145D67F` | `0x7FD6` → wire `D6 7F` ✓ |

Value scaling for reg 14: `value = int(degrees_C × 10)` (32.5 °C → 325 →
`0x0145`).

## 3. Write policy (hard rules)

- Only registers **8 (Power)**, **13 (Mode)** and **14 (SetCurrentTemp)**
  are written by the integration. Optional: 19/20/21 (per-mode setpoint
  mirror) behind `WRITE_MODE_SETPOINT_TOO = False` (default off).
- Blocks **1001–2090 are never written** (probe is read-only).
- Writes are serialized against reads via one `asyncio.Lock`; each write is
  followed by one debounced confirm refresh (`async_request_refresh`) —
  no optimistic state.
- Turn-on order: **reg 8 = 1 first, then reg 13** (a mode write while
  powered off may be ignored).
- Setpoint is clamped to the device-reported bounds (min/max of
  regs 22–25) in display units before conversion to °C ×10.

## 4. Echo / acknowledgement

- Default (`WRITE_ECHO_EXPECTED = True`): the client waits up to 3 s
  (`WRITE_ECHO_TIMEOUT`) for the 8-byte echo
  `63 10 addr:2 qty:2 CRC` on ff02; mismatched addr/qty or bad CRC raises
  `AquaTempResponseError`. A 5-byte **exception echo**
  (`63 90 code CRC`) surfaces as `ModbusExceptionError(code)`.
- If the device sends no echo at all (found during calibration): set
  `WRITE_ECHO_EXPECTED = False` → writes become fire-and-forget; the
  debounced confirm refresh is then the only acknowledgement.

## 5. Builder / parser contract

- Build: `modbus.build_write_registers(start, values)` — H1 for a
  single value; N registers are packed as qty=N, bc=2N (only N=1 is used
  today).
- Parse: `modbus.parse_write_echo(frame, start, qty)` — validates length 8,
  CRC, function code (or exception), and echoes back the exact
  (start, qty) pair.

## 6. High-block query frames (read-only selectors, FC10-shaped)

Distinct from writes: bare 8-byte **query selectors** with no bytecount/data
— recovered from the APK and used only by the probe tool:

```
63 10 [start:2 BE] [qty=2 BE] [CRC:2]
631003E9002DD9E6  (1001–1045)   63100416002DE8A2  (1046–1090)
63100443002DF8B2  (1091–1135)   63100470002D08BD  (1136–1180)
631007D1002D591B  (2001–2045)   631007FE002D68D2  (2046–2090)
```

Response shapes are **[V] UNVERIFIED**; the probe accepts any CRC-valid
buffer (silence/deadline rules) and logs raw hex. Never written to.