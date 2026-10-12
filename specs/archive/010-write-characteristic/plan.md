# Spec 010 — Implementation plan (app-parity resolution + strategy pivot)

Status: **PLANNED**. Depends on: research 010 (disassembly proof),
spec 009 (probe instrumentation, already shipped in `tools/session_probe.py`
and `aqua_client.py`). Everything below is implementable from the
current repo state; no new evidence is required to write the code —
the remaining evidence (E4'/E5) gates only the *next* spec's direction.

## 0. The one-paragraph verdict that shapes this plan

The disassembly (research §1-2) proved the app writes to ff03 on this
unit — the same characteristic our client already resolves at runtime —
always without response, with the same frame bytes, after the same
subscribe → 2 s sequence, with no handshake and no MTU dance. So this
plan contains **no magic retarget**: it (a) makes our *resolution logic*
match the app's UUID-bucket rule so the right char is picked by design
instead of by fallback, (b) erases the wrong constants inherited from
the DEX misreading, and (c) wires the two decisive experiments into the
runbook. The blocker itself now lives behind E4'/E5, not behind our
code.

## 1. Change list (files)

| File | Change |
|---|---|
| `custom_components/aqua_temp_ble/const.py` | FR-1: `UUID_WRITE` := ff03; add `UUID_NOTIFY_BUCKETS`/`UUID_WRITE_BUCKETS`; drop `UUID_WRITE_FALLBACK`; comment corrections |
| `custom_components/aqua_temp_ble/aqua_client.py` | FR-2/FR-3/FR-4: shared bucket-based resolver for notify+write; property-advisory logging; write-mode rule; sequence comment |
| `tests/test_characteristic_resolution.py` (new) | AC-2/AC-3 resolution tests with fake GATT tables |
| `tests/test_write_mode.py` (new) | AC-2 write-mode rule tests (property variants) |
| `FINDINGS.md` | FR-7: correction block appended to §11.3 |
| `specs/010-write-characteristic/quickstart.md` | FR-5/FR-6 runbook rewrite |
| `README.md` | "Next steps" pointer to E4'/E5 (one paragraph) |

## 2. The resolver, in code shape

One helper, both directions (sketch — the Coder owns naming/style):

```python
def _resolve_from_buckets(
    self, client: BleakClient, buckets: Sequence[str], role: str
) -> GattCharInfo | None:
    """App-parity resolution (research 010 §1): UUID bucket first,
    property-blind; advertised properties are advisory only."""
    for uuid in buckets:                       # FR-2 item 1
        char = self._lookup_char(client, uuid)
        if char is not None:
            self._log_property_advisory(role, char)   # FR-2 item 2
            return char
    return None                                # → last-resort scan
```

`_resolve_notify_characteristic`:

```python
notify = self._resolve_from_buckets(client, const.UUID_NOTIFY_BUCKETS, "notify")
if notify is not None:
    self._notify_uuid = notify.uuid
elif <current adaptive pool scan>:             # FR-2 item 3, unchanged
    ...
else:
    raise ... (current loud error, unchanged)
```

`_resolve_write_characteristic` mirrors it with
`UUID_WRITE_BUCKETS`, then `_set_write_mode_from_properties` applies
the FR-3 rule:

```python
def _set_write_mode_from_properties(self, uuid: str, props: list[str]) -> None:
    # App parity: WriteType.withoutResponse always (research 010 §2.3).
    if "write-without-response" in props:
        self._write_with_response = False
    elif "write" in props:
        # ATT constraint: a 'write'-only char cannot take Write Commands.
        _LOGGER.warning("write characteristic %s forbids write-without-response "
                        "(properties=%s); using write-with-response", uuid, props)
        self._write_with_response = True
    else:
        _LOGGER.warning("write characteristic %s advertises no write property "
                        "(properties=%s) — app parity: writing anyway (without-response)",
                        uuid, props)
        self._write_with_response = False
```

Keep the existing INFO line formats byte-identical (`using write
characteristic <uuid> (properties=[…])` and the last-resort variant) —
spec-009 grep recipes depend on them (AC-4). The ff01-first branch of
the old `_resolve_write_characteristic` is deleted: a characteristic
ff01 is not a write target in the app (research §1.3) — if some *other*
layout exposes one, the last-resort adaptive scan still covers it
(FR-2 item 3).

## 3. Contracts

`contracts/resolve-characteristics.md` pins the resolver's truth table
(the same fake tables as the tests, so contract ⇄ tests ⇄ code stay in
lock-step).

## 4. Test matrix (AC-2/AC-3)

Fake GATT tables, resolver called directly (no I/O):

| # | Table | Expected notify | Expected write | Write mode | Advisories |
|---|---|---|---|---|---|
| T1 | ff01 svc {ff02 (notify), ff03 (write-wo-response, write)} | ff02 | ff03 | without | none |
| T2 | ff01 svc {ff02, ff03 (no write props at all)} | ff02 | ff03 | without | WARNING (write-away-anyway) |
| T3 | ff01 svc {ff02, ff03 (write only)} | ff02 | ff03 | **with** | WARNING (forbids w/o response) |
| T4 | 2214 svc {2216, 2215} (gen-2 layout) | 2216 | 2215 | per props | — |
| T5 | 1800 svc {2a00, 2aa6} only (GAP fallback) | 2a00 | 2aa6 | per props | — |
| T6 | unknown svc {charX (write-wo-response)} | none → adaptive | charX | without | INFO fallback line |
| T7 | 1801 svc only | loud error (unchanged) | loud error | — | — |
| T8 | ff01 svc {ff02, ff03, 2215-elsewhere} (bucket collision) | ff02 | ff03 (first bucket UUID wins) | per props | — |

T5 documents the app's GAP fallback literally (research §1.3: the app
classifies 2a00/2aa6 into the buckets too; last-wins overwrite in the
app, first-wins in ours because we scan the bucket tuple in order —
both select the vendor chars whenever the vendor service exists, which
is the only case that matters).

## 5. Runbook deltas (quickstart.md)

- **Step 0 (E4')**: the two greps on existing production logs; decision
  table property-result → strategy (FR-5).
- **Step 1**: unchanged probe usage (spec 009) with updated expectation
  notes (Phase Q/W now test the *proven* target char).
- **Step 2**: E5 capture protocol with the expected ATT lines (FR-6):
  CCCD `01 00` on ff02; Write Command `63 03 00 07 00 2D 3C 54` on ff03.
- **Step 3**: decision matrix — same as research 009 §11 with H1/H2/H3
  rows struck out and the C3 emphasis updated.

## 6. Order of implementation

1. `const.py` (FR-1) — 15 min, unblocks everything.
2. Resolver + write-mode in `aqua_client.py` (FR-2/FR-3) — the core.
3. Tests T1-T8 (AC-1/2/3) — run `python3 -m pytest` + the standalone
   import check (`python3 -c "import aqua_client"` pattern from spec
   005).
4. Log-format regression check (AC-4): grep the new INFO lines in the
   tests' captured logs.
5. FINDINGS.md correction block (FR-7) + README pointer.
6. quickstart.md rewrite (FR-5/FR-6).
7. Hand the E4' grep to the operator (the logs are in the chat
   history/HA instance, not in the repo) — the spec ships with the
   command lines ready.

## 7. Risks / non-risks

- **Non-risk**: "ff03 without write property + without-response write" —
  if the property is absent, BlueZ may refuse the ATT Write Command
  where Android's stack might not; the WARNING surfaces it, and this
  case is exactly what E4' resolves from the existing log. The app
  being property-blind means the vendor's own units carry the property
  or the app would fail too.
- **Risk (small)**: some other BlueNRG firmware exposes extra chars —
  covered by the unchanged last-resort scan (T6).
- **Risk (none)**: write-with-response fallback (T3) is more capable
  than the app, not less — but only reachable when without-response is
  ATT-illegal.