#!/usr/bin/env python3
"""Standalone smoke test for AquaTempBleClient (live device, read-only).

The push-world smoke test (spec 021, tasks.md A4): connect, send ONE
vendor base read as the TRIGGER — this unit never answers the classic
FC03 read with base data (law L1, spec 020), but it ANSWERS every
request with a burst of four pushed register blocks (1001/1046/2001/
2046, 45 registers each) on the notify characteristic — then dump the
captured store with the known labels. The trigger's 8 s timeout is the
expected world: the answer arrives as the block burst during the
window. Read-only: never writes.

Requirements: `pip install bleak` (nothing else, no Home Assistant).

Usage (run next to the heat pump, vendor app CLOSED):
    python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"))

import aqua_client  # noqa: E402
import const  # noqa: E402
import registers  # noqa: E402


async def main(argv: list[str]) -> int:
    # Standalone visibility (spec 007 FR-1 corollary, plan 007 §3):
    # make the connect stage-timing lines — attempt window, connect
    # summary, failure stage — visible on stderr without Home Assistant
    # (INFO records are otherwise dropped outside HA).
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Aqua Temp BLE smoke test (read-only, push world)"
    )
    parser.add_argument("address", help="BLE address of the unit (XX:XX:XX:XX:XX:XX)")
    parser.add_argument("--name", default=None, help="expected device name (optional)")
    args = parser.parse_args(argv)

    const.DEBUG_RAW = True  # chunk logging helps to understand fragmentation
    client = aqua_client.AquaTempBleClient(args.address, name=args.name)
    await client.connect()
    try:
        # 1. TRIGGER — ONE vendor base read. On this unit the call times
        #    out after 8 s (the expected world, law L1 / L2 spec 020):
        #    the answer IS the block burst that lands on the notify pipe
        #    while the request waits. A COMPLETED read means the unit
        #    answered the classic read (a different firmware world).
        print(f"AquaTemp smoke: {args.address} — sending the trigger read")
        try:
            regs = await client.read_registers(
                registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
            )
        except aqua_client.AquaTempTimeoutError:
            print("trigger read timed out as expected — the answer is the block burst")
            regs = None
        if regs is not None:
            print(
                "trigger read ANSWERED with base data — the classic world "
                "(never observed on the production unit); dumping it:"
            )
            length = 2 * registers.BASE_BLOCK_COUNT + 5
            print(f"AquaTemp smoke: (FC03 start=7 count=45) len={length} CRC=OK")
            for addr in range(registers.BASE_BLOCK_START,
                              registers.BASE_BLOCK_START + registers.BASE_BLOCK_COUNT):
                raw = regs.get(addr, 0)
                line = f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) | /10 = {raw / 10:7.1f}"
                label = registers.KNOWN_LABELS.get(addr)
                if label:
                    line += f"  # {label}"
                print(line)

        # 2. LISTEN — the bounded harvest (instant when the burst already
        #    landed during the trigger window; 30 s window otherwise).
        caps = await client.harvest_pushes(
            const.PUSH_HARVEST_STARTS, const.PUSH_HARVEST_TIMEOUT
        )

        # 3. DUMP — every captured block, per register, with labels.
        if not caps:
            print("AquaTemp smoke: NO block captured — unit unreachable? "
                  "(vendor app closed? in range?)")
            return 1
        for start in sorted(caps):
            cap = caps[start]
            print(
                f"\nAquaTemp smoke: block={start} (pushed capture, start={cap.start} "
                f"qty={cap.qty} len={cap.length} count={cap.count}) CRC=OK"
            )
            for addr in sorted(cap.registers):
                raw = cap.registers[addr]
                line = (
                    f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) "
                    f"| /10 = {raw / 10:7.1f}"
                )
                label = registers.KNOWN_LABELS.get(addr)
                if label:
                    line += f"  # {label}"
                print(line)
        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(sys.argv[1:])))
    except KeyboardInterrupt:
        print("\ninterrupted.")