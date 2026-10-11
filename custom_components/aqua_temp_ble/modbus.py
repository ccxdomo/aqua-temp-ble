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
# The unit's block-frame responses reuse the query-block request byte
# (research 008 §2.1) — NOT a write echo. A block frame is
#   63 10 start:2 qty:2 bytecount data:bytecount crc:2LE
# and bytecount == 2*qty discriminates it from the 8-byte echo whose
# offset-6 byte is a CRC byte (research 008 §5 Q6).
FC_BLOCK = 0x10


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


def _decode_registers(
    body: bytes, start: int, count: int, little_endian: bool
) -> dict[int, int]:
    """Decode count int16 registers from body -> {register: signed int16}.

    Shared by parse_read_response and parse_block_response (plan 008
    §1.1 refactor); byte-identical behaviour for FC03 — the self-test
    round-trip vectors pin it.
    """
    regs: dict[int, int] = {}
    fmt = "<h" if little_endian else ">h"
    for i in range(count):
        regs[start + i] = struct.unpack(fmt, body[2 * i : 2 * i + 2])[0]
    return regs


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
    return _decode_registers(frame[3 : 3 + 2 * count], start, count, little_endian)


def parse_block_response(
    frame: bytes, start: int, count: int, little_endian: bool = False
) -> dict[int, int]:
    """Validate + decode the unit's block frame to {register: signed int16}.

    Shape [P, CRC-verified on 4 live frames — research 008 §2.1]:
      63 10 start:2 qty:2 bytecount data:bytecount crc16:2 (LE)
    Length = bytecount + 9 (99 for qty=45). Raises ModbusCRCError (bad
    CRC), ModbusExceptionError (fc|0x80), ModbusError (shape/start/qty/
    bytecount mismatch). The same little_endian calibration switch as the
    base decode (registers.REG_DATA_LITTLE_ENDIAN).
    """
    if len(frame) < 9 or not validate_crc(frame):
        raise ModbusCRCError(f"bad CRC: {frame.hex()}")
    if frame[1] == FC_BLOCK | 0x80:
        raise ModbusExceptionError(frame[2])
    if (
        frame[0] != SLAVE_ID
        or frame[1] != FC_BLOCK
        or struct.unpack(">HH", frame[2:6]) != (start, count)
        or frame[6] != 2 * count
        or len(frame) != frame[6] + 9
    ):
        raise ModbusError(f"malformed block response: {frame.hex()}")
    return _decode_registers(frame[7 : 7 + frame[6]], start, count, little_endian)


def block_response_length(buf: bytes) -> int | None:
    """Length of a 63 10 start:2 qty:2 bytecount data crc frame, or None.

    None below 7 bytes (bytecount byte not visible yet) and when
    buf[6] != 2*qty(buf[4:6]) — the discriminator against the 8-byte
    write echo, whose offset-6 byte is a CRC byte (research 008 §5 Q6:
    the two known echoes carry 0x88/0x68 there — no false positive).
    """
    if len(buf) < 7 or buf[0] != SLAVE_ID or buf[1] != FC_BLOCK:
        return None
    qty = struct.unpack(">H", buf[4:6])[0]
    if buf[6] != 2 * qty:
        return None
    return buf[6] + 9


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

    # 10. Block frames (spec 008) — the four proven production frames the
    #     c8d8260 reassembler dropped 12 times (research 008 §1/§2).
    block_vectors = [
        # (name, full hex, start, calc CRC, {reg: value} spot checks)
        (
            "1001-1045",
            "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a",
            1001,
            0x3A56,
            {1001: 0, 1012: 1, 1021: 1050, 1038: 30, 1045: 0},
        ),
        (
            "1046-1090",
            "63100416002d5a01220005000500960190010e000500050064015effba00820028000800030000000000000000000000000000010e00000000000000000000000000000000000000000000000000000000000000000000000000000000000000008576",
            1046,
            0x7685,
            {1046: 290, 1056: -70, 1068: 270, 1090: 0},
        ),
        (
            "2046-2090 push 1",
            "631007fe002d5a7ffd008c008c008c00870096" + "00" * 78 + "f49b",
            2046,
            0x9BF4,
            {2046: 32765, 2047: 140, 2051: 150, 2090: 0},
        ),
        (
            "2046-2090 push 2",
            "631007fe002d5a7ffd0091008c008c00870096" + "00" * 78 + "a565",
            2046,
            0x65A5,
            {2046: 32765, 2047: 145, 2051: 150},
        ),
    ]
    for name, hexstr, start, crc_val, spot in block_vectors:
        frame = bytes.fromhex(hexstr)
        ok = (
            len(frame) == 99
            and validate_crc(frame)
            and crc16_modbus(frame[:-2]) == crc_val
            and struct.unpack("<H", frame[-2:])[0] == crc_val
        )
        _check(f"block frame {name}: 99 B, CRC 0x{crc_val:04X}", ok, failures)
        try:
            regs = parse_block_response(frame, start, 45)
            ok = len(regs) == 45 and all(regs.get(r) == v for r, v in spot.items())
        except ModbusError:
            ok = False
        _check(f"parse_block_response({name}) decode", ok, failures)

    # 11. block_response_length — the boundary rule + the echo
    #     discriminator (research 008 §5 Q6, contract 008-A §3).
    frame1001 = bytes.fromhex(block_vectors[0][1])
    ok = all(block_response_length(frame1001[:p]) == 99 for p in range(7, 100))
    _check("block_response_length: 99 from every prefix >= 7", ok, failures)
    ok = all(block_response_length(frame1001[:p]) is None for p in range(0, 7))
    _check("block_response_length: None below 7 bytes (never guess)", ok, failures)
    echo8 = _frame(FC_WRITE_MULTI, struct.pack(">HH", 8, 1))
    echo14 = _frame(FC_WRITE_MULTI, struct.pack(">HH", 14, 1))
    ok = (
        echo8[6] == 0x88
        and echo14[6] == 0x68
        and block_response_length(echo8) is None
        and block_response_length(echo14) is None
    )
    _check("block_response_length: write echoes (offset-6 0x88/0x68) rejected", ok, failures)
    # Pathological synthetic echo whose CRC low byte == 2*qty: the length
    # rule reads it as a block frame (the documented ambiguity — the
    # reassembler's silence/deadline match check self-heals it, contract
    # 008-B §4.3).
    patho = bytes.fromhex("631020000001024b")
    ok = patho[6] == 0x02 and block_response_length(patho) == 11
    _check("block_response_length: pathological echo (CRC_lo == 2*qty) -> 11", ok, failures)

    # 12. parse_block_response error paths (plan 008 §1.1 validation order)
    corrupt_block = bytearray(frame1001)
    corrupt_block[10] ^= 0xFF
    try:
        parse_block_response(bytes(corrupt_block), 1001, 45)
        ok = False
    except ModbusCRCError:
        ok = True
    _check("block CRC-corrupt raises ModbusCRCError", ok, failures)
    try:
        parse_block_response(frame1001[:50], 1001, 45)
        ok = False
    except ModbusCRCError:
        ok = True
    _check("block truncated raises ModbusCRCError", ok, failures)
    try:
        parse_block_response(frame1001, 1091, 45)
        ok = False
    except ModbusError as err:
        ok = type(err) is ModbusError
    _check("block wrong start raises ModbusError", ok, failures)
    try:
        parse_block_response(frame1001, 1001, 44)
        ok = False
    except ModbusError as err:
        ok = type(err) is ModbusError
    _check("block wrong qty raises ModbusError", ok, failures)
    exc_head = bytes([SLAVE_ID, FC_BLOCK | 0x80, 0x02]) + b"\x00" * 4
    exc_frame = exc_head + crc16_modbus(exc_head).to_bytes(2, "little")
    try:
        parse_block_response(exc_frame, 1001, 45)
        ok = False
    except ModbusExceptionError as err:
        ok = err.code == 2 and len(exc_frame) == 9
    _check("block exception fc raises ModbusExceptionError(2)", ok, failures)
    le_block = parse_block_response(frame1001, 1001, 45, little_endian=True)
    _check(
        "block little-endian flip",
        le_block[1021] == 0x1A04,
        failures,
    )

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} failure(s)")
        raise SystemExit(1)
    print(f"SELF-TEST PASSED: all vectors OK ({len(KNOWN_FRAMES)} recovered frames verified)")