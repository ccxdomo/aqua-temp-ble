#!/usr/bin/env python3
"""Spec 008: reconstruct frames 3/4/5 via the CRC oracle (linear over GF(2))."""
import struct
from itertools import combinations

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def build(start: int, regs: list[int]) -> bytes:
    body = b"".join(struct.pack(">h", v) for v in regs)
    frame = bytes([0x63, 0x10]) + struct.pack(">HH", start, 45) + bytes([len(body)]) + body
    return frame + struct.pack("<H", crc16(frame))

print("== Frames 4 and 5: islands + 39 zero registers ==")
for label, islands, tail in [
    ("Frame 4", [0x7FFD, 0x008C, 0x008C, 0x008C, 0x0087, 0x0096], "f49b"),
    ("Frame 5", [0x7FFD, 0x0091, 0x008C, 0x008C, 0x0087, 0x0096], "a565"),
]:
    f = build(2046, islands + [0]*39)
    ok = f[-2:] == bytes.fromhex(tail)
    print(f"{label}: built tail={f[-2:].hex()} expected={tail} -> {'CRC MATCH - RECONSTRUCTION PROVEN' if ok else 'NO MATCH'}")
    print(f"  full: {f.hex()}")

print()
print("== Frame 3: island-position search via CRC linearity ==")
# CRC of a fixed-length message is linear over GF(2): frame = base XOR islands.
# delta[p][i] = CRC(frame with ONLY island i at register p) XOR CRC(base frame).
ISLANDS = [0x07D1, 0x0001, 0x000D, 0x00A8, 0x0040]   # left-to-right order in the log line
TARGET = 0x0092                                       # wire "92 00" = LE -> 0x0092
START = 2001
base = build(START, [0]*45)
base_crc = crc16(base[:-2])
delta = [[0]*len(ISLANDS) for _ in range(45)]
for p in range(45):
    for i, v in enumerate(ISLANDS):
        regs = [0]*45; regs[p] = v
        delta[p][i] = crc16(build(START, regs)[:-2]) ^ base_crc
hits = []
for pos in combinations(range(45), 5):
    p1, p2, p3, p4, p5 = pos
    d = delta[p1][0] ^ delta[p2][1] ^ delta[p3][2] ^ delta[p4][3] ^ delta[p5][4]
    if base_crc ^ d == TARGET:
        hits.append(pos)
print(f"candidates matching CRC 0x{TARGET:04X}: {len(hits)}")
for pos in hits:
    regs = [0]*45
    names = ["07d1", "0001", "000d", "00a8", "0040"]
    for i, p in enumerate(pos):
        regs[p] = ISLANDS[i]
    f = build(START, regs)
    print(f"  register positions (offset from 2001): {pos}")
    for i, p in enumerate(pos):
        print(f"    reg {2001+p} = 0x{ISLANDS[i]:04X} ({names[i]})")
    print(f"  rebuilt frame ({len(f)} B): {f.hex()}")
    print(f"  CRC check: calc=0x{crc16(f[:-2]):04X} stored=0x{struct.unpack('<H', f[-2:])[0]:04X}")
