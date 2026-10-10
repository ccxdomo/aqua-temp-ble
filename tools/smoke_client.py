#!/usr/bin/env python3
"""Standalone smoke test for AquaTempBleClient (live device, read-only).

Connects to the unit, reads the 45 base registers and prints them in the same
format as the Home Assistant probe logs. Read-only: never writes.

Requirements: `pip install bleak` (nothing else, no Home Assistant).

Usage (run next to the heat pump, vendor app CLOSED):
    python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"))

import aqua_client  # noqa: E402
import const  # noqa: E402
import registers  # noqa: E402


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Aqua Temp BLE smoke test (read-only)")
    parser.add_argument("address", help="BLE address of the unit (XX:XX:XX:XX:XX:XX)")
    parser.add_argument("--name", default=None, help="expected device name (optional)")
    parser.add_argument("--query", metavar="START", type=int, default=None,
                        help="also probe a high block starting at this register (e.g. 1001)")
    args = parser.parse_args(argv)

    const.DEBUG_RAW = True  # chunk logging helps to understand fragmentation
    client = aqua_client.AquaTempBleClient(args.address, name=args.name)
    await client.connect()
    try:
        regs = await client.read_registers(
            registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
        )
        length = 2 * registers.BASE_BLOCK_COUNT + 5
        print(f"AquaTemp smoke: {args.address} (FC03 start=7 count=45) len={length} CRC=OK")
        for addr in range(registers.BASE_BLOCK_START,
                          registers.BASE_BLOCK_START + registers.BASE_BLOCK_COUNT):
            raw = regs.get(addr, 0)
            line = f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) | /10 = {raw / 10:7.1f}"
            label = registers.KNOWN_LABELS.get(addr)
            if label:
                line += f"  # {label}"
            print(line)

        if args.query is not None:
            raw = await client.query_block(args.query, registers.BASE_BLOCK_COUNT)
            print(f"\nAquaTemp smoke: query block start={args.query} len={len(raw)} "
                  f"CRC={'OK' if len(raw) >= 4 else 'NONE'}")
            print(f"raw: {raw.hex()}")
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(sys.argv[1:])))
    except KeyboardInterrupt:
        print("\ninterrupted.")