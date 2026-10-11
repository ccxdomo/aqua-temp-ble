# Spec 010 — Tasks (ordered, for the Coder)

Status legend: each block is independently landable; run the tests after
each block. Total estimated effort: one focused session. Touch ONLY the
files listed (AC-6). `specs/001-009` are read-only.

## Block 1 — constants (FR-1)

1. `custom_components/aqua_temp_ble/const.py`:
   - `UUID_WRITE` := `0000ff03-0000-1000-8000-00805f9b34fb`, comment:
     `[P] app write bucket {ff03, 2215, 2aa6} — research 010 §1; the app
     never selects a characteristic ff01`.
   - `UUID_NOTIFY` stays ff02; comment updated to cite the notify
     bucket `{ff02, 2216, 2a00}`.
   - Add:
     ```python
     UUID_NOTIFY_BUCKETS = (UUID_NOTIFY, "00002216-0000-1000-8000-00805f9b34fb", "00002a00-0000-1000-8000-00805f9b34fb")
     UUID_WRITE_BUCKETS = (UUID_WRITE, "00002215-0000-1000-8000-00805f9b34fb", "00002aa6-0000-1000-8000-00805f9b34fb")
     ```
   - Remove `UUID_WRITE_FALLBACK` (ff03 is now first-choice; grep all
     usages — `aqua_client.py` and tests — and update).
   - Do NOT renumber/alter any other constant.

2. `tests/` quick fix pass: replace every reference to
   `UUID_WRITE_FALLBACK`/ff01-as-write from the existing tests with the
   new expectations (the old tests asserted the ff01-first preference —
   those assertions are the misreading being fixed; see Block 3 for the
   new tables).

## Block 2 — resolver + write mode (FR-2, FR-3, FR-4)

3. `aqua_client.py`: add the shared bucket resolver
   (`_resolve_from_buckets(client, buckets, role)` per plan §2) with the
   property-advisory WARNING when the resolved char lacks the role's
   property.
4. `_resolve_notify_characteristic`: bucket tuple first
   (`UUID_NOTIFY_BUCKETS`), then the existing adaptive pool scan,
   then the existing loud error. Keep the existing INFO log line
   formats byte-identical (AC-4).
5. `_resolve_write_characteristic`: bucket tuple first
   (`UUID_WRITE_BUCKETS`); delete the ff01-first branch; keep the
   last-resort adaptive scan unchanged. Same AC-4 constraint.
6. `_set_write_mode_from_properties`: implement the FR-3 rule exactly
   (without-response when advertised OR when properties unknown;
   with-response only when only `write` is advertised; WARNING in both
   advisory cases).
7. `connect()` sequence comment: one line citing research 010 §2
   ("app order: subscribe ff02 → 2 s → write ff03 → listen — proven").
   NO behavioural change to the sequence (FR-4).

## Block 3 — tests (AC-1, AC-2, AC-3)

8. `tests/test_characteristic_resolution.py`: fake-GATT-table fixtures
   and cases T1-T8 from plan §4 (ff03-without-props, gen-2 2214 layout,
   GAP-only fallback, unknown-vendor fallback, empty loud error,
   bucket-collision first-wins).
9. `tests/test_write_mode.py`: the FR-3 truth table (write-wo-response →
   without; write-only → with + WARNING; no props → without + WARNING).
10. Run: `python3 -m pytest tests/ -q` and the standalone-import smoke
    (`python3 -c "import aqua_client"` pattern, spec 005), plus the
    AC-4 log-format assertions included in the tests.

## Block 4 — documentation (FR-5, FR-6, FR-7)

11. `FINDINGS.md`: append the correction block to §11.3 (exact text in
    spec.md FR-7). No other edits.
12. `README.md`: replace the "next steps" paragraph with the E4'/E5
    pointer (quickstart §0 and §2; two sentences + links).
13. `specs/010-write-characteristic/quickstart.md`: already written by
    the Architect — Coder verifies commands against the shipped
    `tools/session_probe.py` flags and fixes flag names only if the
    tool's argparse differs.

## Block 5 — hand-off (FR-5)

14. Post in the completion report the two E4' grep command lines
    (quickstart §0) for the operator to run against the HA/pasted
    production logs, and the E5 capture checklist (quickstart §2) —
    the logs and the phone are the operator's side.

## Verification checklist (all blocks)

- [x] `grep -rn "ff01" custom_components/` returns no write-target usage
      (only the docstring/comment mentions if any).
- [x] `grep -rn "UUID_WRITE_FALLBACK"` returns nothing.
- [x] pytest green; census log-line formats unchanged (AC-4 test).
- [x] No diff in `modbus.py`, `registers.py`, `coordinator.py`,
      `config_flow.py` (AC-6).
- [x] FINDINGS correction block present; README pointer present.