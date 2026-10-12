# Spec 011 — Android vs Linux/BlueZ: the transport-level write gate — diagnostics, pairing support, and the evidence-gated fix

Status: **PLANNED** — built from `research.md` (the ATT Write-Command
silence mechanism, the Android/BlueZ security asymmetry, and the levers
bleak/BlueZ actually expose). Scope guard: this spec does **not** change
the frame allowlist (contract 009 §2), the session order (contract 009
§1), the register map (001/003), the decode layer (008), the census
(008/009), the re-send bounds (009 §5) or the resolver (010 — which may
land before or after this spec; see FR-8 coordination note). It ships
exactly three things:

1. **the security probe** — the config-flow connection test, on
   first-read timeout, re-sends the SAME base-read frame as a
   Write Request (`response=True`) and reports the ATT verdict in the
   log (FR-1/FR-2);
2. **the pairing attempt** — when the probe reports a security-class
   error (or, in "force" mode, always), the test attempts to bond via
   the bleak/bleak-retry-connector pairing APIs, then re-runs the base
   read once (FR-3/FR-4);
3. **the evidence-gated production lever** — an integration OPTION
   `pair_on_connect` (default OFF) that makes every connection
   establish with pairing enabled, to be switched on by the operator
   after the probe/pair experiments prove the bond gate (FR-6).

No new frame bytes are ever sent (FR-5). No behavior of the normal
read/poll path changes while the option stays OFF (FR-7).

## 0. Why this spec exists (one paragraph)

The vendor app and our integration send byte-identical frames to the
same characteristic with the same write type — and only the app's
writes are answered (research 011 §1, F1-F3). The only layer left is
the transport, and the transport's one brutal asymmetry is the ATT
Write Command's lack of an error channel: a server that refuses our
write cannot say so (research 011 §2.1 [P]). The leading cause is a
security gate on ff03's value writes (encrypted-link requirement) that
the bonded phone passes and the unbonded Pi fails — silently, by ATT
design (research 011 §3/§11). This spec makes the refusal **visible**
(the acknowledged probe), makes the bond **actionable** (the pairing
ladder), and makes the fix **permanent if proven** (the option).

## 1. Functional requirements

### FR-1 — Acknowledged-write seam in the client [P0]

`AquaTempBleClient._write_frame` and `_request` gain a
`force_response: bool = False` parameter (transport seam; tests keep
overriding `_write_frame`):

- `force_response=False` → the current behavior, byte-identical
  (without-response, the vendor parity of spec 010);
- `force_response=True` → the same `bytes(frame)` written with
  `response=True`.

`read_registers(start, count, *, force_response=False, …)` threads the
flag through. **The frame bytes are NOT touched by this flag** —
byte-equality is asserted in tests (AC-2). The match spec, census,
re-send bounds and timeouts of specs 008/009 are reused unchanged: a
probe request is the same request machinery with one ATT-level
difference. If the acknowledged write raises (bleak
`BleakGATTProtocolError` on bleak ≥3, or any BleakError chain on
older bleak), the client converts it to
`AquaTempConnectionError(f"acknowledged write failed: {exc_chain(exc)}")`
— a NEW, distinguishable error class carrying the chain verbatim (the
ATT error text must survive to the log; spec 005 discipline).

### FR-2 — Security-probe stage in the config-flow connection test [P0]

`config_flow._async_test` extends its existing stage sequence
(`connect` → `first-read`) with evidence-driven stages:

```
connect → first-read (unchanged, incl. the 009 re-send)
        → [on AquaTempTimeoutError] security-probe (one acknowledged
          base read, up to REQUEST_TIMEOUT + the ATT round-trip)
        → [on security-class error] pair → first-read-retry (one
          base read, re-send bounds unchanged)
        → [on pair success] first-read-retry (same call)
        → disconnect
```

Rules (normative strings in `contracts/security-probe.md` §3):

1. The probe and the retries reuse `read_registers` with the existing
   `_ResponseMatch`, census and timeout semantics — no new frames, no
   new matching rules.
2. The probe's outcome is classified by the client's exception text
   (research 011 §10.1 taxonomy) into exactly one verdict, logged at
   INFO with the `security probe:` prefix: `att-error-security` /
   `att-error-permission` / `att-error-handle` / `att-accepted-silent`
   / `local-failure` (contract §5).
3. The pairing attempt (FR-3) runs ONLY after an `att-error-security`
   verdict, or when the operator selects the "force" variant in the
   flow menu (§FR-2.1). It never runs inside the coordinator's poll
   path (FR-7).
4. Budget: the 70.0 s envelope is UNCHANGED and every stage runs
   inside the flow-level `asyncio.timeout(TEST_TIMEOUT)` — the typical
   path fits (≈3 connect + 3 setup + 8 + ≤8 + ≤25 + 8 ≈ 55 s, contract
   §4); a pathological 2×20 s connect leaves the probe stages to hit
   the flow timeout mid-way, which is an accepted, logged outcome
   routed to the existing `cannot_connect` key (UI keys unchanged).
   The pair stage additionally carries its own inner
   `asyncio.timeout(PAIR_TIMEOUT)` so a wedged D-Bus Pair cannot eat
   the remaining budget (AC-5).
5. UI error keys are UNCHANGED (`no_response`, `cannot_connect`, …) —
   the verdicts live in the log (the operator reads them from the HA
   UI log view; no new translations this spec).

FR-2.1 (optional, P1): the test-step menu gains a "run the pairing
test" entry point — re-enters `async_step_test` with a
`self._force_pair = True` stash so the pair stage runs even when the
first read SUCCEEDS (for the X3 persistence retest: pair attempt on an
already-bonded device must be a fast no-op, verifying the bond). This
is the UI-only replacement for `bluetoothctl pair`.

### FR-3 — Pairing support in the client (capability ladder) [P0]

`AquaTempBleClient` gains `async def pair(self) -> None` implementing
the ladder, each rung logged (contract §3):

1. **Rung A — bleak method**: if `hasattr(client, "pair")` and
   callable (bleak < 1.0 kept the method) → `await client.pair()`
   wrapped in `asyncio.timeout(const.PAIR_TIMEOUT)`.
2. **Rung B — constructor parameter path (bleak ≥ 1.0)**: tear the
   connection down and re-establish it with pairing requested — via
   `bleak_retry_connector.establish_connection(..., pair=True)` when
   the installed brc accepts the kwarg (capability check with
   `inspect.signature`, logged), else via raw `BleakClient(pair=True)`
   in the standalone fallback. On success the client re-runs its
   normal post-connect setup (enumerate → notify subscribe → write
   char resolve) — the same `connect()` path, now pair-enabled.
3. **Rung C — agent fallback**: if rung A/B fail with an
   agent-signature error (contract §5.2 strings), register a minimal
   `org.bluez.Agent1` (capability `NoInputNoOutput`; handlers
   auto-approve/pass-through) over `dbus_fast` (bleak's own D-Bus
   dependency — no new requirement), then retry rung A. Guarded import;
   non-Linux/backends without dbus_fast log the absence and return the
   original error. The agent object is released after the attempt
   (best-effort, DEBUG on failure).

`const.py` gains `PAIR_TIMEOUT = 25.0` (D-Bus pairing can legitimately
take seconds; bleak's docs advise generous timeouts when pairing) and
`PAIR_ENABLE_PROP = "pair"` plumbing. The client also gains
`best-effort` bond-state logging (contract §3.6): before and after the
attempt, log the D-Bus `Paired` property when reachable via the BlueZ
backend's device path (guarded; DEBUG-only on failure). All pair
outcomes are logged: `pairing: succeeded`, `pairing: failed: <chain>`,
`pairing: skipped: already bonded`, `pairing: unavailable: <reason>`.

### FR-4 — Post-pair retry [P0]

On a successful pair (either rung), the test performs exactly one
more base read (the FR-2 `first-read-retry` stage, re-send bounds
unchanged). Three outcomes, all logged:

- **answer** → the bond gate is PROVEN for this unit: log the
  `security probe: answered after pairing` line (contract §3.4) —
  this line is the case-closed evidence;
- **timeout with the same census** → the gate is not (only) the bond;
  log `security probe: still silent after pairing` — H-APP-GATE
  variant or hypothesis dead (decision matrix, contract §6);
- **error** → log the chain; route to the existing UI keys.

### FR-5 — No new protocol bytes [P0, invariant]

The security probe and all retries send EXACTLY the vendor's own
first-frame constant `63030007002d3c54` (CRC-pinned in the modbus.py
self-test) — built by the same `build_read_holding_registers` call,
differing only in the ATT write type. The frame allowlist of contract
009 §2 is unchanged; no write registers, no query blocks, no
experimental frames enter the config test. AC-2 pins byte-equality.

### FR-6 — The `pair_on_connect` option (evidence-gated fix) [P1]

- `const.py`: `DEFAULT_PAIR_ON_CONNECT = False`.
- Options flow gains the boolean toggle (`pair_on_connect`).
- `AquaTempBleClient.connect(max_attempts=…, pair: bool | None = None)`
  threads pairing into the establish path (brc `pair=True` /
  raw-client `pair=True`); the coordinator passes the option's value.
- The option ships **OFF**: quickstart §3 instructs the operator to
  enable it ONLY after X2/X3 produced the `answered after pairing`
  evidence. With the option OFF, the production connect/read path is
  byte-for-byte today's behavior (FR-7).
- Documentation (options description string) states this contract in
  one sentence (translations: EN only this spec; strings.json).

### FR-7 — Normal-path non-regression [P0]

With `pair_on_connect` OFF and outside the config test: no new I/O, no
new timers, no changed constants, no changed log formats of the
008/009 census. The probe code exists only on the config-test path.
The coordinator's polling read stays single-shot (contract 009 §5
unchanged).

### FR-8 — Coordination with spec 010 (which may land before this spec)

The probe targets `self._write_uuid` — whatever the shipped resolver
picked. If 010 has landed (ff03 first-choice, property-blind), the
probe hits ff03 directly; if not, the current candidate/last-resort
resolution hits ff03 on this unit anyway (production log line
`using write characteristic 0000ff03-…`). No ordering dependency; the
Coder must NOT couple the two diffs (each spec's AC files-only lists
stay disjoint).

### FR-9 — Probe-tool parity (P2, developer contexts)

`tools/session_probe.py` gains a `--phase security` twin (acknowledged
base read + pair attempt + retry, same verdict strings) so a developer
with a terminal reproduces the HA test exactly. Not required for the
operator path; lands last.

## 2. Acceptance criteria

- **AC-1**: `python3 -m pytest tests/` fully green (existing suites
  untouched); new tests under `tests/test_security_probe_011.py` cover:
  the taxonomy classifier (injected `BleakGATTProtocolError`-shaped
  exceptions per verdict class), the pair ladder (rung A present /
  absent via fake clients), the post-pair retry outcomes, the option
  gating (OFF ⇒ no pair call anywhere), and the budget arithmetic.
- **AC-2**: byte-equality pin — the probe's frame equals
  `build_read_holding_registers(7, 45)` and the string constant
  `63030007002d3c54` (test), and `force_response=True` is the ONLY
  difference asserted at the `_write_frame` seam (fake records the
  `response` argument).
- **AC-3**: verdict log lines match the normative strings of contract
  §3 exactly (grep-style assertions like the census tests of 009).
- **AC-4**: with the option OFF, a fake-client walkthrough of the
  config test that SUCCEEDS on the first read runs NO probe, NO pair
  attempt (assert no extra `_write_frame` calls beyond the read
  re-send bounds), and emits zero new INFO lines on the success path.
- **AC-5**: failure-path budget respected: a fake that stalls all
  stages hits `TimeoutError` inside the unchanged `TEST_TIMEOUT` and
  routes to `cannot_connect` (no hang, no new UI key).
- **AC-6**: version bump `0.6.0` → `0.7.0` in `manifest.json`;
  requirements unchanged (bleak>=0.22.0 / brc>=3.5.0 already cover the
  ladder; dbus-fast is a bleak transitive dependency on Linux — the
  fallback import is guarded, never a new requirement line).
- **AC-7**: durable artifacts in English; every new comment cites
  `research 011 §…`; specs 001-010 and their contracts are not
  modified.
- **AC-8**: `python3 -m py_compile` clean on all touched files; the
  standalone import smoke (`import aqua_client` outside HA) still
  passes (dbus_fast import is lazy/guarded).

## 3. Non-goals

- No new request frames of any kind (FR-5; the allowlist discipline
  stands).
- No change to the write characteristic, write mode, session order or
  settle constant (010's territory).
- No Track B (push harvest) work — its promotion is an E5/X-outcome
  decision for a follow-up spec (contract §6).
- No `Trusted` flag management, no D-Bus adapter-level configuration,
  no kernel/btmgmt tuning (Tier D is out of the operator's reach and
  out of scope).
- No translations beyond the one option string (EN) this iteration.