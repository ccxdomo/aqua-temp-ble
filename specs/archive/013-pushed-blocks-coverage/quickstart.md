# Quickstart 013 — how to verify the push-plane spec

Audience: the owner (no terminal — HA UI, phone, panel only) and the
developer reading pasted logs. Every step states what it proves.
Single-connection rule applies to every live step (spec 006 §9): the
vendor app must be killed / phone Bluetooth OFF, or nothing connects.

## §0 — Free evidence from the log you already have (2 min, optional)

Download the full log from Settings → System → Logs (the panel keeps
only WARNING+ — spec 012's lesson). Search the file for:

| Search | What it proves |
|---|---|
| `security probe: verdict` | `att-accepted-silent` — the request-path verdict (already pasted; confirms world (a) of research 013 §4 Q4's routing) |
| `idle window before first request` | pushes arrived BEFORE our first TX ⇒ stream is subscribe-triggered/periodic (E2's answer, likely already in the file) |
| `block push observed` | per-start counts + timing — refine the cadence model (research §5.4) |

## §1 — One connection test = the 2001-2045 capture (the big one)

After the Coder lands FR-1/FR-3 (build 0.9.0):

1. Vendor app killed, phone BT off. HA → Settings → Devices & Services →
   Aqua Temp BLE → Configure → re-run the connection test.
2. **Expected: the test now SUCCEEDS** (mode push) — the log shows:
   - 4 WARNING lines `block push captured: start=… hex=6310…` — one per
     distinct start (1001, 1046, 2001, 2046);
   - `push harvest: T-block captured (start=2046 ×N); T-window raws:
     2046=32765 2047=14x 2048=14x 2049=14x 2050=13x 2051=15x`.
3. Paste those WARNING lines back. **The 2001-2045 hex is the prize**
   (U-1): the never-decoded block. The Architect decodes it and the
   follow-up spec maps it (S/O/D/P homes? base-control mirrors?).

What it proves: the pushes carry the T-block live [P→production]; the
test passes against a unit whose request path is dead (US-1); the
2001 capture closes the biggest evidence gap.

## §2 — The calibration run (pins T02/T03 — the YES/NO of this spec)

1. With the phone: open the vendor app, connect, open the parameter
   view (password "66") — or just the home screen if it shows inlet/
   outlet. **Photograph the T-family values** (T01 Suction shows
   3276.5 — the sentinel — that is the anchor). Note the time.
2. Kill the app; phone BT OFF. Within ~2 min, re-run the HA connection
   test.
3. Compare the success summary's T-window raws with the photo:
   - 2046 = 32765 ↔ T01 absent ✓ (the invariant anchor);
   - the five live raws ↔ T02-T06 in the photographed order, within
     ~±1.0 °C (thermal drift over ≤ 2 min on an idle unit).
4. Match → check "values matched" in the confirm step → restart → the
   temperature entities appear (inlet, outlet, ambient, coil, exhaust;
   suction shows `unknown` — correct, sensor absent).
   Mismatch/permutation → report it; nothing is confirmed; the mapping
   is corrected in `registers.py` (one line) and the test re-runs.

What it proves: the T-window member order (research U-2) — this is the
step that turns [A] into [P live] for "inlet/outlet are in the pushes".

## §3 — E5: the control-path capture (unchanged from specs 009/011)

One Android HCI snoop of a vendor-app session on THIS unit
(developer options → bug report → `btsnoop_hci.log`; Wireshark,
filter `btatt`). Answers: does the unit answer the app's FC03 (world
(a) — H-APP-GATE is central-keyed; pairing becomes interesting) or is
the app push-fed too (world (b) — control needs the write-path
investigation)? This is the CONTROL question; monitoring (§1-§2)
does not depend on it.

## §4 — Watch it live (after §2)

HA → the device page: the temperature entities update at push cadence
(observed pushes: ~every 0.7-4 s per block — far faster than the old
30 s poll). The `push_stream` diagnostic shows per-start counts and
the last push age. If it goes stale (stream stopped — U-3), the
entities honestly go `unknown`/unavailable and the log says so; the
next cycle reconnects.

## §5 — Report template

```
Build: 0.9.0 (marker line from the log)
§0 grep results: verdict=…; idle window=…; push count=…
§1 WARNING lines (4 hex lines + T-block summary): …
§2 photo values (T01-T06 + time): … / T-window raws: … / confirmed: yes|no
§4 entity values after restart: inlet=… outlet=… ambient=…
push_stream diagnostic: …
```