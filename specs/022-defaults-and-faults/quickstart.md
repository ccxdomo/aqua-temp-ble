# Spec 022 — Quickstart: the new user experience + the E03 fault-hunt protocol

Date: 2026-10-12
Audience: the owner. Two parts: §1 what the 0.22 build changes on the
user side (VOLET 1), §2+ the reversible fault experiment that can pin
the fault register (VOLET 2). The evidence this protocol produces feeds
the exposure contract (`contracts/fault-evidence-contract.md`).

---

## 1. What 0.22 changes for you (VOLET 1)

- **No more checkboxes, no more gates.** Adding the unit asks for a
  name only. Sensors, the climate tile, power control and setpoint
  control all work immediately.
- The confirm screen now shows what the test actually captured
  (temperatures, power state, heat target) instead of "unknown".
- Configure offers only the polling interval and the display unit.
- What still protects the machine (automatically, no action from you):
  writes go ONLY to the two proven registers (power 1011, setpoint
  1046), power accepts only on/off, the setpoint only 10.0-40.0 °C in
  0.5 steps — out-of-range requests are refused loudly, never rounded
  or clamped. Every write is confirmed by the unit's report; an
  unconfirmed OFF raises a notification.
- Fault code / firmware: still not displayed — no reliable source has
  been identified in the unit's report (the vendor app has a fault
  table, but the register that feeds it is not recoverable statically —
  `research.md` §2). §2 of this document is how we may fix that.

## 2. The E03 experiment — what and why

Your unit is **not plumbed**: if it runs, the flow protection will trip
and the panel is expected to display **E03** (flow fault). That is a
reversible, low-risk event — dry-running briefly is the unit's normal
commissioning state, and the flow protection exists precisely to catch
it. While the fault is displayed, one register in the unit's data
report very likely changes. A capture before / during / after the fault
reveals WHICH — the same diff-revert method that pinned the power
(2011), mode (2019) and setpoint (1046) registers.

**Any displayed code is equally valuable.** If the panel shows E01, E02
or anything else instead of E03, run the protocol exactly the same and
write the code down — the diff is what matters.

### Optional step 0 — the vendor's fault table (no disassembly needed)

The app's APK contains its own fault tables at
`assets/faults/faults_*.json` (proven to exist — research §2.1-c). The
APK extraction on the gateway kept only `lib/arm64-v8a/`, so the table
is not on this host. If you still have the original APK: open it as a
ZIP, extract `assets/faults/*.json`, and hand the files over — they may
contain the vendor's own code→description (and possibly dataId→code)
map, which would complement the live diff. Not required for §3.

## 3. The protocol, step by step

**Prerequisites**

- The 0.22 build is installed (controls are active by default).
- The vendor app is CLOSED (single BLE link — HA needs it).
- HA's poll cycle is healthy (entities show live values).
- Stay near the unit while it runs.

**Which world are you in?** Two entries lead to the same evidence:

- **Healthy unit** (the general case): the unit is OFF and no fault is
  displayed — run the steps in order (baseline → provoke → fault capture
  → clear → cleared capture → diff).
- **Already faulted** (the 2026-10-12 reality): the unit is un-plumbed and
  the panel is showing **E03 right now** — run them in the FAULTED order:
  capture the fault state FIRST (panel code + time → probe), then clear,
  then capture the healthy state, then diff. The healthy baselines are
  already recorded (the research Part 3.3 tables); your healthy capture
  confirms them, the fault capture is the new evidence.

**The burst keeps flowing during the fault** [P — the owner's reload
while the panel showed E03 logged four `block push captured` WARNING
lines, crc=OK]. One caveat: the values of THAT reload were lost to the
021 hex demotion (the C2 regression) — re-capture with the 0.22 build
(from which the WARNING capture lines carry the full frame hex again).

### Step 1 — Baseline capture (healthy)

1. HA → Settings → Devices & Services → your Aqua Temp device → find
   the **Probe registers** button. (Or Developer Tools → Actions →
   `aqua_temp_ble.probe_registers`, leave `device_id` empty to probe
   all, `block` default — **enable "Return response"** so the whole
   snapshot lands in the Actions result panel; the log lines carry the
   same data.)
2. Run it once. You get every register of every captured block with its
   raw value — the response's per-block `count=` field is the freshness
   tell (a count frozen between two probes = a stale store; a live
   stream increments it).
3. **Save the whole output** (copy-paste to a file). This is the
   BASELINE (or the CLEARED capture, in the already-faulted order).
   Sanity-check the known members: power (2011) = 0, the temperatures
   look right, and note the raws at 2012, 2017, 2018 (expected: 1,
   13, 168) and 2034 (0 when running-ON healthy, 4 when idle-OFF).

### Step 2 — Start the pump and wait for the fault

1. Start it from HA: the climate tile → **Heat** (that is the proven
   power write). The pump runs dry.
2. Watch the panel. The fault code is expected within **a few
   minutes** (some units show it immediately — a flow precondition;
   some run a bit first). Do not leave it running unattended.
3. When the panel DISPLAYS the code: write down **the exact code**
   (e.g. `E03`).

### Step 3 — Fault capture (code displayed)

1. While the code is still displayed, run **Probe registers** again
   (the unit answers requests even while faulted — PROVEN: the burst
   kept flowing during the owner's live E03; if the probe fails / the
   stream is silent anyway, note that and go to step 4).
2. **Save the output.** This is the FAULT capture. In the
   already-faulted order this is your FIRST capture — write the panel
   code and the time next to it.

### Step 4 — Clear the fault

1. Stop the unit from HA: the climate tile → **Off** (or the panel's
   off).
2. Wait for the panel to return to normal (no code). If the fault
   latches, a brief power-cycle of the unit clears it — the code's
   memory of it is what the panel shows, not what we need.

### Step 5 — Cleared capture

1. With the panel normal, run **Probe registers** one last time.
2. **Save the output.** This is the CLEARED capture.

### Step 6 — The diff (or hand the three files over)

Compare the three outputs (or just send them to the pipeline with the
panel code). What we are looking for:

- every register that was **0 at baseline → non-zero in the fault
  capture → 0 again in the cleared capture**;
- any change in **2012 / 2017 / 2018 / 2034** (the known unknowns);
- anything else that moved with the fault and reverted without it.

That mover, with its three values, is the candidate fault home — and
the contract (`contracts/fault-evidence-contract.md` §1) is satisfied:
the follow-up can expose a **Fault** binary sensor (ON = fault, with
the raw as evidence), and if the raw↔code link is proven (your E03
panel code ↔ one raw value), the tile can even show the code.

**The prioritized watch list** (what to look at first — the probe dumps
all 45 raws of each block, so the diff covers everything; this is the
shortlist):

**P1 — block 2001 (the status plane):**

1. The ~39 registers that read **0 at healthy baseline** (2001-2009,
   2013-2016, 2020-2033, 2035-2045): a fault word plausibly reads 0
   when healthy → non-zero on trip. ANY 0→non-zero is the prime hit.
2. **2012** (healthy 1 = bit 0 only): new bits = a bitfield hit — IF
   the app's ten-flag list order is the bit order, WaterFlowSwitch =
   **bit 2** → expect raw 5 (0x0005) or 4 (0x0004); polarity unknown
   [V] — also watch 1→0.
3. **2034** (0 healthy-ON; **4** in the idle-OFF capture): an
   unlabeled state enum PROVEN to move — the strongest second-value
   lead; expect possibly a third value during E03.
4. **2017** (13) / **2018** (168): the already-nonzero wildcards — any
   value/bit change; if 2018 is a runtime counter (168 h = one week),
   a freeze or jump during the fault is informative.
5. **2010 = 2001** (the self-reference): expected static; a move is
   notable.
6. **2011** (power) / **2019** (mode): the CONSEQUENCE plane — the trip
   may flip them (1→0); record, do not pin (the homes are [P]).

**P2 — block 2046 (the temperature plane):**

7. **2047/2048** (inlet/outlet): no-flow drift/divergence — the
   physical signature of E03 (consequence, diagnostic).
8. **2046** (the sentinel 32765): leaving sentinel = the sensor-fault
   plane (cf. the app's *SensorFault flags).
9. **2052-2090**: the zero tail — the same 0→non-zero logic.

**P3/P4 — blocks 1001 and 1046:** config/ladder, static by nature [P
≥ 2 days]; any move during a fault is surprising — check, low prior.

**Patterns that make a hit:** 0→non-zero; new bits in 2012 (bit 2);
E-number-shaped or pool-numeric values (021/089/120/217/156/157/158/
389 [V]); sentinel moves; a register freezing mid-update. **Non-hits:**
2011/2019 moves (consequences), temperature drift (physics), values
that revert within one cycle (mirror noise).

**The instruments, by name:** the `aqua_temp_ble.probe_registers`
service, block `base`, with return-response enabled (the complete
four-block snapshot with labels; the `count=` field is the freshness
tell); the `Push stream` diagnostic's `control_candidates` attributes
(2012/2017/2018/2011/2019 raws with evidence tags — 2017/2018 joined
in spec 022); and the `block push captured: … hex=…` WARNING lines on
every HA reload (the passive capture — spec 022 FR-13).

## 4. The evidence checklist (what the record needs)

| # | Item | Where from |
|---|---|---|
| 1 | Baseline capture (all blocks) | step 1 |
| 2 | The panel's exact code (e.g. E03) | step 2 |
| 3 | Fault capture + which registers changed vs baseline | step 3 + diff |
| 4 | Cleared capture + the changed registers reverted | step 5 + diff |
| 5 | Whether the burst flowed during the fault (probe ok / silent) | step 3 |
| 6 | The register's healthy-state value and fault-state value, written down (e.g. `2034: 0 → 5`) | the diff |

## 5. If something unexpected happens

- **A different code (E01/E02/...)**: proceed identically; record the
  code. Every proven code = one more row in the raw↔code map.
- **The unit won't start at all / the panel flashes a code
  immediately**: that IS the fault display — capture it (step 3).
- **The probe fails during the fault** (stream silent): note it, clear
  the fault, and capture immediately after — valid but weaker evidence
  (labeled as such in the record).
- **Nothing changes in the report**: that is a real result — it means
  the fault does not ride the pushed blocks (or this fault does not).
  The record then says so, and the optional APK assets (§2 step 0)
  become the main lead. We do NOT expose an unproven fault.

## 6. After the run

Hand the three captures + the panel code + the checklist to Codepilot.
If a home is pinned, the follow-up spec activates the exposure
contract: one `binary_sensor` "Fault" (problem), raw values as
attributes, the code string only for codes proven by your captures —
and the confirm/options texts updated to say the fault IS displayed.