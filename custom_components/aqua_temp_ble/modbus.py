"""Modbus RTU frame build/parse for Aqua Temp heat pumps over BLE.

Pure protocol layer — NO I/O here. Everything in this module is unit-testable
standalone:

    python3 custom_components/aqua_temp_ble/modbus.py

Facts recovered from the vendor app (FINDINGS.md §11), all CRC-verified:
- slave address 0x63
- CRC16-Modbus (poly 0xA001, init 0xFFFF), appended little-endian (low first)
- base read: FC03 start=7 count=45 -> 63030007002d3c54 (45 registers, 95-byte response)
- proprietary 8-byte "query block" frames share the FC10 shape (response unknown [V])
- writes: FC10, H1 layout (qty=1, bytecount=2, big-endian 16-bit value)
- temperatures: value = int(degrees_C * 10); sentinel -9990 = sensor absent
"""

from __future__ import annotations

import struct

SLAVE_ID = 0x63
FC_READ_HOLDING = 0x03
FC_WRITE_MULTI = 0x10


class ModbusError(Exception):
    """Base class for Modbus protocol errors."""


class ModbusCRCError(ModbusError):
    """Frame CRC does not match."""


class ModbusExceptionError(ModbusError):
    """Device answered with a Modbus exception code."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Modbus exception code {code} (0x{code:02X})")
        self.code = code


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus: poly 0xA001, init 0xFFFF. Wire order: low byte first."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _frame(fc: int, payload: bytes) -> bytes:
    """Build slave+fc+payload+CRC (CRC appended little-endian)."""
    adu = struct.pack(">BB", SLAVE_ID, fc) + payload
    return adu + struct.pack("<H", crc16_modbus(adu))


def build_read_holding_registers(start: int, count: int) -> bytes:
    """FC03 read holding registers. start=7,count=45 -> 63030007002d3c54  [P]"""
    return _frame(FC_READ_HOLDING, struct.pack(">HH", start, count))


def build_query_block(start: int, count: int) -> bytes:
    """Proprietary 8-byte block query: 63 10 start qty CRC.
    start=1001,count=45 -> 631003e9002dd9e6  [P] (response format [V])"""
    return _frame(FC_WRITE_MULTI, struct.pack(">HH", start, count))


def build_write_registers(start: int, values: list[int]) -> bytes:
    """FC10 write, H1 layout: qty=len(values), bytecount=2*qty, big-endian.
    start=14, values=[325] -> 6310000e0001020145d67f

    H1 (qty=1, 16-bit) is the safe default chosen in the blueprint §0: if the
    device actually expects the 32-bit H2 layout (qty=2, bytecount=4), an H1
    frame is at worst ignored — it can never clobber a neighbour register.
    """
    payload = struct.pack(">HHB", start, len(values), 2 * len(values))
    payload += struct.pack(">" + "H" * len(values), *[v & 0xFFFF for v in values])
    return _frame(FC_WRITE_MULTI, payload)


def expected_response_length(buf: bytes) -> int | None:
    """Total Modbus frame length implied by the first bytes, or None if unknown.

    FC03 response: addr, fc, bytecount + bytecount data + 2 CRC = bytecount+5.
    FC10 echo:     addr, fc, start:2, qty:2 + 2 CRC = 8.
    Exception:     addr, fc|0x80, errcode + 2 CRC = 5.
    """
    if len(buf) < 2 or buf[0] != SLAVE_ID:
        return None
    fc = buf[1]
    if fc & 0x80:
        return 5
    if fc == FC_READ_HOLDING:
        return buf[2] + 5 if len(buf) >= 3 else None
    if fc == FC_WRITE_MULTI:
        return 8
    return None


def validate_crc(frame: bytes) -> bool:
    """True when the trailing little-endian CRC matches the frame body."""
    return len(frame) >= 4 and crc16_modbus(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]


def parse_read_response(
    frame: bytes, start: int, count: int, little_endian: bool = False
) -> dict[int, int]:
    """Validate + decode an FC03 response to {register: raw signed int16}.

    little_endian comes from registers.REG_DATA_LITTLE_ENDIAN (calibration
    switch, runbook §4.4 step 2). Standard Modbus is big-endian. [A]
    """
    if len(frame) < 5 or not validate_crc(frame):
        raise ModbusCRCError(f"bad CRC: {frame.hex()}")
    if frame[1] == FC_READ_HOLDING | 0x80:
        raise ModbusExceptionError(frame[2])
    if frame[1] != FC_READ_HOLDING or frame[2] != 2 * count or len(frame) != 2 * count + 5:
        raise ModbusError(f"malformed response: {frame.hex()}")
    regs: dict[int, int] = {}
    fmt = "<h" if little_endian else ">h"
    for i in range(count):
        regs[start + i] = struct.unpack(fmt, frame[3 + 2 * i : 5 + 2 * i])[0]
    return regs


def parse_write_echo(frame: bytes, start: int, qty: int) -> None:
    """Validate an FC10 echo (addr, fc, start:2, qty:2, CRC). Raises on mismatch."""
    if len(frame) != 8 or not validate_crc(frame):
        raise ModbusError(f"bad write echo: {frame.hex()}")
    if frame[1] == FC_WRITE_MULTI | 0x80:
        raise ModbusExceptionError(frame[2])
    if struct.unpack(">HH", frame[2:6]) != (start, qty):
        raise ModbusError(f"echo mismatch: {frame.hex()}")


# ---------------------------------------------------------------------------
# Self-test: run this file directly. Gate = all recovered vectors byte-exact.
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    def _check(name: str, ok: bool, failures: list[str]) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    # The 7 frames recovered from the vendor APK object pool. [P]
    KNOWN_FRAMES = [
        "63030007002d3c54",  # read base block 7..51
        "631003e9002dd9e6",  # query block 1001-1045
        "63100416002de8a2",  # query block 1046-1090
        "63100443002df8b2",  # query block 1091-1135
        "63100470002d08bd",  # query block 1136-1180
        "631007d1002d591b",  # query block 2001-2045
        "631007fe002d68d2",  # query block 2046-2090
    ]
    WRITE_VECTORS = [
        (8, [1], "631000080001020001d7ba"),    # power on
        (8, [0], "631000080001020000167a"),     # power off
        (13, [0], "6310000d0001020000162f"),    # mode cool
        (13, [1], "6310000d0001020001d7ef"),    # mode heat
        (13, [2], "6310000d000102000297ee"),    # mode auto
        (14, [325], "6310000e0001020145d67f"),  # setpoint 32.5 degC
    ]
    QUERY_VECTORS = [
        (1001, "631003e9002dd9e6"),
        (1046, "63100416002de8a2"),
        (1091, "63100443002df8b2"),
        (1136, "63100470002d08bd"),
        (2001, "631007d1002d591b"),
        (2046, "631007fe002d68d2"),
    ]

    failures: list[str] = []

    # 1. CRC against the 7 recovered frames
    for hexstr in KNOWN_FRAMES:
        frame = bytes.fromhex(hexstr)
        ok = validate_crc(frame) and crc16_modbus(frame[:-2]) == struct.unpack(
            "<H", frame[-2:]
        )[0]
        _check(f"CRC vector {hexstr}", ok, failures)

    # 2. Builders byte-exact
    _check(
        "build_read_holding_registers(7,45)",
        build_read_holding_registers(7, 45).hex() == KNOWN_FRAMES[0],
        failures,
    )
    for start, hexstr in QUERY_VECTORS:
        _check(
            f"build_query_block({start},45)",
            build_query_block(start, 45).hex() == hexstr,
            failures,
        )
    for start, values, hexstr in WRITE_VECTORS:
        _check(
            f"build_write_registers({start},{values})",
            build_write_registers(start, values).hex() == hexstr,
            failures,
        )

    # 3. Round-trip of a synthetic 95-byte FC03 response (values incl. the
    #    sentinel and a negative temperature)
    values = {8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
              22: 60, 23: 400, 24: 60, 25: 400,
              26: 243, 27: -9990, 28: -125, 47: 0, 48: 2, 49: 2, 50: 1, 51: 2040}
    payload = bytearray()
    for addr in range(7, 52):
        payload += struct.pack(">h", values.get(addr, 0))
    resp = _frame(FC_READ_HOLDING, bytes([len(payload)]) + bytes(payload))
    _check("synthetic response is 95 bytes", len(resp) == 95, failures)
    regs = parse_read_response(resp, 7, 45)
    _check(
        "parse round-trip",
        len(regs) == 45
        and regs[7] == 0
        and regs[8] == 1
        and regs[14] == 325
        and regs[26] == 243
        and regs[27] == -9990  # sentinel preserved
        and regs[28] == -125   # negative temperature preserved
        and regs[51] == 2040,
        failures,
    )

    # 4. Little-endian decode flip (registers.REG_DATA_LITTLE_ENDIAN)
    le_payload = bytearray()
    for addr in range(7, 52):
        le_payload += struct.pack("<h", values.get(addr, 0))
    le_resp = _frame(FC_READ_HOLDING, bytes([len(le_payload)]) + bytes(le_payload))
    le_regs = parse_read_response(le_resp, 7, 45, little_endian=True)
    be_regs = parse_read_response(le_resp, 7, 45)  # decoded with wrong order
    _check(
        "little-endian flip",
        le_regs[26] == 243 and le_regs[14] == 325 and be_regs[26] != 243,
        failures,
    )

    # 5. CRC error path
    corrupt = bytearray(resp)
    corrupt[10] ^= 0xFF
    try:
        parse_read_response(bytes(corrupt), 7, 45)
        ok = False
    except ModbusCRCError:
        ok = True
    _check("CRC-corrupt response raises ModbusCRCError", ok, failures)

    # 6. Modbus exception response
    exc = _frame(FC_READ_HOLDING | 0x80, b"\x02")
    try:
        parse_read_response(exc, 7, 45)
        ok = False
    except ModbusExceptionError as err:
        ok = len(exc) == 5 and err.code == 2
    _check("exception response raises ModbusExceptionError(2)", ok, failures)

    # 7. Malformed response
    try:
        parse_read_response(resp[:20], 7, 45)
        ok = False
    except ModbusError:
        ok = True
    _check("truncated response raises ModbusError", ok, failures)

    # 8. Write echo
    echo = _frame(FC_WRITE_MULTI, struct.pack(">HH", 8, 1))
    try:
        parse_write_echo(echo, 8, 1)
        ok_echo = len(echo) == 8
    except ModbusError:
        ok_echo = False
    _check("write echo validates", ok_echo, failures)
    try:
        parse_write_echo(echo, 9, 1)
        mismatch_ok = False
    except ModbusError:
        mismatch_ok = True
    _check("write echo mismatch raises", mismatch_ok, failures)
    bad_echo = bytearray(echo)
    bad_echo[3] ^= 0xFF
    try:
        parse_write_echo(bytes(bad_echo), 8, 1)
        crc_ok = False
    except ModbusError:
        crc_ok = True
    _check("bad-CRC echo raises", crc_ok, failures)

    # 9. expected_response_length
    checks = [
        (expected_response_length(bytes.fromhex("63")) is None, "len<2 -> None"),
        (expected_response_length(bytes.fromhex("6303")) is None, "no bytecount yet -> None"),
        (expected_response_length(resp[:3]) == 95, "FC03 header -> 95"),
        (expected_response_length(resp[:40]) == 95, "FC03 partial -> 95"),
        (expected_response_length(bytes.fromhex("63100008")) == 8, "FC10 -> 8"),
        (expected_response_length(bytes.fromhex("6383")) == 5, "exception -> 5"),
        (expected_response_length(bytes.fromhex("6203")) is None, "wrong slave -> None"),
        (expected_response_length(bytes.fromhex("6363")) is None, "unknown fc -> None"),
    ]
    for ok, name in checks:
        _check(f"expected_response_length: {name}", ok, failures)

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} failure(s)")
        raise SystemExit(1)
    print(f"SELF-TEST PASSED: all vectors OK ({len(KNOWN_FRAMES)} recovered frames verified)")