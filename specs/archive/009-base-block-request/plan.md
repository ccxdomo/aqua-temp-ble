# Spec 009 plan — implementation design for the Coder

Everything follows `research.md` (proofs and cause ranking) and
`contracts/session-request.md` (normative rules). Read spec.md §2 for
the FR each block implements. Order below = implementation order; every
block ends green (`pytest tests/` + the modbus self-test). The strategy
in one line: **instrument first, one bounded experiment in production,
no new frames in the integration** (research §7/§11).

## 1. Strategy — why instrumentation, and why one re-send

Three causes survive the byte-accounting proof (research §0.2): C1
(write never reaches the engine), C2 (answers only after repeats /
longer window, incl. C5 collision-timing), C3 (push-only firmware). The
probe phases L/B/P/Q/W separate them in ONE run at the panel (research
§11). The re-send (FR-2) is deliberately shipped into the connection
test **before** the probe verdict because it is the only change that is
simultaneously (a) vendor-plausible (the app's 2 s Duration sits in the
subscribe→read slice [P research 006 §2], role [V]), (b) risk-free
(idempotent FC03 read, 4 × 8 bytes, inside the unchanged window,
research §8 allowlist), and (c) itself an experiment: its census counts
answers per attempt. If it fixes the first read, the project moves
forward without waiting for anyone; if it changes nothing, the probe
carries the diagnosis. Everything else waits for evidence
(decision matrix, research §11).

## 2. FR-1 — census upgrade (aqua_client.py)

### 2.1 State

```python
self._req_t0: float | None = None          # request start (for t+ offsets)
self._req_first_seen: float | None = None  # first census frame offset
self._req_last_seen: float | None = None   # last census frame offset
self._idle_pushes: list[tuple[int, float]] = []  # (start, t since subscribe)
```

The existing `_req_rx_bytes` / `_req_frame_count` / `_req_block_starts`
stay. Idle state resets in `connect()` (after subscribe) and clears in
`disconnect()` alongside the 008 census state (contract 008-B §7).

### 2.2 Timestamps

Use the existing `_now()` seam (never `time.monotonic` directly — spec
007's test contract). `_request` stamps `_req_t0` when the first TX
goes out; census lines render `t+%.2fs` relative to it
(`_req_first_seen`/`_req_last_seen` update on every censused frame).
The idle summary renders offsets relative to the subscribe moment:
`connect()` already computes `t_notify` for the connect summary — store
the subscribe time on the instance (e.g. `self._t_subscribe`) so the
idle-state init can use it.

### 2.3 Grammar (normative — contract 009 §3)

- Census line (request window):
  `block push observed: start=%d qty=%d len=%d crc=%s (waiting for start %s; t+%.2fs)`
- Non-block census line: unchanged shape + `; t+%.2fs`.
- Timeout message:
  `no matching response within %.1fs (received %d frame(s) / %d bytes; blocks seen: %s; first t+%.1fs, last t+%.1fs; none for start %s)`
  where the blocks list renders per-start counts
  (`", ".join(f"{start}x{n}" for …)`, sorted; fallback `none`).
- Idle-window summary (INFO, once, at the first request of a
  connection, BEFORE the TX debug line):
  `idle window before first request: %d block push(es), starts seen: %s, first at t+%.2fs after subscribe`
  (`no block pushes` when the list is empty — that absence IS the E2
  evidence, quickstart §0/§1).

### 2.4 Tests (update test_reassembly_matching.py / new test_census_009.py)

- AC-1 vector run: 3 × (1001, 1046, 2001, 2046) fake-notify → assert
  message contains `1001x3` … `2046x3`, `first t+`, `last t+`,
  `none for start 7`.
- Idle summary: fake-notify pushes during settle → assert exactly one
  summary line, correct start list and offset; no pushes →
  `idle window before first request: 0 block push(es)`.
- Old 008 properties re-pinned: counters are totals (bytes still
  counted for CRC-invalid frames), nothing dropped silently, census
  never fatal.

## 3. FR-2 — bounded re-send (aqua_client.py + config_flow.py)

### 3.1 API

```python
async def read_registers(
    self, start: int, count: int, *, resend_every: float | None = None,
    resend_max: int = 0,
) -> dict[int, int]:
```

Default `None`/`0` = today's single-shot (coordinator unchanged,
NFR-3). The connection test passes `resend_every=2.0, resend_max=3`
(sends at t+0/2/4/6 s inside the unchanged 8.0 s window).

### 3.2 Mechanism (inside `_request`)

After the initial `_write_frame(frame)`, when `resend_every` is set and
`resend_max > 0`: spawn a re-send task on the running loop:

```python
async def _resend_loop() -> None:
    for n in range(1, resend_max + 1):
        await asyncio.sleep(resend_every)          # 2.0 s cadence
        if done.is_set() or self._pending is None:
            return
        await self._write_frame(frame)            # identical bytes
        _LOGGER.info("first-read re-send %d/%d: %s", n, resend_max, frame.hex())

```

guarded by an `asyncio.Event` set in the `finally` (task cancelled
there too — never leaks past the request). No new pending state, no
match change, no timeout change: the re-sends share the ONE in-flight
request (contract 009 §5).

### 3.3 Call site

`config_flow.py` first-read:
`read_registers(BASE_BLOCK_START, BASE_BLOCK_COUNT, resend_every=2.0, resend_max=3)`
— one-line change; the stage arithmetic and UI keys untouched.

### 3.4 Tests (new test_first_read_resend.py)

- Fake write char + faked clock: with the parameters → exactly 4 writes
  at t+0/2/4/6 (assert write payloads identical
  `63030007002d3c54`); without → exactly 1 (coordinator parity,
  NFR-3).
- Answer arrives after the 2nd send → request completes once, re-send
  task stops (no writes past completion).
- Nothing arrives → same AquaTempTimeoutError, message unchanged by
  FR-1 rules, budget arithmetic unchanged (elapsed still ≈ window).
- Disconnect mid-window → re-send loop cancelled, no leaked task
  (`all_tasks()` empty of loops after the request).

## 4. FR-3 — `tools/session_probe.py` (new file)

### 4.1 Shape

CLI after `tools/smoke_client.py`'s pattern (sys.path insert, import
`modbus`/`const` from the component — NO HA imports, NO aqua_client
dependency: the probe owns its bleak client so it can do things the
client forbids: subscribe-all, write-with-response, phase timing).

```
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C \
  [--phases L,B,P,Q,W,E] [--listen 30] [--b-window 15] \
  [--poll-every 2.0] [--poll-count 15] [--subscribe-all] \
  [--write-with-response] [--experimental] [--dry-run] [--debug]
```

Default phases: `L,B,P,Q,W` (E only with `--experimental`; P after B).
Output: one line per event with ms timestamps
(`[00003.412] RX 99B block start=1001 …`), phase markers
(`=== PHASE Q: TX 631003e9002dd9e6 (query 1001) ===`), a decoded
per-register dump of every block frame (reuse the 008 probe format:
`reg NNNN = raw | /10`), and an end-of-run summary (per-start counts,
cadence stats min/median/max gap, per-phase verdicts).

### 4.2 Core loop

- Enumerate the GATT table on connect and PRINT it (the probe is also
  the E4 capture: ff03's properties, all handles).
- Reassembly: the probe owns a small pure boundary function
  implementing contract 008-A §3's rule (slave check → exception 5 →
  FC03 bytecount+5 → FC10 block-shape bytecount+9 vs 8-byte echo →
  else unknown). It deliberately does NOT import aqua_client (the
  probe manages its own notifications); a unit test pins its behaviour
  equal to the shipped vectors (99 from every prefix ≥ 7 of a block
  frame, 8 for the two known echoes, None below 7 bytes).
- `--subscribe-all`: subscribe to every characteristic with notify or
  indicate, tag each event with the char UUID (C6).
- TX helper honoring `--write-with-response` per phase (W forces
  with-response).

### 4.3 Phase verdicts (printed, human-readable)

- **L**: `push onset: t+X s after subscribe; cadence median Y s;
  starts: 1001x?, …` → E2/E7 decided.
- **B**: `answer: FC03 95B | exception | none within 15 s` → E1/E3.
- **P**: `answered on attempt N | none in N attempts` → C2/C5.
- **Q**: **out-of-cycle test** — verdict POSITIVE only if a 1001 frame
  arrives whose inter-frame gap from the previous frame is
  significantly BELOW the Phase-L baseline median (e.g. < 50 %), i.e.
  an immediate reaction, not the cycle's next 1001 slot (~2.7 s away)
  → C1 vs C3.
- **W**: `ATT error: <code> | write accepted, then <reaction>` → C1a.
- **E**: gated print of the risk statement (research §8), then verdicts
  as B for `63100007002db997` and `630303e9002d5c25`.

### 4.4 Gates baked into the tool (contract 009 §4)

- `--dry-run`: prints phases, frames, CRCs; never connects.
- Phase E refuses without `--experimental` (hard error, tested).
- The probe never sends FC10 writes; the frame table is a module-level
  frozenset and TX asserts membership (allowlist, research §8).

### 4.5 Tests (new test_session_probe.py)

Pure-helper tests only (no bleak in CI): phase plan/dry-run text;
allowlist membership; E-gate error; Q-verdict logic on synthetic event
timelines (in-cycle vs out-of-cycle arrival); cadence stats. The bleak
flow is verified by the owner per quickstart §2.

## 5. FR-4/FR-5 — kit

This kit IS the deliverable (quickstart §0-§4, contract). The Coder
does not edit the kit (AC-5 checks presence) but implements everything
this plan names.

## 6. Grep gates (final diff)

- No change to `REQUEST_TIMEOUT|SETTLE_AFTER_SUBSCRIBE|SILENCE_TIMEOUT|
  WRITE_ECHO_TIMEOUT|MAX_RX_BUFFER|TEST_TIMEOUT` values.
- `63100007002d|630303e9` appear ONLY under `tools/` (NFR-4).
- `resend_every` appears in aqua_client.py + config_flow.py + tests,
  NOWHERE else (NFR-3).
- New comments cite `research 009 §…` / `contract 009 §…` (NFR-7).