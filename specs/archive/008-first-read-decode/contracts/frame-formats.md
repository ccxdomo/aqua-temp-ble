# Contract 008-A — frame formats on the wire

Normative. Every shape below is either byte-proven (CRC-verified live
frame, APK-recovered frame, or Modbus standard) or explicitly tagged [V].
Field order is big-endian unless the CRC (little-endian) or a note says
otherwise. `crc16_modbus` = poly 0xA001, init 0xFFFF (FINDINGS §11).

## 1. Requests (TX, written to the write characteristic)

### 1.1 Base read [P apk — `63030007002d3c54`]

```
63        slave
03        function code (read holding registers)
start:2   0007
qty:2     002D (45)
crc:2     LE
```

### 1.2 Query block [P apk — 6 frames, FINDINGS §11.1]

```
63        slave
10        function byte (reused; NOT a write here)
start:2   03E9 | 0416 | 0443 | 0470 | 07D1 | 07FE
qty:2     002D (45)
crc:2     LE
```

### 1.3 Write (H1) [P apk]

```
63 10 start:2 qty:2(=0001) bytecount(=02) value:2 crc:2
```

## 2. Responses (RX, notified on ff02)

### 2.1 FC03 response [P apk shape; [V] observed live on this unit]

```
63 03 bytecount(=2*qty) data:bytecount crc:2LE   — bytecount+5 bytes
```

### 2.2 Block frame — THE shape of the production drops [P, CRC-verified ×4]

```
63        slave
10        function byte (echoes the query-block request byte)
start:2   the block's start register
qty:2     block length (45)
bytecount 2*qty (0x5A = 90)   <- offset 6; DISCRIMINATOR vs the echo
data:2*qty bytes              <- qty × BE signed int16, register start+i
crc:2     LE over bytes 0..bytecount+6
```

Total = bytecount + 9 = **99 bytes** for qty=45.

Verified vectors (production, 2026-10-11 — also embedded in
`decode_frames.py` and the parser tests):

| block | CRC (calc == stored) |
|---|---|
| 1001-1045 | 0x3A56 (wire `56 3a`) |
| 1046-1090 | 0x7685 (wire `85 76`) |
| 2046-2090 push 1 | 0x9BF4 (wire `f4 9b`) |
| 2046-2090 push 2 | 0x65A5 (wire `a5 65`) |

### 2.3 Write echo [P apk; [V] on this unit]

```
63 10 start:2 qty:2 crc:2LE   — 8 bytes; byte at offset 6 = CRC low byte
```

Offset-6 values of the two known echoes: 0x88 (reg 8), 0x68 (reg 14)
[P, calculated] — never equal to 2*qty(=2), so the block discriminator
rejects them. Pathological case: an echo whose CRC low byte happens to
equal 2*qty — see contract 008-B §4.3.

### 2.4 Exception [P modbus standard]

```
63 fc|0x80 code crc:2LE   — 5 bytes
```

## 3. Boundary rules (how to know a frame is complete)

| head of buffer | rule |
|---|---|
| `63 8X …` | 5 (exception) |
| `63 03 [len<3]` | unknown — wait |
| `63 03 bc …` | bytecount + 5 |
| `63 10 [len<7]` | **unknown — wait** (bytecount byte not visible) |
| `63 10 s s q q bc` with `bc == 2*qty` | **bytecount + 9 (block frame)** |
| `63 10 s s q q bc` with `bc != 2*qty` | 8 (write echo) |
| anything else | unknown — silence timer governs |

The block rule takes precedence over the echo rule at len ≥ 7
(research §5 Q6: bytecount == 2*qty is the discriminator). Below 7 bytes
the boundary is UNKNOWN — never guess 8 (that is the second production
bug: cutting a 99-byte block frame at byte 8).

## 4. Fragmentation model

- Observed on the production link: **one notification carries one whole
  99-byte frame** [P — the drop lines log complete CRC-valid buffers, so
  the ATT MTU was negotiated ≥ ~102].
- The reassembler MUST nonetheless reassemble fragmented frames
  (20-byte chunks at MTU 23): accumulate chunks, detect the boundary from
  the accumulated head, complete at the boundary (test vector: 5×20-byte
  chunks of a 99-byte frame).
- A truncated frame (chunks stop) is left to the silence timer / request
  deadline; it never completes a request (CRC cannot validate).