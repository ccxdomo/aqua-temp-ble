# Contract — Read frame (FC03)

Status: **PROVEN** — the frame below was recovered from the vendor APK's
object pool and verifies CRC byte-exact. It is asserted in
`tests/test_modbus.py` and `modbus.py`'s self-test.

Transport: written to GATT characteristic
`0000ff01-0000-1000-8000-00805f9b34fb`; response arrives on ff02 (see
`ble-response.md`).

---

## 1. Frame format

```
63 03 [start:2 BE] [count:2 BE] [crc_lo] [crc_hi]
```

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 1 | slave address | `0x63` |
| 1 | 1 | function code | `0x03` (read holding registers) |
| 2–3 | 2 | start register (big-endian) | base block: `0x0007` |
| 4–5 | 2 | register count (big-endian) | base block: `0x002D` (45) |
| 6–7 | 2 | CRC16-Modbus, **little-endian** (low byte first) | computed over bytes 0–5 |

## 2. Verified example (regression vector)

```
63 03 00 07 00 2D 3C 54        hex: 63030007002D3C54
```
- start = 7, count = 45 → reads holding registers **7..51**
- CRC over `63030007002D` = `0x543C` → wire bytes `3C 54` ✓

## 3. Semantics of the base block read

- Response: standard FC03 (see `ble-response.md`), 90 data bytes = 45
  signed int16 registers, big-endian ([A]; flip
  `REG_DATA_LITTLE_ENDIAN` after calibration if byte-swapped).
- This is the **only** read the poll loop performs — exactly one frame per
  poll cycle (default 30 s), never per-register reads.
- Register meanings and scaling: `data-model.md` §2. Temperatures ÷10,
  sentinel `-9990` = sensor absent.

## 4. Builder / parser contract

- Build: `modbus.build_read_holding_registers(start, count)` — pure
  function, returns the 8-byte frame.
- Parse: `modbus.parse_read_response(frame, start, count, little_endian)`
  → `{register: signed int16}`; raises `ModbusCRCError` on bad CRC,
  `ModbusExceptionError(code)` on `fc|0x80`, `ModbusError` on shape
  mismatch (bytecount ≠ 2·count, wrong length).
- Length rule: an FC03 response is `data_bytecount + 5` bytes total.

## 5. Error behavior

| Case | Behaviour |
|---|---|
| No response within 8 s (`REQUEST_TIMEOUT`) | `AquaTempTimeoutError` — a CRC-valid partial buffer is accepted at the deadline if one arrived |
| CRC mismatch | `AquaTempResponseError(ModbusCRCError)`; client disconnects on next cycle for a clean slate |
| Modbus exception frame (5 bytes, `fc=0x83`) | `AquaTempResponseError(ModbusExceptionError(code))` |
| Connection lost | pending request fails with `AquaTempConnectionError`; lazy reconnect next cycle |