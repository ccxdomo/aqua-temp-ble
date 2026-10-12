# Spec 018 quickstart — verifying the power-dominant display

Owner-facing verification, from the deployed fix (0.13.1). Total
time: ~10 minutes for §1 (the AC-7 live test), one passive
observation window for §2 (the AC-8 research step).

## §0 Preconditions (one glance)

1. The integration's Configure dialog: **`push_control_confirmed`**
   checked (the climate exists only behind it — spec 015) and
   **`write_control`** checked (the buttons — spec 017). If the
   climate tile exists and the on/off buttons work, both are
   already on.
2. The log page (Settings → System → Logs) shows the
   `custom_components.aqua_temp_ble` records.

## §1 The live test (AC-7) — ~10 minutes

1. **Baseline, unit OFF**: the tile shows **`off`** and the pump is
   silent. If it shows `unknown` here, stop — that is the §2
   diagnostics case, not this test.
2. **Tap the ON control** (the HEAT button or the climate's
   turn-on). Expect, in order:
   - the pump physically starts (as before — the write path is
     unchanged);
   - the log shows `power write via 1011 = 1 …` then
     `power write confirmed by mirror (2011 = 1)`;
   - **the tile shows `heat` within ~10-15 s** — the fix under
     test. The old behavior (`unknown`) is the bug this spec fixes.
3. **Check the WARNING is gone**: the log page must show **no**
   `Unknown pushed Mode value 0` line for the whole session (the
   INFO `pushed mode value … unmapped` line appears only for
   values other than 0/4 — not expected today).
4. **Tap OFF**: the pump stops, the log shows the OFF audit +
   confirmed lines, and **the tile shows `off`** within ~10-40 s
   (one confirmation cycle, at most one poll cycle after).
5. **Repeat ON/OFF once** to confirm the round trip is stable.

**Record the result** (the AC-7 closure, the 016-AC-11/017-AC-12
pattern): the two tiles' final states + the verdict lines, pasted
into research.md §8's ledger note (or the release notes).

## §2 The research step (AC-8, non-blocking) — the long-ON observation

This step does not gate the fix (the display is correct in every
outcome); it closes research 018 §8 **G-1** — WHY the app-flow
world showed `2019 = 4` while our button leaves it at `0`.

1. Tap **ON** (leave the unit running).
2. Open the **`push_stream` diagnostic sensor** (the diagnostic
   entity of the device) → its `control_candidates` attribute →
   read **`2019`** at: ~30 s, ~2 min, ~5 min after the ON.
3. Record what 2019 does:
   - **stays `0`** while the machine runs → the app-flow `4` came
     from something else the app session does (E5 / the logcat
     oracle are the named deciders);
   - **flips to `4`** after a delay → 2019 is a lagged
     "heating engaged" status (a candidate `hvac_action` oracle
     for a FUTURE spec — the tile already shows `heat` regardless).
4. Paste the observation (values + rough times) into research.md
   §8 G-1's row — either outcome closes it.

## §3 The log-volume check (FR-4)

Over any multi-hour window after the fix:

- the log page shows **no**
  `Module custom_components.aqua_temp_ble…aqua_client is logging
  too frequently` line;
- the per-cycle traffic is now ONE line per poll cycle
  (`push cycle: trigger read timed out as expected (census: …)`);
- the per-connect evidence lines (`block push captured`, the
  connect summary) still appear — they are the contract evidence
  and must stay.

If per-frame visibility is ever wanted (a calibration session),
enable DEBUG for `custom_components.aqua_temp_ble.aqua_client` —
the `block push observed` lines live there now (contract 018-B
§3).

## §4 If something looks wrong

| Symptom | Meaning | Action |
|---|---|---|
| Tile `unknown` while ON after the fix | power is decoding `None` — no fresh 2001 block (connection/stream problem), NOT a mode problem | check `push_stream` = `on`; the connect lines; retry after a service reload |
| Tile `off` while the machine runs | the mirror reports 2011 = 0 against reality | check the panel; run the 016 power-write probe; do not trust the button over the panel |
| `power write unconfirmed …` WARNING + a persistent notification | the confirmation got no fresh report (radio/link) | the notification is by design (017 FR-3 step 7); retry the command |
| `pushed mode value <N> (reg 2019) unmapped` at INFO | the unit reported a NEW mode value (N ∉ {0, 4}) | note the value + the unit's state; it feeds the research map (research 018 §8 G-3) |

## §5 Safety reminders (unchanged from 017)

A remote start is a REAL pump start, day or night. The unit's own
controller governs its protections exactly as when the vendor app
toggles the same register. The unconfirmed-OFF persistent
notification is the one surface you must not ignore.