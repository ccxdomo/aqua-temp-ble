# Spec 023 — Expose the fault code (register 2074, proven by the owner's E03 run)

Date: 2026-10-12
Repo state: main @ 84494aa, manifest 0.16.0 (the spec-022 build — the
WARNING capture hex, the E03 protocol, the active-by-default world)
Status: **APPROVED FOR IMPLEMENTATION** — the evidence bar of
[`../022-defaults-and-faults/contracts/fault-evidence-contract.md`](../022-defaults-and-faults/contracts/fault-evidence-contract.md)
§1 is SATISFIED by the owner's 2026-10-12 E03 run (research.md Part 1:
the diff-revert `0 -> 256 -> 0` on register **2074**, the panel
displaying **E03**, the burst flowing throughout). This spec activates
the contract's §2 exposure shape and is bound by its §3 code-string
rule and §4 non-goals.

The one-paragraph story: the owner provoked the unit's flow fault
(E03, the un-plumbed dry run), captured the healthy and faulted
blocks with the 0.22 build's restored WARNING hex, and the diff is
unambiguous — **one register appears with the fault: 2074 goes
0 -> 256 (0x0100, bit 8)** and reverts to 0 on clear. Everything else
that moved is explained (2011 = the power consequence, 2047/2049/
2050/2051 = thermal drift). The E03 frame is CRC-verified byte-exact
(research §1.2). What is proven: **2074 is the fault word; nonzero =
fault present; raw 256 was displayed as E03 on the panel.** What is
NOT proven: which protection bit 8 names (the vendor's ten-flag list
order would make bit 8 the *outlet water temp sensor fault*, not the
flow switch — research §2) and the vendor's full code table (the APK
assets are still not on this host; the app names E03 "water flow
problem" [P owner, app display 2026-10-12], but the rest of the table
is unread). The entity therefore
exposes the binary truth (ON/OFF) plus the raw arithmetic as
attributes, and maps exactly the ONE proven code point (256 -> "E03");
every other non-zero raw honestly reads `unknown code (raw N)`.

---

## 1. Functional requirements

### FR-1 — One Fault binary sensor, created automatically

A push-mode entry gets exactly ONE new entity: a `binary_sensor`
named **Fault** (`device_class: problem`), created unconditionally
at setup alongside the six temperature sensors and the climate — no
option, no checkbox, no confirmation act of any kind (read-only
exposure; the 022 active-by-default world; the fault plane has
nothing to confirm — the evidence bar was satisfied before the code
exists, contract 022 §4). Classic-mode entries create NO binary
entity (register 47 is unreachable — law L1, spec 020 §5).

### FR-2 — The decode rule (the pinned register, the honest states)

The entity reads pushed register **2074** (the fault word, pinned by
the owner's diff-revert — research §1) from the coordinator snapshot
(`data.registers`, the fresh 2046-block capture):

- `2074 == 0` → **OFF** (no fault);
- `2074 != 0` → **ON** (fault present);
- register absent (no usable snapshot) → **unknown** — never a
  guessed OFF;
- stale/silent stream → the cycle fails honestly
  (`UpdateFailed`) and the entity goes **unavailable** with every
  other entity (the freshness gate — contract 013 §6.1, unchanged).

No optimistic fault clearing: the state only ever follows a fresh
pushed block, exactly like the climate and the sensors.

### FR-3 — The diagnostic attributes

The entity carries `extra_state_attributes` (evidence, never the
state): `register` (2074), `raw` (the raw value), `hex` (0x0100
style), `set_bits` (the indices of the set bits — pure arithmetic,
NO names: `[8]` for 256), and `code` — the panel-code string when a
PROVEN mapping exists for the raw (see FR-4), absent otherwise. The
`set_bits` list exists so the NEXT fault observation records its bit
pattern with zero tooling (the opportunistic map-building protocol,
quickstart §4).

### FR-4 — The code-string rule (one proven point, honestly labeled)

`registers.PUSHED_FAULT_CODE_MAP` is the single source of truth and
contains exactly the recorded observations:

```python
PUSHED_FAULT_CODE_MAP: dict[int, str] = {256: "E03"}
```

- raw 256 → `code: "E03"` — the one live-proven correspondence (the
  panel displayed E03 while 2074 read 256, 2026-10-12);
- any other non-zero raw → `code: "unknown code (raw N)"` (contract
  022 §3: no guessed tables in entity code);
- raw 0 / absent → no `code` attribute.

Extending the map requires ONE recorded observation per code: the
panel's displayed code + the 2074 raw (with its diff-revert context)
handed to the repo record (a session note or a follow-up spec), then
a one-row edit. **Bit-to-flag-name mapping is FORBIDDEN in entity
code** (the ten-flag list order is an [A] hypothesis — research §2);
bit names live in research notes until a live diff proves them.

### FR-5 — The registers.py additions (single source of truth)

- `REG_PUSHED_FAULT = 2074` with the [P] evidence comment block;
- the pure decode helpers `decode_fault_flag(raw) -> bool | None`,
  `fault_code(raw) -> str | None`, `fault_set_bits(raw) -> list[int]`
  (stdlib-only, pure, mirroring the existing decode helpers);
- `KNOWN_LABELS[2074]` = the fault-word label with its [P] tag (the
  probe dump annotates it);
- `PUSH_CONTROL_CANDIDATES[2074]` = the fault-word tag (the `Push
  stream` `control_candidates` attributes and the probe output
  consume it automatically — the diff instrument keeps working, the
  2011/2019/1046 precedent of pinned homes that stay observable).
- `PUSHED_CONTROL` and `PUSHED_TEMPS` are NOT touched (the fault is
  neither a control mirror nor a temperature); the registry integrity
  rules need no change (2074 collides with nothing).

### FR-6 — The binary_sensor platform returns, without the dead placeholders

- `const.PLATFORMS` becomes `["climate", "sensor", "button",
  "binary_sensor"]`; the comment block above it is rewritten (the 021
  prune removed registry placeholders with no pinned register; spec
  023 returns the platform for the ONE diff-proven binary — no other
  binary entity exists).
- `custom_components/aqua_temp_ble/binary_sensor.py` is created: the
  platform file with the single entity class (plan §2.3). There is NO
  binary registry, NO `RegDef` loop, NO placeholder entries — the
  entity list is one literal class instance, so the 021 dead-weight
  pattern cannot return.

### FR-7 — The texts tell the new truth (README; the JSONs need nothing)

- README's features table gains the Fault row (proven — owner E03
  diff-revert; 1 binary sensor);
- the "Not available — by design" paragraph drops the fault-code
  clause (firmware stays — still no home; the OTHER binary surfaces
  stay pruned);
- the troubleshooting row "Fault code / firmware not displayed" is
  rewritten: the fault IS displayed now (with the code when
  proven); firmware is not (no home identified).
- `strings.json` / `translations/en.json` / `translations/fr.json`
  carry NO fault sentence today (the 022 rewrite moved the honesty to
  the docs — verified by grep) and need NO change; the three files
  keep their exact key parity untouched.

### FR-8 — No portal, no gate, no option (the read-only guarantee)

Nothing in this spec adds or re-adds any confirmation surface: no
option key, no config-flow step, no checkbox, no service, no global
switch. The fault exposure is READ-ONLY decoding of a register the
unit already pushes every cycle. The write plane is untouched:
`ensure_writable`'s allowlists (1011, 1046) and value rails stay
byte-identical; 2074 is a read home and is refused by the funnel
forever (spec 003 §6 — it is not in any allowlist).

### FR-9 — The diagnostics continuity

The `Push stream` diagnostic's `control_candidates` attributes gain
2074 (via FR-5), and the probe store dump labels it — so the owner
can re-run a raw diff at any time without new tooling (the 022 FR-8
instrument pattern, extended to the pinned fault word).

---

## 2. Acceptance criteria

- **AC-1** A push-mode entry creates exactly ONE binary_sensor entity:
  name "Fault", `device_class: problem`, unique_id
  `<ADDRESS>_fault`, attached to the entry's device; a classic-mode
  entry creates none. [test]
- **AC-2** Decode: canned raw 0 → `off`; 256 → `on`; 4 → `on`;
  absent register → state `unknown`. The entity is unavailable while
  the coordinator cycle fails (the existing freshness-gate behavior).
  [test]
- **AC-3** Attributes: raw 256 → `code: "E03"`, `set_bits: [8]`,
  `raw: 256`, `hex: "0x0100"`; raw 4 → `code: "unknown code (raw 4)"`,
  `set_bits: [2]`; raw 0 → no `code` attribute, `set_bits: []`.
  [test]
- **AC-4** The owner's evidence is pinned by fixture: feeding the
  byte-exact E03 frame (99 bytes, CRC 551d — research §1.2) through
  the store → the entity is ON with `code: "E03"`; the healthy twin
  (the same frame with 2074 = 0) → OFF. [test]
- **AC-5** `PLATFORMS == ["climate", "sensor", "button",
  "binary_sensor"]`; `binary_sensor.py` exists; no other binary
  entity key exists anywhere in the integration (the 021 placeholders
  stay dead — no registry, no leftover). [test + grep]
- **AC-6** The `Push stream` `control_candidates` include 2074 with
  its evidence tag, and the probe dump labels register 2074 (the
  `KNOWN_LABELS` entry). [test]
- **AC-7** README: the features table carries the Fault row; the
  "not available" paragraph and the troubleshooting row state the
  new truth; no "fault code not displayed" claim remains anywhere in
  the README. The three translation JSONs are untouched (key parity
  test stays green). [manual + test]
- **AC-8** No new option key, flow step, service, or confirmation
  anywhere for the fault: the options schema for push entries stays
  exactly `poll_interval` + `temperature_unit`. [test]
- **AC-9** The full suite and the `modbus.py` self-test stay green;
  the value rails, allowlists and pinned frames are byte-identical
  (the untouchables, plan §5). [CI]

---

## 3. Out of scope

- **The bit names** (which protection bit 8 names) — [A] in research
  §2 until a live diff or the vendor's table proves them; NEVER in
  entity code.
- **The vendor's raw↔code table beyond the proven point** — the APK
  assets re-extraction (`assets/faults/faults_*.json`) remains the
  owner's optional static lead (research 022 §2.1-c).
- **A separate code-string sensor entity** — the code rides the
  Fault entity's attributes (one quantity, one entity; contract 013
  §4.1). A diagnostic sensor for code history may be a FUTURE spec's
  decision if the owner wants recorder graphs of codes; not now.
- **The firmware entity** — still no pushed home; unchanged.
- **Classic-mode fault exposure** — register 47 unreachable (law L1).
- **Fault writes** — none, ever; the fault plane is read-only.
- **specs 001-022** — never modified; 022's quickstart protocol is
  superseded for the fault part by THIS spec's quickstart (which says
  so); 022's files stay as the historical record.