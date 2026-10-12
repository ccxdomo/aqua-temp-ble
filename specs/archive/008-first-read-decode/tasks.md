# Spec 008 tasks — ordered implementation blocks for the Coder

Work top-to-bottom; each block ends green (`pytest tests/` + the modbus
self-test). Red-first where noted (reproduce the production bug against
c8d8260 first — the pattern that shipped c8d8260 itself). Normative refs:
spec.md (FR/AC), plan.md (design), contracts/ (rules), research.md
(proofs). Do not touch anything outside spec.md NFR-1's file list.

## Block 0 — baseline (no code changes)

- [x] 0.1 `python3 custom_components/aqua_temp_ble/modbus.py` — self-test
      PASS as-is (all FINDINGS §11 vectors). Note the output; it must be
      byte-identical at the end (NFR-6).
- [x] 0.2 `pytest tests/` — fully green on c8d8260.
- [x] 0.3 `python3 specs/008-first-read-decode/decode_frames.py` — read
      the root-cause simulation you are about to fix (research §3).

## Block 1 — protocol knowledge in `modbus.py` (FR-1, AC-1)

- [x] 1.1 `tests/test_block_frames.py` NEW: vectors for the four proven
      production frames (full hex from `decode_frames.py`):
      parse+decode asserts (reg 1021==1050 in block 1001; reg 1046==290
      in block 1046; regs 2046==32765 / 2047==140 / 2051==150 in block
      2046 push 1; reg 2047==145 in push 2), plus error paths (corrupt
      CRC, truncated, wrong start, wrong qty, exception fc) and the
      little-endian flip. RED on c8d8260 (no parser yet).
- [x] 1.2 `modbus.py`: `FC_BLOCK`, `_decode_registers` refactor (extract
      the loop from `parse_read_response` — round-trip vectors must stay
      byte-identical), `parse_block_response`, `block_response_length`
      (plan §1). `expected_response_length` UNTOUCHED (NFR-6).
- [x] 1.3 `block_response_length` tests: 99 from every prefix ≥ 7;
      None below 7; None for the two known echoes (offset-6 byte
      0x88/0x68); 99 for the pathological synthetic echo
      (CRC_lo == 2*qty — contract 008-B §4.3 test vector).
- [x] 1.4 `modbus.py` self-test: add the 4 proven frames + the
      block-length checks to `__main__` (existing vectors untouched).

## Block 2 — matching reassembler in `aqua_client.py` (FR-2/3/4, AC-2/4/5/6/7)

- [x] 2.1 `tests/test_reassembly_matching.py` NEW, RED-first:
      - **AC-2 (the production case)**: pending `read_registers(7,45)`,
        notify a `63 10 00 07 00 2d 5a …` block frame → request completes,
        45 registers returned. Against c8d8260 this reproduces the
        production failure verbatim (`AquaTempTimeoutError … got 0
        bytes` + the WARNING drop) — keep that assertion pair in the
        test history comment.
      - AC-3 regression: FC03 response still completes.
      - AC-4: block-1001 push during a base read → INFO census logged,
        request unaffected, later start-7 block completes.
      - AC-5: pushes only → timeout message contains
        `received 12 frame(s) / 1188 bytes; blocks seen: 1001, …; none
        for start 7` and NOT `got 0 bytes`.
      - AC-7: fragmented start-7 block (5×20-byte chunks) completes.
      - AC-6: echo path — 8-byte echo completes; a push first is
        discarded and the echo still completes; pathological echo
        (CRC_lo == 2*qty) self-heals via silence timer (contract §4.3).
      - exception frame still fails fast (unchanged).
      - idle census: one INFO per connect, then DEBUG (fake clock).
- [x] 2.2 Implement per plan §2: `_ResponseMatch`, `_frame_boundary`
      (contract 008-A §3 — block rule FIRST for fc 0x10, None below 7
      bytes), the `_on_notify` flow (contract 008-B §2 — never drop for
      function code alone), match predicates (§3), silence/deadline
      match checks (§4), census lines + counters (§5), the normative
      timeout message (§6). State cleanup in `disconnect()`.
- [x] 2.3 Call sites: `read_registers` (accept_fc03+accept_block, parser
      dispatch on frame[1]), `query_block` (accept_block),
      `write_registers` (accept_echo). Signatures unchanged.
- [x] 2.4 Grep gate: the diff contains NO change to
      `REQUEST_TIMEOUT|SILENCE_TIMEOUT|SETTLE_AFTER_SUBSCRIBE|
      WRITE_ECHO_TIMEOUT|MAX_RX_BUFFER` values, no new TX frame, no
      config-flow step change (NFR-2/3).

## Block 3 — probe decode in `coordinator.py` (FR-5, AC-8)

- [x] 3.1 Test: `_log_raw_probe` on a block-1001 response logs
      per-register lines (`reg 1001 = 0 (0x0000) | /10 = 0.0` …) and the
      dict gains `start`/`qty`; ModbusError → raw-hex fallback branch.
      RED on c8d8260 (the probe would try the FC03 parse and fall back).
- [x] 3.2 Implement (plan §3): try `parse_block_response` first, keep the
      raw fallback. No new blocks, no entity change.

## Block 4 — kit + docs

- [x] 4.1 Confirm the spec kit exists as delivered (spec/plan/research/
      data-model/contracts ×2/quickstart/tasks + decode_frames.py +
      reconstruct.py) — the Coder does NOT edit them (AC-10 checks
      presence only).
- [x] 4.2 Every new code comment cites `research 008 §…` /
      `contract 008-B §…` (NFR-5). All comments in English.

## Block 5 — final gates (hand to Reviewer)

- [x] 5.1 `python3 -m py_compile` on modbus.py / aqua_client.py /
      coordinator.py.
- [x] 5.2 `python3 custom_components/aqua_temp_ble/modbus.py` — self-test
      PASS, Block-0 output unchanged + new vectors.
- [x] 5.3 `pytest tests/` fully green.
- [x] 5.4 `git status` — only NFR-1 files modified.
- [x] 5.5 Reviewer checklist (plan §5): AC-1 … AC-10 one by one; the
      grep gate from 2.4 re-run on the final diff.

## Definition of done

All ACs of spec 008 pass; no constant in NFR-2's list changed; the
production case (pushed start-7 block → first-read success) is pinned by
AC-2's test; the census message is pinned by AC-5's test; the kit is
intact. Production verification (the run itself) follows quickstart §1 —
its outcome (E1 yes/no) feeds the follow-up spec if the base block is
not in the unit's stream.