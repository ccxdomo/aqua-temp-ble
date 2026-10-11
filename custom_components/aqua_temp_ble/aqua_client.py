"""AquaTempBleClient — BLE transport for the Aqua Temp pool heat pump.

Owns the bleak connection and enforces a strict one-at-a-time
request/response cycle. Responses to a Modbus request arrive as GATT
notifications on ff02 and MAY be fragmented (typical chunks of 20 bytes at
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
        """Resolve the device, connect and subscribe to ff02.

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
        """
        if self.is_connected:
            return
        device = await self._resolve_device()
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
                client: BleakClient = await brc.establish_connection(
                    BleakClient,
                    device,
                    name=self._name or getattr(device, "name", "") or self._address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=max_attempts,
                    timeout=const.CONNECT_TIMEOUT,
                )
            else:
                client = await self._connect_with_raw_client(device)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "connect to %s failed: %s", self._address, exc_chain(exc)
            )
            raise AquaTempConnectionError(
                f"could not connect to {self._address}: {exc_chain(exc)}"
            ) from exc
        self._client = client
        try:
            await client.start_notify(const.UUID_NOTIFY, self._on_notify)
            self._resolve_write_characteristic(client)
            # Vendor parity (research 006 §2 step 4): give the module a
            # beat to route ff02 notifications after the CCCD write
            # before the first Modbus frame (spec 006 FR-1). Inside this
            # try block on purpose — a setup failure here must tear down
            # the half-open client like any other (a cancelled sleep is
            # handled by the caller's task teardown, spec 005's
            # cancellation contract).
            await asyncio.sleep(const.SETTLE_AFTER_SUBSCRIBE)
        except AquaTempError:
            # Link is up but ff02/ff01 setup failed: tear down so the next
            # connect() starts from a clean state.
            await self.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "post-connect setup (ff02 notify / ff01 lookup) on %s "
                "failed: %s",
                self._address,
                exc_chain(exc),
            )
            await self.disconnect()
            raise AquaTempConnectionError(
                f"could not connect to {self._address}: {exc_chain(exc)}"
            ) from exc
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

    def _resolve_write_characteristic(self, client: BleakClient) -> None:
        """Pick the write mode from the ff01 characteristic properties.

        Vendor parity (research 006 §2.1): the app writes WITHOUT response
        (WriteType.withoutResponse in its write path). Rule, in order:
        write-without-response first, write (with response) fallback, hard
        error when the characteristic offers neither — never guess a mode
        the GATT table does not advertise (property-gated, spec 006 FR-2).
        """
        try:
            char = client.services.get_characteristic(const.UUID_WRITE)
        except Exception as exc:  # noqa: BLE001
            raise AquaTempConnectionError(f"cannot inspect GATT services: {exc}") from exc
        if char is None:
            raise AquaTempConnectionError(
                f"write characteristic {const.UUID_WRITE} not found on the device"
            )
        props = list(getattr(char, "properties", []) or [])
        if "write-without-response" in props:
            # Vendor parity: the app writes WITHOUT response
            # (WriteType.withoutResponse in its write path, research 006 §2.1).
            self._write_with_response = False
        elif "write" in props:
            self._write_with_response = True
        else:
            raise AquaTempConnectionError(
                f"write characteristic {const.UUID_WRITE} has no usable write property"
            )
        _LOGGER.debug(
            "ff01 properties %s -> write_with_response=%s",
            props,
            self._write_with_response,
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
        """Transport seam — send a raw frame on ff01. (Overridden in tests.)"""
        await self._client.write_gatt_char(
            const.UUID_WRITE, bytes(frame), response=self._write_with_response
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