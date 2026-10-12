# Quickstart 007 — Measuring where the connect time goes (runbook)

The production fact: the vendor app shows values in **3-5 s**; our
connection test takes **>40 s and fails**. Research 007 says the >40 s is
almost certainly **two 20 s connect windows expiring** (research §8) and
that the leading root causes are **host/stack-level** (research §9) —
but that verdict must be *measured*, not assumed. This runbook converts
your next failed run into a root-cause class. No reverse-engineering tools
are required until step 6 (optional).

**Setup before every step:** phone Bluetooth OFF, vendor app force-closed
(the unit accepts one BLE link — research 006 §2.2), unit powered on, HA
host within a few meters of the PAC.

---

## Step 1 — One instrumented failing run (5 min) — the stage truth

After the Coder ships spec 007 FR-1/FR-2/FR-3, enable debug for our
integration only:

```yaml
logger:
  logs:
    custom_components.aqua_temp_ble: debug
```

Run the connection test **3 times in the same session** (a single success
is itself data — note it). Then collect from the log:

- `connecting to 08:06:A6:C0:94:1C: up to 2 connect attempt(s) …`
- `connect to 08:06:A6:C0:94:1C failed at stage connect after … s: …`
- `connection test to 08:06:A6:C0:94:1C failed at stage … after … s …`

**What it proves:**
| You see | Meaning |
|---|---|
| `failed at stage connect after ≈40-43 s` + `BleakNotFoundError … <- TimeoutError` | **Connect-stage stall confirmed** (research §8): link/discovery never completes inside the 20 s windows. Proceed to steps 3-4. |
| `connect summary` line with `connect` > 15 s on a *success* | Link/discovery is slow but completes — host-level slowness; steps 3-4 still apply. |
| failure at stage `notify` / `write-char` / `first-read` | The stall is **not** the connect: different bug class, fixable in code — capture the chain and stop here; it goes to a new spec. |

## Step 2 — `bluetoothctl` timed differential (3 min) — integration path vs host

From the HA host shell:

```bash
time bluetoothctl connect 08:06:A6:C0:94:1C
bluetoothctl info 08:06:A6:C0:94:1C   # note "Address type" (expect Public)
bluetoothctl disconnect 08:06:A6:C0:94:1C
```

- Connects in a few seconds → the host stack CAN reach the unit; the
  failure interacts with the integration's path (report both logs).
- Hangs ~20 s / fails → host-level (research §9 H-A/H-B/H-C territory):
  steps 3-4 name it.
- Note the address type: anything other than `public` is a finding
  (research §9 H-B).

## Step 3 — `btmon` HCI trace during one failing test (10 min) — the decisive capture

```bash
sudo btmon > /tmp/btmon_test.log &       # start trace
# run the HA connection test once
sudo pkill btmon
grep -aE "LE Connection Complete|LE Create Connection|Exchange MTU|Read By Group|Read By Type|Disconnect" /tmp/btmon_test.log | head -60
```

**Decision table (research §9):**

| btmon shows | Root cause | Consequence |
|---|---|---|
| `LE Create Connection`, **no** `LE Connection Complete` in 20 s | **H-B** — the link never establishes (adapter/RF/address) | host-level: adapter placement, USB dongle/extension; integration unchanged |
| `LE Connection Complete`, then `Read By Group Type` repeats/never completes | **H-A** — GATT discovery stall (BlueZ cache cold every time, research §3) | host/module-level; capture the trace as the evidence |
| `LE Connection Complete`, `Exchange MTU`, link drops right after | **H-C** — MTU-exchange firmware quirk | host/module-level; report to vendor with trace |
| everything completes, yet the HA test failed | the failure is NOT on the air — re-check step 1 logs / D-Bus layer | send logs to the developer |

## Step 4 — HA-stopped differential (5 min) — scanning contention

HA's Bluetooth discovery scanner runs continuously; on a single-radio
host (built-in Raspberry Pi Bluetooth), scanning and connecting contend
(research §9 H-B variant). Stop HA (or disable its Bluetooth integration
temporarily — Settings → Devices → Bluetooth), then:

```bash
time bluetoothctl connect 08:06:A6:C0:94:1C
```

Then restart HA. **Connects only while HA is stopped → scanning contention
is a major contributor → the practical fix is host-level (USB dongle or
relocating the adapter), not integration code.**

## Step 5 — Wedge clearing (5 min) — only if steps 2-3 also fail

A previously cancelled attempt can leave the unit's single-link slot
occupied (spec 005 §4.3):

1. `bluetoothctl remove 08:06:A6:C0:94:1C`
2. Power-cycle the PAC (mains off 30 s).
3. Wait ~30 s, re-run step 2, then step 1.

**Connects after power-cycle only → H-D wedge.** Note: with the vendor app
installed on a phone with Bluetooth ON, the app aggressively re-takes the
link — keep the phone's Bluetooth off for HA tests (research 006 §2.2).

## Step 6 — (optional, decisive for the vendor side) `adb logcat` one vendor session

Answers three open questions in one capture (research §2, §6, §7):
the role of the app's 2 s constant, whether the app requests an MTU, and
whether values render before the first read (cache).

```bash
adb logcat -c
adb logcat -v time | grep -aE "日志|BlueNRG|MTU|OnMtuChanged|63030007002D3C54"
```

Phone Bluetooth ON, open the app, select the PAC, watch ~60 s. Read:
- timestamps of `设备已连接` (connected) → `发现服务` (services) →
  `指令已发送` (frame sent): **the vendor's per-stage durations on the same
  unit** (research §1.5).
- `63030007002D3C54` occurrences: **once** = the 2 s constant is a delay;
  **repeating ~every 2 s** = it is a poll cadence (research §2 reading (c)
  — spec 006 FR-1 then gets re-worded in a follow-up, harmless in code).
- any `OnMtuChanged` / MTU line with a value: the app requests a bigger
  MTU (research §6 G-5).
- values visible on screen **before** the first `指令已发送`: the app
  renders stored data first (research §7 G-7 — re-frames the 3-5 s, does
  not change the fix path).

## Step 7 — Report template

Paste into the next issue/spec discussion:

```
HA build: <spec-007-era integration?>
Step 1: <the 3 log lines of one failed test — or N successes>
Step 2: bluetoothctl connect: <duration / error> ; Address type: <…>
Step 3: btmon pattern: <one row of the decision table>
Step 4: connect with HA stopped: <duration / error>
Step 5 (if run): power-cycle changed anything: <yes/no>
Step 6 (if run): <logcat excerpt / not run>
RSSI shown by HA: <…>   distance PAC↔host: <…>   host adapter: <built-in / USB>
```

---

## What NOT to do

- **Do not raise timeouts, budgets or attempts again** to "give it more
  time" — the 70 s budget already cannot rescue a stalled connect
  (research §8 corollary); the next change must come from a measured
  root cause.
- **Do not conclude "unit refuses new centrals"** — the unit accepts the
  phone's connect in 1-2 s (research §9 frame); the asymmetry is
  host-side until btmon proves otherwise.
- **Do not clear BlueZ state between the paired runs of steps 1-4**
  (`remove`/power-cycle) — you would erase the very state you are
  measuring; wedge clearing is step 5 only.

## Status of this runbook

| Claim | Status |
|---|---|
| >40 s = 2 × 20 s connect-window signature | **[P arithmetic]**, confirmation = step 1 log (research §8) |
| Vendor 3-5 s = live session (no value cache needed) | **[P budget]** (research §7) |
| H-A/H-B/H-C ranked causes, all host/stack-level | **[H]** — steps 2-4 decide (research §9) |
| 2 s constant's role (delay vs cadence) | **[V]** — step 6 decides (research §2) |
| App requests MTU? | **[V]** — step 6 decides (research §6) |
| App renders cached values? | **[V]** — step 6 decides (research §7) |