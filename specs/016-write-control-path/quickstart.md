# Spec 016 quickstart — the power-write proof, run from the HA UI only

Audience: the owner. No terminal, no SSH, no logcat — the whole
protocol is Home Assistant UI + ears + the vendor panel.

## 0. Before you press (2 minutes)

1. **The unit must be OFF** (the panel shows it off / the app, if you
   look, shows off). The probe REFUSES to run otherwise — by design it
   only proves an off→on→off cycle.
2. **Kill the vendor app on every phone / turn the phones' Bluetooth
   off.** The unit accepts ONE central at a time; a phone holding the
   link makes the probe fail at connect (the usual single-connection
   rule — spec 006 §9).
3. Know what the probe will do, physically: **the heat pump will
   actually START during the test** (that is the point), run for a few
   seconds (the report window), then turn back off. If the pool
   plumbing makes starting undesirable right now, wait for a good
   moment — the pump will circulate.

## 1. Run the proof (1 press, ~1 minute)

1. Settings → Devices & Services → your Aqua Temp device → the
   **diagnostics** area → the new button **"Power write probe"**
   (or Developer Tools → Actions → `aqua_temp_ble.power_write_probe`
   targeting the device).
2. Press it. The probe:
   - connects, triggers the report, and checks the power mirror
     (register 2011) reads 0 (off) —
   - sends the vendor's own power-ON frame
     (`631003f30001020001f131` — the frame recovered from the app's
     disassembly),
   - reads the report again — did 2011 follow to 1?
   - sends the power-OFF frame (`631003f3000102000030f1`),
   - reads the report again — did 2011 come back to 0?
   - logs ONE verdict line.

While it runs you can LISTEN: the pump starting during the window is
itself strong evidence the write acted.

## 2. Read the verdict (Settings → System → Logs, or the persistent
notification)

| Verdict line (WARNING) | Meaning | Your next step |
|---|---|---|
| `power write probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0→1→0) — evidence for WRITE_CONTROL_PROVEN` | **THE PROOF.** The write acted and reverted | §3 below — the climate write can be enabled |
| `power write probe: not-executed (mirror 2011 stayed 0 after ON)` | the unit did not act on our frame (the bond-gate residual — research 016 §4 Q4 rank 2) | report it; the next step is the pairing attempt (`pair_on_connect` / the config-flow test — quickstart 011 §2), then re-run §1 |
| `power write probe: not-reversible (mirror 2011 stayed 1 after OFF)` | ON acted, OFF did not — **the unit is still ON** | turn the unit OFF from the panel or the vendor app; report the line |
| `power write probe: att-error: <chain>` | the Bluetooth stack refused the write — the chain names why (security/permission/handle) | report the line verbatim |
| `power write probe: refused-baseline-not-off (mirror 2011 = <v>)` | the unit was not confirmed off — nothing was written | start over from §0 |
| `power write probe: no-report (no fresh 2001 block)` | the report never came back this window (radio/link) | just try again; if it repeats, report it |

Also cross-check (all UI-only): the coordinator's climate entity went
`OFF → HEAT → OFF` during the run (it reads the same mirror), and —
after the probe, once you reopen the app — the app agrees the unit is
off.

## 3. After a PROVEN verdict — enable the climate write (phase 2)

The proof is the gate; enabling is your decision:

1. The integration's option **"Allow power control (write)"** — or,
   in this build, the documented config switch `WRITE_CONTROL_PROVEN`
   (Settings → the integration's Configure / the documented YAML key
   — whichever surface the Coder ships, FR-8) — set it to ON.
2. Restart Home Assistant (the calibration-switch precedent).
3. The climate now offers OFF/HEAT buttons: setting HEAT writes the
   proven frame; the climate NEVER assumes success — the next report
   confirms (a mismatch logs a WARNING).

## 4. If you want the deep-dive evidence (optional, still no terminal)

- The verdict line plus the coordinator's DEBUG lines carry the three
  mirror observations (times + raws) — enough for any follow-up spec.
- The vendor's own write addresses for the OTHER controls (mode,
  setpoint, timers) live in the app's logcat — `adb logcat` while
  toggling in the app prints `address = …` lines (FINDINGS §12.2,
  quickstart 015 §2). Not needed for power control — recorded here
  as the follow-up path (research 016 §8 U-2/U-3).

## 5. Safety recap

- The probe writes ONLY the two vendor-pinned 11-byte frames
  (research §2.1), only ever the values 0 then 1 then 0, and only
  after confirming the unit is off. No other register is touched.
- A failed revert is never silent — the verdict tells you the unit is
  on and how to turn it off.
- The climate's write capability stays disabled until you flip the
  proven switch (contract 016 §4); no update can enable it for you.