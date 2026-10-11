"""AquaTempBleClient — BLE transport for the Aqua Temp pool heat pump.

Owns the bleak connection and enforces a strict one-at-a-time
request/response cycle. Responses to a Modbus request arrive as GATT
notifications on the notify characteristic (ff02 on the documented
units; adaptively discovered otherwise — see
_resolve_notify_characteristic) and MAY be fragmented (typical chunks
of 20 bytes at
default MTU; a single 95-byte notification is possible when the MTU is
negotiated higher; actual chunking is [V]). Reassembly rules, in order
(BLUEPRINT §2.2d):

1. expected length (primary completion),
2. silence timeout (SILENCE_TIMEOUT without a new chunk) — covers unknown
   response shapes, e.g. high-block queries [V],
3. request timeout (REQUEST_TIMEOUT, overall deadline) — accepts a
   CRC-valid buffer if one arrived, else raises AquaTempTimeoutError.

Every completed frame is CRC-validated and function-code-validated before
the awaiting caller resumes.

Standalone use (outside Home Assistant, e.g. tools/smoke_client.py):
    sys.path.insert(0, "<repo>/custom_components/aqua_temp_ble")
    import aqua_client
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import namedtuple
from typing import Any

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
# Adaptive GATT discovery (the fix for the production incident).
#
# Production evidence: the user's unit establishes the GATT link in
# ~2.9 s but does NOT expose ff01 — the historic hard-coded lookup died
# with "write characteristic 0000ff01-... not found on the device". The
# unit's real table is unknown (its advertisement exposes no service
# UUIDs either), so the client now ENUMERATES the table after connect
# (logged at INFO), resolves the write/notify characteristics
# adaptively with ff01/ff02 as the FIRST choice, and fails with an
# error whose MESSAGE lists the real table when nothing usable exists.

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
        self._expected_fc: int | None = None
        self._silence_timer: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ state
    @property
    def address(self) -> str:
        return self._address

    @property
    def is_connected(self) -> bool:
        return self._client is not None and bool(self._client.is_connected)

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

    async def connect(self, max_attempts: int = 2) -> None:
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
            brc = self._load_retry_connector()
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
                client: BleakClient = await brc.establish_connection(
                    BleakClient,
                    device,
                    name=self._name or getattr(device, "name", "") or self._address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=max_attempts,
                    timeout=const.CONNECT_TIMEOUT,
                )
            else:
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
                client = await self._connect_with_raw_client(device)
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
            self._resolve_write_characteristic(client)
            t_write_char = _now()
            # Vendor parity (research 006 §2 step 4): give the module a
            # beat to route ff02 notifications after the CCCD write
            # before the first Modbus frame (spec 006 FR-1). Inside this
            # try block on purpose — a setup failure here must tear down
            # the half-open client like any other (a cancelled sleep is
            # handled by the caller's task teardown, spec 005's
            # cancellation contract).
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

    async def _connect_with_raw_client(self, device: Any) -> BleakClient:
        """Standalone fallback: raw BleakClient with the historic 2-attempt
        loop. Used only when bleak_retry_connector is not installed
        (standalone tools / tests, no Home Assistant).
        """
        last_exc: Exception | None = None
        for attempt in (1, 2):
            client = BleakClient(
                device,
                disconnected_callback=self._on_disconnect,
                timeout=const.CONNECT_TIMEOUT,
            )
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
        """Property-gated write mode (spec 006 FR-2), unchanged rule.

        Vendor parity (research 006 §2.1): the app writes WITHOUT
        response. write-without-response first, write (with response)
        fallback, hard error when the characteristic offers neither —
        never guess a mode the GATT table does not advertise.
        """
        if "write-without-response" in props:
            # Vendor parity: the app writes WITHOUT response
            # (WriteType.withoutResponse in its write path, research 006 §2.1).
            self._write_with_response = False
        elif "write" in props:
            self._write_with_response = True
        else:
            raise AquaTempConnectionError(
                f"write characteristic {uuid} has no usable write property"
            )

    def _resolve_notify_characteristic(self, client: BleakClient) -> None:
        """Pick the notify characteristic: ff02 first, then fallback.

        Strategy, in order:
          a. the known UUID_NOTIFY (ff02) when the device exposes it;
          b. a notify/indicate characteristic of the SAME service as
             the write candidate (the vendor data service), else of any
             vendor-specific service — standard SIG services (battery
             2a19, ...) are never candidates: subscribing to them
             would deliver non-Modbus chunks forever;
          c. explicit AquaTempConnectionError whose MESSAGE lists the
             real table.
        An alternative choice is announced at INFO: 'using notify
        characteristic <uuid> (properties=...)'. Runs BEFORE start_notify
        so a resolution failure precedes any subscription.
        """
        char = self._lookup_char(client, const.UUID_NOTIFY)
        if char is not None:
            self._notify_uuid = const.UUID_NOTIFY
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
        """Pick the write characteristic: ff01 first, then fallbacks.

        Strategy, in order:
          a. the known UUID_WRITE (ff01) when the device exposes it —
             the mode stays property-gated (spec 006 FR-2), including
             the loud error when ff01 advertises no write property
             (spec 006 AC-2 pins that behaviour);
          b. a vendor-service writable characteristic
             (_find_write_candidate);
          c. ff03 (UUID_WRITE_FALLBACK, handle 2215 — role unknown,
             FINDINGS §11.3) as the last-resort candidate, even when it
             advertises no write property: a refused GATT write fails
             loudly at the first request instead of the integration
             dying at connect;
          d. explicit AquaTempConnectionError whose MESSAGE lists the
             real table.
        An alternative choice is announced at INFO: 'using write
        characteristic <uuid> (properties=...)'.
        """
        # (a) Known UUID — unchanged historic behaviour.
        char = self._lookup_char(client, const.UUID_WRITE)
        if char is not None:
            props = self._char_props(char)
            self._set_write_mode_from_properties(const.UUID_WRITE, props)
            self._write_uuid = const.UUID_WRITE
            _LOGGER.debug(
                "ff01 properties %s -> write_with_response=%s",
                props,
                self._write_with_response,
            )
            return
        # (b) Vendor-service writable characteristic.
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
        # (c) ff03 last resort (works even without an enumerable table).
        char = self._lookup_char(client, const.UUID_WRITE_FALLBACK)
        if char is not None:
            props = self._char_props(char)
            self._write_uuid = const.UUID_WRITE_FALLBACK
            if "write-without-response" in props:
                self._write_with_response = False
            elif "write" in props:
                self._write_with_response = True
            else:
                # Last resort: ff03 exists but advertises no write
                # property — its role was never confirmed (FINDINGS
                # §11.3). Try it anyway (a refused GATT write fails
                # loudly at the first request) with the vendor-parity
                # mode: without response (research 006 §2.1).
                self._write_with_response = False
                _LOGGER.warning(
                    "write characteristic %s (properties=%s) does not "
                    "advertise a write property; using it as the "
                    "last-resort candidate",
                    const.UUID_WRITE_FALLBACK,
                    props,
                )
            _LOGGER.info(
                "using write characteristic %s (properties=%s) on %s",
                const.UUID_WRITE_FALLBACK,
                props,
                self._address,
            )
            return
        # (d) Nothing usable: explicit error carrying the real table.
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
    async def _write_frame(self, frame: bytes) -> None:
        """Transport seam — send a raw frame on the resolved write
        characteristic (ff01 on documented units; an adaptively
        discovered UUID otherwise). (Overridden in tests.)"""
        await self._client.write_gatt_char(
            self._write_uuid or const.UUID_WRITE,
            bytes(frame),
            response=self._write_with_response,
        )

    async def _request(self, frame: bytes, expected_fc: int | None, timeout: float) -> bytes:
        """Send one frame and await its complete, validated response.

        expected_fc=None accepts any function code (used for the experimental
        high-block queries whose response shape is unknown [V]).
        """
        if self._pending is not None and not self._pending.done():
            raise AquaTempError("another request is already in flight")
        if not self.is_connected:
            raise AquaTempConnectionError("not connected")
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        self._expected_fc = expected_fc
        self._rx.clear()
        _LOGGER.debug("TX (%d bytes): %s", len(frame), frame.hex())
        try:
            try:
                await self._write_frame(frame)
            except AquaTempError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AquaTempConnectionError(f"GATT write failed: {exc}") from exc
            try:
                async with asyncio.timeout(timeout):
                    return await self._pending
            except TimeoutError:
                # Rule 3: overall deadline — accept a CRC-valid buffer if any.
                if len(self._rx) >= 5 and modbus.validate_crc(bytes(self._rx)):
                    _LOGGER.debug(
                        "deadline accepts CRC-valid frame (%d bytes): %s",
                        len(self._rx),
                        bytes(self._rx).hex(),
                    )
                    return bytes(self._rx)
                raise AquaTempTimeoutError(
                    f"no complete response within {timeout:.1f}s "
                    f"(got {len(self._rx)} bytes)"
                ) from None
        finally:
            self._cancel_silence_timer()
            self._pending = None
            self._expected_fc = None
            self._rx.clear()

    def _on_notify(self, *args: Any) -> None:
        """Handle one GATT notification chunk.

        bleak < 0.22 calls (sender, data); newer bleak calls (data). [V]
        """
        data = args[-1] if args else b""
        if const.DEBUG_RAW:
            _LOGGER.debug("ff02 chunk (%d): %s", len(data), bytes(data).hex())
        pending = self._pending
        if pending is None or pending.done():
            self._rx.clear()  # stale push: drop
            return
        self._rx.extend(data)
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
        if len(self._rx) >= 2 and self._expected_fc is not None:
            fc = self._rx[1]
            if fc not in (self._expected_fc, self._expected_fc | 0x80):
                _LOGGER.warning(
                    "dropping unexpected function code 0x%02X: %s",
                    fc,
                    bytes(self._rx).hex(),
                )
                self._rx.clear()
                return
        expected = modbus.expected_response_length(bytes(self._rx))
        if expected is not None and len(self._rx) >= expected:
            # Rule 1: expected length reached — complete (remainder dropped).
            self._complete(bytes(self._rx[:expected]))
            return
        # More (or unknown-shape) data may follow: arm the silence timer.
        self._arm_silence_timer()

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
        if len(buf) >= 5 and modbus.validate_crc(buf):
            _LOGGER.debug(
                "silence timer completes frame (%d bytes): %s", len(buf), buf.hex()
            )
            self._complete(buf)
        else:
            # Chunks may still arrive; the request timeout is the final deadline.
            _LOGGER.debug(
                "silence after %d bytes (no valid CRC yet); waiting for the request timeout",
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
    async def read_registers(self, start: int, count: int) -> dict[int, int]:
        """FC03 read; returns {register: raw signed int16}."""
        frame = modbus.build_read_holding_registers(start, count)
        resp = await self._request(frame, modbus.FC_READ_HOLDING, const.REQUEST_TIMEOUT)
        try:
            return modbus.parse_read_response(
                resp,
                start,
                count,
                little_endian=registers.REG_DATA_LITTLE_ENDIAN,
            )
        except modbus.ModbusError as exc:
            raise AquaTempResponseError(exc) from exc

    async def query_block(self, start: int, count: int) -> bytes:
        """Experimental 8-byte block query; response shape is unknown [V].

        Returns the raw CRC-validated response bytes. Never used by polling.
        """
        frame = modbus.build_query_block(start, count)
        return await self._request(frame, None, const.REQUEST_TIMEOUT)

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
        echo = await self._request(frame, modbus.FC_WRITE_MULTI, const.WRITE_ECHO_TIMEOUT)
        try:
            modbus.parse_write_echo(echo, start, len(values))
        except modbus.ModbusError as exc:
            raise AquaTempResponseError(exc) from exc
        _LOGGER.debug("write echo OK: %s", echo.hex())