# Spec 008 — First-read decode: accept the unit's block frames

Status: **PLANNED** — built from `research.md` (the byte-level decode of
the five real production frames, the CRC verification, and the root-cause
trace). Scope guard: this spec does **not** redesign the config flow
(002/004), device resolution (005), session parity (006), stage
instrumentation (007), the register/decode layer (001/003) or the entity
surface (003). It fixes the **transport decode layer** — the frame
grammar knowledge and the reassembly/matching rules — and ships the
measurement runbook that decides the follow-up if the base block is not in
the unit's stream.

## 0. The production fact this spec answers

After the GATT fix (c8d8260: ff01 = service, ff03 = write char, ff02 =
notify char), the connection test now connects, resolves, subscribes and
settles — and then fails at first-read:

```
no response: AquaTempTimeoutError: no complete response within 8.0s
(got 0 bytes) at stage first-read after 12.4 s (budget 70 s)
```

Meanwhile the log shows **12 occurrences of
"dropping unexpected function code 0x10"** — each one a complete,
CRC-valid, 99-byte frame of live register data from the unit
(research §1-§2: four of the five pasted frames are CRC-verified by
calculation).

**The unit answers. Our client deletes every answer.**

The root cause (research §3, [P] against the code): the FC gate in
`_on_notify` drops any frame whose function code ≠ the request's expected
code (0x03 for the base read), clears the buffer, and leaves nothing for
the deadline branch to count — hence "got 0 bytes". Even with the gate
open, `expected_response_length` maps fc 0x10 to the 8-byte write-echo
shape and would cut the 99-byte frame at byte 8 with a bad CRC.

This spec teaches the client the proven frame grammar and replaces the
drop-gate with request-aware matching. It deliberately does **not** change
what we send (the base read stays the vendor-proven `63030007002d3c54`;
no new frames), does **not** change any timeout, and does **not** guess the
follow-up strategy for the base block — the census decides (research §7).

## 1. User stories

- **US-1** As the pool owner, I want the connection test to succeed when
  the unit sends its data, so I can finish configuring the integration
  without reverse-engineering my own log.
- **US-2** As the pool owner, when the first-read still fails, I want the
  log to say **what the unit actually sent** ("received 12 frames, blocks
  1001, 1046, …; none for start 7") instead of "got 0 bytes", so one run
  tells us what to fix next.
- **US-3** As the developer, I want the reassembler to accept the
  unit's block-frame grammar (`63 10 start qty bytecount data CRC`,
  CRC-verified, research §2) and to match responses to requests by
  start/qty, so another block's push can never complete my request.
- **US-4** As the developer, I want every pushed frame logged in a census
  (block start, qty, length, CRC verdict, timing), so the periodic-vs-
  triggered question and the start-7 question are answered by the next
  production run (research §7 E1/E2).
- **US-5** As the integrator, I want the probe tool to decode block
  responses into registers, so the H/R families of spec 003 become
  calibratable with real data (the four decoded blocks are the baseline).

## 2. Functional requirements

### FR-1 — Block-frame protocol knowledge in `modbus.py` [P0]

New pure functions, unit-testable standalone:

- `parse_block_response(frame, start, count, little_endian=False)` →
  validate + decode the proven 99-byte shape (research §2.1) to
  `{register: signed int16}`. Validation: slave 0x63; fc 0x10 (exception
  `fc|0x80` → `ModbusExceptionError` as today); `start`/`qty` echo the
  request; `bytecount == 2*qty`; `len(frame) == bytecount + 9`; CRC16
  little-endian valid (else `ModbusCRCError`). Shares the register-decode
  helper with `parse_read_response`.
- `block_response_length(buf)` → `buf[6] + 9` when the buffer looks like
  a block frame **and** `buf[6] == 2 * qty(buf[4:6])`, else `None`;
  `None` below 7 bytes. The `bytecount == 2*qty` test is the discriminator
  against the 8-byte write echo, whose offset-6 byte is a CRC byte
  (research §5 Q6/§8: the two known echoes carry 0x88/0x68 there — no
  false positive).
- `expected_response_length` keeps its current contract and vectors
  (self-test unchanged); the reassembler consults the block rule FIRST for
  fc-0x10 buffers (plan §2.3, contract `reassembly-matching.md` §2).

### FR-2 — Request-aware matching replaces the FC drop-gate [P0]

`_request` carries a response-match spec instead of the bare
`expected_fc` (plan §2.2): each request kind names the response shapes it
accepts and the start/qty a frame must echo:

- `read_registers(start, count)`: accepts the FC03 answer **and** the
  block frame with `start/qty` equal to the request (FR-3);
- `query_block(start, count)`: accepts the block frame for that
  start/qty;
- `write_registers` echo mode: accepts the 8-byte echo (unchanged).

`_on_notify` becomes: accumulate → boundary-detect (`frame_boundary`,
plan §2.3) → at a complete frame: CRC-validate, then **match** against the
pending request. Matching frames complete the request; non-matching or
CRC-invalid frames are census-logged and discarded **without failing the
request** (it keeps waiting for its own frame). The old drop lines
("dropping unexpected function code", silent stale-push clear) are
replaced by the census (FR-4). Exception frames (`fc|0x80`) still fail
fast with `ModbusExceptionError` [unchanged].

### FR-3 — The base read accepts a pushed block frame [P0]

`read_registers(start, count)` completes on EITHER shape and dispatches to
the matching parser (`parse_read_response` / `parse_block_response`). This
is the production first-read fix: if the unit's stream includes a
`63 10 00 07 00 2d 5a …` block, the test succeeds. The request bytes are
unchanged (`63030007002d3c54` — the vendor-proven base read, research
§5 Q5). No timeout values change (research §7: measure first).

### FR-4 — Census + honest timeout diagnostics [P0]

- While a request is pending: every complete CRC-valid frame that does
  not match logs ONE INFO census line
  (`block push observed: start=%d qty=%d len=%d crc=OK …`, with the block
  start) and increments per-request counters (frames seen, total bytes
  received, block starts seen). CRC-invalid frames log at DEBUG.
- While NO request is pending (settle/idle): census at DEBUG, plus ONE
  INFO line per connect (first push) so periodic-vs-triggered is visible
  without log spam.
- The `AquaTempTimeoutError` message reports the counters:
  `no matching response within 8.0s (received 12 frame(s) / 1188 bytes;
  blocks seen: 1001, 1046, …; none for start 7)` — the "got 0 bytes"
  blind spot is gone (research §5 Q3). The silence-timer and deadline
  accept-paths apply the same match check before completing (FR-2), so a
  non-matching CRC-valid buffer can never resolve a request.

### FR-5 — Probe decodes block responses [P1]

`coordinator.async_probe_block` (high blocks) parses the response with
`parse_block_response` and logs the per-register probe format
(`reg NNNN = raw … | /10 …`) like the base probe; falls back to raw hex
on parse failure. The probe output dict gains `start`/`qty`. No new
blocks, no new entities, no write surface change (spec 003 §6 intact).

### FR-6 — Runbook shipped (no code) [P1]

`quickstart.md`: (1) run the fixed build once and read the census
(decides research §7 E1/E2/E3); (2) if first-read still fails: capture the
`GATT enumeration` line (ff03's advertised properties — E4); (3) probe
block 1001 and compare arrival timing vs stream cadence (E4); (4) the
definitive capture: Android HCI snoop of one vendor session (E5); (5) a
report template. Each step states what it proves and its [P]/[V] status.

## 3. Non-functional requirements

- **NFR-1** Touched files (nothing else):
  `custom_components/aqua_temp_ble/modbus.py`,
  `…/aqua_client.py`,
  `…/coordinator.py` (probe decode only),
  plus tests under `tests/` and the spec-008 kit itself.
- **NFR-2** **No new frames are sent**; no frame the client sends changes;
  no timeout, retry, settle or poll constant changes (REQUEST_TIMEOUT,
  SILENCE_TIMEOUT, SETTLE_AFTER_SUBSCRIBE, WRITE_ECHO_TIMEOUT, MAX_RX_BUFFER
  all untouched). The census is observation (spec 007's philosophy
  continues: measure before tuning).
- **NFR-3** No changes to registers.py, entities, config-flow steps, UI
  keys or translations; the first-read call site
  (`read_registers(BASE_BLOCK_START, BASE_BLOCK_COUNT)`) is unchanged.
- **NFR-4** Standalone mode keeps working (no HA imports in modbus/aqua_client).
- **NFR-5** Durable artifacts in English; every new comment cites
  `research 008 §…`.
- **NFR-6** All existing tests stay green; the `modbus.py` self-test
  vectors (FINDINGS §11 frames) stay byte-identical; new tests only ADD
  coverage.

## 4. Acceptance criteria

- **AC-1** `parse_block_response` decodes the four proven production
  frames byte-exact (vectors embedded in the test; CRC re-verified), and
  raises `ModbusCRCError`/`ModbusError` on corrupt/truncated/mismatched
  frames (unit tests).
- **AC-2** The production case is fixed end-to-end: with a pending
  `read_registers(7,45)` and a notified `63 10 00 07 00 2d 5a …` block
  frame, the request completes and returns the 45 registers (fake-notify
  unit test).
- **AC-3** Regression: a standard FC03 response still completes
  `read_registers` (existing vectors).
- **AC-4** A push for another block during a pending base read is
  census-logged at INFO and does NOT complete or fail the request; a
  later matching frame still completes it (unit test).
- **AC-5** When nothing matching arrives, the timeout message names the
  received frames, total bytes and block starts — never a bare
  "got 0 bytes" (unit test on the message).
- **AC-6** The write-echo path is unchanged: the 8-byte echo completes
  `write_registers`; a push arriving first is discarded without breaking
  the echo wait (unit test; includes the pathological echo whose CRC low
  byte equals 2×qty — the silence timer self-heals it, contract §4.3).
- **AC-7** A fragmented block frame (20-byte chunks, MTU-23 case)
  reassembles to 99 bytes and completes the request (unit test).
- **AC-8** The probe decodes a block-1001 response into the per-register
  probe log format (unit test on `_log_raw_probe` output).
- **AC-9** `python3 -m py_compile` clean on touched files; `pytest tests/`
  fully green; `python3 custom_components/aqua_temp_ble/modbus.py`
  self-test unchanged and passing; no file outside NFR-1's list modified.
- **AC-10** `quickstart.md` exists with the census runbook and the
  E1-E5 experiments (delivered with this kit — Coder verifies presence).

## 5. Risks & guards

| Risk | Guard |
|---|---|
| Accepting 0x10 frames lets a foreign block complete a request | Match rule requires start/qty echo; silence + deadline paths also match-checked (FR-2/FR-4, AC-4); only one request is in flight (single-flight unchanged) |
| Echo/block ambiguity (echo CRC_lo == 2×qty) misparsed as block | Discriminator + silence-timer self-heal (contract §4.3, AC-6); the two known echoes carry 0x88/0x68 at offset 6 [P] |
| Census spams the log at INFO on every poll | INFO only while a request is pending + one INFO per connect when idle; DEBUG otherwise (FR-4) |
| First-read still fails because no start-7 block exists | Not a failure of this spec: the census converts the next run into the decision data (research §7); quickstart §1-§4 pick the follow-up; no blind strategy change (NFR-2) |
| Someone "fixes" the read by switching the base read to query-block format | NFR-2 forbids it this iteration: the query-block request for start 7 is unproven (the vendor app never sends it [P]); measure first |
| Frame grammar turns out wrong on a second unit | Grammar is CRC-proven on THIS unit (research §2.6); parsers raise loudly on mismatch; nothing auto-writes anywhere |

## 6. Out of scope (explicit)

- Changing the base-read request format or any timeout (needs census +
  E4/E5 evidence first — follow-up spec).
- Consuming high-block data into entities (spec 003 families stay
  probe-only).
- Any write-path change (ff03 / write mode) — E4 decides if one is ever
  needed.