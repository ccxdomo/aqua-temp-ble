# Contract 015-B — Write security: the allowlist extension rule

Normative rules governing ANY future change to the write allowlist
(`registers.ALLOWED_WRITE_BASE`, guarded by `ensure_writable` — the
single write funnel, spec 003 §6/FR-9). This spec changes NOTHING in the
shipped guard; this contract records the rule that governs the future
spec that will.

## §1 Current state (unchanged by spec 015)

- Allowlist: `{8 Power, 13 Mode, 14 SetCurrentTemp}` — base-block
  registers whose names are proven by the 45-name APK list [P apk,
  research §5.1]; `{19, 20, 21}` join only behind the default-off
  `WRITE_MODE_SETPOINT_TOO` switch.
- Everything else is refused by `ensure_writable` with a ValueError —
  including the entire pushed plane 1001-2090 and the never-pushed
  blocks.
- Rationale (spec 003 §6, restated): installer/safety parameters live in
  the high plane — H04 "Exhaust Temp. High" 105.0 °C sits at reg 1021
  [P panel]; an erroneous write there can damage the unit or prevent
  startup. The 1001-2090 plane is treated as HOSTILE-to-writes unless a
  door below is passed.

## §2 The three-door rule (all three required, in order)

### Door 1 — Vendor-proven write address

The register address the VENDOR app itself writes for the control in
question, evidenced by:

- the logcat oracle: the `参数选择修改 ----- value = … index = …
  address = …` lines printed by the app's own write path on every
  control write [P apk — FINDINGS §12.2, research §5.3], captured while
  the owner performs the exact control action; or
- an HCI-level capture of a vendor session showing the FC16 write
  frame's register field.

No other evidence opens this door: NOT a read diff (contract 015-A
§8), NOT register adjacency, NOT "it worked on another model".

### Door 2 — Understood delivery gate

Our writes are currently ATT-accepted but Modbus-silent
(att-accepted-silent [P — six runs]). A new write target may ship only
when the delivery question is answered — i.e. the same write from Home
Assistant is observed to reach the firmware (the E5 / pairing /
revival world; the spec 011 levers and the contract 014 §5.1 revival
WARNING are the observation instruments). An allowlist extension with
an undelivered write path adds risk with zero capability.

### Door 3 — Its own spec

The extension ships as a dedicated spec with: the address+value
semantics (from Door 1), the accepted-range rules (clamped by the
unit's own bounds — base 22-25 today, or the pinned pushed bounds of
contract 015-A §5), the confirmation read path (write → debounced
refresh → device-confirmed state), and tests. `ensure_writable`'s error
message updates with the new addresses.

## §3 Explicit prohibitions

1. **No write to 2012/2034 or any 2001-block register** on the strength
   of read correlations — they are unpinned reads; their write semantics
   are unknown (contract 015-A §8).
2. **No write to the 1046 ladder** (the R-family/defrost/EEV thresholds)
   even if the setpoint read home lands there — the ladder's members are
   interleaved with safety thresholds (H04's window is one block away);
   only the Door-1 oracle may separate the editable members from the
   dangerous ones, member by member.
3. **No generic write surface** — no probe/service/config option that
   writes an arbitrary register (the spec 003 §6 "no generic write
   surface" rule, restated for the pushed plane).
4. **No allowlist change rides on this spec 015** — FR-12/AC-6 pin the
   shipped guard byte-identically.

## §4 The oracle's known unknowns (recorded for the future spec)

- The vendor's status/control model addresses power/mode/setpoint at
  base 8/13/14 [P apk name list] — the base addresses are the
  first-line candidates for the app's writes.
- The app demonstrably also writes the high plane (the time-sync frame
  to register 1267, FINDINGS §11.1) — so Door 1 may return high-plane
  addresses; the guard then extends to EXACTLY those addresses, never
  the window around them.
- The control-parameters page's editable members (Cooling set / Heating
  set / Auto. Set …) are written by the app [P apk label list] — the
  oracle will reveal their addresses in the same logcat session
  (quickstart §2); that evidence belongs to the future spec's record,
  not to this one's implementation.