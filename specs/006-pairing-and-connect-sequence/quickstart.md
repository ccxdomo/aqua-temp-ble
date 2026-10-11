# Quickstart 006 — Resolving "Impossible de se connecter" (pairing & connect runbook)

What to do physically, in order, and what you should see. Each step says
what it proves. Total time: ~10 minutes. No reverse-engineering tools
needed — the vendor app's own wizard is the only reference document you
may want.

**Ground truth first (research.md §8):** the PAC requires **no code, no
PIN, no "unlock" frame** before it accepts a BLE connection. The vendor
app's session is simply *connect → discover → subscribe → read*. The
"pairing mode" gesture below exists (it is the WiFi-provisioning config
mode), but it is **not proven to be required** for a BLE-only unit — it
is step 3's experiment, not step 1's reflex.

---

## Step 1 — Free the link (2 min) — **do this before anything else**

A BLE peripheral accepts **exactly one** connection at a time. The vendor
app **auto-reconnects** to the unit (research §2.2) — a phone with the
Aqua Temp app merely open in the background can hold the link forever
and refuse every other client.

1. On the phone: force-close the Aqua Temp app (Android: Recents → swipe
   it away, or Settings → Apps → Force stop).
2. On the phone: turn **Bluetooth off** (this also kills any hidden
   background link).
3. Wait ~10 s.

**You should see:** HA still lists the device after a fresh scan
(Bluetooth → … the MAC 08:06:A6:C0:94:1C, name `BlueNRG-C0941C`).

> Note: the RSSI shown in HA's list can come from a cached discovery —
> a visible entry does **not** prove the link was free. That is why this
> step comes first even if the device "looks" available.

## Step 2 — Retry the connection test (1 min)

Run the config flow again (or the "Test connection" step).

- **If it connects** → done: the phone was holding the single BLE link.
  Keep the vendor app closed whenever Home Assistant should control the
  unit (two masters cannot share it — that is physics, not a bug).
- **If it still fails** → continue.

## Step 3 — The "bLE" experiment (2 min) — settles the pairing question

This answers, once and for all, whether **your** unit gates connections
behind configuration mode (static analysis cannot — research §8).

**The gesture** (from the vendor's own instruction, research §1.1):

> Press and hold the panel button **[shown by the wizard's photo]** for
> **5 seconds**, until the display shows **"bLE"** and **blinks once per
> second** — that is configuration mode.

- Which button: the wizard (vendor app → Add Device → BLE type) shows the
  exact photo. On PHNIX panels it is most likely the **power / ON-OFF
  button** — **[V]**, not proven; the app photo is authoritative
  (research §1.2).
- **While the display still shows "bLE" blinking**, run the HA
  connection test again (config mode may time out — if "bLE"
  disappears, re-enter it and retry quickly).

Outcomes:
- **Connects** → your unit requires config mode for new centrals
  (first-provisioning behaviour). Finish the **initial pairing once with
  the vendor app** (add the unit while "bLE" shows) and re-test in
  normal mode afterwards; report the result so the runbook is updated.
- **Still fails** → the pairing gate is not your problem; let the mode
  lapse (or power-cycle) and continue. **Do not leave the unit in
  config mode.**

**What the display means** (research §1.1):
| Panel shows | Meaning |
|---|---|
| normal temperature | normal mode |
| `bLE` blinking 1×/s | configuration/pairing mode entered |
| `888` blinking 3× | provisioning success — WiFi models only, not your BLE-only unit |

## Step 4 — Power-cycle the PAC (3 min)

Mains off (or breaker), wait 30 s, on. This clears a wedged BLE module
(stale GATT state on the peripheral side). Wait ~30 s after power-on for
the module to advertise, then repeat **Step 2**.

## Step 5 — Range check (1 min)

-67 dBm is workable but connect requests are more fragile than
advertising sniffing:
- Move the HA host / dongle closer (target RSSI ≥ -80, better ≥ -70).
- If the host is a Raspberry Pi: put the dongle on a short **USB
  extension** away from the case/PSU — classic fix for connect
  instability.
- Metal cabinet, pool-house wall, pump motor between → relocate.

## Step 6 — Differential: is it the host or the module? (5 min)

From the HA host shell:

```bash
bluetoothctl
[bluetoothctl] scan on          # wait for 08:06:A6:C0:94:1C
[bluetoothctl] scan off
[bluetoothctl] info 08:06:A6:C0:94:1C   # check "Address type" = Public
[bluetoothctl] connect 08:06:A6:C0:94:1C
```

- `connect` succeeds → the module is fine; the failure is in the
  integration's path (report the logs, step 7).
- `connect` fails with timeout after ~20 s → **host/module-level**
  (MTU-exchange quirk of the module firmware or adapter issue) — this is
  out of the integration's control; capture the error text.
- Then `disconnect` (and `remove` the device if it appeared) so BlueZ
  state stays clean for HA.

## Step 7 — Capture the evidence

```yaml
logger:
  logs:
    custom_components.aqua_temp_ble: debug
```

Retry the test once more, then attach from `homeassistant.log`:

- `connection test to 08:06:A6:C0:94:1C …` lines (they carry the real
  transport error and its cause chain — spec 005),
- any `connect … failed:` / `post-connect setup … failed:` lines,
- the `bluetoothctl` outcome from step 6.

---

## What NOT to do

- **Do not hunt for a "secret code"** — there is none in the vendor app
  (research §8: no bonding, no PIN, no pre-connection frame; the only
  password in the app — `"66"` — unlocks a debug parameter view, not the
  transport).
- **Do not send the time-sync frame** `631004f30004080001…` hoping it
  unlocks anything — it is a manual settings feature (menu → confirm
  dialog), never sent at session start (research §5).
- **Do not run the vendor app on the phone at the same time** as HA —
  one link only; the vendor app wins because it auto-reconnects every
  few seconds.

## Status of this runbook

| Claim | Status |
|---|---|
| No code/frame needed before connection | **[P]** (vendor-app reverse, research §8) |
| Single-connection rule + vendor auto-reconnect | **[P]** (BLE controller fact + app logs, research §2.2) |
| The 5 s "bLE" gesture text | **[P]** (verbatim vendor i18n, 12 languages, research §1.1) |
| Which physical button the `[icon]` is | **[V]** — likely power; the wizard's photo is authoritative (research §1.2) |
| Whether "bLE" mode is REQUIRED on a BLE-only unit | **[V]** — step 3 settles it; not required in the vendor's daily-use flow (research §1.3, §2.3) |