# Plan 002 — Design of the scanning config flow

Status: **PLANNED**. This file describes the target design; the Coder
implements it in the order of `tasks.md`. Behaviour contracts are in
`contracts/` (state machine, device list format).

Confidence legend: **[P] PROVEN** (HA 2024.12.0 sources / this repo),
**[A] ASSUMED**, **[V] TO-VERIFY**, **[D] DECISION**.

---

## 1. State machine

```
                        add integration
                              │
                              ▼
                    ┌─── async_step_user ────┐  (menu, or no-devices menu)
                    │  scanner_count == 0?   │
                    │   abort no_bluetooth_  │
                    │   adapter              │
                    └──┬────────────────────┘
                       │ menu choice
      ┌────────────────┼──────────────────────┐
      │ "device"       │ "scan"                │ "manual"
      ▼                ▼                      ▼
async_step_device   async_step_scan        async_step_manual
 form: full list    progress, wait          form: MAC
 (tiered, no       SCAN_WAIT_SECONDS            │
  pseudo-entries)        │ back to user ◄───────┤
      │ pick              (same for a failed
      │                   scan-free user visit)
      ▼
async_step_test (progress: live connect + 45-register read, 20 s budget)
      │ ok                 │ fail
      ▼                   ▼
async_step_confirm     back to the origin step
 name → entry            (device / manual) with error
```

- The dormant `async_step_bluetooth` path is unchanged: it feeds the same
  test step with a `discovery_info` address (kept byte-identical).
- `scan` is reachable from the user menu **and** from the no-devices
  menu; both return to `async_step_user`.

## 2. Steps

### 2.1 `async_step_user` — menu

Built fresh on every visit (no caching between visits):

1. If `bluetooth.async_scanner_count(self.hass)` returns 0
   (**[P]** exists at 2024.12.0) → `async_abort("no_bluetooth_adapter")`.
2. Build the candidate list (`contracts/device-list-format.md`):
   `{address: label}` plus the tier counts.
3. If the list is empty → `async_show_menu(step_id="no_devices",
   menu_options={"scan": …, "manual": …})` — the pairing procedure is the
   step description (FR-14).
4. Otherwise → `async_show_menu(step_id="user",
   menu_options={"device": …, "scan": …, "manual": …},
   description_placeholders={"recognized": n_recognized,
   "total": n_total})` **[P]** menu + `description_placeholders`.
   - Menu options are plain dict keys routing to
     `async_step_<key>` **[P]** (FlowHandler semantics; switchbot uses
     the same mechanism).

### 2.2 `async_step_device` — the full list

- `vol.In(self._device_labels)` — insertion-ordered dict, tiers already
  sorted by the list builder. Dropdown order follows the dict's insertion
  order **[A]** (JSON/JS object key order; visually verified in
  quickstart V3). There are **no pseudo-entries** here — every value is a
  real address; actions live in the menu.
- Submit → `self._address = user_input["address"]`,
  `self._origin = "device"`, unique-id guard, → `async_step_test`.
- `self._device_labels` is rebuilt whenever the step is entered with
  `user_input=None` (each visit shows the current history; a failed test
  that routes back re-runs the build — harmless, history read only).

### 2.3 `async_step_scan` — bounded rescan

- Progress step mirroring the existing `test` pattern **[P]** (same
  repo): `async_show_progress(step_id="scan", progress_task=_async_wait())`.
- `_async_wait()`: `await asyncio.sleep(const.SCAN_WAIT_SECONDS)`, set
  `self._scan_done`, return. On re-entry (`_scan_done`) reset the flag and
  `return await self.async_step_user()`.
- No BLE API is called: HA's local scanner runs continuously in ACTIVE
  mode by default at 2024.12 **[P]** (`bluetooth/__init__.py`:
  `BluetoothScanningMode.PASSIVE if passive else ACTIVE`), remote proxies
  stream advertisements as they arrive. The sleep only gives a unit that
  just entered pairing mode time to advertise.
- Repeated invocations are harmless (E-11).

### 2.4 `async_step_manual` — unchanged, richer description

- Behaviour identical to today: MAC regex, `invalid_address` error,
  unique-id guard, `self._origin = "manual"` → test.
- Description gains the pairing tip (FR-7): if the address is unknown,
  put the unit in pairing mode and scan instead.

### 2.5 `async_step_test` — unchanged except `hass`

- Pass the client `hass=self.hass` (FR-9) so `_resolve_device` tries the
  HA cache first — the client constructor already accepts `hass`
  **[P]** (`aqua_client.AquaTempBleClient.__init__`).
- Failure routing already uses `self._origin`; add `"device"` to the
  branch (return `async_step_device(errors={"base": error})`).

### 2.6 `async_step_confirm`, options flow — untouched.

## 3. Device list builder (pure function)

Lives in `config_flow.py` as `_build_device_list(hass) -> (labels, counts)`
— inputs: `bluetooth.async_discovered_service_info(hass, connectable=True)`
**[P]** + `flow._async_current_ids()` **[P]**. Algorithm, ordering and
labels: see `contracts/device-list-format.md`. No I/O beyond the history
read; no scans.

```
def _build_device_list():
    configured = current_ids (upper-cased unique ids)
    entries = []
    for info in async_discovered_service_info(hass, connectable=True):
        if info.address.upper() in configured: skip
        has_name = info.name != info.address          # [P] fallback rule
        recognized = name-prefix match or ff01/ff02 in service_uuids
        rssi_key = info.rssi if isinstance(info.rssi, int) else -200
        entries.append(...)
    sort: recognized(rssi desc) then others(rssi desc)
    cap others at MAX_OTHER_DEVICES (30)
    labels = {address: f"{name or 'Unnamed device'} — {address} — RSSI {rssi}"}
```

Prefix match: `info.name.lower().startswith(NAME_PREFIXES)` — same
constant as today (`const.NAME_PREFIXES`), so the vendor list stays the
single source of truth. UUID match: `const.UUID_WRITE/UUID_NOTIFY`
(lower-cased) present in `info.service_uuids` — opportunistic only [V].

## 4. New constants (`const.py`)

| Constant | Value | Role |
|---|---|---|
| `SCAN_WAIT_SECONDS` | `8.0` | bounded rescan delay [D] |
| `MAX_OTHER_DEVICES` | `30` | cap on the "other devices" tier [D] |

(`NAME_PREFIXES`, `UUID_WRITE`, `UUID_NOTIFY` are reused as-is.)

## 5. Code touch points

| File | Change |
|---|---|
| `config_flow.py` | new `_build_device_list`, `async_step_device`, `async_step_scan`; `async_step_user` becomes a menu; `async_step_manual` description; test step: `hass=` + `device` origin; `async_step_bluetooth` untouched |
| `const.py` | two constants above |
| `strings.json`, `translations/en.json`, `translations/fr.json` | keys below |
| `README.md` | configuration + troubleshooting wording (all-devices list, rescan, pairing) |
| `manifest.json` | **no change** (deliberately no bluetooth matchers) |

## 6. Exact user-facing texts (translation keys)

Keys added/changed under `config` (menu labels under
`config.step.<id>.menu_options.<key>` **[P]** — switchbot strings
precedent at 2024.12.0):

### strings.json / en.json

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Add an Aqua Temp heat pump",
        "description": "Bluetooth devices seen nearby: {total}. Recognized Aqua Temp units: {recognized}. Units already configured are not listed.",
        "menu_options": {
          "device": "Select a discovered device",
          "scan": "Scan again",
          "manual": "Enter the Bluetooth address manually"
        }
      },
      "device": {
        "title": "Select your heat pump",
        "description": "Recognized Aqua Temp units are listed first, followed by other nearby Bluetooth devices (nearest first). If your unit is missing, put it in pairing mode — hold the panel button for about 5 seconds until the display shows \"bLE\" blinking once per second — then go back and scan again.",
        "data": {
          "address": "Device"
        }
      },
      "scan": {
        "title": "Scanning",
        "description": "Looking for nearby Bluetooth devices. This takes about {seconds} seconds…"
      },
      "no_devices": {
        "title": "No Aqua Temp unit found",
        "description": "No unconfigured Bluetooth device was seen nearby.\n\nTo make your heat pump visible:\n\n1. Put it in pairing mode: press and hold the panel button for about 5 seconds, until the display shows \"bLE\" blinking once per second.\n2. The unit then advertises under a name starting with \"BlueNRG\".\n3. Close the vendor app completely — the unit accepts only one Bluetooth connection.\n4. Stay within range, then scan again.\n\nIf no Bluetooth device appears at all, check that Home Assistant has a Bluetooth adapter (Settings → System → Hardware) or an active Bluetooth proxy.",
        "menu_options": {
          "scan": "Scan again",
          "manual": "Enter the Bluetooth address manually"
        }
      },
      "manual": {
        "title": "Manual address",
        "description": "Enter the Bluetooth address of the unit, e.g. `XX:XX:XX:XX:XX:XX`. Make sure the vendor app is closed first — the unit accepts a single connection.\n\nTip: if you do not know the address, put the unit in pairing mode (hold the panel button ~5 s until the display shows \"bLE\") and scan again.",
        "data": {
          "address": "Bluetooth address"
        }
      },
      "test": { "…unchanged…": "" },
      "confirm": { "…unchanged…": "" }
    },
    "abort": {
      "already_configured": "This heat pump is already configured.",
      "not_supported": "This Bluetooth device is not an Aqua Temp heat pump.",
      "no_bluetooth_adapter": "Home Assistant has no working Bluetooth adapter or proxy. Plug in a supported adapter (or check the Bluetooth integration), then try again. On container installs, make sure bluez and /var/run/dbus are available."
    },
    "error": { "…unchanged (cannot_connect, no_response, crc_error, invalid_address)…": "" }
  }
}
```

(`{seconds}` = `SCAN_WAIT_SECONDS` from code; `{total}`/`{recognized}`
from the list builder. The old `user` step description/`data` block is
replaced by the menu + the new `device` step.)

### fr.json — same keys, French

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Ajouter une pompe à chaleur Aqua Temp",
        "description": "Appareils Bluetooth détectés à proximité : {total}. Unités Aqua Temp reconnues : {recognized}. Les unités déjà configurées ne sont pas listées.",
        "menu_options": {
          "device": "Choisir un appareil détecté",
          "scan": "Relancer le scan",
          "manual": "Saisir l'adresse Bluetooth manuellement"
        }
      },
      "device": {
        "title": "Sélectionnez votre pompe à chaleur",
        "description": "Les unités Aqua Temp reconnues sont listées en premier, puis les autres appareils Bluetooth à proximité (les plus proches d'abord). Si votre unité n'apparaît pas, mettez-la en mode appairage — maintenez le bouton du panneau environ 5 secondes jusqu'à l'affichage « bLE » clignotant une fois par seconde — puis revenez et relancez le scan.",
        "data": {
          "address": "Appareil"
        }
      },
      "scan": {
        "title": "Scan en cours",
        "description": "Recherche des appareils Bluetooth à proximité. Cela prend environ {seconds} secondes…"
      },
      "no_devices": {
        "title": "Aucune unité Aqua Temp trouvée",
        "description": "Aucun appareil Bluetooth non configuré n'a été détecté à proximité.\n\nPour rendre votre pompe à chaleur visible :\n\n1. Mettez-la en mode appairage : maintenez le bouton du panneau environ 5 secondes, jusqu'à l'affichage « bLE » clignotant une fois par seconde.\n2. L'unité s'annonce alors sous un nom commençant par « BlueNRG ».\n3. Fermez complètement l'application du fabricant — l'unité n'accepte qu'une seule connexion Bluetooth.\n4. Restez à portée, puis relancez le scan.\n\nSi aucun appareil Bluetooth n'apparaît du tout, vérifiez que Home Assistant dispose d'un adaptateur Bluetooth (Paramètres → Système → Matériel) ou d'un proxy Bluetooth actif.",
        "menu_options": {
          "scan": "Relancer le scan",
          "manual": "Saisir l'adresse Bluetooth manuellement"
        }
      },
      "manual": {
        "title": "Adresse manuelle",
        "description": "Saisissez l'adresse Bluetooth de l'unité, ex. `XX:XX:XX:XX:XX:XX`. Fermez d'abord l'application du fabricant — l'unité n'accepte qu'une seule connexion.\n\nAstuce : si vous ne connaissez pas l'adresse, mettez l'unité en mode appairage (maintenez le bouton du panneau ~5 s jusqu'à l'affichage « bLE ») et relancez le scan.",
        "data": {
          "address": "Adresse Bluetooth"
        }
      },
      "test": { "…inchangé…": "" },
      "confirm": { "…inchangé…": "" }
    },
    "abort": {
      "already_configured": "Cette pompe à chaleur est déjà configurée.",
      "not_supported": "Cet appareil Bluetooth n'est pas une pompe à chaleur Aqua Temp.",
      "no_bluetooth_adapter": "Home Assistant ne dispose d'aucun adaptateur ou proxy Bluetooth fonctionnel. Branchez un adaptateur pris en charge (ou vérifiez l'intégration Bluetooth), puis réessayez. Sur les installations conteneur, vérifiez que bluez et /var/run/dbus sont disponibles."
    },
    "error": { "…inchangé…": "" }
  }
}
```

## 7. Testing strategy

- **No new unit tests are possible** for the flow itself (this repo has
  no HA test harness by design — spec 001 §7: HA-dependent modules are
  covered by `py_compile` + review + manual quickstart). The list
  builder is deliberately a **pure function over its inputs** so the
  Coder *may* add a stdlib-only test by injecting fake service-info
  objects (optional, recommended if trivial).
- Manual validation scenarios: `quickstart.md` (V1–V10).
- Regression gates: py_compile, `modbus.py` self-test, `test_modbus.py`,
  `test_reassembly.py` — unchanged code must stay green.

## 8. Implementation order

See `tasks.md` (constants → list builder → steps → strings/translations
→ README → gates → manual QA).