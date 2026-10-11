# Spec 012 quickstart — recovering the verdict you already have (no terminal)

Every step below runs from the Home Assistant UI or your phone. **You
never need a terminal on the Pi.** Precondition for every test run (the
single-connection rule, spec 006): **kill the vendor app on your phone
and turn the phone's Bluetooth off**.

What happened on your last run (the short version): the probe almost
certainly DID run — the 20.9 s line decomposes as ~4.9 s connect (your
usual connect time) + the 8 s read + the probe's own 8 s window. Its
verdict was written into the **log file** at a level your log page
does not display (that page keeps only errors and warnings). The
0.8.0 build (below) fixes that: the probe lines become warnings and
appear on the page directly.

## §0 Read the verdict you already have (5 minutes, no new run)

The lines are in the log file of the test you ALREADY ran.

1. **Settings → System → Logs** → the download button (top right —
   "Download full log"). You get `home-assistant.log`.
2. Open the downloaded file and search for:
   - `security probe` → paste every line it finds (expected: the
     "re-sending base read as acknowledged write …" line, then one
     `security probe: verdict …` line — that second line is **the
     answer the last three builds were built to obtain**);
   - `connect summary` → paste the line (its `total …s` confirms the
     arithmetic: ≈ 4.9 s means the probe stage ran);
   - count the lines starting `block push observed` (≈ 32 means the
     probe window ran; 16 means it did not).
3. If `security probe` finds **nothing** in the file: paste the
   `connect summary` line anyway — a `total ≈ 12.9 s` means the HA
   process was still running the previous build (the copied files
   never loaded). That is exactly what the 0.8.0 build's marker line
   (§1) makes impossible to miss in the future.

## §1 Update to the 0.8.0 build — and FULLY restart Home Assistant

1. Copy the updated `custom_components/aqua_temp_ble` as before.
2. **Restart Home Assistant** (Settings → System → ⋮ menu → Restart).
   This step is REQUIRED: the connection-test code is loaded once per
   HA start — without a restart the old code keeps running even
   though the files are new.
3. After the restart, run the connection test once more
   (Settings → Devices & Services → Aqua Temp BLE → the test step).
   The FIRST line on the Logs page is now
   `connection test to <address>: build 0.8.0`.
   - If it says `build 0.8.0` → the new code is running; the probe
     lines will appear right below it.
   - If it says something else (or nothing) → the restart did not
     take; do not interpret any probe line.

## §2 Read the outcome — now on the Logs page directly

The probe lines are now warnings: they appear on
**Settings → System → Logs** with no download needed. Paste them.

| Logs page shows | Meaning | Next |
|---|---|---|
| `security probe: verdict att-accepted-silent` | the unit **accepted our write** at the Bluetooth layer but its engine ignored it — the gate is inside the firmware, not the link (this is the expected verdict per the 20.9 s arithmetic) | §3 (the phone capture) — pairing is NOT the fix |
| `security probe: verdict att-error-security` + `pairing: …` | the unit refused the write because of security/pairing — the pair ladder already ran automatically | quickstart 011 §2 (the "bLE" choreography) if pairing failed |
| `security probe: answered after pairing` | case closed — the bond gate is proven | quickstart 011 §2 (persistence retest) |
| `security probe: acknowledged write answered …` | the Write Request itself woke the engine | paste the line; the follow-up spec decides |
| `verdict att-error-permission` / `att-error-handle` | our write pipe is wrong at the GATT layer | paste everything; wait for the follow-up spec |
| `local-failure` | local stack problem | paste the chain |

## §3 The phone capture (E5) — only if §2 shows att-accepted-silent

Same procedure as quickstart 011 §3 (Android HCI snoop of one
vendor-app session): it decides what the phone does that we do not —
most likely an app-layer identity/bond check invisible at the GATT
layer. The capture is the follow-up spec's direct input; no further
probing frames will be added from the Pi side (that ladder is closed).

## §4 Report template

```
1. §0 greps pasted? (y/n) — security probe lines (verbatim):
   connect summary total: … s ; block push observed count: …
2. §1 build marker line (verbatim): …
   HA fully restarted? (y/n)
3. §2 probe lines from the Logs page (verbatim):
   verdict line (verbatim):
   pairing line (verbatim, if any):
4. §3 capture sent? (y/n)
```

## Discipline

- Whatever you find in §0: paste it BEFORE any further code change —
  the verdict of the already-run test is decisive evidence
  (contract 011 §6 routing is binding).
- Do not enable "Pair on every connect" — the expected verdict
  (att-accepted-silent) does not route to pairing; the option exists
  to be earned (contract 011 §1.5).
- Do not power-cycle the heat pump between §0/§1/§2 runs
  (comparable state).