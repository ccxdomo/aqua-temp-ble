# Spec 023 — Quickstart: verify the Fault entity with the E03 + bank new codes

Date: 2026-10-12
Audience: the owner. Two parts: §1-3 verify the new Fault entity
against the fault you already proved (the E03 dry run); §4 is the
protocol for banking FUTURE fault codes (opportunistically — no
dangerous experiments).

---

## 1. What 0.23 gives you

One new entity per unit: **`<device name> Fault`** — a binary sensor
(`device_class: problem`). It reads the fault word **register 2074**
— the register your 2026-10-12 E03 run pinned (`0 -> 256 -> 0`):

- **OFF** when 2074 reads 0 (no fault);
- **ON** when 2074 reads anything else (fault present);
- **unknown** when the data report is absent; **unavailable** when
  the stream goes silent (same honest behavior as every other
  entity).

Its attributes carry the evidence: `raw`, `hex`, `set_bits` (which
bits are set — e.g. `[8]` for the E03), and `code` — the panel code
**when it was proven live**: raw 256 shows `code: "E03"` (your run);
any other non-zero raw honestly shows `unknown code (raw N)` until a
recorded observation proves it.

No configuration: the entity appears with the others when the entry
loads. Nothing to tick, confirm, or enable.

## 2. The live verification (the E03 replay)

You already proved E03 once; this replays it with the entity
watching (~15 minutes, the same reversible dry run — see spec 022's
quickstart for the safety notes; do not leave the unit running
unattended):

1. **Healthy baseline:** with the unit off and no fault on the
   panel → the Fault tile shows **off**, attributes show
   `raw: 0`, no `code`.
2. **Provoke:** the climate tile → **Heat**. The pump runs dry.
   Watch the panel: the code appears within minutes (E03 expected —
   any code works, write down the exact one).
3. **Watch the entity:** it flips **on**. Expected attributes for
   E03: `raw: 256`, `hex: "0x0100"`, `set_bits: [8]`,
   `code: "E03"`.
4. **The log evidence (passive):** a HA reload during the fault logs
   `block push captured: start=2046 ... crc=OK hex=631007fe002d5a...`
   with the `01 00` at bytes 63-64 — your captured frame, now pinned
   byte-exact in the test suite.
5. **Clear:** climate → **Off** (the fault clears; power-cycle if it
   latches). The tile returns **off**, `raw` back to 0.

That round trip is the acceptance: `off -> on (E03) -> off`.

## 3. If the entity disagrees with the panel

- **Panel shows a fault, entity stays off** → the fault does not ride
  2074 (a real result — record it: panel code + time + a probe
  capture; the 022 watch-list instruments still cover the hunt).
- **Entity on, panel clear** → note the raw (`set_bits` tells you
  which bit); a lingering/annunciating fault word is plausible, but
  a mismatch is evidence — record it.
- **Unknown code on a KNOWN panel code** → that is exactly the §4
  banking case.

## 4. Banking future codes (the opportunistic protocol)

Every new fault that happens on its own is one free proven row. When
a fault appears — yours or the machine's own timing:

1. Write down the **exact code the panel displays**.
2. Open the Fault entity → copy the attributes (`raw`, `set_bits`).
   (A probe capture or the WARNING hex works too — the raw is what
   matters.)
3. When it clears, note the revert (the entity back to off).

Hand the pair to Codepilot: one row gets added to the code map
(`PUSHED_FAULT_CODE_MAP`), the tile then shows that code by name.

**Safety line — do NOT provoke faults deliberately beyond the dry
run.** The flow trip (E03) is proven safe and reversible. A
deliberate high-pressure trip, low-pressure manipulation, emergency
switch, or sensor disconnection risks the hardware or the wiring —
never chase them; record them when the machine produces them.

**The optional static shortcut:** the vendor app's own fault tables
live in the original APK at `assets/faults/faults_*.json` (proven
to exist; not on this host). If you still have the APK: open it as a
ZIP, extract that folder, hand the files over — the code meanings
(and possibly the bit order) may fall out without a single live run.

## 5. What the tile can and cannot tell you (the honesty)

- **Can:** that a fault is present (the binary truth), which raw/bit
  pattern the machine reports (the arithmetic), and the code string
  for every raw you have proven live (today: E03 = 256).
- **Cannot (yet):** which protection a bit NAMES. The app's ten-flag
  list is the candidate vocabulary, but the bit order is unproven —
  and under the list's order, your bit 8 would read
  *OutletWaterTempSensorFault*, not the flow switch (research 023
  §2). The spec refuses to guess: no bit carries a name until a
  recorded diff or the vendor's table proves it.
- **Firmware:** still not displayed — no home identified (unchanged
  since 022).