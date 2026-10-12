# Spec 018 — Mode values: the climate shows `unknown` even while the heat pump runs

Date: 2026-10-12
Repo state: main @ b81b604, manifest 0.13.0
Status: **proposed (Coder-ready)**
Depends on: specs 013/014 (the push plane), 015 (the pinned control homes
2011/2019 + the read-only climate), 016 (the write plane 1011), 017 (the
controlled climate, `hvac_modes = [OFF, HEAT]`).
Evidence: `research.md` (this directory) — every FR below cites it.

## 1. Problem

The control works and is proven live: the owner's buttons start and stop
the real machine (the confirmation cycle's mirror cross-check passes both
ways, research 017 §1 + the owner's physical confirmation). **But the
climate tile displays `unknown` while the machine is ON** — the owner's
production observation, quoted verbatim: *« et HEAT ne s'affiche pas »,
« ca met aussi unknown »* — with the log line:

```
Unknown pushed Mode value 0 (reg 2019) — update registers.PUSHED_MODE_VALUE_TO_NAME ...
```

The root cause is PROVEN and narrow (research 018 §2/§3): the shipped
display decode requires the mode mirror 2019 to carry a **mapped** value
whenever power is on (contract 015-A §5). Our proven, deliberately
minimal power write (1011, contract 016 §4 — the mode is excluded and
its write would be an unpinned pair-write, research 017 §5) updates the
power mirror **2011** but never the mode mirror **2019**: a fresh
post-write 2001 block reports `2011 = 1, 2019 = 0`, the value 0 is
unmapped, and the decode renders `unknown` — for a machine that is
audibly heating. The 015-era assumption "2019 = 4 whenever on" (D-3)
held only in the app-flow world (the 08:31 diff-revert capture) and is
FALSIFIED in our control world.

The unit is **heat-only** (H06 [P panel]; the vendor's own app refuses
mode changes on it [P apk, research 017 §5]). Power on ⇒ the machine
heats — there is no other mode it can be in. This spec makes the
display follow the PROVEN power mirror and demotes the mode mirror to
a diagnostic, and it silences the two log nuisances the owner observed
(the now-wrong WARNING and the census-driven `logging too frequently`
rate limit).

## 2. Functional requirements

### FR-1 — The power-dominant display (push plane only)

`AquaTempData.from_pushes` decodes `hvac_mode` from POWER alone
(contract 018-A):

| power (mirror 2011) | mode (mirror 2019) | `hvac_mode` |
|---|---|---|
| `None` (no capture / gate closed) | any | `None` → tile `unknown` (honest, unchanged) |
| `False` (raw 0) | any (not consulted) | `HVACMode.OFF` (unchanged, [P owner diff-revert]) |
| `True` (raw ≠ 0) | any (not consulted) | **`HVACMode.HEAT`** — the heat-only hardware fact (H06 [P panel]) |

The mode mirror NEVER gates the displayed `hvac_mode` on the push
plane. This is not invention: the machine's mode space is {off, heat}
(H06 [P panel]; the app's mode-lock [P apk]; spec 017 FR-8 already
ADVERTISES `hvac_modes = [OFF, HEAT]` on exactly this evidence), and
power = 1 is the proven statement "the machine runs" — on a heat-only
unit that means heating. The 2019 register is a command/status mirror
our write path never touches (contract 016 §4's mode exclusion);
a display gated on it renders the machine's real running state as
`unknown` forever — the observed defect.

### FR-2 — The mode mirror becomes a diagnostic

- `mode_value` (the raw 2019 value) STAYS in the snapshot and the
  gate-independent `push_stream` diagnostic raws (spec 015 FR-10,
  unchanged) — the research surface for pinning 2019's semantics
  (research 018 §8, G-1).
- An **unmapped NONZERO** mode value while power is on logs ONE INFO
  line per value per HA session (the module-set pattern):
  `pushed mode value <v> (reg 2019) unmapped — display follows power
  (heat-only, spec 018); update registers.PUSHED_MODE_VALUE_TO_NAME for
  the research map`. It is a research/calibration signal — it no longer
  degrades any display, so INFO is the proportionate level.
- Value **0** and value **None** are SILENT: 0 is the observed,
  expected value in our control world (and after a proven app-flow
  revert); the power=False branch never consults mode at all.
- The old WARNING string `Unknown pushed Mode value …` is REMOVED
  (0-while-on is normal state, not an error; a WARNING that fires on
  every healthy control session is noise that drowns the contract-012
  evidence).

### FR-3 — `PUSHED_MODE_VALUE_TO_NAME` becomes the observed-values registry

Content unchanged (`{4: "heat"}`); role redefined: the registry of the
mode values OBSERVED on this unit (the diagnostic's unmapped-check
reads it). The comment is rewritten to record the two PROVEN values:
`4` = the app-flow on-state (015 diff-revert, 08:31 [P owner]);
`0` = observed while ON after OUR power-only writes AND while off —
expected, never an error. It is no longer the display gate; polluting
the base `MODE_VALUE_TO_NAME` stays forbidden (unchanged).

### FR-4 — The block-census logging amendment (the `logging too frequently` fix)

In `aqua_client._census_frame`, the per-frame block line
(`block push observed: start=… qty=… len=… crc=…`) drops from INFO to
**DEBUG** (contract 018-B, amending contract 008-B §5's level for the
block line). In push mode this line fires for every block of every
burst — ≥ 4 lines per 30 s poll cycle plus 4 more per button-press
confirmation — which drives the `custom_components.aqua_temp_ble.
aqua_client` logger past Home Assistant's rate-limit filter (the
owner's observed `logging too frequently: 200 messages since last
count`). The mute that follows can swallow contract-mandated WARNING
evidence from the same logger (`block push captured` — contract 013
§2.1's capture mechanism; `BLE connection lost`).

Preserved unchanged: the pushed-block store (the data itself), the
once-per-connect `block push captured` WARNING (013 §2.1), the
per-request census summary carried in the `AquaTempTimeoutError`
message and logged once per cycle by the coordinator (008-B §6 /
009 §3), the idle-window summary WARNING (009 §3), the generic
non-block `frame observed` line (INFO, rare), the first-idle block
push INFO (once per connect), and the request counters
(`_req_frame_count`, `_req_block_starts`) — the timeout message's
census is computed from them.

### FR-5 — Non-regression (the planes we do NOT touch)

- The CLASSIC plane (`from_regs`, `MODE_VALUE_TO_NAME`, its own
  unknown-mode warning) is byte-identical — it serves other units
  whose mode space is NOT this unit's hardware fact; a hypothetical
  cool-capable unit must keep its honest mode-gated decode.
- `hvac_action` is UNCHANGED: OFF when power is False, `None`
  otherwise (the compressor binary is uncalibrated [V] — power on ≠
  actively heating; do not guess). The `2019 = 4 → HVACAction.HEATING`
  idea is a FUTURE spec's, behind research 018 §8 G-1's decider.
- The 015/017 gates, the confirmation cycle, the funnel guard, the
  entity's `hvac_mode`/`hvac_modes` properties and the whole write
  surface are untouched (the 017 suite's verdicts stay valid).

## 3. Non-functional requirements

- **NFR-1 — Minimal, surgical diff.** Three code files only:
  `coordinator.py` (the decode block + the module set), `registers.py`
  (one comment), `aqua_client.py` (one level constant). No signature,
  entity, contract-gate or write-path change.
- **NFR-2 — English-only artifacts** (repo rule).
- **NFR-3 — Tests updated with the contracts they pinned.** The 015
  suite's unknown-mode test flips to the new truth table; the census
  level assertions follow contract 018-B; a new 018 suite pins the
  truth table, the diagnostic's once-per-session rule and the census
  level (tasks.md T1/T3/T4).
- **NFR-4 — No new dependencies.**

## 4. Acceptance criteria

- **AC-1 The truth table (test)**: `from_pushes` with the control gate
  open renders: (2011=1, 2019=0) → `HEAT`; (1, 4) → `HEAT`;
  (1, None) → `HEAT`; (1, 7) → `HEAT`; (0, *) → `OFF`; gate closed or
  no capture → `None`. The mode value never changes the result.
- **AC-2 The warning is gone (test)**: a decode of (power=1, mode=0)
  produces ZERO records containing `Unknown pushed Mode` at ANY level;
  a decode of (1, None) likewise. The old warning cannot reappear.
- **AC-3 The diagnostic (test)**: (1, 7) logs exactly ONE INFO
  (`pushed mode value 7 (reg 2019) unmapped …`) per session per value;
  the second decode logs nothing; (1, 4) logs nothing (mapped);
  (0, 7) logs nothing (the OFF branch never reads mode).
- **AC-4 The census level (test)**: block frames received while a
  request is pending log at DEBUG (no `block push observed` record at
  INFO or above); the `block push captured` once-per-connect WARNING
  and the timeout message's census summary are unchanged; the request
  counters still feed the timeout census.
- **AC-5 Classic plane byte-identical (test)**: the classic decode's
  existing tests pass unchanged — including its own unknown-mode
  warning (a different plane, a different unit's calibration).
- **AC-6 The inherited suites**: the 015 suite (updated where it pinned
  the amended display contract — its gate-closed, power-OFF and
  mode=4-HEAT cases keep their verdicts), the 016/017 suites (verdicts
  unchanged — their scripted mirrors decode to the same HEAT/OFF), and
  the 013/014 suites (the poll cycle is untouched) all pass.
- **AC-7 The live verification (the owner — quickstart 018 §1)**: with
  the gates open, tapping the ON control starts the pump AND the tile
  shows **HEAT within ~10-15 s**, with the audit + confirmed lines in
  the log page and NO `Unknown pushed Mode` line; tapping OFF stops it
  and the tile shows OFF; over a multi-hour window the log page shows
  no `logging too frequently` line for `aqua_client`. This AC closes
  with the owner's recorded observation (the 016 AC-11 / 017 AC-12
  pattern).
- **AC-8 The research step (the owner, non-blocking)**: the long-ON
  observation (quickstart 018 §2) — 2019 watched via the `push_stream`
  diagnostic's `control_candidates` while the unit runs ≥ 5 min after a
  button ON — recorded verbatim in research.md §8 (either outcome
  closes a G-ledger row; the display fix is correct in both).

## 5. Out of scope

- The semantics of 2019 (status enum vs command mirror) — research
  018 §8 G-1; the named deciders (the long-ON test, E5, the logcat
  oracle) change NOTHING in this spec's display logic.
- Any write to 1012/1013 (the mode pair) — forbidden (contract 016 §4
  non-transference; research 017 §5's pair-write hazard).
- `hvac_action` refinement (2019 = 4 as an "actively heating"
  oracle) — a future spec behind G-1.
- The classic plane, `MODE_VALUE_TO_NAME`, the setpoint, the mute.
- The mode VALUE map's extension (beyond {4}) — evidence first.