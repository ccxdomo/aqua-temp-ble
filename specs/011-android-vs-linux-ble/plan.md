# Spec 011 plan — the experiment ladder and the implementation order

Hard constraint from the operator (binding for every step below): **no
terminal on the HA Pi.** Every experiment is therefore classified by
what it costs to run:

- **Tier 0** — read the logs you already have (HA UI only, zero new runs);
- **Tier B** — push code (the integration itself), run from the HA UI;
- **Tier C** — the operator's phone (UI apps only; no terminal);
- **Tier D** — requires SSH on the Pi — **documented for completeness
  only; NOT part of the operator path.**

Ordering principle: cheapest decisive evidence first; each step's
outcome is bound to a follow-up by the decision matrix (contract §6).

## Phase 1 — Tier 0: the greps you already own (do first, free)

1. **E4' properties grep** (inherited from spec 010 quickstart §0 —
   not duplicated here): from the HA log of the two past production
   runs, extract `GATT enumeration`, `using write characteristic`,
   `last-resort`, and `idle window before first request` lines.
   Decides: ff03's advertised properties (E4), push onset vs TX (E2 —
   research 009 §1.1).
   - ff03 advertises a write property ⇒ the app's ATT-level writes are
     legal on this unit; the silence stays transport-level (research
     011 §3.4 row F1).
   - ff03 advertises none ⇒ the app is property-blind and would ALSO be
     refused at the Android stack — tension with F1 (the app visibly
     works) — flag it; X1 becomes even more decisive.
2. Result goes into the X1 run's report (quickstart §0 report
   template).

## Phase 2 — Tier B: the diagnostics build (code push + HA UI run)

The single code iteration that carries X1/X2/X3 (spec.md FR-1..FR-4):

1. **X1 — security probe** (built once, runs automatically):
   run the connection test once from the HA config flow. On the
   first-read timeout the test sends the acknowledged probe and logs
   the verdict (contract §5.1). Paste the `security probe:` lines.
   | Verdict | Decides |
   |---|---|
   | `att-error-security` (0x05/0x0F / "not paired") | H-BOND rank 1 — proceed to X2 in the same run (automatic) |
   | `att-error-permission` / `att-error-handle` | resolver/cache column — stop, cross-check Phase 1 lines |
   | `att-accepted-silent` | H-APP-GATE rank 2 — skip X2's value, go E5 |
   | `local-failure` | stack-level — paste chain, decide later |
2. **X2 — pairing attempt** (automatic after an `att-error-security`
   verdict): the ladder runs (contract §3). If it fails with
   AuthenticationFailed/Timeout → the unit likely wants its pairing
   mode: **the operator re-runs the test while holding the panel button
   5 s ("bLE" blinking)** — the quickstart §2 choreography. Success
   line: `security probe: answered after pairing` = **case closed**.
3. **X3 — persistence retest** (one more UI run, no bLE, option still
   OFF): proves the bond survived the reconnect AND the link
   re-encrypts without a new pair (research §3.6 last row). If X3
   fails while X2 answered: the firmware does not re-encrypt on
   reconnect — the `pair_on_connect` option (FR-6) is the permanent
   shape; enable it and retest (quickstart §3).
4. **Promotion** (only after X2/X3 evidence): enable
   `pair_on_connect`, one normal poll cycle, done.

## Phase 3 — Tier C: the phone (no terminal)

1. **E5 — Android HCI snoop of one vendor-app session** (protocol:
   spec 009 quickstart §3; extended reading list in quickstart 011
   §4). The security-specific reads:
   - `LE Pairing …` SMP PDUs or an early `Encryption Change` event ⇒
     the phone's session is encrypted — H-BOND's vendor-side proof;
   - the app's Write Command to ff03 and the FC03 notification answer
     (or their absence);
   - whether the app re-sends (E8) and the negotiated MTU/conn params
     (baseline only — excluded causes, research §5/§8).
2. **C2 — nRF Connect differential** (optional but powerful):
   from a phone that is NOT the bonded daily phone (or after removing
   the bond — risky, skip if unsure), vendor app killed, connect to
   `BlueNRG-C0941C`, write `63030007002D3C54` to ff03:
   - silence ⇒ a BOND-FREE ANDROID reproduces our failure → the gate
     is the link state, cross-platform [P];
   - answered ⇒ bond-free access works on Android → H-BOND weakened
     sharply; the difference is BlueZ-specific (research §11 ranks 4-5
     gain weight) → E5 + Tier D escalate.

## Phase 4 — Tier D: terminal truth (NOT for this operator — completeness only)

`bluetoothctl info` (Paired flag), `bluetoothctl pair/trust`,
`btmgmt info` (auto-secure), `btmon` (the on-air Write Command and
any `Security Request`/`Encryption Change` events — the complete
mechanism in one screen), `journalctl -u bluetooth*` ("SMP security
requested but not available"), `/var/lib/bluetooth/<adapter>/<addr>/`
(bond storage). If SSH ever becomes available, `btmon` replaces every
hypothesis above with direct observation; until then the code probes
are the truth source.

## Implementation plan (Coder order — details in tasks.md)

| Block | Content | Files |
|---|---|---|
| 1 | constants: `PAIR_TIMEOUT`, `DEFAULT_PAIR_ON_CONNECT`, version bump | `const.py`, `manifest.json` |
| 2 | client seams: `force_response` plumbing, `AquaTempConnectionError` chain for acknowledged writes, verdict classifier (pure) | `aqua_client.py` |
| 3 | client pairing: ladder rungs A/B/C + bond-state logging + `connect(pair=…)` threading | `aqua_client.py` |
| 4 | config flow: probe/pair/retry stages, stage labels, budget, FR-2.1 force-pair re-entry | `config_flow.py` |
| 5 | options: `pair_on_connect` toggle + coordinator threading | `config_flow.py`, `coordinator.py` |
| 6 | tests: taxonomy/ladder/retry/gating/budget/byte-equality + regressions | `tests/test_security_probe_011.py` |
| 7 | tool parity: `--phase security` | `tools/session_probe.py` |
| 8 | docs: README next-steps pointer, quickstart links | `README.md` |

Landing discipline: blocks 1-2-4 + tests are the minimal X1 build
(X1 alone is shippable and decisive); block 3 completes X2/X3; block
5 is the promotion lever. Blocks 7-8 close.

## Success criteria for this spec (what closes it)

- The operator's pasted log contains one of: `answered after pairing`
  (fix promoted), or a verdict that routes the matrix to E5/Track B
  (contract §6) — silence-forever is not an acceptable end state.
- No regression in the census/first-read behavior (AC-4, spec.md).