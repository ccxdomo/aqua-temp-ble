# Aqua Temp BLE

A Home Assistant custom integration for **PHNIX pool heat pumps** sold under
the "Aqua Temp" app (`com.phinx.poolheater4`, Piscina Temp, …). It talks to
the unit directly over **Bluetooth Low Energy** — no vendor cloud, no
account, no WiFi needed (the unit does not even have WiFi).

The protocol was recovered by static analysis of the Android app:
**Modbus RTU frames over a BLE GATT characteristic** (slave `0x63`,
CRC16-Modbus little-endian). Every recovered frame is CRC-verified against
the app's own constants — see the reverse-engineering notes in
[`FINDINGS.md`](../FINDINGS.md) and [`BLUEPRINT.md`](../BLUEPRINT.md) in the
parent directory.

> ⚠️ **Status: pre-calibration.** The register map is ~80% established from
> static analysis. Everything marked **[P]** below is proven; the few
> remaining assumptions are isolated in `registers.py` (the single calibration
> surface) and the built-in **probe tool** closes them in one live session
> (see `docs/CALIBRATION.md`). Unverified data is **never** shown under a
> label — entities for unverified registers are simply not created.

## Features

| What | Status | Entities |
|---|---|---|
| Inlet / outlet / ambient water temperatures | **[P] proven** | 3 sensors (°C, ÷10 scaling, sentinel-aware) |
| Power on/off, mode heat/cool/auto, setpoint | **[P] read, [V] write to confirm live** | 1 climate entity |
| Fault code, firmware | **[P] address, [V] semantics** | 2 sensors (diagnostic) |
| Compressor / circulate pump / 4-way valve | **[V] registers not pinned yet** | hidden until calibration |
| Register probe / calibration tool | **working** | 1 button + 1 service |

Protocol facts baked into the code (all CRC-verified against the app):

- GATT write `0000ff01-…`, notify `0000ff02-…` (BlueNRG module)
- Base read: `63 03 0007 002D` → 45 holding registers (7..51), 95-byte response
- Writes: FC10 with the safe **16-bit H1 layout** (`63 10 addr 0001 02 value`)
- Temperatures: `value = int(degrees × 10)`; sentinel `-9990` = sensor absent

## Requirements

- Home Assistant **2024.12 or newer** (config flow, `OptionsFlow` with
  `config_entry`, `SupportsResponse`).
- A working Bluetooth adapter on the HA host (HA OS/Supervised usually fine;
  for container installs make sure `/var/run/dbus` and bluez are available).
- `bleak` is provided by HA itself (`requirements` lists it for standalone use).

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories*
2. Add this repository, category **Integration**
3. Install **Aqua Temp BLE**, restart Home Assistant

### Manual

```bash
cd config/custom_components
git clone <this-repo-url> aqua-temp-ble-tmp
cp -r aqua-temp-ble-tmp/custom_components/aqua_temp_ble .
rm -rf aqua-temp-ble-tmp
```

then restart Home Assistant.

## Pairing / first connection

- The unit must have been paired once with the vendor app (hold the panel
  button ~5 s until the display shows **`bLE`** blinking 1×/s).
- **Close the vendor app completely before using this integration** — the
  unit accepts a **single BLE connection**. If the app holds it, HA will show
  the entities as unavailable and retry politely.
- The unit advertises as **`BlueNRG-…`** (other models: `HIFLYING…`, `PROV_…`).

## Configuration

Settings → Devices & Services → Add Integration → **Aqua Temp BLE**

The flow starts with a **menu** offering three choices:

- **Select a discovered device** — the full list of every Bluetooth device
  Home Assistant has seen, in two tiers:

  1. **Recognized Aqua Temp units first** — advertised name starting with
     `BlueNRG-`, `HIFLYING-` or `PROV_`, or the ff01/ff02 GATT service UUIDs;
  2. then **other nearby devices**, nearest first (RSSI sort).

  Already-configured units are excluded, and unnamed devices appear as
  `Unnamed device — <address> — RSSI <value>` — the live connection test
  decides what is really a heat pump, not the advertised name.

- **Scan again** — waits ~8 s so a unit you just put in pairing mode has
  time to advertise, then rebuilds the menu with fresh counts (put the unit
  in **pairing mode** first: hold the panel button ~5 s until the display
  shows **`bLE`** blinking 1×/s);
- **Enter the Bluetooth address manually** — type the MAC
  (`XX:XX:XX:XX:XX:XX`) yourself. This stays one click away for
  MAC-changed units.

1. Pick a device (or enter the address manually).
2. HA performs a live connection test and shows what it read
   (power, mode, temperatures). A wrong pick simply fails with a clear
   error and returns to the same list — retry or pick another device.
3. Name the device — done.

If nothing at all is discovered, the menu shows the full pairing
procedure (hold the panel button ~5 s until **`bLE`** blinks 1×/s, close
the vendor app — the unit accepts a single BLE connection) plus the two
remaining actions.

Options (Configure on the device card):

- **Poll interval**: 10..600 s (default 30). Exactly **one** 45-register read
  per cycle — the heat pump is never flooded with requests.
- **Temperature unit**: °C/°F display only. The wire is always ×10 Celsius;
  °F values are converted at the entity boundary.

## Entities

| Entity | Register | Notes |
|---|---|---|
| Climate | 8/13/14 (+19-25, 26) | on/off, heat/cool/auto, setpoint (0.5 steps) |
| Inlet water temperature | 26 | ÷10, sentinel → unknown |
| Outlet water temperature | 27 | ÷10 |
| Ambient temperature | 28 | ÷10 |
| Fault code | 47 | 0 = no fault assumed (**[A]**, verify step 6) |
| Firmware | 48-51 | dot-joined (**[V]** semantics, verify) |
| Probe registers (button) | — | reads block 7..51 and logs it |
| Compressor / pump / valve | 29..46 [V] | appear after calibration |

## Calibration (one live session)

Before trusting mode writes and enabling the binary sensors, run the
calibration runbook — it takes ~10 minutes with a phone:

1. Press **Probe registers** (or call `aqua_temp_ble.probe_registers`)
2. Compare the log (`home-assistant.log`) with the app's Status page
3. Pin the few remaining constants in `custom_components/aqua_temp_ble/registers.py`
4. Restart HA

Full runbook: **[`docs/CALIBRATION.md`](docs/CALIBRATION.md)**

```yaml
# service example (Developer Tools → Actions)
service: aqua_temp_ble.probe_registers
data:
  block: base
```

## Standalone tools (no HA needed)

- `tools/smoke_client.py` — connect + dump the 45 registers (read-only)
  ```bash
  pip install bleak
  python3 tools/smoke_client.py XX:XX:XX:XX:XX:XX
  ```
- `tests/test_modbus.py`, `tests/test_reassembly.py` — protocol + reassembly
  unit tests (plain python, no extra deps)

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Unit not listed in the flow | not in pairing mode / out of range → pairing mode (`bLE` blinking 1×/s), then **Scan again** |
| Entities unavailable | vendor app holds the single BLE connection — close it |
| Setup fails `cannot_connect` | unit asleep / out of range / address changed → re-configure |
| MAC changed after power cycle | name-based fallback reconnects, but re-configure for a clean unique_id |
| Weird temperature values | byte-order — see CALIBRATION step 2 (`REG_DATA_LITTLE_ENDIAN`) |
| No reaction to writes | write layout 16/32-bit — see CALIBRATION step 5 (`WRITE_ECHO_EXPECTED`) |
| Verbose chunk logs | set `DEBUG_RAW = True` in `const.py` |

## Security / privacy

Local-only BLE; no cloud, no account, no credentials, no data leaves the LAN.
This integration sends writes only to registers 8 (power), 13 (mode) and 14
(setpoint) in the base block, and never writes to blocks 1001-2090.

## License & disclaimer

Provided as-is for personal use. This controls a pool heat pump — always
verify temperatures and behaviour on the device panel after installation,
especially before the calibration runbook is complete.

---

## Version française (résumé)

Intégration Home Assistant pour les pompes à chaleur de piscine PHNIX
(application « Aqua Temp »). Communication directe en **Bluetooth Low
Energy** (Modbus RTU sur GATT) — pas de cloud constructeur, pas de compte, la
PAC n'a même pas de WiFi.

**Installation** : via HACS (dépôt custom, catégorie Integration) ou copie
manuelle de `custom_components/aqua_temp_ble/` dans `config/custom_components/`,
puis redémarrage de HA.

**Avant la première connexion** : appareillez la PAC avec l'application du
constructeur au moins une fois (bouton du panneau maintenu ~5 s jusqu'à
l'affichage **`bLE`**). **Fermez ensuite complètement l'application** — la PAC
n'accepte qu'**une seule** connexion BLE à la fois.

**Configuration** : Paramètres → Appareils et services → Ajouter une
intégration → Aqua Temp BLE. L'unité s'annonce sous le nom `BlueNRG-…`. Un
test de connexion réel est effectué avant la création de l'appareil.

**Statut** : le mappage des registres est établi à ~80% par analyse statique
de l'APK. Les 20% restants se calibrent en une session (~10 min) avec le
**bouton « Probe registers »** et le runbook [`docs/CALIBRATION.md`](docs/CALIBRATION.md)
(qui explique aussi la capture logcat de l'application et le mot de passe
debug « 66 »). Aucune donnée non confirmée ne s'affiche sous un label — les
capteurs binaires (compresseur, pompe, vanne 4 voies) n'apparaissent qu'une
fois leurs registres confirmés.

**Avertissement** : cette intégration pilote une PAC de piscine. Vérifiez
toujours les températures et le comportement sur le panneau de l'appareil,
en particulier avant la fin de la calibration.