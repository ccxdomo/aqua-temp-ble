# Research 001 — Reverse-engineering and design decisions

Status: **RETROSPECTIVE** — the analysis was done before/while building;
this file records the decisions and their evidence so future work does not
re-litigate them. Evidence lives in `FINDINGS.md` (raw analysis) and the
byte-exact test vectors.

---

## 1. Target and method

- Target: PHNIX pool heat pump ("Aqua Temp" / "Piscina Temp", Android app
  `com.phinx.poolheater4` v2.2.1, Flutter/Dart AOT, obfuscated).
- The owner's unit has **no WiFi**: BLE is the only operating channel. The
  vendor cloud (REST + MQTT in the app) is irrelevant for this unit — a
  local integration is the only way to control it without the app.
- Method: static analysis. `blutter` (arm64) recovered the object pool
  (`pp.txt`, ~29k objects) and class structure; function bodies were NOT
  decompilable (`dedup_instructions` + compressed pointers), so protocol
  constants were recovered from **string/byte constants in the object pool**
  — including the app's own outgoing frames (it logs them in cleartext).

## 2. Why "Modbus RTU over GATT"

Evidence (all from the APK, `FINDINGS.md` §8/§11):
- The pool contains the frames themselves: `63030007002D3C54` and six
  `6310…` frames, next to debug-log strings ("log: command sent",
  "log: command 6310").
- Frame structure decodes cleanly as Modbus RTU: slave `0x63`, FC03 read
  (start 7, count 45), FC10-shaped writes/queries, CRC16-Modbus appended
  little-endian.
- GATT plumbing comes from the non-obfuscated Java plugin
  (`flutter_blue_plus`): write on `ff01`, notify on `ff02`, CCCD `2902`.

Decision: implement standard Modbus framing on the two known GATT
characteristics rather than guessing a vendor protocol — the 7 recovered
frames all CRC-verify byte-exact, giving a hard regression anchor.

## 3. Why CRC16-Modbus little-endian, and how it was verified

Reference implementation: poly `0xA001` reversed, init `0xFFFF`, no
xor-out; wire order **low byte first**. The 7 recovered frames carry stored
CRCs (`3C54`, `D9E6`, `E8A2`, `F8B2`, `08BD`, `591B`, `68D2`) that match
this computation exactly — 7/7. Those frames are asserted byte-exact in
`tests/test_modbus.py` and `modbus.py`'s self-test, so any CRC/framing
regression fails the gate.

## 4. Scaling, sentinel and signedness

- Adjacent constants in the pool (`double(325)`, `double(315)` = 32.5 °C /
  31.5 °C next to the Inlet Temp decoder) establish `value = int(°C × 10)`.
- `-999.0 °C` appears as the "sensor absent" marker → raw `-9990` is the
  sentinel; decoded to `unknown` (never displayed as a temperature).
- Temperatures are parsed as **signed** int16 (negative ambient
  temperatures are real).

## 5. Base block and the "45 registers" inference

A `List<String>(45)` in the pool holds exactly 45 label names, and the
recovered read frame asks for 45 registers from address 7 (7..51). Labels
include Power, EnBoostMode, Mode, SetCurrentTemp, per-mode setpoints and
bounds (19–25), Inlet/Outlet/Ambient temps (26–28), Fault1 (47) and four
firmware-version registers (48–51). This is the register map shipped in
`registers.py`; the label→index mapping is ~80 % certain because the
indices are built at runtime inside app code that static analysis cannot
decompile.

## 6. Notification reassembly — why three rules

GATT notifications carry what fits in the MTU (typically 20-byte chunks for
a 95-byte FC03 response; a single notification is possible with a higher
MTU — actual behaviour unverified). Modbus has no length-delimiter inside
the response body, but the **header** implies the total length
(bytecount+5). The client therefore completes a response, in priority order:
1. **expected length** (from `expected_response_length`) — the fast path;
2. **silence gap** (`SILENCE_TIMEOUT`, 0.6 s without a new chunk) — covers
   unknown-shape responses (high-block queries);
3. **request deadline** (`REQUEST_TIMEOUT`, 8 s) — accepts a CRC-valid
   buffer if one arrived, else times out.

Any completed frame is CRC- and function-code-validated before the caller
resumes; leading garbage and stale chunks are dropped. This makes the
client robust to chunking surprises without trusting the link.

## 7. Writes: why the H1 (16-bit) layout, why no optimistic state

The APK hints at a 32-bit variant (qty=2, bytecount=4) but the exact layout
is a runtime value. Decision matrix:
- H1 (`63 10 addr 0001 02 value`) — if the device actually expects H2, the
  frame is at worst **ignored**; a neighbour register can never be clobbered.
- H2 — if wrong, it may zero a neighbouring register.
→ Ship H1; pin it with one logcat capture (`docs/CALIBRATION.md` step 5a —
the app logs every outgoing frame in cleartext).

No optimistic state: writes are serialized against reads via one
`asyncio.Lock` and confirmed by a debounced re-read. HA state therefore
always reflects **device-confirmed** values — important for a pool heat
pump where a silently-dropped write would otherwise look successful.

Power-before-mode on turn-on: a mode write while powered off may be
ignored by the controller, so the climate entity writes reg 8 = 1 first.

## 8. "No unverified data under a label"

The register map is ~80 % certain; the remaining items (compressor, pump,
4-way valve, exact mode enum, firmware join, …) are **not** guesses the UI
can accidentally confirm. Decision: every unverified register is
`address=None` in `registers.py` → the entity is **not created** at all.
Data can only appear under a label after a live session pins the register.
This costs three binary sensors today and buys: no wrong "compressor ON"
tile, no wrong mode name, and a one-constant calibration path.

## 9. Display units

Wire format is ×10 Celsius, proven. The vendor app displays °C/°F per
user setting. Decision: keep the wire pure; convert at the **entity
boundary** (`TemperatureConverter` in `climate.py`). The options flow stores
`temperature_unit` and the coordinator exposes it as a property; sensors
stay °C (native temperature sensors convert automatically in HA); the
climate entity converts. Options changes re-render immediately (see
`plan.md` §5).

## 10. Device resolution and the MAC problem

BlueNRG modules may change their MAC across power cycles. Resolution order:
HA bluetooth cache → `BleakScanner.find_device_by_address` → **name-prefix
scan** (`bluerng-`, `hiflying-`, `prov_` — the prefixes the vendor app
itself filters on). If found under a different address: warn loudly and use
it for the session, but **never rewrite the config** (the unique_id must
stay stable; the user is told to re-configure).

## 11. Single connection reality

The unit accepts exactly **one** BLE connection. The vendor app holding it
means HA cannot talk. Policy: lazy reconnect on the next cycle after a
failure (never a connect loop), entity `available` reflects connectivity,
and the config flow surfaces a clear "close the vendor app" error.

## 12. Calibration design (why a probe tool instead of hardcoding)

The remaining unknowns need **one live session**. Instead of shipping
guessed constants, the integration ships a probe:
- a diagnostic **button** (base block) and a **service** (base + the six
  recovered high-block queries),
- a deterministic log format (raw int16, hex, ÷10, label) usable as
  documentation,
- service response for programmatic comparison,
- read-only. Writes never go to blocks 1001–2090.

All corrections land in `registers.py` only (single calibration surface);
the runbook is `docs/CALIBRATION.md`.

## 13. Alternatives considered / rejected

| Alternative | Why rejected |
|---|---|
| Vendor cloud API | The unit has no WiFi; would also need an account and leaks data off-LAN |
| MQTT path from the app | Same reason — no network path exists on this unit |
| Sniffing BLE traffic | Not needed: the app's own cleartext frame constants + logcat give every vector |
| Frida runtime dump | Kept as fallback (`aqua_hooks.js`); static pool + live probe were sufficient |
| Hardcoding assumed registers as real entities | Violates "no unverified data under a label" (§8) |

## 14. Open questions handed to calibration

Everything still `[V]`/`[A]` is listed in `spec.md` §5 (UNVERIFIED) and
`data-model.md`. The three highest-value: mode enum values (U-1), write
layout H1/H2 (U-2), binary registers 29–46 (U-7).