# Spec 022 — Contract: fault exposure (the evidence bar)

Status: **CONDITIONAL** — this contract binds any follow-up that
exposes a fault. It activates ONLY on the evidence defined in §1. Until
then (FR-10) no fault entity of any platform may exist. The research
basis is `../research.md` §2; the live instrument is
`../quickstart.md` (the E03 protocol).

---

## 1. The evidence bar — what must be recorded BEFORE any entity

All five, on the OWNER's production unit, in the repo record (a session
note, a follow-up spec, or an appendix here):

1. **A baseline capture** of the pushed blocks in the healthy state
   (the probe log: every raw of block 2001, plus the other blocks).
2. **A fault capture** taken while the panel DISPLAYS a fault code
   (the panel's code written down verbatim, e.g. `E03`), diffed against
   the baseline: at least one register in the pushed plane
   (1001-2090) CHANGED with the fault.
3. **A cleared capture** after the fault clears (power off, condition
   removed, panel back to normal): the changed register(s) REVERTED to
   the baseline values. (Diff-revert — the standard that pinned 2011,
   2019 and 1046. No revert, no pin.)
4. **The burst state during the fault**: whether the pushed stream kept
   flowing (probes succeeded) or went silent (captures taken after the
   clear instead — weaker evidence, labeled as such).
5. **The register's healthy-state value and fault-state value**,
   written down (e.g. `2034: 0 → 5`).

Without all five: the research stays at [V] and no entity ships.

## 2. The exposure shape (what a proven fault gets)

Exactly ONE entity per entry, in push mode only:

- **Platform**: `binary_sensor` (the justified return of the platform
  pruned by spec 021 — that prune removed placeholders with NO pinned
  register and NO pushed home; this is the one binary with a real,
  diff-proven home).
- **Name**: `Fault`; unique_id `<ADDRESS>_fault`; device: the entry's
  device (manufacturer PHNIX, model Aqua Temp (BlueNRG)).
- **device_class**: `problem`.
- **ON when** the pinned register(s) indicate a fault, per the decode
  rule recorded with the evidence (a threshold, a bit mask, or a
  nonzero-when-healthy-is-zero rule — whichever the diff proved).
  OFF when it reads the healthy value; `unavailable` when the block is
  absent/stale (the freshness gate — never a guessed OFF).
- **Attributes** (diagnostic evidence, never the state): the raw
  value(s), the register address(es), the evidence tag, and — once
  proven — the fault code string.

## 3. The code string rule ("E03" on the tile)

- The vendor's fault vocabulary is proven to exist (research §2.1-a:
  E01-E08, E19, E29, E051, E081, F/P codes) but the **raw↔code map is
  NOT recovered** (it lives in the APK's `assets/faults/faults_*.json`,
  not on this host; research §2.1-c).
- A `Fault code` may be DISPLAYED (as attribute or — in a later spec —
  a diagnostic sensor) ONLY for raw values whose code was observed
  live on the panel during a recorded capture (§1 items 2-3). Every
  other non-healthy raw displays as `unknown code (raw N)`.
- Guessing a table (bit position → E-code, list order → bit order) is
  FORBIDDEN in entity code; such hypotheses live in research notes
  tagged [A] until a live diff proves them.

## 4. Non-goals / standing rules

- No fault WRITE path, ever — the fault plane is read-only
  (`ensure_writable` allowlists unchanged: 1011, 1046 only).
- No fault entity in classic mode (register 47 remains the classic
  candidate; unreachable — law L1).
- The confirm/options texts that say "fault code not displayed" are
  updated in the SAME follow-up that adds the entity (one sentence
  each, three files — strings/en/fr).
- The freshness and honesty rules apply unchanged: absent block →
  unavailable, never OFF; no optimistic fault clearing.
- This contract composes with VOLET 1: nothing here reintroduces a
  human gate. The fault entity ships ACTIVE like everything else
  (there is nothing to confirm — the evidence bar is this contract's
  §1, satisfied before the code exists).