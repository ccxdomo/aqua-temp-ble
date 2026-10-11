# Spec 010 — Quickstart: verify the resolution, read the log, capture the app

Runbook for the spec-010 state. Everything here is evidence work — no
code has to change to execute steps 0-3 (the resolver fix ships in
parallel and changes none of these steps' commands).

## 0. E4' — the two-grep verdict on ff03's properties (do this FIRST)

The ce41aa5 production run (the spec-009 census build) already logged
the GATT table and the write-char resolution. The pasted log lives in
the project chat / HA log. Run both greps (any order):

```bash
# 1. the advertised properties of every characteristic on THIS unit:
grep -a "GATT enumeration" <ha-log-or-pasted-log>

# 2. which char our client picked and what it advertised:
grep -a -A2 "using write characteristic" <ha-log-or-pasted-log>
grep -a -B2 -A2 "last-resort" <ha-log-or-pasted-log>
```

Decision table (research 010 §6.3):

| ff03 advertises | Meaning | Next |
|---|---|---|
| `write` or `write-without-response` | The app CAN write ff03 at the ATT layer — C1's "wrong pipe" is fully dead; silence is C3 (app also fails?) or C5-class timing | go to step 2 (E5) — decisive |
| **no write property at all** | The vendor app — property-blind (research §1) — **cannot control this unit either**; its writes would fail at the Android stack (`日志:指令发送失败`) | push-only firmware (C3) becomes the leading theory; still confirm with E5 (the app on this unit should visibly fail to control), then promote Track B in spec 011 |

Record the result in README "next steps" (FR-5).

## 1. The standalone probe (unchanged commands, updated expectations)

`tools/session_probe.py` (spec 009) — its phases now test the *proven*
target char:

```bash
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --phases L   # L: push cadence, timestamps
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --phases B   # B: 1 TX + 15 s window
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --phases P    # P: 2 s cadence, 30 s
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --phases Q    # Q: block query on ff03
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --phases W    # W: write-WITH-response on ff03
python3 tools/session_probe.py --address 08:06:A6:C0:94:1C --subscribe-all  # C6 sweep
```

What each result means now (H1/H2/H3 are dead, so):

- Phase Q/W answered → the engine reacts to ff03 after all; diff
  timings vs pushes (C5) → spec 011.
- Phase W returns an ATT error → ff03's permissions reject writes at the
  ATT layer → same column as step 0's "no write property" row.
- Everything silent → C3; the app almost certainly shares the fate →
  step 2 settles it.

## 2. E5 — one HCI snoop of the vendor app on THIS unit (the decisive capture)

On any Android phone with the vendor app installed and paired to the
PAC:

```bash
# 1. Enable Bluetooth HCI snoop log:
adb shell am broadcast -a com.android.bluetooth.BluetoothAdapter.action.SNOOP_LOG_ON? # vendor-dependent;
#   Settings → Developer options → "Bluetooth HCI snoop log" ON on most ROMs.

# 2. With the phone next to the PAC: open the app, connect, open the
#    device page, change NOTHING for 30 s, then toggle power OFF/ON once.

adb pull /sdcard/MIUI/debug_log/btsnoop_hci.log .   # path varies: /data/misc/bluetooth/logs/btsnoop_hci.log
wireshark -r btsnoop_hci.log -Y btatt               # or: tshark -r ... -Y btatt -T fields ...
```

Expected lines (research 010 §2 — read the capture against these):

1. `Write Request/Command` → handle of ff02's CCCD, value `01 00`
   (subscribe), within ~1 s of connection completion;
2. ≈2 s later: **Write Command to ff03's handle**, payload
   `63 03 00 07 00 2d 3c 54`;
3. then (if the app is healthy here) `Handle Value Notification`s on
   ff02 carrying `63 03 5a …` (the 95-byte base-block answer) — or the
   six query blocks only, same cadence as ours;
4. after the power toggle: `Write Command` on ff03 with a `63 10 …`
   frame, then a `63 10 …` echo notification.

The four possible verdicts:

| Capture shows | Verdict | Spec 011 does |
|---|---|---|
| App gets FC03 answer on ff02 | our delivery differs below GATT (timing/conn params) | diff ATT timelines ours-vs-app; fix delivery |
| App writes ff03, gets nothing (and its UI shows stale/no data) | **C3 push-only confirmed** | Track B primary: harvest+calibrate push stream |
| App writes some OTHER handle/char | extraordinary — contradict the Dart disassembly → DEX smali session (research §6.4) | re-open H-class analysis |
| App never subscribes/writes (session fails early) | unit refuses the app too | the "bLE"/config-mode experiment (spec 006 quickstart §3) first |

## 3. Verify the shipped resolver (after the Coder lands FR-1..3)

```bash
python3 -m pytest tests/ -q          # AC-1..AC-3
python3 -c "import sys; sys.path.insert(0, 'custom_components/aqua_temp_ble'); import const; \
  print(const.UUID_WRITE); print(const.UUID_WRITE_BUCKETS)"   # FR-1 sanity
```

Then one live connection test from the HA config flow (the census build
prints the same lines as before — AC-4):

```
GATT enumeration: …
using write characteristic 0000ff03-0000-1000-8000-00805f9b34fb (properties=[…])
```

If the census build already showed ff03, the observable behaviour is
identical — by design (research §6.2: the fix hardens the *logic*, not
the runtime outcome on this unit).