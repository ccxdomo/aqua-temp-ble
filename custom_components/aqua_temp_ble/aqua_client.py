"""AquaTempBleClient — BLE transport for the Aqua Temp pool heat pump.

Owns the bleak connection and enforces a strict one-at-a-time
request/response cycle. Responses to a Modbus request arrive as GATT
notifications on the notify characteristic (ff02 on the documented
units; adaptively discovered otherwise — see
_resolve_notify_characteristic) and MAY be fragmented (typical chunks
of 20 bytes at default MTU; one whole 99-byte block frame per
notification was observed in production — research 008 §5 Q4).

Reassembly rules (BLUEPRINT §2.2d, revised by spec 008 — research 008
§4, contracts/reassembly-matching.md):

1. boundary completion (primary): accumulate chunks, compute the frame
   boundary from the accumulated head (_frame_boundary), complete at
   the boundary;
2. silence timeout (SILENCE_TIMEOUT without a new chunk) — completes a
   CRC-valid frame that MATCHES the pending request (the self-heal for
   boundary-rule misreads, e.g. the pathological write echo — contract
   008-B §4.2/§4.3);
3. request timeout (REQUEST_TIMEOUT, overall deadline) — the same match
   check, else raises AquaTempTimeoutError with the received-frame
   census (contract 008-B §6).

Every completed frame is CRC-validated and matched against the pending
request before the awaiting caller resumes: a frame completes a request
only when its shape — and, for block frames and echoes, its start/qty —
matches the request (contract 008-B §3). The c8d8260 FC drop-gate
(research 008 §3) is gone: nothing is ever dropped for its function
code alone, and non-matching frames are census-logged, never fatal.

Standalone use (outside Home Assistant, e.g. tools/smoke_client.py):
    sys.path.insert(0, "<repo>/custom_components/aqua_temp_ble")
    import aqua_client
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import struct
import time
from collections import Counter, namedtuple
from dataclasses import dataclass
from typing import Any, Sequence

from bleak import BleakClient, BleakScanner

try:  # package import (Home Assistant)
    from . import const, modbus, registers
except ImportError:  # standalone tools/tests: run from the component directory
    import const
    import modbus
    import registers

_LOGGER = logging.getLogger(__name__)


class AquaTempError(Exception):
    """Base error for the Aqua Temp BLE client."""


class AquaTempConnectionError(AquaTempError):
    """Cannot connect, or the connection was lost."""


class AquaTempDeviceNotFoundError(AquaTempError):
    """The device could not be found in any discovery source.

    Deliberately NOT an AquaTempConnectionError (spec 005 FR-9): the
    device was never found, which is a different failure than "found but
    could not connect". Being an AquaTempError keeps the coordinator's
    `except AquaTempError` handling unchanged.
    """


class AquaTempTimeoutError(AquaTempError):
    """No complete response arrived in time."""


class AquaTempResponseError(AquaTempError):
    """Device replied, but the frame is invalid (CRC, shape, exception code).

    Wraps a modbus.ModbusError when one is available (see .modbus_error).
    """

    def __init__(self, message_or_exc: str | Exception) -> None:
        if isinstance(message_or_exc, BaseException):
            self.modbus_error: modbus.ModbusError | None = message_or_exc  # type: ignore[assignment]
            super().__init__(str(message_or_exc))
        else:
            self.modbus_error = None
            super().__init__(message_or_exc)


def exc_chain(exc: BaseException) -> str:
    """Render an exception with its cause chain, root cause included.

    Public helper (spec 005 FR-1) so the config flow can log full cause
    chains too. The production incident showed the real transport error
    never reaching the logs; this renders 'Type: message <- RootType:
    root message' so log lines and AquaTempConnectionError strings always
    carry the underlying failure, not just the outer wrapper.
    """
    parts = [f"{type(exc).__name__}: {exc}"]
    seen = {exc}
    cause = exc.__cause__
    while cause is not None and cause not in seen:
        seen.add(cause)
        parts.append(f" <- {type(cause).__name__}: {cause}")
        cause = cause.__cause__
    return "".join(parts)


# Backwards-compatible private alias (spec 005 FR-1): any external caller
# of the historic name keeps working; internal call sites use the public
# name.
_exc_chain = exc_chain


def _now() -> float:
    """Monotonic clock seam for the stage-timing instrumentation.

    The ONLY clock connect() reads (plan 007 §1.1, contract 007 §4):
    tests fake the clock by patching this module attribute, never
    time.monotonic globally. The stage timings it produces convert the
    next failed run into the research 007 §8 signature table — the
    whole point of spec 007 (FR-1, research 007 §8).
    """
    return time.monotonic()


# --------------------------------------------------------------------------
# Spec 011 — the security-probe seam and the pairing ladder (research 011
# §2.1/§10, contract 011 §5). An ATT Write Command has NO error channel
# (Core Spec 5.4 Part F §3.4.9: response "none", ATT_ERROR_RSP "No") — a
# server that refuses our write cannot say so (research 011 §2.1 [P]). The
# acknowledged twin (Write Request) MUST be answered, and the answer
# names the gate: 0x05/0x0F security, 0x03 permission, 0x01 handle.
# --------------------------------------------------------------------------

# Contract 011 §5.1 string tables (case-insensitive; space-insensitive
# too — D-Bus error names arrive as 'NotPermitted'/'NotPaired', their
# messages as 'Not permitted'). Order matters: "insufficient
# authorization" (ATT 0x08 → permission) is matched BEFORE the security
# forms, or its "insufficient auth" prefix would mis-route it
# (research 011 §10.1 taxonomy).
_ACK_PERMISSION_STRINGS = (
    "insufficient authorization",  # ATT 0x08 (contract 011 §5.1)
    "write not permitted",          # ATT 0x03
    "not permitted",                # BlueZ/D-Bus shape of 0x03
    "write not allowed",
)
_ACK_SECURITY_STRINGS = (
    "insufficient authentication",  # ATT 0x05
    "insufficient encryption",      # ATT 0x0F
    "insufficient auth",            # older stacks' short form (research 011 §10.1)
    "not paired",                   # BlueZ status 0x06 (btmon table, research 011 §14.8)
)
_ACK_HANDLE_STRINGS = (
    "invalid handle",  # ATT 0x01
    "invalid offset",  # ATT 0x04
)


def _classify_ack_write_failure(exc_chain_text: str) -> str:
    """Pure verdict classifier for acknowledged-write failures (spec
    011 FR-2 rule 2, contract 011 §5.1 — the research 011 §10.1
    taxonomy).

    Input: the failure chain text (the AquaTempConnectionError message
    carries it verbatim, the spec 005 discipline). Output: exactly one
    verdict — att-error-security / att-error-permission /
    att-error-handle / local-failure. The fifth §5.1 row,
    att-accepted-silent, is produced by the request-timeout path, not
    here. Unknown strings fall to local-failure with the chain logged
    verbatim by the caller — never guessed.
    """
    text = str(exc_chain_text).lower()
    compact = text.replace(" ", "")
    for needle in _ACK_PERMISSION_STRINGS:
        if needle in text or needle.replace(" ", "") in compact:
            return "att-error-permission"
    for needle in _ACK_SECURITY_STRINGS:
        if needle in text or needle.replace(" ", "") in compact:
            return "att-error-security"
    for needle in _ACK_HANDLE_STRINGS:
        if needle in text or needle.replace(" ", "") in compact:
            return "att-error-handle"
    return "local-failure"


def _pairing_already_bonded(chain_lower: str) -> bool:
    """BlueZ AlreadyExists / already-paired chains (contract 011 §5.2):
    the X3 fast no-op — a Pair() against an existing bond."""
    compact = chain_lower.replace(" ", "")
    return (
        "alreadyexists" in compact
        or "alreadypaired" in compact
        or "already bonded" in chain_lower
    )


def _pairing_agent_signature(chain: str) -> bool:
    """True for the missing-agent Pair() failure signatures (contract
    011 §5.2 row 1, research 011 §10.2/§14.6): 'no … agent' chains and
    the org.bluez.Error.Failed + agent-mention pattern (the documented
    failure of an agent-less bluetoothd, research 011 §3.3).
    Authentication* chains are NOT agent signatures — they carry the
    bLE choreography guidance instead (contract 011 §5.2)."""
    lowered = chain.lower()
    if re.search(r"no [a-z0-9_ ]*agent", lowered):
        return True
    return "agent" in lowered and "org.bluez.error.failed" in lowered


def _pairing_ble_guidance(chain: str) -> str:
    """The bLE choreography hint for authentication failures (contract
    011 §5.2 action): the unit likely wants its pairing mode — hold the
    panel button 5 s until the display shows 'bLE' blinking (quickstart
    011 §2). Appended to the failure line so the operator's pasted log
    carries the next step."""
    lowered = chain.lower()
    if any(
        needle in lowered
        for needle in (
            "authenticationfailed",
            "authenticationrejected",
            "authenticationtimeout",
            "authentication failed",
            "authentication rejected",
            "authentication timeout",
        )
    ):
        return " — retry while the panel shows bLE (hold the panel button 5 s)"
    return ""


def _kwargs_accepts(func: Any, name: str) -> bool:
    """True when func accepts a `name` parameter (spec 011 FR-3/FR-6
    capability check, research 011 §10.1: the pair plumbing moved across
    bleak/brc API generations, so it is detected with
    inspect.signature, never assumed). An explicit parameter or a
    **kwargs sink both count; a broken signature is a polite False."""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    if name in parameters:
        return True
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in parameters.values()
    )


def _package_version(name: str) -> str:
    """Installed package version for the ladder's log lines (contract
    011 §3.3), 'unknown' when absent or unresolvable — guarded, never
    raises (the version is context, not a gate)."""
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:  # noqa: BLE001 — PackageNotFoundError et al.
        return "unknown"


def _dbus_fast_message() -> Any | None:
    """The dbus_fast Message class, or None when the library is absent
    (spec 011 FR-3 rung C / the bond-state read — guarded import, never
    a requirement; AC-8; test seam for the 011 suite)."""
    try:
        from dbus_fast import Message

        return Message
    except ImportError:
        return None


@dataclass(frozen=True)
class _ResponseMatch:
    """What may complete the in-flight request (contract 008-B §1).

    Each request kind names the response shapes it accepts plus the
    start/count a frame must echo; _matches() is the single authority on
    completion — nothing is ever dropped for its function code alone
    (research 008 §3: the c8d8260 FC gate deleted twelve 99-byte
    CRC-valid block frames and the read died with 'got 0 bytes').
    """

    accept_fc03: bool = False   # 63 03 bytecount data crc (95 B for 45 regs)
    accept_block: bool = False  # 63 10 start qty bytecount data crc (99 B)
    accept_echo: bool = False   # 63 10 start qty crc (8 B write echo)
    start: int | None = None    # register start a block/echo frame must echo
    count: int | None = None    # register count a block/echo frame must echo


def _frame_boundary(buf: bytes) -> int | None:
    """Total length of the frame at the head of buf, or None if unknown.

    Order (contract 008-A §3 — the block rule FIRST for fc 0x10):
    1. buf[0] != SLAVE_ID -> None (caller already dropped non-Modbus)
    2. fc & 0x80 -> 5 (exception)
    3. fc == FC_READ_HOLDING (0x03) -> bytecount+5 (needs len >= 3)
    4. fc == FC_BLOCK (0x10):
         len < 7 -> None (bytecount byte not visible — WAIT, never guess:
           cutting a 99-byte block frame at byte 8 was the second
           production bug, research 008 §3)
         block_response_length(buf) -> bytecount+9 (block shape wins)
         else -> 8 (write echo)
    5. anything else -> None (silence timer governs)
    """
    if len(buf) < 2 or buf[0] != modbus.SLAVE_ID:
        return None
    fc = buf[1]
    if fc & 0x80:
        return 5
    if fc == modbus.FC_READ_HOLDING:
        return buf[2] + 5 if len(buf) >= 3 else None
    if fc == modbus.FC_BLOCK:
        if len(buf) < 7:
            return None
        block_len = modbus.block_response_length(buf)
        if block_len is not None:
            return block_len
        return 8
    return None


# --------------------------------------------------------------------------
# App-parity characteristic resolution + adaptive GATT discovery.
#
# Spec 010 (research 010 §1-§2, the disassembled vendor app): the app
# classifies characteristics into a notify bucket {ff02, 2216, 2a00}
# and a write bucket {ff03, 2215, 2aa6} BY UUID, property-blind — on
# the production unit (service ff01 hosting ff02+ff03) that resolves
# to notify = ff02, write = ff03, exactly the pair the runtime census
# log already showed. The historic "ff01 is the write char" reading
# was a DEX artifact (research 010 §3; FINDINGS.md §11.3 correction).
#
# The client therefore resolves BOTH roles by bucket first (the
# advertised properties are advisory only, never a disqualification),
# keeps ENUMERATING the real table after connect (logged at INFO —
# the E4' evidence), falls back to the adaptive vendor-service scan
# for unknown GATT layouts (the c8d8260 safety net), and fails with
# an error whose MESSAGE lists the real table when nothing usable
# exists.

#: One enumerated GATT characteristic (uuid/handle/properties + the
#: service that hosts it). Built by _enumerate_gatt, consumed by the
#: adaptive resolvers and the listing error messages.
GattCharInfo = namedtuple(
    "GattCharInfo", ["uuid", "handle", "properties", "service_uuid", "service_handle"]
)

# The Bluetooth base UUID suffix: 16-bit aliases are the
# 0000XXXX-0000-1000-8000-00805f9b34fb literals.
_STANDARD_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def _sixteen_bit_alias(uuid: str) -> int | None:
    """16-bit alias of a standard-base UUID, else None (full 128-bit)."""
    text = str(uuid).strip().lower()
    if len(text) == 36 and text.startswith("0000") and text[8:] == _STANDARD_UUID_SUFFIX:
        try:
            return int(text[4:8], 16)
        except ValueError:
            return None
    return None


def _is_vendor_service_uuid(uuid: str) -> bool:
    """True unless the UUID is a standard SIG service.

    Heuristic, deliberately cheap: a full 128-bit UUID without the
    Bluetooth base is vendor-specific by definition, and a 16-bit
    alias outside the 0x1800-0x18FF standard block too — that covers
    the 0xFF00-0xFFFF transparent-transport convention of the Chinese
    modules (ff00/ff01/ff02, FINDINGS §11.3) and member services such
    as 0xFE59 (Nordic UART). Standard services (GAP 0x1800, GATT
    0x1801, device info 0x180A, battery 0x180F, ...) never host the
    vendor protocol pipe, so their characteristics are never
    candidates for the fallbacks.
    """
    alias = _sixteen_bit_alias(uuid)
    return alias is None or not (0x1800 <= alias <= 0x18FF)


class AquaTempBleClient:
    """Bleak connection + single-flight Modbus request/response cycle."""

    def __init__(
        self, address: str, name: str | None = None, hass: Any | None = None
    ) -> None:
        self._address = address
        self._name = name
        self._hass = hass  # optional Home Assistant instance (device cache)
        self._client: BleakClient | None = None
        self._write_with_response = True
        # Adaptive GATT discovery (see the module-level note): resolved
        # during connect() — the known UUIDs when the device exposes
        # them, discovered fallbacks otherwise.
        self._write_uuid: str | None = None
        self._notify_uuid: str | None = None
        self._gatt_chars: list[GattCharInfo] | None = None
        self._rx = bytearray()
        self._pending: asyncio.Future[bytes] | None = None
        # Spec 008: the match spec replaces the c8d8260 expected-fc gate
        # (research 008 §3) — plus the per-request census counters (FR-4).
        self._match: _ResponseMatch | None = None
        self._req_rx_bytes = 0        # TOTAL bytes received during the request
        self._req_frame_count = 0     # complete CRC-valid frames seen
        self._req_block_starts: list[int] = []  # block starts seen (census)
        self._idle_census_logged = False  # one INFO census per connect while idle
        # Spec 009 FR-1 census timing state (plan 009 §2.1, contract 009
        # §3): _req_t0 stamps the request start; _req_first_seen/
        # _req_last_seen track the first/last census offsets rendered in
        # the timeout message; _idle_pushes collects (start, t after
        # subscribe) for the idle-window summary — the E2 push-onset
        # evidence (research 009 §1.1); _t_subscribe is stamped by
        # connect() right after the CCCD write.
        self._req_t0: float | None = None
        self._req_first_seen: float | None = None
        self._req_last_seen: float | None = None
        self._idle_pushes: list[tuple[int, float]] = []
        self._t_subscribe: float | None = None
        self._idle_summary_logged = False  # one idle summary per connection
        # Spec 011 FR-4: writes sent for the pending request (the initial
        # write + every re-send) — the `answered after pairing (<n>
        # attempt of the window)` line reads it (contract 011 §3.4).
        self._req_writes = 0
        self._silence_timer: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ state
    @property
    def address(self) -> str:
        return self._address

    @property
    def is_connected(self) -> bool:
        return self._client is not None and bool(self._client.is_connected)

    @property
    def write_uuid(self) -> str:
        """The resolved write characteristic UUID (spec 011: the security
        probe's log line names the exact pipe it tested — contract 011
        §3.1)."""
        return self._write_uuid or const.UUID_WRITE

    @property
    def last_request_write_count(self) -> int:
        """Writes sent for the most recent request (spec 011 FR-4): the
        `security probe: answered after pairing (<n> attempt of the
        window)` line reads it (contract 011 §3.4) — the initial write
        plus every re-send that went out before the answer."""
        return self._req_writes

    # ------------------------------------------------------------ connection
    def _load_retry_connector(self) -> Any | None:
        """Lazily import bleak_retry_connector; None when not installed.

        Deliberately NOT a module-level import: standalone tools and the
        test suite run without the library (no Home Assistant), while Home
        Assistant always ships it via the bluetooth integration.
        """
        try:
            import bleak_retry_connector  # lazy on purpose: absent standalone
        except ImportError:
            _LOGGER.debug(
                "bleak_retry_connector not installed; "
                "falling back to raw BleakClient (standalone mode)"
            )
            return None
        return bleak_retry_connector

    async def connect(self, max_attempts: int = 2, pair: bool = False) -> None:
        """Resolve the device, connect and subscribe to the notify characteristic.

        max_attempts is the bleak-retry-connector attempt budget: the
        coordinator keeps the historic default of 2; since spec 006 FR-3
        the config-flow connection test also passes 2 — two 20 s attempts
        plus the settle and the read fit inside the flow's 70 s budget.
        Note that ONE attempt can itself take up to 20 s:
        bleak-retry-connector hardcodes the per-attempt connect timeout
        (research 005 §3.2) — and connect() now additionally sleeps
        SETTLE_AFTER_SUBSCRIBE after subscribing (spec 006 FR-1), so
        callers' budget math must count both.

        pair (spec 011 FR-6, contract 011 §3.5): requests pairing at the
        establish call — the pair_on_connect option's threading, OFF by
        default and earned only by the connection test's X2/X3 evidence
        (quickstart 011 §2). A backend that cannot pair logs a WARNING
        and connects WITHOUT pairing: the option must never break the
        poll path (FR-7); with pair=False the behavior is
        byte-for-byte today's.

        Home Assistant path: bleak_retry_connector.establish_connection() —
        retried connects with per-failure backoff, stale bluez connection
        cleanup, already-connected device reuse and best-backend selection
        by the HA wrapper (the official pattern for all HA Bluetooth
        integrations; calling BleakClient.connect() directly is what HA's
        "without bleak-retry-connector" warning is about).

        Standalone fallback (library absent): raw BleakClient 2-attempt
        loop, identical in spirit to the historic implementation.

        Either way the REAL failure (type + message + cause chain) is
        logged before being converted to AquaTempConnectionError. A
        resolution miss raises AquaTempDeviceNotFoundError instead
        (spec 005 FR-9) — the device was never found, which is not a
        connection failure.

        Stage-timing instrumentation (spec 007 FR-1, research 007 §8):
        connect() records one monotonic timestamp per stage boundary
        and logs a "connect summary" line on success, the failing stage
        + elapsed on failure — observation only, the connect
        behaviour itself is untouched (spec 007 NFR-2).
        """
        if self.is_connected:
            return
        if pair:
            # Contract 011 §3.5: the option path's DEBUG marker — the
            # pair_on_connect evidence-gated lever (spec 011 FR-6).
            _LOGGER.debug(
                "connect with pairing requested (option pair_on_connect)"
            )
        # Stage "resolve" boundaries (spec 007 FR-1, research 007 §8):
        # t0 is connect()'s entry, t_resolve the moment the device was
        # obtained — the summary's first stage duration.
        t0 = _now()
        device = await self._resolve_device()
        t_resolve = _now()
        if device is None:
            raise AquaTempDeviceNotFoundError(
                f"no Aqua Temp device found for address {self._address}"
            )
        try:
            # Spec 011: the establish half lives in _establish_connection
            # (capability-checked pair threading, research 011 §10.1);
            # the brc/raw branches and their attempt-window INFO lines
            # (contract 007 §2.1) moved there VERBATIM — same code, same
            # log formats, one extra caller (the pairing ladder's rung B).
            client: BleakClient = await self._establish_connection(
                device, max_attempts=max_attempts, pair=pair
            )
        except Exception as exc:  # noqa: BLE001
            # Connect-stage failure (spec 007 FR-1, contract 007 §3.1,
            # research 007 §8): the elapsed is measured from connect()
            # entry; the stage is "connect" — this whole try block IS
            # the connect stage. The chain stays verbatim (spec 005 FR-2).
            elapsed = _now() - t0
            _LOGGER.error(
                "connect to %s failed at stage connect after %.1f s: %s",
                self._address,
                elapsed,
                exc_chain(exc),
            )
            raise AquaTempConnectionError(
                f"could not connect to {self._address}: {exc_chain(exc)}"
            ) from exc
        # Stage "connect" completed (spec 007 FR-1, research 007 §8):
        # link + BlueZ GATT discovery inside bleak's window are done.
        t_connect = _now()
        # Spec 011 FR-3: the post-connect half lives in _post_connect_setup
        # (contract 009 §1 order, the 007 stage/summary lines verbatim) —
        # one more caller needs it: the pairing ladder's rung B, which
        # re-establishes the link with pairing and must run the SAME
        # setup (research 011 §10.1).
        await self._post_connect_setup(
            client, device, t0=t0, t_resolve=t_resolve, t_connect=t_connect
        )

    async def _establish_connection(
        self, device: Any, *, max_attempts: int, pair: bool = False
    ) -> BleakClient:
        """The establish half of connect() — brc inside Home Assistant,
        raw BleakClient standalone (contract 007 §2.1 lines verbatim).

        Spec 011 FR-3 rung B / FR-6: pair=True requests pairing at the
        establish call when the installed backend accepts the kwarg
        (capability check with inspect.signature, research 011 §10.1 —
        never assumed across API generations); a backend that cannot
        pair logs a WARNING and connects WITHOUT pairing — the option
        must never break the poll path (contract 011 §7).
        """
        brc = self._load_retry_connector()
        pair_supported = False
        if pair:
            target = brc.establish_connection if brc is not None else BleakClient
            pair_supported = _kwargs_accepts(target, const.PAIR_ENABLE_PROP)
            if pair_supported:
                _LOGGER.debug(
                    "establish path accepts the %s kwarg (research 011 §10.1)",
                    const.PAIR_ENABLE_PROP,
                )
            else:
                _LOGGER.warning(
                    "pairing was requested (option pair_on_connect) but the "
                    "installed %s does not accept the pair parameter — "
                    "connecting WITHOUT pairing (spec 011 FR-6/FR-7; "
                    "research 011 §10.1)",
                    "bleak-retry-connector" if brc is not None else "bleak",
                )
        if brc is not None:
            # max_attempts is the caller's probe budget (2 for the
            # coordinator, 2 for the config-flow test since spec 006
            # FR-3); transient errors (bluez slots, EOF, BrokenPipe)
            # still get extra retries with backoff inside the library.
            # timeout=const.CONNECT_TIMEOUT is a CONSTRUCTOR-ONLY
            # kwarg: brc hardcodes the connect attempt to
            # BLEAK_TIMEOUT = 20.0 s (research 005 §3.2), so the
            # constant is dead for connects inside Home Assistant —
            # it only bounds the standalone raw-client path below.
            # Attempt-window transparency (spec 007 FR-2, research
            # 007 §4): max_attempts exhausted 20 s windows are the
            # arithmetic the production ">40 s" failure is made of —
            # logged BEFORE the call so a hanging connect is
            # self-explaining in our own log at INFO (contract 007
            # §2.1). The 20.0 is brc's hardcoded BLEAK_TIMEOUT,
            # deliberately not one of our constants.
            _LOGGER.info(
                "connecting to %s: up to %d connect attempt(s) of %.0f s "
                "each (bleak-retry-connector BLEAK_TIMEOUT, research 007 §4)",
                self._address,
                max_attempts,
                20.0,
            )
            kwargs: dict[str, Any] = dict(
                name=self._name or getattr(device, "name", "") or self._address,
                disconnected_callback=self._on_disconnect,
                max_attempts=max_attempts,
                timeout=const.CONNECT_TIMEOUT,
            )
            if pair and pair_supported:
                kwargs[const.PAIR_ENABLE_PROP] = True
            return await brc.establish_connection(BleakClient, device, **kwargs)
        # Attempt-window line, standalone twin (spec 007 FR-2,
        # research 007 §4): the raw fallback loops exactly twice,
        # const.CONNECT_TIMEOUT per attempt (contract 007 §2.1
        # standalone variant).
        _LOGGER.info(
            "connecting to %s: up to 2 connect attempt(s) of %.0f s "
            "each (raw bleak, const.CONNECT_TIMEOUT)",
            self._address,
            const.CONNECT_TIMEOUT,
        )
        return await self._connect_with_raw_client(
            device, pair=pair and pair_supported
        )

    async def _post_connect_setup(
        self,
        client: BleakClient,
        device: Any,
        *,
        t0: float,
        t_resolve: float,
        t_connect: float,
    ) -> None:
        """The post-connect half of connect(): enumerate → subscribe →
        write-char resolve → settle (contract 009 §1 order, spec 011 FR-3).

        Factored out so the pairing ladder's rung B can re-run the
        IDENTICAL setup after a pair-enabled re-establishment — same
        code, same 007 stage/summary log formats (spec 011 FR-7:
        byte-identical, still greppable). Raises on failure after
        tearing the half-open client down, exactly as connect() always
        did; connect() wraps the establish half, this owns the rest.
        """
        self._client = client
        # Post-connect stage boundaries (spec 007 FR-1, contract 007
        # §3.2): None until completed, so the failure handler can name
        # the stage that was in flight — never a duration for an
        # incomplete stage (plan 007 §1.2).
        t_notify: float | None = None
        t_write_char: float | None = None
        try:
            # Adaptive GATT discovery: ONE walk of the device's services
            # logs the REAL table (UUID, handle, properties — the whole
            # point: the unit's layout was never known, only assumed) and
            # feeds the fallback resolution below. Best-effort: a
            # non-enumerable services object logs at DEBUG and leaves
            # the direct UUID lookups working.
            self._gatt_chars = self._enumerate_gatt(client)
            self._resolve_notify_characteristic(client)
            await client.start_notify(
                self._notify_uuid or const.UUID_NOTIFY, self._on_notify
            )
            t_notify = _now()
            # Spec 009 FR-1 (plan 009 §2.2): remember the subscribe moment
            # and reset the idle-window state — the settle pushes below
            # land in _idle_pushes with offsets relative to this stamp
            # (contract 009 §3, research 009 §1.1 E2). No notification can
            # be lost to this reset: nothing is awaited between here and
            # the settle sleep, so the handler cannot interleave.
            self._t_subscribe = t_notify
            self._idle_pushes = []
            self._idle_summary_logged = False
            self._resolve_write_characteristic(client)
            t_write_char = _now()
            # Vendor parity (research 006 §2 step 4; proven again by
            # the app disassembly, research 010 §2): the app order is
            # subscribe ff02 → 2 s → write ff03 → listen — give the
            # module a beat to route ff02 notifications after the
            # CCCD write before the first Modbus frame (spec 006 FR-1;
            # the proven sequence must not be "fixed" later). Inside
            # this try block on purpose — a setup failure here must
            # tear down the half-open client like any other (a
            # cancelled sleep is handled by the caller's task teardown,
            # spec 005's cancellation contract).
            await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)
            t_settle = _now()
        except AquaTempError as exc:
            # Link is up but the characteristic resolution failed: tear
            # down so the next connect() starts from a clean state. The
            # same stage+elapsed ERROR line as the generic branch below
            # (spec 007 §3.2 contract) — the resolution failures now
            # carry the diagnosis (the real GATT table rides in the
            # exception message), so they must not stay silent.
            stage = "notify" if t_notify is None else "write-char"
            _LOGGER.error(
                "post-connect setup (ff02 notify / ff01 lookup) on %s "
                "failed at stage %s after %.1f s: %s",
                self._address,
                stage,
                _now() - t0,
                exc_chain(exc),
            )
            await self.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001
            # Post-connect setup failure (spec 007 FR-1, contract 007
            # §3.2, research 007 §8): the stage is the one in flight,
            # derived from the completed boundaries only.
            if t_notify is None:
                stage = "notify"
            elif t_write_char is None:
                stage = "write-char"
            else:
                stage = "settle"
            elapsed = _now() - t0
            _LOGGER.error(
                "post-connect setup (ff02 notify / ff01 lookup) on %s "
                "failed at stage %s after %.1f s: %s",
                self._address,
                stage,
                elapsed,
                exc_chain(exc),
            )
            await self.disconnect()
            raise AquaTempConnectionError(
                f"could not connect to {self._address}: {exc_chain(exc)}"
            ) from exc
        # Connect summary (spec 007 FR-1, contract 007 §2.2, research
        # 007 §8): ONE greppable line decomposing the whole connect —
        # the runbook's step-1 evidence. Stage order and format are
        # normative; every duration comes from completed stages only
        # and the total is their sum. The parity line below is kept
        # verbatim (other code greps it); the summary precedes it.
        _LOGGER.info(
            "connect summary %s: total %.1f s (resolve %.2f, connect %.1f, "
            "notify %.2f, write-char %.2f, settle %.1f)",
            self._address,
            t_settle - t0,
            t_resolve - t0,
            t_connect - t_resolve,
            t_notify - t_connect,
            t_write_char - t_notify,
            t_settle - t_write_char,
        )
        _LOGGER.info(
            "Connected to %s (%s)",
            self._address,
            self._name or getattr(device, "name", "") or "",
        )

    async def _connect_with_raw_client(
        self, device: Any, pair: bool = False
    ) -> BleakClient:
        """Standalone fallback: raw BleakClient with the historic 2-attempt
        loop. Used only when bleak_retry_connector is not installed
        (standalone tools / tests, no Home Assistant).

        Spec 011 FR-3 rung B / FR-6: pair=True flows into the bleak
        constructor when this bleak generation accepts it (the caller
        capability-checked — research 011 §10.1).
        """
        last_exc: Exception | None = None
        for attempt in (1, 2):
            ctor_kwargs: dict[str, Any] = dict(
                disconnected_callback=self._on_disconnect,
                timeout=const.CONNECT_TIMEOUT,
            )
            if pair:
                ctor_kwargs[const.PAIR_ENABLE_PROP] = True
            client = BleakClient(device, **ctor_kwargs)
            try:
                # Constructor timeout is the canonical path in bleak
                # >= 0.21; the kwarg is kept for older bleak, which reads
                # it from connect() instead.
                await client.connect(timeout=const.CONNECT_TIMEOUT)
                return client
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _LOGGER.warning(
                    "connect attempt %d/2 to %s failed: %s: %s",
                    attempt,
                    self._address,
                    type(exc).__name__,
                    exc,
                )
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        assert last_exc is not None  # the loop always runs twice
        raise last_exc

    async def disconnect(self) -> None:
        """Idempotent disconnect; clears all pending request state."""
        self._cancel_silence_timer()
        self._fail_pending(AquaTempError("client disconnected"))
        client = self._client
        self._client = None
        self._rx.clear()
        # Spec 008 census state (contract 008-B §7 invariant 4): teardown
        # clears the match spec, the counters and the once-per-connect
        # idle census flag.
        self._match = None
        self._req_rx_bytes = 0
        self._req_frame_count = 0
        self._req_block_starts = []
        self._idle_census_logged = False
        # Spec 009 FR-1 (contract 009 §3, contract 008-B §7): clear the
        # census timing and idle-window state with the 008 census — a
        # fresh connect starts a fresh idle window.
        self._req_t0 = None
        self._req_first_seen = None
        self._req_last_seen = None
        self._idle_pushes = []
        self._t_subscribe = None
        self._idle_summary_logged = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("disconnect error (ignored): %s", exc)

    def _on_disconnect(self, _client: BleakClient) -> None:
        """bleak disconnected_callback — runs in the event loop thread.

        bleak fires this callback on ANY disconnect transition, including
        our own clean disconnect() (research 004 §5), so distinguish the
        two: our disconnect() clears self._client BEFORE awaiting bleak's
        disconnect and has already cancelled the silence timer and failed
        any pending request — an expected close, reported at DEBUG. An
        unexpected link loss still warns and fails the in-flight read
        immediately (unchanged behaviour).
        """
        if self._client is None:
            # Self-initiated close (specs/004 FR-6): timer and pending
            # request were already handled by disconnect().
            _LOGGER.debug("BLE connection to %s closed", self._address)
            return
        _LOGGER.warning("BLE connection to %s lost", self._address)
        self._cancel_silence_timer()
        self._fail_pending(AquaTempConnectionError(f"connection to {self._address} lost"))

    # -------------------------------------------- pairing ladder (spec 011)
    async def pair(self) -> str:
        """The pairing ladder (spec 011 FR-3, contract 011 §3).

        Rungs, least intrusive first, each logged (research 011 §10.1):
          A — the bleak client's pair() method (the bleak < 1.0 shape);
          B — re-establish the connection with pairing requested
              (brc establish_connection(pair=True) / BleakClient(pair=True),
              the bleak >= 1.0 shape) + the normal post-connect setup;
          C — register the minimal NoInputNoOutput BlueZ agent (module
              agent.py over dbus_fast, bleak's own D-Bus dependency) and
              retry the taken rung exactly once — the documented fix for
              the agent-less bluetoothd Pair() failure (research 011
              §10.2/§3.3).

        Returns one of "succeeded" | "skipped" (already bonded) |
        "failed" | "unavailable" and NEVER raises: a pairing failure
        must not break the caller (contract 011 §2/§7 — the config test
        routes on the status and the retry read follows success/skipped
        only, FR-4). The bond state is logged before/after (best-effort,
        contract 011 §3.6). Only ever called from the config-test stages
        or behind the pair_on_connect option — never from a poll loop
        (contract 011 §1.5). Evidence lines are WARNING (contract 012
        §2): the operator's HA log page keeps only WARNING+.
        """
        bleak_version = _package_version("bleak")
        brc = self._load_retry_connector()
        brc_version = (
            _package_version("bleak-retry-connector") if brc is not None else None
        )
        # WARNING (contract 012 §2): the bond-state lines are decisive-once
        # evidence — the operator's system_log panel stores WARNING+ only
        # (research 012 §4).
        _LOGGER.warning("pairing: bond state before: %s", await self._read_bond_state())
        pair_method = None
        if self._client is not None:
            candidate = getattr(self._client, "pair", None)
            pair_method = candidate if callable(candidate) else None
        chain = ""
        if pair_method is not None:
            # WARNING (contract 012 §2): the rung line is evidence the
            # routing decision reads (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: attempting via rung A (bleak pair method) "
                "(bleak=%s, brc=%s)",
                bleak_version,
                brc_version or "absent",
            )
            result, chain = await self._call_pair_method_once(pair_method)
        else:
            # WARNING (contract 012 §2): the rung line is evidence the
            # routing decision reads (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: attempting via rung B (reconnect with pair=True) "
                "(bleak=%s, brc=%s)",
                bleak_version,
                brc_version or "absent",
            )
            result, chain = await self._pair_via_reconnect()
        if result == "failed" and _pairing_agent_signature(chain):
            result, chain = await self._rung_c_retry(
                pair_method,
                bleak_version=bleak_version,
                brc_version=brc_version,
            )
        # WARNING (contract 012 §2): the bond-state lines are decisive-once
        # evidence — the operator's system_log panel stores WARNING+ only
        # (research 012 §4).
        _LOGGER.warning("pairing: bond state after: %s", await self._read_bond_state())
        return result

    async def _call_pair_method_once(self, pair_method: Any) -> tuple[str, str]:
        """Rung A: ONE bounded bleak pair() call (const.PAIR_TIMEOUT — a
        D-Bus Pair() can legitimately take seconds, research 011 §10.1).
        The failure chain is logged verbatim (the §5.2 signatures are
        read from it); AlreadyExists-class outcomes are the X3 no-op.
        """
        try:
            await asyncio.wait_for(pair_method(), const.PAIR_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — the chain IS the datum
            chain = exc_chain(exc)
            if _pairing_already_bonded(chain.lower()):
                # WARNING (contract 012 §2): the X3 no-op proof is evidence
                # — the panel keeps WARNING+ only (research 012 §4).
                _LOGGER.warning("pairing: skipped: already bonded")
                return "skipped", chain
            # WARNING (contract 012 §2): the failure chain is evidence the
            # routing decision reads (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: failed: %s%s", chain, _pairing_ble_guidance(chain)
            )
            return "failed", chain
        # WARNING (contract 012 §2): the decisive success line (research
        # 012 §4 — the panel keeps WARNING+ only).
        _LOGGER.warning("pairing: succeeded")
        return "succeeded", ""

    async def _pair_via_reconnect(self) -> tuple[str, str]:
        """Rung B: tear down, re-establish with pairing requested, then
        re-run the normal post-connect setup (spec 011 FR-3 rung 2,
        research 011 §10.1 — bleak >= 1.0 moved pairing into the
        establish call). The capability check runs BEFORE any teardown:
        an incapable backend is 'unavailable', never a broken link.
        """
        brc = self._load_retry_connector()
        supported = _kwargs_accepts(
            brc.establish_connection if brc is not None else BleakClient,
            const.PAIR_ENABLE_PROP,
        )
        if not supported:
            reason = (
                "the installed bleak-retry-connector does not accept the "
                "pair parameter"
                if brc is not None
                else "this bleak build exposes neither a pair method nor "
                "a pair constructor parameter"
            )
            # WARNING (contract 012 §2): an unavailable rung is decisive
            # evidence (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: unavailable: %s (research 011 §10.1)", reason
            )
            return "unavailable", ""
        try:
            await self.disconnect()
            t_b = _now()
            device = await self._resolve_device()
            t_resolve = _now()
            if device is None:
                raise AquaTempDeviceNotFoundError(
                    f"no Aqua Temp device found for address {self._address}"
                )
            client = await self._establish_connection(device, max_attempts=2, pair=True)
            t_connect = _now()
            await self._post_connect_setup(
                client, device, t0=t_b, t_resolve=t_resolve, t_connect=t_connect
            )
        except Exception as exc:  # noqa: BLE001 — the chain stays verbatim
            chain = exc_chain(exc)
            if _pairing_already_bonded(chain.lower()):
                # WARNING (contract 012 §2): the X3 no-op proof is evidence
                # — the panel keeps WARNING+ only (research 012 §4).
                _LOGGER.warning("pairing: skipped: already bonded")
                return "skipped", chain
            # WARNING (contract 012 §2): the failure chain is evidence the
            # routing decision reads (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: failed: %s%s", chain, _pairing_ble_guidance(chain)
            )
            return "failed", chain
        # WARNING (contract 012 §2): the decisive success line (research
        # 012 §4 — the panel keeps WARNING+ only).
        _LOGGER.warning("pairing: succeeded")
        return "succeeded", ""

    async def _rung_c_retry(
        self, pair_method: Any, *, bleak_version: str, brc_version: str | None
    ) -> tuple[str, str]:
        """Rung C: register the minimal BlueZ agent, retry the taken rung
        exactly once, always release the agent in the finally (contract
        011 §3/§7). Import and registration are guarded — an ImportError
        or a failed registration is 'unavailable', never an exception
        (AC-8; research 011 §10.2).
        """
        # WARNING (contract 012 §2): the rung line is evidence the routing
        # decision reads (research 012 §4 — panel = WARNING+).
        _LOGGER.warning(
            "pairing: attempting via rung C (minimal BlueZ agent, "
            "NoInputNoOutput, then one rung retry) (bleak=%s, brc=%s)",
            bleak_version,
            brc_version or "absent",
        )
        bluez_agent = None
        try:
            try:
                from . import agent as bluez_agent  # package (Home Assistant)
            except ImportError:
                import agent as bluez_agent  # standalone (tools/tests)
        except ImportError:  # pragma: no cover — agent.py is stdlib-only
            bluez_agent = None
        if bluez_agent is None or not getattr(
            bluez_agent, "DBUS_FAST_AVAILABLE", False
        ):
            _LOGGER.debug(
                "rung C unavailable: the agent module/dbus_fast import "
                "failed (spec 011 FR-3)"
            )
            # WARNING (contract 012 §2): the unavailable verdict is decisive
            # evidence (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: unavailable: no pairing agent could be "
                "registered (dbus_fast not available)"
            )
            return "unavailable", ""
        bus = await bluez_agent.register_agent()
        if bus is None:
            # WARNING (contract 012 §2): the unavailable verdict is decisive
            # evidence (research 012 §4 — panel = WARNING+).
            _LOGGER.warning(
                "pairing: unavailable: agent registration failed on this "
                "bluetoothd"
            )
            return "unavailable", ""
        try:
            if pair_method is not None:
                return await self._call_pair_method_once(pair_method)
            return await self._pair_via_reconnect()
        finally:
            await bluez_agent.unregister_agent(bus)

    async def _read_bond_state(self) -> str:
        """Best-effort BlueZ Paired property read (contract 011 §3.6,
        research 011 §10.1): 'paired', 'not paired', or 'unknown'.

        Reaches through the bleak backend's device path and D-Bus bus
        (both introspected, never assumed); any failure — no backend,
        no path, no bus, dbus_fast absent, a D-Bus error — degrades to
        'unknown' with a DEBUG line: the bond check is diagnostics,
        never a gate (contract 011 §7)."""
        client = self._client
        if client is None:
            return "unknown"
        try:
            backend = getattr(client, "_backend", None) or client
            path = getattr(backend, "_device_path", None)
            bus = getattr(backend, "_bus", None)
            if not path or bus is None:
                return "unknown"
            message_cls = _dbus_fast_message()
            if message_cls is None:
                return "unknown"
            reply = await bus.call(
                message_cls(
                    destination="org.bluez",
                    path=path,
                    interface="org.freedesktop.DBus.Properties",
                    member="Get",
                    signature="ss",
                    body=["org.bluez.Device1", "Paired"],
                )
            )
            value = (
                reply.body[0]
                if reply and getattr(reply, "body", None)
                else None
            )
            if hasattr(value, "value"):  # dbus-fast Variant shape
                value = value.value
            return "paired" if value else "not paired"
        except Exception as exc:  # noqa: BLE001 — diagnostics only
            _LOGGER.debug(
                "bond state read failed: %s: %s", type(exc).__name__, exc
            )
            return "unknown"

    # --------------------------------------------- adaptive GATT discovery
    @staticmethod
    def _char_props(obj: Any) -> list[str]:
        """Characteristic properties as a plain list, robust to bleak
        versions exposing a set/tuple and to objects without any."""
        props = getattr(obj, "properties", None)
        if isinstance(props, (list, tuple, set, frozenset)):
            return [str(p) for p in props]
        return []

    @staticmethod
    def _iter_service_characteristics(service: Any) -> list[Any]:
        """Characteristics of one service, robust across bleak versions.

        bleak >= 0.19 exposes service.characteristics as a handle-keyed
        collection (mapping.values()); some builds and stand-ins expose
        a plain iterable. Both are supported; anything else yields [].
        """
        chars_obj = getattr(service, "characteristics", None)
        if chars_obj is None:
            return []
        values = getattr(chars_obj, "values", None)
        if callable(values):
            try:
                return list(values())
            except Exception:  # noqa: BLE001
                pass
        try:
            return list(chars_obj)
        except Exception:  # noqa: BLE001
            return []

    def _lookup_char(self, client: BleakClient, uuid: str) -> Any | None:
        """One characteristic by UUID; None when absent or the table is
        broken (the enumeration below then reports what it could walk)."""
        try:
            return client.services.get_characteristic(uuid)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "characteristic lookup %s failed: %s: %s",
                uuid,
                type(exc).__name__,
                exc,
            )
            return None

    def _enumerate_gatt(self, client: BleakClient) -> list[GattCharInfo] | None:
        """Walk the device's services and log the full GATT table.

        ONE connect must be enough to see the REAL layout — service and
        characteristic UUIDs, handles, properties — at INFO:

            GATT enumeration <addr>: 2 service(s), 4 characteristic(s)
            GATT enumeration <addr>: service <uuid> (handle <h>): 3 char(s)
            GATT enumeration <addr>:   char <uuid> (handle <h>) properties [...]

        Best-effort by design (never raises): a non-enumerable services
        object — old bleak builds, exotic backends, test stand-ins —
        logs at DEBUG and yields None, leaving the direct UUID lookups
        working. Feeds _resolve_notify_characteristic /
        _resolve_write_characteristic and the listing error messages.
        """
        services_obj = getattr(client, "services", None)
        if services_obj is None:
            _LOGGER.debug(
                "GATT enumeration %s: no services object on the client",
                self._address,
            )
            return None
        collected: list[tuple[str, Any, list[Any]]] = []
        chars: list[GattCharInfo] = []
        try:
            for service in services_obj:
                svc_uuid = str(getattr(service, "uuid", "") or "").lower()
                svc_handle = getattr(service, "handle", None)
                svc_chars = self._iter_service_characteristics(service)
                collected.append((svc_uuid, svc_handle, svc_chars))
                for char in svc_chars:
                    chars.append(
                        GattCharInfo(
                            str(getattr(char, "uuid", "") or "").lower(),
                            getattr(char, "handle", None),
                            self._char_props(char),
                            svc_uuid,
                            svc_handle,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "GATT enumeration %s failed: %s: %s",
                self._address,
                type(exc).__name__,
                exc,
            )
            return chars or None
        _LOGGER.info(
            "GATT enumeration %s: %d service(s), %d characteristic(s)",
            self._address,
            len(collected),
            len(chars),
        )
        for svc_uuid, svc_handle, svc_chars in collected:
            _LOGGER.info(
                "GATT enumeration %s: service %s (handle %s): %d char(s)",
                self._address,
                svc_uuid,
                svc_handle,
                len(svc_chars),
            )
            for char in svc_chars:
                _LOGGER.info(
                    "GATT enumeration %s:   char %s (handle %s) properties %s",
                    self._address,
                    str(getattr(char, "uuid", "") or "").lower(),
                    getattr(char, "handle", None),
                    self._char_props(char),
                )
        return chars or None

    def _format_gatt_table(self) -> str:
        """Compact one-line rendering of the enumerated table.

        Embedded in the resolution failure MESSAGES so one log line is
        enough to hard-code the right UUID on the next iteration — no
        new probe round needed.
        """
        chars = self._gatt_chars
        if not chars:
            return (
                "GATT table unavailable: the device's services could not "
                "be enumerated"
            )
        services: list[str] = []
        rendered: dict[str, list[str]] = {}
        for char in chars:
            if char.service_uuid not in rendered:
                rendered[char.service_uuid] = []
                services.append(char.service_uuid)
            handle = f" (handle {char.handle})" if char.handle is not None else ""
            rendered[char.service_uuid].append(
                f"char {char.uuid}{handle} properties {char.properties}"
            )
        parts = []
        for svc_uuid in services:
            svc_handle = next(
                c.service_handle for c in chars if c.service_uuid == svc_uuid
            )
            service_desc = (
                f"service {svc_uuid} (handle {svc_handle})"
                if svc_handle is not None
                else f"service {svc_uuid}"
            )
            parts.append(f"{service_desc}: [{', '.join(rendered[svc_uuid])}]")
        return "GATT table: " + "; ".join(parts)

    def _find_write_candidate(self) -> GattCharInfo | None:
        """Fallback step (b): first writable char of a vendor service.

        Ordered pools, first match wins (enumeration order = handle
        order, so 'first' is also 'lowest handle'):
          1. vendor-service chars with write/write-without-response in
             a service that ALSO exposes notify/indicate — a
             request/response pipe looks like this;
          2. any vendor-service writable char.
        Standard SIG services (GAP, GATT, battery, ...) are never
        candidates (_is_vendor_service_uuid).
        """
        chars = self._gatt_chars
        if not chars:
            return None
        writable = [
            c
            for c in chars
            if ("write" in c.properties or "write-without-response" in c.properties)
        ]
        vendor = [c for c in writable if _is_vendor_service_uuid(c.service_uuid)]
        notify_services = {
            c.service_uuid
            for c in chars
            if ("notify" in c.properties or "indicate" in c.properties)
        }
        for pool in (
            [c for c in vendor if c.service_uuid in notify_services],
            vendor,
        ):
            if pool:
                return pool[0]
        return None

    def _set_write_mode_from_properties(self, uuid: str, props: list[str]) -> None:
        """App-parity write mode (spec 010 FR-3, research 010 §2.3).

        The vendor app's write wrapper is the ONLY write path in its
        whole Dart binary and uses exactly ONE WriteType instance:
        withoutResponse (no withResponse exists in the object pool at
        all). So without-response is the default, and the only legal
        divergence is the ATT-layer constraint: a characteristic
        advertising ONLY 'write' forbids Write Commands, so it must be
        written with response. A characteristic with NO write property
        at all is still written without response — the app,
        property-blind (research 010 §1), would do exactly that — with
        a WARNING naming the anomaly. Properties are advisory: never a
        refusal, never a re-pick (FR-2 item 2).
        """
        if "write-without-response" in props:
            # App parity: the app always writes WITHOUT response
            # (WriteType.withoutResponse, research 006 §2.1 / 010 §2.3).
            self._write_with_response = False
        elif "write" in props:
            # ATT constraint (FR-3): a 'write'-only characteristic
            # cannot take Write Commands — with-response is the only
            # legal mode; the WARNING makes the divergence visible.
            _LOGGER.warning(
                "write characteristic %s forbids write-without-response "
                "(properties=%s); using write-with-response",
                uuid,
                props,
            )
            self._write_with_response = True
        else:
            # App parity (FR-2 item 2 / FR-3): write anyway — the app
            # picks by UUID and never reads properties first; a GATT
            # refusal would belong to the write itself, on the wire.
            _LOGGER.warning(
                "write characteristic %s advertises no write property "
                "(properties=%s) — app parity: writing anyway",
                uuid,
                props,
            )
            self._write_with_response = False

    def _log_property_advisory(self, role: str, uuid: str, props: list[str]) -> None:
        """FR-2 item 2 advisory for the NOTIFY bucket pass (spec 010).

        The app is property-blind: when a bucket characteristic lacks
        the role's property, WARN — never refuse, never re-pick. The
        WRITE role's advisories are emitted by
        _set_write_mode_from_properties together with the mode
        decision (single authority, never logged twice).
        """
        if role != "notify":
            return  # write advisories live in _set_write_mode_from_properties
        if "notify" in props or "indicate" in props:
            return
        _LOGGER.warning(
            "notify characteristic %s advertises no notify property "
            "(properties=%s) — app parity: subscribing anyway",
            uuid,
            props,
        )

    def _resolve_from_buckets(
        self, client: BleakClient, buckets: Sequence[str], role: str
    ) -> tuple[str, list[str]] | None:
        """App-parity bucket pass (spec 010 FR-2 item 1, research 010 §1).

        Walks the role's UUID bucket tuple in order and returns the
        first UUID the device exposes as a characteristic, with its
        advertised properties — the vendor app's own rule: its
        discovery classifier (closure 0xa146dc) stores the matched
        characteristic objects (state fields 0x2b notify / 0x2f write)
        from the UUID match alone, never reading the properties first.
        Works through direct UUID lookups, so it needs no enumerable
        services object. Properties are ADVISORY only (the advisory
        WARNING fires here for notify; the write mode + its advisories
        are decided by _set_write_mode_from_properties).
        """
        for uuid in buckets:
            char = self._lookup_char(client, uuid)
            if char is not None:
                props = self._char_props(char)
                self._log_property_advisory(role, uuid, props)
                return uuid, props
        return None

    def _resolve_notify_characteristic(self, client: BleakClient) -> None:
        """Pick the notify characteristic: the app's UUID bucket first,
        then the adaptive fallbacks (spec 010 FR-2).

        Strategy, in order:
          a. the app's notify bucket (UUID_NOTIFY_BUCKETS = ff02, 2216,
             2a00 — research 010 §1.1/§1.3), property-BLIND: the first
             UUID the device exposes wins, exactly as the vendor app's
             discovery classifier stores its notify char (state field
             0x2b). A bucket char advertising no notify property is
             subscribed ANYWAY with a WARNING (FR-2 item 2) — the app
             never reads properties here;
          b. a notify/indicate characteristic of the SAME service as
             the write candidate (the vendor data service), else of any
             vendor-specific service — standard SIG services (battery
             2a19, ...) are never candidates: subscribing to them
             would deliver non-Modbus chunks forever;
          c. explicit AquaTempConnectionError whose MESSAGE lists the
             real table.
        The classic ff02 pick is silent (byte-identical to the historic
        behaviour); any other choice is announced at INFO: 'using
        notify characteristic <uuid> (properties=...)'. Runs BEFORE
        start_notify so a resolution failure precedes any subscription.
        """
        bucket = self._resolve_from_buckets(client, const.UUID_NOTIFY_BUCKETS, "notify")
        if bucket is not None:
            uuid, _props = bucket
            self._notify_uuid = uuid
            if uuid != const.UUID_NOTIFY:
                _LOGGER.info(
                    "using notify characteristic %s (properties=%s) on %s",
                    uuid,
                    _props,
                    self._address,
                )
            return
        chars = self._gatt_chars or []
        candidates = [
            c for c in chars if ("notify" in c.properties or "indicate" in c.properties)
        ]
        write_candidate = self._find_write_candidate()
        write_service = (
            write_candidate.service_uuid if write_candidate is not None else None
        )
        for pool in (
            [c for c in candidates if c.service_uuid == write_service],
            [c for c in candidates if _is_vendor_service_uuid(c.service_uuid)],
        ):
            if pool:
                self._notify_uuid = pool[0].uuid
                _LOGGER.info(
                    "using notify characteristic %s (properties=%s) on %s",
                    pool[0].uuid,
                    pool[0].properties,
                    self._address,
                )
                return
        raise AquaTempConnectionError(
            "no notify-capable characteristic on the device "
            f"(known UUID {const.UUID_NOTIFY} absent; {self._format_gatt_table()})"
        )

    def _resolve_write_characteristic(self, client: BleakClient) -> None:
        """Pick the write characteristic: the app's UUID bucket first,
        then the adaptive fallbacks (spec 010 FR-2/FR-3).

        Strategy, in order:
          a. the app's write bucket (UUID_WRITE_BUCKETS = ff03, 2215,
             2aa6 — research 010 §1.1/§1.3), property-BLIND, first
             match wins: the vendor app's discovery classifier stores
             its write char (state field 0x2f) from the UUID match
             alone, and its sender REFUSES to run without it (research
             010 §2.2). The write mode then follows FR-3
             (without-response unless the char advertises only
             'write'); a bucket char with no write property at all is
             written anyway, with a WARNING — the app would do the same;
          b. a vendor-service writable characteristic
             (_find_write_candidate) — the unchanged c8d8260 adaptive
             safety net for GATT layouts outside the app's buckets: a
             characteristic ff01 can only ever be picked HERE (the app
             never selects it, research 010 §1.3);
          c. explicit AquaTempConnectionError whose MESSAGE lists the
             real table.
        The choice is ALWAYS announced at INFO: 'using write
        characteristic <uuid> (properties=...)' — the E4' evidence
        line (quickstart 010 §0); on the production unit it reads
        byte-identical to the ce41aa5 census line.
        """
        # (a) App-parity bucket pass — direct UUID lookups, so it works
        # even when the table cannot be enumerated.
        bucket = self._resolve_from_buckets(client, const.UUID_WRITE_BUCKETS, "write")
        if bucket is not None:
            uuid, props = bucket
            self._set_write_mode_from_properties(uuid, props)
            self._write_uuid = uuid
            _LOGGER.info(
                "using write characteristic %s (properties=%s) on %s",
                uuid,
                props,
                self._address,
            )
            return
        # (b) Vendor-service writable characteristic (the adaptive
        # safety net for unknown layouts — unchanged).
        candidate = self._find_write_candidate()
        if candidate is not None:
            self._set_write_mode_from_properties(candidate.uuid, candidate.properties)
            self._write_uuid = candidate.uuid
            _LOGGER.info(
                "using write characteristic %s (properties=%s) on %s",
                candidate.uuid,
                candidate.properties,
                self._address,
            )
            return
        # (c) Nothing usable: explicit error carrying the real table.
        raise AquaTempConnectionError(
            "no usable write characteristic on the device "
            f"(known UUID {const.UUID_WRITE} absent; {self._format_gatt_table()})"
        )

    async def _resolve_device(self) -> Any | None:
        """Find the BLE device for the configured address.

        HA mode (hass is not None): explicit Home Assistant bluetooth APIs,
        in HA's own sanctioned order (plan 005 §1.1, contract
        device-resolution.md §2):
          tier 1: connectable manager history (the config-flow device
                  list's source of truth — research 005 §2.2),
          tier 2: non-connectable history (HA's own fallback order —
                  research 005 §3.1; logged: connection may fail),
          tier 3: name-prefix match over the connectable discovery
                  history (the MAC-may-have-changed case).
        Every tier is an instant cache read — a scan is NEVER started
        inside Home Assistant; the historic raw bleak calls were silently
        monkeypatched into cache lookups by the HA bluetooth manager and
        their log texts lied about scanning (research 005 §3.1).

        Standalone mode (hass is None): raw bleak lookups only —
        find_device_by_address, then discover + name-prefix filter —
        byte-identical to the historic implementation (NFR-4).

        Every tier/step logs its provenance (FR-3) so a listed-but-
        unconnectable device becomes diagnosable in one run.
        """
        if self._hass is not None:
            try:
                # Lazy import on purpose: standalone tools/tests run without
                # Home Assistant; inside HA the symbols always exist.
                from homeassistant.components.bluetooth import (
                    async_ble_device_from_address,
                    async_discovered_service_info,
                )
            except ImportError:
                _LOGGER.debug("HA bluetooth helper not available (standalone mode)")
            else:
                # Tier 1: the manager's connectable history.
                device = async_ble_device_from_address(
                    self._hass, self._address, connectable=True
                )
                if device is not None:
                    _LOGGER.debug(
                        "%s: resolved from HA connectable cache", self._address
                    )
                    return device
                # Tier 2: HA's own fallback order — the address was only
                # seen by scanners that cannot make GATT connections.
                device = async_ble_device_from_address(
                    self._hass, self._address, connectable=False
                )
                if device is not None:
                    _LOGGER.warning(
                        "%s: only visible via a non-connectable scanner; "
                        "the connection may fail",
                        self._address,
                    )
                    return device
                # Tier 3: the MAC-may-have-changed case — a name-prefix
                # match over the connectable discovery history (the same
                # effective data the old patched BleakScanner.discover()
                # read; research 005 §3.1).
                _LOGGER.warning(
                    "%s: not found in the HA bluetooth cache; looking for a "
                    "name-prefix match (the MAC may have changed)",
                    self._address,
                )
                matches = [
                    info
                    for info in async_discovered_service_info(
                        self._hass, connectable=True
                    )
                    if (info.name or "").lower().startswith(const.NAME_PREFIXES)
                ]
                if not matches:
                    return None
                if len(matches) > 1:
                    _LOGGER.warning(
                        "multiple Aqua Temp candidates found: %s",
                        [(info.name, info.address) for info in matches],
                    )
                chosen = matches[0]
                if chosen.address.lower() != self._address.lower():
                    _LOGGER.warning(
                        "Device found at a DIFFERENT address %s (configured: %s). "
                        "The MAC has probably changed — please re-configure the "
                        "integration with the new address. Using the discovered "
                        "device for this session.",
                        chosen.address,
                        self._address,
                    )
                return chosen.device
        # Standalone mode (hass is None, or the HA bluetooth helper is
        # absent): the ONLY path that starts raw bleak lookups (FR-8).
        # 1. bleak direct lookup by address
        try:
            device = await BleakScanner.find_device_by_address(self._address, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("find_device_by_address failed: %s", exc)
            device = None
        if device is not None:
            return device
        # 2. scan by name prefix (MAC may have changed across reboots)
        _LOGGER.warning(
            "address %s not found directly; scanning by name prefix %s ...",
            self._address,
            const.NAME_PREFIXES,
        )
        try:
            devices = await BleakScanner.discover(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("BleakScanner.discover failed: %s", exc)
            return None
        matches = [
            d for d in devices if (d.name or "").lower().startswith(const.NAME_PREFIXES)
        ]
        if not matches:
            return None
        if len(matches) > 1:
            _LOGGER.warning(
                "multiple Aqua Temp candidates found: %s",
                [(d.name, d.address) for d in matches],
            )
        chosen = matches[0]
        if chosen.address.lower() != self._address.lower():
            _LOGGER.warning(
                "Device found at a DIFFERENT address %s (configured: %s). The MAC "
                "has probably changed — please re-configure the integration with "
                "the new address. Using the discovered device for this session.",
                chosen.address,
                self._address,
            )
        return chosen

    # -------------------------------------------------------- request/response
    async def _write_frame(
        self, frame: bytes, *, force_response: bool = False
    ) -> None:
        """Transport seam — send a raw frame on the resolved write
        characteristic (ff03 on the documented units — the app's write
        bucket head, research 010 §1; an adaptively discovered UUID
        otherwise). (Overridden in tests; the overrides accept the
        force_response kwarg since spec 011 — tasks 011 §3.)

        Spec 011 FR-1: force_response=True writes the SAME bytes as an
        ATT Write Request (response=True) — the acknowledged twin that
        turns the unit's silent refusal into an ATT error (research 011
        §2.1: a Write Command has no error channel by Core-Spec design;
        the probe is the X1 experiment, research 011 §12). The byte
        payload is NEVER touched by this flag (AC-2: the frame
        allowlist and the vendor parity stand)."""
        await self._client.write_gatt_char(
            self._write_uuid or const.UUID_WRITE,
            bytes(frame),
            response=True if force_response else self._write_with_response,
        )

    async def _request(
        self,
        frame: bytes,
        match: _ResponseMatch,
        timeout: float,
        *,
        resend_every: float | None = None,
        resend_max: int = 0,
        force_response: bool = False,
    ) -> bytes:
        """Send one frame and await the complete, matching response.

        The match spec (contract 008-B §1) is the ONLY authority on what
        may complete the request — the c8d8260 expected-fc gate is gone
        (research 008 §3). Non-matching frames are census-logged and
        never fatal (FR-2).

        Spec 009 FR-2 (contract 009 §5): when resend_every is set and
        resend_max > 0, a background loop re-sends the IDENTICAL frame
        every resend_every seconds, up to resend_max extra writes — all
        inside the SAME timeout window (single-flight untouched: one
        pending request; the loop only re-writes). Used only by the
        config-flow connection test (NFR-3).

        Spec 011 FR-1: force_response=True writes the frame as an
        acknowledged ATT Write Request — the security probe's seam
        (research 011 §2.1). A failure of the acknowledged write raises
        AquaTempConnectionError("acknowledged write failed: <chain>") —
        a NEW, distinguishable message carrying the chain verbatim so
        the ATT error text survives to the log and the verdict
        classifier (contract 011 §5.1); the without-response wrapper
        below stays byte-identical (spec 005).
        """
        if self._pending is not None and not self._pending.done():
            raise AquaTempError("another request is already in flight")
        if not self.is_connected:
            raise AquaTempConnectionError("not connected")
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        self._match = match
        self._req_rx_bytes = 0
        self._req_frame_count = 0
        self._req_block_starts = []
        self._req_writes = 0  # spec 011 FR-4: the request's write counter
        # Spec 009 FR-1 (plan 009 §2.2): stamp the request start and
        # reset the per-request census timing — _census_frame renders
        # its t+ offsets relative to _req_t0 and updates
        # _req_first_seen/_req_last_seen for the timeout message
        # (contract 009 §3).
        self._req_t0 = _now()
        self._req_first_seen = None
        self._req_last_seen = None
        self._rx.clear()
        self._log_idle_summary_once()
        _LOGGER.debug("TX (%d bytes): %s", len(frame), frame.hex())
        resend_task: asyncio.Task[None] | None = None
        done: asyncio.Event | None = None
        try:
            try:
                await self._write_frame(frame, force_response=force_response)
                self._req_writes = 1
            except AquaTempError:
                raise
            except Exception as exc:  # noqa: BLE001
                if force_response:
                    # Spec 011 FR-1 (contract 011 §5.1): the acknowledged
                    # write's failure chain must survive verbatim — it
                    # carries the ATT error text the verdict classifier
                    # reads; the distinct message keeps the two write
                    # types diagnosable apart.
                    raise AquaTempConnectionError(
                        f"acknowledged write failed: {exc_chain(exc)}"
                    ) from exc
                raise AquaTempConnectionError(f"GATT write failed: {exc}") from exc
            if resend_every is not None and resend_max > 0:
                # Spec 009 FR-2 (contract 009 §5): the bounded re-send loop
                # — spawned AFTER the initial write so a failed initial
                # write never re-sends; cancelled in the finally so it
                # never leaks past the request. Spec 011 FR-1: the loop
                # re-sends with the SAME ATT write type (force_response
                # threads through — one request, one write type).
                done = asyncio.Event()
                resend_task = asyncio.create_task(
                    self._resend_loop(
                        frame, resend_every, resend_max, done, force_response
                    )
                )
            try:
                async with asyncio.timeout(timeout):
                    return await self._pending
            except TimeoutError:
                # Rule 3 (contract 008-B §4.3): overall deadline — accept a
                # complete CRC-valid frame that MATCHES the request (the
                # deadline self-heal for boundary-rule misreads), else raise
                # with the honest census. 'got 0 bytes' reported bytes
                # REMAINING after the c8d8260 drops, not bytes received
                # (research 008 §5 Q3); the counters fix that (FR-4).
                buf = bytes(self._rx)
                if len(buf) >= 5 and modbus.validate_crc(buf) and self._matches(buf):
                    _LOGGER.debug(
                        "deadline accepts matching frame (%d bytes): %s",
                        len(buf),
                        buf.hex(),
                    )
                    return buf
                # Spec 009 FR-1 (contract 009 §3): per-start counts +
                # first/last census offsets — the 4-block-cycle model
                # (research 009 §0.3) becomes testable from one run.
                # The first/last clause is omitted when nothing was
                # censused (there is no offset to report; the honest
                # totals stay — contract 008-B §5/§6 carried by 009 §3).
                counts = Counter(self._req_block_starts)
                starts_seen = (
                    ", ".join(f"{start}x{n}" for start, n in sorted(counts.items()))
                    or "none"
                )
                first_last = (
                    f"; first t+{self._req_first_seen:.1f}s, "
                    f"last t+{self._req_last_seen:.1f}s"
                    if self._req_first_seen is not None
                    else ""
                )
                raise AquaTempTimeoutError(
                    f"no matching response within {timeout:.1f}s "
                    f"(received {self._req_frame_count} frame(s) / "
                    f"{self._req_rx_bytes} bytes; blocks seen: {starts_seen}"
                    f"{first_last}; "
                    f"none for start {match.start})"
                ) from None
        finally:
            self._cancel_silence_timer()
            if resend_task is not None and done is not None:
                # Contract 009 §5: the re-send loop is cancelled when the
                # request completes, fails or the client disconnects —
                # never leaks past the request (plan 009 §3.2).
                done.set()
                resend_task.cancel()
            self._pending = None
            self._match = None
            self._rx.clear()

    async def _resend_loop(
        self,
        frame: bytes,
        every: float,
        max_sends: int,
        done: asyncio.Event,
        force_response: bool = False,
    ) -> None:
        """Re-send the identical frame on a fixed cadence (spec 009 FR-2,
        contract 009 §5).

        Sends at t+every, t+2*every, … up to max_sends extra writes, all
        inside the caller's request window (the config-flow first read
        passes every=2.0, max_sends=3 → t+0/2/4/6 s inside the unchanged
        8.0 s window — research 009 §8: 4 × 8 bytes of idempotent FC03
        reads, the app's plausible 2 s poll cadence, research 009 §1.3).
        Stops when the request completes (done / pending done), the
        client disconnects, or a re-send write fails; a failed re-send
        never fails the request — the deadline and the census stay in
        charge (plan 009 §3.2).

        Spec 011 FR-1: force_response threads through so a re-sent
        request keeps ONE ATT write type; each successful re-send
        bumps _req_writes (the `answered after pairing (<n> attempt of
        the window)` line reads it, contract 011 §3.4).
        """
        for n in range(1, max_sends + 1):
            try:
                await asyncio.sleep(every)
            except asyncio.CancelledError:
                return
            if done.is_set() or self._pending is None or self._pending.done():
                return
            _LOGGER.info(
                "first-read re-send %d/%d: %s", n, max_sends, frame.hex()
            )
            try:
                await self._write_frame(frame, force_response=force_response)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "first-read re-send %d/%d failed: %s: %s",
                    n,
                    max_sends,
                    type(exc).__name__,
                    exc,
                )
                return
            self._req_writes += 1

    def _log_idle_summary_once(self) -> None:
        """Emit the idle-window summary at the FIRST request of a
        connection (spec 009 FR-1, contract 009 §3).

        Exactly one INFO line per connect, logged BEFORE the TX debug
        line of that first request: the number of block pushes that
        arrived while idle, their starts, and the first push offset
        relative to the subscribe — the E2 push-onset evidence (research
        009 §1.1). The zero-push case is itself the datum ('0 block
        push(es)': push onset later than the settle — quickstart 009
        §0/§1).
        """
        if self._idle_summary_logged:
            return
        self._idle_summary_logged = True
        if self._idle_pushes:
            starts = ", ".join(
                str(start) for start in sorted({s for s, _ in self._idle_pushes})
            )
            first = self._idle_pushes[0][1]
            _LOGGER.info(
                "idle window before first request: %d block push(es), "
                "starts seen: %s, first at t+%.2fs after subscribe",
                len(self._idle_pushes),
                starts,
                first,
            )
        else:
            _LOGGER.info("idle window before first request: 0 block push(es)")

    def _on_notify(self, *args: Any) -> None:
        """Handle one GATT notification chunk.

        bleak < 0.22 calls (sender, data); newer bleak calls (data). [V]

        Spec 008 flow (contract 008-B §2): accumulate, boundary-detect,
        then per complete frame: CRC-validate, census, MATCH against the
        pending request. A frame is never dropped for its function code
        alone — the c8d8260 FC gate is the production root cause
        (research 008 §3).
        """
        data = args[-1] if args else b""
        if const.DEBUG_RAW:
            _LOGGER.debug("ff02 chunk (%d): %s", len(data), bytes(data).hex())
        pending = self._pending
        if pending is None or pending.done():
            # No request in flight (settle/idle): census, never
            # accumulate — a stale push must not poison the next
            # request's buffer (the c8d8260 'stale push: drop' rule,
            # now observable — contract 008-B §5).
            self._idle_census(bytes(data))
            return
        self._rx.extend(data)
        self._req_rx_bytes += len(data)  # TOTAL received, never reset on discard
        if self._rx and self._rx[0] != modbus.SLAVE_ID:
            _LOGGER.warning(
                "dropping non-Modbus notify: %s", bytes(self._rx).hex()
            )
            self._rx.clear()
            return
        if len(self._rx) > const.MAX_RX_BUFFER:
            self._rx.clear()
            self._fail_pending(AquaTempResponseError("response too long"))
            return
        while True:
            expected = _frame_boundary(bytes(self._rx))
            if expected is None or len(self._rx) < expected:
                break
            frame = bytes(self._rx[:expected])
            del self._rx[:expected]
            if not modbus.validate_crc(frame):
                # CRC-invalid frame: census at DEBUG, keep waiting — a
                # corrupt frame never fails the request (FR-2, contract
                # 008-B §2 step 9).
                self._census_frame(frame, crc_ok=False)
                continue
            if frame[1] & 0x80 or self._matches(frame):
                # Exception frames fail fast, matching frames complete —
                # the unchanged c8d8260 error semantics, reached via
                # matching (contract 008-B §3).
                self._complete(frame)
                return
            # CRC-valid non-matching frame: census (INFO) + counters,
            # then keep waiting for the request's own frame (contract
            # 008-B §2 step 12 — never fatal, never completing).
            self._census_frame(frame, crc_ok=True)
            break
        # More (or unknown-shape) data may follow: arm the silence timer.
        self._arm_silence_timer()

    def _matches(self, frame: bytes) -> bool:
        """True when frame may complete the pending request (contract
        008-B §3).

        The predicates carry their own completeness rules, so a truncated
        or partial buffer can never match (FR-4): FC03 needs
        len == bytecount+5, a block frame needs
        block_response_length(frame) == len(frame), an echo needs exactly
        8 bytes.
        """
        match = self._match
        if match is None or match.count is None or len(frame) < 3:
            return False
        fc = frame[1]
        if fc == modbus.FC_READ_HOLDING:
            # An FC03 response carries no start echo — the request supplies
            # it (unchanged c8d8260 semantics).
            return (
                match.accept_fc03
                and frame[2] == 2 * match.count
                and len(frame) == frame[2] + 5
            )
        if fc == modbus.FC_BLOCK:
            if modbus.block_response_length(frame) == len(frame):
                # block frame: start/qty must echo the request
                return (
                    match.accept_block
                    and struct.unpack(">HH", frame[2:6]) == (match.start, match.count)
                )
            # 8-byte echo shape — including the pathological echo whose
            # CRC low byte equals 2*qty (contract 008-B §4.3 self-heals it)
            return (
                match.accept_echo
                and len(frame) == 8
                and struct.unpack(">HH", frame[2:6]) == (match.start, match.count)
            )
        return False

    def _census_frame(self, frame: bytes, crc_ok: bool) -> None:
        """Census one complete frame seen while a request is pending
        (FR-4, contract 008-B §5).

        Block pushes are the E1/E2 decision data (research 008 §7): one
        INFO line each; other shapes log a generic line; CRC-invalid
        frames at DEBUG. The counters feed the honest timeout message
        (research 008 §5 Q3).
        """
        verdict = "OK" if crc_ok else "FAIL"
        level = logging.INFO if crc_ok else logging.DEBUG
        is_block = (
            len(frame) >= 7
            and frame[1] == modbus.FC_BLOCK
            and modbus.block_response_length(frame) == len(frame)
        )
        if crc_ok:
            self._req_frame_count += 1
        # Spec 009 FR-1 (plan 009 §2.2): every censused frame updates the
        # first/last offsets; census lines render t+%.2fs relative to the
        # request start (contract 009 §3). The 0.0 fallback only exists
        # for direct-notify test paths — _request stamps _req_t0 before
        # any notification can be processed.
        offset = 0.0
        if self._req_t0 is not None:
            offset = _now() - self._req_t0
            if self._req_first_seen is None:
                self._req_first_seen = offset
            self._req_last_seen = offset
        if is_block:
            start, qty = struct.unpack(">HH", frame[2:6])
            if crc_ok:
                self._req_block_starts.append(start)
                _LOGGER.log(
                    level,
                    "block push observed: start=%d qty=%d len=%d crc=%s "
                    "(waiting for start %s; t+%.2fs)",
                    start,
                    qty,
                    len(frame),
                    verdict,
                    self._match.start if self._match is not None else None,
                    offset,
                )
            else:
                _LOGGER.log(
                    level,
                    "block push observed: start=%d qty=%d len=%d crc=%s; "
                    "t+%.2fs",
                    start,
                    qty,
                    len(frame),
                    verdict,
                    offset,
                )
        else:
            _LOGGER.log(
                level,
                "frame observed: fc=0x%02X len=%d crc=%s (non-matching; "
                "request keeps waiting; t+%.2fs)",
                frame[1],
                len(frame),
                verdict,
                offset,
            )

    def _idle_census(self, data: bytes) -> None:
        """Census frames arriving with no request pending (FR-4, contract
        008-B §5).

        Settle-time pushes are the periodic-vs-triggered evidence
        (research 008 §7 E2): one INFO line per connect for the first
        block push, DEBUG afterwards. Nothing is ever accumulated — a
        stale push must not poison the next request's buffer (unchanged
        c8d8260 'stale push: drop' semantics, now observable).
        """
        if not data:
            return
        if data[0] != modbus.SLAVE_ID:
            _LOGGER.debug(
                "dropping non-Modbus notify while idle: %s", data.hex()
            )
            return
        expected = _frame_boundary(data)
        if expected is not None and len(data) >= expected:
            frame = data[:expected]
            crc_ok = modbus.validate_crc(frame)
            is_block = (
                crc_ok
                and len(frame) >= 7
                and frame[1] == modbus.FC_BLOCK
                and modbus.block_response_length(frame) == len(frame)
            )
            if is_block:
                start, qty = struct.unpack(">HH", frame[2:6])
                # Spec 009 FR-1 (plan 009 §2.2): collect (start, t after
                # subscribe) for the idle-window summary — the E2
                # push-onset evidence (research 009 §1.1). Offset 0.0
                # when the subscribe stamp is unknown (direct-notify
                # test paths; production always stamps it in connect()).
                offset = (
                    _now() - self._t_subscribe
                    if self._t_subscribe is not None
                    else 0.0
                )
                self._idle_pushes.append((start, offset))
                if not self._idle_census_logged:
                    self._idle_census_logged = True
                    _LOGGER.info(
                        "block push observed while idle: start=%d qty=%d len=%d crc=OK",
                        start,
                        qty,
                        len(frame),
                    )
                else:
                    _LOGGER.debug(
                        "block push observed while idle: start=%d qty=%d len=%d crc=OK",
                        start,
                        qty,
                        len(frame),
                    )
            else:
                _LOGGER.debug(
                    "frame observed while idle: fc=0x%02X len=%d crc=%s",
                    frame[1],
                    len(frame),
                    "OK" if crc_ok else "FAIL",
                )
        else:
            _LOGGER.debug(
                "stale notification chunk while idle (%d bytes)", len(data)
            )

    def _arm_silence_timer(self) -> None:
        """(Re)arm the silence timer after every chunk (rule 2)."""
        self._cancel_silence_timer()
        loop = asyncio.get_running_loop()
        self._silence_timer = loop.call_later(const.SILENCE_TIMEOUT, self._on_silence)

    def _on_silence(self) -> None:
        self._silence_timer = None
        pending = self._pending
        if pending is None or pending.done():
            return
        buf = bytes(self._rx)
        # Rule 2 (contract 008-B §4.2): complete ONLY a CRC-valid frame
        # that MATCHES the pending request — a truncated or non-matching
        # buffer never resolves a request (FR-4). This is the self-heal
        # path for the pathological echo whose CRC low byte equals 2*qty:
        # the boundary rule misreads it as a block frame, but the 8-byte
        # buffer is CRC-valid and matches the echo spec (contract 008-B
        # §4.3).
        if len(buf) >= 5 and modbus.validate_crc(buf) and self._matches(buf):
            _LOGGER.debug(
                "silence timer completes frame (%d bytes): %s", len(buf), buf.hex()
            )
            self._complete(buf)
        else:
            # Chunks may still arrive; the request timeout is the final deadline.
            _LOGGER.debug(
                "silence after %d bytes (no matching frame yet); "
                "waiting for the request timeout",
                len(buf),
            )

    def _complete(self, frame: bytes) -> None:
        """Validate CRC + function code of a complete frame, then resolve."""
        pending = self._pending
        self._cancel_silence_timer()
        if pending is None or pending.done():
            self._rx.clear()
            return
        _LOGGER.debug("RX frame (%d bytes): %s", len(frame), frame.hex())
        if not modbus.validate_crc(frame):
            self._rx.clear()
            self._fail_pending(AquaTempResponseError(modbus.ModbusCRCError(
                f"bad CRC: {frame.hex()}"
            )))
            return
        fc = frame[1]
        if fc & 0x80:
            code = frame[2]
            self._rx.clear()
            self._fail_pending(AquaTempResponseError(modbus.ModbusExceptionError(code)))
            return
        self._rx.clear()  # any remainder beyond the frame is dropped
        pending.set_result(frame)

    def _fail_pending(self, exc: Exception) -> None:
        pending = self._pending
        self._cancel_silence_timer()
        if pending is not None and not pending.done():
            pending.set_exception(exc)

    def _cancel_silence_timer(self) -> None:
        if self._silence_timer is not None:
            self._silence_timer.cancel()
            self._silence_timer = None

    # ----------------------------------------------------------- high level
    async def read_registers(
        self,
        start: int,
        count: int,
        *,
        resend_every: float | None = None,
        resend_max: int = 0,
        force_response: bool = False,
    ) -> dict[int, int]:
        """FC03 read; returns {register: raw signed int16}.

        Spec 008 FR-3: the request completes on EITHER the FC03 answer OR
        the unit's block frame echoing (start, count) — the production
        first-read fix (research 008 §1). The request bytes are unchanged
        (the vendor-proven base read, research 008 §5 Q5).

        Spec 009 FR-2 (contract 009 §5): resend_every/resend_max re-send
        the identical frame on a fixed cadence INSIDE the unchanged
        request window — used ONLY by the config-flow connection test
        (resend_every=2.0, resend_max=3 → sends at t+0/2/4/6 s). The
        coordinator's polling path keeps the single-shot default until a
        follow-up spec cites evidence (NFR-3).

        Spec 011 FR-1: force_response=True writes the SAME frame as an
        acknowledged ATT Write Request — the security probe's read
        (contract 011 §1.2/§2: the only probe bytes ever sent are the
        vendor's own base read, re-sent with the opposite ack policy;
        the match spec, census and timeout semantics of 008/009 are
        reused unchanged). No other request kind exposes the flag
        (FC10 writes and query_blocks keep today's signatures).
        """
        frame = modbus.build_read_holding_registers(start, count)
        resp = await self._request(
            frame,
            _ResponseMatch(accept_fc03=True, accept_block=True, start=start, count=count),
            const.REQUEST_TIMEOUT,
            resend_every=resend_every,
            resend_max=resend_max,
            force_response=force_response,
        )
        try:
            if resp[1] == modbus.FC_BLOCK:
                # the pushed block frame (research 008 §2.1)
                return modbus.parse_block_response(
                    resp,
                    start,
                    count,
                    little_endian=registers.REG_DATA_LITTLE_ENDIAN,
                )
            return modbus.parse_read_response(
                resp,
                start,
                count,
                little_endian=registers.REG_DATA_LITTLE_ENDIAN,
            )
        except modbus.ModbusError as exc:
            raise AquaTempResponseError(exc) from exc

    async def query_block(self, start: int, count: int) -> bytes:
        """8-byte block query; the unit answers with a block frame [P]
        (research 008 §2.1): 63 10 start qty bytecount data crc.

        Returns the raw CRC-validated response bytes. Never used by polling.
        """
        frame = modbus.build_query_block(start, count)
        return await self._request(
            frame,
            _ResponseMatch(accept_block=True, start=start, count=count),
            const.REQUEST_TIMEOUT,
        )

    async def write_registers(self, start: int, values: list[int]) -> None:
        """FC10 write (H1 layout) + optional echo wait (registers.WRITE_ECHO_EXPECTED)."""
        frame = modbus.build_write_registers(start, values)
        if not registers.WRITE_ECHO_EXPECTED or const.WRITE_ECHO_TIMEOUT <= 0:
            # fire-and-forget mode
            if not self.is_connected:
                raise AquaTempConnectionError("not connected")
            _LOGGER.debug("TX fire-and-forget (%d bytes): %s", len(frame), frame.hex())
            try:
                await self._write_frame(frame)
            except AquaTempError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AquaTempConnectionError(f"GATT write failed: {exc}") from exc
            return
        echo = await self._request(
            frame,
            _ResponseMatch(accept_echo=True, start=start, count=len(values)),
            const.WRITE_ECHO_TIMEOUT,
        )
        try:
            modbus.parse_write_echo(echo, start, len(values))
        except modbus.ModbusError as exc:
            raise AquaTempResponseError(exc) from exc
        _LOGGER.debug("write echo OK: %s", echo.hex())


# --------------------------------------------------------------------------
# Spec 011 — the probe/pair/retry sequence (contract 011 §2). ONE
# implementation, two consumers: the config-flow connection test
# (config_flow._async_test) and the developer tool's --phase security
# (tools/session_probe.py, FR-9 — import the client, never copy it).
# --------------------------------------------------------------------------


async def run_security_probe(
    client: "AquaTempBleClient",
    *,
    retry_resend_every: float | None = None,
    retry_resend_max: int = 0,
    on_stage: Any = None,
) -> dict[int, int] | None:
    """Run the security-probe / pair / first-read-retry stages on a
    CONNECTED client (spec 011 FR-2/FR-4, contract 011 §2).

    Called strictly AFTER the caller's first read timed out, on the
    SAME live session (contract 011 §3 — the session order is
    untouched, the probe never reconnects):

      security-probe   ONE acknowledged base read — the SAME vendor
                       frame with response=True (the X1 experiment:
                       research 011 §2.1/§12); no re-send on the probe;
      [pair]           the pairing ladder, ONLY on an att-error-security
                       verdict (contract 011 §2) — bounded by its own
                       PAIR_TIMEOUT (contract 011 §4) so a wedged D-Bus
                       Pair cannot eat the remaining budget;
      first-read-retry ONE plain base read, ONLY after a pair attempt
                       that returned succeeded/skipped-already-bonded
                       (FR-4), with the caller's re-send bounds.

    Returns the answered register dict, or None when no answer arrived
    — the caller then re-raises its ORIGINAL first-read timeout so the
    UI keys stay unchanged (FR-2.5: no_response keeps its meaning and
    advice). Never raises AquaTempTimeoutError; propagates the retry
    read's other AquaTempErrors for the caller's existing routing
    (contract 011 §2). A pairing failure is logged, never raised
    (contract 011 §7: the test must stay runnable).

    Stage labels are reported through on_stage BEFORE each stage runs
    (contract 011 §2: failure lines report the running stage, never a
    later one — the 007 conventions extended to the probe stages).
    Evidence lines are WARNING (contract 012 §2): the operator's HA log
    page keeps only WARNING+.
    """
    stage = on_stage or (lambda _name: None)
    address = getattr(client, "address", "?")
    write_uuid = getattr(client, "write_uuid", None) or const.UUID_WRITE

    # Stage security-probe (contract 011 §3.1): the entry line names the
    # exact pipe and address it is about to test.
    stage("security-probe")
    # WARNING (contract 012 §2): the operator's system_log panel stores
    # WARNING+ only (research 012 §4) — below WARNING the evidence is
    # operator-invisible.
    _LOGGER.warning(
        "security probe: re-sending base read as acknowledged write "
        "(response=True) on %s to %s",
        write_uuid,
        address,
    )
    verdict = "att-accepted-silent"
    chain = ""
    regs: dict[int, int] | None = None
    try:
        regs = await client.read_registers(
            registers.BASE_BLOCK_START,
            registers.BASE_BLOCK_COUNT,
            force_response=True,
        )
    except AquaTempConnectionError as exc:
        # The acknowledged write was refused — classify the chain
        # (contract 011 §5.1) and log exactly one verdict line at WARNING
        # (contract 012 §2 — the panel keeps WARNING+ only, research
        # 012 §4).
        chain = str(exc)  # carries the ATT error chain verbatim (FR-1)
        verdict = _classify_ack_write_failure(chain)
        _LOGGER.warning("security probe: verdict %s: %s", verdict, chain)
    except AquaTempTimeoutError as exc:
        # The write was accepted but the engine stayed silent — the
        # H-APP-GATE branch (research 011 §11 rank 2); the census rides
        # in the exception message (contract 009 §3). WARNING (contract
        # 012 §2): the verdict must reach the operator's panel (research
        # 012 §4).
        _LOGGER.warning(
            "security probe: verdict att-accepted-silent "
            "(ack ok, no Modbus answer within %.1fs; census: %s)",
            const.REQUEST_TIMEOUT,
            exc,
        )
        return None
    except AquaTempResponseError as exc:
        # The acknowledged write REACHED the engine — the unit answered
        # with a Modbus exception frame: the write pipe is open at the
        # ATT layer (research 011 §2.2 row 2). No regs to route; the
        # original timeout keeps the UI key (FR-2.5). WARNING (contract
        # 012 §2): decisive evidence — the panel keeps WARNING+ only
        # (research 012 §4).
        _LOGGER.warning(
            "security probe: acknowledged write answered by a Modbus "
            "exception frame (%s) — the write pipe is open at the ATT "
            "layer (research 011 §2.2)",
            exc_chain(exc),
        )
        return None
    else:
        # The acknowledged write itself woke the engine — itself
        # decisive evidence (contract 011 §2 row 3 / §6 row 4): the
        # gate is the write TYPE, not the bond. WARNING (contract 012
        # §2): the panel keeps WARNING+ only (research 012 §4).
        _LOGGER.warning(
            "security probe: acknowledged write answered — the Write "
            "Request woke the Modbus engine (contract 011 §2; research "
            "011 §2.2)"
        )
        return regs
    if verdict != "att-error-security":
        # Contract 011 §2: the ladder runs ONLY after a security-class
        # verdict; other verdicts log and route — never pair.
        return None

    # Stage pair (contract 011 §3.3): bounded by PAIR_TIMEOUT with
    # asyncio.wait_for on purpose — an OUTER budget cancellation
    # (the flow's TEST_TIMEOUT) surfaces as CancelledError and passes
    # through untouched, while OUR expiry becomes this TimeoutError
    # (contract 011 §4's inner bound without asyncio.timeout's nested
    # ambiguity).
    stage("pair")
    pair_result = "failed"
    try:
        pair_result = await asyncio.wait_for(client.pair(), const.PAIR_TIMEOUT)
    except TimeoutError:
        _LOGGER.warning(
            "security probe: pair stage exceeded the %.0f s pairing "
            "budget (contract 011 §4)",
            const.PAIR_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — a pairing failure never breaks the test
        # WARNING (contract 012 §2): the failure chain is decisive-once
        # evidence — the system_log panel stores WARNING+ only (research
        # 012 §4).
        _LOGGER.warning("pairing: failed: %s", exc_chain(exc))
        pair_result = "failed"
    if pair_result not in ("succeeded", "skipped"):
        # FR-4: the retry follows a successful/skipped pair only — a
        # failed, wedged or unavailable pairing never burns the
        # remaining budget on another read.
        return None

    # Stage first-read-retry (FR-4): exactly ONE more base read with
    # the caller's re-send bounds (contract 011 §2).
    stage("first-read-retry")
    try:
        regs = await client.read_registers(
            registers.BASE_BLOCK_START,
            registers.BASE_BLOCK_COUNT,
            resend_every=retry_resend_every,
            resend_max=retry_resend_max,
        )
    except AquaTempTimeoutError as exc:
        # WARNING (contract 012 §2): decisive evidence — the operator's
        # system_log panel stores WARNING+ only (research 012 §4).
        _LOGGER.warning(
            "security probe: still silent after pairing (census: %s)", exc
        )
        return None
    # WARNING (contract 012 §2): the case-closed evidence line — the
    # operator's system_log panel stores WARNING+ only (research 012 §4).
    _LOGGER.warning(
        "security probe: answered after pairing (%d attempt of the window)",
        getattr(client, "last_request_write_count", 1),
    )
    return regs