# Spec 019 quickstart — the setpoint proof and activation, run from the HA UI only

Audience: the owner. No terminal, no SSH — the whole protocol is
Home Assistant UI + the vendor panel.

## 0. Before you press (2 minutes)

1. **Know what the probe will do — it is gentler than the power
   probe, and the first half changes NOTHING:**
   - **Phase A (the no-op):** the probe reads the current setpoint
     S from the pushed block, then writes **S itself** back to
     register 1046 — the register receives exactly the value it
     already holds. Nothing can change: not the setpoint, not the
     machine's behavior. It proves the write is HARMLESS (and that
     no neighbor register moved).
   - **Phase B (the real proof):** the probe writes **S − 0.5 °C**,
     checks the block shows the new value, then writes **S back**
     and checks again. The physical effect: the pool's heat target
     dips half a degree for about 2 minutes, then returns. Your
     panel's setpoint display is the live cross-check.
2. **Kill the vendor app on every phone / turn the phones'
   Bluetooth off** (the single-connection rule, spec 006).
3. The unit can be ON or OFF — the setpoint register updates
   either way (your own panel change this morning moved it while
   the unit was off). OFF or idle is the calmest moment.
4. The probe refuses to run unless it can read a sane setpoint
   first (10.0–40.0 °C in 0.5 steps) — if your setpoint is outside
   that, it writes nothing and says so.

## 1. Run the proof (1 press, ~2–3 minutes)

Settings → Devices & Services → your Aqua Temp device →
diagnostics → the new button **"Setpoint write probe"** (or
Developer Tools → Actions → `aqua_temp_ble.setpoint_write_probe`
targeting the device). Press it.

While it runs, you can watch (all UI): the climate tile's target
temperature is the SAME register the probe is working on — in
phase B you should see it dip 0.5 and come back.

## 2. Read the verdict (Settings → System → Logs)

| Verdict line (WARNING) | Meaning | Your next step |
|---|---|---|
| `setpoint write probe: NO-OP HELD …` then `… EXECUTED + REVERSIBLE (mirror 1046 followed <S>→<T>→<S>) — evidence for WRITE_SETPOINT_PROVEN` | **THE PROOF.** The write acted and reverted; the tile showed it live | §3 — the setpoint control can be enabled |
| `setpoint write probe: no-op-neighbor-changed (… registers …)` | Phase A saw an UNEXPECTED change in another register of the block — the probe ABORTED before any real change | report the line; check the panel's setpoint is unchanged; wait for the fix before re-running |
| `setpoint write probe: not-executed (mirror 1046 stayed <S>)` | The unit did not act on our write | try once more (radio); if it repeats, report it — the fallback (a different family register) is a researched candidate and needs a spec addendum, not a blind retry |
| `setpoint write probe: not-reversible (… the setpoint is now <T/10> °C …)` | The dip write acted but the revert did not — **the setpoint is now 0.5 °C lower than before** | restore the setpoint from the panel or the vendor app (30 seconds); report the line |
| `setpoint write probe: att-error: <chain>` | The Bluetooth stack refused the write | report the line verbatim |
| `setpoint write probe: refused-baseline (…)` | No sane fresh setpoint reading (no block / out of range / not a 0.5 step) — nothing was written | check the panel's setpoint display; retry |
| `setpoint write probe: no-report (no fresh 1046 block)` | The report never came back this window (radio) | just try again |

**The honest note about phase A:** a no-op that "held" cannot
distinguish a write that executed from a write that was silently
dropped — that is exactly why phase B exists. The two lines
together are the proof.

## 3. After a PROVEN verdict — enable the setpoint control

1. The integration's Configure dialog → check **"Allow setpoint
   control (write)"** (the option; no restart needed) — the
   climate tile now offers the target slider (10.0–40.0 °C, 0.5
   steps).
2. The first verification (one press): set the target 0.5 °C away
   from the current one, watch the tile follow (~8–10 s — the tile
   shows what the MACHINE reports, never what you asked), set it
   back. Compare once with the panel's display: they must agree.
3. The write is refused outside 10.0–40.0 °C or off the 0.5 step —
   by design, and loudly (never silently rounded).

## 4. If you want the deep-dive evidence (optional)

- The verdict line + the climate's `last_setpoint_write`
  attribute (in the tile's developer tools state) carry the raws
  and the timing of every stage.
- The vendor's family-key semantics (why 1046 and not another
  register) is the research item U-4 — the app's own logcat while
  changing the setpoint in the vendor app prints `address = …`
  lines (FINDINGS §12.2). Not needed for control.

## 5. Safety recap

- The probe writes ONLY register 1046 (the heat setpoint), ONLY
  values it read from that same register first (S, T = S ∓ 0.5),
  only after the sanity gates — no other register is ever touched,
  and phase A's whole-block diff would CATCH a mis-aimed write
  before anything real happens.
- The guard (always on, even after activation) accepts only
  10.0–40.0 °C in 0.5 steps on 1046 — nothing else can ever be
  written by this integration.
- The setpoint write capability stays disabled until you check the
  option after the proof; no update can enable it for you.
- A failed revert is never silent — the verdict tells you the new
  setpoint and how to restore it.