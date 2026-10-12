# Quickstart 017 — turning on power control and verifying it (HA UI only)

Audience: the owner. Everything runs from the Home Assistant UI;
the only prerequisite is knowing what you are about to enable: REAL
remote control of your heat pump.

## §0 Before you enable (2 minutes)

1. **Check the control display gate.** Settings → Devices & Services
   → Aqua Temp BLE → Configure: the **"Control mapping confirmed"**
   (`push_control_confirmed`) checkbox must be ON — without it the
   climate entity does not exist at all in push mode (spec 015). If
   you are reading a climate tile that shows the unit's on/off
   state, it is already on (that is why you have a tile).
2. **Know the evidence.** Your own probe verdict — `power write
   probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0→1→0)` — and
   your own observation ("it turned on, and it turned off") are the
   recorded proof this feature is built on (research 017 §1).
   Control was proven on YOUR machine, not in theory.
3. **Know what happens physically.** Tapping HEAT writes the same
   frame the vendor app's power slide sends (register 1011 = 1):
   the pump REALLY starts, day or night, whether anyone is home.
   Tapping OFF stops it. There is no extra confirmation step —
   treat the tile with the same respect as the app's power slide.
4. **The single-connection rule still applies.** The vendor app
   must be closed / the phone's Bluetooth off while Home Assistant
   is commanding the unit (the unit accepts ONE central — spec 006).

## §1 Enable the write control (FR-1)

The activation is YOUR act — no update can enable it for you:

1. Settings → Devices & Services → Aqua Temp BLE → **Configure**.
2. Check **"Allow power control (write)"** (the new `write_control`
   checkbox; the description names the probe verdict it requires).
3. Save. The entry reloads — no Home Assistant restart needed.
   The climate tile now shows **Off / Heat** mode buttons.

(The alternative path — flipping `WRITE_CONTROL_PROVEN = True` in
`registers.py` and restarting HA — stays honored; the option is the
no-restart path. Either one enables the same surface. Contract 017
§2 is the rule: never enabled by anything but you.)

## §2 The first live verification (AC-12 — do this once, record it)

One HEAT command and one OFF command, with your ears and the tile:

1. **Tap HEAT.** Expected, in order:
   - the pump starts (it really starts — listen);
   - within **~8-10 s** (worst ~28 s) the tile switches to **Heat**
     — the tile follows the unit's own report of its state, never
     the button (that is why it is not instant; the unit reports on
     request — research 017 §2);
   - Settings → System → Logs shows, at INFO:
     `power write via 1011 = 1 — mirror should follow in the next
     report` then `power write confirmed by mirror (2011 = 1)`.
2. **Tap OFF.** Expected: the pump stops; the tile returns to
   **Off** within ~8-10 s; the log shows the same pair for `= 0`.
3. **Record the outcome** (a screenshot of the tile + the two log
   lines is enough — it closes AC-12 and becomes the shipped
   surface's first live proof; contract 017 §8).

Notes on what you will see:
- The **activity pill** (current action) stays **empty/unknown
  while on** — that is honest: "powered on" is not the same as
  "compressor running", and the compressor's register is not
  calibrated yet. The mode display (Heat) is the on/off + mode
  decode.
- **No setpoint control appears** — on purpose (see §5).

## §3 Reading the log lines (Settings → System → Logs)

| Line | Meaning | Your action |
|---|---|---|
| `power write via 1011 = <v> — mirror should follow…` (INFO) | the command was sent | none (the audit trail) |
| `power write confirmed by mirror (2011 = <v>)` (INFO) | the unit's own report confirms the state | none — everything worked |
| `power write not confirmed by mirror (mirror 2011 = <x>, requested <v>)` (WARNING) | the unit reports a DIFFERENT state than requested (panel override, a guard, a race) | look at the panel; the tile shows the unit's truth |
| `power write unconfirmed (no fresh report within <t> s) — the unit's state is UNKNOWN: check the panel` (WARNING) | the report never came back (link, distance, the app holding the connection) | check the panel; if the unit is on and you wanted off, send OFF again |
| the persistent notification "power write unconfirmed…" | an OFF command could NOT be verified — the unit may still be RUNNING | check the panel; turn the unit OFF from the panel/app if needed; the notification clears itself on the next confirmed command |
| `Unknown pushed Mode value …` (WARNING, once per session) | the unit reported an on-mode value the map does not know (the map is `4 → heat` on this heat-only unit) | report the value in the line; it is a one-line fix in `registers.PUSHED_MODE_VALUE_TO_NAME` |

## §4 Disable anytime

Uncheck **"Allow power control (write)"** → Save. The entry reloads
and the climate returns to the read-only surface (spec 015) — the
write path is gone, the state display stays. The option and the
global switch are both always yours.

## §5 What is deliberately NOT in this feature

- **The setpoint cannot be set — or even displayed — from Home
  Assistant.** No register in the unit's pushed data has ever been
  proven to hold the setpoint, and the write address has never been
  recovered. There is no fake button and no invented number; the
  setpoint stays on the panel/vendor app. The named follow-up (a
  logcat session while changing the setpoint in the app — quickstart
  015 §2) may open a future spec.
- **The mode cannot be set.** Your unit is heat-only; the vendor's
  own app refuses mode changes on it, and the vendor's own mode
  write would also touch a second, unproven register (research 017
  §5). The mode is DISPLAYED (Heat when on).
- **No schedules, no time lockouts.** If you want time-based
  control, use Home Assistant's own automations — deliberately, in
  your own hands, with the audit lines of §3 as the trail.

## §6 If something does not follow

- **The tile never changes after a command:** read §3's lines; the
  WARNING names what happened (no report = the link; mismatch =
  the unit's own state). The most common cause is the vendor app
  holding the single connection (§0.4).
- **The tile shows unknown while the unit is on:** the mode-value
  line of §3 — one-line fix, then reload.
- **Everything else:** the **"Power write probe"** diagnostic button
  (spec 016) still exists and still works — it re-verifies the
  write path end-to-end and prints its verdict line; it is the
  standing instrument for any "did the write path break?" question.