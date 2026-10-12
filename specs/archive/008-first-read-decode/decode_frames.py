#!/usr/bin/env python3
"""Spec 008 research tool: byte-level decode of the real production frames.

Reproduces the decode of the five "dropping unexpected function code 0x10"
frames from the production log (2026-10-11), verifies every CRC by
calculation, and simulates what the CURRENT code (c8d8260) does with each
frame — the root-cause demonstration for research.md §4.

Provenance of the frame bytes (research.md §2):
- Frames 1 and 2 are byte-exact transcriptions of the log lines: each is
  198 hex chars = 99 bytes and its CRC16-Modbus validates, which proves the
  transcription (a single wrong nibble breaks the CRC with p = 65535/65536).
- Frames 4 and 5 are CRC-oracle reconstructions: header + the visible data
  islands + the logged CRC tail uniquely determine the frame (bytecount 0x5A
  fixes the data length at exactly 90 bytes; the reconstructed CRCs match
  the logged tails f49b / a565 bit-for-bit). See reconstruct.py.
- Frame 3: the header (block 2001) is proven; the exact data is [V] — the
  pasted line's zero-runs cannot be transcribed byte-reliably and the CRC
  alone leaves 40 candidate reconstructions (reconstruct.py). Not decoded
  here; the fixed build's census logging will re-capture it.

Run:  python3 specs/008-first-read-decode/decode_frames.py
"""
from __future__ import annotations

import struct

# --- the frames, as logged by the production run (WARNING lines) -----------
FRAME_1_BLOCK_1001 = (
    "631003e9002d5a"                       # slave, fc, start 0x03E9, qty 45, bytecount 0x5A
    "0000000000000000000000000000000000"   # regs 1001-1007 = 0
    "000000000000"                         # regs 1008-1010 = 0
    "0001"                                 # reg 1012? -> see decode; exact layout below
    "00000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)
# NOTE: FRAME_1 above is assembled for readability only; the authoritative
# byte-exact string is the single 198-char log line below (CRC-validated).
FRAMES: dict[str, str] = {
    "frame 1 (block 1001-1045)": (
        "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
    ),
    "frame 2 (block 1046-1090)": (
        "63100416002d5a01220005000500960190010e000500050064015effba00820028000800030000000000000000000000000000010e00000000000000000000000000000000000000000000000000000000000000000000000000000000000000008576"
    ),
    "frame 4 (block 2046-2090, first push)": (
        "631007fe002d5a7ffd008c008c008c00870096" + "00" * 78 + "f49b"
    ),
    "frame 5 (block 2046-2090, second push)": (
        "631007fe002d5a7ffd0091008c008c00870096" + "00" * 78 + "a565"
    ),
}
FRAME_3_HEADER = "631007d1002d5a"  # proven prefix of the third log line (block 2001)

SENTINELS = {-9990, 32765}


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus: poly 0xA001, init 0xFFFF (FINDINGS.md §11)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


# --- current production code, verbatim logic (modbus.py / aqua_client.py) ---
SLAVE_ID = 0x63


def expected_response_length(buf: bytes) -> int | None:
    """modbus.py @ c8d8260, verbatim."""
    if len(buf) < 2 or buf[0] != SLAVE_ID:
        return None
    fc = buf[1]
    if fc & 0x80:
        return 5
    if fc == 0x03:
        return buf[2] + 5 if len(buf) >= 3 else None
    if fc == 0x10:
        return 8
    return None


def validate_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16_modbus(frame[:-2]) == struct.unpack(
        "<H", frame[-2:]
    )[0]


def decode_block_frame(frame: bytes) -> dict:
    """The PROVEN block-frame grammar (research.md §3):

        63 10 [start:2 BE] [qty:2 BE] [bytecount] [data: bytecount BE int16] [CRC16 LE]

    Total length = 7 + bytecount + 2 = bytecount + 9 = 99 for qty=45.
    """
    start, qty = struct.unpack(">HH", frame[2:6])
    bc = frame[6]
    regs = list(struct.unpack(f">{bc // 2}h", frame[7 : 7 + bc]))
    return {
        "slave": frame[0],
        "fc": frame[1],
        "start": start,
        "qty": qty,
        "bytecount": bc,
        "declared_len": 7 + bc + 2,
        "actual_len": len(frame),
        "regs": regs,
        "crc_calc": crc16_modbus(frame[:-2]),
        "crc_stored": struct.unpack("<H", frame[-2:])[0],
    }


def main() -> None:
    print("=" * 78)
    print("SPEC 008 — decode of the real production frames (log 2026-10-11)")
    print("=" * 78)

    for name, hx in FRAMES.items():
        f = bytes.fromhex(hx)
        d = decode_block_frame(f)
        print(f"\n--- {name} ---")
        print(f"  wire: {f[:7].hex(' ')}  (slave fc start.hi start.lo qty.hi qty.lo bytecount)")
        print(
            f"  slave=0x{d['slave']:02X}  fc=0x{d['fc']:02X}  start={d['start']}"
            f" (0x{d['start']:04X})  qty={d['qty']}  bytecount=0x{d['bytecount']:02X}"
            f"={d['bytecount']}"
        )
        print(
            f"  declared len {d['declared_len']} vs actual {d['actual_len']} -> "
            f"{'MATCH' if d['declared_len'] == d['actual_len'] else 'MISMATCH'};"
            f" bytecount == 2*qty -> {d['bytecount'] == 2 * d['qty']}"
        )
        crc_ok = d["crc_calc"] == d["crc_stored"]
        print(
            f"  CRC: calc=0x{d['crc_calc']:04X} stored=0x{d['crc_stored']:04X} (wire "
            f"{f[-2]:02X} {f[-1]:02X}, little-endian) -> "
            f"{'VALID [P]' if crc_ok else 'INVALID'}"
        )
        print(f"  {d['qty']} registers, {d['start']}..{d['start'] + d['qty'] - 1}:")
        for i, v in enumerate(d["regs"]):
            if v == 0:
                continue  # only non-zero registers printed
            addr = d["start"] + i
            note = "  <- SENTINEL 32765 (sensor absent)" if v in SENTINELS else ""
            print(f"    reg {addr} = {v:6d} (0x{v & 0xFFFF:04X}) /10 = {v / 10:7.1f}{note}")

    print(f"\n--- frame 3 (block 2001-2045): header only, data [V] ---")
    h = bytes.fromhex(FRAME_3_HEADER)
    start, qty = struct.unpack(">HH", h[2:6])
    print(
        f"  wire prefix: {FRAME_3_HEADER} -> slave=0x63 fc=0x10 start={start}"
        f" qty={qty} bytecount=0x{h[6]:02X}={h[6]} -> declared len {h[6] + 9}"
    )
    print("  data unverifiable from the pasted line (research.md §2.4): the")
    print("  zero-runs cannot be transcribed byte-reliably and the CRC alone")
    print("  leaves 40 reconstructions (reconstruct.py). Re-capture with the")
    print("  spec-008 census logging.")

    print()
    print("=" * 78)
    print("ROOT CAUSE — what the CURRENT code (c8d8260) does with each frame")
    print("=" * 78)
    print(
        "Production request in flight: read_registers(7, 45) ->\n"
        "  TX 63030007002d3c54 (FC03, start 7, count 45), expected_fc = 0x03\n"
        "  (config_flow._async_test -> client.read_registers(registers.BASE_BLOCK_START,\n"
        "  registers.BASE_BLOCK_COUNT); aqua_client._request sets self._expected_fc = 0x03)"
    )
    for name, hx in FRAMES.items():
        f = bytes.fromhex(hx)
        rx = f  # one notification carries the whole 99-byte frame [P: the
        # logged drop lines contain complete CRC-valid frames]
        fc = rx[1]
        dropped = fc not in (0x03, 0x83)
        print(f"\n  {name}:")
        print(
            f"    _on_notify: rx[0]=0x{rx[0]:02X} == SLAVE_ID -> pass;"
            f" len(rx)={len(rx)} >= 2 and expected_fc=0x03 -> FC gate reads rx[1]"
        )
        print(
            f"    fc = 0x{fc:02X} not in (0x03, 0x83) -> "
            f"WARNING 'dropping unexpected function code 0x{fc:02X}',"
            " self._rx.clear(), return"
            if dropped
            else "    fc accepted"
        )
        if dropped:
            print(
                "    => the 99 data bytes are DISCARDED; the request's byte"
                " counter stays 0; after 12 such drops the 8 s deadline"
                " fires with len(self._rx) == 0"
            )
    print(
        "\n  Counterfactual (gate open, expected_fc=None or 0x10):"
        " expected_response_length(63 10 ...) returns 8 (write-echo shape)"
        " -> _complete(rx[:8]) -> validate_crc on the 8-byte prefix FAILS"
        " -> AquaTempResponseError('bad CRC'). Either way the data is lost."
    )
    print(
        "\n  Production result: AquaTempTimeoutError: no complete response"
        " within 8.0s (got 0 bytes) at stage first-read after 12.4 s"
        " (budget 70 s)  == exactly the observed log."
    )

    print()
    print("=" * 78)
    print("WRITE-ECHO COLLISION (the FC10 ambiguity, research.md §5.3)")
    print("=" * 78)
    for start, req in [(8, "631000080001020001d7ba"), (14, "6310000e0001020145d67f")]:
        reqb = bytes.fromhex(req)
        echo = reqb[:6] + struct.pack("<H", crc16_modbus(reqb[:6]))
        qty = struct.unpack(">H", echo[4:6])[0]
        print(
            f"  write echo ({start}): {echo.hex()} -> offset-6 byte 0x{echo[6]:02X}"
            f" vs 2*qty = {2 * qty} -> block-shape rule fires: {echo[6] == 2 * qty}"
        )


if __name__ == "__main__":
    main()