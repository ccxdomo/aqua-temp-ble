# Spec 023 — Plan: the design

Date: 2026-10-12
The evidence record is `research.md` (the owner's E03 diff-revert,
the frame arithmetic, the bit-8 analysis); the normative rules are
`contracts/fault-exposure-contract.md`. This file is the WHAT and the
HOW for the Coder: the change map, file by file, with the exact code
shapes. The untouchables are §5.

---

## 1. The shape of the change

Four production files change (`registers.py`, `const.py`, the NEW
`binary_sensor.py`, `README.md`), zero test-only regressions
elsewhere, one new test suite. No option, no flow, no service, no
schema changes anywhere. Total new production code: ~120 lines.

```
custom_components/aqua_temp_ble/
  registers.py        + REG_PUSHED_FAULT, 3 helpers, the code map,
                       1 KNOWN_LABELS entry, 1 candidate entry
  const.py            ~ PLATFORMS gains "binary_sensor" (+ comment)
  binary_sensor.py    NEW — the platform file (ONE entity, literal)
  README.md           ~ 3 spots (features row, not-available para,
                        troubleshooting row)
tests/
  test_fault_023.py   NEW — the suite (AC-1..AC-8)
```

## 2. The design, file by file

### 2.1 registers.py — the fault plane (FR-5)

Placement: after the pushed-control section (`PUSHED_CONTROL` /
`PUSHED_MODE_VALUE_TO_NAME`) and before `PUSH_CONTROL_CANDIDATES`,
a new section. Exact content:

```python
# --- pushed fault plane (spec 023, research 023 §1) ---------------------
# The fault word — PINNED by the owner's E03 diff-revert (2026-10-12):
# healthy 0 -> fault 256 (0x0100, bit 8) while the panel displayed
# E03 -> 0 again after the clear. The E03 frame is CRC-verified
# byte-exact (research 023 §1.2); the register sits in the 2046-block
# zero tail (offset (2074-2046)*2 = 56 in the block data). Read-only
# forever: 2074 is in NO write allowlist (spec 003 §6 / contracts
# 016 §4 / 019 §2.2 — the funnel refuses it).
REG_PUSHED_FAULT = 2074  # [P owner diff-revert 2026-10-12]

# The PROVEN raw<->code observations — DATA, one row per recorded
# live diff-revert (contract 023 §3). Today: exactly one. raw 256
# (bit 8) was captured while the panel displayed E03. Extending the
# map = one new recorded observation + one row (quickstart 023 §4).
# Bit-to-FLAG-NAME tables are FORBIDDEN here (the ten-flag list order
# is an [A] hypothesis, research 023 §2 — never in entity code).
PUSHED_FAULT_CODE_MAP: dict[int, str] = {
    256: "E03",  # [P owner run 2026-10-12 — panel E03 while raw 256]
}


def decode_fault_flag(raw: int | None) -> bool | None:
    """The fault word -> fault present; None when the register is absent.

    The diff-revert rule (contract 023 §2): 0 = clear, non-zero = fault
    present. Bit semantics are NOT decoded (unproven); only the binary
    truth is.
    """
    if raw is None:
        return None
    return raw != 0


def fault_code(raw: int | None) -> str | None:
    """The panel-code string for a fault raw; None when clear/absent.

    Only PROVEN observations map (PUSHED_FAULT_CODE_MAP); every other
    non-zero raw reads 'unknown code (raw N)' — the contract 022 §3
    rule against guessed tables, carried into entity code.
    """
    if raw is None or raw == 0:
        return None
    return PUSHED_FAULT_CODE_MAP.get(raw, f"unknown code (raw {raw})")


def fault_set_bits(raw: int | None) -> list[int]:
    """The indices of the set bits (arithmetic only, no names).

    Exists so the NEXT fault observation records its bit pattern with
    zero tooling (the opportunistic map protocol, quickstart 023 §4).
    """
    if raw is None:
        return []
    return [i for i in range(16) if raw & (1 << i)]
```

`PUSH_CONTROL_CANDIDATES` gains (the 2011/2019/1046 pinned-homes
precedent — pinned homes stay observable for diffs):

```python
    2074: "fault word [P owner diff-revert 2026-10-12 — spec 023]",
```

`KNOWN_LABELS.update` gains (the probe dump annotates the register):

```python
KNOWN_LABELS.update({
    2074: "Fault word (bitfield) [P owner diff-revert 2026-10-12]",
})
```

NOT touched: `PUSHED_CONTROL`, `PUSHED_TEMPS`, `PUSHED_MIRRORS`,
`ALLOWED_WRITE_*`, `SETPOINT_*`, the frames, `ensure_writable`,
`check_registry_integrity` (2074 collides with nothing; it is not a
`pushed_temps` or `pushed_control` key, so the integrity rules do not
apply to it — verify by reading, no edit).

### 2.2 const.py — the platform list (FR-6)

```python
# before:
# Platform names — identical to homeassistant.const.Platform values. Plain
# strings are used on purpose (see module docstring). The binary_sensor
# platform was pruned in the spec-021 cleanup: its registers were never
# pinned and no pushed home exists, so no binary entity can ever be created.
PLATFORMS = ["climate", "sensor", "button"]

# after:
# Platform names — identical to homeassistant.const.Platform values. Plain
# strings are used on purpose (see module docstring). The binary_sensor
# platform was pruned in the spec-021 cleanup (registry placeholders with
# no pinned register); spec 023 returns it for the ONE diff-proven binary
# (the Fault word 2074 — the owner's E03 diff-revert). No other binary
# entity exists: there is no registry and no placeholder — the platform
# file creates exactly one literal entity per push entry.
PLATFORMS = ["climate", "sensor", "button", "binary_sensor"]
```

### 2.3 binary_sensor.py — the NEW platform file (FR-1/2/3/6)

The whole file (the button.py platform shape; the sensor.py decode
discipline; ~100 lines):

```python
"""Binary sensor platform for aqua_temp_ble — the fault exposure
(spec 023).

ONE entity, created for push-mode entries only: the Fault binary
sensor (device_class problem), reading the pushed fault word
register 2074 — the owner's E03 diff-revert (0 -> 256 -> 0,
2026-10-12; research 023 §1). The 021 prune removed registry
placeholders with no pinned register; this platform returns WITHOUT
a registry: one literal entity class, so no dead placeholder can
ever reappear. Classic-mode entries create nothing (register 47 is
unreachable — law L1, spec 020 §5).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator
from .entity import AquaTempEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if str(entry.data.get("mode", "classic")) != "push":
        return  # law L1: the classic fault home (reg 47) is unreachable
    async_add_entities([AquaTempFaultBinarySensor(coordinator, entry)])


class AquaTempFaultBinarySensor(AquaTempEntity, BinarySensorEntity):
    """The fault word 2074 as a problem binary (spec 023, FR-2/FR-3).

    ON when the raw is non-zero, OFF when it reads the healthy 0,
    unknown when the register is absent — never a guessed OFF; a
    stale stream fails the cycle and the entity goes unavailable with
    every other entity (the freshness gate). The attributes carry the
    evidence (raw, hex, the set-bit list, the code string when the
    raw has a PROVEN code).
    """

    _attr_name = "Fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "fault")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return None
        # The 2046-block capture feeds data.registers in push mode
        # (coordinator.from_pushes); 2074 rides it like the temps.
        return registers.decode_fault_flag(
            data.registers.get(registers.REG_PUSHED_FAULT)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return {}
        raw = data.registers.get(registers.REG_PUSHED_FAULT)
        attrs: dict[str, Any] = {"register": registers.REG_PUSHED_FAULT}
        if raw is not None:
            attrs["raw"] = raw
            attrs["hex"] = f"0x{raw & 0xFFFF:04X}"
            attrs["set_bits"] = registers.fault_set_bits(raw)
            code = registers.fault_code(raw)
            if code is not None:
                attrs["code"] = code
        return attrs
```

Design notes (the WHY, for the Coder):

- **No entity_category.** The fault is an OPERATIONAL signal (the
  machine's own fault state), not integration plumbing — the
  `push_stream` diagnostic is DIAGNOSTIC; this one is a regular
  entity so automations and alerts treat it as a first-class surface
  (the HA "problem" convention for appliance faults).
- **No registry, no loop.** The 021 prune's lesson: the dead
  placeholders existed because a registry drove creation. Here the
  entity list is one literal — a second binary entity can only
  appear via a new spec with new evidence.
- **`has_entity_name = True`** (inherited from AquaTempEntity) makes
  the tile render as "<device name> Fault".
- **unique_id** is `<ADDRESS>_fault` via the AquaTempEntity base
  (`address.upper()` + key), matching the 022 contract §2 shape.

### 2.4 README.md — three spots (FR-7)

1. **Features table** — a new row after "Setpoint control":

   ```
   | Fault indicator (code when proven) | **proven** (owner E03 diff-revert) | 1 binary sensor |
   ```

2. **The "Not available — by design" paragraph** (lines ~40-44) —
   drop the fault-code clause; keep mode and firmware; reword the
   binary-sensors clause:

   > the **mode** is not pilotable (heat-only unit; the vendor app
   > locks it, and its write would clobber an unpinned pair register);
   > the **firmware version** has no identified home in the data
   > report; the **other** binary surfaces (compressor/pump/fan/
   > valve/switches) were never pinned and stay pruned — the fault
   > IS exposed since spec 023 (register 2074, the E03 diff-revert).

3. **Troubleshooting row** — replace:

   ```
   | Fault code / firmware not displayed | no reliable source has been identified in the unit's data report so far (the vendor app's own fault table lives in the APK; see specs/022) |
   ```

   with:

   ```
   | Fault: the "Fault" binary sensor | ON when the fault word (register 2074) is non-zero — proven by the owner's E03 run; the `code` attribute shows the panel code when the raw↔code pair was recorded live (E03 = raw 256), else "unknown code (raw N)". Firmware: still not displayed (no home identified). |
   ```

The three translation JSONs are untouched (verified: they carry no
fault sentence today — the 022 rewrite moved that honesty to the
docs; the key-parity test stays green as-is).

### 2.5 tests/test_fault_023.py — the new suite (AC-1..AC-8)

The fixtures (byte-exact, in the test file header with the
provenance comments):

```python
# The owner's E03 capture (block 2046), byte-exact — CRC verified
# (research 023 §1.2). 2074 = 0x0100 = 256 at the bit-8 position.
FRAME_2046_E03 = (
    "631007fe002d5a"          # fn, 0x10, start 2046, qty 45, 90 bytes
    + "7ffd"                  # 2046: the T01-absent sentinel
    + "00910091009600960096"  # 2047-2051: the fault-capture temps
    + "00" * 44               # 2052-2073: the zero run
    + "0100"                  # 2074: THE FAULT (256 = bit 8)
    + "00" * 32               # 2075-2090: the zero tail
    + "551d"                  # CRC-16/Modbus (verified)
)
# The healthy twin: the same capture with 2074 = 0 (synthetic, valid).
FRAME_2046_E03_CLEAR = (
    "631007fe002d5a7ffd00910091009600960096"
    + "00" * 78 + "54f0"      # CRC recomputed for 2074 = 0
)
```

The cases (the suite walks the existing push-store test pattern —
`test_push_store_013.py` / `test_coordinator_push_013.py` fixtures):

1. **AC-2/AC-3 decode + attributes** (pure, no HA platform): feed a
   coordinator snapshot built from captures carrying 2074 = 0 / 256 /
   4 / absent; assert `is_on` off/on/on/None and the attributes
   (`code` E03 / unknown-code / absent; `set_bits` [8] / [2] / []).
2. **AC-4 the evidence fixtures**: both 99-byte frames through the
   real store (`_on_notify`) → the coordinator's snapshot decodes
   them → the entity reads ON (code E03, set_bits [8], raw 256) and
   OFF. This pins the owner's evidence byte-exact into CI.
3. **AC-1 creation**: push entry setup → exactly one binary_sensor
   entity, name "Fault", problem class, unique_id `<ADDR>_fault`;
   classic entry → zero.
4. **AC-5/AC-8 the platform surface**: `PLATFORMS` content grep;
   `binary_sensor.py` exists; NO other binary entity keys in
   `custom_components/`; the push options schema stays exactly
   `poll_interval` + `temperature_unit`.
5. **AC-6 diagnostics**: 2074 in `PUSH_CONTROL_CANDIDATES` → the
   `push_stream` `control_candidates` attributes include it;
   `KNOWN_LABELS[2074]` exists (probe output labels it).
6. **AC-7 docs**: README contains the new Fault row / reworded
   rows; no "not displayed" fault claim remains (grep).

### 2.6 What does NOT change (verify, do not edit)

- `coordinator.py` — nothing: `from_pushes` already exposes the
  2046-block raws on `data.registers`, and `push_control_raws`
  picks 2074 up automatically once it joins
  `PUSH_CONTROL_CANDIDATES`.
- `sensor.py`, `climate.py`, `button.py`, `config_flow.py`,
  `aqua_client.py`, `modbus.py`, `strings.json`, `translations/` —
  untouched.
- All pinned frames, allowlists, value rails, the probes, the
  WARNING capture line, the classic plane.

## 3. The phases

- **Phase A — the Coder**: everything above (tasks.md T1-T6). No
  live unit needed: the evidence fixtures ARE the E03 run, pinned.
- **Phase B — the owner (verification, optional)**: run the
  quickstart §2-3 live check (provoke E03 → the Fault entity ON with
  code E03 → clear → OFF). Not a ship gate — AC-4 already pins the
  frames.
- **Phase C — future codes**: the opportunistic protocol
  (quickstart §4): each new fault observation (panel code + the
  entity's attributes) adds one proven row to
  `PUSHED_FAULT_CODE_MAP` via a one-line follow-up.

## 4. Risks and mitigations

- **A stale block showing an old fault**: impossible-by-construction —
  the freshness gate fails the cycle when the T-block is stale; a
  successful cycle always carries a fresh 2074.
- **A fault the unit does not report on 2074**: the honest gap —
  the entity can only ever be as truthful as the pushed plane. The
  research record (§5) states the limit: only faults that move 2074
  are visible; a fault that moves nothing stays invisible (and the
  022 watch-list instruments remain for exactly that investigation).
- **Bit 8 misread as a named flag**: prevented by construction —
  no bit names exist in code; `set_bits` is arithmetic only.

## 5. The untouchables

- The write plane: `ensure_writable`, the allowlists ({0,1} for 1011;
  raw 100..400 step 5 for 1046), the pinned frames.
- The read plane: sentinels, plausibility, the freshness gate, the
  probes, the capture line (contract 013 §2.1).
- The heat-only display rule (spec 018) and the mirror confirmation
  cycles (specs 016/017/019).
- The dormant classic plane.
- `specs/001-022` — never modified.