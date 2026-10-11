# Spec 011 research — Android vs BlueZ: why the vendor app's writes are answered and ours are silently ignored

Date: 2026-10-11
Evidence base: the production connection-test log of the census/re-send
build (lineage of commit 0b16e0a) against `08:06:A6:C0:94:1C`
(BlueNRG-C0941C); FINDINGS.md; specs 006/008/009/010 (all tagged there);
the shipped code @ 0b16e0a (`aqua_client.py`, `config_flow.py`,
`const.py`, `modbus.py`); external sources quoted with URLs in §14.

Premises inherited from the task brief as ESTABLISHED FACTS (not
re-derived here — the brief forbids re-discovering them):

- **F1** The vendor app on the owner's phone displays live inlet and
  outlet temperatures against THIS unit. The unit's FC03 request path is
  therefore functional for the app's session.
- **F2** The app writes **ff03**, with `WriteType.withoutResponse`, the
  frame `63 03 00 07 00 2D 3C 54` byte-for-byte ours, sequence
  subscribe → 2 s → write, no handshake, no requestMtu (spec 010
  research, disassembly proof).
- **F3** Our integration does exactly the same (ff03, without-response,
  same frame, subscribe → settle 2 s → write) and receives total
  silence.

Status legend: **[P]** PROVEN (verifiable from the cited source or by
arithmetic on logged data), **[V]** TO-VERIFY (one named experiment
decides), **[A]** ASSUMED (reasonable default, cheap to correct),
**[D]** DECISION.

---

## 0. TL;DR

1. The single mechanism that reconciles F1-F3 is **the ATT Write
   Command's total lack of an error channel**. Core Spec 5.4 Part F
   §3.4.9 lists `ATT_WRITE_CMD` with successful-response PDU **"none"**
   and `ATT_ERROR_RSP` allowed **"No"** [P — spec, §14.1]. A GATT server
   that refuses our write — for security, permissions, handle, or any
   reason — has **no legal way to tell us**. It drops the PDU and says
   nothing. Punchthrough documents the exact production pattern:
   servers "may technically allow write without response but will
   **silently discard packets unless bonding or encryption is in
   place**" [P — §14.10].
2. Android and BlueZ differ precisely on the state that gates such a
   refusal: **link encryption / bond**. Android's stack bonds when the
   peripheral's security demands it and **automatically starts encrypted
   communication on reconnect for bonded devices** [P — §14.11]. A
   headless BlueZ has **no pairing agent by default** — the official
   D-Bus documentation states `Pair()` "will fail" when "there is no
   application agent and also no default agent present" [P — §14.3] —
   and nothing ever triggers pairing on our path, because our failing
   operation is a Write Command, which produces **no error to react
   to**.
3. Ranked answers to the brief's eight questions (§3-§10): the
   bonding/security gate is the **only hypothesis consistent with every
   proven fact**; the Write-Command silence is the amplifier that hides
   it; GATT-cache/handle mismatch is LOW; MTU is excluded by
   arithmetic; connection parameters are excluded by the 8 s window; a
   connection-level accept list is DISPROVEN by the working link; and
   `withoutResponse` is honored identically by both stacks (same ATT
   opcode 0x52) — the difference is not what we send, it is **what state
   the link is in when we send it**.
4. One code-only experiment splits the world (§12 X1): **send the SAME
   base-read frame as a Write Request (`response=True`)**. If the unit
   gates ff03 value writes on encryption, the server MUST answer the
   acknowledged operation with ATT error `0x05 Insufficient
   Authentication` or `0x0F Insufficient Encryption` [P — §14.14] —
   surfacing as a bleak exception **inside our own HA log, no terminal
   needed**. One pairing attempt afterwards (X2) re-tests the
   hypothesis end-to-end: pair → re-send → answer = case closed.
5. The phone-only decisive capture (E5, Android HCI snoop) additionally
   shows whether the vendor app's session runs on an **encrypted link**
   (SMP pairing PDUs, `Encryption Change` HCI events) — the direct,
   vendor-side proof of the asymmetry [V — not yet captured].

---

## 1. The evidence this spec must explain

| # | Fact | Status |
|---|---|---|
| E-1 | Production: `received 16 frame(s) / 1584 bytes; blocks seen: 1001x12, 1046x4; first t+0.4s, last t+7.9s; none for start 7`. 1584 = 16 × 99 exactly — every byte reassembled into complete CRC-valid block pushes; **zero bytes** left for an FC03 answer, a Modbus exception frame, or garbage | [P — production log] |
| E-2 | A standard Modbus slave MUST answer a valid FC03 request or return an exception frame. Total silence means the request bytes **never reached the Modbus engine** — not "answered but mangled" | [P — Modbus semantics, research 009 §0.2] |
| E-3 | The bounded re-send (t+0/2/4/6 s, 4 × 8 bytes) changed nothing. A random-loss/collision model would be statistically implausible across 4 attempts in a 0.67 s-cadence push stream — the drop is **deterministic**, not timing-related | [P — arithmetic; A — inference] |
| E-4 | The pushes kept flowing at ~0.67 s cadence through all four write attempts: the link, the ATT bearer and our RX path are healthy and uninterrupted | [P — production log] |
| E-5 | **Our CCCD write succeeded**: notifications only flow after the server processes the subscribe (a Write to the CCCD of ff02). The peripheral therefore accepts at least some of our writes on the plaintext link — notifications arrive, so the subscribe was processed | [P — GATT semantics + E-4] |
| E-6 | The Pi has never paired with the unit; the link is plaintext. The phone (vendor app) shows live values (F1) | [P — premise + F1] |
| E-7 | The unit is an ST **BlueNRG** module (advertises `BlueNRG-C0941C`, public address, ST OUI `08:06:A6`), a common base for Chinese transparent-serial/Modbus bridges | [P — discovery] |
| E-8 | The vendor product has a panel **pairing-mode gesture**: hold a button 5 s → the display shows "bLE" blinking 1×/s (FINDINGS §3, spec 006 §1) | [P — app strings] |
| E-9 | The app itself never calls any bonding/pairing API (no `createBond`/`removeBond` anywhere in the Dart pool; only the passive `OnBondStateChanged` FBP event exists) — yet the app's writes work on the owner's phone | [P — spec 006 §8] |

The reconciliation problem: E-5 proves the peripheral processes SOME of
our writes; E-1/E-2 prove it processes NONE of our ff03 value writes;
F1/F2 prove it processes the app's ff03 value writes — which differ from
ours in no GATT-visible way (same UUID, same bytes, same write type,
same session order). The difference must therefore live **below the
GATT-visible layer**: in the link state (§3), or in the server's
peripheral state (§9), or in our local stack (§6/§7 — both excluded).

---

## 2. The mechanism: how a byte-perfect write produces zero effect [P]

### 2.1 A Write Command cannot fail loudly

Bluetooth Core Specification 5.4, Part F (ATT), §3.4.9 "Attribute PDU
response summary" [§14.1]:

| Attribute PDU Method | Successful Response PDU | ATT_ERROR_RSP Allowed | Valid Error Codes |
|---|---|---|---|
| ATT_WRITE_CMD | **none** | **No** | **none** |
| ATT_WRITE_REQ | ATT_WRITE_RSP | Yes | Invalid Handle, Write Not Permitted, Insufficient Authentication, Insufficient Authorization, Insufficient Encryption Key Size, Insufficient Encryption, … |

Consequences, each independently quotable:

- An `ATT_WRITE_CMD` ("write without response", opcode 0x52) is
  fire-and-forget **by design**: the server acknowledges nothing.
  If the server rejects the write — because the characteristic
  requires an encrypted/authenticated link, because the handle is
  wrong, because the value is not writable — it has **no error channel
  and must drop the PDU silently**. The signed-write note in the same
  spec section reads: "If the authentication signature verification
  fails, then the server shall ignore the command" — the ignore
  semantics are normative for command-type writes.
- The same write sent as `ATT_WRITE_REQ` ("write with response",
  opcode 0x12) **must** produce either `ATT_WRITE_RSP` or an
  `ATT_ERROR_RSP` naming the reason — including `0x05 Insufficient
  Authentication` and `0x0F Insufficient Encryption` for security-gated
  attributes [P — §14.14, Nordic DevZone: "An Error Response shall be
  sent by the server in response to the Write Request if insufficient
  authentication, insufficient authorization, …"].
- Field-documented instance of the exact production pattern: servers
  "may technically allow write without response but will silently
  discard packets unless bonding or encryption is in place" [P —
  §14.10, Punchthrough]. Silicon Labs, same point: "for a write without
  response there will be no indication of failure" [P — §14.13].

**This is why four byte-perfect re-sends produced zero observable
effect. The vendor's own write type — withoutResponse, F2 — is the
one ATT operation whose rejection is invisible to the sender.**

### 2.2 What a Write Request would have shown

| Server behavior on ff03 | Write Command (ours/app's) | Write Request (the probe) |
|---|---|---|
| Value write requires encryption/bond | silently dropped | ATT Error `0x05` / `0x0F` → visible exception |
| Handle stale / char not writable | silently dropped | ATT Error `0x01` Invalid Handle / `0x03` Write Not Permitted |
| Write accepted | (invisible — no ack) | `ATT_WRITE_RSP` (ack in ~ms) + whatever the Modbus engine does |

So ONE acknowledged write of the SAME frame (X1, §12) converts the
production silence into a named diagnosis, without any new protocol
bytes, from inside the integration, visible in the HA log [D — probe
design, spec.md FR-1].

### 2.3 Delivery blindness on both stacks

- bleak docs (3.0.2), `write_gatt_char`: a write without response "will
  **queue data to be written and return immediately**"; the
  `BleakGATTProtocolError` raise ("if the peripheral replied with
  ATT_ERROR_RSP") applies "**Only when `response=True`**" [P — §14.4].
  bleak returning without error therefore proves only that BlueZ
  accepted the PDU locally — never that the peripheral did [P — this
  was already stated in research 009 §4; the bleak docs make it
  normative].
- Android is symmetrically blind: `onCharacteristicWrite` fires for
  `WRITE_TYPE_NO_RESPONSE` on **local stack acceptance**, not on
  delivery [P — §14.21]. The vendor app's own success log
  ("指令已发送") is equally unproving — which is why nobody ever
  noticed a delivery problem from the app side.

---

## 3. Q1 — Pairing / bonding: the leading hypothesis

### 3.1 What the Bluetooth spec mandates — and what it leaves open [P]

Core Spec 5.4, Part H (Security Manager), §2.4 [§14.2]:

> "Security shall be initiated by the Security Manager in the device in
> the Central role."

and §2.4.6 "Peripheral Security Request":

> "The Peripheral may request security by transmitting a Security
> Request command to the Central. When a Central receives a Security
> Request command **it may encrypt the link, initiate the pairing
> procedure, or reject the request.**"
> "After receiving a Security Request, the Central shall first check
> whether it has the required security information to enable encryption;
> If this information is missing or does not meet the security properties
> requested by the Peripheral, then the Central **shall initiate the
> pairing procedure**. … If the Central has the required security
> information … it **shall perform encryption setup using LTK**."

Three facts follow, all spec-level:

1. **Per-characteristic security is server-side state** (GATT
   permissions: none / encrypt / encrypt+auth per attribute). Nothing
   forces a server to protect its notify pipe and its write pipe
   identically — "notify open, write encrypted" is a legal and common
   layout (e.g. read-only sensors that accept control only from bonded
   centrals). [P — spec architecture]
2. **The central decides whether security ever happens.** The
   peripheral can only ask. Two centrals with different policies sit on
   the same peripheral with different link states. [P]
3. Once a bond exists, the central **shall** encrypt on the
   peripheral's request. So a bonded central that honors security
   requests rides an encrypted link; an unbonded central that ignores
   them does not. [P]

### 3.2 The Android side

- **Bonded ⇒ encrypted automatically on reconnect**: "on Android, if a
  device is already bonded, the OS can automatically reconnect and
  **start encrypted communication without user prompts**" [P — §14.11,
  Punchthrough pairing guide].
- **Bond-on-first-access**: when a GATT operation hits a
  security-requiring attribute, Android's stack initiates pairing by
  itself (the classic behavior; documented in the Nordic BLE-library
  thread: peripheral characteristic requires encryption → the stack
  handles bonding on access) [P — §14.19 for the pattern]. The app needs
  no `createBond` call (E-9 is therefore fully compatible with a bonded
  phone).
- Android ships a **system pairing agent** (the OS UI); Just Works
  pairing with a NoInputNoOutput peripheral completes without user
  interaction [A — standard platform behavior].
- Therefore the phone's session plausibly runs **encrypted** — either
  the unit requested security once (SMP Security Request → Android
  paired, silently, possibly during the initial add-device wizard — the
  product documents a pairing gesture, E-8) or the phone auto-bonded on
  first protected access. Both paths are invisible to the Dart code
  (E-9). **SUPPOSED, not yet proven: the phone is bonded and the app's
  writes ride an encrypted link — E5 (HCI snoop) decides** [V].

### 3.3 The BlueZ/Linux side

- **No agent by default in a headless stack.** The official BlueZ
  D-Bus documentation for `org.bluez.Device1.Pair()` [P — §14.3]:
  > "If the application has registered its own agent, then that
  > specific agent will be used. Otherwise it will use the default
  > agent. … **In case there is no application agent and also no
  > default agent present, this method will fail.**"
  A headless HA OS runs bluetoothd without bluetoothctl/GNOME, i.e.
  with no registered agent — the documented failure mode is real; it
  is also **fixable from code** (register a minimal
  `org.bluez.Agent1` with `NoInputNoOutput` capability — the capability
  that auto-completes Just Works pairing; an external Python agent via
  D-Bus is documented to fix exactly this [P — §14.7]).
- **Nothing triggers pairing on our path.** Linux centrals elevate
  security when an *acknowledged* operation fails with insufficient
  authentication (BlueZ's auto-secure path), or when userspace calls
  `Pair()`. Our failing operation is a Write Command — **no ack, no
  error, no trigger** (§2.1). The kernel's reaction to an
  unfulfillable peripheral Security Request is a log line, not a
  pairing: "Bluetooth: SMP security requested but not available" [P —
  §14.9, kernel bugzilla 104011]. With a bond present, the same
  Security Request leads to encryption start (spec §2.4.6, "shall
  perform encryption setup using LTK") [P — spec; V — this unit's
  firmware actually sending Security Requests].
- **bleak/brc do expose the levers** (§10): the client has a `pair`
  parameter since bleak 1.0 ("Attempt to pair with the device before
  connecting, if it is not already paired") [P — §14.4];
  `bleak-retry-connector.establish_connection` documents a `pair`
  parameter ("Whether to pair with the device on connect") [P — §14.18];
  and the underlying BlueZ `Pair()` method "will connect to the remote
  device, initiate pairing and then retrieve all … GATT primary
  services" — i.e. it works whether or not a connection is up [P —
  §14.3].

### 3.4 The fit matrix — H-BOND against every proven fact

| Fact | H-BOND prediction | Fits? |
|---|---|---|
| F1 app's writes answered | bonded phone → encrypted link → server accepts Write Command | ✅ |
| E-1/E-2 our writes: zero effect, zero exception | unbonded Pi → plaintext link → server silently drops the security-violating Write Command | ✅ (§2.1 makes "silently" literal) |
| E-3 re-send useless | the drop is a state gate, not a collision — repetition changes nothing | ✅ |
| E-4 pushes continue | notify char has no security requirement (server-side per-attribute state, §3.1) | ✅ |
| E-5 our CCCD write accepted | CCCD security enforcement is lax in many stacks (this unit demonstrably accepts our subscribe on plaintext) | ✅ [A — the "why" is stack laxity; the fact is P] |
| E-9 app never calls bonding APIs | Android bonds at stack level (§3.2) — invisible to the Dart code | ✅ |
| E-8 vendor pairing gesture exists | the product has a pairing UX — consistent with a firmware that stores/expects a bonded central | ✅ [A — inference] |

No other surviving hypothesis fits E-5 + F1 simultaneously: a broken
write pipe (wrong char/handle) would break the app too (F2); an
engine-level gate keyed on anything we send is excluded by byte-parity
(F2/F3).

### 3.5 Pairing-mode precedents

- This unit's own product line documents the panel gesture: hold 5 s →
  "bLE" blinking → configuration/pairing mode (FINDINGS §3; spec 006
  §1.3). A BLE appliance that ships a pairing-mode UX is an appliance
  that keeps pairing state. [P — the strings; A — the inference]
- Same-family precedent, documented in the wild: the Dometic FreshJet
  FJX **RV air-conditioner BLE bridge** (ESPHome/Home Assistant
  project) — its README: "*Pairing says 'Not paired'. **Hold + and − on
  the AC panel for 3 s until it shows BL. Pairing mode lasts about a
  minute.** Close the Dometic app on every phone …*" [P — §14.17]. An
  HVAC BLE bridge, write-requires-bond, panel-entered pairing mode,
  same symptoms. The pattern exists in this exact product category.
- The single-connection rule (spec 006 §9) still applies to every
  experiment: the vendor app must be dead / phone BT off while the Pi
  tests, or nothing connects at all.

### 3.6 What remains unproven in H-BOND

| Gap | Closes with |
|---|---|
| ff03's actual write-security requirement on this unit | X1 W-probe (§12) — error code 0x05/0x0F proves it; ack + still-no-answer moves the gate to the firmware's application layer (still bond-family, §3.4) |
| Whether the phone is bonded / link encrypted | E5 HCI snoop — SMP PDUs + `Encryption Change` events around the app's session |
| Whether the unit accepts pairing outside "bLE" mode | X2 — pair attempt in normal mode; retry in bLE mode on failure |
| Whether an agent exists in the user's HA OS bluetoothd | X2's outcome — the documented failure signature (§10.2) tells us |
| Whether the unit sends SMP Security Requests at all (auto-encrypt after bonding) | X3 persistence retest — a second unpaired-mode test that succeeds proves re-encryption happens without re-pairing |

---

## 4. Q2 — GATT security permissions, and the "list-attributes showed no properties" question

- **Properties ≠ permissions.** The characteristic `Flags` exposed by
  BlueZ (`org.bluez.GattCharacteristic1.Flags`: read / write /
  write-without-response / notify / indicate …) are the **advertised
  ATT properties**. The **security permissions** (none / encrypted /
  encrypted+authenticated, per attribute) are server-internal; **BlueZ
  D-Bus exposes no security-permission property at all** — the only way
  a central learns them is the error code of a failed acknowledged
  access. [P — BlueZ D-Bus API surface; hence X1 is the only code-only
  disclosure mechanism.]
- **Why the user's `list-attributes` showed no properties: normal.**
  `bluetoothctl`'s `list-attributes` "Lists the attributes of either
  the local device or a remote device, encompassing services,
  characteristics, and handles" [P — §14.15, bluetoothctl-gatt(1)] —
  UUIDs and handles, not Flags. Flags appear in D-Bus properties and in
  our own integration's INFO `GATT enumeration … properties [...]`
  lines. **The absence of properties in that listing is a display
  matter, not evidence of anything.** [P]
- What E4' (the grep of the existing production enumeration lines,
  quickstart 010 §0 — still pending) will tell us: ff03's advertised
  properties. What it can NOT tell us: the security requirement (above).
  Both data points matter, but only X1 discriminates the gate.
- The spec-mandated linkage between a characteristic's permissions and
  its CCCD's is not enforced uniformly by stacks; this unit accepts our
  CCCD write on a plaintext link (E-5) while (per hypothesis) refusing
  value writes — a laxity that is common in embedded GATT servers
  [A — mechanism; the observed behavior is P].

---

## 5. Q3 — MTU / Data Length Extension: excluded by arithmetic [P]

- Our write is 8 bytes; an ATT Write Command needs 3 + 8 = 11 bytes of
  MTU. The default ATT_MTU is 23 on every stack; nothing on either side
  negotiates below 23. **MTU cannot reject our write while accepting
  the app's** — unless the stacks negotiated differently AND the server
  policed it, and even then 8 ≪ 20.
- Android: pre-14, MTU stays 23 unless the app calls `requestMtu` —
  **the vendor app never calls it** (spec 010 §5 Q7 [P]), so the app
  runs at MTU 23 on this unit for value writes; Android 14+
  auto-negotiates 517 on GATT-client activity [P — §14.12,
  developer.android.com behavior change]. Either way ≥ 23. If MTU were
  the gate, the app would fail identically. Dead.
- bleak on BlueZ reports `mtu_size` as always 23 (backend limitation)
  and handles fragmentation internally [P — §14.4 warning box]. Our RX
  side already handles both observed regimes (20-byte chunks at MTU 23
  and whole 99-byte block frames) [P — spec 008 §5 Q4]. Dead as a
  receive-side factor too.
- DLE affects packet airtime, never PDU acceptance. Dead.

**Verdict: excluded as a cause; contributes nothing.**

## 6. Q4 — GATT cache / handle map: LOW

- The GATT table is **defined by the server**; handles are the
  peripheral's assignments. Two centrals reading the same table get the
  same handles. "Android's cached map vs BlueZ's fresh map" cannot
  invent different handles for the same firmware. [P — architecture]
- ff02 demonstrably resolves correctly (E-4: notifications flow), and
  ff03 is resolved from the **same enumerated table in the same
  connect** (`GATT enumeration` feeds both resolvers). A handle error on
  one and not the other requires a selectively-wrong cache — possible
  only via BlueZ's persisted attribute cache
  (`main.conf [GATT] Cache=always` — "Always cache attributes even for
  devices not paired", §14.16) going stale after a firmware table
  change. This unit's table has never changed. [P — factors; A — LOW
  rating]
- Residual risk path: a stale-cache write lands on a wrong handle →
  Write Command silently ignored (§2.1) — i.e. **X1 discriminates this
  too**: `0x01 Invalid Handle` (or `0x03`) on the acknowledged probe =
  handle/property problem; `0x05`/`0x0F` = security problem. [D]
- Android's GATT cache is irrelevant to the server's handles (above)
  and is managed by the OS (Service Changed handling). Dead as a
  differentiator.

## 7. Q5 — Is `withoutResponse` honored identically by BlueZ?

- Semantics: yes, same opcode. BlueZ `WriteValue(type="command")`
  emits an `ATT_WRITE_CMD` (0x52); Android `WRITE_TYPE_NO_RESPONSE`
  emits an `ATT_WRITE_CMD` (0x52). BlueZ does not convert a command
  into a request (long-write Prepare/Execute flows apply to
  `type="request"` only, and never to an 8-byte value). [P — stack
  behavior; A — version spread noted]
- What differs is the **callback semantics around local acceptance**,
  on both stacks (§2.3) — symmetrically blind, so no differentiator
  there either.
- Verifying what actually leaves the air: `btmon`/`btsnoop` on the Pi
  (terminal — Tier D, §12) or Android's HCI snoop (E5). The
  code-only proxy is X1's acknowledged twin: same frame, opposite ack
  policy, same session state — any divergence between the two outcomes
  is server-side policy, any parallel silence is a local-stack problem.
  [D — design]
- Note for the probe's legality: a Write Request to a characteristic
  that advertises only write-without-response may be refused with
  `0x03 Write Not Permitted` by a strict server. That error is itself a
  diagnostic (property-level gate), harmless to send (the server writes
  nothing on refusal), and the frame bytes are the vendor's own
  read-only FC03 request either way. [P — ATT semantics; D — probe is
  safe]

## 8. Q6 — Connection parameters: excluded [P arithmetic]

- Both centrals negotiate comparable defaults (balanced-class
  30-50 ms intervals); the peripheral may send an L2CAP
  connection-parameter update that both stacks honor. [A — standard
  behavior; not worth deeper citation because:]
- The request window is 8.0 s with pushes arriving at 0.67 s cadence
  (E-1/E-4) — the RX direction demonstrably completes ~24 connection
  events per window. An answer needing 1-3 events cannot be lost to
  interval math while the push stream flows steadily. A slower interval
  can add latency; it cannot delete a response, and it cannot make FOUR
  re-sends vanish (E-3).
- **Verdict: excluded as a cause.**

## 9. Q7 — Accept list / whitelist at the peripheral

- A controller-level **filter accept list gating connections** is
  disproven by production: our connection completes, the ATT bearer
  works, notifications flow (E-4/E-5). If the unit refused unknown
  centrals at the LL level, the Pi would never get a link at all. [P]
- A **write-level gate keyed on bond/identity** — stack-side per-char
  security (§3.1) or firmware-side bond check — is exactly the H-BOND
  family (§3.4) and is NOT disproven; it is the leading hypothesis.
  The vendor pairing-mode UX (E-8) and the Dometic precedent (§3.5)
  are its supporting context. [A]
- Practical note: the unit accepts ONE central (spec 006 §9) — any
  experiment requires the vendor app killed / phone BT off. [P]

## 10. Q8 — The levers: what can be forced, from where

### 10.1 Code-only (the integration — the deliverable of this spec)

| Lever | Mechanism | Evidence/Status |
|---|---|---|
| **Acknowledged probe** | `write_gatt_char(..., response=True)` for the same frame — surfaces `0x05/0x0F/0x03/0x01` as a bleak `BleakGATTProtocolError` (bleak ≥3: "if the peripheral replied with ATT_ERROR_RSP … Only applies when response=True") or BleakError chain (older) | [P — §14.4] |
| **Pairing, bleak ≥1.0 path** | `BleakClient(..., pair=True)` — "Attempt to pair with the device before connecting, if it is not already paired"; bleak docs advise extending the timeout when pairing (PIN time) | [P — §14.4] |
| **Pairing via brc (HA path)** | `bleak_retry_connector.establish_connection(..., pair=True)` — documented parameter "Whether to pair with the device on connect" | [P — §14.18] |
| **Pairing, BlueZ D-Bus semantics** | `org.bluez.Device1.Pair()`: "will connect to the remote device, initiate pairing and then retrieve all … GATT primary services"; works bonded-or-not, connected-or-not; error vocabulary documented (`AlreadyExists`, `AuthenticationFailed`, `AuthenticationRejected`, `AuthenticationTimeout`, `ConnectionAttemptFailed` …) | [P — §14.3] |
| **Bond-state observability** | D-Bus properties `Paired` (readonly), `Trusted` (readwrite), `Connected`, `ServicesResolved` — best-effort read for diagnostics (object path resolvable via the BlueZ backend; guarded, DEBUG-on-failure) | [P — §14.3] |

### 10.2 The agent problem (the one real deployment risk)

`Pair()` **fails without a registered agent** (§3.3, official docs).
Expected failure signatures in bleak: D-Bus error chains containing
`org.bluez.Error.Failed` / "agent" texts; Just Works with a
`NoInputNoOutput` agent completes without interaction. Fallback lever,
fully code-only: **register a minimal `org.bluez.Agent1`** (capability
`NoInputNoOutput`, empty/pass-through handlers: `Release`,
`RequestPinCode`, `DisplayPinCode`, `RequestPasskey`, `DisplayPasskey`,
`RequestAuthorization` auto-approve, `AuthorizeService` auto-approve)
via **dbus-fast** — the same D-Bus library bleak already uses on Linux,
so no new dependency in HA. Precedent: an external Python agent via
D-Bus fixed pairing that failed without one [P — §14.7]. [D — FR-3
fallback design, gated on X2's observed signature]

### 10.3 User-choreography levers (no terminal)

- The **"bLE" pairing-mode dance** (hold panel button 5 s → display
  blinks "bLE") while the connection test runs its pair attempt —
  the Dometic precedent says pairing-mode windows are ~1 min, enough
  for one test cycle. [A — needs the X2 outcome to know if required]
- Enabling the shipped `pair` option (spec.md FR-5) after a positive
  X2/X3 — production connects then pair on every establish. [D]

### 10.4 Terminal-only levers (documented for completeness — NOT usable by this operator)

`bluetoothctl info/pair/trust`, `btmgmt info` (auto-secure setting),
`btmon` (on-air Write Command proof), `journalctl -u bluetooth`
(SMP/kernel lines: "security requested but not available"),
`/var/lib/bluetooth/<adapter>/<addr>/` (bond storage). These remain the
fastest truth for any operator WITH SSH; this project must not depend
on them (spec constraint: the owner cannot use a terminal on the Pi).

---

## 11. Cause ranking (most probable first)

| Rank | Cause | Class | Fits E-1..E-9 + F1-F3? | Killer experiment |
|---|---|---|---|---|
| 1 | **H-BOND**: ff03 value writes require an encrypted (bonded) link; notify/CCCD unsecured. App's phone bonded → works; Pi unbonded → silently dropped (§2.1) | link state | ✅ all | X1 (0x05/0x0F), X2 (pair → answer), E5 (Encryption Change) |
| 2 | **H-APP-GATE** (variant of 1): GATT accepts the write on plaintext, but the **firmware application layer** drops frames from non-bonded centrals | peripheral app state | ✅ all (X1 would ACK, still silent) | X1 ack+silent → X2 still decisive; E5 |
| 3 | **H-SILENCE-AMP** (amplifier, co-cause of 1/2): withoutResponse hides the security error, so no stack ever auto-escalates security (§3.3) | mechanism | ✅ (explains why nothing self-heals) | inherent — X1 works around it by construction |
| 4 | H-STALE-CACHE: BlueZ persisted GATT cache points ff03's UUID at a wrong handle; Write Command to a wrong handle is silently ignored (§6) | local stack | ⚠️ low (ff02 live-proves the table) | X1 (0x01/0x03 signature) |
| 5 | H-LOCAL-DROP: BlueZ never transmits our Write Command at all | local stack | ⚠️ very low (CCCD write transmitted fine, E-5) | X1/X2; Tier D btmon if ever |
| 6 | H-MTU / H-CONDPARAM / H-ACCEPTLIST(LL) | transport | ❌ excluded (§5, §8, §9) | already excluded by arithmetic/logic |

H-BOND and H-APP-GATE are both bond-family; both are fixed by the same
pairing path, and X1's error code (or its absence) is what tells them
apart. Everything else is excluded or near-excluded by evidence already
in hand.

---

## 12. The experiment matrix (details & choreography: plan.md, quickstart.md)

| ID | Experiment | Tier (no terminal?) | Decides |
|---|---|---|---|
| E4' | grep the existing production log (`GATT enumeration`, `using write characteristic`, idle-window summary) | Tier 0 — HA UI log only | ff03 advertised properties; push onset (E2) |
| **X1** | **Security probe build**: the connection test re-sends the base read as a Write Request on timeout | Tier B — code push + HA UI run | security gate (0x05/0x0F), property gate (0x03), handle gate (0x01), or acked-but-silent (→H-APP-GATE) |
| **X2** | **Pair attempt**: after X1's security verdict, the test pairs (bleak/brc `pair`; optional "bLE" choreography on failure) and re-runs the base read | Tier B — code + UI (+ the panel gesture, no terminal) | H-BOND end-to-end; agent availability (10.2) |
| **X3** | **Persistence retest**: one more connection test with NO pairing mode, option still default | Tier B | bond survives reconnect; link re-encrypts without re-pairing |
| E5 | Android HCI snoop of one vendor-app session on this unit (spec 009 §3 protocol, reading list extended in quickstart 011 §4) | Tier C — phone UI only | phone bonded? link encrypted? app's write type/handle; MTU/conn-param baseline |
| C2 | nRF Connect differential: from a phone WITHOUT the vendor bond, write the frame to ff03 (vendor app killed) | Tier C — phone only | bond-free Android reproduces our silence → cross-platform proof of the gate; if answered instead, bond hypothesis weakened sharply |
| Tier D | bluetoothctl pair/info/trust, btmon, btmgmt, journalctl | **requires SSH — excluded for this operator**; kept for completeness | local-stack ground truth |

## 13. Unproven ledger (what this spec does NOT claim)

- That ff03's writes are security-gated on this unit — X1's error code
  decides [V].
- That the owner's phone is bonded / that the app's session is encrypted
  — E5 decides [V]. (Android Settings' paired-device list often hides
  LE-only bonds — do not treat its absence as evidence either way.)
- That the unit accepts pairing outside "bLE" mode — X2 decides [V].
- That an agent exists (or can be registered) inside the operator's HA
  OS bluetoothd — X2's failure signature decides, FR-3 is the fallback
  [V].
- That a bonded BlueZ link re-encrypts on reconnect for this firmware
  (depends on the unit sending SMP Security Requests, or on our
  explicit security elevation) — X3 decides [V].
- The exact BlueZ auto-secure retry semantics across the operator's
  bluetoothd version — X1/X2 outcomes are designed to be informative
  regardless [A].

## 14. References

1. Bluetooth Core Spec 5.4, Part F (ATT), §3.4.9 + signed-write note —
   https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-54/out/en/host/attribute-protocol--att-.html
2. Bluetooth Core Spec 5.4, Part H (SM), §2.4/§2.4.6 Peripheral Security
   Request —
   https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-54/out/en/host/security-manager-specification.html
3. BlueZ D-Bus `org.bluez.Device1` (Pair/Paired/Trusted/Connected docs) —
   https://bluez.readthedocs.io/en/latest/device-api/ (mirror:
   https://man.archlinux.org/man/org.bluez.Device.5.en)
4. bleak `BleakClient` API (pair parameter, write_gatt_char
   response=True / BleakGATTProtocolError, mtu_size warning) —
   https://bleak.readthedocs.io/en/latest/api/client.html
5. bleak issue #309 — Pair with devices that are not connected (BlueZ
   Pair semantics) — https://github.com/hbldh/bleak/issues/309
6. bleak issue #1434 — pairing only works with a registered agent —
   https://github.com/hbldh/bleak/issues/1434
7. bdteo — "BlueZ Pairing Fix: External Python Agent & D-Bus Polling"
   (agent availability as root cause) —
   https://bdteo.com/bluez-pairing-python-agent-workaround-authentication-failed/
8. btmon(1) — HCI status-code table (0x06 "Not Paired: Operation
   requires an existing bond") —
   https://man.archlinux.org/man/extra/bluez-utils/btmon.1.en
9. Linux kernel bugzilla 104011 — "Bluetooth: SMP security requested but
   not available" (kernel reaction to unfulfillable Security Request) —
   https://bugzilla.kernel.org/show_bug.cgi?id=104011
10. Punchthrough — "Write Requests vs. Write Commands" ("silently
    discard packets unless bonding or encryption is in place") —
    https://punchthrough.com/ble-write-requests-vs-write-commands/
11. Punchthrough — "BLE Pairing and Bonding" (Android bonded ⇒ automatic
    encrypted reconnection) —
    https://punchthrough.com/ble-pairing-and-bonding-guide/
12. Android 14 behavior change — ATT MTU auto-set to 517 —
    https://developer.android.com/about/versions/14/behavior-changes-all#mtu-set-to-517
    (context: https://punchthrough.com/android-ble-guide/)
13. Silicon Labs community — write without response has no failure
    indication —
    https://community.silabs.com/s/question/0D51M00007xeSvxSAE/is-encrypted-write-without-response-possible
14. Nordic DevZone — ATT Error Response SHALL be sent for Write Request
    (insufficient authentication/…) —
    https://devzone.nordicsemi.com/f/nordic-q-a/20488/how-to-set-gatt-s-error-response-after-a-write-request
15. bluetoothctl-gatt(1) — list-attributes scope (services,
    characteristics, handles) — https://mankier.com/1/bluetoothctl-gatt
16. BlueZ main.conf [GATT] Cache (attribute cache "even for devices not
    paired") —
    https://github.com/bluez/bluez/blob/master/src/main.conf
17. prebsit/dometic-fjx7-ha — RV-AC BLE bridge README ("Pairing says
    'Not paired' … panel pairing mode … close the app on every phone")
    — https://github.com/prebsit/dometic-fjx7-ha
18. bleak-retry-connector usage — establish_connection `pair`
    parameter — https://bleak-retry-connector.readthedocs.io/en/latest/usage.html
19. Nordic DevZone — bonding devices with the Android BLE library
    (encrypted characteristic ⇒ stack-initiated bond) —
    https://devzone.nordicsemi.com/f/nordic-q-a/65102/how-to-bond-devices-using-the-android-ble-library
20. Android Developers — Connect to a GATT server (stack-managed bond
    flows) —
    https://developer.android.com/develop/connectivity/bluetooth/ble/connect-gatt-server
21. Stack Overflow — WRITE_TYPE_NO_RESPONSE callback fires on local
    acceptance —
    https://stackoverflow.com/questions/21791948/android-ble-gatt-characteristic-write-type-no-response-not-working
22. Home Assistant developer docs — Bluetooth (best practices; no
    pairing feature documented) —
    https://developers.home-assistant.io/docs/bluetooth/
23. Kynetics — Pairing agents in the BlueZ stack (agent lifecycle) —
    https://technotes.kynetics.com/2018/pairing-agents-bluez/