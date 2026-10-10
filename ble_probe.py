#!/usr/bin/env python3
"""
Aqua Temp (com.phinx.poolheater4) — BLE probe & controller
==========================================================

Purpose
-------
The static analysis of the APK (see FINDINGS.md) fully recovered the transport
(UUIDs, scan filter) but NOT the frame payloads: they are built at runtime and
the Dart AOT snapshot is protected with `dedup_instructions`.

This tool talks to the unit directly over BLE so the real frames can be
observed, and later replayed.

Two modes
---------
  1. probe   (default) : connect, subscribe to notifications on FF02,
                         print every received frame as hex + ASCII,
                         and let you send raw frames on FF01.
  2. scan-only         : list nearby BLE devices to find the unit's name.

Requirements
------------
  pip install bleak

Usage
-----
  python ble_probe.py                       # auto: scan, pick the unit, probe
  python ble_probe.py --name BlueNRG        # force a name filter
  python ble_probe.py --list                # just list nearby BLE devices
  python ble_probe.py --addr XX:XX:..:XX    # connect to a known MAC

During the session, type commands on stdin (press Enter after each):
  s                send a "read status" style frame? -> no, use raw hex below
  w <hex>          write the given hex bytes to FF01   (e.g. w aa0100ff55)
  h <hex>          write hex to FF01, no spaces
  t                print the transcript of everything seen so far
  q                quit

Observed procedure while using the official app (do this while this tool runs,
or compare transcripts):
  1. read the temperatures in the app   -> note the frames on FF02
  2. change the setpoint (e.g. 28->29)  -> note the frame on FF01 + the echo
  3. power on/off                       -> note the frame on FF01

The goal is to correlate UI actions with hex frames.
"""

from __future__ import annotations

import argparse
import asyncio
import binascii
import sys
import time
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("ERROR: bleak is not installed.  Run:  pip install bleak", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Known identifiers, recovered from classes3.dex (see FINDINGS.md)
# ---------------------------------------------------------------------------
UUID_WRITE = "0000ff01-0000-1000-8000-00805f9b34fb"   # commands  -> write here
UUID_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"   # status    <- notify here
UUID_CCCD = "00002902-0000-1000-8000-00805f9b34fb"     # CCCD (handled by bleak)

# Device name prefixes the official app filters on (from the Dart pool).
NAME_FILTERS = ["hiflying", "blueng", "aqua", "phinx", "pool"]

TRANSCRIPT: list[str] = []


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg: str) -> None:
    line = f"[{ts()}] {msg}"
    print(line)
    TRANSCRIPT.append(line)


def hexdump(data: bytes) -> str:
    h = binascii.hexlify(data).decode()
    pretty = " ".join(h[i : i + 2] for i in range(0, len(h), 2))
    ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{pretty:<48} |{ascii_}|"


def on_notify(_sender, data: bytearray) -> None:
    """Called for every notification on FF02."""
    log(f"RX ff02  {hexdump(bytes(data))}")


async def list_devices(timeout: float = 8.0) -> list:
    print(f"Scanning BLE for {timeout:.0f}s ...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    items = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        items.append((dev, name, adv))
    print(f"\n{len(items)} device(s) found:\n")
    for dev, name, adv in sorted(items, key=lambda x: (x[1] or "~")):
        uuids = ",".join(adv.service_uuids or []) or "-"
        print(f"  {dev.address:<20} {name or '(no name)':<24} rssi={adv.rssi:<5} {uuids}")
    print()
    return items


def pick_device(items: list, forced: str | None):
    """Choose the unit: explicit name filter, else known prefixes, else ask."""
    if forced:
        f = forced.lower()
        for dev, name, adv in items:
            if f in (name or "").lower():
                return dev, name
        return None, None

    for prefix in NAME_FILTERS:
        for dev, name, adv in items:
            if prefix in (name or "").lower():
                log(f"Matched '{name}' on prefix '{prefix}'")
                return dev, name
    return None, None


async def probe(dev, name: str, auto_write: list[str] | None = None) -> None:
    log(f"Connecting to {name or dev.address} ({dev.address}) ...")
    async with BleakClient(dev) as client:
        log(f"Connected: {client.is_connected}")

        # Discover and print the GATT tree -- useful to confirm ff01/ff02 exist
        log("GATT services:")
        for service in client.services:
            log(f"  service {service.uuid}")
            for ch in service.characteristics:
                props = ",".join(ch.properties)
                log(f"    char {ch.uuid}  [{props}]")

        log(f"Subscribing to notifications on {UUID_NOTIFY} ...")
        try:
            await client.start_notify(UUID_NOTIFY, on_notify)
            log("Subscribed. Notifications on FF02 will be printed as they arrive.")
        except Exception as exc:  # noqa: BLE001
            log(f"!! Could not subscribe to FF02: {exc}")

        # Optional: fire a few raw frames right away (scripted capture)
        if auto_write:
            for frame in auto_write:
                await write_frame(client, frame)
                await asyncio.sleep(1.5)

        log("")
        log("Interactive mode. Commands:  w <hex>   t (transcript)   q (quit)")
        log("Tip: open the official app now and operate the unit to capture frames.")
        log("")

        loop = asyncio.get_event_loop()
        while True:
            cmd = await loop.run_in_executor(None, lambda: sys.stdin.readline())
            if cmd == "":  # stdin closed
                break
            cmd = cmd.strip()
            if not cmd:
                continue
            if cmd in ("q", "quit", "exit"):
                break
            if cmd == "t":
                print("\n".join(TRANSCRIPT))
                continue
            if cmd.startswith("w ") or cmd.startswith("h "):
                await write_frame(client, cmd[2:].strip())
                continue
            log(f"unknown command: {cmd!r}  (use: w <hex> | t | q)")

        try:
            await client.stop_notify(UUID_NOTIFY)
        except Exception:  # noqa: BLE001
            pass
    log("Disconnected.")


async def write_frame(client: BleakClient, hexstr: str) -> None:
    """Send raw hex bytes (tolerates spaces / 0x / 'aa bb cc') onto FF01."""
    cleaned = hexstr.replace(" ", "").replace("0x", "").replace(",", "")
    try:
        payload = bytes.fromhex(cleaned)
    except ValueError as exc:
        log(f"!! invalid hex {hexstr!r}: {exc}")
        return
    log(f"TX ff01  {hexdump(payload)}")
    try:
        # response=True -> write-with-response when the characteristic allows it
        await client.write_gatt_char(UUID_WRITE, payload, response=True)
        log("        write sent (with response)")
    except Exception as exc:  # noqa: BLE001
        log(f"        with-response failed ({exc}); retrying without response")
        try:
            await client.write_gatt_char(UUID_WRITE, payload, response=False)
            log("        write sent (no response)")
        except Exception as exc2:  # noqa: BLE001
            log(f"        write FAILED: {exc2}")


async def main() -> int:
    ap = argparse.ArgumentParser(description="Aqua Temp BLE probe")
    ap.add_argument("--list", action="store_true", help="only list nearby BLE devices")
    ap.add_argument("--name", help="force a device-name substring (default: auto)")
    ap.add_argument("--addr", help="connect to this MAC/address directly")
    ap.add_argument("--send", action="append", default=[],
                    help="hex frame to send on FF01 after connect (repeatable)")
    ap.add_argument("--timeout", type=float, default=8.0, help="scan timeout (s)")
    args = ap.parse_args()

    items = await list_devices(args.timeout)
    if args.list:
        return 0
    if not items:
        log("No BLE device found. Is the adapter on? Is the unit in pairing/operating mode?")
        return 2

    if args.addr:
        for dev, name, adv in items:
            if dev.address.lower() == args.addr.lower():
                await probe(dev, name, args.send)
                return 0
        log(f"Address {args.addr} not seen in this scan.")
        return 3

    dev, name = pick_device(items, args.name)
    if dev is None:
        print("\nNo obvious match (hiflying / blueng / aqua / phinx / pool).")
        print("Pick one manually, then rerun with:")
        print("   --addr <address>        (or)   --name <substring>\n")
        return 4

    await probe(dev, name, args.send)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\ninterrupted.")
