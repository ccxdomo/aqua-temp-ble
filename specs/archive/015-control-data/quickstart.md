# Quickstart 015 — the control-data experiments (operator runbook)

Everything here runs with the shipped 0.10.0 build — no code changes, no
terminal needed except where noted. The four captures per run are the
`block push captured: start=… hex=…` WARNING lines (Settings → System →
Logs, or `adb logcat`; the operator's log page keeps WARNING+ [P —
spec 012 §4]).

**Ground rules for every run**
- One central at a time: the vendor app must be dead / phone BT OFF
  while the Pi is connected (spec 006 §9).
- Phone BT ON only for the app actions; the captures are HA's own
  connection tests (Developer Tools → the integration's "connection
  test" / the config-flow test — a fresh connect produces fresh
  first-push WARNING lines for all four blocks [spec 013 FR-1]).
- Record WHAT THE APP DISPLAYS before every capture: on/off state,
  mode, setpoint value (a photo of the home screen is ideal — it is
  the calibration anchor, research §8 U-8).

---

## §1 The toggle-and-diff calibration (THE experiment — pins power/mode/setpoint)

### Step 0 — baseline (5 min)

1. Open the app; note/photograph: power state (ON/OFF), mode, target
   temperature.
2. Phone BT off → run one connection test (HA) → save the four
   `block push captured` hex lines → label them "baseline".

### Step P — the power diff (15 min)

1. Phone BT on → in the app, slide the power toggle to the OTHER state
   → wait ~1 min (let the app refresh its display).
2. Phone BT off → run one connection test → save the four lines →
   label "power-toggled".
3. DIFF against baseline: any register that flipped 0↔1 (or changed
   at all) in blocks 1001/1046/2001/2046 is the power candidate.
   Expected candidate: reg 2012 (research §2.3); a 1001/1046 flip
   would also be decisive (they were static so far).
4. Phone BT on → slide the power BACK → phone BT off → one connection
   test → "power-reverted". The candidate must follow the revert.
5. A register that flipped AND reverted = the power home is PINNED
   (contract 015-A §2.1). Nothing flipped = the power state is NOT in
   the pushed plane (spec FR-4's negative proof — also a first-class
   result; see §1 outcome).

### Step M — the mode diff (may be refused)

1. In the app, try changing the mode. If the app refuses ("heat-only
   mode cannot be changed" — expected on this unit [P apk]), record
   the refusal and skip: the mode read home stays [V] (its candidate
   remains reg 2034; the mapping may stay [A] — spec FR-7/D-3).
2. If a mode change IS accepted: capture → diff → revert → capture →
   diff, exactly as step P. A register that moved with the mode change
   and followed the revert = the mode home.

### Step S — the setpoint diff (15 min)

1. Note the DISPLAYED setpoint first. Compare with the five ladder
   candidates: 29.0 / 15.0 / 27.0 / 35.0 / 27.0 °C
   (regs 1046 / 1049 / 1051 / 1055 / 1068). If the displayed value
   matches NONE of them, the setpoint is not in the pushed plane
   (decisive, research §4 Q5) — record and stop here.
2. If it matches: in the app, change the setpoint by +0.5 °C → capture
   → diff (the matching ladder member must move +5) → revert → capture
   → diff (must move back). The member that followed = the setpoint
   home is PINNED.

### Outcome

- **Pinned** (any quantity): open the confirmation gate (Phase B,
  spec FR-5..FR-8): the option `push_control_confirmed: true` on the
  entry after the Coder's build lands, then the climate appears
  read-only.
- **Silent** (a quantity's diff moved nothing): that quantity has no
  pushed home [P-grade]; the climate (or its key) stays unavailable
  and the follow-ups are §2/§3 below.

## §2 The logcat write-address oracle (the vendor's write targets)

Requires adb on the phone (or `adb logcat` over the owner's setup):

```bash
adb logcat -c
adb logcat | grep -aE "参数选择修改|address|6310|指令"
```

Then, IN THE APP (one action at a time, ~20 s apart):
1. slide the power toggle;
2. change the mode (if allowed);
3. change the setpoint ±0.5 °C.

Each action prints `参数选择修改 ----- value = V index = I address = A`
— WRITE DOWN the value/address pairs per action (they are the
vendor-proven write addresses; the future write spec's Door-1 evidence,
contract 015-B §2.1). The app also logs its own outgoing frames
(`日志:指令 6310 …`) — the FC16 write frames appear there too.

Cross-check: the power WRITE address and the §1-P READ home should
agree (or mirror); a disagreement is itself evidence — record both.

## §3 E5 — the HCI snoop of one vendor session (decides the app's source)

Unchanged since spec 009 §3 (the definitive picture of what the phone
receives): Developer options → enable Bluetooth HCI snoop → ONE app
session (connect + open the home screen + toggle power) → bug report →
extract `btsnoop_hci.log` → look for: does the unit answer the app's
`63030007002D3C54` with a 95-byte FC03 response (`63 03 5A …`)?

- YES → world (a): the unit answers the phone's base read (identity/
  bond-keyed gate); the app's home screen reads base 8/13/14. The Pi's
  request path needs the pairing/identity lever (spec 011).
- NO → the app is fed by the burst like us — its control display
  comes from a hidden mapping (§1 will have found it) or from
  1091/1136 (§4).

## §4 The 1091/1136 probe (the never-pushed blocks — S/O/D/P homes?)

Developer Tools → Actions → `aqua_temp_ble.probe_registers`, block
`1091`, then `1136` (and `2001` as the control case — spec 014's
U-6', still unrun):

- **Direct block answer** (`block push captured`-style frame with
  start=1091, or a probe log with decoded registers): the
  never-pushed plane is readable — the S/O/D/P families (and possibly
  the D-family digital states) become calibratable via the same
  diff protocol.
- **Burst-only** (the probe times out but the census shows the usual
  1001/1046/2001/2046): 1091/1136 are unreachable without E5 —
  record the census line either way.

## §5 Verification after Phase B lands (the climate)

1. Set the option `push_control_confirmed: true` (per-entry) —
   `climate.aqua_temp_heat_pump` appears.
2. Compare against the app: hvac_mode matches the on/off (+mode)
   display; target temperature matches the setpoint display; the
   entity offers NO controls (read-only, spec FR-8).
3. Toggle the unit in the app → the next HA cycle (≤ poll interval +
   freshness window) reflects the change — device-confirmed, never
   optimistic.
4. If anything mismatches: close the gate (option false), capture the
   four WARNING lines again, and re-run §1 — a mismatch is a
   miscalibration, not a bug to mask.