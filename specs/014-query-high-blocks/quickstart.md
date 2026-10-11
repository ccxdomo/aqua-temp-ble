# Quickstart 014 — how to verify the query cycle

Audience: the owner (no terminal on the Pi; the HA UI log panel keeps
WARNING+ only — contract 012 §2) and the developer reading the pasted
logs. Steps are ordered; §0 needs NO new run at all.

## §0 — Close the 2001 gap with the log you ALREADY have (no run)

The 22:41:13 instant harvest return proves all four block starts were
captured that connect (research 014 §8.3) — so the complete 0.9.0 log
contains FOUR `block push captured: start=… qty=45 len=99 crc=OK
hex=…` WARNING lines, each with the full 99-byte hex:

1. Open Home Assistant → Settings → System → Logs (or the System log
   panel) and search `block push captured`.
2. Paste ALL FOUR lines (starts 1001, 1046, 2001, 2046) — the 2001
   line is the project's never-captured block (spec 013's U-1).
3. Optional consistency check while pasting: in the 1001 hex line,
   hex characters 94-97 (data bytes 40-41 = frame bytes 47-48 =
   register 1021) should read `041a` (= 1050 = H04 105.0 °C) if the
   machine settings are static [V until pasted].

## §1 — After the 0.10.0 build lands: watch the entry heal itself

The 22:41 entry (mode `push`) is in the setup-retry loop with
`push stream silent`. After updating the integration files (build
0.10.0) — reload the integration or just let the retry tick — the
next poll cycle now sends its trigger and the loop should clear:

Expected log lines (grep these):

1. `connection test …: build 0.10.0` (the build marker — if it still
   says 0.9.0, the files never loaded, research 012 §5).
2. Per poll cycle, INFO: `push cycle: trigger read timed out as
   expected (census: no matching response within 8.0s (received N
   frame(s) …; blocks seen: 1001x…, 1046x…, 2001x…, 2046x…))` — the
   trigger's burst, censused. The per-start counts here are the U-7
   evidence: paste one such line.
3. The setup error disappears; the entry becomes `Loaded` and the
   `push_stream` diagnostic sensor appears (`on` while fresh).

If instead you see `push stream silent after trigger` — the trigger
did not wake the unit (a REAL new condition, distinct from 0.9.0's
listen-only silence — contract 014 §4.2): paste the surrounding
lines; the census in the INFO line of step 2 tells whether any frames
arrived at all.

## §2 — The calibration run (unchanged, quickstart 013 §2)

1. Photograph the vendor app's parameter view (T01-T06) or its home
   screen inlet/outlet values.
2. Kill the app / phone Bluetooth OFF (the single-link rule).
3. Within ~2 min: run the integration's reload (or one connection
   test) and compare the `T-window raws` line against the photo.
4. Match ⇒ enable `push_temp_confirmed` in the entry's options
   (restart/reload so the entities appear). Mismatch ⇒ paste the
   line — a permuted member order is a one-line fix in
   `registers.PUSHED_TEMPS` (data-model 013 §6), then re-run.

## §3 — The Phase-2 experiment: does a query-block frame get a DIRECT answer?

This decides the follow-up's trigger upgrade (research 014 §10 U-6).
The "Probe registers" service (fixed in spec 012) can send the vendor's
own query-block frame for block 2046:

1. Developer tools → Services → `aqua_temp_ble.probe_registers`,
   block `2046` (the T-family block), while the unit is connected
   (vendor app dead).
2. Read the result/log: if the answer is the 2046 block frame
   (`start=2046 … len=99 CRC=OK` with the T-window registers), the
   unit answers per-block queries directly — the follow-up spec swaps
   the trigger to `build_query_block(2046, 45)` in one line.
3. If the probe times out or the answer is a mixed burst, paste the
   census lines — either way the evidence lands.

## §4 — The unchanged Track A evidence (control path)

- **E5** — one Android HCI snoop of a vendor-app session on THIS unit
  (Developer options → bug report → `btsnoop_hci.log`; quickstart 009
  §3 protocol): does the phone's FC03 base read get answered? This
  remains the single most decisive control-path experiment.
- **The revival line**: if `push mode: trigger read answered` ever
  appears (contract 014 §5.1), paste it — the request path revived
  (pairing world) and a future spec re-evaluates the mode.

## §5 — What each failure now MEANS (the operator's decoder ring)

| Log line | Meaning | Action |
|---|---|---|
| `push stream silent after trigger` | the proven trigger did not wake the unit (no burst within 8 + 30 s) | paste the cycle's INFO census line; the retry loop keeps trying |
| `trigger read failed: …` | a transport failure during the trigger (link lost / write refused) | the chain rides in the line; treat like a classic-mode read failure |
| `push stream silent` (0.9.0 literal, gone in 0.10.0) | the old listen-only starvation — should never appear on 0.10.0 | if seen, the build marker says 0.9.0 → the files never loaded |
| INFO `push cycle: trigger read timed out as expected (census: …)` | the healthy world — the trigger fired and the burst was censused | paste one per session for the U-7 evidence |