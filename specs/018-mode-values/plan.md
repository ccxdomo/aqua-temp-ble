# Spec 018 plan — the power-dominant display + the logging fix

The exact implementation of spec 018 (FR-1..FR-5). Three code files,
one new test file, targeted updates to the suites that pinned the
amended contracts. Everything below is written against main @ b81b604
(manifest 0.13.0).

## 1. `coordinator.py` — the decode (FR-1/FR-2)

### 1.1 The module set (rename, same pattern)

```python
# was:
# Log each unknown Mode value only once per HA session.
# Spec 015 (FR-7/D-3): the same once-per-session rule for the PUSHED
# mode enum — ...
_logged_unknown_push_modes: set[int | None] = set()

# becomes:
# Spec 018 (contract 018-A §3): the once-per-session ledger for
# unmapped NONZERO pushed mode values — a research/calibration
# signal, never a display gate (the display follows power on this
# heat-only unit). 0 and None never enter it: 0 is the observed,
# expected value after our power-only writes (research 018 §1).
_logged_unmapped_push_modes: set[int] = set()
```

### 1.2 The decode block inside `from_pushes`

Replace the `if power is False: … elif power is True: …` block
(the verbatim shipped text is in research.md §1) with:

```python
            if power is False:
                hvac_mode = HVACMode.OFF
            elif power is True:
                # Spec 018 (contract 018-A §2): the display follows
                # POWER on this heat-only unit (H06 [P panel]) — a
                # mode-gated display renders the machine's real
                # running state `unknown`, because our proven power
                # write (1011) updates the power mirror 2011 and
                # never the mode mirror 2019 (research 018 §3, M-1).
                hvac_mode = HVACMode.HEAT
                if (
                    mode_value is not None
                    and mode_value != 0
                    and mode_value not in registers.PUSHED_MODE_VALUE_TO_NAME
                    and mode_value not in _logged_unmapped_push_modes
                ):
                    _logged_unmapped_push_modes.add(mode_value)
                    _LOGGER.info(
                        "pushed mode value %r (reg %s) unmapped — display "
                        "follows power (heat-only, spec 018); update "
                        "registers.PUSHED_MODE_VALUE_TO_NAME for the "
                        "research map (quickstart 018 §2)",
                        mode_value,
                        m_addr,
                    )
```

Notes for the Coder:
- The surrounding `# FR-7 (contract 015-A §5)` comment gains
  `amended by contract 018-A §2` (one line).
- `MODE_NAME_TO_HVAC` keeps its classic-plane consumer (`from_regs`);
  the push decode no longer reads it — no dead code.
- The docstring of `from_pushes` (the D-3 sentence "an unmapped value
  renders None + ONE warning per session; power False wins and
  renders OFF") is rewritten to the 018 rule (power wins; the mode
  mirror is a diagnostic with a once-per-session INFO for unmapped
  nonzero values).

### 1.3 NOT changed (the non-regression ring)

`from_regs` (the classic plane), `from_merged`, `_async_update_push`,
`async_control_write_power`, the properties (`push_control_confirmed`,
`write_control_allowed`), `decode_temp_logged`, the `_logged_implausible`
family. `climate.py` is not touched at all (the entity is a
passthrough — research 018 §2.2).

## 2. `registers.py` — the comment (FR-3)

`PUSHED_MODE_VALUE_TO_NAME` content unchanged (`{4: "heat"}`); the
comment block becomes the observed-values registry note:

```python
# Spec 018 (contract 018-A §3): the OBSERVED-VALUES registry for the
# pushed mode mirror — documentation + the unmapped-diagnostic's
# membership check. It is NO LONGER the display gate: the climate's
# hvac_mode follows power alone on this heat-only unit (H06 [P
# panel]). Observed values [P owner, 2026-10-12]:
#   4 — the app-flow on-state (the 015 diff-revert, 08:31);
#   0 — the off world AND the on-state after OUR power-only writes
#       (our 1011 write never touches the mode plane, contract 016
#       §4 — research 018 §3, M-1): expected, never an error.
# Other values unknown (research 018 §8 G-3); an unmapped NONZERO
# value logs ONE INFO per session per value. The base
# MODE_VALUE_TO_NAME (reg 13, the classic plane) must never be
# polluted from this map (unchanged).
PUSHED_MODE_VALUE_TO_NAME: dict[int, str] = {
    4: "heat",
}
```

## 3. `aqua_client.py` — the census level (FR-4, contract 018-B)

`_census_frame`: the two BLOCK branches log at `logging.DEBUG`
instead of the computed `level` (which stays for the generic
non-block line). The counters (`_req_frame_count`,
`_req_block_starts`, `_req_first_seen`, `_req_last_seen`) are
UNTOUCHED — the timeout message's census depends on them.

```python
        if is_block:
            start, qty = struct.unpack(">HH", frame[2:6])
            if crc_ok:
                self._req_block_starts.append(start)
                # Spec 018 (contract 018-B): the per-frame block
                # census logs at DEBUG — in push mode the bursts are
                # the NORMAL answer (≥4 lines/cycle at INFO drove HA's
                # rate limiter past its threshold, muting contract
                # evidence; the census survives in the store, the
                # once-per-connect capture WARNING and the timeout
                # summary). The counters above are unchanged.
                _LOGGER.log(
                    logging.DEBUG,
                    "block push observed: start=%d qty=%d len=%d crc=%s "
                    "(waiting for start %s; t+%.2fs)",
                    ...same args...
                )
            else:
                _LOGGER.log(logging.DEBUG, ...same...)
```

The line TEXT stays byte-identical (only the level changes — the
008/009 suites' message assertions keep working; their LEVEL
assertions move, see §4.3). The first-idle INFO line
(`_idle_census`) is unchanged (once per connect — bounded).

## 4. Tests

### 4.1 New file `tests/test_mode_display_018.py` (AC-1..AC-4)

The truth table (from_pushes, gate open, scripted 2001 captures):
- (2011=1, 2019=0) → `HVACMode.HEAT`, and ZERO records containing
  `Unknown pushed Mode` at ANY level (AC-2);
- (1, 4) → HEAT; (1, None) → HEAT, zero warnings (AC-1/AC-2);
- (1, 7) → HEAT + exactly ONE INFO `pushed mode value 7 (reg 2019)
  unmapped …` per session per value; a second decode of 7 logs
  nothing; (1, 8) logs its own single INFO (AC-3);
- (0, 0/4/7) → OFF and NO mode records at all (the OFF branch never
  reads mode — AC-1);
- gate closed / no capture → `hvac_mode is None` (AC-1);
- the census level: a block frame while a request is pending → the
  `block push observed` record at DEBUG only; the timeout census
  summary still counts it (AC-4).

### 4.2 `tests/test_control_015.py` (AC-6)

- The FR-7 decode tests that pinned the mode gate flip to the new
  truth table: the `raws[2019] = 7` test (line ~625: "unknown + ONE
  warning") becomes HEAT + the once-per-session INFO; any
  `(2011=1, 2019=0)` case becomes HEAT.
- The cases whose verdicts DO NOT change keep passing untouched:
  gate-closed → None (line ~554/576), power 0 → OFF (~582/611/620),
  mode 4 → HEAT (~605/710), the entity read-only surface tests.
- The docstring/comment citations move from contract 015-A §5 to
  contract 018-A (the suite documents the amendment, not a silent
  rewrite).

### 4.3 The census-level suites (AC-4)

- `tests/test_census_009.py` (~lines 264-295, 430, 465): the
  "block push observed" LEVEL assertions drop from `>= INFO` to
  `>= DEBUG` (the message-text assertions unchanged).
- `tests/test_push_store_013.py` (~286-289): same level move.
- `tests/test_reassembly_matching.py` (~283-385): the census
  collectors read at DEBUG.
- Verify only — expected untouched: `test_climate_control_017.py`
  (its scripted mirrors decode 2011=1/2019=4 → HEAT and 2011=0 →
  OFF: same verdicts), the 013/014 suites (the poll cycle and the
  store are untouched), `test_control_015.py`'s registry test
  (`PUSHED_MODE_VALUE_TO_NAME == {4: "heat"}` — content unchanged).

### 4.4 Full run

`python3 -m pytest tests/ -q` — the whole suite green (AC-5/AC-6).

## 5. Version + docs

- `manifest.json` → `0.13.1` (fix release).
- `README.md`: check for any climate-display wording that says
  "unknown while on" — update if present (expected: none).

## 6. Risks / non-regression matrix

| Change | Risk | Mitigation |
|---|---|---|
| power=True → HEAT unconditionally (push plane) | a future NON-heat-only unit on the push plane would display HEAT wrongly | the push plane is THIS unit's world (015's homes are per-unit evidence); the classic plane keeps the mode gate; contract 018-A §1 scopes the rule to the push plane; a future multi-mode unit gets its own spec |
| the WARNING removal | a genuinely broken 2019 decode would no longer surface at WARNING | the display no longer depends on 2019 — a wrong 2019 cannot mislead the tile; the push_stream diagnostic carries the raw gate-independently; unmapped nonzero still logs (INFO) |
| census at DEBUG | less INFO visibility of block traffic in the log page | the per-cycle timeout census summary (coordinator INFO) + the capture WARNINGs + the push_stream diagnostic keep the traffic visible at one line per cycle |
| set rename | churn in the 015 suite's references | mechanical; the suite is updated in the same PR |

## 7. What the Coder must NOT do

- No write to 1012/1013/1076 or any register outside
  `ALLOWED_WRITE_CONTROL` (contract 016 §4).
- No change to `hvac_action` (power=True keeps `None`).
- No change to the classic plane, the gates, the confirmation cycle,
  the funnel, the entity properties.
- No speculative extension of `PUSHED_MODE_VALUE_TO_NAME` beyond
  observed values.
- No touch to specs 001-017 (018 amends via its own contracts).