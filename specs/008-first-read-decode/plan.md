# Spec 008 plan — implementation design for the Coder

Everything here follows `research.md` (proofs) and `contracts/` (normative
rules). Read spec.md §2 for the FR each block implements. The order below
is the implementation order; every block ends green (tests pinned first,
red-first where it reproduces the production bug — the c8d8260 pattern).

## 1. `modbus.py` — protocol knowledge (FR-1)

### 1.1 New constant + parser

```python
FC_BLOCK = 0x10  # the unit's block-frame responses reuse the query-block
#                 request byte (research 008 §2.1). NOT a write echo.
```

```python
def parse_block_response(frame, start, count, little_endian=False):
    """Validate + decode the unit's block frame to {register: signed int16}.

    Shape [P, CRC-verified on 4 live frames — research 008 §2.1]:
      63 10 start:2 qty:2 bytecount data:bytecount crc16:2 (LE)
    Length = bytecount + 9 (99 for qty=45).
    Raises ModbusCRCError (bad CRC), ModbusExceptionError (fc|0x80),
    ModbusError (shape/start/qty/bytecount mismatch).
    """
```

Validation order (mirrors `parse_read_response`'s contract):
1. `len(frame) >= 9 and validate_crc(frame)` else `ModbusCRCError`;
2. `frame[1] == FC_BLOCK | 0x80` → `ModbusExceptionError(frame[2])`;
3. `frame[1] != FC_BLOCK` or `unpack(">HH", frame[2:6]) != (start, count)`
   or `frame[6] != 2*count` or `len(frame) != frame[6] + 9` →
   `ModbusError(f"malformed block response: {frame.hex()}")`;
4. decode `count` int16 registers from `frame[7:7+frame[6]]` with the
   shared helper (`little_endian` = `registers.REG_DATA_LITTLE_ENDIAN`,
   the same calibration switch as the base decode).

Refactor note: extract the register-decode loop of
`parse_read_response` into `_decode_registers(body, start, count,
little_endian)` and call it from both parsers — behavior byte-identical
for FC03 (the existing self-test round-trip pins it).

### 1.2 New length rule

```python
def block_response_length(buf) -> int | None:
    """Length of a 63 10 start:2 qty:2 bytecount data crc frame, or None.

    None below 7 bytes (bytecount not visible yet) and when
    buf[6] != 2*qty(buf[4:6]) — the discriminator against the 8-byte
    write echo, whose offset-6 byte is a CRC byte (research 008 §5 Q6).
    """
```

`expected_response_length` stays **exactly as is** (its self-test vectors
are pinned; NFR-6). The reassembler's boundary function (§2.3) consults
the block rule first for fc-0x10 buffers.

### 1.3 Self-test additions (run via `python3 modbus.py`)

- CRC + decode vectors for the four proven frames (embed the full hex —
  they are the production vectors, research §2): block 1001 (CRC 0x3A56),
  1046 (0x7685), 2046 first push (0x9BF4), 2046 second push (0x65A5);
  assert the non-zero registers (e.g. reg 1021 == 1050, reg 2046 == 32765
  sentinel, reg 2047 == 140/145) and the zero registers.
- `block_response_length`: 99 from every prefix length ≥ 7 of a block
  frame; `None` below 7; `None` for the two write echoes (offset-6 byte
  0x88/0x68 ≠ 2); `99` for the pathological synthetic echo whose CRC low
  byte == 2 (documents the ambiguity, contract §4.3).
- `parse_block_response` error paths: corrupt CRC, truncated, wrong
  start, wrong qty, exception fc.

## 2. `aqua_client.py` — matching reassembler (FR-2, FR-3, FR-4)

### 2.1 State

`self._expected_fc` is replaced by a match spec (keep the attribute name
`_expected_fc` out; new private fields):

```python
self._match: _ResponseMatch | None = None   # what may complete the request
self._req_rx_bytes = 0        # TOTAL bytes received during the request (never reset on discard)
self._req_frames: list[tuple[int, int]] = []  # (start, qty) of complete frames seen
self._idle_census_logged = False  # one INFO census line per connect while idle
```

`_ResponseMatch` (module-level dataclass, frozen):

```python
@dataclass(frozen=True)
class _ResponseMatch:
    accept_fc03: bool = False   # 63 03 bytecount data crc
    accept_block: bool = False  # 63 10 start qty bytecount data crc
    accept_echo: bool = False   # 63 10 start qty crc (8 bytes)
    start: int | None = None    # the register start a frame must echo
    count: int | None = None    # the register count a frame must echo
```

### 2.2 Call sites

- `read_registers(start, count)`:
  `_request(frame, _ResponseMatch(accept_fc03=True, accept_block=True,
  start=start, count=count), const.REQUEST_TIMEOUT)`.
  After the frame returns: dispatch on `frame[1]` — 0x03 →
  `parse_read_response`, 0x10 → `parse_block_response` (same
  `little_endian` switch); wrap `ModbusError` in `AquaTempResponseError`
  as today.
- `query_block(start, count)`:
  `_request(frame, _ResponseMatch(accept_block=True, start=start,
  count=count), …)` — unchanged signature/return (raw bytes).
- `write_registers` echo mode:
  `_request(frame, _ResponseMatch(accept_echo=True, start=start,
  count=len(values)), const.WRITE_ECHO_TIMEOUT)` — rest unchanged.

### 2.3 Boundary detection

```python
def _frame_boundary(buf: bytes) -> int | None:
    """Total length of the frame at the head of buf, or None if unknown.

    Order (contract reassembly-matching.md §2):
    1. buf[0] != SLAVE_ID -> None (caller already dropped non-Modbus)
    2. fc & 0x80 -> 5 (exception)
    3. fc == FC_READ_HOLDING (0x03) -> bytecount+5 (needs len >= 3)
    4. fc == FC_BLOCK (0x10):
         len < 7 -> None (bytecount byte not visible — WAIT, never guess)
         block_response_length(buf) -> bytecount+9   (block shape wins)
         else -> 8                                    (write echo)
    5. anything else -> None
    """
```

This replaces the direct call to `expected_response_length` inside
`_on_notify` (which keeps its old behavior and vectors for API
compatibility — NFR-6).

### 2.4 `_on_notify` — the new flow (replaces the FC gate)

```
data = args[-1] (unchanged)
if pending is None or done:
    census-idle path (§2.6); return
rx.extend(data); req_rx_bytes += len(data)
if rx[0] != SLAVE_ID: drop non-Modbus (unchanged line); return
if len(rx) > MAX_RX_BUFFER: unchanged overflow behavior
expected = _frame_boundary(rx)
if expected is None: arm silence; return
while expected is not None and len(rx) >= expected:        # frame(s) ready
    frame = bytes(rx[:expected])
    del rx[:expected]
    if not validate_crc(frame):
        census(frame, crc="FAIL")   # DEBUG; keep waiting
        continue
    census(frame, crc="OK")
    if _matches(frame): complete(frame); return
    # non-matching CRC-valid frame: logged, discarded, request keeps waiting
    arm silence; return
arm silence
```

`_matches(frame)`: fc-0x03 → `accept_fc03 and frame[2] == 2*count`;
fc-0x10 with `block_response_length(frame) == len(frame)` →
`accept_block and unpack(">HH", frame[2:6]) == (start, count)`;
8-byte fc-0x10 → `accept_echo and unpack(">HH", frame[2:6]) ==
(start, count)`. Exception frames (fc|0x80) fail the request immediately
(unchanged semantics via `_complete`).

The remainder loop (`while`) is [A] robustness for a burst carrying two
frames in one notification (none observed — research §5 Q4 — but the
boundary math makes it free).

### 2.5 Silence timer + deadline (rule 2/3 fix)

- `_on_silence`: complete ONLY if the buffer holds a CRC-valid frame
  that **matches** (`_frame_boundary(rx) == len(rx) and
  validate_crc and _matches`) — else keep waiting (DEBUG line, as today).
  This is what self-heals the pathological echo (contract §4.3).
- `_request`'s `except TimeoutError`: the deadline-accept branch applies
  the same match check before accepting; on no match raise
  `AquaTempTimeoutError` with the counters:

```python
f"no matching response within {timeout:.1f}s "
f"(received {self._req_frames and len(self._req_frames) or 0} frame(s) / "
f"{self._req_rx_bytes} bytes; blocks seen: {starts}; none for start {start})"
```

  (When zero frames were received the message says
  `received 0 frames / 0 bytes` — still honest, never "got 0 bytes" after
  deleting 12 frames.)

### 2.6 Census logging (FR-4)

```
INFO  (pending):  block push observed: start=%d qty=%d len=%d crc=OK (waiting for start %d)
DEBUG (pending):  block push observed: start=%d qty=%d len=%d crc=FAIL
INFO  (idle, once per connect): block push observed while idle: start=%d qty=%d len=%d crc=OK
DEBUG (idle):     same as above
```

Census lines carry the observed (start, qty) — that is the E1/E2 data
(research §7). Keep the lines greppable (`block push observed`).

### 2.7 What does NOT change

`connect()`/GATT resolution (specs 005-007), `_on_disconnect`,
single-flight enforcement, `disconnect()` cleanup (also clears the new
counters), `write_registers` fire-and-forget branch, `DEBUG_RAW`
chunk logging, stage instrumentation.

## 3. `coordinator.py` — probe decode (FR-5)

`_log_raw_probe`: after `query_block`, try `parse_block_response(raw,
start, registers.BASE_BLOCK_COUNT, little_endian=…)`; on success log the
per-register probe lines exactly like `_log_base_probe` (shared format,
labels from `KNOWN_LABELS` when present) and include `start`/`qty` in the
returned dict; on `ModbusError` fall back to the existing raw-hex branch
(WARNING + `"raw"` key). No new blocks, no entity changes.

## 4. Tests (red-first where noted) — see tasks.md for the ordered list

New file `tests/test_block_frames.py` (protocol vectors + parser errors)
and `tests/test_reassembly_matching.py` (fake-notify client scenarios —
AC-2/AC-4/AC-5/AC-6/AC-7). Extend `tests/test_coordinator_probe.py` (or
the existing probe test file) for AC-8. The red-first case is AC-2:
against c8d8260 it reproduces the production failure verbatim
(`AquaTempTimeoutError … got 0 bytes` with the block frame dropped),
then goes green with the fix.

## 5. Sequence for the Reviewer

1. `git diff` touches only NFR-1 files; grep the diff for
   `REQUEST_TIMEOUT|SILENCE_TIMEOUT|SETTLE_AFTER_SUBSCRIBE|
   WRITE_ECHO_TIMEOUT|MAX_RX_BUFFER|BASE_BLOCK` — must show value changes
   nowhere.
2. `python3 custom_components/aqua_temp_ble/modbus.py` — self-test PASS
   with the NEW vectors (proven frames) alongside the old ones.
3. `pytest tests/` green.
4. Read AC-2's test: the production case (pushed start-7 block) completes;
   AC-4's test: a block-1001 push during a base read cannot complete it.
5. quickstart.md present (AC-10).