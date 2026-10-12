# Contract — BLE response (GATT notifications on ff02)

Status: framing **[P]** (recovered/verified), chunking behaviour **[V]**
(design covers 1–95-byte responses). This is the transport-level contract
the client (`aqua_client.py`) implements; see `read-frame.md` for what is
read and `write-frame.md` for echo handling.

Transport: responses to any request arrive as **GATT notifications** on
characteristic `0000ff02-0000-1000-8000-00805f9b34fb` (subscribed with
`start_notify` at connect time). A response MAY be fragmented across
several notifications (typical ~20-byte chunks at default MTU; a single
notification is possible with a higher MTU — actual chunking unverified).

---

## 1. Frame shapes the client accepts

| Response | Shape | Total length | Notes |
|---|---|---|---|
| FC03 read | `63 03 bc [bc bytes] CRC:2` | `bc + 5` | base block: `bc=90` → **95 bytes** |
| FC10 echo | `63 10 addr:2 qty:2 CRC:2` | **8 bytes** | write acknowledgement |
| Exception | `63 [fc|0x80] code CRC:2` | **5 bytes** | e.g. `63 83 02 …` (illegal data address) |

Length derivation rule (`modbus.expected_response_length`): from the first
bytes of the buffer, slave `0x63` must be byte 0; `fc&0x80` → 5; `fc=0x03`
→ `byte2 + 5`; `fc=0x10` → 8; anything else → unknown (None).

## 2. Reassembly rules (in priority order)

1. **Expected length** (primary): as soon as the buffer length reaches the
   implied total, the frame is complete — any extra bytes in the same
   chunk are dropped.
2. **Silence gap** (`SILENCE_TIMEOUT = 0.6 s`): if no new chunk arrives for
   0.6 s and the buffer is a CRC-valid frame, it is completed. Covers
   unknown-shape responses (high-block queries [V]).
3. **Request deadline** (`REQUEST_TIMEOUT = 8 s`, or 3 s for the write
   echo): at the deadline, a **CRC-valid** buffer that arrived is accepted;
   otherwise the request fails with `AquaTempTimeoutError`.

Other transport guarantees:

- **Stale chunks are dropped**: notifications arriving with no pending
  request, or after the pending future is done, never corrupt the next
  request (buffer cleared).
- **Garbage prefix**: a buffer whose byte 0 ≠ `0x63` is dropped whole
  with a warning.
- **Function-code filter**: with a pending request expecting `fc`, chunks
  building a different `fc` (or `fc|0x80` other than the exception form)
  are dropped.
- **Buffer cap** (`MAX_RX_BUFFER = 512`): an over-long buffer is dropped
  and the pending request fails `AquaTempResponseError`.
- Every completed frame is **CRC-validated** and function-code-validated
  in `_complete()` before the caller resumes — no partial/garbage data ever
  reaches the coordinator.

## 3. bleak notification signature

`_on_notify(self, *args)` uses `args[-1]` — bleak < 0.22 calls
`(sender, data)`, newer bleak calls `(data,)`. Both covered; asserted by
the reassembly tests.

## 4. Single-flight guarantee

At most **one** Modbus request is in flight at any time (`_pending`
future). A concurrent caller fails fast with
`AquaTempError("another request is already in flight")` instead of
interleaving on the notify pipe. The coordinator serializes reads and
writes behind one `asyncio.Lock`, so this is a safety net, not the primary
gate.

## 5. Error mapping

| Condition | Exception raised to caller |
|---|---|
| CRC invalid on completed frame | `AquaTempResponseError(ModbusCRCError)` |
| Exception frame (`fc&0x80`) | `AquaTempResponseError(ModbusExceptionError(code))` |
| Response too long / malformed | `AquaTempResponseError` |
| Deadline with no CRC-valid buffer | `AquaTempTimeoutError` |
| Connection lost mid-request | `AquaTempConnectionError` (from the disconnected callback) |
| GATT write failure | `AquaTempConnectionError` |

## 6. Reference vectors (asserted in `tests/test_reassembly.py`, 15 tests)

- 95-byte FC03 response delivered as: single notification; 20-byte chunks;
  2 chunks; 1 chunk + remainder.
- Leading garbage before `63…` → dropped, then a clean frame completes.
- Corrupted CRC → `ModbusCRCError` raised, pending fails.
- Truncated FC03 (`bytecount` says 95, only 20 arrive) → silence rule keeps
  waiting → deadline → `AquaTempTimeoutError`.
- Deadline acceptance: buffer complete + CRC-valid at deadline → accepted.
- Write echo (8 bytes) validates; 5-byte exception echo raises
  `ModbusExceptionError(2)`; wrong addr/qty echo raises.
- Stale notification (no pending request) → dropped, no state corruption.
- Single-flight guard → second concurrent request fails fast.

## 7. Timeouts (transport knobs, `const.py`)

| Knob | Default | Purpose |
|---|---|---|
| `CONNECT_TIMEOUT` | 15 s | bleak connect per attempt (2 attempts) |
| `REQUEST_TIMEOUT` | 8 s | read/overall response deadline |
| `SILENCE_TIMEOUT` | 0.6 s | gap that ends a fragmented response |
| `WRITE_ECHO_TIMEOUT` | 3 s | echo wait (0 disables) |
| `MAX_RX_BUFFER` | 512 B | garbage guard |
| `DEBUG_RAW` | False | per-chunk hex logging (noisy) |