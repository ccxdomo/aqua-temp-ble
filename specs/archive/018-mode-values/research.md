# Spec 018 research — the climate's `unknown`: the mode mirror decoded, traced and adjudicated

Date: 2026-10-12
Evidence base: the shipped code @ b81b604 (`coordinator.py`,
`climate.py`, `registers.py`, `aqua_client.py`, `const.py`); the
owner's production observation (2026-10-12, quoted in the brief); the
owner's 015 diff-revert evidence (research 015 §8.1, 2026-10-12);
research 016/017 (the write plane, the mode handler); a fresh
disassembly session on the surviving artifacts
(`/tmp/flutterapp/lib/arm64-v8a/libapp.so`, `/tmp/blutter_out/`,
`/tmp/disasm.py` — all re-verified present this session).

Status legend (the repo convention): **[P]** PROVEN (cited code /
cited spec / the owner's recorded observation / disassembly with
addresses), **[P owner]** the owner's observation, **[P panel]** the
parameter-view photographs, **[P apk]** recovered from the APK,
**[V]** TO-VERIFY (one named experiment decides), **[A]** ASSUMED,
**[D]** DECISION.

---

## 0. TL;DR

> **The control works; the display is fed by a mirror our control
> never touches.** The shipped push-plane decode requires the mode
> mirror **2019** to carry a mapped value whenever power is on
> (contract 015-A §5). The owner's button writes the PROVEN power
> register **1011** (and nothing else — contract 016 §4 forbids the
> mode pair); the firmware updates the power mirror **2011** but the
> mode mirror **2019** stays 0, so the decode renders `unknown` while
> the machine audibly heats — with the WARNING the owner quoted as
> the direct proof.
>
> - **The trace (§2)**: `(power=1, mode=0)` → `hvac_mode = None` →
>   tile `unknown` — by design of contract 015-A §5, whose "2019 = 4
>   whenever on" assumption (D-3) is falsified in our control world.
> - **H1 CONFIRMED observationally (§3)**: our 1011 write powers the
>   machine, moves 2011, never moves 2019 [P]. **H2 (stale block)
>   REFUTED** [P — frame atomicity + the warning's own `power=True`].
>   **H3 (display code broken) REFUTED as a bug** [P — the code IS
>   the contract]; the defect is the contract's assumption.
> - **The fix (§4/contract 018-A)**: power wins — `power=1` ⇒ **HEAT**
>   (heat-only unit, H06 [P panel]; the mode mirror becomes a
>   diagnostic). Not invention: the machine has no other mode.
> - **The app's 08:31 `4`**: the app's power flow writes ONLY 1011
>   (re-verified in the disassembly this session, §5.1) and the app
>   REFUSES mode writes on this unit (mode-lock, research 017 §5) —
>   so the FIRMWARE set 2019=4 in the app-flow world, by a mechanism
>   we have not replicated (the open ledger, §8 G-1: status-enum
>   with lag vs another app frame vs E5-gated behavior). The display
>   fix is correct in every branch of that open question.

---

## 1. The observed fact, decoded

The owner's production report (2026-10-12): the buttons work — the
PAC physically starts and stops (« ca s'est bien allumee et quand
j'ai rappuye ca s'est eteint », the 017-era physical confirmation
plus today's session) — **but the tile shows `unknown` in both
states**, « et HEAT ne s'affiche pas », « ca met aussi unknown », and
the log carries:

```
Unknown pushed Mode value 0 (reg 2019) — update registers.PUSHED_MODE_VALUE_TO_NAME after calibration (quickstart 015 §5)
```

**What that WARNING line proves by itself [P — code reading of the
shipped `from_pushes`]** — it can fire in exactly one branch:

```python
if power is False:
    hvac_mode = HVACMode.OFF
elif power is True:
    name = (registers.PUSHED_MODE_VALUE_TO_NAME.get(mode_value)
            if mode_value is not None else None)
    hvac_mode = MODE_NAME_TO_HVAC.get(name) if name is not None else None
    if hvac_mode is None and mode_value not in _logged_unknown_push_modes:
        _logged_unknown_push_modes.add(mode_value)
        _LOGGER.warning("Unknown pushed Mode value %r (reg %s) — …", mode_value, m_addr)
```

To reach the WARNING, ALL of these held at decode time:
1. the 015 control gate was open (`control_confirmed`) and a
   2001-block capture existed (otherwise `power` stays `None` and no
   branch runs);
2. `power is True` — the capture's **2011 ≠ 0**: the machine was ON in
   the frame being decoded;
3. `mode_value == 0` — the SAME capture's **2019 = 0**, unmapped
   (`PUSHED_MODE_VALUE_TO_NAME = {4: "heat"}`), so `hvac_mode = None`.

So a **fresh** 2001 frame — 2011 ≠ 0 only exists post-write, the unit
was off before the button — reported `2011 = 1, 2019 = 0` in the same
45-register frame. That single line is the whole root cause, pinned.

## 2. THE TRACE — `hvac_mode`, case by case, on the shipped code

### 2.1 The decode (`coordinator.AquaTempData.from_pushes`, push plane)

Order of tests [P — verbatim code]: **power first** (`False` → OFF,
stop), **then** (only if `True`) the mode lookup; `None` power never
consults mode. Simulated on the real code, gate open, capture
present (2011/2019 = the frame raws):

| # | power (2011) | mode (2019) | code path | `hvac_mode` | tile | verdict |
|---|---|---|---|---|---|---|
| T1 | 1 (≠0) | **0** | `power is True` → `PUSHED_MODE_VALUE_TO_NAME.get(0)` → `None` → `hvac_mode=None` → WARNING once/session | **`None`** | **`unknown`** | **the observed defect [P owner + the WARNING]** |
| T2 | 1 | 4 | lookup → `"heat"` → `MODE_NAME_TO_HVAC["heat"]` | `HEAT` | `heat` | the 015-expected world (the 08:31 app-flow capture [P owner]) |
| T3 | 0 | 0 (never read) | `power is False` → OFF, return | `OFF` | `off` | correct [P owner diff-revert] |
| T4 | 0 | 4 (never read) | same | `OFF` | `off` | correct — mode ignored when off |
| T5 | **None** (no capture / gate closed) | — | neither branch | `None` | `unknown` | honest (015 FR-11c) |
| T6 | 1 | None (absent, defensive) | lookup guard `if mode_value is not None` → `name=None` → `hvac_mode=None` → WARNING `"Unknown pushed Mode value None"` | `None` | `unknown` | defensive case, same defect family |
| T7 | 1 | 7 (unmapped, hypothetical) | lookup → `None` → WARNING | `None` | `unknown` | the 015 test's scripted case (tests/test_control_015.py:625) |

**Precedence**: OFF wins over any mode (power first); there is NO
case where a mode OVERRIDES power — the defect is not an ordering
bug, it is the requirement that a KNOWN mode exist at all when
power is on (contract 015-A §5's "an unmapped value renders
`unknown`").

### 2.2 The entity (`climate.AquaTempClimateEntity`)

```python
@property
def hvac_mode(self) -> HVACMode | None:
    data = self.coordinator.data
    return data.hvac_mode if data else None
```

Pure passthrough [P] — no entity-side logic, no order, no gate. The
tile is the snapshot's value, nothing else. `hvac_action` [P]:
`power is False → HVACAction.OFF`, else `None` (never a guess — the
compressor binary is uncalibrated [V]).

### 2.3 The classic plane (for contrast — NOT the shipped world on this unit)

`from_regs` mirrors the same shape with base reg 13
(`MODE_VALUE_TO_NAME = {0: "cool", 1: "heat", 2: "auto"}`, [A]):
`(power=1, mode unmapped)` → `unknown` + its own once-per-session
warning. This plane never runs on this unit (the unit never answers
base reads [P — spec 014]); spec 018 leaves it byte-identical
(FR-5) because a hypothetical multi-mode unit must keep its honest
mode-gated decode.

### 2.4 Who calls the decode

Both callers build the same snapshot type [P]: the poll cycle
(`_async_update_push`, every ~30 s, from the store's LATEST 2001
capture) and the post-write confirmation cycle (spec 017 FR-3, from
a capture freshness-gated `last_seen > t_write`). The WARNING can
fire from either; both read post-write frames in the observed
session, so §1's proof holds for both.

### 2.5 The OFF-state `unknown` the owner reported — triage

The steady-state OFF path yields `off` (T3) — the shipped code has
NO steady-state path that renders `unknown` with a fresh 2001
capture at 0. The reported off-state `unknown` is therefore one of
[candidates, not proven]:
- **(a) the post-OFF transient** [most plausible]: the OFF
  confirmation pushes its snapshot as soon as the MIRROR decides —
  if the first post-write frame still carries 2011=1 (firmware
  report lag), the verdict is `not-confirmed`, the snapshot decodes
  power=True + mode 0 → `unknown` for up to one poll cycle (≤30 s),
  then OFF; the owner watching the tile in that window sees
  `unknown` while the machine is already stopping;
- **(b)** an observation with the 2001 capture absent from the
  store (a reconnect window before the first burst) → T5;
- **(c)** summary compression — the ON state never showed HEAT, so
  "unknown" is remembered as the dominant state of the tile.

The fix does not touch the OFF path (it was already correct);
quickstart 018 §1 step 4 verifies OFF → `off` as an AC.

## 3. The three hypotheses — adjudicated

### H1 — "our 1011 write powers the machine WITHOUT setting 2019" → **CONFIRMED observationally [P]**

- The WARNING (§1) proves a post-write 2001 frame carried
  `2011 = 1` AND `2019 = 0` — the same frame, both registers.
- The 08:31 `4` [P owner diff-revert] belongs to the **app-flow**
  world (the app held the link; our captures bracketed it).
- The write plane is power-only BY CONTRACT: `ALLOWED_WRITE_CONTROL
  = {1011: frozenset({0, 1})}` (016 §4); the mode pair 1012/1013 is
  excluded (an unpinned pair register + the vendor's own mode-lock,
  research 017 §5). Our button CANNOT have touched 2019's write
  side. [P]

**The open sub-question — WHY the app-flow world produced 4** — is
a NEW ledger item (§8 G-1), not a blocker: the app's power handler
writes ONLY 1011 (re-verified this session, §5.1) and the app
REFUSES mode writes on this heat-only unit (mode-lock GUARD 2
[P disassembly, research 017 §5]) — so **the firmware itself set
2019 = 4** in that world. Candidates: (i) 2019 is a
firmware-computed STATUS ("heating engaged"), updated with a lag our
≤10 s confirmation window cannot see (front-runner — the 08:31
capture postdates the app's ON slide by minutes); (ii) another app
session frame correlates (the time-sync write 1267, FINDINGS
§11.1; the six-block queries); (iii) an app-session-gated behavior
(the E5 world). Named deciders: the long-ON test (quickstart §2 —
2019 watched via the `push_stream` raws for ≥5 min after a button
ON), E5 (the HCI snoop), the logcat oracle. **The display fix is
correct in every branch**: if (i) is true, 2019 is an activity
signal (a future `hvac_action` oracle — out of scope); if (ii)/(iii),
it is a command mirror we still never write.

### H2 — "the 2001 block is stale after our write" → **REFUTED [P]**

Three independent kills:
1. **Frame atomicity**: 2011 and 2019 sit in the SAME 45-register
   2001-block frame (registers 2001-2045, one CRC-valid push). There
   is no mechanical way for 2011 to be fresh and 2019 stale within
   one capture — the store keeps whole decoded windows, latest
   frame wins (contract 013 §3.3).
2. **The freshness gates**: the confirmation cycle's oracle requires
   `last_seen > t_write` (contract 016 §3, `_wait_fresh_control`);
   the poll cycle decodes the store's LATEST capture, refreshed by
   every trigger's burst [P — spec 014, six runs]. A stale frame
   would carry the PRE-write state: 2011 = 0 → `power is False` →
   OFF, NO mode lookup, NO warning. The observed WARNING REQUIRES
   power=True in the decoded frame.
3. **The 2011 proof**: the mirror demonstrably updates within our
   own connection (0→1→0 across the 016 probe and today's
   confirmation verdicts) — the block content IS live post-write.

### H3 — "the display logic in climate.py is broken" → **REFUTED as a code bug [P]; the defect is the CONTRACT's assumption**

The trace (§2) matches contract 015-A §5 verbatim: "power False →
`off`; power True + mode → the mapped mode …; an unmapped value
renders `unknown`". The entity is a passthrough. Nothing is
misordered, nothing misread. What broke is the CONTRACT's D-3
assumption — "4 = the on-state mode … the honest assumption until
the logcat oracle or a mode diff corrects it" (registers.py,
research 015 §8.1) — which held only in the app-flow world. A
contract-compliant implementation of a wrong assumption is still a
wrong display; spec 018 amends the contract (018-A), not the
implementation discipline.

## 4. The brief's eight questions — verdicts

### Q1 — the complete `hvac_mode` trace

§2.1's table T1-T7, simulated on the shipped code. Summary:
`(1,0)` → `unknown`+WARNING; `(1,4)` → `heat`; `(0,0)` → `off`;
`(0,4)` → `off`; power `None` → `unknown`; `(1,None)` →
`unknown`+WARNING("…value None"). The entity adds nothing (§2.2).

### Q2 — why `unknown` even when ON

Because the shipped decode REQUIRES a mapped mode whenever power is
on (contract 015-A §5). With 2019 = 0 (the normal value after OUR
proven power-only write, §3-H1), the requirement fails on every
healthy control session. The code is not broken; the requirement
is wrong for a heat-only unit (contract 018-A §2 replaces it).

### Q3 — heat-only ⇒ HEAT when power = 1: legitimate?

**YES — it is a hardware fact, not a mode guess [D, on [P]
evidence].** The unit's mode space is {off, heat}: H06 "Mode type" =
heat-only [P panel; options Chiller/Heat pump/Heat only, P apk
research 015 §5.2]; the vendor's own app refuses mode changes on it
[P apk + P disassembly, research 017 §5]; spec 017 FR-8 already
ships the write surface `hvac_modes = [OFF, HEAT]` on exactly this
evidence. Power = 1 is the proven statement "the machine runs"
([P] mirror + the owner's ears); on a heat-only machine that IS
heating. The inverse honesty argument settles it: displaying
`unknown` while the machine demonstrably heats HIDES a known fact
behind an unmapped mirror value our write path never touches — that
is the dishonest display. (What stays honestly unknown: the
ACTIVITY — `hvac_action` stays `None` while on; power on ≠ compressor
engaged, §2.2.)

### Q4 — is the 2019 mirror refreshed after our write?

**The 2001 BLOCK is refreshed [P] — the 2019 VALUE inside it is not
moved by our write [P].** The confirmation verdict requires a fresh
capture (`last_seen > t_write`) and the mirror 2011 follows the
write within it; the same fresh frame's 2019 reads 0 (§1). So
"stale block" is refuted (H2); what stays open is the FIRMWARE's
rule for updating 2019 (§3-H1's sub-question, G-1): our 1011 write
does not trigger it, the app-flow world did (08:31). There is
nothing to fix on the read side — the block is fresh; the value is
simply 0.

### Q5 — the map of 2019

- **Observed values on this unit: {0, 4} [P]** — 0 in the off world
  and after our power-only writes (015 diff-revert + today's
  WARNING); 4 in the app-flow on world (08:31 [P owner]). No other
  value has ever been seen in the 2001 block on this unit.
- **The WRITE-plane mode index is {0, 1, 2} [A]** (cool/heat/auto —
  research 017 §5: the per-mode setpoints at `state.field_0x23/
  0x2b/0x33` for mode 0/1/2) — a DIFFERENT register's enum (1012),
  never written on this unit (mode-lock).
- **Disassembly, this session (§5)**: the app's home-screen
  status-verb i18n cluster exists — `heatingEngaged`/`制热运行中`
  ("heating running", pp+0x45df8/0x45e00), `coolingEngaged` (pp+
  0x45c70), `autoModeEngaged` (pp+0x45b28), `待机` "standby"
  (pp+0x462d0) — but the ONLY code referencing these keys is the
  locale-table BUILDER closure (13 copies at 0xa874 spacing, first
  at 0x7c2f80-0x7c951c, storing key→value pairs into per-locale
  objects); the read-value→verb decode itself was NOT located. **No
  read-plane mode VALUE table (value→name) was recovered.** The
  map stays what the observations made: `{4: "heat"}` + the
  documented 0.
- **Is there really more than one mode on this machine? NO [P]** —
  heat-only (Q3). The 0/4 pair reads most naturally as
  off/standby(0) vs engaged(4) — a status shape — but pinning that
  is G-1's deciders' job, not this spec's.

### Q6 — the corrected logic: the right design?

**Power wins; the mode never gates the display [D — contract
018-A].** The truth table: power `None` → `unknown` (honest);
power `False` → `OFF`; power `True` → `HEAT`. The mode mirror is a
diagnostic (snapshot `mode_value` + the `push_stream` raws;
unmapped NONZERO → one INFO per value per session). Keeping the
honesty argument requires the opposite: we WOULD display
`unknown` while on only if the mode were genuinely unknowable —
but on a heat-only unit the mode is KNOWN from the power state
itself (Q3). The 2019 sub-modes question is moot: there are none
to distinguish (the vendor locks the mode; the write surface ships
[OFF, HEAT]). The design is minimal, symmetric with the advertised
write surface, and correct in every branch of G-1.

### Q7 — the WARNING

**Remove it for 0 (and None); keep a once-per-session INFO for
unmapped NONZERO values [D].** The WARNING was contract-015's
calibration prompt (an unmapped value DEGRADED the display, so the
operator had to act). After 018: nothing degrades — an unmapped
value is a research-completeness signal, not an operator action
item — so WARNING is disproportionate (contract 012 §2 reserves
WARNING+ for operator-actionable evidence) and 0-with-power-on
would fire on EVERY healthy control session (pure noise). The INFO
line keeps the calibration path alive for the research map (if the
unit ever reports 2/5/…, we want to know once per session).

### Q8 — `logging too frequently`

**Cause [P — code reading]**: HA's own rate-limit filter (its
message, not ours) tripping on the `aqua_client` logger's
steady-state INFO volume in push mode — chiefly the per-frame
block census line (`_census_frame`: one INFO per CRC-valid block
frame while a request is pending = ≥4 lines per 30 s poll cycle
from the trigger's burst, +4 per button-press confirmation
trigger), amplified by every reconnect's per-connect set (GATT
enumeration ~6-8 INFO lines, connect summary, `connecting to` /
`Connected to`, 4× `block push captured` WARNING, the idle-window
WARNING). Volume math: 4-8 INFO/30 s ≈ 500-1000/h steady;
reconnect storms add ~15 lines each — consistent with the owner's
"200 messages since last count".
**Harm**: the filter's subsequent mute swallows records from the
same logger that carry contract evidence (`block push captured` —
contract 013 §2.1's capture mechanism; `BLE connection lost`).
**Remediation [D — contract 018-B]**: the block census line drops
to DEBUG; the census data survives in the store, the
once-per-connect capture WARNING, the per-cycle timeout census
summary (the coordinator's single INFO line) and the `push_stream`
diagnostic. Everything WARNING+ is preserved.

## 5. The disassembly session (new evidence, 2026-10-12)

Artifacts re-verified present: `/tmp/flutterapp/lib/arm64-v8a/
libapp.so` (11 MB), `/tmp/blutter_out/` (asm/ + pp.txt), the repo's
`blutter/pp.txt`, `/tmp/disasm.py` (capstone 5.0.7 in
`/tmp/blutter-venv`).

### 5.1 The app's power handler — ONE write, no mode write [P]

Closure 0x6a15c8 → body 0x6a1600 (research 016 §2's `_Iwd`),
re-disassembled in full this session (0x6a1600-0x6a1784):

```
0x6a1620-0x6a1634:  state = context.field_0x17; GUARD: field_0x9b (slave) → dialog
0x6a1688-0x6a16c4:  slave/emergency branches → dialogs (no write)
0x6a16e8-0x6a1728:  x6 = 1 if state.field_7 == 0 else 0; stur x6, [state, #7]
                    (the optimistic power toggle)
0x6a172c-0x6a173c:  offline guard (context.field_0x13 vs pool) → dialog
0x6a1740-0x6a1754:  x2 = 0x3F3 (1011), x3 = 1 (qty), bytecount string
                    pp+0x20b68 → builder 0x64fc0c
0x6a1774:           sender 0xa14ea8; then ret
```

ONE builder call, ONE sender call, register 1011, qty 1, the
toggled 1/0 — **no mode write anywhere in the power flow** [P].
Consequence for H1: the app's power slide and our button send the
SAME logical write (1011 = 1/0), so the 08:31 `4` was NOT produced
by an app power+mode write pair — the firmware set it (§3).

### 5.2 The status-verb strings — found, decode not located [P negative]

A pool-reference scan of the whole .text (1,967,086 instructions
decoded, resyncing scan; script preserved at `/tmp/
scan_mode_strings.py`, targets pp+0x45b20-0x462d0) found every
reference to the home-screen status verbs:

| pool offset | string | references |
|---|---|---|
| pp+0x45df8 / 0x45e00 | `heatingEngaged` / `制热运行中` | 13 / 1 |
| pp+0x45c70 / 0x45c78 | `coolingEngaged` / `制冷运行中` | 13 / 1 |
| pp+0x45b28 / 0x45b30 | `autoModeEngaged` / `自动运行中` | 13 / 1 |
| pp+0x462d0 | `待机` (standby) | 1 |

All 13 references per key are the SAME locale-table builder closure
(first copies: 0x7c2f80/0x7c40ac/0x7c5680/0x7c951c; then 12 more at
exactly 0xa874 spacing = 13 locale tables): the sequence loads a
key, stores it into a per-locale string object (field offsets
0x153/0x157/0x15f/…), loads the translated value, and inserts the
pair (the `bl 0xa62290` map-insert). **No consumer/decoder closure
references these keys — the UI reads the built objects by field
offset, so the value→verb decision code carries no pool-string
fingerprint and was not located this session.** Honest verdict: no
read-plane mode VALUE table recovered (Q5); the 0/4-vs-status-verbs
link (heating engaged / standby) remains a plausible READING
[standing hypothesis G-1 (i)], not a proven decode.

### 5.3 What survives from the prior sessions (cited, unchanged)

The mode handler's pair-write shape (1012+1013 = [mode, setpoint×10],
research 017 §5 [P]) and its heat-only GUARD 2 (field_0x6b != 1 →
the mode-lock dialog) — the app never writes a mode on this unit
[P]. The write-plane mode index {0,1,2} [A]. The H06 heat-only
option list [P apk].

## 6. The logging volume, measured from the code [P]

Per 30 s poll cycle (steady state, healthy link), the
`aqua_client` logger emits: 1 DEBUG TX line; ≥4 INFO block-census
lines (the burst; more if the unit pushes more than one burst in
the 8 s request window); the first idle block push at INFO (once
per connect); plus per-CONNECT: ~6-8 GATT enumeration INFO lines +
`connecting to` + `connect summary` + `Connected to` INFO + 4×
`block push captured` WARNING + `idle window` WARNING. Each button
press adds a confirmation trigger → 4 more census INFO lines.
The owner's rate-limit verdict (`200 messages since last count`) is
consistent with hours of steady state and/or reconnect churn
(`UpdateFailed` cycles disconnect → the next cycle re-pays the
per-connect set). The `coordinator` logger's per-cycle INFO
(`push cycle: trigger read timed out as expected (census: …)`) is
one line per cycle — not the driver; it stays.

## 7. The hypothesis ledger (spec 018)

| # | statement | status |
|---|---|---|
| M-1 | Our proven power write (1011) updates the power mirror 2011 and NOT the mode mirror 2019 | **[P — the WARNING + frame atomicity]** |
| M-2 | The post-write 2001 block is fresh (the confirmation's freshness gate + the 2011 verdicts) — no stale-read defect exists | **[P — H2 refuted]** |
| M-3 | The shipped display code implements contract 015-A §5 exactly; the defect is the contract's D-3 assumption, falsified in our control world | **[P — the trace]** |
| M-4 | The app's power flow writes only 1011; the app never writes a mode on this unit → the firmware set 2019=4 in the app-flow world | **[P disassembly this session + research 017 §5]** |
| M-5 | power=1 ⇒ heating on this unit (heat-only hardware fact) — the display may show HEAT without consulting 2019 | **[D on [P panel/apk] — contract 018-A]** |
| M-6 | 2019 = 4 is a firmware-computed "engaged/heating" status that lags the power command (the front-runner for G-1) | [A — the 08:31 timing is consistent; unproven] |
| M-7 | The block-census INFO volume drives HA's rate limiter; the mute can hide contract evidence | [P code reading + the owner's line; HA's constants unstated] |

## 8. What remains unproven — and the decider for each

| # | gap | decider | blocks 018? |
|---|---|---|---|
| G-1 | The firmware's rule for 2019=4: engaged-status with lag (M-6) vs another app frame vs E5-gated behavior | the long-ON test (quickstart §2: button ON, watch 2019 in the `push_stream` raws ≥5 min); then E5 (HCI snoop of an app session) / the logcat oracle if needed | **NO — the display fix is correct in every branch** (if M-6, 2019 is a future `hvac_action` oracle; else a command mirror we never write) |
| G-2 | The owner's off-state `unknown`: post-OFF transient (§2.5a) vs absent capture (b) vs summary compression (c) | quickstart §1 step 4 (OFF → `off` within ~10-40 s) | NO — the OFF path is unchanged and already correct |
| G-3 | 2019's full value space (observed {0,4}) | any future capture with the unit in a new state; the logcat oracle | NO — the display no longer depends on it |
| G-4 | The read-value→status-verb decode in the app (5.2's unfound closure) | a targeted disassembly pass (the state-object field that feeds the verb selection) or E5 | NO — research-only |
| G-5 | HA's exact rate-limit thresholds (the "200 since last count" window) | HA internals — deliberately not restated | NO — the remediation (volume ↓) is threshold-independent |

## 9. Reproduction

```bash
# the shipped decode + warning (the T1 path):
python3 - <<'EOF'
import sys; sys.path.insert(0, "custom_components/aqua_temp_ble")
from homeassistant.components.climate import HVACMode  # or stub for standalone
EOF
# (the decode is pinned by tests/test_control_015.py:554-651 — run:)
python3 -m pytest tests/test_control_015.py -q

# the power handler re-verification (this session, §5.1):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a15c8 0x6a1830 flags
# the mode handler + mode-lock (research 017 §5):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a2318 0x6a2600

# the status-verb pool scan (§5.2; script kept for the record):
/tmp/blutter-venv/bin/python /tmp/scan_mode_strings.py
# (targets: pp+0x45b20/0x45b28/0x45b30, 0x45c68/0x45c70/0x45c78,
#  0x45df0/0x45df8/0x45e00, 0x462d0; the 13 builder copies first at
#  0x7c2f80 / 0x7c40ac / 0x7c5680 / 0x7c951c)

# the locale-table builder, for the record (a key→value insert pair):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x7c2f60 0x7c3010 flags

# the logging volume drivers (code reading):
grep -n "block push observed" custom_components/aqua_temp_ble/aqua_client.py
grep -n "GATT enumeration\|connect summary\|Connected to" custom_components/aqua_temp_ble/aqua_client.py
```

The owner's quoted lines (the WARNING, `et HEAT ne s'affiche pas` /
`ca met aussi unknown`, the `logging too frequently: 200 messages`
verdict) are the 2026-10-12 production report relayed in this spec's
brief.