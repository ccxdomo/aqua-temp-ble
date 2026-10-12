# Contract 011 — security-probe & pairing rules (normative)

These rules bind every implementation of spec 011 and every follow-up
that touches the probe/pair path. Companion contracts: 009 §2 (frame
allowlist — unchanged), 008-B (reassembly/matching — reused by the
probe), 007 §3 (stage instrumentation conventions — extended here).
Proof base: research 011 (all [P]/[V]/[A]/[D] tags there).

## §1 Scope & invariants

1. The probe/pair machinery lives ONLY in:
   - the config-flow connection test (`config_flow._async_test`),
   - `AquaTempBleClient` transport seams (`pair`, `force_response`),
   - the `pair_on_connect` option threading,
   - (parity, last) `tools/session_probe.py --phase security`.
2. **Frame allowlist unchanged** (contract 009 §2): the only bytes the
   probe path may put on the wire are the vendor's base read
   `63030007002d3c54` — built by `build_read_holding_registers`, never
   a hand-written constant. No FC10, no query blocks, nothing else.
3. **Session order unchanged** (contract 009 §1): connect → enumerate
   → subscribe → settle 2 s → first request. The probe stages happen
   strictly AFTER the first read's timeout, on the same live session.
4. **Single-flight unchanged**: the probe is a `_request` like any
   other — one pending request; the 008/009 match spec, census, and
   timeout semantics apply verbatim to it.
5. `pair()` is NEVER called from a poll loop; only from the config
   test stages or, with the option ON, once per
   `establish_connection`.

## §2 The probe sequence (state machine)

```
connect (unchanged)
→ first-read  (unchanged incl. 009 re-send; success ⇒ done, NO probe)
→ on AquaTempTimeoutError:
   security-probe:  read_registers(7, 45, force_response=True)
                    (same match spec/census; REQUEST_TIMEOUT window)
   ├─ raises AquaTempConnectionError("acknowledged write failed: …")
   │    → classify (§5.1) → verdict line
   │        ├─ att-error-security → pair ladder (§3.2)
   │        └─ other verdicts      → log + route; NO pair
   ├─ answers (Modbus frame completes the request) ⇒ success (the
   │    acknowledged op woke the engine — itself decisive evidence,
   │    §6 row 4)
   └─ times out silently ⇒ verdict att-accepted-silent (§5.1 row 4)
→ first-read-retry: read_registers(7, 45)   [only after a pair attempt
   that returned success/skipped-already-bonded; exactly ONE retry]
→ disconnect (unchanged; always in the existing finally)
```

Stage labels for the 007-style test-level log lines: `security-probe`,
`pair`, `first-read-retry` (the stage is stamped BEFORE each stage
runs; failure lines report the running stage, never a later one).

## §3 Log grammar (normative strings; INFO unless stated)

1. Probe entry:
   `security probe: re-sending base read as acknowledged write (response=True) on <uuid> to <addr>`
2. Verdicts (exactly one, from §5.1):
   `security probe: verdict att-error-security: <exc chain>`
   `security probe: verdict att-error-permission: <exc chain>`
   `security probe: verdict att-error-handle: <exc chain>`
   `security probe: verdict att-accepted-silent (ack ok, no Modbus answer within <t>s; census: <008/009 timeout line>)`
   `security probe: verdict local-failure: <exc chain>`
3. Pairing:
   `pairing: attempting via <rung> (bleak=<version>, brc=<version or absent>)`
   `pairing: bond state before: <paired|unknown>`
   `pairing: succeeded` / `pairing: skipped: already bonded`
   `pairing: failed: <exc chain>` (chain must contain the D-Bus error
   text verbatim — the §5.2 signatures are read from it)
   `pairing: unavailable: <reason>` (no rung available on this backend)
   `pairing: bond state after: <paired|unknown>` (best-effort)
4. Retry outcomes:
   `security probe: answered after pairing (<n> attempt of the window)` — the case-closed line
   `security probe: still silent after pairing (census: <timeout line>)`
5. Option path (production, option ON, DEBUG):
   `connect with pairing requested (option pair_on_connect)`

Grep stability: the census/format lines of 008/009 keep their exact
formats (AC-4 of spec 008/009 regression suites); every new line above
is additive.

## §4 Budget arithmetic (the unchanged TEST_TIMEOUT = 70.0 s)

```
TYPICAL path (production-observed connect cost, spec 007 summaries):
connect (resolve+link+GATT)      ≈ 3.0 s
notify+write-char+settle           3.0 s   (≈1 + SETTLE 2.0)
first-read window                  8.0 s   (const.REQUEST_TIMEOUT, re-send inside)
security-probe window             ≤8.0 s   (same REQUEST_TIMEOUT; the ATT error
                                            itself returns in ~ms and fails the
                                            write stage early)
pair ladder                       ≤25.0 s   (const.PAIR_TIMEOUT; includes rung
                                            re-connect when rung B is taken)
first-read-retry window            8.0 s
───────────────────────────────────
typical total                     ≈55.0 s  ⇒ fits 70.0 s [P arithmetic]

PATHOLOGICAL path (two full 20 s connect attempts, research 007 §4):
the connect stages alone consume ≤43 s, leaving <27 s — the probe
stages then hit the flow-level timeout mid-way. That is an ACCEPTED,
LOGGED outcome (FR-2.4 / spec AC-5): every stage runs INSIDE the
flow's asyncio.timeout(TEST_TIMEOUT); expiry mid-probe/mid-pair
routes to the existing cannot_connect key. The probe budget is
therefore "best effort within the remainder", never a hang.
```

- Every stage runs inside the flow-level
  `asyncio.timeout(TEST_TIMEOUT)`; a budget expiry mid-probe or
  mid-pair routes to the existing `cannot_connect` key (unchanged) —
  the 70 s envelope is NOT raised for this spec.
- The `pair` stage is additionally wrapped in its own
  `asyncio.timeout(PAIR_TIMEOUT)` so a wedged D-Bus Pair() cannot eat
  the whole remaining budget before the retry stage.

## §5 Error taxonomy

### §5.1 Client-visible write failures → verdict

| Exception text (chain, case-insensitive match) | ATT meaning | Verdict |
|---|---|---|
| `insufficient authentication` (ATT 0x05) / `insufficient encryption` (0x0F) — bleak ≥3 raises `BleakGATTProtocolError` carrying the ATT error; older bleak/BlueZ map to error strings like `Not paired`, `insufficient auth` | link not encrypted/authenticated enough for this attribute | `att-error-security` |
| `not paired` (BlueZ status 0x06, btmon table: "Operation requires an existing bond") | BlueZ-side bond requirement surfaced | `att-error-security` |
| `write not permitted` (0x03) / `not permitted` / `write not allowed` | property/permission gate (char not writable at ATT layer) | `att-error-permission` |
| `invalid handle` (0x01) / `invalid offset` (0x04) | stale cache / wrong handle (research §6) | `att-error-handle` |
| `insufficient authorization` (0x08) | authorization (out of scope) | `att-error-permission` |
| anything else raised by the acknowledged write | local stack | `local-failure` |
| no exception, request times out | server accepted the write, engine stayed silent | `att-accepted-silent` |

The classifier is a pure function (unit-tested); unknown strings fall
to `local-failure` with the chain logged verbatim — never guessed.

### §5.2 Pair failure signatures (agent problem, research 011 §10.2)

| D-Bus / bleak chain text | Meaning | Action |
|---|---|---|
| `no … agent` / `org.bluez.Error.Failed` + agent mentions / bleak #1434 pattern | no pairing agent registered | rung C (FR-3): register minimal NoInputNoOutput agent, retry rung A |
| `AuthenticationFailed` / `AuthenticationRejected` / `AuthenticationTimeout` | pairing started, peripheral refused/lost | log + quickstart §2 choreography (the "bLE" window); retest guidance line `pairing: failed: … retry while the panel shows bLE (hold 5 s)` |
| `AlreadyExists` | already bonded | `pairing: skipped: already bonded` → proceed to retry read |
| `ConnectionAttemptFailed` | single-connection rule / out of range (spec 006 §9) | log + existing `cannot_connect` guidance |

## §6 Decision matrix (binding follow-ups)

| Evidence (log line) | Meaning | Follow-up MUST |
|---|---|---|
| `verdict att-error-security` + `answered after pairing` | **H-BOND proven** (research §11 rank 1) | promote: operator enables `pair_on_connect` (quickstart §3); X3 persistence retest; next spec makes the pair-ensure step permanent in the coordinator |
| `verdict att-error-security` + `still silent after pairing` | bond not sufficient at GATT layer — H-APP-GATE (rank 2) or pairing didn't take | E5 capture (quickstart §4) decides; C2 nRF differential optional |
| `verdict att-accepted-silent` | GATT open, engine ignores — app-layer gate or engine state | E5 priority; do NOT add more probing frames (§1.2 stands) |
| `verdict att-error-permission` / `att-error-handle` | ff03 not writable at ATT layer / handle map wrong — contradicts the app-parity premise (F2) unless our resolution diverged | cross-check the `using write characteristic` line vs the E4' enumeration; re-open research 010 §6.3 column |
| `verdict local-failure` | bleak/BlueZ stack-level problem | log chain → Tier D (btmon) if ever SSH exists; E5 baseline still useful |
| first-read SUCCEEDS on any re-send attempt | C2/C5 (timing) — spec 009 §11 row 2 | spec 009's own matrix applies (polling promotion), 011 stops |

## §7 Safety invariants

- The probe writes are the vendor's read-only FC03 request — a server
  that accepts them changes nothing (Modbus FC03 is a read); a server
  that refuses produces an ATT error and nothing else (research 011
  §7). No register is ever written by this spec's machinery.
- `pair()` may change peripheral state (a bond). That is its purpose;
  it runs only on the config-test path or behind the option, is
  logged (§3), and the ladder never loops (each rung at most once per
  test; rung C retries rung A exactly once).
- The minimal agent (rung C) auto-approves `NoInputNoOutput` Just
  Works flows only; it does NOT attempt numeric-comparison MITM
  (capability is fixed; a MITM-demanding peripheral would fail —
  correct, out of scope).
- On any unexpected exception the test stores the existing UI keys and
  logs the chain — the flow never shows a new/untranslated error
  (FR-2.5).

## §8 What this contract does NOT decide (deferred, evidence-gated)

- Whether `Trusted` should be set after bonding (BlueZ
  `Disconnect`-on-non-trusted-LE quirk, research 011 §14.3) — deferred
  until X3 shows reconnect problems.
- Whether the coordinator needs a per-session security-ensure step
  (write-with-response warm-up) when the unit does not send SMP
  Security Requests — X3's outcome decides (research §3.6).
- Track B promotion — an E5 outcome, spec 009 §11 row 6.