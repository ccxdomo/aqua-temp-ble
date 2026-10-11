# Contract 008-B — reassembly and request matching

Normative. This replaces the FC drop-gate (the production root cause,
research §3) with request-aware matching. It binds `aqua_client.py`'s
notify path, silence timer and deadline branches. Frame formats and
boundary rules: contract 008-A.

## 1. The request-match spec

Each in-flight request carries ONE immutable match spec:

```python
_ResponseMatch(accept_fc03, accept_block, accept_echo, start, count)
```

| request | accept_fc03 | accept_block | accept_echo | start | count |
|---|---|---|---|---|---|
| `read_registers(s, c)` | True | True | False | s | c |
| `query_block(s, c)` | False | True | False | s | c |
| `write_registers(s, [v])` (echo mode) | False | False | True | s | len(v) |
| `query_block` old `expected_fc=None` callers | — | — | — | — | superseded |

## 2. Notification processing (per chunk)

```
1  pending absent/done        -> idle census path (§5), return
2  rx.extend(chunk); req_rx_bytes += len(chunk)
3  rx[0] != 0x63              -> drop non-Modbus (existing line), return
4  len(rx) > MAX_RX_BUFFER    -> overflow failure (existing behavior)
5  expected = boundary(rx)    (contract 008-A §3)
6  expected is None           -> arm silence timer, return
7  while len(rx) >= expected:
8      frame = rx[:expected]; del rx[:expected]
9      CRC invalid            -> census crc=FAIL (DEBUG), keep waiting
10     census crc=OK
11     matches(frame)         -> complete(frame), return
12     (non-matching)         -> arm silence timer, return
```

Rules:
- **Never drop a frame for its function code alone.** The only
  unconditional drop is step 3 (non-Modbus head byte).
- A non-matching CRC-valid frame NEVER fails, never completes the
  request — it is logged (census) and forgotten. The request keeps
  waiting for its own frame until the silence/deadline paths decide.
- Remainder bytes stay in the buffer and are re-processed by the loop
  (two frames in one notification — none observed [research §5 Q4],
  but free to support).

## 3. Match predicates

- FC03 frame: `accept_fc03 and frame[2] == 2*count`
  (an FC03 response carries no start echo — the request supplies it;
  unchanged from c8d8260 semantics).
- Block frame: `accept_block and unpack(">HH", frame[2:6]) == (start, count)`
  and `frame[6] == 2*count` and `len(frame) == frame[6]+9`.
- Echo: `accept_echo and len(frame) == 8 and
  unpack(">HH", frame[2:6]) == (start, count)`.
- Exception frame (`fc|0x80`, 5 bytes): matches nothing; completes by
  FAILING the request with `ModbusExceptionError` (unchanged).

## 4. Completion paths (all three apply the match)

### 4.1 Boundary completion (rule 1)
Steps 7-12 above. CRC-valid + matching → `pending.set_result(frame)`.

### 4.2 Silence timer (rule 2 — 0.6 s, unchanged value)
Fires → if the buffer holds a COMPLETE CRC-valid MATCHING frame →
complete. Otherwise: DEBUG line, keep waiting (chunks may still arrive).
A truncated or non-matching buffer must NEVER complete here.

### 4.3 Deadline (rule 3 — REQUEST_TIMEOUT, unchanged value)
`asyncio.timeout` fires → if the buffer holds a complete CRC-valid
MATCHING frame → complete. Else raise `AquaTempTimeoutError` with the
counters (§6). **This is the self-heal path for the pathological echo**
whose CRC low byte equals 2*qty (boundary rule misreads it as a block
frame and waits): at silence/deadline the 8-byte echo is CRC-valid and
matches the echo spec → completes. Test vector pins this.

## 5. Census (observation)

| situation | level |
|---|---|
| request pending, CRC-valid non-matching frame | INFO — `block push observed: start=%d qty=%d len=%d crc=OK (waiting for start %d)` |
| request pending, CRC-invalid frame | DEBUG — same, `crc=FAIL` |
| idle (settle/no request), first frame per connect | INFO — `block push observed while idle: …` |
| idle, subsequent frames | DEBUG |

Counters (reset per request): `req_rx_bytes` (total received, step 2),
`req_frames` (list of (start, qty) seen, CRC-valid only).

## 6. Timeout message (normative)

```
no matching response within {timeout:.1f}s (received {n_frames} frame(s) /
{req_rx_bytes} bytes; blocks seen: {sorted set of starts}; none for start {start})
```

- With pushes: `… (received 12 frame(s) / 1188 bytes; blocks seen: 1001,
  1046, 1091, 1136, 2001, 2046; none for start 7)`.
- With nothing: `… (received 0 frame(s) / 0 bytes; blocks seen: none;
  none for start 7)`.
- The literal string `got 0 bytes` must not reappear: it reported bytes
  *remaining after deletion*, not bytes *received* (research §5 Q3).

## 7. Invariants

1. One request in flight (single-flight, unchanged); its match spec is
   the ONLY authority on what completes it.
2. No frame the unit sends can fail a request except an exception frame.
3. Nothing is written to the device as a side effect of reassembly
   (census is pure observation).
4. `disconnect()`/teardown clears rx, counters, census-once flag and
   cancels the timer (unchanged cleanup, extended to the new fields).
5. Behavior outside the block-frame concern is byte-identical to
   c8d8260: same requests, same timeouts, same error types, same
   write-echo contract.