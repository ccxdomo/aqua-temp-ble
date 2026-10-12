# Spec 015 — Control data: finding power / mode / setpoint in what the unit sends

Date: 2026-10-12
Repo state: main @ fcc9d38, manifest 0.10.0
Status: **Phase A evidence recorded (power 2025 / mode 2034 pinned by the
owner's diff-revert, research §8.1) — Phase B implemented: the read-only
climate behind the `push_control_confirmed` gate (default closed); the
owner's verification step (quickstart §5) opens it.**

## 1. Problem

The integration works for temperatures: the trigger+listen cycle (spec
014) captures all four pushed blocks every poll, and the five live
T-family sensors display in Home Assistant behind the confirmed
calibration (`registers.PUSHED_TEMPS`, [P owner photo]).

The climate entity `climate.aqua_temp_heat_pump` remains `unknown`:
push mode decodes `power/mode/setpoint/fault` as `None` by design
(contract 013 §4.4 — no identified pushed home, optimistic state
forbidden), and the base block that carries them by name (regs 8/13/14,
the 45-name list [P apk]) is served to us by no grammar (six runs, spec
014 §2/M-7).

But the vendor app DISPLAYS on/off, mode and a setpoint [P owner] — the
information exists in what the unit sends. This spec decodes every
pushed block completely, hunts the control homes, defines the honest
promotion path (calibration evidence → confirmation gate → read-only
climate), and locks the write security posture.

## 2. Evidence baseline (research.md)

1. **All four pushed blocks are decoded, CRC-verified** (research §2):
   1001/1046 byte-identical to the pinned vectors (static ≥ 2 days);
   **2001 — never before decodable — contains exactly five non-zero
   registers** (2010=2001 self-ref, 2012=1 flag, 2017=13, 2018=168,
   2034=4 enum), **no setpoint-shaped value**; 2046 is the calibrated
   T-window.
2. **No offset relates the base block to the pushed plane** (research
   §3): the mirrors +994/+1000/+1994/+2014 all die on content.
   `1001 + n ≡ 7 + n` is FALSE; reg 1002 is not the power register.
3. **New APK evidence** (research §5): the complete 45-name base-block
   list (independently confirming the shipped map); the
   control-parameters page's ordered label list (the editable setpoints
   "Cooling set / Heating set / Auto. Set" live in the 1001-1180
   machine-settings series); the proof that every power/mode/setpoint
   WRITE in the app goes through the code path that logs
   `参数选择修改 ----- value = … index = … address = …`.
4. **The verdict:** power/mode/setpoint have **no identified home in the
   pushed plane** — only candidates: power→2012 [V], mode→2034 [V],
   setpoint→{1046, 1049, 1051, 1055, 1068} [V]. The app's display source
   is one of three worlds (request-fed / hidden mapping / other blocks),
   undecidable statically (research §4 Q6).

## 3. Goals

Find the control data honestly, or prove it absent from the pushed
plane; enable a **read-only climate** in push mode the moment the
evidence pins it; never weaken the write security.

## 4. Requirements

### Phase A — evidence (owner-run experiments, no code)

- **FR-1** The quickstart defines the **toggle-and-diff calibration**
  (quickstart §1): baseline capture → change ONE control in the app
  (power / mode / setpoint ±0.5 °C) → capture → diff → revert → capture
  → diff. A register that follows the change AND the revert is PINNED.
  The captures are the existing `block push captured` WARNING lines
  (spec 013 FR-1) — no new logging is required to run the experiment.
- **FR-2** The quickstart defines the **logcat write-address oracle**
  (quickstart §2): `adb logcat` while the owner toggles power/mode/
  setpoint in the app; the `参数选择修改 … address = A` lines yield the
  vendor's exact write addresses+values (research §5.3).
- **FR-3** The quickstart names the two standing experiments that decide
  the app's source world: the E5 HCI snoop (§3) and the
  probe-1091/1136 readability test (§4 — spec 014's U-6', unrun).
- **FR-4** The spec records the **negative-proof rule**: if the power
  diff moves NO pushed register, the control state is NOT in the pushed
  plane [P-grade] and the climate stays unavailable; the follow-up path
  is E5 / the write-oracle / the request-path revival (spec 011).

### Phase B — read-only climate behind the confirmation gate (Coder, only after FR-1 pins homes)

- **FR-5** New constants in `registers.py` (single source of truth),
  default-unknown:
  `PUSHED_CONTROL: dict[str, int | None]` with keys
  `"power"`, `"mode"`, `"setpoint"` — values `None` until pinned by the
  diff experiment; the setpoint candidate may be any register of
  1001-2090 (the ladder members 1046/1049/1051/1055/1068 included).
- **FR-6** A **control confirmation gate** mirroring the
  `PUSH_TEMP_CONFIRMED` precedent: a global switch
  `PUSH_CONTROL_CONFIRMED = False` + a per-entry option
  `push_control_confirmed` OR'd together (coordinator property
  `push_control_confirmed`, the `push_temp_confirmed` pattern).
  Unconfirmed homes never reach the entity plane.
- **FR-7** `AquaTempData.from_pushes` decodes the confirmed control
  keys: `power` (flag, `raw != 0`), `mode_value` (raw) and `hvac_mode`
  (via `MODE_VALUE_TO_NAME` — on this heat-only unit the mapping is [A]
  until the mode-diff or the logcal oracle corrects it),
  `target_temp` (÷10, plausibility-guarded). Unconfirmed → `None`
  exactly as today (non-regression: the shipped 0.10.0 snapshot is
  unchanged while the gate is closed).
- **FR-8** The **climate entity is created in push mode** when
  `push_control_confirmed` is true, as **READ-ONLY**: no
  `ClimateEntityFeature` (no TARGET_TEMPERATURE write support),
  `_attr_hvac_action` = OFF when power is False; writes raise
  `HomeAssistantError`/are not advertised. The existing classic-mode
  climate is untouched (mode == "classic" path byte-identical).
- **FR-9** Bounds: `min_temp`/`max_temp` keep the fallbacks (5.0/40.0)
  until bounds homes are pinned (the 1046 ladder's 100/400 members are
  candidates [A-weak]); no bounds candidate is pinned without its own
  diff evidence.
- **FR-10** Diagnostics: the push-mode diagnostic surface (push_stream
  attributes or the probe service) exposes the raw values of the
  candidate registers {2012, 2034} + the ladder five {1046, 1049, 1051,
  1055, 1068} so the owner can run the diff without a terminal.
- **FR-11** Tests: the 015 suite pins (a) from_pushes with
  confirmed/unconfirmed control keys, (b) the climate read-only surface
  in push mode, (c) the gate non-regression (0.10.0 behaviour with the
  gate closed), (d) `check_registry_integrity` extended to the pushed
  control candidates (no duplicate addresses across planes; candidate
  addresses inside 1001-2090).

### Phase C — write path (NOT this spec; recorded requirements only)

- **FR-12** The write allowlist `{8, 13, 14}` (+19-21 behind the
  switch) is **unchanged** by this spec; `ensure_writable` untouched.
- **FR-13** The recorded rule for any future extension (contract
  `write-security-contract.md`): vendor-proven write address (logcat
  `address = A` / HCI evidence) + understood delivery gate + its own
  spec. A read-diff correlation NEVER authorizes a write target
  (contract §8).

## 5. Acceptance criteria

- **AC-1** `python3 specs/015-control-data/decode_blocks_015.py`
  reproduces every table of research §2 and validates all four CRCs
  (exit 0).
- **AC-2** No entity, mapping, or write surface changes ship while the
  FR-1 evidence is absent: with `PUSH_CONTROL_CONFIRMED=False` (default),
  the integration's behaviour is byte-identical to 0.10.0 (the FR-11c
  test).
- **AC-3** After the FR-1 power diff pins (say) reg 2012 and the owner
  confirms the option, `climate.aqua_temp_heat_pump` exists in push mode
  with `hvac_mode` reflecting the unit's on/off state, `target` from the
  pinned setpoint home, and no writable features (FR-8).
- **AC-4** If the FR-1 power diff moves nothing, the spec's verdict
  (control not in the pushed plane) is recorded in research.md §8/§9 and
  the climate remains uncreated — with the follow-up path named (E5,
  logcat oracle, revival).
- **AC-5** Every new mapping in `registers.py` carries its [P]/[A]/[V]
  tag and its evidence source in a comment (the repo discipline).
- **AC-6** The write allowlist is provably unchanged: the shipped tests
  (`ensure_writable` suite) pass without modification.

## 6. Non-goals

- No write capability of any kind (transport-blocked [P] and
  evidence-gated [D] — FR-12/13).
- No touching of specs 001-014 artifacts (inherited contracts honored
  verbatim; the contract addenda here EXTEND the 013/014 rules, they do
  not amend them).
- No speculative mapping of the 2001 unknowns (2010/2017/2018) beyond
  the candidate table (data-model §3); no D-family speculation (spec 003
  §9 discipline).
- No new request frames beyond the shipped trigger (the vendor base
  read) — the probe service's existing block queries are the only new
  TX the quickstart may exercise.

## 7. Risks / safety

- The write-security rationale (spec 003 §6) now covers the whole
  1001-2090 plane explicitly: the H04 safety threshold (105.0 °C) lives
  at 1021, one window from the P-candidates — an erroneous write can
  damage the unit. The allowlist is the boundary; FR-13 is the only
  door.
- The mode mapping on a heat-only unit may be UNTESTABLE by toggle (the
  app refuses mode changes [P apk]); hvac_mode may therefore rest on
  an [A] mapping — FR-7 requires the tag to surface in the entity's
  diagnostics, never silently.
- Single-capture blocks (2001) cannot prove liveness; only the
  diff-revert protocol (FR-1) promotes a candidate (contract §7).