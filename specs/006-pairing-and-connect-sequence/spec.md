# Spec 006 — Pairing & connect sequence: vendor parity + honest failure UX

Status: **PLANNED** — built from `research.md` (vendor-app reverse of the
pairing gesture, the BLE session sequence, and the time-sync frame).
Scope guard: this spec does **not** redesign the config flow (002/004),
device resolution (005) or the register/protocol layer (001/003) — it adds
vendor-parity behaviours to the existing connect path and ships the
pairing/connect runbook. The Coder implements from this kit; the Reviewer
validates against the acceptance criteria.

## 0. The user question this spec answers

Production blocker: HA lists the heat pump (`BlueNRG-C0941C`,
08:06:A6:C0:94:1C, RSSI -67) but the connection test fails with
"Impossible de se connecter". The owner asks (a) whether a code/frame must
be sent before the PAC accepts a connection, and (b) how to put the PAC in
pairing mode when no explicit button exists.

The answers (research.md, proof refs there):

- **(a) NO.** The vendor app's session is
  `connect → discover → subscribe ff02 → read` — no bonding, no PIN, no
  unlock frame (research §2, §8). Nothing must be sent "before" the
  connection; the connection itself is refused for other reasons
  (single-connection link, host-side, or a first-provisioning gate in the
  module firmware — the one case static analysis cannot exclude).
- **(b) The gesture exists** — hold the panel button shown by the vendor
  wizard (likely the power button, [V]) for 5 s until the panel displays
  **"bLE" blinking 1×/s** — but it is the **WiFi-provisioning/config-mode**
  gesture, not a proven GATT gate (research §1). For the owner's BLE-only
  unit the quickstart's 2-minute experiment settles whether it matters at
  all.

Therefore this spec changes **no protocol behaviour that could "unlock"
anything** — there is nothing to unlock. It (1) aligns our client with the
vendor's exact session details, (2) gives the connection test the same
retry persistence the vendor app itself has, (3) extends discovery to the
vendor's own name filter, and (4) ships the runbook that ends the guessing.

## 1. User stories

- **US-1** As the pool owner, when the connection test fails, I want the
  test to retry the way the vendor app does, so transient refusals
  (phone releasing the link, stale BlueZ state) don't fail the flow.
- **US-2** As the pool owner, I want a written, ordered checklist telling
  me exactly what to do physically (kill the phone app, phone Bluetooth
  off, the 5-second "bLE" gesture, power-cycle, range), so I can resolve
  the failure without reverse-engineering.
- **US-3** As the pool owner whose unit advertises under a module name the
  vendor also scans for (`WT-…`), I want HA to list it the same way the
  vendor app does, so I am not blocked at discovery.
- **US-4** As the developer, I want the client to mirror the vendor's
  write mode (without response) and post-subscribe settle, so that when
  the link finally connects our behaviour differs from the working vendor
  session in zero observable ways.
- **US-5** As the integrator, I want the pairing-gesture documentation to
  distinguish PROVEN from SUPPOSED (button identity, whether "bLE" is
  required), so future debugging isn't misled by folklore.

## 2. Functional requirements

### FR-1 — Post-subscribe settle delay (vendor parity) [P0]

After `start_notify(ff02)` succeeds in `AquaTempBleClient.connect()`, wait
`const.SETTLE_AFTER_SUBSCRIBE` (= **2.0 s**) before the connect returns
and any request is sent. Vendor evidence: the 2-second Duration constant
sits in the session manager's subscribe→read path (research §2 step 4,
§7). Rationale: covers the CCCD-propagation race where the very first
base read is issued before the module routes notifications. Cheap (2 s
once per connect, and the coordinator connects at most once per poll
cycle).

### FR-2 — Write without response (vendor parity) [P0]

`_resolve_write_characteristic` currently prefers **with-response**
(`"write" in props`). The vendor writes **without response**
(research §2.1). New rule, in order:

1. `"write-without-response" in props` → `response=False`;
2. else `"write" in props` → `response=True`;
3. else → unchanged error (no usable write property).

Log the chosen mode at DEBUG (existing log line gains the decision).
Guard: the property check guarantees we only use a mode the
characteristic advertises — no behavioural risk for a spec-003-clean
setup.

### FR-3 — Connection-test retry budget (vendor parity) [P0]

In `config_flow._async_test`: `max_attempts=1` → **`2`**, and
`TEST_TIMEOUT = 40.0` → **`70.0`**. Budget arithmetic (logged comment):
2 × 20 s bleak-retry-connector-hardcoded connect attempts + backoff +
2 s settle (FR-1) + 8 s read + margin. Update the `TimeoutError` log
message and the progress step description so the user knows the probe can
take ~1 minute. Supersedes spec 005 FR-5's value (routing key unchanged,
all 005 logging branches preserved). Vendor evidence: the app retries
connects before giving up (research §2.2). Value: attempt 2 catches the
"phone released the link mid-attempt" and "stale BlueZ state cleared"
cases.

### FR-4 — Discovery filter parity: `wt-` prefix [P1]

`const.NAME_PREFIXES`: add `"wt-"` (vendor operating filter is
`BlueNRG-` + `WT-`, research §3). Affects: config-flow listing tier
match, tier-3 name-prefix resolution, standalone scan fallback. Keep
`prov_` (harmless; it is the provisioning-mode name of WiFi models).

### FR-5 — Runbook shipped (no code) [P1]

`quickstart.md` (this kit) is the user-facing procedure: the ordered
checklist (kill vendor app + phone Bluetooth off → retry → "bLE"
experiment → power-cycle → range → `bluetoothctl` differential). The
error-copy texts from specs 002/005 already carry the right advice
("close the vendor app — single connection"; "hold the panel button ~5 s
until the display shows bLE" for the not-visible case) — **no translation
changes are required by this spec.**

## 3. Non-functional requirements

- **NFR-1** Touched files (nothing else):
  `custom_components/aqua_temp_ble/const.py`,
  `…/aqua_client.py`,
  `…/config_flow.py`,
  plus tests under `tests/`.
- **NFR-2** No new frames are sent by the integration (no time-sync, no
  unlock — research §5/§8: none exists to send). The time-sync service is
  explicitly **out of scope** (clock layout still [V]; revisit in a later
  spec after one live logcat).
- **NFR-3** No change to the Modbus layer, registers, write guard, or
  entities (specs 001/003 own those).
- **NFR-4** Standalone mode (`tools/smoke_client.py`, tests) keeps
  working without Home Assistant (no new HA imports).
- **NFR-5** Durable artifacts in English; every changed constant carries a
  comment citing `research 006 §…`.
- **NFR-6** All existing tests stay green; new tests only ADD coverage.

## 4. Acceptance criteria

- **AC-1** `connect()` awaits `SETTLE_AFTER_SUBSCRIBE` after a successful
  `start_notify` and before returning (unit test: fake client, patched
  clock; assert ordering: notify → sleep → first request).
- **AC-2** With fake ff01 properties `["write-without-response", "write"]`
  the client writes with `response=False`; with `["write"]` only, with
  `response=True` (unit tests).
- **AC-3** The config-flow test performs up to 2 connect attempts within
  a 70 s budget; the budget comment and `TimeoutError` log cite the new
  arithmetic; the progress description mentions the ~1 min duration.
- **AC-4** A device advertising `WT-AB12` is listed by the config-flow
  name-prefix path (unit test on the tier function / discovery predicate).
- **AC-5** `python3 -m py_compile` clean on all touched files;
  `pytest tests/` fully green.
- **AC-6** `quickstart.md` exists with the six-step procedure and the
  [P]/[V] status of each step (delivered with this kit — Coder verifies
  presence only).

## 5. Risks & guards

| Risk | Guard |
|---|---|
| FR-2 changes write behaviour of an eventually-working link | Mode is property-gated; vendor parity is the evidence-based choice; DEBUG log names the mode |
| FR-3 doubles worst-case test time (70 s spinner) | Progress text warns; budget still bounded; user can cancel; attempt-2 only runs after attempt-1 failed |
| `wt-` prefix collides with foreign devices | Listing-tier only (no auto-config); a false positive appears in the "other devices" list at worst |
| Someone "fixes" pairing by sending a magic frame | NFR-2 forbids it; research §8 proves none exists |