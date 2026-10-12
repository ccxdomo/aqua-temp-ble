# Calibration & verification runbook — the push world

This runbook explains how to verify (and, where needed, correct) the
register mapping **live on a unit**. The four owner-verified gates it
used to open were removed in spec 022 — everything is active by
default; the verifications below remain as optional confidence checks
for a new or unknown unit. The authoritative, current reference is
[`specs/020-integration-reference/`](../specs/020-integration-reference/)
— start with its `quickstart.md` §4 (the calibration + verification
sequence performed and recorded on the production unit on 2026-10-12)
and its `data-model.md` (the complete register → quantity map).

Status legend: **[P]** proven (live on the production unit, or
byte-exact from the vendor APK disassembly), **[A]** assumed default,
**[V]** to-verify on other units.

## 0. The world you are calibrating for

This firmware (the production unit, BlueNRG module) **never answers the
classic Modbus base read** with register data (law L1, spec 020) and
**never pushes spontaneously** — it *answers every request* with a burst
of four 45-register blocks (1001/1046/2001/2046; law L2). The integration
reads that world (trigger + listen) and the calibration runbook below is
written for it.

Prerequisites:

- **No pairing is needed** — the write pipe opens at the ATT layer with
  no bond (the `att-accepted-silent` verdict [P]). The old "put the unit
  in pairing mode (bLE)" advice is wrong for this unit: that gesture is
  the WiFi-provisioning mode on some models, at best a last resort to
  make the unit advertise.
- **Close the vendor app completely** — the unit accepts a single BLE
  connection. HA will politely wait and show unavailable otherwise.
- Enable file logging if needed:
  `logger.set_level` → `custom_components.aqua_temp_ble: debug`
  (`home-assistant.log` gets the probe output).

## 1. The probe surfaces

- **Button** `Probe registers` (diagnostic entity) — triggers the unit's
  data report and logs **every captured register** with labels (on
  push-mode entries: one trigger read, the burst lands, the whole store
  is dumped — the classic base read this unit never serves was rerouted
  in the spec-021 cleanup).
- **Service** `aqua_temp_ble.probe_registers` — same trigger + dump with
  `block: base`; a numbered block queries one block of the answer burst
  directly (these answer: 1001/1046/2001/2046); optionally returns the
  values as a service response:

```yaml
service: aqua_temp_ble.probe_registers
data:
  block: base        # base | 1001 | 1046 | 2001 | 2046
```

Log format (one line per register — RAW is the signed int16, hex is the
unsigned 16-bit view):

```
AquaTemp probe: XX:XX:XX:XX:XX:XX block=2001 (pushed capture, start=2001 qty=45 len=99 count=2) CRC=OK
  reg 2001 =      0 (0x0000) | /10 =     0.0
  reg 2011 =      1 (0x0001) | /10 =     0.1  # Power (pushed control) [P owner diff 2026-10-12]
  ...
```

Standalone (no HA): `python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX`
(read-only trigger + store dump, requires only `pip install bleak`).

## 2. Verifying a new unit (optional)

Every step below was performed and recorded on the production unit
(2026-10-12). On a new unit, they are optional confidence checks — no
gates to open, no options to flip (spec 022: everything is active by
default); each step's expected outcome is stated with it:

### 2.1 Temperatures (photo comparison) [P done]

1. Open the vendor app's parameter view (see *Extra aid* below) and
   photograph the T-family page; note the time.
2. Kill the app, run one connection test (or one poll), then press
   **Probe registers** — the T-block dump (start 2046) prints
   `2046=… 2047=… … 2051=…`.
3. Compare with the photo: values must match (a 0.1-0.2 °C drift between
   the photo and the read is fine; a different *pattern* is not).
4. Match → the mapping is confirmed; the sensors already show live
   values (active by default). Mismatch → correct the ONE line in
   `registers.PUSHED_TEMPS` (a permuted T-window is a one-line edit) and
   re-verify.

### 2.2 Power/mode display (app diff) [P done]

1. With the app open, toggle the app's power slide once, then back.
2. Capture before/after (probe or the `block push captured` log lines)
   and DIFF: register **2011** must flip 0↔1 with the slide; **2019**
   moves with the mode flow (0/4 observed). Reversibility is the pinning
   rule — a one-way change pins nothing.
3. Match the app's display → the mapping is confirmed; the climate tile
   already follows (`hvac_mode` follows power alone — OFF/HEAT,
   heat-only unit, spec 018; the target shows the setpoint mirror).
   Live alternative: the `push_stream` diagnostic's `control_candidates`
   attribute exposes the candidate raws with their evidence tags.

### 2.3 Power control (the write probe) [P done]

Unit OFF, vendor app closed → press **Power write probe** (or call
`aqua_temp_ble.power_write_probe`). The probe writes the vendor's own
ON frame, verifies mirror 2011 follows, writes OFF, verifies the revert,
and logs ONE verdict line at WARNING. Expected:

```
power write probe: EXECUTED + REVERSIBLE (mirror 2011 followed 0→1→0) — the write is proven (spec 022: the control is active by default) — write char …
```

That verdict is your evidence record — the climate's Heat/Off buttons
already start and stop the pump (active by default, spec 022). Any other
verdict: read the verdict line — it always names the state and the
remediation. The probe only writes the two disassembly-pinned frames
and always aims to leave the unit OFF.

### 2.4 Setpoint control (the two-phase probe) [P done]

Press **Setpoint write probe** (or call
`aqua_temp_ble.setpoint_write_probe`). Phase A writes the register's
OWN current value (a true no-op) and verifies the whole 1046-block is
unchanged (the neighbor alarm); phase B dips the heat setpoint −0.5 °C,
verifies mirror 1046 follows, reverts, verifies again. Physical effect:
the pool's heat target dips half a degree for ~2 minutes. Expected:

```
setpoint write probe: EXECUTED + REVERSIBLE (mirror 1046 followed <S>→<T>→<S>) — the write is proven (spec 022: the control is active by default) — write char …
```

That verdict is your evidence record — the target slider already sets
the heat setpoint (10.0-40.0 °C, 0.5 steps; requests outside the range
or off the step are refused loudly, never rounded).

### 2.5 The setpoint home (why 1046)

The panel-diff calibration (2026-10-12): changing the setpoint at the
panel moved the first register of the pushed 1046-block (290 → 300 for
+1.0 °C) — register **1046** is the heat setpoint [P]. The write target
and the read home coincide (a parameter register the vendor app itself
reads and writes). The ladder candidates around it (1049/1050/1051/
1054/1068) are bounds/other-mode homes and installer thresholds —
**never writable**.

## 3. What is NOT available (by design)

- **The mode** is not pilotable: this unit is heat-only, the vendor app
  itself locks the mode, and the vendor's mode write is a pair write
  (1012+1013) whose 1013 value is unpinned — writing it would clobber
  an unknown register. The climate exposes OFF/HEAT only (both pure
  power writes).
- **Fault code & firmware**: no home was ever identified in the pushed
  blocks — no entity is created (never guessed).
- **Binary sensors** (compressor/pump/fan/valve/switches): their base
  registers were never pinned and no pushed home exists — the surface
  was pruned in the spec-021 cleanup.

## 4. Classic-plane runbook (other-firmware units ONLY)

Kept for the dormant classic plane (spec 020 §5: hypothetical units that
would answer the classic FC03 base read 7-51; unreachable on this
machine). **On the production unit every step below is moot — the base
block never answers.**

- **Endianness**: compare a known temperature (app shows inlet 24.3 °C):
  `reg 26 = 243 (0x00F3)` → big-endian confirmed (default). If you see
  `0xF300` instead, set `REG_DATA_LITTLE_ENDIAN = True` in
  `registers.py` (one constant; the parse flips).
- **Mode map** (reg 13, [A] default `{0: cool, 1: heat, 2: auto}`):
  switch the app's mode Cool → Heat → Auto, probing each time; correct
  `registers.MODE_VALUE_TO_NAME` if the order differs.
- **Write frames**: capture the app's own writes with
  `adb logcat | grep -aE "address|6310|指令"` on a paired phone — the
  app logs every outgoing frame in cleartext. This pins the write
  layout (the 16-bit H1 shape ships as the safe default) and the real
  addresses. If no FC10 echo ever arrives on ff02, set
  `WRITE_ECHO_EXPECTED = False` (writes become fire-and-forget).
- **Firmware & fault** (regs 47-51): note reg 47 with a fault active vs
  none (0 = OK assumed [A]); probe the hex of 48-51 and adjust the
  join format if needed.

## 5. The seven families (T/H/R/P/S/O/D)

The vendor app's parameter view (debug password `66`, see below) shows
the complete parameter table in seven families. The panel observation
(proven 2026-10) fixes the labels and displayed values — **never the
numeric register indices**.

| Family | Meaning | HA exposure |
|---|---|---|
| T (T01–T06) | live temperatures | temperature sensors (pushed mirrors 2046-2051) |
| H (H01–H06, H70–H72) | machine settings | **none — never writable from HA** |
| R (R01–R11) | regulation thresholds (installer) | **none — never writable from HA** |
| P (P01–P04) | filtration program | **none — never writable from HA** |
| S (S01–S06) | safety inputs | **none — never pinned; surface pruned (spec 021)** |
| O (O01–O04) | outputs | **none — never pinned; surface pruned (spec 021)** |
| D (D01–D06) | digital states | **none** — page not photographed, no speculation |

> 🔒 **Read-only policy for H/R/P (spec 003 §6).** H04 Exhaust Temp. High
> (105.0 °C) is a **safety shutdown threshold**; R01–R11 are installer
> regulation limits; P01–P04 drive the filtration program. They are
> **never writable from Home Assistant**: no entity, no service, and the
> single write funnel (`registers.ensure_writable`) refuses anything
> outside the proven allowlists (power 1011 with 0/1; setpoint 1046 with
> 100-400 step 5; the classic {8, 13, 14} plane stays dormant). Record
> their addresses in your notes if a probe reveals them — never write
> them.

### 5.1 Panel worksheet (comparison anchors)

With the vendor app open in the parameter view, photograph every family
page and note the time; the unit state must match during the probe. The
pushed T-family raws (displayed ×10): T02 Inlet `170/0x00AA`-style
values, the absent T01 → `32765/0x7FFD` (or `−9990/0xD8FA`), H04 →
`1050/0x041A`, etc. Compare the probe dump against the photos — the
match confirms the mapping (§2.1). Temperatures drift —
re-photograph the T page at probe time if the session is not
back-to-back.

## Extra aid: debug password "66"

In the vendor app, entering **`66`** in the settings/parameter view
switches the UI from *status parameters* to the **control parameters**
view (`查看控制参数` vs `查看状态参数`), exposing raw values including
`H70/H71/H72`. This gives direct read access to raw values from inside
the official app, without any instrumentation — handy to correlate with
probe logs.

## Appendix: verified frames (regression vectors)

All of these are asserted byte-exact in `tests/test_modbus.py` and
`modbus.py`'s built-in self-test
(`python3 custom_components/aqua_temp_ble/modbus.py`).

| Purpose | Frame (hex) |
|---|---|
| Base read 7..51 — the poll TRIGGER (the unit answers it with the block burst, never with base data) | `63030007002D3C54` |
| Query block 1001-1045 | `631003E9002DD9E6` |
| Query block 1046-1090 | `63100416002DE8A2` |
| Query block 2001-2045 | `631007D1002D591B` |
| Query block 2046-2090 | `631007FE002D68D2` |
| Power ON (write 1011 = 1) | `631003F30001020001F131` |
| Power OFF (write 1011 = 0) | `631003F3000102000030F1` |
| Setpoint 30.0 °C (write 1046 = 300) | `63100416000102012C5049` |
| Setpoint 29.5 °C (write 1046 = 295) | `631004160001020127118E` |

The classic-plane write vectors (reg 8/13/14, recovered from the APK and
pinned by the modbus self-test) remain in `tests/test_modbus.py` as
regression vectors for the dormant classic plane — they are unreachable
on this unit (the base block never answers, and the control plane writes
1011/1046 only).