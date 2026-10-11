# Spec 010 — The true write characteristic: app-parity resolution and the strategy pivot

Status: **PLANNED** — built from `research.md` (the disassembly proof
of the vendor app's own GATT usage). Scope guard: this spec does **not**
change the transport decode (008), the frame allowlist (009 §8), the
register map (001/003), the config flow (002/004), device resolution
(005), session parity (006) or connect instrumentation (007). It ships
exactly two things:

1. **the app-parity characteristic resolution** — the write/notify
   choice by UUID bucket, property-blind, exactly as the app does it
   (research §1-2) — replacing the ff01-first preference and the
   property-gated candidate scan that were built on the misread DEX
   constants;
2. **the strategy pivot** — with H1/H2/H3 dead, the blocker moves to
   E4'/E5 evidence; this spec wires the two decisive checks into the
   runbook and the probe expectations.

No new request frame enters the integration (allowlist unchanged). No
spec 001-009 content is modified.

## 0. What changed in our understanding

The brief asked: *which characteristic does the app really write to?*
The answer, proven in the app's own code (research §0-§2):

- the app classifies characteristics into a **notify bucket**
  `{ff02, 2216, 2a00}` and a **write bucket** `{ff03, 2215, 2aa6}` **by
  UUID** (state fields `0x2b`/`0x2f`, discovery classifier `0xa146dc`);
- on the owner's unit that resolves to **notify = ff02, write = ff03** —
  the same pair our client already uses at runtime;
- the write is always `WriteType.withoutResponse`; the frame is raw
  Modbus bytes from a hex string; the sequence is subscribe → 2 s →
  write → listen; **no handshake, no MTU request, no second write path**
  (research §5 table).

So the production silence is NOT a wrong-character symptom. The fix
below hardens our resolution logic to app-parity (it currently reaches
ff03 only through fallbacks and could mis-pick on other GATT layouts),
corrects the wrong documentation constants, and points the project at
the two experiments that can actually break the silence.

## 1. Functional requirements

### FR-1 — Corrected UUID constants

`const.py` must encode the proven buckets, not the DEX misreading:

- `UUID_NOTIFY = 0000ff02-0000-1000-8000-00805f9b34fb` — unchanged value,
  comment updated: `[P] app notify bucket {ff02, 2216, 2a00}, disassembly
  research 010 §1`.
- `UUID_WRITE = 0000ff03-0000-1000-8000-00805f9b34fb` — **was ff01**.
  Comment: `[P] app write bucket {ff03, 2215, 2aa6}; the app never
  selects a characteristic ff01 (research 010 §1.3)`.
- Add ordered fallback tuples (app-bucket order, research §1.3):

```python
UUID_NOTIFY_BUCKETS = (UUID_NOTIFY, "00002216-…", "00002a00-…")
UUID_WRITE_BUCKETS  = (UUID_WRITE,  "00002215-…", "00002aa6-…")
```

- `UUID_WRITE_FALLBACK` (ff03) is removed — ff03 is now the first-choice
  write UUID, and the old constant name would mislead.

### FR-2 — App-parity characteristic resolution (property-blind buckets)

`aqua_client._resolve_notify_characteristic` and
`_resolve_write_characteristic` are rewritten to one shared rule:

1. **UUID buckets first, property-blind**: pick the first UUID of the
   bucket tuple that the device exposes as a characteristic. The app
   does not read advertised properties before choosing (research §1);
   neither do we.
2. **Advisory property logging**: when the resolved char advertises no
   write property (write/write-without-response absent), log at WARNING
   `write characteristic <uuid> advertises no write property
   (properties=[…]) — app parity: writing anyway`. Never refuse, never
   re-pick on property grounds.
3. **Last-resort adaptive scan** (unchanged safety net for unknown
   GATT layouts): if **no** bucket UUID exists on the device, fall back
   to the current vendor-service property-gated candidate scan, then
   the `GattCharInfo` listing. The bucket rule must win whenever any
   bucket UUID is present.
4. The chosen pair and the full GATT table are logged at INFO exactly as
   today (the census and the spec-009 grep expectations must keep
   working).

### FR-3 — Write type pinned to without-response

The write mode is **no longer property-gated**:

- if the resolved char advertises `write-without-response` →
  without-response (current behaviour);
- if it advertises only `write` → **still without-response is NOT
  possible**; in that case log at WARNING and use with-response
  (a char that advertises only `write` forbids Write Commands at the
  ATT layer — this is a GATT constraint, not a preference; the app on
  its units has never faced this case because bucket chars carry the
  property, and if ours does not, the WARNING of FR-2 item 2 already
  flags the anomaly);
- the default for a property-less (last-resort) char stays
  without-response (current behaviour).

Rationale: research §2.3 — the app writes `withoutResponse` always;
our current code silently switches to with-response when the property
set says so, which is a divergence from the app on exactly the units
where the property set is unusual.

### FR-4 — Session order unchanged (documented)

No change to the connect sequence; the proven app order
(subscribe ff02 → 2 s settle → first write) is already ours (spec 006,
`SETTLE_AFTER_SUBSCRIBE = 2.0`). FR-4 only pins the comment in
`aqua_client.connect` to research 010 §2 so nobody "fixes" the order
later.

### FR-5 — E4' runbook item: the two-grep verification of the existing log

`quickstart.md` step 0 becomes normative: from the already-pasted
production logs (ce41aa5 census run and any later run), grep:

```
GATT enumeration
using write characteristic
last-resort
does not advertise a write property
```

The first two decide **ff03's advertised properties on this unit**
(research §6.3): if ff03 advertised a write property, the app could
write it at the ATT layer (C3 loses its cheapest evidence); if it
advertised none, the vendor app cannot control this unit either and
push-only (Track B) becomes the leading strategy. The result is written
into the spec-010 follow-up notes in README §next-steps.

### FR-6 — E5 remains THE decisive experiment (expectations updated)

The HCI-snoop protocol of spec 009 quickstart §3 is restated with the
now-known expectations, so the capture can be read without re-deriving
anything:

- expected subscription: CCCD write `01 00` on ff02;
- expected first command: **Write Command (ATT 0x52) on ff03**,
  payload `63 03 00 07 00 2D 3C 54`, ≈2 s after the CCCD write;
- if the app gets an FC03 answer → diff its ATT trace against ours
  (timing vs push stream, connection parameters) → spec 011;
- if the app shows the same total silence → C3 (push-only firmware) is
  proven → Track B (push harvest) becomes primary (spec 011).

### FR-7 — FINDINGS.md correction note

`FINDINGS.md` §3 and §11.3 are not rewritten by this spec (they are
history), but a correction block must be appended to §11.3 (the repo's
living findings file):

> Correction (spec 010, 2026-10-11): the Dart operating channel selects
> write = ff03 / notify = ff02 by UUID bucket
> ({ff03,2215,2aa6} / {ff02,2216,2a00}); the `ff01` DEX constant belongs
> to the non-operating Java BLE path. The `ff01→2214/ff02→2216/ff03→2215`
> "handle" pairs are two GATT generations' UUID sets. See
> specs/010-write-characteristic/research.md.

## 2. Acceptance criteria

- **AC-1**: `python3 -m pytest` — all existing tests pass; new tests
  (AC-2/AC-3) added under `tests/`.
- **AC-2** (resolution): unit tests with a fake GATT table containing
  service ff01 + chars ff02/ff03 resolve notify=ff02, write=ff03 —
  **with ff03 stripped of every write property** the resolution still
  returns ff03 and the write mode is without-response when
  `write-without-response` is advertised, with-response only when only
  `write` is advertised (FR-3), and the WARNING of FR-2 fires.
- **AC-3** (bucket order): a fake table exposing `2214`-service chars
  `2216`/`2215` resolves notify=2216, write=2215; a table exposing only
  bucket-less vendor chars falls back to the adaptive scan (FR-2 item 3);
  a table with no vendor service resolves nothing and fails with the
  current loud error.
- **AC-4** (no regression in the census): the INFO log lines
  `GATT enumeration` and `using write characteristic … (properties=…)`
  keep their exact format — the spec-009 grep recipes still match.
- **AC-5** (docs): `const.py` comments cite research 010; the
  FINDINGS.md correction block exists (FR-7); quickstart.md steps 0-3
  rewritten per FR-5/FR-6.
- **AC-6** (no scope creep): `modbus.py`, `registers.py`, the frame
  allowlist and the block-request logic of 009 are untouched; the diff
  touches `const.py`, `aqua_client.py` (resolution + write-mode only),
  `tests/`, `specs/010…/` and FINDINGS.md's correction block.

## 3. Non-goals

- No new request frames, no cadence changes (research §6.2: no
  periodic re-send exists in the app's session code; the bounded
  re-send of 009 stays as shipped).
- No Track B implementation — its promotion is an E5 outcome, spec 011.
- No DEX smali session — dead end unless E5 contradicts the Dart
  disassembly (research §6.4).