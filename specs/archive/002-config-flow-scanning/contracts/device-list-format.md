# Contract — Device list format (002)

Status: **PLANNED** — normative for `_build_device_list` in
`config_flow.py`. The builder is a pure function of its inputs (HA
bluetooth history + configured unique ids): no I/O, no scans, no hidden
state. Sorting/labeling rules below are exact.

Confidence legend: **[P]** proven · **[D]** decision ·
**[A]/**[V]** as in the other files.

---

## 1. Inputs

| input | source | notes |
|---|---|---|
| `infos` | `bluetooth.async_discovered_service_info(hass, connectable=True)` | one entry per address, latest advertisement **[P]** |
| `configured` | `flow._async_current_ids()` (set of upper-cased unique ids) | exclusion set **[P]** |
| `NAME_PREFIXES`, `UUID_WRITE`, `UUID_NOTIFY`, `MAX_OTHER_DEVICES` | `const.py` | `UUID_*` compared lower-cased against `info.service_uuids` |

## 2. Output

```
labels: dict[address -> label]      # insertion-ordered; index == dropdown order
counts: {"recognized": int, "total": int}
```

- `total` = number of listed devices (after exclusion, after cap).
- `recognized` = number of tier-1 devices (after exclusion).
- Both feed the `user` menu description placeholders.

## 3. Classification (per info)

1. **Skip** if `info.address.upper() in configured`.
2. `has_real_name = info.name != info.address` — HA synthesizes the name
   as `local_name or device.name or address` **[P]**, so equality means
   "no name anywhere".
3. `recognized = info.name.lower().startswith(NAME_PREFIXES)`
   **or** `UUID_WRITE in uuids or UUID_NOTIFY in uuids`
   (uuids lower-cased) — the UUID test is opportunistic **[V]** and
   never hides a device.
4. `rssi_key = info.rssi if isinstance(info.rssi, int) else -200`
   (defensive sort key, bleak types RSSI as optional **[A]**).

## 4. Ordering

1. All recognized devices first, sorted by `rssi_key` descending.
2. Then all other devices, sorted by `rssi_key` descending.
3. Truncate the **other** tier at `MAX_OTHER_DEVICES` (30) **[D]** —
   recognized devices are never capped.
4. (Recognition never depends on sort position; tiers are disjoint by
   construction.)

Insertion order of `labels` is the dropdown order **[A]** (research §4).

## 5. Labels (exact format)

| case | label |
|---|---|
| named device | `f"{name} — {address} — RSSI {rssi}"` |
| unnamed device | `f"Unnamed device — {address} — RSSI {rssi}"` |
| missing RSSI | the `RSSI` part shows whatever the history holds (`None` allowed as text) |

Examples:

```
BlueNRG-7A — AA:BB:CC:DD:EE:FF — RSSI -62
Unnamed device — 11:22:33:44:55:66 — RSSI -71
```

`name` here is `info.name` verbatim (HA's synthesized value), NOT
re-derived. The em dash separator matches the existing label style of
the current flow.

## 6. Worked examples

Inputs (configured = `{AA:…:01}`):

| address | name | rssi | service_uuids | tier |
|---|---|---|---|---|
| AA:…:01 | BlueNRG-1 | -50 | ff01,ff02 | excluded (configured) |
| BB:…:02 | BlueNRG-7A | -62 | — | 1 (name prefix) |
| CC:…:03 | CC:…:03 | -58 | ff01 | 1 (UUID signal, unnamed) |
| DD:…:04 | DD:…:04 | -90 | — | 2 (unnamed, RSSI last) |
| EE:…:05 | TV Salon | -75 | — | 2 |

Output order: `CC:…:03` (-58) → `BB:…:02` (-62) → `EE:…:05` (-75) →
`DD:…:04` (-90); counts: recognized=2, total=4.

## 7. Non-goals

- No staleness column, no "last seen", no manufacturer-data decoding
  (no known vendor ID **[V]**), no non-connectable devices, no
  configured-device display.