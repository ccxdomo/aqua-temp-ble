# Contract — Verification service (`aqua_temp_ble.probe_registers`)

Status: **IMPLEMENTED** — the calibration/verification surface of the
integration. Everything the probe does is **read-only**; it exists to turn
every `[V]`/`[A]` item in `data-model.md` into a verified fact in one live
session (`docs/CALIBRATION.md`).

---

## 1. Surfaces

| Surface | What it does | Scope |
|---|---|---|
| Button entity `Probe registers` (diagnostic) | reads the **base block** and logs it | base only |
| Service `aqua_temp_ble.probe_registers` | reads base **or** a high block, logs it, optionally returns values | base + 1001/1046/1091/1136/2001/2046 |

Both go through `coordinator.async_probe_block()` → the client I/O lock, so
a probe can never interleave with a poll or a write.

## 2. Service schema

```yaml
service: aqua_temp_ble.probe_registers
data:
  device_id: "a1b2c3d4e5f678901234567890123456"  # optional; single id or list
  block: "base"   # base | 1001 | 1046 | 1091 | 1136 | 2001 | 2046 (default: base)
```

- `device_id` optional. One id, a list of ids, or omitted → probe **every**
  configured Aqua Temp unit.
- `supports_response: OPTIONAL` — the service returns the register dump to
  the caller (usable in Developer Tools → Actions with "returning" scripts).
- Unknown device id / no configured units → `HomeAssistantError` with a
  clear message.
- A failed probe of one unit does not abort the others; each failure is
  logged and returned as `{"error": "..."}` for that address.

## 3. Log contract (human-readable, deterministic)

Format written to `home-assistant.log` at INFO level — one line per
register, stable across runs:

```
AquaTemp probe: XX:XX:XX:XX:XX:XX block=base (FC03 start=7 count=45) len=95 CRC=OK
  reg   7 =      0 (0x0000) | /10 =     0.0
  reg   8 =      1 (0x0001) | /10 =     0.1  # Power [P]
  reg  26 =    243 (0x00F3) | /10 =    24.3  # InletWaterTemp [P]
  ...
```

- RAW column = signed int16 as stored; hex = unsigned 16-bit view;
  `/10` = the temperature hypothesis (to confirm scaling at a glance);
  trailing `# Label [P]` annotation when the register is mapped.
- Base block logs always CRC=OK (a bad CRC fails the read before logging).

## 4. Response contract (machine-readable)

```json
{
  "XX:XX:XX:XX:XX:XX": {
    "block": "base",
    "crc": "OK",
    "registers": {
      "8":  {"raw": 1,    "hex": "0x0001", "tenths": 0.1, "label": "Power [P]"},
      "26": {"raw": 243,  "hex": "0x00F3", "tenths": 24.3, "label": "InletWaterTemp [P]"}
    }
  }
}
```

High blocks (experimental [V]): the probe sends the recovered 8-byte query
selector and accepts any CRC-valid response (`ble-response.md` rules).
If the response parses as FC03, registers are decoded with the same shape
as base (labels from `KNOWN_LABELS` when known); otherwise the raw hex is
returned/logged:

```json
{"XX:XX:XX:XX:XX:XX": {"block": "2001", "crc": "OK", "raw": "631003..."}}
```

Failure shape per address:
`{"XX:XX:XX:XX:XX:XX": {"block": "base", "error": "<reason>"}}`.

## 5. How the probe closes each calibration item

| Probe observation | Calibration action (`registers.py`) |
|---|---|
| `reg 26` shows `0x00F3` for a known 24.3 °C inlet | endianness confirmed → keep `REG_DATA_LITTLE_ENDIAN = False` |
| `reg 26` shows `0xF300` | flip `REG_DATA_LITTLE_ENDIAN = True` |
| `reg 13` value while app sets Cool/Heat/Auto | fix `MODE_VALUE_TO_NAME` order |
| which regs 29–46 are `1` while compressor/pump run | pin `REG_COMPRESSOR`, `REG_CIRCULATE_PUMP`, `REG_FOUR_WAY_VALVE` (polarity via `BINARY_ACTIVE_WHEN_NONZERO`) |
| `reg 48-51` bytes with firmware visible on the panel | fix the firmware join in `AquaTempData.from_regs` |
| `reg 47` with/without an active fault | confirms the fault-code semantics table |
| high-block reads (1001–2090) | map the app's Status/Control parameter pages (phase 2, read-only) |

Write-side items (H1/H2 layout, echo, per-mode mirroring) are closed by a
**logcat capture of the vendor app** (runbook step 5a) or by an idempotent
HA write test (5b) — the probe re-reads afterwards to confirm only the
intended register moved.

## 6. Guarantees

- **Read-only**: no probe path can write to the device.
- **One block per call**; serialized with polling and writes (I/O lock).
- **Never crashes on odd answers**: unknown shapes are logged as raw hex,
  exceptions surfaced per-address in the response.
- The standalone twin `tools/smoke_client.py` produces the same log format
  without Home Assistant (see `quickstart.md`).