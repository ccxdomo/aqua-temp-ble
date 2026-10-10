#!/usr/bin/env python3
"""Unit tests for modbus.py — the Aqua Temp protocol layer.

Acceptance gate (task constraint #2): the 7 frames recovered from the vendor
APK MUST all pass CRC validation. Run standalone:

    python3 tests/test_modbus.py

or via pytest if available. No pytest API is used, so plain python works.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"))

import modbus  # noqa: E402

# --- the 7 frames recovered from the vendor APK object pool, all [P] ---
KNOWN_FRAMES = [
    "63030007002d3c54",  # read base block 7..51 (FC03)
    "631003e9002dd9e6",  # query block 1001-1045
    "63100416002de8a2",  # query block 1046-1090
    "63100443002df8b2",  # query block 1091-1135
    "63100470002d08bd",  # query block 1136-1180
    "631007d1002d591b",  # query block 2001-2045
    "631007fe002d68d2",  # query block 2046-2090
]

# H1 write vectors (qty=1, bytecount=2, big-endian 16-bit value)
WRITE_VECTORS = [
    (8, [1], "631000080001020001d7ba"),     # Power on
    (8, [0], "631000080001020000167a"),      # Power off
    (13, [0], "6310000d0001020000162f"),     # Mode cool
    (13, [1], "6310000d0001020001d7ef"),     # Mode heat
    (13, [2], "6310000d000102000297ee"),     # Mode auto
    (14, [325], "6310000e0001020145d67f"),  # Setpoint 32.5 degC
]

QUERY_VECTORS = [
    (1001, "631003e9002dd9e6"),
    (1046, "63100416002de8a2"),
    (1091, "63100443002df8b2"),
    (1136, "63100470002d08bd"),
    (2001, "631007d1002d591b"),
    (2046, "631007fe002d68d2"),
]

SYNTHETIC_VALUES = {
    8: 1, 11: 0, 13: 1, 14: 325, 19: 300, 20: 315, 21: 280,
    22: 60, 23: 400, 24: 60, 25: 400,
    26: 243, 27: -9990, 28: -125, 47: 0, 48: 2, 49: 2, 50: 1, 51: 2040,
}


def _raises(expected: type[BaseException], fn, *args, **kwargs) -> bool:
    """True if fn(*args, **kwargs) raises exactly `expected`."""
    try:
        fn(*args, **kwargs)
    except expected:
        return True
    except BaseException:
        return False
    return False


def _build_response(
    values: dict[int, int], start: int = 7, count: int = 45, little_endian: bool = False
) -> bytes:
    """Build a synthetic FC03 response frame (95 bytes for 45 registers)."""
    payload = bytearray()
    for addr in range(start, start + count):
        v = values.get(addr, 0)
        payload += (v & 0xFFFF).to_bytes(2, "little" if little_endian else "big")
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, len(payload)]) + bytes(payload)
    return head + modbus.crc16_modbus(head).to_bytes(2, "little")


# --------------------------------------------------------------------- tests
def test_crc_known_frames() -> None:
    """The 7 recovered frames must all carry a valid Modbus CRC."""
    for hexstr in KNOWN_FRAMES:
        frame = bytes.fromhex(hexstr)
        assert modbus.validate_crc(frame), f"validate_crc failed for {hexstr}"
        assert modbus.crc16_modbus(frame[:-2]) == int.from_bytes(frame[-2:], "little"), (
            f"crc16 mismatch for {hexstr}"
        )


def test_build_read_holding_registers() -> None:
    assert modbus.build_read_holding_registers(7, 45).hex() == KNOWN_FRAMES[0]


def test_build_query_block() -> None:
    for start, hexstr in QUERY_VECTORS:
        assert modbus.build_query_block(start, 45).hex() == hexstr


def test_build_write_registers_h1() -> None:
    for start, values, hexstr in WRITE_VECTORS:
        assert modbus.build_write_registers(start, values).hex() == hexstr


def test_parse_read_response_round_trip() -> None:
    resp = _build_response(SYNTHETIC_VALUES)
    assert len(resp) == 95  # slave, fc, bytecount, 90 data, crc
    regs = modbus.parse_read_response(resp, 7, 45)
    assert len(regs) == 45
    assert regs[7] == 0
    assert regs[8] == 1
    assert regs[13] == 1
    assert regs[14] == 325          # 32.5 degC
    assert regs[26] == 243          # 24.3 degC
    assert regs[27] == -9990        # sentinel preserved (sensor absent)
    assert regs[28] == -125         # negative temperature preserved (-12.5 degC)
    assert regs[47] == 0
    assert regs[51] == 2040


def test_parse_read_response_little_endian() -> None:
    """The calibration switch flips the decode; wrong order gives swapped bytes."""
    le_resp = _build_response(SYNTHETIC_VALUES, little_endian=True)
    le_regs = modbus.parse_read_response(le_resp, 7, 45, little_endian=True)
    be_regs = modbus.parse_read_response(le_resp, 7, 45)  # decoded with wrong order
    assert le_regs[26] == 243 and le_regs[14] == 325
    assert be_regs[26] != 243  # e.g. 0x00F3 bytes read as 0xF300


def test_crc_error_path() -> None:
    resp = _build_response(SYNTHETIC_VALUES)
    corrupt = bytearray(resp)
    corrupt[10] ^= 0xFF
    assert _raises(modbus.ModbusCRCError, modbus.parse_read_response, bytes(corrupt), 7, 45)


def test_exception_response() -> None:
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING | 0x80, 0x02])
    exc = head + modbus.crc16_modbus(head).to_bytes(2, "little")
    try:
        modbus.parse_read_response(exc, 7, 45)
        raised = False
    except modbus.ModbusExceptionError as err:
        raised = err.code == 2 and len(exc) == 5
    assert raised


def test_malformed_response() -> None:
    resp = _build_response(SYNTHETIC_VALUES)
    assert _raises(modbus.ModbusError, modbus.parse_read_response, resp[:20], 7, 45)
    # wrong bytecount field
    head = bytes([modbus.SLAVE_ID, modbus.FC_READ_HOLDING, 0x2B]) + resp[3:91]
    bad = head + modbus.crc16_modbus(head).to_bytes(2, "little")
    assert _raises(modbus.ModbusError, modbus.parse_read_response, bad, 7, 45)


def test_write_echo() -> None:
    head = bytes([modbus.SLAVE_ID, modbus.FC_WRITE_MULTI]) + (8).to_bytes(2, "big") + (1).to_bytes(2, "big")
    echo = head + modbus.crc16_modbus(head).to_bytes(2, "little")
    assert len(echo) == 8
    modbus.parse_write_echo(echo, 8, 1)  # must not raise
    assert _raises(modbus.ModbusError, modbus.parse_write_echo, echo, 9, 1)  # mismatch
    bad = bytearray(echo)
    bad[3] ^= 0xFF
    assert _raises(modbus.ModbusError, modbus.parse_write_echo, bytes(bad), 8, 1)
    # exception echo (fc 0x90): a 5-byte frame fails the 8-byte echo shape
    # check first, per the blueprint's parse_write_echo. The exception *code*
    # itself is surfaced by the client's _complete() — see
    # test_reassembly.py::test_exception_response_raises.
    exhead = bytes([modbus.SLAVE_ID, modbus.FC_WRITE_MULTI | 0x80, 0x03])
    execho = exhead + modbus.crc16_modbus(exhead).to_bytes(2, "little")
    assert _raises(modbus.ModbusError, modbus.parse_write_echo, execho, 8, 1)


def test_expected_response_length() -> None:
    resp = _build_response(SYNTHETIC_VALUES)
    assert modbus.expected_response_length(bytes.fromhex("63")) is None
    assert modbus.expected_response_length(bytes.fromhex("6303")) is None
    assert modbus.expected_response_length(resp[:3]) == 95
    assert modbus.expected_response_length(resp[:40]) == 95
    assert modbus.expected_response_length(bytes.fromhex("63100008")) == 8
    assert modbus.expected_response_length(bytes.fromhex("6383")) == 5
    assert modbus.expected_response_length(bytes.fromhex("6203")) is None  # wrong slave
    assert modbus.expected_response_length(bytes.fromhex("6363")) is None  # unknown fc


def main() -> int:
    tests = [
        test_crc_known_frames,
        test_build_read_holding_registers,
        test_build_query_block,
        test_build_write_registers_h1,
        test_parse_read_response_round_trip,
        test_parse_read_response_little_endian,
        test_crc_error_path,
        test_exception_response,
        test_malformed_response,
        test_write_echo,
        test_expected_response_length,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as err:
            failures += 1
            print(f"FAIL  {test.__name__}: {err}")
        else:
            print(f"PASS  {test.__name__}")
    print()
    if failures:
        print(f"FAILED: {failures} test(s)")
        return 1
    print(f"PASSED: {len(tests)}/{len(tests)} tests "
          f"({len(KNOWN_FRAMES)} recovered frames CRC-verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())