# Research 005 — Device resolution & the silent connection-test failure, verified in Home Assistant source

Status: **VERIFIED** — every HA API this spec relies on was verified against the
exact sources at our minimum version **tag 2024.12.0** AND at **tag 2026.10.1**
(the era of the affected production instance), plus their pinned libraries:
HA 2024.12.0 pins `habluetooth==3.6.0`, `bleak-retry-connector==3.6.0`,
`bleak==0.22.3`; HA 2026.10.1 pins `habluetooth==7.1.2`,
`bleak-retry-connector==4.7.1`, `bleak==3.0.2`
(`homeassistant/package_constraints.txt` + `components/bluetooth/manifest.json`
at both tags — fetched and read 2026-10-11). Sources were fetched and read
during spec writing; file/line references below.

Legend: **[P] PROVEN** (read in the sources, reference below) ·
**[H] HYPOTHESIS** (plausible, not verifiable statically) ·
**[D] DECISION** (design choice) · **[V] TO-VERIFY** (needs the live
instance / real unit; checklist in `quickstart.md`).

---

## 1. The incident, decoded

After the spec-004 fix (commit `5aa887b`, verified working on the production
instance), the config flow reaches the device-selection screen and the heat
pump is listed:

```
BlueNRG-C0941C — 08:06:A6:C0:94:1C — RSSI -67
```

The connection test then fails with the `cannot_connect` error
("Impossible de se connecter à l'appareil" / "Failed to connect to the
device…") — and **no log line at all** appears for the failure.

This research explains, from source:

1. why a whole class of test failures is **completely silent** today
   (§4 — the silent-path proof);
2. why the **fallback resolution steps never do what they appear to do**
   inside Home Assistant (§3.1 — the bleak monkeypatch);
3. what the **real contract** of `async_ble_device_from_address`,
   `async_discovered_service_info` and the connection path is (§2);
4. why the 20 s test budget **cannot ever complete** a slow connect (§5 —
   the budget arithmetic);
5. where the device list and the connect path read **different data**
   (§2.3 — the one genuine incoherence).

---

## 2. Verified HA API facts

### 2.1 `async_ble_device_from_address(hass, address, connectable=True)` — the exact contract **[P]**

- HA layer: `homeassistant/components/bluetooth/api.py` —
  `async_ble_device_from_address(hass, address, connectable=True)` at
  **2024.12.0 L86-90** and **2026.10.1 L111-115**. It is a `@callback` that
  delegates to `_get_manager(hass).async_ble_device_from_address(address,
  connectable)`.
- Implementation (habluetooth **3.6.0** `manager.py` L633-641; habluetooth
  **7.1.2** `manager.py` L1484-1492 — identical):

```python
def async_ble_device_from_address(self, address: str, connectable: bool) -> BLEDevice | None:
    """Return the BLEDevice if present."""
    histories = self._connectable_history if connectable else self._all_history
    if history := histories.get(address):
        return history.device
    return None
```

- **`connectable=True` filters on the capability of the SCANNER that saw the
  advertisement, not on anything about the device.** Each scanner carries a
  `connectable` flag; every advertisement it reports is tagged with it
  (habluetooth 3.6.0 `base_scanner.py` L436:
  `service_info.connectable = self.connectable`; 7.1.2 `base_scanner.py`
  L718). The manager files advertisements tagged connectable into
  `_connectable_history`, everything into `_all_history` (3.6.0
  `manager.py` L511-519; 7.1.2 `manager.py` L1277-1282).
- The **local BlueZ scanner is always connectable**: `HaScanner` sets
  `self.connectable = True` (3.6.0 `scanner.py` L179; 7.1.2
  `scanner_bleak.py` L253). Remote scanners (ESPHome proxies, Shelly) are
  connectable iff they support making GATT connections; the manager splits
  registered scanners into `_connectable_scanners` /
  `_non_connectable_scanners` by that flag (3.6.0 `manager.py` L669-690).
- **Answering the brief's Q1 sub-question directly**: if the device is known
  but was only seen by non-connectable scanners, `connectable=True` returns
  `None` and `connectable=False` returns the device. It does **not** matter
  how strong the signal is; only which scanners saw it.
- **Retention (both versions)**: a scanner keeps an address in its
  `discovered_addresses` (its `_previous_service_info` dict — 3.6.0
  `base_scanner.py` L344-346, 7.1.2 L826-828) for
  `CONNECTABLE_FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS = 195` s after
  the **last advertisement** (`_async_expire_devices`, 3.6.0
  `base_scanner.py` L217-226+L317-326; 7.1.2 L130+L726-735;
  `const.py` L34/L36 in both). The manager's `_connectable_history` is
  pruned only by a watchdog that runs every
  `UNAVAILABLE_TRACK_SECONDS = 300` s (const.py L52/L104 in both) and pops
  addresses absent from all connectable scanners' `discovered_addresses`
  (`_async_check_unavailable`, 3.6.0 `manager.py` L320-350; 7.1.2
  `manager.py` L532+). Net effect: **a device can sit in the manager's
  connectable history for up to ~195 s + 300 s ≈ 8 minutes after its last
  advertisement**, while the scanners' own data drops it after 195 s.
  (7.1.2 has a promotion path where a non-connectable advertisement is
  re-flagged connectable if a registered connectable scanner still holds the
  address — `manager.py` L1355-1370 — which does not change these bounds.)

### 2.2 `async_discovered_service_info(hass, connectable=True)` — the list source **[P]**

`api.py` 2024.12.0 L70-76 / 2026.10.1 L95-101 → manager method (3.6.0
`manager.py` L647-652; 7.1.2 L1498-1503):

```python
def async_discovered_service_info(self, connectable: bool) -> Iterable[BluetoothServiceInfoBleak]:
    """Return all the discovered services info."""
    histories = self._connectable_history if connectable else self._all_history
    return histories.values()
```

**The config-flow device list and `async_ble_device_from_address(
connectable=True)` read the SAME dict** (`_connectable_history`, keyed by
address). If a device is listed, a same-address lookup must also find it —
there is no incoherence between our list builder (`_build_device_list` →
`async_discovered_service_info(connectable=True)`) and our resolution step 1.
The brief's Q2 premise ("the list finds it but the lookup does not") is
therefore **disproven as a lookup-level incoherence**.

### 2.3 The one REAL incoherence: the list reads manager history, the connect path reads scanner data **[P]**

The connection path does **not** use the `BLEDevice` we resolve, only its
address (§3.2). At connect time the HA client wrapper re-resolves the device
from the **scanners' current data**:

- habluetooth 3.6.0 `wrappers.py` L349-398
  (`_async_get_best_available_backend_and_device`) and 7.1.2 `wrappers.py`
  L600-688 call
  `manager.async_scanner_devices_by_address(address, True)`, which iterates
  the **connectable scanners'** `discovered_addresses` / current
  advertisement records (3.6.0 `manager.py` L495-516-style scanner walk;
  7.1.2 `manager.py` L495-516) — *not* the manager's `_connectable_history`.
- If no connectable scanner currently holds the address, the wrapper raises
  `BleakError("No backend with an available connection slot that can reach
  address … was found")` (3.6.0 `wrappers.py` L396; 7.1.2 L670-688, which
  appends `async_address_reachability_diagnostics` output — an API that
  exists only at ≥2026.x and is **not** used by our code).

So the temporal gap proven in §2.1 produces exactly one user-visible
incoherence: **the device can be listed (manager history, up to ~8 min
stale) while the connect path finds no live scanner path ("No backend…
was found")**. That failure is *loud* in our integration: the BleakError
surfaces from `establish_connection` as a terminal `BleakConnectionError`,
which `aqua_client.connect()` logs at ERROR before raising
`AquaTempConnectionError` (repo `aqua_client.py`, `connect()`). **It cannot
be the user's silent failure** — but it is worth logging the resolution
provenance so the case is diagnosable (see plan).

### 2.4 `async_scanner_count`, `async_rediscover_address` — unchanged facts **[P]**

Re-confirmed present and same-signature at both tags (api.py 2024.12.0
L54-58/L171+; 2026.10.1). The 2026.10.1 api.py additionally exposes
`async_request_active_scan` (L~205+, "Intended for config-flow discovery…")
and `async_address_reachability_diagnostics` (L116-125) — **both are
dev-branch-era APIs that do not exist at 2024.12.0 and must NOT be used**
(NFR carried from spec 002).

---

## 3. The bleak monkeypatch — the fact that invalidates the "raw scan" hypothesis

### 3.1 Inside Home Assistant, `bleak.BleakScanner` and `bleak.BleakClient` are globally replaced **[P]**

`BluetoothManager.async_setup()` calls `install_multiple_bleak_catcher()`
(habluetooth 3.6.0 `manager.py` L254-260, call at L259; 7.1.2 `manager.py`
L445-453, call at L453). That function (3.6.0 `usage.py` L16-31; 7.1.2
`usage.py` L23-30) does:

```python
bleak.BleakScanner = HaBleakScannerWrapper
bleak.BleakClient = HaBleakClientWrapper
bleak_retry_connector.BleakClient = HaBleakClientWrapper   # 3.6.0 also swaps BleakClientWithServiceCache
```

The bluetooth integration's `async_setup` awaits `manager.async_setup()`
(HA core 2024.12.0 `components/bluetooth/__init__.py` L~200-215), so the
patch is installed before any custom component can run a config flow. HA's
own bluetooth component contains **no** monkeypatch code of its own at
either tag (whole `__init__.py` read; greps for
monkeypatch/HaBleak/wrapper across the fetched component files: zero hits).

Consequences for our `_resolve_device()` steps 2 and 3 (repo
`aqua_client.py`), **all [P]**:

- `BleakScanner.find_device_by_address(address, timeout=5.0)` becomes
  `HaBleakScannerWrapper.find_device_by_address`, a **classmethod cache
  lookup** (3.6.0 `wrappers.py` L68-85; 7.1.2 L105-112):

```python
manager.async_ble_device_from_address(device_identifier, True) \
    or manager.async_ble_device_from_address(device_identifier, False)
```

  → connectable history, then non-connectable history. **The 5.0 s timeout
  is ignored; no scan is started.** (This is HA's own sanctioned
  connectable→non-connectable order — the answer to the brief's Q5.)
- `BleakScanner.discover(timeout=5.0)` becomes
  `HaBleakScannerWrapper.discover` → `manager.async_discovered_devices(True)`
  (3.6.0) / `async_discovered_service_info(True)` devices (7.1.2
  `wrappers.py` L135-160) — again a **cache read; timeout ignored**. Our
  name-prefix filter then runs over the *cached* connectable devices.

**Conclusion:** inside Home Assistant, our "raw bleak fallback" never
starts a scan, never takes 5 s, and cannot conflict with the HA scanner.
The brief's hypothesis that step 2 "does a raw BLE scan outside the HA
manager" is **disproven for the HA path** — the steps are merely additional
cache lookups whose logging lies about what they do ("scanning by name
prefix…"). The raw scans only ever run in **standalone mode** (`hass is
None`: tools/smoke_client.py, unit tests), where bleak is unpatched.

This does not make the current code *correct*: it relies on a global
monkeypatch (fragile, implicit), its log messages are false inside HA, and
its behavior is indistinguishable from a real scan for a reader. The fix
makes the HA path explicit (§7, plan.md).

### 3.2 `establish_connection()` — what it accepts and what really connects **[P]**

bleak-retry-connector **3.6.0** (`__init__.py` L297-307) and **4.7.1**
(L451-462) — signature:

```python
async def establish_connection(
    client_class: type[AnyBleakClient],
    device: BLEDevice,               # typed BLEDevice — NOT an address string
    name: str,
    disconnected_callback=None,
    max_attempts: int = MAX_CONNECT_ATTEMPTS,   # 4 (we pass 2 today)
    cached_services=None,
    ble_device_callback=None,
    use_services_cache=True,
    pair: bool = False,               # 4.7.1 only
    **kwargs,                         # forwarded to the client CONSTRUCTOR
) -> AnyBleakClient
```

- **A plain address string is NOT a valid input.** Besides the type, brc
  calls `get_connected_devices(device)` / `wait_for_disconnect(device)`
  *before and between attempts* (3.6.0 `__init__.py` L297+ L363-365;
  `bluez.py` L276-300, L423-439), whose Linux implementations read
  `device.details["path"]` — a string would raise `AttributeError` inside
  brc and surface as a logged ERROR. Keep passing the resolved `BLEDevice`.
- **The constructor `timeout` kwarg we pass (`const.CONNECT_TIMEOUT` = 15 s)
  is DEAD for the connect attempt**: brc calls
  `await client.connect(timeout=BLEAK_TIMEOUT, dangerous_use_bleak_cache=…)`
  with `BLEAK_TIMEOUT = 20.0` hardcoded (3.6.0 `__init__.py` L97 + L363-365;
  4.7.1 L105 + L544-546), wrapped in a `BLEAK_SAFETY_TIMEOUT = 60.0`
  (L108/L116). bleak's BlueZ backend reads
  `timeout = kwargs.get("timeout", self._timeout)` — the connect-call kwarg
  wins over the constructor default (bleak 0.22.3
  `backends/bluezdbus/client.py` L132; bleak 3.0.2 same file L132). The
  constructor timeout only bounds bleak's *implicit discovery* when a
  client is built from a bare address (bleak 0.22.3
  `backends/client.py` L39-53) — which never happens on our path.
- **Inside HA, the `BLEDevice` we pass is used only as an address carrier.**
  `bleak.BleakClient` is `HaBleakClientWrapper` (§3.1), whose `__init__`
  stores *only* `self.__address` from the device (3.6.0 `wrappers.py`
  L204-278; 7.1.2 L296-371) and whose `connect()`:
  1. re-resolves the best device+backend via
     `manager.async_scanner_devices_by_address(address, True)` (§2.3),
  2. constructs the real backend (`get_platform_client_backend_type()` =
     BlueZ locally; a proxy connector's client class for remote scanners —
     slot-checked, RSSI-sorted, failure-penalized),
  3. calls `super().connect(**kwargs)` → `self._backend.connect(**kwargs)`
     (bleak 0.22.3 `__init__.py` L605-609; 3.0.2 L651-656) with brc's
     `timeout=20.0`,
  4. on ANY failure — including `asyncio.CancelledError` — runs its
     `finally`: clears the backend and releases the connection slot for
     local adapters (3.6.0 `wrappers.py` L331-347; 7.1.2 L505-522 —
     explicitly commented for BaseException).
- **What if we passed a `BLEDevice` from a raw scan (non-HA)?** On Linux
  (our production platform), a raw `BleakScanner` produces devices with
  `details = {"props": …, "path": "/org/bluez/hciX/dev_…"}` — the same
  shape the BlueZ backend requires (`details["path"]` access at bleak
  0.22.3 `bluezdbus/client.py` L81 / 3.0.2 L79) — so the connect works;
  inside HA the wrapper ignores the device anyway (address only). There is
  **no routing metadata loss** on Linux: the answer to the brief's Q3
  sub-question is that a raw-scan device does *not* break
  `establish_connection` on Linux; the "HA BLEDevice with scanner routing
  metadata" concern applies to non-Linux platforms and to *proxy* devices,
  which inside HA are routed by the wrapper regardless of which BLEDevice
  we resolved.
- 4.7.1 only: `establish_connection` passes `_is_retry_client=True` to the
  constructor (L522) — the wrapper warns
  ("BleakClient.connect() called without bleak-retry-connector…") when a
  client is connected outside brc. Our code always goes through brc inside
  HA; the standalone raw path uses bleak's own client and never triggers
  the wrapper.

### 3.3 What brc logs on failure **[P]**

Every per-attempt failure handler in `establish_connection` logs **at DEBUG
only** (`if debug_enabled:` — 3.6.0 L367-455; 4.7.1 L555-655). At default
log level, a connect attempt that hangs and gets cancelled produces **zero
brc log lines**. The terminal exceptions are typed
(`BleakNotFoundError`, `BleakAbortedError`,
`BleakOutOfConnectionSlotsError`, `BleakConnectionError`) — and those, if
they surface, ARE logged by our `aqua_client.connect()` at ERROR. The
distinction that matters for the incident: **terminal failures are loud in
our integration; cancellations are silent** (§4).

---

## 4. The silent-path proof — why the user sees NO log

### 4.1 Which failure paths log, and which do not **[P — repo code]**

`config_flow._async_test()` (repo, commit 5aa887b) maps every failure to a
UI key with **no logging**:

```python
except AquaTempConnectionError: self._test_error = "cannot_connect"   # no log
except AquaTempTimeoutError:    self._test_error = "no_response"       # no log
except AquaTempResponseError:   self._test_error = "crc_error"         # no log
except TimeoutError:            self._test_error = "cannot_connect"   # no log
except Exception:               _LOGGER.exception(...); self._test_error = "cannot_connect"
```

Meanwhile, in `aqua_client.py`:

- `_resolve_device()` step 3 logs a WARNING **when it is reached**
  ("address … not found directly; scanning by name prefix …"), and steps
  2's failure path logs at WARNING only on *exceptions*;
- `connect()` logs **ERROR for every terminal failure** (resolution is not
  its job — a `None` device raises `AquaTempConnectionError("no Aqua Temp
  device found for address …")` **unlogged** from the top of `connect()`).
- the resolution steps 1-2 are **instant cache lookups** inside HA (§3.1),
  so the test reaches `establish_connection` within milliseconds of the
  pick.

Given the user's observation (failure message shown, **no log at all**),
the candidate paths are exactly:

| Path | Would leave a log? | Consistent with "no logs"? |
|---|---|---|
| Resolution `None` (all cache lookups miss) | YES — step-3 WARNING always fires before returning None | **excluded** |
| Terminal `establish_connection` failure (any typed error, incl. "No backend…") | YES — our ERROR "connect to … failed: …" | **excluded** |
| Post-connect setup failure (ff02/ff01) | YES — our ERROR "post-connect setup …" | **excluded** |
| Generic unexpected exception | YES — `_LOGGER.exception` | **excluded** |
| Standalone raw-client path (brc missing) | YES — per-attempt WARNINGs; brc is always present in HA anyway | **excluded** |
| **`asyncio.timeout(TEST_TIMEOUT)` budget expiry mid-connect / mid-read** | **NO — nothing logs anywhere** | **the only consistent path** |

### 4.2 Why budget expiry is completely silent **[P]**

1. `_async_test` wraps the whole client lifecycle in
   `async with asyncio.timeout(TEST_TIMEOUT)` (20.0 s, `config_flow.py`).
   When the budget expires, asyncio cancels the inner task; the
   `asyncio.timeout` context manager converts **its own cancellation** into
   `TimeoutError` at exit (stdlib semantics, Python ≥3.11 — HA 2026.10
   runs Python 3.13).
2. `asyncio.CancelledError` is a **`BaseException`** on Python ≥3.8 — it
   is not caught by brc's handlers (`except asyncio.TimeoutError`,
   `KeyError`, `BrokenPipeError`, `EOFError`,
   `(AttributeError, BleakError)` — 3.6.0 L409-490; 4.7.1 L590-655), nor by
   our `except Exception` in `aqua_client.connect()` (L~185). Nested
   `asyncio_timeout` contexts in brc/bleak re-raise foreign cancellation
   rather than converting it.
3. So the cancellation unwinds through bleak, brc, and our client **with
   no except clause anywhere logging it** (the HA wrapper's `finally`
   releases the slot silently — 3.6.0 `wrappers.py` L331-347; 7.1.2
   L505-522), `_async_test` catches the converted `TimeoutError`, stores
   `"cannot_connect"`, and the user sees only the generic UI message.

**This is a complete, source-proven explanation of the incident's "no
logs" property.** What it does not prove is *why* the connect did not
finish within 20 s — see §6.

### 4.3 Post-fix forensic note **[H]**

On a budget cancellation mid-connect, our `finally` never runs
(`client.disconnect()` is inside the `try` that starts *after* `connect()`
returns), so a D-Bus link that was established in the final milliseconds
could linger in BlueZ. brc's next `establish_connection` would detect and
reuse-or-disconnect an already-connected device (`get_connected_devices`
swap, brc 3.6.0 L305-309 / 4.7.1 L535-539), and the unit's single-connection
limit makes a stale link a plausible contributor to *subsequent* failed
attempts. Unprovable remotely; the new logging (plan FR-2) will expose it
if it happens (look for the connect path being reused).

---

## 5. The budget arithmetic — why the test cannot win a slow connect **[P]**

Current constants (repo): `TEST_TIMEOUT = 20.0` (whole test budget,
`config_flow.py`), `CONNECT_TIMEOUT = 15.0` (passed as constructor kwarg,
`const.py`), `REQUEST_TIMEOUT = 8.0` (read deadline), and
`max_attempts=2` in the flow's `establish_connection` call.

Actual timings inside HA (all [P] from §2-§3):

| Stage | Effective bound | Source |
|---|---|---|
| Resolution (all 3 steps) | ~0 s (cache lookups) | §3.1 |
| Wrapper backend resolution + slot alloc | ~0-1 s | §2.3 |
| One brc connect attempt | **up to 20.0 s** (`BLEAK_TIMEOUT`, hardcoded) | §3.2 |
| `start_notify` + char lookup | ~0.5-2 s | bleak |
| Register read | up to 8.0 s (`REQUEST_TIMEOUT`) | repo |
| **One full honest attempt** | **≈ 21-31 s** | — |
| Two attempts (today's `max_attempts=2`) | up to ~40-50 s + backoffs | brc |

- `TEST_TIMEOUT = 20.0` is **smaller than a single legitimate connect
  attempt**: any connect that does not succeed quickly is *always* cut off
  mid-flight by the budget and reported as a silent `cannot_connect`.
  Even a fully successful-but-slow test (connect at 19 s, read at 27 s)
  would fail — the user would never know the unit actually answered.
- `CONNECT_TIMEOUT = 15.0` is **dead code inside HA** (brc overrides the
  connect timeout to 20.0, §3.2). It only bounds the standalone raw path
  (`_connect_with_raw_client` passes it to `BleakClient.connect`
  explicitly).
- **RSSI −67 (brief's Q7)**: the list's RSSI is the last advertisement's
  value (spec 002 §2.2); it plays no role in any connect decision, and
  −67 dBm is a healthy advertising signal. There is **no proven link**
  between the RSSI and the failure. The proven mechanism is the budget
  arithmetic above; the open question is only *why the connect stalls*
  (§6). A unit that is connected to the vendor app — the documented
  single-connection hazard (FINDINGS.md §3, strings.json `cannot_connect`
  text) — accepts no second link regardless of signal strength **[H]**.

---

## 6. Root cause and ranked hypotheses

**Proven root cause (the defect that makes the failure undiagnosable):**
the test maps every failure to a UI key without logging, and the 20 s
budget silently cancels in-flight connects; combined with the hardcoded
20 s per-attempt connect timeout inside brc, any slow connect is *always*
reported as a generic, log-less `cannot_connect`.

**Proven mechanism, unproven trigger.** Ranked hypotheses for why the
connect did not complete within the budget:

1. **[H1 — most likely] The unit is not accepting a connection** (vendor
   app holds the single BLE link, or the unit is asleep/off/pairing-mode
   exited) → BlueZ Connect hangs until the budget kills it. Supported by:
   the single-connection design (FINDINGS.md §3); the spec-004-era logs
   showing `BLE connection … lost` warnings, which only fire after a
   successful Connected→False transition — i.e. the unit *did* accept
   connections in earlier attempts and something now holds it; the
   mechanism is fully source-proven (§4-§5). **[H]**
2. **[H2] The connect succeeded late and the read/notify was cut off** —
   same silent budget path (T2 of the brief). Cannot be distinguished
   from H1 without the logging fix. **[H]**
3. **[H3] Stale BlueZ link from a previously cancelled attempt** (§4.3)
   making the unit appear busy. **[H]**
4. **Excluded** (would have logged): resolution miss (step-3 WARNING),
   terminal connect errors incl. "No backend…" (our ERROR), post-connect
   setup errors (our ERROR), standalone path (brc always present in HA).

**The fix ships the diagnostic first**: FR-1/FR-2 (logging) guarantee the
next failed test names the failing stage, the exception chain, and the
budget state — converting every remaining [H] into a one-run [V].

---

## 7. Answers to the brief's seven questions (summary)

1. **`async_ble_device_from_address` contract**: manager-history dict
   lookup; `connectable=True` = "seen by a scanner that can make GATT
   connections" (scanner property; local HaScanner always True);
   connectable-only-seen devices return `None` with `connectable=True` and
   the device with `False`; retention up to ~195 s (scanner) + 300 s
   (manager watchdog). Identical at 2024.12.0 and 2026.10.1. **[P]** (§2.1)
2. **List/lookup incoherence**: none at the lookup level — both read
   `_connectable_history`. The genuine gap is list (manager history) vs
   connect path (scanner data, 195 s) → loud "No backend…" failure, not
   the user's silent one. **[P]** (§2.3)
3. **`establish_connection` input**: a `BLEDevice` (string breaks brc's
   D-Bus helpers); inside HA the wrapper keeps only the address and
   re-resolves at connect time; raw-scan BLEDevices work on Linux (same
   `details["path"]` shape); our `timeout=15` constructor kwarg is dead —
   brc hardcodes 20 s per attempt. **[P]** (§3.2)
4. **Raw fallback in HA**: it never runs raw scans today — bleak is
   monkeypatched (cache lookups, timeouts ignored). Keep raw scans for
   standalone only; make the HA path explicit HA-API calls with truthful
   logging. **[P]** (§3.1)
5. **connectable=True vs False**: True-first with False-fallback is HA's
   own order (`HaBleakScannerWrapper.find_device_by_address`); keep it,
   log when the fallback fires. **[P]** (§3.1)
6. **Error message genericity**: split `cannot_connect` into
   `cannot_connect` (connect refused/failed, incl. budget expiry — advice
   stays "close the vendor app / range") and a new **`not_found`** key for
   resolution misses ("unit no longer visible — pairing mode, rescan").
   Every branch logs with the real exception chain. **[D]** (spec FR-4)
7. **RSSI −67 link**: RSSI is display-only; no proven link. The proven
   defect is the budget arithmetic (20 s budget < 20 s single attempt +
   notify + 8 s read; 15 s CONNECT_TIMEOUT dead). Whether the connect
   stalls because of the vendor app (H1), late connect (H2), or a stale
   link (H3) is **[H]** until the logging fix runs. (§5-§6)

---

## 8. What could NOT be verified (needs the live instance / unit)

- **[V-1]** Which of H1/H2/H3 actually occurs on the next failed test —
  the new log lines decide (quickstart decision table).
- **[V-2]** Whether the unit stops advertising while connected to the
  vendor app (would explain a listed-but-unconnectable state and, past
  195 s, the §2.3 "No backend…" path).
- **[V-3]** Exact habluetooth/bleak/bleak-retry-connector versions on the
  production instance — inferred 2026.9/2026.10 era from spec-004
  forensics (`data_entry_flow.py:411` + runner format); the contract
  facts are identical across both pinned version sets, so the fix does
  not depend on the exact patch version. **[P at both extremes]**
- **[V-4]** Frontend rendering of the new `not_found` error key on the
  device/manual forms (same mechanism as existing keys — low risk).
- **[V-5]** Whether a 40 s progress spinner is acceptable UX on the
  owner's frontend (matter-style long progress steps exist as precedent;
  the typical successful test is expected ≪ 40 s).

## 9. Sources read (method)

Fetched and read on 2026-10-11 (all via raw.githubusercontent.com +
GitHub trees API; cached under /tmp/ha-src during spec writing):

- HA core tag **2024.12.0**: `components/bluetooth/{__init__.py, api.py,
  manager.py}`, `homeassistant/package_constraints.txt`;
  tag **2026.10.1**: same files (+ `api.py`'s new dev-only APIs noted in
  §2.4, deliberately unused).
- **habluetooth v3.6.0** (pin of HA 2024.12.0): `manager.py`,
  `base_scanner.py`, `scanner.py`, `wrappers.py`, `usage.py`,
  `central_manager.py`, `const.py`, `models.py`.
- **habluetooth v7.1.2** (pin of HA 2026.10.1): `manager.py`,
  `base_scanner.py`, `scanner_bleak.py`, `scanner.py`, `wrappers.py`,
  `usage.py`, `client_mgmt.py` (architecture survey), `const.py`,
  `models.py`.
- **bleak-retry-connector v3.6.0** and **v4.7.1**: `__init__.py`
  (`establish_connection` in full), `bluez.py` (`wait_for_disconnect`,
  `get_connected_devices`, `get_device`).
- **bleak v0.22.3** and **v3.0.2**: `__init__.py` (BleakClient
  factory/delegation), `backends/client.py` (constructor timeout),
  `backends/bluezdbus/client.py` (constructor `details["path"]`, connect
  timeout resolution, internal discovery-scan branch).
- This repo at `5aa887b` (clean tree): `config_flow.py`, `aqua_client.py`,
  `const.py`, `coordinator.py`, `strings.json`,
  `translations/{en,fr}.json`, `FINDINGS.md` §3, `tests/
  test_config_flow_walkthrough.py` (FakeClient fault-injection model),
  `specs/002` (esp. research.md §2), `specs/004` (research.md, spec.md).
- Python ≥3.11 stdlib semantics relied on (documented, not re-derived):
  `asyncio.timeout` converts its own cancellation to `TimeoutError`;
  `CancelledError` inherits `BaseException`.