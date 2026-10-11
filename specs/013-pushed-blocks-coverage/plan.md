# Plan 013 — the push-plane path: what ships, what stays blocked

Status: **PLANNED** — the design that implements spec.md's FR/AC from
research.md's evidence and data-model.md's mapping. Normative rules:
contracts/push-harvest.md.

## 1. The strategy in one paragraph

The unit answers no request we can currently send (att-accepted-silent,
H-APP-GATE) but volunteers four CRC-valid parameter blocks on the
notify pipe we already subscribe to — and block 2046-2090 carries the
T-family live temperatures (sentinel-anchored window, live-proven
member). So the integration grows a **second read plane**: a pure-listen
harvest that sends nothing, stores everything, and feeds the same
entity keys the base plane was meant to feed. The connection test
succeeds when the T-block pushes arrive (mode `push`), the coordinator
in push mode stops burning 8 s per cycle on a read the unit ignores,
and the temperature entities appear behind a one-run calibration gate.
Control (power/mode/setpoint) has no proven home in the pushes and
stays honestly out: no climate entity in push mode, no writes, no
optimistic state. The request path and its instrumentation (008-012)
stay byte-identical for units that answer — and as the standing
diagnostic for the day E5/pairing revives them.

## 2. Component design

### 2.1 `aqua_client.py` — store + capture + harvest (FR-1/2)

```
connect():  reset _pushed (per-connect state, like the census)
_on_notify / _idle_census:  every CRC-valid BLOCK frame →
    regs = parse_block_response(frame, start_from_frame, qty_from_frame)   # start comes FROM the frame
    _pushed[start].registers = regs (latest wins; keep first/last ts, count++)
    first-per-start → _LOGGER.warning("block push captured: start=%d … hex=%s", …, frame.hex())
    repeats → existing census lines (byte-identical)
_idle summary → WARNING (level change only)
```

- The idle path gains the accumulate/boundary loop (today it is
  per-chunk and would lose fragmented frames — the harvest must not:
  AC-1's chunked vector). The pending-request path already extracts
  complete frames — it feeds the store BEFORE the census/match step.
- `harvest_pushes(wanted_starts, timeout)` — no TX; awaits pushes;
  partial results allowed; returns `{start: PushCapture}`.
- The store is mode-agnostic (kept in classic mode too — free
  documentation of the stream).

### 2.2 `config_flow.py` — the harvest stage (FR-3)

```
_async_test():
    connect → first-read (unchanged) → on AquaTempTimeoutError:
        probe (unchanged) → regs None:
            stage("push-harvest")
            _LOGGER.warning("push harvest: listening %ds for block pushes (no frames sent)…", t)
            caps = await client.harvest_pushes({1001, 1046, 2001, 2046}, 30)
            if 2046 in caps:
                _LOGGER.warning("push harvest: T-block captured (start=2046 ×%d); "
                                "T-window raws: 2046=%d 2047=%d 2048=%d 2049=%d 2050=%d 2051=%d", …)
                → success, entry data records mode="push"
            else: raise first_timeout (existing no_response path, byte-identical)
```

- Budget check [P arithmetic]: ~5 + 8 + 8 + 30 ≈ 51 s < 70 s budget.
- The stage label plumbing follows the 007/011 conventions
  (`self._stage = "push-harvest"`; the final failure line stays the
  first-read literal — 012-pinned).
- Confirm step: add the push-mode description string (additive
  translation; existing keys untouched).

### 2.3 `coordinator.py` — push mode (FR-4/6)

- `_async_update_data()` branches on `mode`:
  - classic: unchanged single FC03 read.
  - push: **zero I/O requests** — read the store; freshness gate
    `now - last_seen ≤ max(2×interval, 60)`; build `AquaTempData`
    from pushes (2.4); stale → `UpdateFailed("push stream silent")`
    (the next cycle's connect recycles the link — U-3 guard).
- `AquaTempData.from_pushes(pushed, confirmed)`:
  - temp fields from PUSHED_TEMPS mirrors (only when confirmed;
    else `None` → entities `unknown`);
  - power/mode/target/fault/firmware → `None` (absent by design);
  - `binaries` → empty (homes unknown);
  - base-plane answer ever arrives → `from_regs` result wins
    (contracts §4; merge in the classic branch is unchanged — the
    classic path never enters push mode).
- `push_stream` diagnostic entity: state from freshness, attributes
  from the store (counts, ages, mode). Sensor platform addition,
  diagnostic category.

### 2.4 `registers.py` + sensor platform (FR-5/7)

- The constants of data-model §6; `PUSHED_SENSORS` registry (push-only
  keys: suction/coil/exhaust) + `PUSHED_MIRRORS` on the base keys
  (inlet/outlet/ambient → 2047/2048/2050).
- `check_registry_integrity` extension (per-plane windows/duplicates;
  AC-6).
- Sensor platform: creation rules per FR-5; values via the
  coordinator snapshot (single decode path — no second decoder).
- Options: `push_temp_confirmed` (bool, default False), set by the
  confirm-step checkbox after the calibration run (quickstart §2);
  flipping it + restart creates the entities (repo discipline: a
  calibration switch, like `WRITE_ECHO_EXPECTED`/`WRITE_MODE_SETPOINT_TOO`).

## 3. Entity coverage — the honest table (research §4 Q5, data-model §6)

| Entity | Push mode | Classic mode | Source plane(s) |
|---|---|---|---|
| `inlet_water_temp` | ✅ value (after confirmation) | ✅ (unchanged) | base 26 / push 2047 |
| `outlet_water_temp` | ✅ (after confirmation) | ✅ | base 27 / push 2048 |
| `ambient_temp` | ✅ (after confirmation) | ✅ | base 28 / push 2050 |
| `coil_temp` | ✅ (push-only key, after confirmation) | unchanged placeholder rules | push 2049 |
| `exhaust_temp` | ✅ (after confirmation) | unchanged | push 2051 |
| `suction_temp` | ✅ reads `unknown` (sentinel — honest) | unchanged | push 2046 |
| `fault_code` | ❌ not created (no pushed home) | ✅ | base 47 |
| `firmware` | ❌ not created | ✅ | base 48-51 |
| climate | ❌ not created (no control data) | ✅ | base 8/13/14 |
| O/S binaries | ❌ (homes unknown [V]) | placeholders as today | maybe 2001 [V] |
| `push_stream` (diagnostic) | ✅ (new) | n/a (store still kept) | store |
| H/R values | logs/probe only — never entities (spec 003 §6) | same | push 1018-1023 / 1046-1060 |

**Functionality lost in push mode (stated in the UI note):** control
(climate), fault code, firmware. **Gained:** live temperatures at push
latency (≤ ~4 s observed — better than the 30 s poll), the 2001-2045
capture, and a working integration on a unit whose request path is
dead.

## 4. What the follow-up spec inherits (the open threads)

1. **The 2001-2045 capture analysis** (U-1): if the FR-1 hex reveals
   S/O/D/P homes (or base-control mirrors), the follow-up extends the
   pushed registry and possibly the entity table; if it reveals
   nothing new, the parameter families stay probe-visible only.
2. **E5** (HCI snoop, quickstart §3) — the control-path verdict: does
   the unit answer the phone's FC03? If yes → H-APP-GATE is
   central-keyed; the landed `pair_on_connect` one-shot test becomes
   interesting (Track A revives). If no → the app itself is push-fed;
   control needs a write-path investigation.
3. **U-3** long-run push behavior — the FR-6 diagnostic's data decides
   reconnect cadence.
4. The `1091/1136` query blocks (never pushed [P]) — reachable only
   through the request path; parked with Track A.

## 5. Test plan (mirrors AC-1..7)

- `tests/test_push_store_013.py`: store decode (pinned vectors,
  2047 140→145 across two frames), 4-WARNING-capture lines, repeat
  census byte-stability, chunked-arrival-while-idle lands in store.
- `tests/test_harvest_013.py`: zero-TX invariant (fake write char),
  completion on wanted start, timeout partials, no-raise.
- `tests/test_flow_harvest_013.py`: the three flow worlds (success /
  no-push failure / probe-answered-no-harvest), budget arithmetic,
  stage label, entry mode field.
- `tests/test_coordinator_push_013.py`: zero read_registers calls,
  freshness gate (faked clock), UpdateFailed on stale, from_pushes
  mapping incl. confirmed=False → unknown, sentinel → unknown.
- `tests/test_registers_push_013.py`: integrity extension vectors,
  permuted-mapping single-source test.
- Existing suites re-run green (008/009/011/012 pinned formats
  untouched — the only level change is the idle summary, asserted
  WARNING now).