#!/usr/bin/env python3
"""
Aqua Temp (com.phinx.poolheater4) — Modbus-over-BLE client
==========================================================

Reverse engineering result: the app talks to the heat pump using **Modbus RTU
over a BLE GATT characteristic**, slave address 0x63, CRC16-Modbus (little
endian). See FINDINGS.md §11.

    GATT write  -> ff01 (handle 2214)   : commands out
    GATT notify -> ff02 (handle 2216)   : status in

Read frames (standard Modbus):
    63 03 0007 002D [CRC]        -> 45 holding registers starting at reg 7

High blocks (0x10 reused as "query block", 45 regs each):
    1001-1045  1046-1090  1091-1135  1136-1180  2001-2045  2046-2090

Temperatures are transmitted as int(value * 10)  (32.5 °C -> 325).

Requirements
------------
    pip install bleak

Usage
-----
    python modbus_ble.py --list                 # find the unit
    python modbus_ble.py                        # connect + read base status
    python modbus_ble.py --name BlueNRG
    python modbus_ble.py --addr XX:XX:XX:XX:XX:XX --read 1001 --count 45
    python modbus_ble.py --raw 63030007002D3C54 # send an exact frame

Interactive shell (after connect):
    r <reg> [count]     read holding registers (FC03)
    q <reg> [count]     query a high block   (FC10 as query)
    w <reg> <val> [...] write registers      (FC16)
    x <hex>             send raw hex frame
    dump                decode the last status response as temperatures
    qq                  quit
"""

from __future__ import annotations

import argparse
import asyncio
import binascii
import struct
import sys
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("ERROR: bleak is missing.  pip install bleak", file=sys.stderr)
    sys.exit(1)

# --- transport (see FINDINGS.md §3 / §11.3) --------------------------------
UUID_WRITE  = "0000ff01-0000-1000-8000-00805f9b34fb"   # -> 2214
UUID_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"   # <- 2216

SLAVE = 0x63
NAME_FILTERS = ["blueng", "hiflying", "aqua", "phinx", "pool"]

# --- register map (partial, see FINDINGS.md §11.4) -------------------------
# Labels come from the pool (VR.Yzd). Only the ones confirmed are listed.
# Base read starts at register 7 for 45 registers.
TEMP_DIVISOR = 10.0

TRANSCRIPT: list[str] = []


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg: str) -> None:
    line = f"[{ts()}] {msg}"
    print(line)
    TRANSCRIPT.append(line)


# ---------------------------------------------------------------------------
# CRC16-Modbus (verified against 7 frames recovered from the APK)
# ---------------------------------------------------------------------------
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(body: bytes) -> bytes:
    """Append the CRC little-endian, as the app does."""
    return body + struct.pack("<H", crc16_modbus(body))


def build_read(reg: int, count: int = 45, fc: int = 0x03) -> bytes:
    """FC03 read holding registers (or FC10 used as 'query block')."""
    body = struct.pack(">BBHH", SLAVE, fc, reg, count)
    return with_crc(body)


def build_write(reg: int, values: list[int]) -> bytes:
    """FC16 write multiple registers."""
    qty = len(values)
    payload = struct.pack(">" + "H" * qty, *values)
    body = struct.pack(">BBHHB", SLAVE, 0x10, reg, qty, qty * 2) + payload
    return with_crc(body)


def check_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return crc16_modbus(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]


def hexdump(data: bytes) -> str:
    h = binascii.hexlify(data).decode()
    pretty = " ".join(h[i:i + 2] for i in range(0, len(h), 2))
    return pretty


# ---------------------------------------------------------------------------
# BLE plumbing
# ---------------------------------------------------------------------------
RX_BUFFER = bytearray()
LAST_RESPONSE = bytearray()


def on_notify(_sender, data: bytearray) -> None:
    global LAST_RESPONSE
    RX_BUFFER.extend(data)
    log(f"RX ff02  {hexdump(bytes(data))}  ({len(data)} bytes)")

    # A Modbus response for FC03 starts with slave, fc, bytecount.
    if len(data) >= 3 and data[0] == SLAVE:
        LAST_RESPONSE = bytearray(data)
        log(f"        <- slave 0x{data[0]:02x} fc 0x{data[1]:02x} "
            f"bytes {data[2]}  crc_ok={check_crc(bytes(data))}")


def decode_registers(frame: bytes, start_reg: int) -> None:
    """Decode a FC03 response: slave, fc, bytecount, data..., crc."""
    if len(frame) < 5:
        log("!! response too short")
        return
    nbytes = frame[2]
    data = frame[3:3 + nbytes]
    regs = [struct.unpack(">H", data[i:i + 2])[0] for i in range(0, len(data) - 1, 2)]
    log(f"-- {len(regs)} registers from {start_reg} --")
    for i, v in enumerate(regs):
        reg = start_reg + i
        # Show both raw and /10 (temperature hypothesis)
        log(f"   reg {reg:5d} (0x{reg:04x}) = {v:5d}   0x{v:04x}   /10 = {v / TEMP_DIVISOR:.1f}")


async def list_devices(timeout: float = 8.0):
    print(f"Scanning BLE for {timeout:.0f}s ...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    items = [(d, (a.local_name or d.name or ""), a) for d, a in found.values()]
    print(f"\n{len(items)} device(s):\n")
    for dev, name, adv in sorted(items, key=lambda x: (x[1] or "~")):
        print(f"  {dev.address:<20} {name or '(no name)':<24} rssi={adv.rssi}")
    print()
    return items


def pick(items, forced=None):
    if forced:
        f = forced.lower()
        for dev, name, adv in items:
            if f in (name or "").lower():
                return dev, name
        return None, None
    for prefix in NAME_FILTERS:
        for dev, name, adv in items:
            if prefix in (name or "").lower():
                return dev, name
    return None, None


async def send_frame(client: BleakClient, frame: bytes) -> None:
    log(f"TX ff01  {hexdump(frame)}  crc_ok={check_crc(frame)}")
    try:
        await client.write_gatt_char(UUID_WRITE, frame, response=True)
        log("        sent (with response)")
    except Exception:
        try:
            await client.write_gatt_char(UUID_WRITE, frame, response=False)
            log("        sent (without response)")
        except Exception as exc:  # noqa: BLE001
            log(f"        FAILED: {exc}")


async def run(args) -> int:
    items = await list_devices(args.timeout)
    if args.list:
        return 0
    if not items:
        log("no BLE device found")
        return 2

    dev = name = None
    if args.addr:
        for d, n, a in items:
            if d.address.lower() == args.addr.lower():
                dev, name = d, n
                break
        if dev is None:
            log(f"address {args.addr} not in scan result")
            return 3
    else:
        dev, name = pick(items, args.name)
        if dev is None:
            print("\nNo match (blueng/hiflying/aqua/phinx/pool). Rerun with "
                  "--addr or --name.\n")
            return 4

    log(f"connecting to {name or dev.address} ({dev.address}) ...")
    async with BleakClient(dev) as client:
        log(f"connected: {client.is_connected}")

        # Confirm the characteristics exist.
        for service in client.services:
            for ch in service.characteristics:
                if "ff0" in ch.uuid:
                    log(f"  found {ch.uuid}  [{','.join(ch.properties)}]")

        try:
            await client.start_notify(UUID_NOTIFY, on_notify)
            log("subscribed to ff02")
        except Exception as exc:  # noqa: BLE001
            log(f"!! cannot subscribe ff02: {exc}")

        async def do_read(reg: int, count: int = 45, fc: int = 0x03) -> None:
            await send_frame(client, build_read(reg, count, fc))
            await asyncio.sleep(args.wait)
            if LAST_RESPONSE:
                decode_registers(bytes(LAST_RESPONSE), reg)
            else:
                log("no response captured (increase --wait)")

        # non-interactive shortcuts
        if args.read is not None:
            await do_read(args.read, args.count)
        if args.raw:
            for f in args.raw:
                await send_frame(client, bytes.fromhex(f.replace(" ", "")))
                await asyncio.sleep(args.wait)
        if args.read is not None or args.raw:
            return 0

        log("")
        log("Interactive. Commands:")
        log("   r <reg> [count]     read registers (FC03)")
        log("   q <reg> [count]     query high block (FC10)")
        log("   w <reg> <v> [v..]   write registers (FC16)")
        log("   x <hex>             send raw frame")
        log("   dump                decode last response")
        log("   qq                  quit")
        log("")

        loop = asyncio.get_event_loop()
        while True:
            line = (await loop.run_in_executor(None, sys.stdin.readline)).strip()
            if not line:
                continue
            if line in ("qq", "quit", "exit"):
                break
            parts = line.split()
            cmd = parts[0]
            try:
                if cmd == "r" and len(parts) >= 2:
                    await do_read(int(parts[1], 0), int(parts[2]) if len(parts) > 2 else 45)
                elif cmd == "q" and len(parts) >= 2:
                    await do_read(int(parts[1], 0), int(parts[2]) if len(parts) > 2 else 45,
                                  fc=0x10)
                elif cmd == "w" and len(parts) >= 3:
                    reg = int(parts[1], 0)
                    vals = [int(x, 0) for x in parts[2:]]
                    await send_frame(client, build_write(reg, vals))
                    await asyncio.sleep(args.wait)
                elif cmd == "x" and len(parts) >= 2:
                    await send_frame(client, bytes.fromhex(parts[1].replace(" ", "")))
                    await asyncio.sleep(args.wait)
                elif cmd == "dump":
                    if LAST_RESPONSE:
                        decode_registers(bytes(LAST_RESPONSE), 7)
                    else:
                        log("nothing captured yet")
                else:
                    log(f"unknown: {line!r}")
            except Exception as exc:  # noqa: BLE001
                log(f"!! {exc}")

        try:
            await client.stop_notify(UUID_NOTIFY)
        except Exception:  # noqa: BLE001
            pass

    log("disconnected")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Aqua Temp Modbus-over-BLE client")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--name")
    ap.add_argument("--addr")
    ap.add_argument("--read", type=lambda x: int(x, 0), help="read registers from this one")
    ap.add_argument("--count", type=int, default=45)
    ap.add_argument("--raw", action="append", default=[], help="send raw hex frame(s)")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--wait", type=float, default=2.0, help="seconds to wait for a reply")
    args = ap.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
