# Plan 015 — the path to the control data (and to a read-only climate)

The strategy in one line: **the data decides** — the owner runs the
toggle-and-diff calibration (Phase A), the evidence pins the homes, and
only then does code render the climate (Phase B); writes wait for
vendor-proven addresses AND a working delivery gate (Phase C, out of
this spec's scope).

---

## 1. Phase A — the evidence (owner + existing tooling, zero code)

### A.1 The toggle-and-diff calibration (quickstart §1)

The template is the T-window's own history: the 2046-2051 window went
from [A-very-strong] to [P owner photo] by ONE side-by-side run. The
control hunt repeats it per quantity, with a revert pass (the liveness
proof that static captures cannot give — research §8 U-1):

```
0. note the app's displayed: on/off state, mode, setpoint value
1. capture (the four "block push captured" WARNING lines — one
   connection test, or one coordinator cycle)
2. change ONE thing in the app (power slide; mode — if the unit
   allows; setpoint +0.5 °C)
3. capture again (fresh connect → fresh first-push lines)
4. DIFF the per-block windows; any register that moved = candidate
5. REVERT the change, capture, diff again — the register must follow
   the revert (reversibility = pinned)
```

The three target registers (data-model §7):

| quantity | candidate set | diff signature |
|---|---|---|
| power | 2012 (the only 2000-series boolean); a 1001/1046 flag is excluded by their proven staticness only if the power state was equal at both captures — the step-0 note closes that hole | 0/1 flip following the slide |
| mode | 2034 (enum); NOTE: the unit is heat-only — the app may refuse mode changes entirely [P apk] | enum change (may be untestable) |
| setpoint | 1046 / 1049 / 1051 / 1055 / 1068 (the five ladder values — static so far, which an untouched setpoint would also be) | ±5 raw move per ±0.5 °C |

Outcome handling:

- **PINNED** (moved + reverted): promote to `PUSHED_CONTROL` behind the
  gate (Phase B).
- **SILENT** (nothing moved): the control state is NOT in the pushed
  plane [P-grade negative proof] → the climate stays unavailable; the
  follow-ups are E5 (the app's session), the logcat oracle (the write
  addresses for the eventual write path), and the request-path revival
  (spec 011's `pair_on_connect` lever + the revival WARNING of
  contract 014 §5.1).

### A.2 The logcat write-address oracle (quickstart §2)

`adb logcat` while toggling power/mode/setpoint in the app; grep
`参数选择修改` / `address` / `6310`. Output: the vendor's exact write
addresses+values (research §5.3 [P apk]). This oracle:

1. feeds any future allowlist decision (Phase C — FR-13),
2. cross-checks the read diff: if the app writes power at address A,
   the READ home is likely A or its mirror — the diff and the oracle
   must agree before any promotion.

### A.3 The standing deciders

- **E5 — the HCI snoop** (quickstart §3, unchanged since spec 009):
  one vendor session on this unit decides world (a) request-fed vs
  (b/c) hidden-mapping/other-blocks for the app's display.
- **The 1091/1136 probe** (quickstart §4 — spec 014's U-6', unrun):
  the "Probe registers" service, block=1091 then 1136 (2001 as the
  control case). A direct block answer opens the never-pushed plane
  (S/O/D/P candidate homes — research §6.2); a burst-only answer leaves
  them unreachable without E5.

## 2. Phase B — the read-only climate behind the confirmation gate (Coder)

**Precondition: at least power (and ideally setpoint) PINNED by A.1
and confirmed by the owner.** Until then, nothing below ships.

1. `registers.py` (single source of truth):
   - `PUSHED_CONTROL: dict[str, int | None] = {"power": None, "mode":
     None, "setpoint": None}` — the pinned homes, each with its [P]
     evidence comment (the PUSHED_TEMPS pattern);
   - `PUSH_CONTROL_CONFIRMED = False` (global switch) — the gate;
   - `check_registry_integrity` extension: pushed-control addresses
     inside 1001-2090, no cross-plane duplicates (FR-11d).
2. `coordinator.py`:
   - `push_control_confirmed` property (the `push_temp_confirmed`
     pattern: global OR per-entry option);
   - `from_pushes` decodes the confirmed keys: `power` (raw != 0),
     `mode_value` + `hvac_mode` (via `MODE_VALUE_TO_NAME`, [A]-tagged —
     the heat-only lock may leave the mapping assumed; the tag surfaces
     in diagnostics), `target_temp` (÷10, plausibility-guarded).
     Unconfirmed → None (the 0.10.0 behaviour, byte-identical).
3. `climate.py`:
   - push-mode entries create the entity only when
     `push_control_confirmed`;
   - READ-ONLY: no `ClimateEntityFeature.TARGET_TEMPERATURE`, writes
     unavailable (FR-8); `hvac_action` = OFF when power False;
   - bounds: fallbacks 5.0/40.0 (FR-9) until bounds homes are pinned
     by their own diffs.
4. `sensor.py`: the push_stream diagnostic (or the probe output) gains
   the candidate raws {2012, 2034, 1046, 1049, 1051, 1055, 1068} so the
   owner can re-run the diff without a terminal (FR-10).
5. Tests (FR-11): gate-closed non-regression (the 0.10.0 snapshot),
   confirmed decode paths, read-only climate surface, integrity checks.
6. Docs: `docs/` calibration section gains the control-diff procedure
   (the quickstart §1 content, operator-facing).

## 3. Phase C — the write path (recorded; NOT this spec)

The three-door rule (contract `write-security-contract.md`):

1. **vendor-proven address** — the logcat oracle (A.2) or an HCI capture
   of an app write names the register; the 45-name base list [P apk]
   says the vendor's model addresses power/mode/setpoint at 8/13/14,
   but the time-sync frame (register 1267) proves the app ALSO writes
   the high plane — only the oracle decides [V];
2. **delivery gate understood** — att-accepted-silent must first become
   answered-after-X (E5 / pairing evidence, spec 011);
3. **its own spec** — the allowlist change, its bounds and its tests.

## 4. What each artifact in this spec does

| artifact | role |
|---|---|
| `research.md` | the complete decode + the 8 verdicts + the new APK evidence |
| `data-model.md` | the per-block register→quantity tables (the Coder's mapping reference) |
| `contracts/control-mapping-contract.md` | the promotion rules: diff → pin → confirm → entity |
| `contracts/write-security-contract.md` | the allowlist extension rule (the three-door rule) |
| `quickstart.md` | the operator experiments (A.1-A.3) step by step |
| `tasks.md` | the Coder's ordered work (Phase B) + the evidence gates |
| `decode_blocks_015.py` + `frames.txt` | the reproduction tooling (AC-1) |

## 5. Decision log (this spec)

| # | decision | rationale |
|---|---|---|
| D-1 | No mapping ships without the diff-revert evidence | one capture cannot prove liveness (the 2001 lesson, research §2.3) |
| D-2 | The climate in push mode is READ-ONLY, forever until Phase C | writes are transport-blocked [P] and evidence-gated; a read-only climate is still the user's ask (state visibility) |
| D-3 | The mode mapping may stay [A] (heat-only lock) — surfaced in diagnostics, never silent | the app refuses mode changes [P apk]; AC-3 requires honesty instead of a fake precision |
| D-4 | 2012/2034 are never written on read evidence alone | a read correlation proves reflection, not control (contract §8) |
| D-5 | The five-candidate setpoint set is resolved by the DISPLAYED value first, then the ±0.5 diff | the owner's photo is the cheapest evidence; the diff is the proof |