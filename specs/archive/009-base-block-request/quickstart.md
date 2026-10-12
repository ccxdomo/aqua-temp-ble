# Spec 009 quickstart — the evidence-capture protocol

For the pool owner. Spec 009 ships instrumentation, not a fix promise:
the goal of this runbook is to turn the NEXT runs into the data that
decides the base-read strategy (research §7/§10/§11). Steps are ordered
by cost — start at §0, it needs NO new run. [P]/[V] tags per the repo
convention.

## §0 Grep the log you ALREADY have [decides half of E4 + E2] [free]

The run that produced `after 13.0 s (budget 70 s)` contains decisive
lines. In Home Assistant: Settings → System → Logs → download the full
log for the timeframe of that test (or `journalctl` on the Pi), then
search for these exact prefixes:

1. `GATT enumeration` — the unit's REAL table: every service/char,
   ff03's `properties [...]` line (does it advertise `write`,
   `write-without-response`, `notify`?). [P once pasted — E4 first half]
2. `using write characteristic` — which resolution path ran (the INFO
   candidate line) — and/or `last-resort` (the WARNING line: ff03
   advertised NO write property — our write command may be silently
   dropped, research §4). [P once pasted — E4 second half]
3. `block push observed while idle` — presence = pushes BEFORE our
   first request (stream is periodic or subscribe-triggered);
   absence = push onset later than 2 s after subscribe (consistent
   with request-triggered). [P once pasted — E2 half]
4. `connect summary` — the exact stage split of the 3.0 s
   pre-settle time. [spec 007 line]

Paste all four groups (a handful of lines). If the log is already
rotated away, skip to §1 — the new build re-emits them (and better).

## §1 Run the connection test on the spec-009 build [decides E2/E3]

Preconditions: phone Bluetooth OFF, vendor app killed (single-connection
rule, spec 006). HA logs at INFO for `custom_components.aqua_temp_ble`.

Re-run the integration's connection test. The new build:

- re-sends the identical base read up to 3 extra times (2 s apart,
  inside the same 8 s window — `first-read re-send 1/3 …` lines);
- prints the idle-window summary before the first request
  (`idle window before first request: …`);
- counts pushes per block start (`blocks seen: 1001x3, …`).

**Outcome A — the test SUCCEEDS.** The unit answers a repeated request
(C2/C5): the census's answer line plus the attempt number IS the
evidence; paste the log. The follow-up spec extends the parity to
polling (contract §6).

**Outcome B — still no answer.** Paste: the timeout line (with counts
and `first/last t+`), all `re-send` lines, all `block push observed`
lines, the idle-window summary, and the §0 groups. The per-start counts
now prove the cycle model (research §0.3) and the re-send lines prove
four deliveries were attempted, not one.

## §2 Run the session probe [decides C1 vs C2/C5 vs C3/C6 — one SSH session]

On the Pi (or any bleak-capable Linux box near the unit), phone BT
still off:

```bash
cd <repo>
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C
# full plan: listen 30 s → 1 base read (15 s) → poll 15×2 s →
#            query 1001 (15 s) → write-with-response base read
# dry-run first (prints the plan + frames, never connects):
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --dry-run
```

Read the per-phase verdicts it prints (plan §4.3):

- **Phase L**: push onset vs subscribe + true cadence + per-start
  counts → E2/E7 [P once run].
- **Phase B**: any FC03 answer / exception within 15 s → E1/E3 [P].
- **Phase P**: `answered on attempt N` → C2 confirmed [P]; `none in
  15` → C2/C5 unlikely [P].
- **Phase Q**: an IMMEDIATE out-of-cycle 1001 push → our writes DO
  reach the engine (C1 dead, C3 dead for queries) [P]; no reaction →
  the write pipe is the suspect (C1 alive) [P].
- **Phase W**: an ATT error code → the write path is refused (C1a)
  [P]; accepted-but-silent → same as production.

Optional, only if §0 shows other notifying characteristics:
`--subscribe-all` (tags any answer arriving elsewhere — C6).

Optional, ONLY after Phase Q is positive, developer-run:
`--experimental` (asks the base block in query shape
`63100007002db997`, plus FC03-at-1001 as the control — research §8;
the tool prints its risk statement first).

Paste the whole timeline output (or save to a file and attach).

## §3 The definitive capture: ONE vendor session on THIS unit [decides the premise, E5]

Research §2.4: no artifact proves the app drives THIS unit. Capture it
once:

1. Android: Settings → About → tap "Build number" 7× → Developer
   options → enable **Bluetooth HCI snoop log**.
2. Toggle Bluetooth OFF/ON, open the vendor app, connect to
   `BlueNRG-C0941C`, wait for the home screen to show values (or to
   show nothing — that IS the answer), change nothing, close the app.
3. Developer options → **Bug report** → extract → find
   `btsnoop_hci.log` (FS/data/misc/bluetooth/logs).
4. Send the file. Read with Wireshark, filter `btatt`: every GATT write
   (which characteristic! which write type!) and every notification,
   with timestamps.

What it proves, in priority order: (a) whether the app gets an FC03
answer on this unit at all (premise §2.4); (b) the write char +
write type the app uses on a ff01-as-SERVICE unit; (c) whether the app
re-sends the base read (E8); (d) where the app's displayed values come
from (request answers vs pushes).

## §4 Report template

```
1. §0 greps: enumeration lines / write-char line / idle line present? (y/n)
2. §1 outcome: success (which re-send count) / failure
   + the full timeout line (with 1001x3-style counts)
   + the idle-window summary line
3. §2 probe: paste the summary block (or attach the full output)
   Phase L verdict: onset __s, cadence __s, starts __
   Phase B: FC03 answer? y/n
   Phase P: answered on attempt __ / none
   Phase Q: out-of-cycle reaction? y/n
   Phase W: ATT error? __ / accepted
4. §3 btsnoop_hci.log attached? y/n
   App home screen showed live values? y/n
```

## What each step proves

| step | proves | status |
|---|---|---|
| §0 | ff03's properties + which write path ran + whether pushes precede our request | [P] once pasted; needs no new run |
| §1 | cycle model (per-start counts), four delivery attempts, push onset vs request | [P] one test run |
| §2 | which cause class is live (C1/C2/C5 vs C3/C6) — the row of research §11 | [P] one probe run |
| §3 | the app's true session on THIS unit — the premise itself | [P] definitive |

## Discipline

- Do not power-cycle the unit between §1 and §2 (keep the state
  comparable); the single-connection rule applies to every step.
- The probe never writes registers (contract §7); the experimental
  flag is developer-only (contract §4).
- Whatever the outcomes: paste them BEFORE any code change — the
  decision matrix (research §11, contract §6) is binding for the
  follow-up spec.