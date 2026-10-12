# Spec 020 — Write contracts: power (1011) and setpoint (1046)

The two proven write surfaces, their frames, value disciplines and
confirmation rules. Everything here is enforced in code at THREE layers
(coordinator funnel → client rail → builder parity with pinned strings);
a violation at any layer fails loudly before bytes move.

---

## 1. The power write — register 1011, values {0, 1}

**Frames (pinned, byte-exact [P disassembly + live]):**

| Command | Frame |
|---|---|
| ON (1011 ← 1) | `631003f30001020001f131` |
| OFF (1011 ← 0) | `631003f3000102000030f1` |

Shape: `63 10 <addr:2> 0001 02 <value:2> <crc:2LE>` (qty=1, bytecount=02,
big-endian value). The builder `modbus.build_write_control(1011, v)` is
asserted byte-equal to both strings by the `modbus.py` self-test and the
spec-016 suite — one source of truth.

**Value discipline:** 1011 accepts ONLY 0 and 1, **forever, even when
proven**. Extending the value set requires a new contract. [P contract 016 §4]

**Send mode:** fire-and-forget Write-Command on the resolved write
characteristic (ff03, without-response — app parity). NEVER through the
classic `write_registers` funnel: its FC10-echo wait would time out on
every write (no echo has ever been observed — law L3). The mirror is the
confirmation, never an echo.

**Confirmation cycle (coordinator.async_control_write_power):**

```
ensure_writable(1011, value)                           # the funnel, FIRST
→ connect if needed → audit INFO line
→ [io lock] write frame → ONE trigger read
→ wait ≤ 20 s for a fresh 2001-block (last_seen > t_write)
→ verdicts:
   mirror 2011 == value  → "confirmed"      → snapshot pushed
   mirror 2011 != value  → "not-confirmed"  → snapshot pushed (mirror truth)
   no fresh block       → "unconfirmed"    → NO snapshot; for value == 0 a
                          persistent notification is raised (a possibly-
                          running pump is never a silent end state)
```

The state mutation is ONLY the snapshot push, built by the same
`from_pushes` the poll cycle uses — never optimistic, the tile reflects
the mirror. The unconfirmed-OFF notification is dismissed by the next
confirmed cycle for the same unit.

**Proof record [P]:** the owner ran the shipped probe; the mirror
followed 0→1→0; the pump physically started and stopped (heard). The
frames are the app's own home-screen power-slide frames.

## 2. The setpoint write — register 1046, raw 100..400 step 5

**Frames (builder-parity [P], address/qty/bytecount shape identical to
the power frame):**

| Value | Frame |
|---|---|
| 30.0 °C (raw 300) | `63100416000102012c5049` |
| 29.5 °C (raw 295) | `631004160001020127118e` |

**Value discipline (PERMANENT):**

- raw ∈ **100..400** (10.0-40.0 °C) — a REGULATION value: a request
  outside the range is REFUSED, never clamped;
- raw a **multiple of 5** (the 0.5 °C granularity — every observed raw
  is a multiple of 5 [P]) — an off-step request is REFUSED, never
  rounded;
- validation is FIRST and LOUD (`ValueError` before any byte moves):
  non-finite values, non-numbers, off-step and out-of-range all raise
  named errors.

**Why 1046:** the write target and the read home COINCIDE — a
parameter register the vendor app itself reads AND writes (the "66"
debug page's plane). The write-family/mode map from the disassembly
adjudicates this heat-only unit to family 2's heat slot = 1046, the
same register the panel diff proved live. The map's other slots are
installer thresholds — never writable.

**Confirmation cycle (coordinator.async_control_write_setpoint):** the
same ladder as power, with the fresh **1046-block** as the oracle
(the mirror must postdate the write), verdicts
confirmed/not-confirmed/unconfirmed, snapshot push on the first two.
NO persistent notification on the unconfirmed path (a failed setpoint
write leaves the machine at its old target — no hazard; the WARNING +
the tile's mirror-truth suffice). The write is allowed regardless of
power state: the register is the machine's stored parameter (the panel
changed the setpoint with the unit off [P owner]).

**Proof record [P]:** the mirror followed S→T→S (300→295→300) in the
probe; the owner then set the setpoint from HA in daily use.

## 3. The probe instruments (writes outside the funnel, by design)

Both probes are **measurement instruments**, never control surfaces;
their frames go through the client's own validated seams, NOT through
the funnel:

- **power_write_probe** (`coordinator.async_power_write_probe`): baseline
  check (mirror must read 0 — never toggles an unverified state) → ON
  frame → mirror must follow to 1 → OFF frame → mirror must revert to 0
  → ONE WARNING verdict line (`power write probe: EXECUTED + REVERSIBLE
  (mirror 2011 followed 0→1→0)`). Writes only the two pinned strings;
  always aims to leave the unit OFF; a failed revert says so loudly.
- **setpoint_write_probe** (`coordinator.async_setpoint_write_probe`):
  four baseline checks (absent/sentinel/out-of-range/off-step → refuse,
  zero writes) → **phase A**: write the register's OWN value (a true
  no-op) and verify the WHOLE 45-register block is unchanged (the
  neighbor alarm catches a mis-aimed write before anything real runs) →
  **phase B**: −0.5 °C dip toward the range interior → mirror must
  follow → revert → mirror must revert → verdict line. Physical effect:
  the pool's heat target dips 0.5 °C for ~2 minutes.

## 4. What is NOT writable, ever

- **The mode (1012/1013).** The vendor's mode write is a PAIR write;
  1013 is unpinned (setpoint-shaped) — writing the mode would clobber
  an unknown register. The app itself locks the mode on this heat-only
  machine. `set_hvac_mode` exposes OFF/HEAT only, both pure power
  writes; anything else raises `HomeAssistantError`.
- **The mute (1076).** Frame recovered, semantics unpinned — needs its
  own proof and its own contract.
- **Installer thresholds** (the setpoint map's other slots; the H/R/P
  families). An erroneous H04/R-limit write can damage the heat pump.
- **Anything not in the allowlists** — see
  [`write-protections-contract.md`](write-protections-contract.md).

## 5. Cross-cutting invariants

1. **The funnel first** (`registers.ensure_writable`) — before any
   connect, lock or I/O; zero bytes move on a refused write.
2. **The mirror decides** — the displayed state is only ever the fresh
   pushed mirror; a write error propagates with no trigger, no
   cross-check, no snapshot.
3. **`async_set_updated_data` is sync** — never awaited (the 017
   production lesson: awaiting it raised `'NoneType' object can't be
   awaited` after every write).
4. **One write per lock** — all writes serialize against reads and each
   other via the coordinator's `asyncio.Lock`.
5. **Client rails under the funnel** — `write_control` accepts only
   (1011, 0|1); `_setpoint_write_frame` enforces int/range/step and
   asserts the built frame's address field is 1046. Defense in depth.