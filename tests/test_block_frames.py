#!/usr/bin/env python3
"""Block-frame protocol tests (spec 008) — the unit's block-response grammar.

The four proven production frames (research 008 §2, CRC-verified by
calculation; the same vectors live in
specs/008-first-read-decode/decode_frames.py) are the test basis:

- parse_block_response must decode them byte-exact (AC-1);
- block_response_length must compute their boundary (99 bytes) from every
  prefix >= 7 bytes, return None below 7, and NEVER confuse the 8-byte
  write echo with a block frame — the offset-6 bytecount==2*qty test is
  the discriminator (research 008 §5 Q6, contract frame-formats.md §2.3).

Run standalone:

    python3 tests/test_block_frames.py

No Bluetooth and no Home Assistant required.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble")
)

import modbus  # noqa: E402

# --- the four proven production frames (research 008 §2, CRC-verified) -----
# Byte-exact transcriptions / CRC-oracle reconstructions of the five
# "dropping unexpected function code 0x10" log lines (2026-10-11).
FRAME_BLOCK_1001 = (
    "631003e9002d5a00000000000000000000000000000000000000000000000100000000000000000000000100000000041a0001000200000000000000000000000000000000000000000000000000000002001e0003000100000000000000000000563a"
)
FRAME_BLOCK_1046 = (
    "63100416002d5a01220005000500960190010e000500050064015effba00820028000800030000000000000000000000000000010e00000000000000000000000000000000000000000000000000000000000000000000000000000000000000008576"
)
FRAME_BLOCK_2046_PUSH1 = (
    "631007fe002d5a7ffd008c008c008c00870096" + "00" * 78 + "f49b"
)
FRAME_BLOCK_2046_PUSH2 = (
    "631007fe002d5a7ffd0091008c008c00870096" + "00" * 78 + "a565"
)

PROVEN_FRAMES = [
    (FRAME_BLOCK_1001, 1001, 0x3A56),
    (FRAME_BLOCK_1046, 1046, 0x7685),
    (FRAME_BLOCK_2046_PUSH1, 2046, 0x9BF4),
    (FRAME_BLOCK_2046_PUSH2, 2046, 0x65A5),
]

# The two write echoes whose offset-6 byte (a CRC byte, not a bytecount)
# rejects the block shape: 0x88 / 0x68 != 2*qty (research 008 §8,
# contract frame-formats.md §2.3).
ECHO_REG8 = "6310000800018849"
ECHO_REG14 = "6310000e00016868"

# Pathological echoes whose CRC low byte equals 2*qty — the block
# discriminator misreads them as block frames (contract 008-B §4.3):
# qty=1  -> CRC_lo == 0x02, boundary rule would wait for 11 bytes;
# qty=45 -> CRC_lo == 0x5A, boundary rule would wait for 99 bytes.
PATHO_ECHO_QTY1 = "631020000001024b"    # start 8192, crc 0x4B02
PATHO_ECHO_QTY45 = "63100811002d5a33"   # start 2065, crc 0x335A


def _raises(expected: type[BaseException], fn, *args, **kwargs) -> bool:
    """True if fn(*args, **kwargs) raises exactly `expected`."""
    try:
        fn(*args, **kwargs)
    except expected:
        return True
    except BaseException:
        return False
    return False


# --------------------------------------------------------------------- tests
def test_proven_frames_crc_valid() -> None:
    """The four production frames are 99 bytes and CRC-valid [P]."""
    for hexstr, _start, crc in PROVEN_FRAMES:
        frame = bytes.fromhex(hexstr)
        assert len(frame) == 99, f"{hexstr[:14]}... is {len(frame)} bytes, not 99"
        assert modbus.validate_crc(frame), f"validate_crc failed for {hexstr[:14]}..."
        assert modbus.crc16_modbus(frame[:-2]) == crc, (
            f"CRC mismatch for {hexstr[:14]}...: expected 0x{crc:04X}"
        )
        assert modbus.crc16_modbus(frame[:-2]) == int.from_bytes(
            frame[-2:], "little"
        ), "stored CRC is not little-endian"


def test_parse_block_1001() -> None:
    """Block 1001-1045: reg 1021 == 1050 (the H04 exhaust-high threshold,
    research 008 §2.2); the rest are sparse flags or zero."""
    frame = bytes.fromhex(FRAME_BLOCK_1001)
    regs = modbus.parse_block_response(frame, 1001, 45)
    assert len(regs) == 45
    assert regs[1012] == 1
    assert regs[1018] == 1
    assert regs[1021] == 1050          # 105.0 degC = H04 threshold [P panel]
    assert regs[1022] == 1
    assert regs[1023] == 2
    assert regs[1037] == 2
    assert regs[1038] == 30
    assert regs[1039] == 3
    assert regs[1040] == 1
    assert regs[1001] == 0             # zero registers decode as 0
    assert regs[1002] == 0
    assert regs[1045] == 0


def test_parse_block_1046() -> None:
    """Block 1046-1090: the R-family regulation ladder (research 008 §2.3)."""
    frame = bytes.fromhex(FRAME_BLOCK_1046)
    regs = modbus.parse_block_response(frame, 1046, 45)
    assert len(regs) == 45
    assert regs[1046] == 290           # 29.0
    assert regs[1049] == 150           # 15.0
    assert regs[1050] == 400           # 40.0
    assert regs[1055] == 350           # 35.0
    assert regs[1056] == -70           # -7.0 — signed int16 decode
    assert regs[1057] == 130           # 13.0
    assert regs[1068] == 270           # 27.0
    assert regs[1090] == 0


def test_parse_block_2046_push1() -> None:
    """Block 2046-2090 push 1: the 0x7FFD sentinel at 2046 [P]."""
    frame = bytes.fromhex(FRAME_BLOCK_2046_PUSH1)
    regs = modbus.parse_block_response(frame, 2046, 45)
    assert len(regs) == 45
    assert regs[2046] == 32765         # 0x7FFD sentinel — sensor absent
    assert regs[2047] == 140           # 14.0 degC
    assert regs[2048] == 140
    assert regs[2049] == 140
    assert regs[2050] == 135           # 13.5
    assert regs[2051] == 150           # 15.0
    assert regs[2052] == 0
    assert regs[2090] == 0


def test_parse_block_2046_push2() -> None:
    """Block 2046-2090 push 2: reg 2047 changed 140 -> 145 between two
    pushes — LIVE data (research 008 §2.5)."""
    frame = bytes.fromhex(FRAME_BLOCK_2046_PUSH2)
    regs = modbus.parse_block_response(frame, 2046, 45)
    assert len(regs) == 45
    assert regs[2046] == 32765
    assert regs[2047] == 145           # 14.5 degC — the live change [P]
    assert regs[2048] == 140
    assert regs[2051] == 150


def test_block_little_endian_flip() -> None:
    """The calibration switch flips the decode (same switch as the base
    block, registers.REG_DATA_LITTLE_ENDIAN)."""
    frame = bytes.fromhex(FRAME_BLOCK_1001)
    be_regs = modbus.parse_block_response(frame, 1001, 45)
    le_regs = modbus.parse_block_response(frame, 1001, 45, little_endian=True)
    assert be_regs[1021] == 1050       # bytes 04 1A read big-endian
    assert le_regs[1021] == 0x1A04     # same bytes read little-endian
    assert le_regs[1021] != be_regs[1021]


def test_parse_block_error_paths() -> None:
    """Corrupt CRC, truncation, wrong start, wrong qty, exception fc."""
    frame = bytes.fromhex(FRAME_BLOCK_1001)

    # Corrupt one data byte -> ModbusCRCError
    corrupt = bytearray(frame)
    corrupt[10] ^= 0xFF
    assert _raises(
        modbus.ModbusCRCError, modbus.parse_block_response, bytes(corrupt), 1001, 45
    )

    # Truncated frame -> the CRC cannot validate -> ModbusCRCError
    assert _raises(
        modbus.ModbusCRCError, modbus.parse_block_response, frame[:50], 1001, 45
    )

    # Trailing bytes re-CRC'd: CRC valid but the length mismatches the
    # declared bytecount -> ModbusError (malformed shape)
    short = frame[:50]
    bad_len = short[:-2] + modbus.crc16_modbus(short[:-2]).to_bytes(2, "little")
    assert _raises(
        modbus.ModbusError, modbus.parse_block_response, bad_len, 1001, 45
    )

    # Wrong start echoed -> ModbusError
    assert _raises(
        modbus.ModbusError, modbus.parse_block_response, frame, 1091, 45
    )

    # Wrong qty echoed -> ModbusError
    assert _raises(
        modbus.ModbusError, modbus.parse_block_response, frame, 1001, 44
    )

    # Exception function code (fc 0x10|0x80 = 0x90) with a valid CRC over
    # >= 9 bytes -> ModbusExceptionError (validation order step 2,
    # plan 008 §1.1)
    head = bytes([modbus.SLAVE_ID, 0x90, 0x02]) + b"\x00" * 4
    exc = head + modbus.crc16_modbus(head).to_bytes(2, "little")
    assert len(exc) == 9
    try:
        modbus.parse_block_response(exc, 1001, 45)
        raised = False
    except modbus.ModbusExceptionError as err:
        raised = err.code == 2
    assert raised


def test_block_response_length_prefixes() -> None:
    """99 from every prefix >= 7 bytes (bytecount byte visible);
    None below 7 — never guess (research 008 §3: cutting the 99-byte
    frame at byte 8 was the second production bug)."""
    frame = bytes.fromhex(FRAME_BLOCK_1001)
    for prefix_len in range(0, 7):
        assert modbus.block_response_length(frame[:prefix_len]) is None, (
            f"prefix {prefix_len} must be None (bytecount not visible yet)"
        )
    for prefix_len in range(7, len(frame) + 1):
        assert modbus.block_response_length(frame[:prefix_len]) == 99, (
            f"prefix {prefix_len} must resolve to 99"
        )


def test_block_response_length_rejects_echoes() -> None:
    """The two known write echoes carry a CRC byte at offset 6 (0x88 /
    0x68), never 2*qty — no false positive (contract frame-formats.md
    §2.3, research 008 §8)."""
    for hexstr, offset6 in ((ECHO_REG8, 0x88), (ECHO_REG14, 0x68)):
        echo = bytes.fromhex(hexstr)
        assert len(echo) == 8
        assert echo[6] == offset6, f"echo {hexstr}: offset-6 byte moved"
        assert modbus.block_response_length(echo) is None, (
            f"echo {hexstr} must not look like a block frame"
        )
        # a short prefix of an echo is also None
        assert modbus.block_response_length(echo[:7]) is None


def test_block_response_length_pathological_echo() -> None:
    """The documented ambiguity (contract 008-B §4.3): an echo whose CRC
    low byte equals 2*qty IS read as a block frame by the length rule —
    the silence/deadline match check self-heals it in the reassembler."""
    echo1 = bytes.fromhex(PATHO_ECHO_QTY1)
    assert echo1[6] == 0x02 == 2 * 1
    assert modbus.block_response_length(echo1) == 11      # 0x02 + 9
    echo45 = bytes.fromhex(PATHO_ECHO_QTY45)
    assert echo45[6] == 0x5A == 2 * 45
    assert modbus.block_response_length(echo45) == 99     # 0x5A + 9


def test_block_response_length_wrong_slave_or_fc() -> None:
    frame = bytes.fromhex(FRAME_BLOCK_1001)
    # wrong slave
    assert modbus.block_response_length(b"\x62" + frame[1:]) is None
    # fc 0x03 is not the block shape
    assert modbus.block_response_length(b"\x63\x03\x5a" + frame[3:]) is None
    # empty / tiny buffers
    assert modbus.block_response_length(b"") is None
    assert modbus.block_response_length(b"\x63") is None


def test_parse_block_wrong_slave_rejected() -> None:
    """FR-1: slave 0x63 is part of the validation."""
    frame = bytearray(bytes.fromhex(FRAME_BLOCK_1001))
    frame[0] = 0x62
    # the CRC breaks with the slave byte flipped; re-CRC to isolate the
    # slave check itself
    bad = bytes(frame[:97]) + modbus.crc16_modbus(bytes(frame[:97])).to_bytes(2, "little")
    assert _raises(modbus.ModbusError, modbus.parse_block_response, bad, 1001, 45)


def main() -> int:
    tests = [
        test_proven_frames_crc_valid,
        test_parse_block_1001,
        test_parse_block_1046,
        test_parse_block_2046_push1,
        test_parse_block_2046_push2,
        test_block_little_endian_flip,
        test_parse_block_error_paths,
        test_block_response_length_prefixes,
        test_block_response_length_rejects_echoes,
        test_block_response_length_pathological_echo,
        test_block_response_length_wrong_slave_or_fc,
        test_parse_block_wrong_slave_rejected,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as err:
            failures += 1
            print(f"FAIL  {test.__name__}: {err}")
        except Exception as err:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {test.__name__}: unexpected {type(err).__name__}: {err}")
        else:
            print(f"PASS  {test.__name__}")
    print()
    if failures:
        print(f"FAILED: {failures} test(s)")
        return 1
    print(f"PASSED: {len(tests)}/{len(tests)} block-frame tests "
          f"(4 proven production frames decoded byte-exact)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())