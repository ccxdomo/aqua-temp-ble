# Quickstart 003 — Validate the sensor-coverage extension

Validation runbook for spec 003. As everywhere in this repo (spec 001
§7): no HA test harness — HA-dependent code is gated by `py_compile` +
review + **manual scenarios**. Hardware scenarios are marked 🔴; the
calibration session requires the unit, the vendor app (for the panel
photos only) and one quiet quarter hour.

Confidence reminder: the new entities **do not exist yet** — they appear
only after calibration pins registers (`contracts/calibration-contract.md`).
Everything below is checkable before that.

---

## 0. Static gates (no hardware)

```bash
python3 -m py_compile custom_components/aqua_temp_ble/registers.py \
                          custom_components/aqua_temp_ble/coordinator.py \
                          custom_components/aqua_temp_ble/sensor.py \
                          custom_components/aqua_temp_ble/binary_sensor.py
python3 custom_components/aqua_temp_ble/modbus.py    # self-test 37 checks
python3 tests/test_registers.py                     # NEW (spec 003)
python3 tests/test_modbus.py                        # 11 tests
python3 tests/test_reassembly.py                    # 15 tests
python3 tests/test_config_flow_list.py              # spec 002
python3 tests/test_config_flow_walkthrough.py       # spec 001/002 regression
```

✓ *pass criteria:* all green — including the **new**
`tests/test_registers.py`: sentinel set (both conventions), plausibility
vectors, polarity matrix, write-guard matrix, registry integrity, and
the shipped-table assertions (new keys present, all new addresses
`None`, existing keys/icons unchanged).

## 1. V1 — Decode honesty (no hardware)

- Unit-test output covers AC-1/AC-2: `decode_temp(-9990)`,
  `decode_temp(32765)` → `None`; `decode_temp(170) → 17.0`;
  `decode_temp(2000) → None`.
- Review check: no code path renders a raw sentinel as a temperature —
  the vendor app's `3276.5 °C` display must be impossible here.

## 2. V2 — No new entities, no regression (no hardware)

On any HA instance with the integration installed (existing entry or a
mock walk):

1. Entity list after restart is **identical** to spec-002 state: 1
   climate, 3 temperature sensors, fault code, firmware, probe button.
   No `suction/coil/exhaust` sensors, no `hp/lp/flow/high fan` binaries —
   they are placeholders until calibrated (AC-3).
2. Existing entities report the same values as before for the same
   snapshot (AC-7).
3. `climate.py`, `modbus.py`, `aqua_client.py`, `config_flow.py` are
   untouched (AC-12 review item).

## 3. V3 🔴 — Pre-calibration live check

Next to the unit, vendor app closed:

1. `python3 tools/smoke_client.py <MAC>` → base-block dump, `CRC=OK`,
   regs 26/27/28 plausible (`/10` matches the pool).
2. `service: aqua_temp_ble.probe_registers` with `block: base` → same
   dump in `home-assistant.log`; response via Developer Tools → Actions
   shows raw/hex/tenths per register (unchanged format, FR-13).
3. Existing sensors match physical reality; no implausible-value
   warnings in the log for good data.

## 4. V4 🔴 — The calibration session (~15 min)

Follow `contracts/calibration-contract.md` end to end:

1. **Photos**: parameter view (password `66`) — T/H/R/P/S/O pages,
   same quarter hour as the probes.
2. **Dumps**: probe `base` + all six high blocks (or smoke_client with
   `--query`).
3. **Pin**: T family (§3 of the contract: 170/195/180 anchors first,
   175 exhaust, second 195 coil, sentinel register suction), then S/O
   with the **two-state method** (idle + heating probe).
4. **Record** H04 (`1050`) and the R cluster for the data model —
   expose nothing (contract §5).
5. Edit only `registers.py`, restart HA.

✓ *pass criteria:*
- Each pinned entity appears with the exact name/unique id of
  `contracts/entity-contract.md` (AC-4).
- Fresh panel photo vs entities: temperatures match within one poll;
  **absent suction shows `unknown`, never 3276.5** (AC-1 field check).
- Any member found only in a high block → entity absent + loud warning
  in the log (AC-5) — recorded in the runbook for phase B.
- No entity exists for any H/R/P member (AC-12).

## 5. V5 🔴 — Polarity & sentinel verification (post-calibration)

1. Start a heating cycle: Compressor / Circulate pump / High fan
   binaries flip ON within one poll; the panel's O page agrees.
2. Stop the pool circulation briefly: Flow switch state changes and
   matches the panel S page text (whichever convention calibration
   pinned — `research.md` §9).
3. If a binary is inverted: flip that RegDef's `active_when_nonzero`
   in `registers.py`, restart — no code change (AC-8).
4. Unplug a water sensor if you ever service the unit: its entity goes
   `unknown` (sentinel), a `WARNING` appears at most once per address.

## 6. V6 — Security review checklist (no hardware, normative)

- `grep`-level review: the **only** write call sites remain the climate
  entity's regs 8/13/14(+19–21); `async_write_register` starts with
  `registers.ensure_writable(...)` (FR-9).
- No service/HTTP/button surface writes any register (FR-10); the probe
  path contains no `write_registers` call (FR-13).
- `ensure_writable(1004)` and any H/R/P address raise (AC-6) — proven by
  the unit tests, re-checked in review.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| New entity absent after pinning + restart | address outside 7…51 (check the startup warning) — needs phase B; or duplicate pin (integrity check raised at import — see log) |
| Entity shows `unknown` though the panel shows a value | sentinel or implausible raw (check the probe dump: is it 32765/−9990 or out-of-window?) — wrong register pinned? re-run the disambiguation |
| `3276.5` visible anywhere | impossible by design — file a bug; it violates AC-1 |
| Temperatures look halved/doubled | scale/endianness — spec-001 runbook steps 2/3 (`REG_DATA_LITTLE_ENDIAN`, anchors 26/27/28) |
| Binary states inverted vs panel | per-RegDef `active_when_nonzero` (V5 step 3) |
| Two entities, one register | import-time integrity check (FR-11) must have raised — re-pin |
| Log floods implausible warnings | a pinned register is garbage — re-check the calibration; the window constants are flip-safe in `registers.py` |