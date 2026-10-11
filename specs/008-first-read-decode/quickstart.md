# Spec 008 quickstart — verify the first-read fix and capture the decision data

For the pool owner. After the Coder ships the spec-008 build, one session
at the panel is enough to either celebrate or produce exactly the
evidence the follow-up needs. Each step says what it proves; [P]/[V] tags
per the repo convention.

## Before you start

- Phone Bluetooth OFF, vendor app killed (the unit accepts a single BLE
  link — spec 006).
- Home Assistant logs at INFO for `custom_components.aqua_temp_ble`
  (Settings → System → Logs). If you can, capture with timestamps:
  `journalctl -f -u home-assistant@… | grep -i aqua` in a terminal, or
  HA's Settings → System → Logs → download full log.

## §1 Run the connection test once [decides E1, E2, E3]

Re-run the integration's connection test (Settings → Devices → Add
Integration → Aqua Temp BLE → your device).

**Expected outcome A — the test SUCCEEDS** (research §7 E1 = yes: the
unit's stream includes a start-7 block frame). The confirm page shows
live values (power/mode/temperatures). Done — no further steps.

**Expected outcome B — the test still fails at first-read.** Now read
the log; the spec-008 build replaces "got 0 bytes" with an honest census
message, e.g.:

```
no matching response within 8.0s (received 12 frame(s) / 1188 bytes;
blocks seen: 1001, 1046, 1091, 1136, 2001, 2046; none for start 7)
```

plus census lines like:

```
block push observed: start=1001 qty=45 len=99 crc=OK (waiting for start 7)
block push observed while idle: start=1001 qty=45 len=99 crc=OK
```

Answer three questions from the log:
1. **Is a `start=7` block ever observed?** (yes → the read should have
   matched — file the log; no → E1 = no.)
2. **Do `while idle` census lines appear BEFORE the first TX?**
   (yes → the stream is periodic [P]; no → it is triggered by our
   request.)
3. **How many distinct block starts appear per ~4 s?** (the cycle model:
   6 blocks ≈ 4 s [research §2.7].)

## §2 If no start-7 block: read the GATT line [E4, first half]

In the same log, find the line from connect time:

```
GATT enumeration 08:06:A6:C0:94:1C: service 0000ff01-…: …
GATT enumeration 08:06:A6:C0:94:1C:   char 0000ff03-… properties ['…']
```

Copy the **ff03 properties line** (and ff02's). This tells us whether
ff03 advertises `write`, `write-without-response`, or both — i.e. whether
our unacknowledged write (`without response`, vendor parity) can even be
accepted by this module. [P once captured]

## §3 Probe block 1001 [E4, second half]

Use the probe (the "Probe registers" button / the
`aqua_temp_ble.probe_registers` service with `block: "1001"`). The
spec-008 probe decodes the block frame into per-register lines
(`reg 1001 = …`).

- If a **block-1001 frame arrives within ~1 s of the probe request**
  (faster than the ~0.67 s/frame stream cadence would predict) → our
  writes DO reach the unit and trigger answers [P].
- If the block-1001 data arrives but at stream cadence only → the write
  path is suspect → §4 becomes decisive.

## §4 The definitive capture: one vendor session on the phone [E5]

This is the one experiment that answers EVERYTHING (the app's request
set, the answer shapes, where base data comes from) on THIS unit:

1. On the Android phone: Settings → About → tap "Build number" 7× →
   Developer options → enable **Bluetooth HCI snoop log**.
2. Toggle Bluetooth OFF/ON, open the vendor app, connect to the unit,
   wait until values display (3-5 s), change nothing, close the app.
3. Developer options → **Generate bug report** (or `adb bugreport`) →
   extract the archive → find `btsnoop_hci.log` (FS/data/misc/bluetooth/logs).
4. Send that file (it contains only BLE traffic).

We read it with Wireshark (filter `btatt`): every GATT write to ff03 and
every notification on ff02, with timestamps — the app's exact session.

## §5 Report template (paste back with the log)

```
1. Test result: success / failure at first-read
2. The full "no matching response …" line (census message)
3. 10-20 "block push observed" lines (with timestamps if possible)
4. The GATT enumeration lines (ff02/ff03 properties)
5. Probe 1001: did per-register lines appear? how fast after the click?
6. btsnoop_hci.log attached? yes/no
```

## What each step proves

| step | proves | status |
|---|---|---|
| §1 | whether a start-7 block exists in the stream; periodic vs triggered; the cycle model | [P] after one run |
| §2 | ff03's advertised write properties (our write mode is property-gated) | [P] once pasted |
| §3 | whether our request reaches the unit and triggers an answer | [P] from timing |
| §4 | the vendor app's exact session on THIS unit — request frames and response shapes | [P] — definitive |

## If §1 shows outcome B

Do not power-cycle or re-try blindly: the census message IS the result.
With `blocks seen: [the six high blocks]; none for start 7` and no
`while idle` lines, the follow-up spec (written from your §2-§4
captures) changes the base-read strategy with evidence — probe-style
block queries, a longer first-read window, or a write-path fix. Nothing
is guessed.