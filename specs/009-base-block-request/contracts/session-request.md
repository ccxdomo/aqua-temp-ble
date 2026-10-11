# Contract 009 — session-request rules (normative)

These rules bind every implementation of spec 009 and every follow-up
that touches the request path. Companion contracts: 008-A
(frame-formats), 008-B (reassembly-matching) — both unchanged. Proof
base: research 009 (tagged [P]/[V]/[A]/[D] there).

## §1 Session order (unchanged from 006/008)

```
connect → enumerate GATT → resolve notify (ff02) → subscribe (CCCD)
→ resolve write char → settle 2.0 s → FIRST request (base read)
→ subsequent requests serialized one-at-a-time
```

- The first request of every session is the base read
  `63030007002d3c54` [P — vendor's first frame, FINDINGS §11.1]. No
  handshake/unlock/activation frame exists or may be invented
  [P — research 006 §8].
- Settle stays 2.0 s (`SETTLE_AFTER_SUBSCRIBE`); role parity with the
  app's 2 s Duration [P pool; role [V] — FR-2's re-send covers the
  "poll cadence" reading without changing the settle].

## §2 What may be sent — the frame allowlist

1. **Integration** (any code under `custom_components/`):
   - `63030007002d3c54` — the base read [P];
   - FC10 writes, H1 layout, to addresses in the spec-003 allowlist
     only ({8,13,14} + {19,20,21} behind `WRITE_MODE_SETPOINT_TOO`);
   - `build_query_block` frames (the six app-proven starts) — probe
     tool only, never polling.
2. **Probe** (`tools/session_probe.py`): the above six block queries,
   plus the experimental pair `63100007002db997` /
   `630303e9002d5c25` behind `--experimental` (§4).
3. **Never**: any other function code, any other start register in the
   integration, broadcast addresses, config-mode frames, the time-sync
   prefix `631004f3…` (research 006 §5: a manual settings feature).
4. Adding any frame to tier 1 REQUIRES a new spec citing live evidence
   for it (the 008→009 discipline: no speculative frames in
   production).

## §3 Census grammar (FR-1) — normative strings

- Request-window census line:
  `block push observed: start={start} qty={qty} len={len} crc={verdict} (waiting for start {expected}; t+{offset:.2f}s)`
- Timeout message:
  `no matching response within {timeout:.1f}s (received {frames} frame(s) / {bytes} bytes; blocks seen: {start}x{n}, …; first t+{first:.1f}s, last t+{last:.1f}s; none for start {start})`
  (blocks list = per-start counts, sorted by start; `none` when no
  CRC-valid block was seen).
- Idle-window summary (exactly once per connection, at the first
  request, INFO):
  `idle window before first request: {n} block push(es), starts seen: {list}, first at t+{offset:.2f}s after subscribe`
  (`0 block push(es)` when none — the absence is the E2 datum.)
- Invariants carried over from contract 008-B §5/§6: counters are
  honest totals (bytes counted even for CRC-invalid frames); nothing
  is dropped without a log line; the deadline message NEVER reports
  fewer bytes than were received.

## §4 Experimental-frame gates

- Phase E (probe) requires `--experimental` AND must follow Phase Q in
  the same run; the tool prints the risk statement (research §8)
  before the first experimental TX.
- `63100007002db997` (query-shaped base request): may be sent ONLY
  after Phase Q proved the engine reacts to query-shaped frames on the
  current write pipe. If Q is negative, sending it is forbidden — it
  would test nothing and adds an unproven frame to the link.
- `630303e9002d5c25` (FC03 at 1001): read-only; allowed under the same
  flag as a control experiment isolating "FC03 handler off" from
  "register 7 absent".
- The experimental pair NEVER enters tier 1 (§2.4) without a follow-up
  spec citing a positive live answer.

## §5 Re-send bounds (FR-2)

- Applies ONLY to the config-flow connection test's first read.
- Parameters: `resend_every=2.0`, `resend_max=3` → sends at t+0, +2,
  +4, +6 s, all inside the unchanged 8.0 s request window.
- The re-sent bytes are the identical frame (assert byte equality in
  tests); every re-send logs `first-read re-send {n}/3: {hex}` at INFO.
- Exactly one request may be in flight (single-flight unchanged); the
  re-send loop is cancelled when the request completes, fails, or the
  client disconnects.
- The coordinator's polling read passes NO re-send parameters
  (single-shot per cycle) until a follow-up spec cites evidence (C2
  verdict) — extending the re-send to polling is a spec-010 decision.

## §6 Decision matrix (research §11) — binding follow-up

| Evidence | Follow-up MUST |
|---|---|
| Phase Q positive (out-of-cycle 1001 answer) | test `63100007002db997` (Phase E); on positive answer → spec 010 adopts query-shaped base reads |
| Q negative, re-send/poll answered | spec 010 adopts app-parity polling for the first read (and possibly polling), citing attempt counts |
| Phase W: ATT error on ff03 | spec 010 re-resolves the write char per the enumeration (candidate order), retests |
| No pipe reacts; enumeration shows another writable char | retarget + retest before any protocol theory |
| E5 shows the app's session differs from ours in char/write-type/order | diff and replicate the app's exact session shape in the integration |
| Nothing reacts anywhere AND E5 shows the app also gets no FC03 answer on this unit | Track B (push harvest + decode) becomes the primary read plane; control path reopens with E5's write evidence |

A follow-up spec that skips a row it owes evidence for is invalid by
this contract.

## §7 Probe safety invariants

- The probe NEVER writes registers (FC10 write frames are not in its
  allowlist); phases are reads/queries + connection-level experiments
  only.
- The probe stops at the first decisive phase when `--stop-on-answer`
  is set; default runs the full phase plan.
- The probe prints the enumerated GATT table at connect (it doubles as
  the E4 capture) and tags every RX event with the receiving
  characteristic under `--subscribe-all`.