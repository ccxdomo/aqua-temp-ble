"""Constants for the aqua_temp_ble integration.

Kept stdlib-only (no Home Assistant imports) so the standalone tools and the
unit tests can import this module outside HA.
"""

DOMAIN = "aqua_temp_ble"

# Platform names — identical to homeassistant.const.Platform values. Plain
# strings are used on purpose (see module docstring).
PLATFORMS = ["climate", "sensor", "binary_sensor", "button"]

# GATT characteristics recovered from the vendor app (FINDINGS.md §3, §11.3).
# FIRST choice on every connect; when the unit does not expose them,
# aqua_client discovers the real characteristics adaptively instead of
# failing (see aqua_client.py, adaptive GATT discovery).
UUID_WRITE = "0000ff01-0000-1000-8000-00805f9b34fb"   # [P] handle 2214
UUID_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"  # [P] handle 2216
# ff03 (handle 2215): role unknown in the vendor app dump (FINDINGS.md
# §11.3). LAST-RESORT write candidate for the adaptive discovery — tried
# only when ff01 is absent and no vendor service exposes a writable
# characteristic. [V]
UUID_WRITE_FALLBACK = "0000ff03-0000-1000-8000-00805f9b34fb"

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

# Config flow (spec 002) — scanning UX tuning (tasks.md block 1).
# Flow-facing constants live here, next to the other integration-wide
# knobs; everything above stays protocol-only.
SCAN_WAIT_SECONDS = 8.0  # bounded rescan grace delay (FR-6)
MAX_OTHER_DEVICES = 30  # cap on the "other devices" tier (FR-4)

# Set True to log every raw notification chunk in hex (debug aid, noisy).
DEBUG_RAW = False