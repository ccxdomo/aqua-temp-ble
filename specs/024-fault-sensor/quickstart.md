# Spec 024 — Quickstart: the code on the tile, and the latch you already proved

Date: 2026-10-12
Audience: the owner. §1 is what 0.24 gives you; §2 is the live
verification that replays YOUR 2026-10-12 experiment (the E03 + the
latch + the power-cut clear); §3-§5 are the reading guide. This
supersedes quickstart 023 §2's clearing step (its "climate → Off
clears the fault" clause is not reliable — the latch is the proven
world; 023's file stays as the historical record).

---

## 1. What 0.24 gives you

One new entity per unit: **`<device name> Fault code`** — the thing
you asked for: the code itself, on the tile, where "Problematique"
used to be all a binary could show. It reads the SAME fault word
register 2074 the Fault binary reads (one source, two faces):

- **`No fault`** when the fault memory is empty (register 0);
- **`E03`** when the register carries the code you proved twice
  (256 ↔ E03 — your diff-revert and your follow-up run);
- **`unknown code (raw N)`** for any other non-zero value — honestly
  unnamed until a live observation records it (§4);
- **`unknown`** if the data report is absent; **unavailable** if the
  stream goes silent (the same honest behavior as every entity).

The **Fault binary stays exactly as it was** — it remains the surface
for automations ("is a fault on record?": on/off). The sensor answers
the other question ("which fault?") for your eyes on the dashboard.
Both come from one register and can never disagree.

And one thing you already discovered is now documented in the README:
**the fault word is LATCHED.** The unit MEMORIZES a fault — the code
stays displayed while the unit is powered, even when the machine is
off and healthy, and only cutting the unit's power supply clears it.
You proved this yourself (2026-10-12): E03 → machine off, the code
stayed → unplug/replug, `No fault`. The tile was never lying — it
reports the machine's own memory faithfully.

No configuration: the sensor appears with the others when the entry
loads. Nothing to tick, confirm, or enable.

## 2. The live verification (the E03 + latch replay, ~15 minutes)

The same reversible dry run as quickstart 023 §2 (same safety notes:
un-plumbed unit, watch it, do not leave it running unattended; this is
the only proven-safe provocation). You did this once; this walks the
tile through every state, including the one that surprised you:

1. **Healthy baseline:** unit off, no fault on the panel → the Fault
   code tile reads **`No fault`** (the Fault binary off), attributes
   show `raw: 0`, no `code`.
2. **Provoke:** the climate tile → **Heat**. The pump runs dry; the
   panel shows **E03** within minutes. The Fault code tile flips to
   **`E03`** — expected attributes: `raw: 256`, `hex: "0x0100"`,
   `set_bits: [8]`, `code: "E03"`, `code_label: "water flow problem"`
   (the vendor app's own label for E03).
3. **THE LATCH CHECK (the part you proved):** climate → **Off**. The
   machine stops, the panel is fault-free, the unit is healthy at
   rest → the tile **KEEPS `E03`**. This is CORRECT, not a bug: the
   unit memorizes the fault while it has power. Turning the machine
   off does not clear the memory. (If you reload HA or the BLE link
   restarts meanwhile, the code stays — the memory is the machine's,
   not ours.)
4. **The clear:** cut the unit's power supply (**unplug, replug** —
   the one proven clear). When the stream returns, the tile reads
   **`No fault`**, `raw: 0` — the memory reset at power-up.

That full round trip — `No fault → E03 → (off, still E03) → No
fault` — is the acceptance. You already ran it once by hand; the
difference is that now the tile says what the panel says, and the
README explains step 3 to future you.

## 3. If the tile disagrees with the panel

| Observation | Reading |
|---|---|
| Tile shows a code, panel is clear, machine healthy | **The latch — normal.** The memory holds an old fault; cut the supply to clear it (§2 step 4). The entity reports the machine's memory faithfully. |
| Tile `No fault`, panel shows a fault | A real result — the fault does not ride register 2074. Record it: panel code + time + a probe capture (the 022 watch-list instruments still cover the hunt). |
| Tile `unknown code (raw N)`, panel shows a known code | The §4 banking case — a free proven row. |
| Tile `unknown` / `unavailable` | No usable report / the stream is silent (distance, the vendor app holding the link, the unit unplugged) — the shared honest behavior, not a fault verdict. |

## 4. Banking future codes (unchanged — quickstart 023 §4)

Each fault that happens on its own is one free proven row: write down
the exact code the panel displays, copy the attributes of EITHER
entity (`raw`, `set_bits` — both faces carry the same evidence), note
the revert when it clears. Hand the pair to Codepilot; one row gets
added to the code map and the tile shows that code by name.

**Safety line — unchanged:** never provoke faults beyond the dry-run
flow trip (E03). Record the ones the machine produces; the APK's
`assets/faults/faults_*.json` remains the optional static shortcut
for the vendor's own table.

## 5. What the tile can and cannot tell you (the honesty)

- **Can:** which code the unit has on record (the memorized fault),
  the raw and the bit pattern (the arithmetic), the code's label when
  the vendor app itself named it (E03 = "water flow problem"), and
  when HA saw each change (the tile's history — the only timeline
  that exists; the unit does not date its faults).
- **Cannot:** whether the fault is ACTIVE right now — the register
  memorizes; the PANEL is the authority for what is in progress.
  Which protection a bit NAMES is still unproven (the disassembly's
  candidate list is a hypothesis, research 023 §2 — no bit name will
  ever appear on the tile until a live diff proves one).
- **Firmware:** still not displayed — no home identified (unchanged).