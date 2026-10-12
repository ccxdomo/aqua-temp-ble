# Spec 023 — Contract: the fault exposure (register 2074)

Status: **ACTIVE** — the evidence bar of
[`../022-defaults-and-faults/contracts/fault-evidence-contract.md`](../022-defaults-and-faults/contracts/fault-evidence-contract.md)
§1 is satisfied (research 023 §1.1: the owner's 2026-10-12 E03
diff-revert, `0 -> 256 -> 0`). This contract is the normative record
of WHAT was pinned and the rules any fault surface must keep. It
carries the 022 contract's §2 shape, §3 code-string rule and §4
non-goals into force.

---

## 1. The pin (what is proven, and how)

- **The fault word is pushed register 2074**, in the 2046 block
  (index 28; data offset 56; frame bytes 63-64; big-endian — three
  anchors, research 023 §1.3).
- **Evidence:** the owner's E03 run (2026-10-12): healthy capture
  (2074 = 0) -> fault capture while the panel displayed **E03**
  (2074 = 256 = 0x0100 = bit 8) -> cleared capture (2074 = 0). The
  E03-state frame is CRC-verified byte-exact (reconstruction CRC
  0x1D55 = the captured tail; research 023 §1.2). The full-capture
  diff shows no other mover except the power consequence (2011: 0 ->
  1) and thermal drift (2047/2049/2050/2051).
- **The decode rule:** `2074 == 0` -> clear; `2074 != 0` -> fault
  present. Bit semantics are NOT part of the pin — only the binary
  truth is.

## 2. The exposure shape (exactly one entity per push entry)

- **Platform:** `binary_sensor` — the justified return of the
  platform pruned by spec 021 (that prune removed registry
  placeholders with no pinned register; this is the one binary with
  a diff-proven home). The platform file creates ONE literal entity;
  there is NO binary registry and NO placeholder entries — creation
  is never registry-driven again.
- **Name:** `Fault`; `device_class: problem`; NO entity_category (an
  operational signal, not plumbing); unique_id `<ADDRESS>_fault`;
  device: the entry's PHNIX device.
- **States:** ON when `2074 != 0`; OFF when `2074 == 0`; `unknown`
  when the register is absent — **never a guessed OFF**; a stale or
  silent stream fails the poll cycle and the entity goes
  **unavailable** with every other entity (the freshness gate,
  contract 013 §6.1 — no fault-specific code needed for this).
- **No optimistic clearing:** the state only ever follows a fresh
  pushed block.
- **Attributes (evidence, never the state):** `register` (2074),
  `raw`, `hex`, `set_bits` (the set-bit indices — arithmetic only,
  NO names), `code` (per §3).

## 3. The code-string rule (the raw <-> panel-code map)

- `registers.PUSHED_FAULT_CODE_MAP` is DATA: one row per RECORDED
  live observation (the panel's displayed code + the 2074 raw, in
  its diff-revert context). Today exactly one:
  `256 -> "E03"` [P owner 2026-10-12].
- A code is DISPLAYED **only** for mapped raws; every other non-zero
  raw reads `unknown code (raw N)`.
- **Extending the map** = one new recorded observation (quickstart
  023 §4) + a one-row edit in a follow-up. Guessing tables is
  FORBIDDEN in entity code.
- **Bit-to-flag-name mapping is FORBIDDEN in entity code.** The
  vendor's ten-flag list order is an [A] hypothesis (research 023
  §2) — and under it, bit 8 is the *outlet water temp sensor fault*,
  not the flow switch. Bit names live in research notes until a
  live diff proves them.

## 4. The diagnostics continuity

2074 stays observable as a RAW alongside its entity: it joins
`PUSH_CONTROL_CANDIDATES` (the `Push stream` `control_candidates`
attributes) and `KNOWN_LABELS` (the probe store-dump labels) — the
pinned-homes precedent (2011/2019/1046). The diff instruments of
specs 015/022 keep working unchanged.

## 5. Non-goals / standing rules

- **No fault WRITE path, ever.** 2074 is in NO allowlist;
  `ensure_writable` refuses it forever (the funnel is unchanged:
  1011 with {0, 1}; 1046 with raw 100..400 step 5).
- **No classic-mode fault entity.** Register 47 is unreachable
  (law L1, spec 020).
- **No portal, gate, option, checkbox, service or confirmation** for
  the fault — the exposure is read-only decoding, active by default
  (contract 022 §4 composed here: the evidence bar was satisfied
  before the code exists; there is nothing to confirm).
- **No second entity for the code.** The code string rides the Fault
  entity's attributes (one quantity, one entity — contract 013
  §4.1). A code-history diagnostic sensor is a FUTURE spec's option,
  only if the owner asks for it.
- **No bit names in code** (§3).
- **The honesty limits stand:** absent block -> unknown; stale ->
  unavailable; a fault that never moves 2074 is INVISIBLE to this
  surface (the entity is exactly as truthful as the pushed plane).

## 6. The extension protocol (how each new code gets pinned)

Each new PROVEN code needs exactly: the panel's displayed code,
written down while the fault lives + the 2074 raw (the entity's
`raw`/`set_bits` attributes, or the WARNING capture hex, or the
probe store dump). Hand the pair to the repo record; a follow-up
adds one row to `PUSHED_FAULT_CODE_MAP`. The dry-run flow trip is
the only PROVEN-safe provocation; all other faults are recorded
opportunistically as they happen (never deliberately provoked —
quickstart 023 §4's safety line). The APK's
`assets/faults/faults_*.json` remains the optional static lead for
the vendor's own table.