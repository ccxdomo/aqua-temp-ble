"""Constants for the aqua_temp_ble integration.

Kept stdlib-only (no Home Assistant imports) so the standalone tools and the
unit tests can import this module outside HA.
"""

DOMAIN = "aqua_temp_ble"

# Platform names — identical to homeassistant.const.Platform values. Plain
# strings are used on purpose (see module docstring).
PLATFORMS = ["climate", "sensor", "binary_sensor", "button"]

# GATT characteristics — the vendor app's OWN buckets, read from the
# disassembled Dart session manager (research 010 §1: the discovery
# classifier closure 0xa146dc stores the matched characteristic objects
# into the session state, field 0x2b notify / 0x2f write, by UUID match
# ALONE — never by advertised properties). The historic ff01 write
# constant was a DEX misreading: the app never selects a
# characteristic ff01 (research 010 §1.3, FINDINGS.md §11.3 correction).
UUID_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"  # [P] app notify bucket {ff02, 2216, 2a00}, disassembly research 010 §1
UUID_WRITE = "0000ff03-0000-1000-8000-00805f9b34fb"   # [P] app write bucket {ff03, 2215, 2aa6} — research 010 §1; the app never selects a characteristic ff01

# The app's full classification buckets, in its own scan order
# (the classifier loop compares each characteristic UUID against these
# triples — research 010 §1.1/§1.3: two vendor GATT generations plus
# the GAP fallback; on the owner's unit the buckets resolve to
# notify = ff02, write = ff03). aqua_client walks them FIRST,
# property-blind; advertised properties are advisory only (spec 010
# FR-2). Unknown GATT layouts still fall back to the adaptive
# vendor-service scan (the c8d8260 safety net).
UUID_NOTIFY_BUCKETS = (
    UUID_NOTIFY,
    "00002216-0000-1000-8000-00805f9b34fb",
    "00002a00-0000-1000-8000-00805f9b34fb",
)
UUID_WRITE_BUCKETS = (
    UUID_WRITE,
    "00002215-0000-1000-8000-00805f9b34fb",
    "00002aa6-0000-1000-8000-00805f9b34fb",
)

# BLE name prefixes the vendor app itself filters on (Dart pool). [P]
# Operating channel: "BlueNRG-" + "WT-" (research 006 §3); "hiflying-" is
# the DEX string-table twin of the same operating filter; "prov_" is the
# WiFi-models provisioning-mode name (harmless to keep).
NAME_PREFIXES = ("bluenrg-", "hiflying-", "prov_", "wt-")

# Seconds. Binds the standalone raw-client path only (tools/smoke_client,
# unit tests): inside Home Assistant, bleak-retry-connector hardcodes
# every connect attempt to 20.0 s and ignores this value (spec 005 FR-6,
# research 005 §3.2).
CONNECT_TIMEOUT = 15.0
REQUEST_TIMEOUT = 8.0    # max wait for a full Modbus response
SILENCE_TIMEOUT = 0.6    # gap without a new chunk that ends a fragmented response
# Seconds to wait after subscribing to ff02 before the first request.
# Vendor parity: the app's session manager holds a 2 s Duration in the
# subscribe→read path (research 006 §2 step 4, §7).
SETTLE_AFTER_SUBSCRIBE = 2.0
WRITE_ECHO_TIMEOUT = 3.0  # max wait for an FC10 echo; 0 = fire-and-forget
MAX_RX_BUFFER = 512

DEFAULT_POLL_INTERVAL = 30  # seconds (options: 10..600)
DEFAULT_TEMPERATURE_UNIT = "C"  # display only; wire is always x10 Celsius [P]

# Spec 011 (the security probe + pairing ladder): the pairing budget and
# the evidence-gated production lever (research 011 §10 / contract 011
# §4). D-Bus Pair() can legitimately take seconds — PIN windows, then
# service discovery after bonding — and bleak's own docs advise
# generous timeouts when pairing; the config test bounds the WHOLE
# ladder with it inside the unchanged 70 s flow budget (contract 011
# §4 arithmetic). Never bounds a poll read (contract 009 §5 untouched).
PAIR_TIMEOUT = 25.0
# Evidence-gated option (spec 011 FR-6): OFF until a connection test
# produced the `security probe: answered after pairing` line (the X2
# proof, quickstart 011 §2) — enabling it without that evidence is a
# contract violation (quickstart 011 discipline). With the option ON,
# every establish requests pairing; with it OFF the production path is
# byte-for-byte today's behavior (FR-7).
DEFAULT_PAIR_ON_CONNECT = False
# The establish kwarg that requests pairing across bleak generations
# (spec 011 FR-3 rung B / FR-6): bleak-retry-connector's
# establish_connection(pair=...) and bleak's BleakClient(pair=...)
# (bleak >= 1.0 moved pairing into the constructor — research 011
# §10.1); capability-checked with inspect.signature, never assumed.
PAIR_ENABLE_PROP = "pair"

# Spec 013 (the pushed-block harvest — the listen-only read plane): the
# harvest listens for the unit's spontaneous block pushes and sends
# NOTHING (contract 013 §1 — zero TX; nothing the firmware's
# application-layer gate could drop). The 30 s window: all four
# observed starts appeared within two consecutive 8 s request windows
# (~16 s) in production (research 013 §5.4) — wide margin. The default
# wanted set is the four observed block starts [P] (1001/1046/2001/
# 2046); the app's other two query blocks (1091/1136) are appended if
# ever seen [V].
PUSH_HARVEST_TIMEOUT = 30.0
PUSH_HARVEST_STARTS = frozenset({1001, 1046, 2001, 2046})

# Config flow (spec 002) — scanning UX tuning (tasks.md block 1).
# Flow-facing constants live here, next to the other integration-wide
# knobs; everything above stays protocol-only.
SCAN_WAIT_SECONDS = 8.0  # bounded rescan grace delay (FR-6)
MAX_OTHER_DEVICES = 30  # cap on the "other devices" tier (FR-4)

# Set True to log every raw notification chunk in hex (debug aid, noisy).
DEBUG_RAW = False