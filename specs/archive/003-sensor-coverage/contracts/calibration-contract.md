# Contract — Calibration of the family registers (003)

Status: **PLANNED** — the normative procedure that turns every `[V]`
index in `data-model.md` into `[P live]`, and the only sanctioned way to
make the spec-003 entities appear. Extends the spec-001 runbook
(`docs/CALIBRATION.md`) with the **panel worksheet**; the Coder folds
this contract's worksheet + steps into the runbook (AC-10).

Hard rules that apply to every step:

1. **Read-only.** The probe service/button and `tools/smoke_client.py`
   never write. The only writes in the whole integration are regs
   8/13/14 (+19–21 behind the default-off mirror switch), guarded by
   `ensure_writable` (spec §6). **H/R/P are never written — a bad
   H04/R-limit write can damage the heat pump or prevent startup.**
2. **One calibration surface**: every correction lands in
   `registers.py` only + restart HA. No other file may hard-code an
   address.
3. **No unconfirmed data under a label**: an entity appears only after
   its address is pinned **and** lies inside the polled base block
   (7…51).

---

## 1. Prerequisites

- Vendor app **closed** (single BLE connection); unit in range; HA
  bluetooth working (spec 001 quickstart §1).
- The **panel worksheet**: with the vendor app open (on the phone, while
  HA is *not* connected), enter the parameter view with the debug
  password `"66"` [P apk — FINDINGS.md §10] and photograph every family
  page (T/H/R/P/S/O — and **D**, still missing, `data-model.md` §9).
  Record the **time** of the photos: the unit state must match during
  the probe.
- This contract's worksheet table (§4) filled with the photographed
  values.

## 2. Step A — probe the blocks

Within a few minutes of the photos (temperatures drift):

```yaml
service: aqua_temp_ble.probe_registers
data:
  block: base      # then 1001, 1046, 1091, 1136, 2001, 2046
```

(or `python3 tools/smoke_client.py <MAC> [--query <block>]` standalone).
Collect: base dump + all six high-block dumps + `home-assistant.log`
lines.

## 3. Step B — pin the T family (temperature anchors)

1. **Confirm the scale + existing identities**: base regs 26/27/28 must
   read `170 / 195 / 180` (`0x00AA/0x00C3/0x00B4`) if the panel showed
   T02=17.0, T03=19.5, T05=18.0 at probe time. If yes: T02≡26, T03≡27,
   T05≡28 confirmed [A→P live]. If byte-swapped (`0xAA00` style): flip
   `REG_DATA_LITTLE_ENDIAN` first (spec-001 runbook step 2).
2. **Find T06 Exhaust**: look for raw `175` (`0x00AF`). Candidates may
   exist in base 29–46 and in the status high blocks — prefer the base
   block (pollable today, FR-7).
3. **Find T04 Coil**: raw `195` — ambiguous with T03 (reg 27, already
   known). The remaining `195` (base or status block) is the coil.
   Disambiguate if needed: repeat the probe after a state change
   (unit heating → coil/outlet diverge; see §4 procedure note).
4. **Find T01 Suction**: look for the sentinel — raw `32765`
   (`0x7FFD`) or `−9990` (`0xD8FA`). Record **which convention** this
   register uses (feeds `data-model.md` §11). Expect **two** candidate
   registers at most; the one that is NOT another known label's address
   is the suction.
5. Pin: `REG_SUCTION_TEMP`, `REG_COIL_TEMP`, `REG_EXHAUST_TEMP`
   (new constants next to the existing temperature registers).

## 4. Step C — pin the S and O families (binary anchors)

The unit was idle at photo time (O01–O04 = OFF, S01/S02 = OFF, S03 =
ON). Binary registers read 0/1 [A]:

1. **Idle-state probe** (already collected): O candidates = registers
   reading `0`; S01/S02 candidates = `0`; S03 candidates = `1`.
   With ~25 unmapped base registers reading `0`, one dump is **not**
   enough — go to step 2.
2. **Two-state method (required)**: probe once while **idle**, once
   while **heating** (compressor + pump + fan running). The registers
   that flip `0 → non-zero` when the compressor starts map to the O
   family; correlate the fan (starts first/with compressor) vs pump vs
   4-way valve (changes only on a mode change, if ever). A flow-switch
   candidate flips when pool circulation stops/starts.
3. **Polarity**: after pinning, set each RegDef's
   `active_when_nonzero` (True = nonzero means ON/O04-style "running";
   False = inverted) so the entity matches the **panel** OFF/ON text
   in both observed states. S03 Flow reading ON at idle is expected —
   see `research.md` §9; keep the panel semantics, not a guess.
4. Pin: `REG_HP_SWITCH`, `REG_LP_SWITCH`, `REG_FLOW_SWITCH`,
   `REG_HIGH_FAN` + polarity per entry (existing
   `REG_COMPRESSOR`/`REG_CIRCULATE_PUMP`/`REG_FOUR_WAY_VALVE` slots
   for the confirmed O members).

**Do not skip the second state.** A single idle dump pins nothing
reliably in the S/O families.

## 5. Step D — record (do NOT expose) H / R / P

- Use the dumps to note where H04 (raw `1050`, distinctive) and the
  R-range cluster (raws 5–400) sit — this is **mapping knowledge for
  the data model only**. It informs a future phase-B decision
  (`research.md` §10) and the runbook worksheet.
- **No entity is created for H/R/P in any case** (spec §6). The
  calibration session may *record* the addresses in the runbook notes;
  `registers.py` gains **no** write path for them (`ensure_writable`
  stays the guard).

## 6. Step E — restart & verify

1. Restart HA. New entities appear for every member pinned inside
   7…51. Members whose registers sit in a high block stay absent —
   the warning in the log names them and the phase-B note applies.
2. Cross-check each new entity against a **fresh panel photo**: values
   within one poll interval, sentinels shown as `unknown` (never
   3276.5), S/O states matching the panel text.
3. Record in the runbook: per member — final address, sentinel
   convention observed, polarity, the two probe dumps used.

## 7. Worksheet (shipped in docs/CALIBRATION.md, from data-model.md §13)

Idle-state predicted raws (displayed ×10 [A]):

| Member | Displayed | Predicted raw (hex) |
|---|---|---|
| T01 Suction | 3276.5 (absent) | `32765 / 0x7FFD` **or** `−9990 / 0xD8FA` — the observed one is this register's convention |
| T02 Inlet | 17.0 | `170 / 0x00AA` (expect reg 26) |
| T03 Outlet | 19.5 | `195 / 0x00C3` (expect reg 27) |
| T04 Coil | 19.5 | `195 / 0x00C3` (the other one) |
| T05 Ambient | 18.0 | `180 / 0x00B4` (expect reg 28) |
| T06 Exhaust | 17.5 | `175 / 0x00AF` |
| H03 Comp. stop | 0.0 | `0` |
| H04 Exhaust high | 105.0 | `1050 / 0x041A` (mapping note only) |
| S01 / S02 / S03 | OFF / OFF / ON | `0` / `0` / `1` [A] |
| O01–O04 | all OFF | `0` [A] |
| P02 / P03 / P04 | 0 / 3 / 0 | `0` / `3` / `0` [A] |
| R01–R11 | 0.5–40.0 range | `5–400` (mapping note only) |

Temperatures drift — re-photograph the T page at probe time if the
session is not back-to-back; a 0.1–0.2 °C drift is fine (raws move by
1–2), a different *pattern* is not.

## 8. Failure modes

| Symptom | Meaning / action |
|---|---|
| No register matches a predicted raw | encoding hypothesis wrong (e.g. S-family not 0/1) — inspect the dump, do not pin; extend the worksheet with the observed encoding |
| A predicted raw matches many registers | use the two-state method (§4.2); never pin on a single coincidence |
| Member found only in a high block | leave unpinned; it needs phase B (high-block polling) — note the block in the runbook |
| Two labels point at one register | import-time integrity check raises at startup (FR-11) — re-run the disambiguation |
| Values look byte-swapped | `REG_DATA_LITTLE_ENDIAN` (spec-001 runbook step 2) — re-dump, do not hand-correct |