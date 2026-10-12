# Spec 019 research — the setpoint: the write address recovered from the disassembly; the read home proven live

Date: 2026-10-12
Evidence base: **the owner's panel diff of this morning** (the setpoint
changed at the PANEL by +1.0 °C; the first register of the pushed
1046-block moved 290 → 300 — relayed verbatim in the spec brief, the
direct diff-toggle proof); **a fresh full disassembly session** on the
surviving artifacts (`/tmp/flutterapp/lib/arm64-v8a/libapp.so`,
`/tmp/blutter_out/` — both re-verified present this session;
`/tmp/disasm.py` capstone 5.0.7 in `/tmp/blutter-venv`); the exhaustive
raw-immediate word scan of the whole exec segment (this session's
`/tmp/scan_*` scripts, kept for the record); FINDINGS.md; specs
008/010/013/014/015/016/017/018 (every inherited claim stays tagged
there); the shipped code @ 0.13.2 (`registers.py`, `coordinator.py`,
`climate.py`, `aqua_client.py`, `button.py`, `const.py`, `modbus.py`).

Status legend (the repo convention): **[P]** PROVEN (disassembly with
addresses / arithmetic / a cited spec / the owner's recorded
observation), **[P owner]** the owner's observation, **[P panel]** the
parameter-view photographs, **[P apk]** recovered from the APK,
**[V]** TO-VERIFY (one named experiment decides), **[A]** ASSUMED
(reasonable, cheap to correct), **[D]** DECISION.

Reproduce any region below:

```bash
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x<start> 0x<end> flags
```

---

## 0. TL;DR — the verdicts, black on white

> **The setpoint is READ at register 1046 — proven live by the owner's
> panel diff this morning [P owner].** The vendor app WRITES the
> setpoint to ONE register with value `temp × 10` — but the target
> register is NOT hardcoded: it is resolved at runtime from a
> per-family, per-mode address map recovered this session [P]:
>
> | family key | mode 0 (cool) | mode 1 (heat) | mode 2 (auto) |
> |---|---|---|---|
> | 1 | 1135 | 1136 | 1137 |
> | 2 | **1051** | **1046** | **1068** |
> | 3 | 1073 | 1074 | 1075 |
>
> The owner's unit (heat-only, mode locked to heat) resolves to the
> **heat** slot — and family 2's heat slot is **1046, the exact
> register the panel diff proved is the live setpoint** [A-strong on
> the family key; P for the map]. The brief's alternative hypothesis
> (register 1013, the mode pair's second word) is **disfavored by the
> static evidence**: an exhaustive scan of the whole `.text` finds
> NO instruction anywhere loading 1013 as an immediate — 1013 exists
> on the wire ONLY as the second word of the mode pair write, which
> is mode-locked on this unit and was excluded by contract 017 §6.
>
> The frames (CRC16-Modbus, computed through the same pipeline that
> reproduces the proven power frames byte-exactly):
>
> | Action | Frame (hex, 11 bytes) |
> |---|---|
> | write 30.0 °C to 1046 | `63100416000102012c5049` |
> | write 29.5 °C to 1046 | `631004160001020127118e` |
> | write 31.0 °C to 1046 | `631004160001020136d182` |
>
> The proof protocol is TWO-PHASE and non-destructive first: phase A
> writes the register's CURRENT value (a no-op — nothing can change),
> phase B is a −0.5 °C diff-revert with the pushed 1046-block as the
> mirror. The setpoint is a REGULATION register — more sensitive
> than power — so the guard is a THIRD, separate gate
> (`WRITE_SETPOINT_PROVEN`) with a bounded value allowlist
> (10.0–40.0 °C), and the climate exposes `TARGET_TEMPERATURE` only
> behind it, reading the target back from the MIRROR (register 1046),
> never from the request.

---

## 1. The read home — register 1046 [P owner]

The owner changed the setpoint at the PANEL by +1.0 °C this morning.
The first register of the pushed 1046-block (start 1046, the
machine-settings ladder) moved **290 → 300** (29.0 → 30.0 °C).
This is a direct diff-toggle with no ambiguity:

- the ONLY change was the panel's setpoint;
- the register followed it exactly, ×10 scaled;
- the 2001-block (power 2011 / mode 2019) did NOT move — the
  setpoint is NOT in the 2001-block [P owner, the brief's own
  observation].

Consequences:

1. **1046 is the heat setpoint's live home on this unit** [P owner].
   The unit is heat-only (H06 [P panel]; the vendor app locks mode
   changes [P apk, research 017 §5]) — the panel's single setpoint IS
   the heat setpoint.
2. The register sits in the PUSHED plane (the 1046-block is one of
   the four blocks the unit bursts [P — spec 013/014]) — so the
   integration can read the setpoint TODAY, and any future write can
   be confirmed by a fresh pushed block (the perfect mirror —
   contract 016 §3's oracle discipline, satisfied natively).
3. The unit accepts setpoint changes while OFF/idle — the morning's
   diff was made with the unit off [P owner]. A setpoint write does
   not require the machine to be running.

### 1.1 The corrected ladder labels

Spec 015 §2.2 guessed the ladder's setpoint candidates from the
control-parameters page's label ORDER ("Cooling set" first → 1046?).
The morning's diff kills that alignment: **the page order is NOT the
register order**. The corrected mapping (this spec's reading):

| reg | value (captures) | 015's guess | corrected label | status |
|---|---|---|---|---|
| **1046** | 290 → 300 [P owner] | "Cooling set?" | **the heat setpoint (the live one)** | **[P owner]** |
| 1051 | 270 | "Heating set / Auto. Set?" | the cool setpoint (an unused default on this unit) | [A-strong — the map, §4] |
| 1068 | 270 | "Auto. set?" | the auto setpoint (an unused default) | [A-strong — the map, §4] |
| 1049 | 150 | "Heating set?" | a ladder member (min-bound or diff family) | [V] |
| 1050 | 400 | "Max. cool / Max. heat" | a max-bound candidate (40.0) | [A-weak] |
| 1054 | 100 | "Min. cool / Min. heat" | a min-bound candidate (10.0) | [A-weak] |

The staticness of the block across ≥ 2 days [P — 015] is consistent:
the owner never changed the setpoint during those captures, and the
unused per-mode defaults (270/270) sat untouched.

---

## 2. THE WRITE HANDLER — fully decoded [P]

The home-screen setpoint handler — spec 016 §1.2's call site 11,
until now the one "runtime address [V]" row — is closure
**0x6a1f54** → inner **0x6a1fe8** of the `[frk] _dR` class (the
home-screen BLE device page). The complete control flow,
disassembled this session:

```
0x6a1f60-0x6a1f6c:  S = context.field_0x17 (the captured page state)
0x6a1f7c-0x6a1fac:  GUARD — a dynamic call on S.field_0xf.field_0x27
                     (a "can set temperature" check, dynamically
                     dispatched; the i18n twins: "目标温度不能高于
                     最高温度！/不能低于最低温度！" — the app's own
                     min/max clamp + model lock, research 015 §5.4).
                     result bit 4 set → RETURN, no write [P]
0x6a1fb4-0x6a1fcc:  A = S.field_0xf; V = S.field_0x13;
                     d0 = V.field_0 (the NEW target, a double)
0x6a1fe8:           the inner:
0x6a2008-0x6a2074:  CS = A.field_0x17 (the device model)
                     mode = CS.field_0xf (raw int)
                     CS setpoint fields by mode: mode 0 → 0x23,
                     mode 1 → 0x2b, mode 2 → 0x33 (the per-mode
                     doubles — the mode handler's own fields,
                     research 017 §5) ← the optimistic local update
0x6a2078-0x6a20b4:  box the new target (tag 0x3E19C) →
                     CS.field_0x10f (the "current setpoint" box —
                     the field the mode handler also refreshes at
                     0x6a24b8 [P])
0x6a20d0-0x6a20ec:  value = (int)(d0 × 10.0), Smi-tagged — the
                     temp×10 wire value [P]
0x6a20f0-0x6a20f4:  session = A.field_0x13 (the bS BLE session)
0x6a2104-0x6a2108:  x3 = CS.field_0x11b  ← THE WRITE ADDRESS
0x6a210c-0x6a2124:  untag x3 (Smi via sbfx / Mint via [x3+7]) → x2
0x6a2128-0x6a2134:  qty = 1; bytecount = [pp+0x20b68] = "02"
0x6a2138:           bl 0x64fc0c — the universal frame builder
0x6a2158:           bl 0xa14ea8 — the universal sender (ff03,
                     withoutResponse — spec 010 §2, unchanged)
```

The frame shape is the proven single-register control shape
(research 016 §1.1): `6310 <addr:4> 0001 02 <val:4> <crc:4>` —
11 bytes, `value = temp × 10` [P].

**The decisive structural fact: the address is loaded from
`CS.field_0x11b` — a RUNTIME value — not a `mov` immediate.** The
power handler hardcodes `mov x2, #0x3f3` (1011) at 0x6a1740 [P,
research 016 §2]; the mode handler hardcodes `mov x2, #0x3f4`
(1012) at 0x6a2550 [P, research 017 §5]; the mute handler hardcodes
`mov x2, #0x434` (1076) at 0x6a1484 [P, research 016 §1.2]. The
setpoint handler has NO such immediate [P — this session's
disassembly].

The raise/lower buttons (closures 0x6a1d74 and 0x6a2258, the pool
constants [pp+0x21128] / [pp+0x21140] selecting the ± direction)
funnel through the shared apply inner 0x6a1e34, which updates the
pending target state — the actual wire write always goes through
the 0x6a1f54/0x6a1fe8 path [P]. ONE write surface, one register.

---

## 3. No 1013 anywhere — the exhaustive immediate scan [P]

A word-level scan of the WHOLE exec segment (0x2f0000-0xa70ff0,
every 4-byte-aligned `movz`-class word decoded, this session)
for raw immediates 1001–1200 returns:

- **`movz #1011` — exactly ONE site: 0x6a1740** (the power handler).
- **`movz #1012` — exactly ONE site: 0x6a2550** (the mode handler).
- **`movz #1076` — TWO sites: 0x6a1484 (the mute handler [P,
  research 016]) and 0x5e0700** (the per-parameter closure farm —
  §4.2 below).
- **`movz #1013` — ZERO sites.** No instruction in the entire
  binary loads 1013 as an immediate [P].

Interpretation: the app NEVER hardcodes a write to 1013. The only
1013 on the wire is the SECOND WORD of the mode pair write
(`6310 03F4 0002 04 <mode> <setpoint×10> <crc>` — the pair
[1012, 1013] [P, research 017 §5]), which:

1. is sent ONLY by the mode handler — and the vendor's own app
   REFUSES mode changes on this heat-only unit (GUARD 2,
   `CS.field_0x6b == 1` required, research 017 §5 [P]) — so the
   vendor itself never sends 1013 here;
2. was excluded from OUR write surface by contract 017 §6
   (an unpinned pair register).

**The brief's "setpoint writes 1013" hypothesis is therefore
UNSUPPORTED by the static evidence.** The truth is data-driven —
§4.

(The scan also finds, for the record: `movz #1018-1023` at
0x5e2330-0x5e4ca4, `#1046-1051` at 0x5e2f04-0x5e3e58, `#1055`,
`#1063`, `#1068-1069`, `#1073-1083`, `#1101-1113`… — the
per-parameter closure farm of §4.2, plus 13-copy locale-table
families at 0xa874 spacing for 1003+4i-style sequences —
research 018 §5.2's pattern; none of them is a home-screen write.)

---

## 4. THE ADDRESS RESOLUTION — the per-family, per-mode map [P]

### 4.1 The binder

The function that computes `CS.field_0x11b` is the closure at
**0x5dc3c4** (found by scanning the whole `.text` for stores to
field offset 0x11b and cross-matching the co-located 0x10f/0x11b
stores; the setpoint handler's LOAD is 0x6a2108, its unique
consumer in the page region). Disassembled in full this session:

```
0x5dc3c4-0x5dc3dc:  entry (arg1 = the model, arg2 = a raw int key)
0x5dc3ec-0x5dc448:  build obj1: Smi 1 → field 0xf,
                     list [1135, 1136, 1137] → field 0x13
                     (tagged Smis 0x8de/0x8e0/0x8e2)
0x5dc450-0x5dc4a0:  Smi 2 → field 0x17,
                     list [1051, 1046, 1068] → field 0x1b
                     (tagged Smis 0x836/0x82c/0x858)
0x5dc4a4-0x5dc4f4:  Smi 3 → field 0x1f,
                     list [1073, 1074, 1075] → field 0x23
                     (tagged Smis 0x862/0x864/0x866)
0x5dc4f8-0x5dc504:  the pool object [pp+0x1cff8] =
                     TypeArguments <int, List<int>> →
                     bl 0x3193c8 — a Map<int, List<int>>
                     construction from the pairs [P apk]
0x5dc508-0x5dc530:  key = Smi(arg2); dynamic dispatch
                     (selector 0x8d4) → the list for that key
0x5dc53c-0x5dc570:  mode = box(CS.field_0xf); dynamic dispatch
                     (selector 0xa7e) on the list → the element
0x5dc574-0x5dc57c:  CS.field_0x11b = the element  ← THE ADDRESS
0x5dc59c-0x5dc7c0:  the SAME function then refreshes the current
                     setpoint box CS.field_0x10f from the
                     per-mode fields (mode 0 → 0x23, mode 1 →
                     0x2b, mode 2 → 0x33) — the same mode
                     dispatch as the write handler [P; the field
                     layout cross-check that identifies this
                     binder as the model's]
```

So the address formula is:

```
CS.field_0x11b = Map{
    1: [1135, 1136, 1137],
    2: [1051, 1046, 1068],
    3: [1073, 1074, 1075],
}[arg2][mode]        [P — every constant read in the disassembly]
```

where `mode` is the app's mode enum (0/1/2 = cool/heat/auto [A —
research 017 §5's per-mode field order, unchanged]) and `arg2` is
the family key.

### 4.2 The parameter-closure farm (the address space's context)

The raw-immediate scan's 0x5de-0x5e5 region is a farm of
per-parameter accessor closures — one function chunk per register
address (1073 at 0x5e02bc, 1074 at 0x5e0428, 1075 at 0x5e05a0,
**1076 (the proven mute register) at 0x5e0700**, 1077…1083, then
1018-1023, 1032-1040, 1046-1051, 1053-1069…). This is the app's
parameter-object system — FINDINGS §12.1's `param->field_1f`
address lists made visible. The setpoint map's addresses are
members of this parameter space: **the setpoint is written to a
PARAMETER register, not a command register** [P — the structure].

### 4.3 The caller and the family key [V]

The binder has ONE caller: `bl 0x5dc3c4` at **0x5dc008**, inside a
dispatcher that switches on a device-type field (x2.field_7 ==
198 → a different family's binder 0x5dd770; == 16 → OUR binder;
else → 0x5dc158) [P]. At the call: arg1 = `parent.field_0x3f`
(the model), arg2 = `parent.field_0x47` (a raw int on the page
state) [P].

The exact semantics of arg2 — which value the OWNER'S unit feeds —
is runtime data that static analysis cannot fully resolve [V].
The candidates (mode type / device subtype) and the guard evidence
are in §5. **The live probe (quickstart §1) decides it.**

---

## 5. Why 1046 — the adjudication [A-strong]

The owner's unit resolves to ONE of the three lists. The evidence:

1. **The read home [P owner]**: 1046 is the live heat setpoint on
   this unit (the panel diff). Family 2's heat slot IS 1046. If
   the app's write targeted a different register than the one the
   machine actually regulates, the vendor's own setpoint UI would
   be broken on this family — not credible for a shipped product.
   The read and write homes COHERE for family 2.
2. **The guard semantics**: the mode-lock guard requires
   `CS.field_0x6b == 1` (research 017 §5). The natural reading of
   the [P apk] option list ("Chiller"/"Heat pump"/"Heat only") is
   0-based with "Heat pump" (the full-range product) == 1 — the
   only type where mode changes make sense; the owner's heat-only
   unit is type 2 → the mode lock fires exactly as observed
   [P owner 015]. **Mode type 2 → map key 2 → [1051, 1046, 1068]**
   → heat (mode 1) → **1046**.
3. **The pushed-plane cross-check**: family 3's heat slot (1074)
   reads ZERO in every pushed 1046-block capture [P — 015 §2.2's
   decode: 1069-1090 all zero] while the actual setpoint is ~29-30
   (290→300). If the owner's family were 3, its live heat
   setpoint register would read 0 — absurd. Family 1's slots
   (1135-1137) sit in the NEVER-PUSHED 1136-block window
   (1136-1180 is a query block the unit never pushes on this
   firmware [P — spec 014]) — an app whose read plane never sees
   its own write target would be equally broken.
4. **The unused defaults cohere**: family 2's cool (1051) and auto
   (1068) slots read 270/270 — plausible per-mode defaults for a
   heat-only unit whose owner never touches cool/auto [A-strong].

Verdict: **the setpoint write address on this unit is 1046**
[A-strong — the map [P], the family key [V] decided by the live
probe]. The alternative readings (key 1 or 3) are possible but
each dies on evidence 1/3 above; the probe's diff-revert is the
named decider — and it is NON-DESTRUCTIVE FIRST (quickstart §0-1).

**A bonus correction this yields**: the parameter plane is
read/write (the "66" debug page reads AND writes these registers —
FINDINGS §12.1) — which is why the write target can equal the
pushed read home. The power/mode/mute command registers (1011/
1012-1013/1076) are a different, command sub-plane of the same
numbered space; no constant-offset rule exists between the
sub-planes (research 016 §3's caveat, re-confirmed).

---

## 6. THE FRAMES — CRC-computed [P arithmetic]

The builder grammar (research 016 §1.1, the app's own pipeline):
`"6310" + addr4 + "0001" + "02" + val4 + crc4`, CRC16-Modbus
little-endian on the wire. 1046 = 0x0416. Sanity: the same
pipeline reproduces the proven power frames byte-exactly
(`631003f30001020001f131` / `…0030f1` — verified again this
session).

| Action | Body | CRC | Full frame (11 B) |
|---|---|---|---|
| 30.0 °C → 1046 | `63100416000102012c` | 0x4950 | **`63100416000102012c5049`** |
| 29.5 °C → 1046 | `631004160001020127` | 0x8E11 | **`631004160001020127118e`** |
| 31.0 °C → 1046 | `631004160001020136` | 0x82D1 | **`631004160001020136d182`** |
| ~~31.0 °C (errata)~~ | ~~`631004160001020132`~~ | ~~0x41D0~~ | **ERRATA**: the original line encoded `0x0132` = 306 = 30.6 °C, an arithmetic slip (306 is not a multiple of 5, outside the vendor's 0.5 °C value space). The proven `temp x 10` formula gives 310 = `0x0136`. The code pins `0x0136`; this table carries the correction. |
| 28.0 °C → 1046 | `631004160001020118` | 0x9E51 | `631004160001020118519e` |
| 10.0 °C → 1046 (the min) | `631004160001020064` | 0xEF51 | `63100416000102006451ef` |
| 40.0 °C → 1046 (the max) | `631004160001020190` | 0xF851 | `63100416000102019051f8` |
| (record only) 30.0 °C → 1013 | `631003f5000102012c` | 0xDA30 | `631003f5000102012c30da` |

The 1013 row is recorded for completeness (the brief's hypothesis);
it is NOT the primary target and must NOT be sent without its own
proof.

Reproduction (any Python):

```python
def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
# body = bytes.fromhex("63100416000102012c"); wire = body + crc16(body).to_bytes(2,"little")
```

---

## 7. THE RANGE — what may ever be written [D on the evidence]

The setpoint is a REGULATION register. An absurd value (0 °C,
60 °C) could drive continuous running or inhibit startup. The
evidence for the bounds:

| Source | Statement | Status |
|---|---|---|
| The pushed ladder | 1050 = 400 (40.0) and 1054 = 100 (10.0) — the max/min bound candidates | [A-weak — 015 §2.2] |
| The panel R-family | 11 members, 0.5–40.0 °C observed | [P panel] |
| The app's own clamp | "目标温度不能高于最高温度！/不能低于最低温度！" — the app refuses targets outside the per-mode min/max it decoded from the device | [P apk — 015 §5.4; the guard closure §2] |
| The shipped fallbacks | FALLBACK_MIN/MAX_TARGET 5.0/40.0 | [A — registers.py] |
| The observed values | the live setpoint 290→300; all ladder setpoint-shaped raws in 150–400; every observed raw a multiple of 5 (0.5 °C granularity) | [P decode] |

**Decision [D]: the write guard accepts ONLY raw values 100–400
(10.0–40.0 °C), multiples of 5 (the 0.5 °C step).** Rationale: the
top is the strongest number in every source (40.0); the bottom
10.0 is the machine's own min-bound candidate and the lowest
sensible pool heat target (below it, anti-freeze-style continuous
running becomes the use case — NOT this unit's job, and the
spec's write surface is the pool setpoint); the 0.5 granularity
matches the vendor's whole observed value space. The unit's REAL
per-mode bounds are [A-weak] candidates in the ladder — this spec
does NOT display them; the climate shows the static 10.0/40.0 and
the guard enforces the same pair (one source of truth). A future
spec may pin the ladder bound registers and narrow the range.

---

## 8. The brief's eight questions — verdicts

| # | Question | Verdict |
|---|---|---|
| 1 | The write address | NOT hardcoded. `map[family][mode]` with the three decoded lists [P]; the owner's family = 2 → heat → **1046** [A-strong]; 1013 disfavored (zero immediates in .text [P]); the live probe decides (quickstart §1) |
| 2 | The exact frame | `6310 0416 0001 02 <temp×10:4> <crc>` — 11 bytes; 30.0 °C = **`63100416000102012c5049`**; the full CRC table in §6 [P arithmetic] |
| 3 | The safe range | 10.0–40.0 °C, 0.5 steps — the guard's permanent value allowlist (raw 100–400, multiples of 5) [D on §7's evidence] |
| 4 | Is 1046 always the setpoint? The block's contents | On this heat-only unit YES — the mode is locked to heat, so the heat slot is THE setpoint [P owner + the mode lock]. The map says the register is per-mode: cool = 1051, heat = 1046, auto = 1068 [A-strong] — the 1046-block holds the machine-settings ladder (the "66" page's dataset: per-mode setpoints, differentials, bounds, defrost/EEV — 015 §2.2's decode with the corrected labels of §1.1) |
| 5 | The proof probe | TWO phases: A — write the CURRENT value (a true no-op; verifies harmlessness + the whole block unchanged), B — the −0.5 °C diff-revert with the fresh pushed 1046-block as the mirror (verifies execution). The mirror is native (the register is pushed). Non-destructive first by construction [D — quickstart §1] |
| 6 | The security guard | A THIRD gate (`WRITE_SETPOINT_PROVEN` + the `write_setpoint` option); address allowlist {1046}; value allowlist 100–400 ×5; the funnel rule (before any lock/IO); the client rail; never optimistic (the mirror decides); the neighbor-diff alarm in the probe; no persistent notification (unlike unconfirmed OFF — a failed setpoint write leaves the machine at its old target, no hazard; the WARNING + the tile's mirror-truth suffice) [D] |
| 7 | The climate | `TARGET_TEMPERATURE` behind the setpoint gate; `target_temperature` reads the pushed 1046 (the mirror) behind the existing `push_control_confirmed` gate (the home is [P owner]); min 10.0 / max 40.0 / step 0.5; writes allowed regardless of power state [P owner — the panel changed it while off]; hvac_mode/action unchanged (018's power-wins) [D] |
| 8 | The gate | YES — a third, separate gate (not an extension of `WRITE_CONTROL_PROVEN`): (a) contract 016 §4's non-transference — every register needs its own proof; (b) a different risk class — a regulation VALUE vs a boolean; (c) independent activation — power control without setpoint control must remain possible; (d) a different value discipline — a bounded range vs {0,1} [D] |

---

## 9. The hypothesis ledger (spec 019, S-series)

| # | Statement | Status |
|---|---|---|
| S-1 | Register 1046 is the live heat setpoint's read home on this unit | **[P owner — the panel diff, 2026-10-12 morning]** |
| S-2 | The vendor app's setpoint write is ONE register, qty 1, "02", value temp×10, address from `CS.field_0x11b` | **[P — §2's disassembly]** |
| S-3 | `CS.field_0x11b = {1:[1135,1136,1137], 2:[1051,1046,1068], 3:[1073,1074,1075]}[family][mode]` | **[P — §4.1's disassembly]** |
| S-4 | No write to 1013 is hardcoded anywhere in the app | **[P — §3's exhaustive scan]** |
| S-5 | The owner's family key = 2; the setpoint write address = 1046 | **[A-strong — §5's adjudication; decided by the probe]** |
| S-6 | The mode enum 0/1/2 = cool/heat/auto (the map's list order) | [A — inherited; 017 §5] |
| S-7 | The safe write range is 10.0–40.0 °C in 0.5 steps | [D — §7] |
| S-8 | The unit executes our setpoint frame (the H-EXECUTES world, extended to 1046) | [V — the probe's phase B decides; the power write's execution (research 017 §1) is strong prior evidence: same transport, same builder, same sender] |
| S-9 | The 1046-block is writable-and-pushed (parameter plane: read home == write home) | [A-strong — §5; the probe's phase A/B decide] |
| S-10 | The ladder bound registers are 1050 (max 40.0) / 1054 (min 10.0) | [A-weak — 015's candidates; out of this spec's write path] |

---

## 10. What remains unproven — and the named decider

| # | Gap | Decider |
|---|---|---|
| U-1 | Whether the owner's family key is really 2 (S-5) | **The probe** (quickstart §1): the diff-revert's mirror follows or it does not. A `not-executed` verdict with the 1046 frame → re-run once (radio), then the fallback hypothesis is family 3's heat slot 1074 — a NEW probe frame would need its own spec addendum (the map gives us the candidate list — §4.1) |
| U-2 | Whether the firmware executes OUR frame to 1046 (S-8) | The probe's phase B (the `EXECUTED + REVERSIBLE` verdict) |
| U-3 | Whether a no-op write has any side effect (a re-broadcast, a flash write) | The probe's phase A (the whole-block diff) |
| U-4 | The exact semantics of the family key (arg2 / parent.field_0x47) | Research-only: the logcat oracle (FINDINGS §12.2) or a targeted disassembly pass; NOT needed for the control |
| U-5 | The real per-mode bound registers (S-10) | A bounds-diff at the panel (the owner changes the min/max in the installer menu while we capture — a future spec) |
| U-6 | The cool/auto write addresses' live behavior (1051/1068) | Out of scope — this unit is heat-only; a future multi-mode unit needs its own spec (the map documents the candidates) |
| U-7 | Whether the app's setpoint write works on this unit at all (the owner has only changed it at the panel) | Implicit in U-2: our probe writes the same register the app's map targets; if the probe executes, the app's own write surface is de-facto validated too |

---

## 11. Reproduction

```bash
# the write handler (this spec's §2):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a1f54 0x6a2180 flags
# the mode handler (017 §5, re-verified — the 0x2b/0x33 order):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x6a2318 0x6a2600 flags
# the address binder + the map (§4.1):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x5dc3c4 0x5dc5f8 flags
# the binder's caller / the family dispatch (§4.3):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x5dbf90 0x5dc060 flags
# the per-parameter closure farm (§4.2 — the 1076 site):
/tmp/blutter-venv/bin/python /tmp/disasm.py 0x5e06e0 0x5e0760 flags
# the exhaustive immediate scan (§3) — this session's script:
/tmp/blutter-venv/bin/python - <<'EOF'
import struct
data = open("/tmp/flutterapp/lib/arm64-v8a/libapp.so","rb").read()
seg = data[0x2f0000:0xa70ff0]
for off in range(0, len(seg)-4, 4):
    w = struct.unpack_from("<I", seg, off)[0]
    if (w >> 21) == 0x694:
        imm = (w >> 5) & 0xFFFF
        if imm in (1011, 1012, 1013, 1076):
            print(hex(0x2f0000+off), imm)
EOF
# the CRC table (§6): any Python with the crc16 of §6
```

Pool citations: `blutter/pp.txt` lines 23152 (`[pp+0x1cff8]
TypeArguments: <int, List<int>>` — the map's type), 19381
(`[pp+0x16d60]` — an unrelated closure-family field, the negative
control that kept this session honest), 25806 (`[pp+0x212d0]`
`"63030007002D3C54"` — the base read).

The owner's morning observation (1046: 290 → 300 after the panel's
+1.0 °C) is the spec brief's relayed record; the probe
(quickstart §1) is its machine-readable successor.