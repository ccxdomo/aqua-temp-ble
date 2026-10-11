# Spec 012 research — the trace, the arithmetic, and the verdict that was always in the log

Date: 2026-10-11
Evidence base: the observed production log line of the d860933 build
(20.9 s run, below); the shipped code @ d860933 (`config_flow.py`,
`aqua_client.py`, `const.py`, `manifest.json`, tests); the previous
production elapsed lines (specs 008/009 research, the 12.4 s and
13.0 s runs); Home Assistant's `system_log` and `logger` documentation
(§14); Python logging semantics (propagation / effective level).

Status legend: **[P]** PROVEN (verifiable from the cited source or by
arithmetic on logged data), **[V]** TO-VERIFY (one named check
decides), **[A]** ASSUMED (reasonable default, cheap to correct),
**[D]** DECISION.

## 0. TL;DR

1. The observed report — "the final `no response` line appeared, and
   **zero** `security probe:` / `pairing:` lines despite debug level" —
   cannot mean the probe did not run **in the code the repo ships**:
   the wiring is airtight (§2) — any entry into `run_security_probe`
   logs an entry line before anything else can happen
   (aqua_client.py:2316-2317), and every `return None` path logs its
   verdict first. An unlogged probe invocation is impossible [P].
2. The duration arithmetic (§3) proves the probe **did run**: the
   20.9 s elapsed decomposes as ≈4.9 s connect (the operator's two
   historical production connects: 4.4 s and 5.0 s [P]) + the 8.0 s
   first-read window + **exactly one more 8.0 s window that only the
   probe stage can consume**, with no room for a pair or retry stage.
   That is the arithmetic fingerprint of the **att-accepted-silent**
   verdict: the acknowledged write was accepted at the ATT layer
   (no error ⇒ no fast-fail) and the engine stayed silent [P
   arithmetic; V on the log file until the retroactive grep].
3. The reason the operator saw nothing: **every probe/pair evidence
   line of spec 011 is emitted at INFO** (contract 011 §3: "normative
   strings; INFO unless stated"), and the operator's reading surface —
   HA **Settings → System → Logs**, the `system_log` ring buffer —
   "only the 50 last **errors and warnings** will be stored inside a
   condensed log" [P — HA docs, §14.1]. INFO lines are invisible in
   that panel **regardless of the logger's debug level**: the `logger`
   integration's level controls what is written to
   `home-assistant.log` (the full file, downloadable from the same
   page), not what the panel stores [P — HA docs, §14.2]. The operator
   read the panel; the probe's verdict was in the file all along.
4. Spec 011's own design assumption was the bug: FR-2.5 states "the
   verdicts live in the log (**the operator reads them from the HA UI
   log view**)" — false for the panel the operator actually uses
   [P — §4's four-quickstart pattern]. The one ambiguity the trace
   cannot close from outside (was the loaded module even d860933?) is
   resolved structurally by the build-marker line (spec 012 FR-2) and
   operationally by the retroactive grep (quickstart §0) [D].
5. Consequence for the diagnosis ladder: att-accepted-silent ⇒ ff03
   value writes are **not** security-gated at the ATT layer for the
   unbonded Pi (the GATT server answered the Write Request) — the
   silent drop happens in the firmware's application layer
   (H-APP-GATE, research 011 §11 rank 2) ⇒ contract 011 §6 row 3
   routes to **E5 (the phone capture)**; pairing is not the fix, and no
   more probing frames may be sent [D — routing, evidence pending the
   retroactive grep].

## 1. The evidence this spec must explain

| # | Fact | Status |
|---|---|---|
| F-1 | The operator runs d860933 (files copied), debug logging configured on `custom_components.aqua_temp_ble`, ran the connection test | [P — premise] |
| F-2 | The final line: `connection test to 08:06:A6:C0:94:1C: no response: AquaTempTimeoutError: no matching response within 8.0s (received 16 frame(s) / 1584 bytes; blocks seen: 1001x12, 1046x4; first t+0.1s, last t+7.6s; none for start 7) at stage first-read after 20.9 s (budget 70 s)` | [P — production log] |
| F-3 | The operator reports ZERO `security probe:` and ZERO `pairing:` lines | [P — the report; see §4 for what surface it was read from] |
| F-4 | The message format matches `_async_test`'s `except AquaTempTimeoutError` handler byte-for-byte (config_flow.py:555-563) | [P — code] |
| F-5 | Historical production elapsed lines on the same unit/Pi: `… at stage first-read after 12.4 s (budget 70 s)` (spec 008 build) and `… after 13.0 s (budget 70 s)` (spec 009 build) | [P — specs 008 research §1, 009 research §1] |
| F-6 | Neither of those builds had a probe stage; their elapsed = connect-overhead + the single 8.0 s read window ⇒ connect-overhead was **4.4 s** and **5.0 s** | [P — arithmetic on F-5] |
| F-7 | Across four quickstarts (007 §1, 009 §0, 010 §0, 011 Phase 1) the operator has **never** delivered a single INFO-level line (`connect summary`, `GATT enumeration`, `using write characteristic`, `idle window…`); the E4' greps of quickstart 011 §0 are still pending | [P — the specs' own state] |
| F-8 | Every production line ever pasted by the operator in this project (12.4 s, 13.0 s, the E-1 census line, the 20.9 s line) is a **WARNING** emitted by `_async_test`'s failure handlers | [P — the research files] |

## 2. The trace (from the observed WARNING backwards, code @ d860933)

Normative pre-fact: `AquaTempTimeoutError(AquaTempError)` (aqua_client.py:79)
is NOT a subclass of builtin `TimeoutError`, and NOT of
`AquaTempConnectionError` (:65) — the handler ordering of `_async_test`
routes it exactly once, to its own handler (config_flow.py:555) [P].

```
config_flow.py:472   t_test = time.monotonic()            ← the elapsed clock starts
config_flow.py:473   self._stage = "connect"
config_flow.py:488   await client.connect(max_attempts=2)  ← resolve+establish+setup
                     (INFO lines: "connecting to … 2 attempt(s)…" aqua_client.py:624,
                      "connect summary … total X s" :763, "Connected to" :774)
config_flow.py:489   self._stage = "first-read"
config_flow.py:497   regs = await client.read_registers(7, 45,
                         resend_every=2.0, resend_max=3)
   aqua_client.py:2167 read_registers → :1647 _request:
     pending armed; idle summary INFO (:1859/:1867); TX DEBUG;
     resend loop task (:1788; INFO re-send lines :1821 at t+2/4/6 —
     inside the window, no extra time);
     window: async with asyncio.timeout(8.0) (:1735-1736).
   The unit pushes 16 block frames (1001×12, 1046×4) → census INFO
   lines (:2075); none matches start 7 → at t+8.0:
   aqua_client.py:1769 raise AquaTempTimeoutError(
       "no matching response within 8.0s (received 16 frame(s) / 1584
        bytes; blocks seen: 1001x12, 1046x4; first t+0.1s, last
        t+7.6s; none for start 7)")        ← the census text of F-2 [P]
config_flow.py:501   except AquaTempTimeoutError as first_timeout:   ← catches
config_flow.py:510   regs = await run_security_probe(client, …)      ← THE ONLY CALL SITE
   aqua_client.py:2316 stage("security-probe")  → config_flow.py:202-208
                    self._stage = "security-probe"
   aqua_client.py:2317 _LOGGER.info("security probe: re-sending base read
                    as acknowledged write (response=True) on <uuid> to <addr>")
                    ← THE ENTRY LINE. Nothing between function entry
                      (:2273) and this call can raise (getattr-with-
                      defaults, an attribute set, lazy %-args) [P].
   aqua_client.py:2326-2331 probe read: read_registers(7, 45, force_response=True)
     → _request → _write_frame (:1625) writes with response=True
       (ATT_WRITE_REQ on ff03):
       • ATT error   → AquaTempConnectionError → :2337 verdict INFO line
                       → non-security verdicts: :2374 return None  [FAST — ms]
                       → security verdict: :2382 stage("pair") …
       • Modbus exception frame → :2354 INFO line → :2360 return None  [FAST]
       • answered    → :2365 INFO line → :2370 return regs             [decisive]
       • write accepted, engine silent → the FULL 8.0 s window runs
         → AquaTempTimeoutError → :2342 _LOGGER.info(
             "security probe: verdict att-accepted-silent (ack ok, no
              Modbus answer within 8.0s; census: …)") → :2348 return None
                                                                        [8.0 s]
config_flow.py:516-517  if regs is None: raise first_timeout
config_flow.py:518-519  finally: await client.disconnect()   ← runs after the probe
config_flow.py:521      self._stage = "disconnect"          ← only on success paths
config_flow.py:555-563   except AquaTempTimeoutError as exc:
                         self._test_error = "no_response"
                         _LOGGER.warning("connection test to %s: no response: %s "
                           "at stage first-read after %.1f s (budget %.0f s)", …,
                           time.monotonic() - t_test, …)
                         ← THE OBSERVED LINE. "at stage first-read" is a
                           LITERAL in the format string — it does NOT read
                           self._stage [P — code]. The elapsed is evaluated
                           HERE, at handler time — after the probe (if any)
                           and after the finally's disconnect [P].
```

**All `return None` paths of `run_security_probe` and their log lines**
(every one logs BEFORE returning; none is silent) [P — code]:

| Path | Log line | Latency after entry |
|---|---|---|
| att-accepted-silent (:2348) | `security probe: verdict att-accepted-silent (…)` (:2342, INFO) | 8.0 s |
| Modbus-exception-frame (:2360) | `security probe: acknowledged write answered by a Modbus exception frame …` (:2354, INFO) | fast |
| non-security verdict (:2374) | `security probe: verdict <v>: <chain>` (:2337, INFO) | fast |
| pair failed/wedged/unavailable (:2399) | pair-budget WARNING (:2387) and/or `pairing: failed: …` (:2393, INFO) + ladder lines | pair-dependent |
| still-silent-after-pairing (:2415) | `security probe: still silent after pairing (census: …)` (:2412, INFO) | pair + 8.0 s |

**Corollary [P]:** any invocation of `run_security_probe` leaves at
least the entry line (:2317) in the log **file**. Zero probe lines in
the **file** ⇒ never invoked. Zero probe lines in the **panel** ⇒
possible while invoked — the panel stores only WARNING+ (§4).

## 3. The arithmetic of 20.9 s (the discriminator)

Fixed costs (code + constants) [P]:
- first-read window: `const.REQUEST_TIMEOUT = 8.0` (const.py:53); the
  re-sends (t+2/4/6) run inside it, adding no time; the census text of
  F-2 ("within 8.0s", "last t+7.6s") confirms expiry at 8.0 [P].
- probe window: the same 8.0 s, single write, no re-send.
- retry window: 8.0 s. Pair stage: 0 → `PAIR_TIMEOUT = 25.0` s.
- disconnect: small (~0.1-0.5 s), inside the elapsed.
- the elapsed is measured at handler time (config_flow.py:563,
  `time.monotonic() - t_test`) — it INCLUDES the probe, any pair, any
  retry, and the disconnect [P — §2].

Let C = entry + connect + setup and D = disconnect.
`20.9 = C + D + 8.0 (first read) + P (probe stage) + R (retry) + pair`.

| World | Decomposition | C + D | Verdict |
|---|---|---|---|
| **(a) probe ran, att-accepted-silent** | 8.0 + 8.0 | **4.9 s** | inside the operator's historical 4.4-5.0 s **exactly** [F-6] |
| (b) probe never entered (pre-011 module loaded) | 8.0 | **12.9 s** | 2.6-2.9× every previously observed connect; unexplained outlier |
| (c) probe fast-failed (ATT error), no pair | 8.0 + ~0 | 12.9 s | same outlier as (b) — and a verdict line would exist in the file |
| (d) security verdict → pair (≤25 s) + retry | 8.0 + ~0 + pair + 8.0 | ≤ -12 s | **impossible** — exceeds 20.9 even with pair ≈ 0 |
| (e) probe answered (success path) | — | — | produces the confirm step, NOT the observed `no response` line |

**Reading [P arithmetic]:** only world (a) fits 20.9 s with a connect
consistent with the operator's own history. Worlds (c)/(d) are
excluded by the arithmetic; (b) is the sole surviving alternative and
requires a connect anomaly with zero corroboration (F-7: no
`connect summary`, no brc retry lines were ever pasted). Under world
(a), the elapsed **pins the verdict**: the probe consumed one full
8.0 s window and no pair stage ⇒ the write was accepted (no ATT
fast-fail) and the engine stayed silent ⇒ **att-accepted-silent**.
Its meaning: `write_gatt_char(response=True)` returned normally ⇒ the
unit's GATT server answered the Write Request (`ATT_WRITE_RSP`) ⇒
ff03 value writes are **not** encrypted-link-gated for the unbonded
Pi; the FC03 request bytes were accepted on the wire and dropped by
the firmware's application layer — H-APP-GATE (research 011 §11 rank
2), i.e. contract 011 §6 row 3: **E5 priority, no more probing
frames, pairing is not the indicated fix** [D — routing; V — the grep
below turns the predicted verdict into a pasted one].

The two worlds are separable **retroactively, for free**, from the
log file of the very run already on disk [D — quickstart §0]:

| Grep of the downloaded full log (`home-assistant.log`) | World (a) | World (b) |
|---|---|---|
| `security probe` | entry line + `verdict att-accepted-silent …` lines present | absent |
| `connect summary` | `total ≈ 4.9 s` | `total ≈ 12.9 s` |
| count of `block push observed` | ≈ 32 (the probe window censuses ~16 more pushes) | 16 |

## 4. The visibility mechanism (why "no probe lines despite debug")

- HA's `system_log` — the ring buffer behind **Settings → System →
  Logs**, the panel the operator reads — "**only the 50 last errors and
  warnings will be stored inside a condensed log**"; its event `level`
  is "Either `WARNING` or `ERROR`" [P — §14.1]. INFO records never
  enter it, at any logger configuration.
- The `logger` integration's per-logger level (e.g.
  `custom_components.aqua_temp_ble: debug`) controls what is **written
  to `home-assistant.log`** — the full file, downloadable from the same
  page — not what the panel stores [P — §14.2]. "Debug level" and
  "the panel shows debug lines" are different claims; only the first is
  true.
- Spec 011 put its entire evidence grammar at INFO (contract 011 §3
  header: "normative strings; INFO unless stated") while FR-2.5
  assumed "the operator reads them from the HA UI log view" [P — the
  011 files]. The panel IS an HA UI log view; it shows none of it.
- The operator's behavior corroborates the mechanism [P — F-7/F-8]:
  four quickstarts asked for INFO lines; none was ever delivered; the
  E4' greps (quickstart 011 §0) are still pending; every line ever
  pasted is a WARNING from `_async_test`'s handlers — the only
  WARNING-level lines the config-test path emits. Under world (a) the
  paste (F-2) is **exactly** the panel's full content for that test:
  one WARNING line. [P — fit; A — that the panel is the surface used]
- The census verbosity discipline was right (16 pushes per window must
  not be WARNINGs — 008/009 kept them INFO by design); applying the
  same default to **decisive-once** lines was the error. The test suite
  could not catch it: both caps (`_LogCap` :276, `_FlowLogCap` :1207)
  attach handlers at DEBUG and store `record.getMessage()` only — the
  suite asserts strings, never levels (§6, Q6) [P — code].

## 5. The stale-module alternative (world (b), documented and closed)

Mechanism [P — Python semantics; A — HA timing]: HA imports a custom
component's `config_flow` platform once per process start; Python's
`sys.modules` cache then serves the old module for the process's
lifetime regardless of later file changes. Copying d860933's files onto
a running HA does **not** reload them; a full HA restart does. The
pre-011 build (0b16e0a) routes the first-read timeout **directly** to
the outer handler — its `except AquaTempTimeoutError` block
(git show 0b16e0a:config_flow.py) produces a byte-identical final
line and, of course, zero probe lines.

What fits: the literal reading of F-3. What does not: the 12.9 s
connect outlier (§3), and the operator's update history — every
previous build's new behavior DID show up in pastes (the census format
changed between the 008 and 009 runs and the pastes tracked it [P]),
so the update procedure has worked before.

Residual weight: LOW but not zero — the trace cannot see the operator's
HA process. Closure: the retroactive grep (§3 table) decides today; the
build-marker line (spec FR-2) makes the running build identifiable in
the panel forever after [D].

## 6. Answers to the six questions

1. **The trace / the first `return None` / silent-None conditions.**
   From `except AquaTempTimeoutError` (config_flow.py:501) the ONLY
   call is `run_security_probe` (:510 → aqua_client.py:2273). Its first
   observable action is the stage stamp (:2316) then the entry log
   (:2317) — nothing between can raise. The first possible
   `return None` is the att-accepted-silent branch (:2342-2348),
   ~8.0 s in. ALL five `return None` paths log first (§2 table) —
   **there is no condition anywhere in the helper that returns None
   without logging** [P]. Therefore: zero probe lines in the FILE ⇒
   never invoked (world (b)); zero probe lines in the PANEL ⇒ proves
   nothing (world (a) is fully compatible — INFO is panel-invisible).
2. **The duration.** First read: exactly 8.0 s (window; re-sends
   inside add nothing). Probe: 8.0 s if the write is accepted and the
   engine is silent; ms on an ATT error; fast on a Modbus exception
   frame. Retry: 8.0 s. Pair: 0-25 s. 20.9 s is compatible with (a)
   probe-ran-and-failed-silently (connect 4.9 s = the operator's
   historical 4.4-5.0 s [F-6]) — and with (b) never-entered ONLY via
   an anomalous 12.9 s connect. (c) probe-fast-fail variants either
   mis-fit the elapsed or contradict the arithmetic as shown in §3's
   table; the success variants contradict the observed `no response`
   key. **The arithmetic says the probe ran and returned
   att-accepted-silent** [P arithmetic; V file-grep].
3. **The stage label.** The observed "at stage first-read" is a
   **hardcoded literal** in the AquaTempTimeoutError handler's format
   string (config_flow.py:559-563) — that handler never reads
   `self._stage`. The dynamic-stage handlers are
   `except AquaTempConnectionError` (:546) and the budget
   `except TimeoutError` (:592). So the label proves neither
   non-execution nor execution: if the probe ran, `self._stage` was
   "security-probe" (:2316 via :202-208) at `raise first_timeout`
   time, and the message still says "first-read" — the label names the
   failure's exception class site (the base read), per contract 007
   §3.3. `self._stage = "disconnect"` (:521) is only reached on
   success paths. The stage is READ (if at all) by the other handlers,
   at handler time; the observed line's stage is a constant [P].
4. **Where the 20.9 s is measured.** The handler that produced the
   line is INSIDE `_async_test` (config_flow.py:555-563), not
   elsewhere. `t_test` starts at :472 (function entry); the elapsed
   `time.monotonic() - t_test` is evaluated at :563 — AFTER the probe
   (if any), after the `finally`'s disconnect (:519). So the reading is
   correct: **the 20.9 s includes the probe** — while the exception's
   MESSAGE text ("within 8.0s …") is frozen from the first read
   (the re-raised `first_timeout`, config_flow.py:517). This is why
   the arithmetic works at all [P].
5. **Another exception?** If `run_security_probe` raised a non-timeout
   exception, the `finally` disconnects (:519) and the outer handlers
   route: AquaTempConnectionError → `connection test to %s failed: %s
   at stage %s …` (:546, cannot_connect); AquaTempResponseError →
   `bad response` (:571); a bare AquaTempError (e.g. "another request
   is already in flight") → `except Exception` →
   `_LOGGER.exception("unexpected error during connection test at
   stage unknown …")` + traceback (:608). None of these formats matches
   the observed line. The global handler's message is visibly
   different ("unexpected error … at stage unknown"), and the observed
   line matches the AquaTempTimeoutError handler byte-for-byte —
   **confirmed: that handler produced it**, via `raise first_timeout`
   (:517) — the probe's None return — or, in world (b), the old
   build's direct propagation [P].
6. **The test coverage.** YES — the call-through IS covered:
   `tests/test_security_probe_011.py`'s flow tests
   (`test_flow_case_closed_probe_pair_retry_answered` :1367,
   `test_flow_accepted_silent_verdict_routes_no_response` :1467,
   `test_flow_still_silent_after_pairing…` :1416, …) drive the REAL
   `async_step_device → async_step_test → _async_test` with
   `FakeProbeClient` scripted to raise AquaTempTimeoutError on read
   #1, then assert the probe's read actually happened
   (`client.reads[1]["force_response"] is True` — only
   `run_security_probe` issues that call), the pair calls, the retry
   read, and the log lines via `_flow_logs` (captures BOTH loggers).
   **The hole:** both caps store `record.getMessage()` only (:276-283,
   :1207-1213) — the suite asserts strings, never LEVELS, and nothing
   anywhere asserts the operator-visible contract ("a human reading
   Settings → System → Logs would see the verdict"). Contract 011
   §3's "INFO unless stated" therefore passed green while being
   operator-invisible. That is the gap that let this through, plus the
   absence of any build identity in the log (the stale-module world
   was untestable-by-construction) [P — code; D — FR-2/FR-3].

## 7. The fix rationale (why these two changes, and no others)

- **INFO → WARNING for the evidence grammar only** (FR-1): the lines
  are decisive-once (≤ ~6 per test), the panel shows WARNING+ [§4],
  the project's own convention already puts test-level failure lines
  at WARNING (contract 007 §3.1), and the strings stay byte-identical
  so every grep and every 011 assertion keeps working. Census and
  per-connect diagnostics stay INFO (16-per-window verbosity must not
  become WARNING noise — the 008/009 discipline stands).
- **The build marker** (FR-2): the one fact the trace could not
  determine from outside — WHICH build produced a given log — becomes
  self-evident in the panel, permanently closing world (b) as a
  recurring confusion mode (updates that never loaded, version-number
  ambiguity, stale modules after file copies).
- Nothing else: no new frames (contract 011 §6 row 3 forbids more
  probing), no pairing changes (the predicted verdict does not route
  to pairing), no UI-key or format changes (grep stability).

## 8. Unproven ledger (what this spec does NOT claim)

- That the retroactive grep finds the probe lines in the operator's
  file — [V], quickstart §0 decides in minutes; predicted: entry +
  att-accepted-silent verdict present, `connect summary total ≈ 4.9`,
  ~32 census lines.
- The verdict VALUE — att-accepted-silent is pinned by arithmetic
  under world (a) [V until the grep]; if the grep instead shows world
  (b), the 012 build's re-run (quickstart §1-2) produces the verdict
  line visibly at WARNING either way.
- That the operator's debug configuration is/was effective — moot
  after FR-1: the evidence rides at WARNING, visible at any level.
- The exact HA import timing of config-flow modules per release
  (Python's `sys.modules` caching is [P]; which restart the operator
  did is [A]) — FR-2 makes the question operationally irrelevant.
- What the app-layer gate keys on (bond identity? app state?) — the
  E5 phone capture decides (contract 011 §6 row 3); out of scope here.
- That att-accepted-silent is "H-APP-GATE" rather than "engine
  state" — the routing is identical for both (E5); the distinction is
  E5's to make, not this spec's.

## 9. References

1. Home Assistant — `system_log` integration ("only the 50 last errors
   and warnings will be stored inside a condensed log"; event level
   "Either WARNING or ERROR") —
   https://www.home-assistant.io/integrations/system_log/
2. Home Assistant — `logger` integration (per-logger levels route to
   `home-assistant.log`; the `custom_components.<x>: debug` pattern) —
   https://www.home-assistant.io/integrations/logger/
3. The observed production line and the d860933 code (this repo).
4. Previous production elapsed lines: specs/008-first-read-decode/
   research.md §1 (12.4 s), specs/009-base-block-request/research.md
   §1 (13.0 s) — the connect-overhead baselines 4.4 s / 5.0 s.
5. Contract 011 §3 (the INFO grammar) and spec 011 FR-2.5 (the false
   "HA UI log view" assumption) — specs/011-android-vs-linux-ble/.
6. Contract 011 §6 (the decision matrix, row 3) and research 011 §11
   (H-APP-GATE, rank 2) — the routing this verdict triggers.
7. Python logging semantics — effective level, propagation,
   `sys.modules` import caching (docs.python.org).
8. specs/011 tasks.md "EXCEPTION" note vs the landed manifest
   (0.7.0 IS in d860933) — the tasks note is stale; the marker and
   manifest now move together (FR-3c pins equality).