# Plan 014 — the request-triggered poll cycle: what ships, what stays

Status: **PLANNED** — the design that implements spec.md's FR/AC from
research.md's evidence. Normative rules: `contracts/query-cycle.md`.
One production file changes (`coordinator.py`); everything else is
tests, strings and the version bump — the diff is deliberately tiny
because the listen half already exists and is production-proved
(research 014 §7).

## 1. The strategy in one paragraph

The unit answers requests — that is the whole discovery of this spec —
and it answers them with its parameter-dataset block frames, never with
base-block data. The push-mode poll cycle therefore LEADS with the one
request the unit has answered six times (the vendor's own base read,
read-only, allowlisted), LISTENS with the harvest machinery spec 013
already built (instant return when the T-block is in the store), and
applies the unchanged freshness gate and entity mapping. The cycle's
failure literal changes to name the new, stronger verdict (`push stream
silent after trigger`); a trigger that ever COMPLETES (the revival
world) is logged once and its base data merged into the snapshot. The
connection test, the client, the flow, the mapping and the gate are
untouched — they produced the evidence this spec consumes.

## 2. Component design

### 2.1 `coordinator.py` — the trigger+listen cycle (FR-1/FR-2)

```python
async def _async_update_push(self) -> AquaTempData:
    async with self._io_lock:
        if not self.client.is_connected:
            await self.client.connect(
                pair=self.config_entry.options.get(
                    "pair_on_connect", const.DEFAULT_PAIR_ON_CONNECT
                )
            )
        # 1. TRIGGER (contract 014 §2): ONE vendor read — the frame the
        #    unit demonstrably answers with its block burst (research
        #    014 §6). On this unit: AquaTempTimeoutError after 8 s —
        #    EXPECTED; the store fills during the window.
        try:
            regs = await self.client.read_registers(
                registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
            )
        except AquaTempTimeoutError as exc:
            _LOGGER.info(
                "push cycle: trigger read timed out as expected (census: %s)", exc
            )
            regs = None
        except AquaTempError as err:
            await self.client.disconnect()
            raise UpdateFailed(f"trigger read failed: {err}") from err
        captures = dict(self.client.pushed_blocks)
        # 2. REVIVAL (contract 014 §5): the trigger ANSWERED — the base
        #    plane is back. Log once per session, use the base data.
        if regs is not None:
            self._log_trigger_answered_once()
            return AquaTempData.from_merged(
                regs, captures, self.push_temp_confirmed
            )
        # 3. LISTEN (contract 014 §3): presence-based harvest on the
        #    already-subscribed pipe — instant when the burst already
        #    landed during the trigger window.
        window = self.push_freshness_window()
        t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
        now = _now()
        if t_cap is None or now - t_cap.last_seen > window:
            caps = await self.client.harvest_pushes(
                {registers.PUSH_TEMP_BLOCK_START},
                const.PUSH_HARVEST_TIMEOUT,
            )
            if caps:
                captures.update(caps)
                t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
            now = _now()
        # 4. FRESHNESS (contract 013 §6.1 unchanged) + the new verdict.
        if t_cap is None or now - t_cap.last_seen > window:
            await self.client.disconnect()
            raise UpdateFailed("push stream silent after trigger")  # contract 014 §4
        if registers.BASE_BLOCK_START in captures:
            self._log_push_mode_base_anomaly()   # unchanged 013 helper
        return AquaTempData.from_pushes(captures, self.push_temp_confirmed)
```

Notes on the shape (each pinned by an AC):

- The revival check runs **before** the freshness failure — a
  completed trigger must never fail the cycle for lacking pushes
  (the base answer IS the data).
- The trigger is single-shot (no `resend_*`): the burst outlasts the
  TX (research 014 §5 M-5/M-6); the ladder recycles the link on
  failure. The re-send bounds stay a named one-line fallback
  (research 014 §7).
- The write type is the client's resolved default (without-response,
  app parity — research 010 §2.3). The `force_response=True` variant
  is the named fallback lever, not an option (research 014 §7).
- `_log_trigger_answered_once()`: the module-set pattern
  (`_logged_implausible` precedent) — WARNING, once per HA session:
  `"push mode: trigger read answered — the request path revived;
  using base data (mode stays push; a future spec re-evaluates)"`.

### 2.2 `AquaTempData.from_merged(regs, captures, confirmed)` (FR-2)

```python
@classmethod
def from_merged(cls, regs, captures, confirmed) -> AquaTempData:
    """Base-plane answer + pushed overlay (spec 014 FR-2, contract 014 §5)."""
    base = cls.from_regs(regs)               # typed fields, base wins
    t_cap = captures.get(registers.PUSH_TEMP_BLOCK_START)
    pushed_regs = (
        dict(t_cap.registers) if (t_cap is not None and confirmed) else {}
    )
    counts = {int(s): c.count for s, c in captures.items()}
    return cls(
        registers={**regs, **pushed_regs},   # disjoint windows 7-51 / 2046-2090 [P]
        power=base.power, boost=base.boost, mode_value=base.mode_value,
        hvac_mode=base.hvac_mode, target_temp=base.target_temp,
        min_target=base.min_target, max_target=base.max_target,
        inlet_temp=base.inlet_temp, outlet_temp=base.outlet_temp,
        ambient_temp=base.ambient_temp, fault_code=base.fault_code,
        firmware=base.firmware, binaries=base.binaries,
        mode="push", push_confirmed=confirmed, push_counts=counts,
        push_first_seen=t_cap.first_seen if t_cap is not None else None,
        push_last_seen=t_cap.last_seen if t_cap is not None else None,
    )
```

Why the merge is safe: the base block (7-51) and the T-window
(2046-2051) are numerically disjoint — the dict union cannot shadow
either plane [P]. The push-mode entity surface reads pushed addresses
(`sensor.py` unchanged) — still fed from `pushed_regs`; the typed base
fields are visible in the snapshot/diagnostics and are the evidence a
future mode re-evaluation reads. The honesty gate applies ONLY to
pushed raws, exactly as in `from_pushes` (contract 013 §4.3 — base
values are base-pinned registers, not unconfirmed pushes).

### 2.3 No changes — verified by design (FR-5, AC-6)

| File | Why untouched |
|---|---|
| `aqua_client.py` | the store, harvest, census, probe and re-send machinery are the PROVEN halves (research 014 §7); the trigger is an ordinary `read_registers` call — the client already does everything |
| `config_flow.py` | the 22:41 run is the end-to-end proof of the flow as shipped: its own stages trigger the burst, its harvest stage succeeds |
| `registers.py` | the mapping, the gate and the integrity rules are unchanged (the second T-window capture CONFIRMS the anchor — research 014 §8.1) |
| `sensor.py` | push-mode addressing reads `data.registers` at `PUSHED_TEMPS` addresses — fed identically by `from_pushes` and `from_merged` |
| `const.py` | the trigger reuses `BASE_BLOCK_START/COUNT` + `REQUEST_TIMEOUT`; the listen reuses `PUSH_HARVEST_TIMEOUT` — no new constants |

### 2.4 Strings / README (FR-4)

- `strings.json` + `translations/en.json` + `translations/fr.json`:
  the `confirm_push` description's second sentence changes from "it
  continuously pushes live data that the integration now reads without
  sending anything" to the truth (EN draft): *"the unit answers each
  poll request with a live-data report that the integration reads"*
  — and the options-flow `push_temp_confirmed` labels stay as-is. Same
  keys, updated content, both languages.
- `README.md` push-mode section (build 0.9.0 paragraph): the cycle
  description gains the trigger sentence; the "0.9.0 ships" paragraph
  becomes "0.10.0 ships" with the one-behavior-change summary.

### 2.5 Tests

- **New `tests/test_coordinator_query_014.py`** (red-first):
  - AC-1: timeout-trigger + scripted store feed ⇒ snapshot, exactly one
    `read_registers(7,45)`, no other writes, INFO line asserted;
  - AC-2: no feed, faked short harvest ⇒ `UpdateFailed("push stream
    silent after trigger")` + disconnect;
  - AC-3: `AquaTempConnectionError` ⇒ `UpdateFailed("trigger read
    failed: …")` + disconnect;
  - AC-4: answered trigger ⇒ `from_merged` field-by-field (base typed
    fields; pushed raws in `registers` only when confirmed; log-once);
  - cycle-order guard: the revival branch runs before the freshness
    failure (an answered trigger with an empty store still succeeds).
- **Re-pinned `tests/test_coordinator_push_013.py`** (FR-3): the
  failure literal; the zero-`read_registers` assertion inverts to
  exactly-one — each with a comment citing contract 014 §4.
- Everything else re-runs green untouched (AC-6).

## 3. Edge cases, decided

| Edge | Decision |
|---|---|
| Store holds a STALE T-block (same connection) and the new burst omits it | harvest returns on presence; the freshness gate fails; the literal fires; the link recycles — the 013 defensive stance, unchanged (contract 014 §4.2) |
| Burst arrives AFTER the trigger window (latency tail) | the harvest's 30 s listen catches it (research 014 §5 M-2 bounds: 0.2-5 s observed) |
| Trigger completes with a start-7 BLOCK frame (never observed) | `read_registers` already decodes it as base regs 7-51 (code trace) — the revival path handles it identically |
| Two cycles back-to-back (poll interval < cycle time) | the io lock serializes; DataUpdateCoordinator never overlaps a running cycle; worst case 38 s/cycle is documented (contract 014 §3) |
| `pair_on_connect` enabled | threaded unchanged into connect (FR-5) — the option's evidence gate (contract 011 §1.5) stays the authority |

## 4. What the follow-up spec inherits

1. **The 2001-2045 paste** (quickstart §0 — no run needed): the four
   `block push captured` hex lines of the 22:41 log close spec 013's
   U-1; the follow-up maps the block (and re-checks control-data homes
   — research 014 §8.4).
2. **The query-block experiment** (quickstart §3): if probe block=2046
   yields a DIRECT 2046 block answer, the trigger upgrades to
   `build_query_block(2046, 45)` in one line — tighter windows, the
   literal per-block request/response the app itself uses.
3. **The revival evidence**: if the FR-2 WARNING ever fires in
   production, the follow-up re-evaluates the mode (contract 013 §4.2's
   future decision, now with data).
4. **E5** (HCI snoop) and the long-run U-3' evidence (the per-cycle
   census lines + `push_stream` diagnostic) — unchanged trackers.

## 5. Test plan summary (mirrors AC-1..7)

1. `tests/test_coordinator_query_014.py` — the five vectors above
   (fakes: client stub whose `read_registers` raises/returns per test;
   store fed by direct `PushCapture` construction — the 013 suite's
   fake patterns, `SimpleNamespace` shapes per its lessons learned).
2. Re-pins in `test_coordinator_push_013.py` — literal + inverted
   assertion, comments citing contract 014 §4.
3. Full `pytest tests/` + `python3 custom_components/aqua_temp_ble/modbus.py`
   self-test green; `git status` clean outside spec 014 NFR-1's list;
   manifest 0.10.0.