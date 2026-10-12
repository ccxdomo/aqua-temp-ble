# Spec 020 — Architecture (as shipped)

This file documents the **current** architecture of
`custom_components/aqua_temp_ble` (manifest 0.15.0) — the structure a
maintainer needs before touching anything. Everything described here is
PROVEN in code; where behaviour was proven on the wire it is tagged [P].

Revised after spec 022: the activation gates were removed; everything
is active by default. The automatic write protections are unchanged.

---

## 1. Module map

```
custom_components/aqua_temp_ble/
├── const.py         # protocol constants, timeouts, option defaults, GATT buckets
├── modbus.py        # pure protocol layer: frame build/parse + self-test (no I/O)
├── registers.py     # THE single source of truth: register map, allowlists, value rails
├── aqua_client.py   # BLE transport: GATT resolution, reassembly, pushed store,
│                    #   request/response, write seams, harvest, security probe
├── coordinator.py   # DataUpdateCoordinator: poll cycle, snapshot decode,
│                    #   write confirmation cycles, proof probes
├── climate.py      # the climate entity (full surface, active by default)
├── sensor.py        # temperature sensors + push_stream diagnostic
├── button.py        # diagnostic buttons: probe / power probe / setpoint probe
├── config_flow.py   # discovery menu → device list → live test → confirm
│                    #   (name only; the screen shows the captured values)
├── __init__.py      # setup/unload, options listener, 3 domain services
└── services.yaml / strings.json / translations/
```

Layering rule: `modbus.py` and `registers.py` are stdlib-only (importable
outside HA by tools and tests). `aqua_client.py` imports only those plus
`bleak` (+ HA bluetooth caches when `hass` is passed). Everything
Home-Assistant-facing lives above the client.

## 2. The read plane: trigger + listen

Entry data carries `mode: "push"` (stamped at setup by the config flow;
"classic" entries would take the FC03 path — unreachable on this unit).

**Poll cycle** (`coordinator._async_update_push`, under one `asyncio.Lock`):

1. **Connect** if the link is down — bleak-retry-connector, ≤2 attempts,
   then subscribe to the notify characteristic (see §4). The unit accepts
   a single BLE connection; the vendor app holding it is the common cause
   of a failed connect.
2. **TRIGGER**: ONE vendor base read `63030007002d3c54` (regs 7-51 — the
   frame the unit demonstrably answers). On this unit `read_registers`
   raises `AquaTempTimeoutError` after `REQUEST_TIMEOUT` (8 s) — the
   **expected** world: the answer IS the block burst that arrives on the
   notify pipe while the request waits. [P]
3. **LISTEN**: the notify handler assembles frames (boundary rule →
   silence timer 0.6 s → request deadline) and feeds **every CRC-valid
   block frame** into the pushed store, even while the request is pending.
   If the T-block is missing or stale after the trigger, one bounded
   zero-TX harvest (`harvest_pushes`, default window 30 s) collects the
   rest of the burst.
4. **FRESHNESS**: the T-block capture must be younger than
   `max(2 × poll_interval, 60 s)`. Stale/absent → `UpdateFailed("push
   stream silent after trigger")` + disconnect (entities go unavailable —
   honest, never stale values).
5. **SNAPSHOT**: `AquaTempData.from_pushes(captures)` — the T-block
   feeds the temperature registers; the control block (2001) feeds
   power/mode; the setpoint decodes from the 1046-block that *contains*
   its home (the block-source rule). Unconditional decode (active by
   default, spec 022): an absent block still decodes `None`, sentinels
   and implausible raws still decode `unknown`.

A trigger that is **answered** (FC03 data — never observed) would log the
revival WARNING and use base data — the deliberate escape hatch for a
future firmware world.

## 3. The pushed store

`aqua_client.pushed_blocks: dict[int, PushCapture]` — one entry per block
start (`PushCapture`: start, qty, registers, first_seen, last_seen, count).
Latest-frame-wins per start; every capture is CRC-validated before
storage; the first capture of each start per connect logs its full hex at
WARNING (bounded: ≤4 lines/connect; the hex was demoted to DEBUG by the
021 cleanup and restored by spec 022 FR-13 — it is the passive capture
instrument for the fault hunt). The store is the *only* source the
snapshot decode reads in push mode — the entity plane never touches raw
notifies.

## 4. GATT resolution (spec 010, proven)

- **Notify = `ff02`**, **write = `ff03`** — selected property-blind by the
  vendor app's own UUID buckets (notify `{ff02, 2216, 2a00}`, write
  `{ff03, 2215, 2aa6}`), under service `ff01`. [P disassembly + live]
- Writes go **without response** (the app's only WriteType); the single
  legal divergence: a characteristic advertising only `write` forces
  with-response (ATT constraint, logged).
- Adaptive fallback (`_find_write_candidate`) exists for unknown GATT
  layouts; it can never pick a standard SIG service. The historic ff01
  constant was a DEX misreading (never used by the app).

## 5. The entity surface (push mode)

All push-mode entities are created unconditionally — active by default
(spec 022); no confirmation step, no option, no switch stands between
a fresh entry and the full surface.

| Platform | Entities |
|---|---|
| sensor | Inlet/Outlet/Coil/Ambient/Exhaust water + Suction temperature (6 keys; T01 shows `unknown` — absent sensor) |
| sensor | `Push stream` diagnostic (on/off + counts, ages, control-candidate raws: 2012 + the 2017/2018 fault candidates, spec 022) |
| climate | 1 entity (OFF/HEAT display + setpoint read + both writes) |
| button | Probe registers / Power write probe / Setpoint write probe (diagnostic) |
| binary_sensor | none (no pushed homes; the platform was pruned in the 021 cleanup pass) |

Climate surface (unconditional in push mode):

- hvac modes `[OFF, HEAT]` (heat-only rule, spec 018 — COOL/AUTO are
  refused: the vendor app locks the mode and the mode write would
  clobber an unpinned register pair);
- `TURN_ON`/`TURN_OFF` (where the HA version defines them) +
  `TARGET_TEMPERATURE`, step 0.5, bounds 10.0–40.0 °C (the guard's own
  constants), target displayed from mirror 1046.

`extra_state_attributes` exposes the last power/setpoint write's stage
dict (value, verdict, mirror raw, age) — diagnostics only; the tile's
truth is always the mirror.

## 6. The write paths (the confirmation cycle)

Both control writes follow the same shape
(`coordinator.async_control_write_power` / `…_setpoint`):

```
validate (setpoint: finite, 0.5-multiple, 10.0–40.0 — loud ValueError)
  → registers.ensure_writable(addr, value)   # the funnel: allowlist + rail
  → connect if needed
  → audit INFO line ("power write via 1011 = 1 — mirror should follow…")
  → [io lock] fire-and-forget write on the client rail (no FC10 echo wait)
  → ONE trigger read ("the write does not wake the report; the trigger does")
  → wait ≤ PROBE_MIRROR_TIMEOUT (20 s) for a fresh block (last_seen > t_write)
      → mirror == requested   → verdict "confirmed", snapshot pushed
      → mirror != requested   → verdict "not-confirmed", snapshot pushed
      → no fresh block        → verdict "unconfirmed", NO snapshot;
                                an unconfirmed OFF raises a persistent
                                notification (a running pump is never a
                                silent end state)
```

The snapshot push (`async_set_updated_data` — a PLAIN sync method, never
awaited: the 017 production lesson) is built by the same `from_pushes`
the poll cycle uses — the entity plane reflects the mirror, never the
request. The climate raises `HomeAssistantError` on refused writes so HA
surfaces them.

## 7. What is deliberately dormant

- **Classic-mode paths** (`from_regs`, base-block entity creation, the
  classic climate write branches, `write_registers` + FC10 echo wait):
  reachable only if a unit ever answers the classic read — kept for
  other-firmware tolerance, unreachable on this machine.
- **The binary_sensor plane**: pruned in the 021 cleanup pass — no
  register was ever pinned (no base pin, no pushed home), so no binary
  entity could exist without guessing ("no unproven data under a
  label"). Spec 022 pins the evidence bar for its justified return
  (a fault entity, behind a recorded fault diff-revert).
- **`from_merged`** (base answer + pushed overlay): the never-observed
  revival world; one WARNING if it ever fires.
- **Probe button "base"** — fixed in the 021 cleanup pass: on a push
  entry it no longer reads register 7 (never answered) — it triggers
  the burst and dumps the whole pushed store with labels; the classic
  direct read survives only on the dormant classic plane.

## 8. Services

| Service | Purpose |
|---|---|
| `aqua_temp_ble.probe_registers` | read a block and log every register with labels (calibration aid); response-optional |
| `aqua_temp_ble.power_write_probe` | the spec-016 diff-revert proof: ON frame → mirror 2011 must follow → OFF frame → must revert; one WARNING verdict line |
| `aqua_temp_ble.setpoint_write_probe` | the spec-019 two-phase proof: no-op write (nothing can change) + neighbor check, then −0.5 °C dip → mirror 1046 follows → revert → verifies; one WARNING verdict line |

The probes are **measurement instruments**, not control surfaces: they
bypass the funnel through their own validated seams (`write_probe_frame`
pinned-frames-only; `_setpoint_write_frame` value rail), always start from
a verified baseline, and always aim to leave the unit unchanged.

## 9. Pairing (pruned)

The spec-011 pairing ladder (`agent.py`, `client.pair()`, the
`pair_on_connect` option) never earned its evidence: the production
verdict was `att-accepted-silent` (writes accepted without bonding),
so none of it ever ran in anger. The 021 cleanup pass pruned the whole
surface; the verdict and law L5 (research.md) stand as the record.

## 10. Timing constants (const.py)

| Constant | Value | Meaning |
|---|---|---|
| `REQUEST_TIMEOUT` | 8.0 s | read window = the burst window (timeout = expected) |
| `SILENCE_TIMEOUT` | 0.6 s | chunk gap that completes a fragmented frame |
| `SETTLE_AFTER_SUBSCRIBE` | 2.0 s | vendor parity pause before the first request |
| `PUSH_HARVEST_TIMEOUT` | 30 s | bounded zero-TX harvest window |
| `PROBE_MIRROR_TIMEOUT` | 20 s | fresh-mirror wait after a write (all starts ≤ ~16 s observed) |
| `DEFAULT_POLL_INTERVAL` | 30 s | user-tunable 10..600 s |
| `TEST_TIMEOUT` | 70.0 s | whole config-flow test budget (2×20 s connects + reads + probe) |

## 11. Tests

`tests/` carries the per-spec suites (reassembly, census, store/harvest,
coordinator cycles, probes, climate surfaces, config-flow walkthrough,
active-by-default pins — ~18k lines across ~30 files). The protocol
vectors are also pinned in `modbus.py`'s `__main__` self-test
(byte-exact frames, CRC checks, builder parity with `registers.py`
constants). Any protocol change must keep both green.