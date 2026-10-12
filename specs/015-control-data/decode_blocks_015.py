#!/usr/bin/env python3
"""Spec 015 decode tool — the four complete 0.10.0 pushed-block captures, register by register.

Decodes the four complete 99-byte block frames (the owner-pasted 0.10.0 log
hex, relayed in the spec-015 brief; CRC-verified in frames.txt) with the
project's proven frame grammar (research 008 §2.1, pinned as modbus.py
self-test §10 vectors):

    63 10 <start:2 BE> <qty:2 BE> <bytecount> <data: bytecount> <crc16 LE>

- verifies the CRC16-Modbus of each frame (poly 0xA001, init 0xFFFF,
  stored little-endian),
- decodes the 45 big-endian signed int16 registers per frame,
- prints full per-register tables (raw, hex, /10, plausibility tags),
- runs the structural analyses the brief demands: offset-mirror tests,
  control-candidate scans (power 0/1, mode enums, setpoint 200-400,
  sentinels 0x7FFD / -9990).

Pure stdlib. Run from anywhere:
    python3 specs/015-control-data/decode_blocks_015.py

The four hex lines live in frames.txt (one frame per line, 198 chars each;
line 4 was reconstructed byte-exactly from the observed window + the
CRC oracle — see research.md §1.4).
"""

from __future__ import annotations

import struct
from pathlib import Path

HERE = Path(__file__).parent
FRAMES_FILE = HERE / "frames.txt"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def decode_frame(name: str, hexstr: str) -> dict[int, int] | None:
    raw = bytes.fromhex(hexstr.strip())
    print(f"\n=== frame {name} ===")
    print(f"  length: {len(raw)} bytes (expected 99)")
    if len(raw) != 99:
        print("  !! NOT 99 bytes — refusing to decode")
        return None
    slave, fc = raw[0], raw[1]
    start, qty = struct.unpack(">HH", raw[2:6])
    bytecount = raw[6]
    data = raw[7:97]
    crc_stored = struct.unpack("<H", raw[97:99])[0]
    crc_calc = crc16_modbus(raw[:97])
    ok = crc_stored == crc_calc
    print(f"  slave=0x{slave:02X} fc=0x{fc:02X} start={start} qty={qty} "
          f"bytecount={bytecount} (0x{bytecount:02X})")
    print(f"  CRC stored=0x{crc_stored:04X} calc=0x{crc_calc:04X} -> "
          f"{'VALID' if ok else 'INVALID'}")
    if not ok:
        return None
    regs: dict[int, int] = {}
    for i in range(qty):
        (val,) = struct.unpack(">h", data[2 * i: 2 * i + 2])
        regs[start + i] = val
    return regs


def tag(val: int) -> str:
    """A quick plausibility annotation for eyeballing."""
    tags = []
    if val == 32765:
        tags.append("SENTINEL 0x7FFD (sensor absent)")
    if val == -9990:
        tags.append("SENTINEL -9990 (sensor absent)")
    if val in (0, 1):
        tags.append("boolean 0/1")
    if 2 <= val <= 5:
        tags.append("small enum?")
    if 200 <= val <= 400:
        tags.append("setpoint-shaped x10 (20.0-40.0 C)")
    if -450 <= val <= 1500 and val != 0 and val % 5 == 0:
        tags.append("temp-shaped /10")
    return "; ".join(tags)


def print_table(name: str, regs: dict[int, int]) -> None:
    print(f"\n--- {name}: all 45 registers ---")
    print(f"{'reg':>5} {'raw':>7} {'hex':>7} {'/10':>8}  tags")
    nonzero = 0
    for addr in sorted(regs):
        v = regs[addr]
        if v != 0:
            nonzero += 1
        print(f"{addr:>5} {v:>7} 0x{v & 0xFFFF:04X} {v / 10:>8.1f}  {tag(v)}")
    print(f"-> {nonzero} non-zero of {len(regs)}")


def main() -> None:
    lines = FRAMES_FILE.read_text().split()
    names = ["1001", "1046", "2001", "2046"]
    all_regs: dict[int, dict[int, int]] = {}
    for name, hexstr in zip(names, lines):
        regs = decode_frame(name, hexstr)
        if regs is not None:
            all_regs[int(name)] = regs
            print_table(name, regs)

    # --- structural analyses (the brief's questions 3/4/5) ---------------
    print("\n=== offset-mirror test: does 1001+n mirror base 7+n (offset 994)? ===")
    b1001 = all_regs.get(1001, {})
    base_map = {
        8: "Power", 11: "EnBoostMode", 13: "Mode", 14: "SetCurrentTemp",
        19: "SetCoolTargetTemp", 20: "SetHeatTargetTemp", 21: "SetAutoTargetTemp",
        22: "SetCoolMinTargetTemp", 23: "SetCoolMaxTargetTemp",
        24: "SetHeatMinTargetTemp", 25: "SetHeatMaxTargetTemp",
        26: "InletWaterTemp", 27: "OutletWaterTemp", 28: "AT",
        47: "Fault1", 48: "FW1", 49: "FW2", 50: "FW3", 51: "FW4",
    }
    offset = 1001 - 7  # 994
    for base_addr, label in base_map.items():
        hi = base_addr + offset
        print(f"  base {base_addr:>2} ({label:<18}) -> 1001-plane {hi:>4} "
              f"= {b1001.get(hi, 'N/A')}")

    print("\n=== setpoint scan: any raw in 200..400 anywhere? ===")
    for block, regs in sorted(all_regs.items()):
        hits = {a: v for a, v in regs.items() if 200 <= v <= 400}
        print(f"  block {block}: {hits if hits else 'none'}")

    print("\n=== power/mode scan: 0/1 flags and 2..5 enums, all blocks ===")
    for block, regs in sorted(all_regs.items()):
        ones = {a: v for a, v in regs.items() if v == 1}
        enums = {a: v for a, v in regs.items() if 2 <= v <= 5}
        print(f"  block {block}: raw==1 at {ones if ones else 'none'} | "
              f"2..5 at {enums if enums else 'none'}")

    print("\n=== T-window, 2046-2051, this capture ===")
    t = all_regs.get(2046, {})
    print("  ", {a: t.get(a) for a in range(2046, 2052)})

    # --- the 2001 block, the candidate control home ----------------------
    print("\n=== block 2001: non-zero registers only ===")
    b2001 = all_regs.get(2001, {})
    for a in sorted(b2001):
        v = b2001[a]
        if v != 0:
            print(f"  reg {a} = {v} (0x{v & 0xFFFF:04X}) /10 = {v / 10:.1f}  {tag(v)}")

    # --- arithmetic self-checks the research section cites ----------------
    print("\n=== arithmetic self-checks ===")
    print(f"  2001 - 1001 = {2001 - 1001}, 2046 - 1046 = {2046 - 1046}, "
          f"1046 - 1001 = {1046 - 1001}, 1001 - 7 = {1001 - 7}")
    t01 = t.get(2046, 0)
    print(f"  T-window deltas vs base 26/27/28: 2047-26={2047-26}, "
          f"2048-27={2048-27}, 2050-28={2050-28}")
    print(f"  reg 2047 raw = {t.get(2047)} -> {t.get(2047, 0)/10:.1f} C "
          f"(calibrated T02 Inlet water)")


if __name__ == "__main__":
    main()