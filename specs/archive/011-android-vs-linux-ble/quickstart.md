# Spec 011 quickstart — the no-terminal test procedure (for the pool owner)

Every step below runs from the Home Assistant UI or your phone. **You
never need a terminal on the Pi.** Precondition for EVERY step (the
single-connection rule, spec 006): **kill the vendor app on your phone
and turn the phone's Bluetooth off**, or the heat pump will never talk
to the Pi.

What we are testing: the leading theory says the unit accepts
*notifications* from anyone but only accepts *writes* from a paired
(bonded) device — and your phone is paired while the Pi is not. The
steps below make the unit's refusal visible, then try to pair the Pi.

## §0 Paste what you already have (5 minutes, no new run)

From Home Assistant: **Settings → System → Logs** (download the full
log if offered). In the log of your LAST failed connection test,
search for and paste these exact line beginnings:

1. `GATT enumeration` — the unit's real service/characteristic table
   (shows what ff03 advertises);
2. `using write characteristic` (and `last-resort` if present);
3. `idle window before first request`;
4. the full `connection test … no response` timeout line.

Send all of them. They decide what ff03 advertises and cost nothing.

## §1 Run the connection test on the spec-011 build (X1 + X2, automatic)

After the integration is updated to the 011 build (the usual update
path), re-run the connection test from the flow
(Settings → Devices & Services → Aqua Temp BLE → Add/Configure entry →
the test step). Nothing new to click: **on timeout the test now
automatically:**

1. re-sends the base read as an *acknowledged* write — if the unit
   refuses writes from unpaired devices, its refusal becomes a log
   line instead of silence;
2. if the refusal is a security error, tries to **pair** the Pi with
   the unit;
3. if pairing succeeded, re-asks for the data once.

Then paste every line starting with `security probe:` or `pairing:`.

**How to read the outcome (the log tells you which world you are in):**

| Log shows | Meaning | Next |
|---|---|---|
| `security probe: verdict att-error-security` + `pairing: succeeded` + `security probe: answered after pairing` | **Case closed** — the unit gates writes on pairing; the Pi is now paired | §2 (persistence check) |
| `verdict att-error-security` + `pairing: failed: … retry while the panel shows bLE` | the unit only accepts pairing in its pairing mode | §2's choreography, then re-run §1 |
| `verdict att-accepted-silent` | the unit ACCEPTS our write but its engine ignores it — the gate is inside the firmware, not the link | §3 (phone capture) |
| `verdict att-error-permission` / `att-error-handle` | our write pipe is wrong at the GATT layer (contradicts the app analysis — needs cross-check) | paste everything + the §0 lines; wait for the follow-up spec |
| `local-failure` | a local stack problem on the Pi | paste the chain; §3 still valuable |

## §2 The "bLE" choreography (only if §1 says the pair attempt failed)

The unit has a pairing mode (the manual gesture): **press and hold the
panel button for 5 seconds until the display shows "bLE" blinking once
per second** (same gesture as when you first added the device in the
vendor app; on sibling products the window lasts about a minute).

1. With one hand, put the unit in "bLE" mode.
2. With the other hand (or a helper), run the connection test again
   (§1). The pair stage runs inside the window.
3. Watch the log: `pairing: succeeded` + `answered after pairing` =
   done. Release the button.

Then, **without** touching the button again, run the test once more
(the persistence check, X3): if it still answers, the pairing is
permanent and you are finished. If it goes silent again, the unit does
not re-encrypt on reconnect by itself — enable the integration's new
**"Pair on every connect"** option (Settings → Devices & Services →
Aqua Temp BLE → Configure) and run the test one last time; keep the
option enabled only if that test succeeds.

## §3 The phone capture (E5 — decisive, still no terminal)

If §1/§2 do not close the case, one capture of the vendor app's own
session settles what the phone does that we do not:

1. Android Settings → About phone → tap "Build number" 7× → Developer
   options → enable **Bluetooth HCI snoop log**.
2. Toggle Bluetooth OFF/ON, open the vendor app, connect to the unit,
   let the home screen show its values, change nothing, close the app.
3. Developer options → **Bug report** → take it → in the report find
   `btsnoop_hci.log` (under `data/misc/bluetooth/logs/` or the MIUI/
   OEM debug folder) and send the file.

We read it (Wireshark, `btatt` + `btsmp` filters) for the four
security-relevant facts:

- an **`LE Pairing`/SMP exchange** or an **`Encryption Change`** event
  right after the connection → your phone's session runs **encrypted**
  → the pairing theory confirmed vendor-side;
- the app's **Write Command to ff03** (`63 03 00 07 00 2d 3c 54`) and
  whether a 95-byte FC03 answer comes back on ff02;
- whether the app re-sends its request and how often;
- the negotiated MTU and connection parameters (baseline).

## §4 Optional cross-check from a second phone (nRF Connect)

If you have another Android phone (or can install a free app on any
phone that is NOT your daily vendor-app phone): install **nRF Connect**
(Play Store), kill the vendor app everywhere, connect to
`BlueNRG-C0941C`, tap characteristic `ff03`, and write the hex value
`63030007002D3C54` (write type "Write Command" first, then "Request").

- **Silence from the fresh phone** → a bond-free Android fails exactly
  like our Pi → the gate is pairing, cross-platform. Strong.
- **An answer arrives** → bond-free phones CAN write → the pairing
  theory weakens; the difference is Pi/BlueZ-specific → §3 becomes
  the priority.

## §5 Report template

```
1. §0 greps pasted? (y/n) — ff03 properties line:
2. §1 verdict line (verbatim):
   pairing outcome line (verbatim):
   answered after pairing? (y/n/—)
3. §2 needed the bLE gesture? (y/n/—) … result:
   persistence retest (no gesture): answered? (y/n)
   pair-on-connect option enabled? result:
4. §3 btsnoop_hci.log sent? (y/n) … app showed live values? (y/n)
5. §4 attempted? result:
```

## What each step proves

| Step | Proves | Status |
|---|---|---|
| §0 | ff03's advertised properties + which resolution path ran | [P] once pasted — free |
| §1 | whether the unit's write refusal is a security gate (ATT error), a permission/handle problem, or acceptance-with-ignoring; end-to-end fix if pairing succeeds | [P] one test run |
| §2 | whether the unit requires its pairing mode; whether the bond persists across reconnects | [P] one or two runs |
| §3 | whether the vendor app's session is encrypted (the vendor-side proof); the app's true write/answer pattern on this unit | [P] definitive |
| §4 | whether the gate reproduces on a bond-free Android | [P] one run |

## Discipline

- Vendor app killed + phone Bluetooth off for every Pi test.
- Do not power-cycle the heat pump between §1 and §2 (comparable state).
- Never enable "pair on every connect" before §1/§2 produced their
  evidence — the option exists to be earned (contract 011 §1.5).
- Whatever happens: paste the `security probe:` / `pairing:` lines
  BEFORE any further code change — the decision matrix
  (contracts/security-probe.md §6) is binding for the follow-up.