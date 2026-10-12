# Contract 018-A — The climate display contract (push plane)

Normative rules for decoding the push-mode climate's `hvac_mode`. This
contract AMENDS contract 015-A §5's `hvac_mode` line (whose text stays
authoritative for everything else); it governs the PUSH plane only —
the classic plane (`from_regs`, `MODE_VALUE_TO_NAME`) is untouched and
keeps the mode-gated decode (a different unit's calibration governs
it; its unknown-mode honesty rule stands).

> The display follows the PROVEN power mirror. On a heat-only unit,
> "running" and "heating" are the same fact. A command mirror our
> write path never touches must never gate the shown state.

## §1 The truth table (normative)

Inputs: the latest 2001-block capture, behind the 015 control
confirmation gate (`control_confirmed`). `power` = `decode_flag(raw
2011, active_when_nonzero=True)`; `mode_value` = the raw 2019 value.

| power | mode | `hvac_mode` | Note |
|---|---|---|---|
| `None` | — | `None` (tile `unknown`) | no capture or gate closed — honest, unchanged (015 FR-11c) |
| `False` | — (never read) | `HVACMode.OFF` | [P owner diff-revert 2026-10-12: 0→1→0] |
| `True` | any | **`HVACMode.HEAT`** | heat-only hardware fact (H06 [P panel]); the mode mirror NEVER gates the result |

There is NO fourth row. No mode value — 0, 4, unmapped, absent — can
change the output while power is known.

## §2 Why power wins (the evidence, not a preference)

1. The mode space of THIS unit is {off, heat} by hardware and by the
   vendor's own operating envelope: H06 "Mode type" = heat-only
   [P panel]; the app's mode handler refuses mode changes on
   heat-only units (GUARD 2, research 017 §5 [P disassembly]); spec
   017 FR-8 already ships `hvac_modes = [OFF, HEAT]` on that evidence.
   A state machine that advertises {OFF, HEAT} and displays a third
   value (`unknown`) for a running machine contradicts its own
   advertised state space.
2. Our write plane is power-only BY CONTRACT (016 §4: the mode pair
   1012/1013 is excluded — an unpinned pair register, research 017
   §5). A display that requires the mode mirror to move renders every
   button-controlled ON permanently `unknown` (research 018 §3, H1
   [P]: a fresh post-write frame carries `2011 = 1, 2019 = 0`).
3. Displaying HEAT for power = 1 is NOT invention: it is the only
   mode a heat-only machine can be in while running. The dishonest
   display is the current one — hiding a known fact behind an
   unmapped mirror value.

## §3 The mode mirror's role after this contract

- `mode_value` stays in the snapshot and in the gate-independent
  `push_stream` diagnostic raws (015 FR-10) — the research surface.
- An unmapped NONZERO value while power is on → ONE INFO line per
  value per session (the module-set pattern). Level: INFO — a
  research signal, not a display defect (contract 012 §2's WARNING
  discipline is reserved for operator-actionable evidence).
- 0 and None are silent: 0 is the PROVEN value in our control world
  (research 018 §1) and the expected off-world value [P owner diff];
  None is the defensive absent case.
- `PUSHED_MODE_VALUE_TO_NAME` = the observed-values registry
  (content unchanged, `{4: "heat"}`); it is no longer a display gate.
  Extending it requires new observed evidence (the 015 §8.1
  discipline); the base `MODE_VALUE_TO_NAME` must never be polluted
  from it (unchanged).

## §4 What this contract does NOT change

- `hvac_action` (OFF when power is False; `None` otherwise — the
  compressor binary is uncalibrated [V]; power on ≠ actively
  heating).
- The OFF and unknown paths: power False → OFF, power None →
  unknown — byte-identical to 015.
- The confirmation cycle, the funnel guard, the gates, the entity
  properties (017's surface stands).
- The classic plane's decode.

## §5 Test obligations

The truth table is pinned by tests: (1,0)→HEAT, (1,4)→HEAT,
(1,None)→HEAT, (1,7)→HEAT, (0,*)→OFF, no-capture/gate-closed→None;
zero records containing `Unknown pushed Mode` at any level; the
once-per-session INFO diagnostic. A regression that reintroduces a
mode gate on the push plane fails these tests.