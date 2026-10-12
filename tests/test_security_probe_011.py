#!/usr/bin/env python3
"""Spec 011 tests — the security probe; spec 021 — the pairing prune.

Covers the surviving deliverables of specs/011-android-vs-linux-ble plus
the spec-021 cleanup:

1. FR-1 — the acknowledged-write seam: `force_response=True` writes
   the SAME vendor frame with response=True (the ATT Write Request
   that makes the unit's refusal visible, research 011 §2.1/§12 X1);
   byte-equality is pinned (AC-2) and the acknowledged failure raises
   AquaTempConnectionError("acknowledged write failed: <chain>") so
   the ATT error text survives to the log (contract 011 §5.1).
2. FR-2 — the probe sequence (contract 011 §2, amended by spec 021):
   the config-flow connection test, on a first-read timeout, probes
   with the acknowledged write and classifies the verdict (§5.1
   taxonomy). The pairing ladder and its first-read retry were PRUNED
   (tasks.md D3): the production verdict `att-accepted-silent`
   (law L5, spec 020) proved the unit's write pipe opens with no
   bond, so every verdict now routes — the original timeout keeps the
   UI key.
3. Spec 021 prune pins: no client pair method, no pair connect kwarg,
   no pairing constants, no agent module, no pair_on_connect option.
4. Spec 012 (the log-visibility contract, Part 7): the probe evidence
   lines are at WARNING — the operator's HA log page
   (Settings → System → Logs, the system_log ring buffer) stores
   WARNING+ only (research 012 §4) — and every connection test starts
   with a build-marker WARNING naming the running version (spec 012
   FR-2/FR-3, contract 012 §2/§3).

The suite is dual-mode like its siblings: client-level tests import
aqua_client standalone over a fake bleak (the test_first_read_resend
pattern); config-flow tests import config_flow against stubbed
homeassistant/voluptuous modules under a synthetic package (the
test_config_flow_walkthrough pattern), driving the flow
manager-faithfully.

Run standalone:

    python3 tests/test_security_probe_011.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

try:
    import pytest

    pytestmark = pytest.mark.asyncio
except ImportError:  # pragma: no cover — pytest-less standalone host
    pass

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "aqua_temp_ble"
)

# ---------------------------------------------------------------------------
# Standalone component imports (fake bleak when the library is absent —
# the transport seam is faked at the bleak-client level, never used for
# real; the injected module matches the other spec files' pattern).
# ---------------------------------------------------------------------------

try:
    import bleak  # noqa: F401
except ImportError:
    bleak = types.ModuleType("bleak")

    class _FakeBleakClientPlaceholder:  # pragma: no cover — placeholder
        is_connected = False

    class _FakeBleakScannerPlaceholder:  # pragma: no cover — placeholder
        pass

    bleak.BleakClient = _FakeBleakClientPlaceholder
    bleak.BleakScanner = _FakeBleakScannerPlaceholder
    sys.modules["bleak"] = bleak

sys.path.insert(0, str(COMPONENT_DIR))

import aqua_client  # noqa: E402
import const  # noqa: E402
import modbus  # noqa: E402
import registers  # noqa: E402

ADDRESS = "08:06:A6:C0:94:1C"
BASE_READ_HEX = "63030007002d3c54"  # the vendor-proven base read [P]
UUID_WRITE = const.UUID_WRITE


# ---------------------------------------------------------------------------
# Frame helpers (the siblings' proven builders)
# ---------------------------------------------------------------------------


def _build_block_frame(
    start: int, count: int = 45, values: dict[int, int] | None = None
) -> bytes:
    """The unit's block frame [P] (research 008 §2.1)."""
    values = values or {}
    payload = bytearray()
    for i in range(count):
        payload += (values.get(start + i, 0) & 0xFFFF).to_bytes(2, "big")
    head = (
        bytes([modbus.SLAVE_ID, 0x10])
        + start.to_bytes(2, "big")
        + count.to_bytes(2, "big")
        + bytes([len(payload)])
    )
    body = head + bytes(payload)
    return body + modbus.crc16_modbus(body).to_bytes(2, "little")


SYNTHETIC_VALUES = {8: 1, 13: 1, 14: 325, 26: 243, 27: 999, 28: -125, 51: 2040}
ANSWER_BLOCK = _build_block_frame(7, 45, SYNTHETIC_VALUES)


# ---------------------------------------------------------------------------
# Fakes: the bleak-client level (client tests) + the client wrapper
# ---------------------------------------------------------------------------


class FakeGattChar:
    """GATT characteristic stand-in (answers every UUID lookup)."""

    def __init__(self, props):
        self.properties = list(props)


class FakeServices:
    """Services stand-in: bucket lookups work, enumeration does not
    (the client's best-effort path — DEBUG, never fatal)."""

    def __init__(self, char=None):
        self._char = char

    def get_characteristic(self, _uuid):
        return self._char


class _RecordingBleakClientBase:
    """Fake connected bleak client: records (uuid, data, response) writes.

    The AC-2 seam delta is asserted HERE: force_response is visible on
    the wire only as response=True (the frame bytes must be identical
    either way — contract 011 §1.2). Deliberately exposes NO pair
    method (spec 021): a regression that reintroduces client-side
    pairing would AttributeError on this fake and fail loudly.

    Optional behaviors (scriptable per test):
    - ack_error: exception raised on acknowledged writes (response=True)
      — the ATT refusal the probe surfaces;
    - answer_frames/answer_on_write: notification frame(s) fed into the
      wrapped client's _on_notify on the Nth write (a scripted answer).
    """

    def __init__(
        self,
        *,
        ack_error: BaseException | None = None,
        answer_frames: list[bytes] | None = None,
        answer_on_write: int = 1,
    ) -> None:
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool]] = []
        self.write_count = 0
        self.ack_error = ack_error
        self.answer_frames = list(answer_frames or [])
        self.answer_on_write = answer_on_write
        self.client_ref: aqua_client.AquaTempBleClient | None = None
        self.disconnected = False
        # the app-parity ff03 layout: bucket lookups resolve, the table
        # itself is not enumerable (the client logs at DEBUG and falls
        # back to the direct UUID lookups — spec 010's path).
        self.services = FakeServices(
            FakeGattChar(["write-without-response", "write"])
        )

    async def write_gatt_char(self, uuid, data, response=True) -> None:
        self.write_count += 1
        self.writes.append((str(uuid), bytes(data), bool(response)))
        if response and self.ack_error is not None:
            raise self.ack_error
        if self.answer_frames and self.write_count >= self.answer_on_write:
            frames, self.answer_frames = self.answer_frames, []
            for frame in frames:
                if self.client_ref is not None:
                    self.client_ref._on_notify(bytearray(frame))

    async def start_notify(self, _uuid, _handler) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


def _make_probe_client(fake: _RecordingBleakClientBase) -> aqua_client.AquaTempBleClient:
    """A real AquaTempBleClient over a fake bleak client (connected).

    The write mode is the production ff03 mode (write-without-response,
    the vendor parity of spec 010) so the AC-2 seam assertions see the
    session's real default; force_response=True is the only override.
    """
    client = aqua_client.AquaTempBleClient(ADDRESS)
    client._client = fake
    client._write_with_response = False
    fake.client_ref = client
    return client


class _LogCap(logging.Handler):
    """Capture records emitted on the aqua_client logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []
        # Spec 012 FR-3: the caps also record each record's levelname —
        # the suite now asserts LEVELS, not just message strings (the
        # coverage hole that let an operator-invisible INFO grammar pass).
        self.levels: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        self.levels.append(record.levelname)


class _capture_logs:
    """Attach a capture handler to aqua_client._LOGGER for the duration."""

    def __enter__(self) -> _LogCap:
        self.cap = _LogCap()
        self.logger = aqua_client._LOGGER
        self.old_level = self.logger.level
        self.logger.addHandler(self.cap)
        self.logger.setLevel(logging.DEBUG)
        return self.cap

    def __exit__(self, *_exc) -> bool:
        self.logger.removeHandler(self.cap)
        self.logger.setLevel(self.old_level)
        return False


class _patch_const:
    """Patch a const attribute for the duration of a block."""

    def __init__(self, name: str, value) -> None:
        self.name = name
        self.value = value

    def __enter__(self) -> None:
        self.old = getattr(const, self.name)
        setattr(const, self.name, self.value)

    def __exit__(self, *_exc) -> bool:
        setattr(const, self.name, self.old)
        return False


def _leaked_tasks() -> list[asyncio.Task]:
    """Running tasks from this suite's machinery (leak guard)."""
    return [
        t
        for t in asyncio.all_tasks()
        if "_resend_loop" in getattr(t.get_coro(), "__qualname__", "")
    ]


# ---------------------------------------------------------------------------
# Part 1 — the pure verdict classifier (contract 011 §5.1)
# ---------------------------------------------------------------------------


async def test_classifier_table_every_contract_row() -> None:
    """AC-1: every §5.1 row classifies to its verdict, case-insensitively,
    including the BlueZ 'Not paired' status string and the short
    'insufficient auth' form older stacks emit (research 011 §10.1).
    Unknown strings fall to local-failure — never guessed."""
    classify = aqua_client._classify_ack_write_failure
    table = [
        # (chain text, verdict)
        ("BleakGATTProtocolError: write failed: Insufficient Authentication", "att-error-security"),
        ("insufficient encryption", "att-error-security"),
        ("Not paired: Operation requires an existing bond", "att-error-security"),
        ("org.bluez.Error.NotPaired: ...", "att-error-security"),
        ("insufficient auth", "att-error-security"),
        ("Write Not Permitted", "att-error-permission"),
        ("org.bluez.Error.NotPermitted: Not permitted", "att-error-permission"),
        ("write not allowed", "att-error-permission"),
        ("Insufficient Authorization", "att-error-permission"),  # NOT security
        ("invalid handle", "att-error-handle"),
        ("Invalid Offset", "att-error-handle"),
        ("something completely different", "local-failure"),
        ("", "local-failure"),
    ]
    for text, expected in table:
        got = classify(text)
        assert got == expected, f"{text!r}: expected {expected}, got {got}"
    # The classifier must also see through the wrapper chain the seam
    # produces (acknowledged write failed: <chain verbatim>).
    wrapped = "acknowledged write failed: BleakGATTProtocolError: ATT error: Insufficient Authentication"
    assert classify(wrapped) == "att-error-security", wrapped


# ---------------------------------------------------------------------------
# Part 2 — FR-1: the acknowledged-write seam (AC-2)
# ---------------------------------------------------------------------------


async def test_probe_bytes_equal_and_response_only_delta() -> None:
    """AC-2: the probe writes EXACTLY the vendor's base frame
    (build_read_holding_registers(7, 45) == 63030007002d3c54) and the
    ONLY seam delta is response=True; the same request without the flag
    writes the same bytes with the session's write mode (False here)."""
    fake = _RecordingBleakClientBase()
    client = _make_probe_client(fake)
    client._write_with_response = False

    with _patch_const("REQUEST_TIMEOUT", 0.2):
        # normal read: without-response, the vendor parity of spec 010
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass
        # probe read: the acknowledged twin of the SAME request
        try:
            await client.read_registers(7, 45, force_response=True)
        except aqua_client.AquaTempTimeoutError:
            pass

    assert len(fake.writes) == 2, fake.writes
    expected = modbus.build_read_holding_registers(7, 45)
    assert expected.hex() == BASE_READ_HEX  # the constant pin
    for uuid, data, response in fake.writes:
        assert data == expected, (data.hex(), expected.hex())
        assert uuid == UUID_WRITE
    # the ONLY delta: response True on the acknowledged write
    assert [r for _u, _d, r in fake.writes] == [False, True], fake.writes


async def test_acknowledged_write_failure_chain_survives() -> None:
    """FR-1: an acknowledged-write failure raises AquaTempConnectionError
    whose message starts with 'acknowledged write failed:' and carries
    the chain verbatim (contract 011 §5.1 reads the ATT text from it);
    the without-response path keeps today's 'GATT write failed:' wrapper."""
    fake = _RecordingBleakClientBase(
        ack_error=RuntimeError("BleakGATTProtocolError: Insufficient Authentication")
    )
    client = _make_probe_client(fake)
    client._write_with_response = False

    # acknowledged path (the probe)
    exc = None
    try:
        await client.read_registers(7, 45, force_response=True)
    except aqua_client.AquaTempConnectionError as err:
        exc = err
    assert exc is not None, "acknowledged write failure was swallowed"
    assert str(exc).startswith("acknowledged write failed: "), str(exc)
    assert "Insufficient Authentication" in str(exc), str(exc)

    # without-response path: today's wrapper, byte-identical (spec 005).
    # The fake raises only on acknowledged bleak writes, so the session
    # mode is flipped to with-response to make the PLAIN write fail —
    # the wrapper under test is chosen by force_response (False here),
    # not by the session mode.
    fake.ack_error = RuntimeError("link went down")
    client._write_with_response = True
    exc = None
    try:
        await client.read_registers(7, 45)
    except aqua_client.AquaTempConnectionError as err:
        exc = err
    assert exc is not None
    assert str(exc).startswith("GATT write failed: "), str(exc)


async def test_normal_read_cycle_no_acknowledged_writes() -> None:
    """The poll path keeps the vendor parity (spec 010): a normal read
    cycle sends its frames without-response only — no acknowledged
    writes, no probe machinery (the spec 021 world: and no pairing
    surface exists at all)."""
    fake = _RecordingBleakClientBase(
        answer_frames=[ANSWER_BLOCK], answer_on_write=1
    )
    client = _make_probe_client(fake)
    regs = await client.read_registers(7, 45)
    assert regs[26] == 243
    assert [r for _u, _d, r in fake.writes] == [False], fake.writes


# ---------------------------------------------------------------------------
# Part 3 — the spec-021 prune pins (tasks.md D3)
# ---------------------------------------------------------------------------


async def test_pairing_surface_pruned() -> None:
    """Spec 021 (tasks.md D3): the pairing surface is GONE — no client
    pair method, no pair kwarg on connect(), no pairing constants, no
    agent module; run_security_probe carries no retry bounds (the
    ladder and its first-read-retry were pruned with the lever)."""
    assert not hasattr(aqua_client.AquaTempBleClient, "pair"), "pair() must be gone"
    for name in ("PAIR_TIMEOUT", "DEFAULT_PAIR_ON_CONNECT", "PAIR_ENABLE_PROP"):
        assert not hasattr(const, name), f"const.{name} must be gone"
    assert not (COMPONENT_DIR / "agent.py").exists(), "agent.py must be gone"
    client = _make_probe_client(_RecordingBleakClientBase())
    try:
        await client.connect(pair=True)  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("connect() must not accept a pair kwarg anymore")
    params = inspect.signature(aqua_client.run_security_probe).parameters
    assert "retry_resend_every" not in params, params
    assert "retry_resend_max" not in params, params
    assert not hasattr(client, "last_request_write_count")


# ---------------------------------------------------------------------------
# Part 4 — FR-2: the probe sequence (run_security_probe)
# ---------------------------------------------------------------------------


async def test_probe_security_verdict_routes_none_no_pairing() -> None:
    """Spec 021: an att-error-security verdict is classified and logged
    at WARNING, then the probe returns None — NO pairing attempt, NO
    retry read (the ladder was pruned; law L5, spec 020 proved no bond
    is needed). The original timeout keeps the caller's UI key."""
    fake = _RecordingBleakClientBase(
        ack_error=RuntimeError(
            "BleakGATTProtocolError: Insufficient Authentication"
        )
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert fake.write_count == 1, "no retry read may follow the verdict"
    infos = [m for m in cap.messages if m.startswith("security probe:")]
    assert any(
        m.startswith("security probe: verdict att-error-security: ")
        and "Insufficient Authentication" in m
        for m in infos
    ), infos
    # the probe entry line names the pipe under test (contract 011 §3.1)
    assert any(
        m == f"security probe: re-sending base read as acknowledged write (response=True) on {UUID_WRITE} to {ADDRESS}"
        for m in infos
    ), infos


async def test_probe_non_security_verdict_logged() -> None:
    """Contract §2: a non-security verdict (permission here) logs its
    classified line and routes — the resolver/cache column of the
    decision matrix; nothing else runs."""
    fake = _RecordingBleakClientBase(
        ack_error=RuntimeError("BleakGATTProtocolError: Write Not Permitted")
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert fake.write_count == 1
    infos = [m for m in cap.messages if m.startswith("security probe:")]
    assert any(
        m.startswith("security probe: verdict att-error-permission: ") for m in infos
    ), infos


async def test_probe_accepted_silent_verdict() -> None:
    """The ack-accepted-but-silent case: the write succeeded at the ATT
    layer and the engine ignored it — att-accepted-silent with the
    census in the line (contract §5.1 row 4; the H-APP-GATE branch)."""
    fake = _RecordingBleakClientBase()  # acknowledged write accepted, no answer
    client = _make_probe_client(fake)
    with _patch_const("REQUEST_TIMEOUT", 0.2), _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    infos = [m for m in cap.messages if m.startswith("security probe:")]
    assert any(
        m.startswith(
            "security probe: verdict att-accepted-silent "
            "(ack ok, no Modbus answer within 0.2s; census: "
        )
        for m in infos
    ), infos


async def test_probe_answers_directly() -> None:
    """Contract §2 row 3: the acknowledged write itself WAKES the
    engine — the probe's read answers, which is decisive evidence and
    a success (the helper returns the regs)."""
    fake = _RecordingBleakClientBase(
        answer_frames=[ANSWER_BLOCK], answer_on_write=1
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is not None and regs[26] == 243, regs
    assert fake.write_count == 1, "an answering probe needs no second read"
    assert any(
        "security probe: acknowledged write answered" in m for m in cap.messages
    ), cap.messages


async def test_probe_modbus_exception_frame_logged() -> None:
    """Edge: the acknowledged write reaches the engine and the unit
    answers with a Modbus exception frame — the write pipe is OPEN at
    the ATT layer; the helper logs the datum and returns None (no
    regs to route)."""
    exception_frame = (
        bytes([modbus.SLAVE_ID, 0x83, 0x02])
        + modbus.crc16_modbus(bytes([modbus.SLAVE_ID, 0x83, 0x02])).to_bytes(2, "little")
    )
    fake = _RecordingBleakClientBase(
        answer_frames=[exception_frame], answer_on_write=1
    )
    client = _make_probe_client(fake)
    with _capture_logs() as cap:
        regs = await aqua_client.run_security_probe(client)
    assert regs is None
    assert any(
        "security probe: acknowledged write answered" in m for m in cap.messages
    ), cap.messages


# ---------------------------------------------------------------------------
# Part 5 — the config-flow stages (FR-2, contract 011 §2) — stubbed HA
# ---------------------------------------------------------------------------

PKG = "_aqua_flow_011"  # synthetic package name (distinct from siblings)


class AlreadyConfigured(Exception):
    """Stand-in for HA's AbortFlow(already_configured)."""


def _install_stubs() -> None:
    vol_mod = types.ModuleType("voluptuous")

    class _VolRequired:
        """Stub vol.Required that RECORDS its kwargs — the options-flow
        test asserts the gates' defaults through it."""

        def __init__(self, key, **kwargs):
            self.key = key
            self.kwargs = kwargs

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return isinstance(other, _VolRequired) and self.key == other.key

    class _Vol:
        @staticmethod
        def Schema(schema):
            return schema

        Required = _VolRequired

        @staticmethod
        def In(container):
            return container

        @staticmethod
        def All(*args):
            return args

        @staticmethod
        def Coerce(type_):
            return type_

        @staticmethod
        def Range(**kwargs):
            return kwargs

    for name in ("Schema", "In", "All", "Coerce", "Range"):
        setattr(vol_mod, name, getattr(_Vol, name))
    vol_mod.Required = _VolRequired
    sys.modules["voluptuous"] = vol_mod

    bleak_mod = types.ModuleType("bleak")
    bleak_mod.BleakClient = object
    bleak_mod.BleakScanner = object
    sys.modules["bleak"] = bleak_mod

    class _StubConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()
            cls.handler = kwargs.get("domain")

        def async_show_menu(self, *, step_id, menu_options=None, description_placeholders=None):
            return {
                "type": "menu",
                "step_id": step_id,
                "menu_options": dict(menu_options or {}),
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress(self, *, step_id, progress_action=None, progress_task=None, description_placeholders=None):
            return {
                "type": "progress",
                "step_id": step_id,
                "progress_action": progress_action,
                "progress_task": progress_task,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_show_progress_done(self, *, next_step_id):
            return {"type": "progress_done", "step_id": next_step_id, "handler": getattr(self, "handler", None)}

        def async_abort(self, *, reason):
            return {"type": "abort", "reason": reason}

        def async_create_entry(self, *, title=None, data=None, options=None):
            return {"type": "create_entry", "title": title, "data": data, "options": options}

        async def async_set_unique_id(self, unique_id, *, raise_on_progress=True):
            self._stub_unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            if getattr(self, "_stub_unique_id", None) in self.hass.current_ids:
                raise AlreadyConfigured(self._stub_unique_id)

        def _async_current_ids(self, include_ignore=True):
            return list(self.hass.current_ids)

    class _StubOptionsFlow:
        def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": dict(errors) if errors else None,
                "description_placeholders": dict(description_placeholders or {}),
            }

        def async_create_entry(self, *, title=None, data=None):
            return {"type": "create_entry", "title": title, "data": data}

    ha = types.ModuleType("homeassistant")
    ha_components = types.ModuleType("homeassistant.components")
    ha_helpers = types.ModuleType("homeassistant.helpers")

    cfg_entries = types.ModuleType("homeassistant.config_entries")
    cfg_entries.ConfigFlow = _StubConfigFlow
    cfg_entries.ConfigFlowResult = dict
    cfg_entries.OptionsFlow = _StubOptionsFlow

    ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _bt_async_scanner_count(hass, connectable=True):
        return hass.bt_scanner_count

    def _bt_async_discovered_service_info(hass, connectable=True):
        # Test isolation (this install REPLACES the module the lazy
        # imports resolve): the flow's fakes carry bt_infos, the
        # resolve-device fakes carry connectable_infos — support both.
        for attr in ("bt_infos", "connectable_infos"):
            if hasattr(hass, attr):
                return list(getattr(hass, attr))
        return []

    def _bt_async_ble_device_from_address(hass, address, connectable=True):
        # The resolve-device stub's semantics (the fake hass carries the
        # history tiers directly): this install REPLACES that module in
        # sys.modules, so the lazy import inside _resolve_device must
        # keep finding the function — otherwise the 005 tier tests break
        # under the full suite (test isolation).
        history = (
            getattr(hass, "connectable_history", None)
            if connectable
            else getattr(hass, "all_history", None)
        )
        if history is not None:
            return history.get(address)
        return None

    ha_bt.async_scanner_count = _bt_async_scanner_count
    ha_bt.async_discovered_service_info = _bt_async_discovered_service_info
    ha_bt.async_ble_device_from_address = _bt_async_ble_device_from_address

    ha_translation = types.ModuleType("homeassistant.helpers.translation")

    async def _tr_async_get_translations(hass, language, category, integrations, *, config_flow=False):
        return dict(hass.translations)

    ha_translation.async_get_translations = _tr_async_get_translations

    ha.config = types.SimpleNamespace(language="en")
    ha.components = ha_components
    ha.helpers = ha_helpers
    ha_components.bluetooth = ha_bt

    for name, module in {
        "homeassistant": ha,
        "homeassistant.components": ha_components,
        "homeassistant.components.bluetooth": ha_bt,
        "homeassistant.config_entries": cfg_entries,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.translation": ha_translation,
    }.items():
        sys.modules[name] = module


def _import_config_flow():
    _install_stubs()
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT_DIR)]
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.config_flow", COMPONENT_DIR / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.config_flow"] = module
    spec.loader.exec_module(module)
    return module


cf = _import_config_flow()


class FlowInfo:
    def __init__(self, address, name, rssi, service_uuids=()):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.service_uuids = list(service_uuids)


class FlowHass:
    def __init__(self, infos=None):
        self.config = types.SimpleNamespace(language="en")
        self.bt_scanner_count = 1
        self.bt_infos = list(infos or [])
        self.current_ids = set()
        self.translations = {}

    def async_create_task(self, target):
        return asyncio.ensure_future(target)


class _FlowLogCap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []
        # Spec 012 FR-3: levels recorded alongside messages — the level
        # contract of contract 012 §2 is asserted through this cap.
        self.levels: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        self.levels.append(record.levelname)


class _flow_logs:
    """Capture BOTH the flow's and the stubbed aqua_client's loggers."""

    def __enter__(self) -> _FlowLogCap:
        self.cap = _FlowLogCap()
        self.loggers = [cf._LOGGER, sys.modules[f"{PKG}.aqua_client"]._LOGGER]
        self.old_levels = [lg.level for lg in self.loggers]
        for lg in self.loggers:
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self.cap)
        return self.cap

    def __exit__(self, *_exc) -> bool:
        for lg in self.loggers:
            lg.removeHandler(self.cap)
        for lg, level in zip(self.loggers, self.old_levels):
            lg.setLevel(level)
        return False


FLOW_REGS = {
    cf.registers.REG_POWER: 1,
    cf.registers.REG_MODE: 1,
    cf.registers.REG_INLET_WATER_TEMP: 245,
    cf.registers.REG_OUTLET_WATER_TEMP: 232,
    cf.registers.REG_AMBIENT_TEMP: cf.registers.SENTINEL_RAW,
}


class FakeProbeClient:
    """Duck-typed AquaTempBleClient for the flow's test task.

    Scriptable per-call read outcomes (dict = success, Exception =
    raise, "hang" = never returns) and a recorder for every read's
    kwargs — the probe's force_response and the re-send bounds are
    asserted through it. Deliberately exposes NO pair method (spec 021:
    the flow must never be able to pair)."""

    instances: list["FakeProbeClient"] = []
    script: list = []

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.script = []

    def __init__(self, address, name=None, hass=None):
        self.address = address
        self.name = name
        self.hass = hass
        self.reads: list[dict] = []
        self.harvest_calls = 0  # spec 013: the push-harvest stage seam
        self.disconnected = False
        self.connect_kwargs = None
        type(self).instances.append(self)

    async def connect(self, max_attempts=None):
        self.connect_kwargs = {"max_attempts": max_attempts}
        return None

    async def read_registers(
        self, start, count, *, resend_every=None, resend_max=0, force_response=False
    ):
        self.reads.append(
            {
                "start": start,
                "count": count,
                "resend_every": resend_every,
                "resend_max": resend_max,
                "force_response": force_response,
            }
        )
        script = type(self).script
        outcome = script[len(self.reads) - 1] if len(self.reads) <= len(script) else None
        if outcome == "hang":
            await asyncio.sleep(30.0)
            return {}
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)

    async def harvest_pushes(self, wanted_starts=None, timeout=None):
        # Spec 013 (contract 013 §2): the push-harvest stage after a
        # probe-None verdict — none of the 011 worlds script pushes, so
        # the fake returns partials (empty) and the ORIGINAL
        # no_response path stays exercised byte-identically.
        self.harvest_calls += 1
        return {}

    async def disconnect(self):
        self.disconnected = True


def _make_flow(hass=None, client_cls=FakeProbeClient, script=None):
    """Build a fresh flow bound to the fake hass. The fake's class state
    is reset FIRST, then the script is applied — reset() clears it, so
    the ordering here is the contract (the walkthrough's fault-after-
    make_flow pattern)."""
    client_cls.reset()
    if script is not None:
        client_cls.script = list(script)
    cf.AquaTempBleClient = client_cls
    flow = cf.AquaTempConfigFlow()
    flow.hass = hass or FlowHass(infos=[FlowInfo(ADDRESS, "BlueNRG-C0941C", -50)])
    return flow


async def _drive_progress(flow, result):
    """Mirror HA's FlowManager for SHOW_PROGRESS results (spec 004)."""
    assert result["type"] == "progress", result
    await result["progress_task"]
    result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    assert result["type"] in ("progress", "progress_done"), result
    if result["type"] == "progress_done":
        result = await getattr(flow, f"async_step_{result['step_id']}")(None)
    return result


FIRST_TIMEOUT = "no matching response within 8.0s (received 0 frame(s) / 0 bytes; blocks seen: none; none for start 7)"
SECURITY_CHAIN = (
    "acknowledged write failed: BleakGATTProtocolError: "
    "ATT error: Insufficient Authentication"
)


async def test_flow_success_path_runs_no_probe() -> None:
    """AC-4: a first read that SUCCEEDS runs NO probe, no extra read
    calls — zero new probe lines on the success path."""
    flow = _make_flow(script=[dict(FLOW_REGS)])
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    client = FakeProbeClient.instances[-1]
    assert len(client.reads) == 1, client.reads
    assert client.reads[0]["force_response"] is False
    assert client.reads[0]["resend_every"] == 2.0
    assert client.reads[0]["resend_max"] == 3
    assert client.disconnected
    assert not any(
        m.startswith("security probe:") for m in log.messages
    ), log.messages


async def test_flow_security_verdict_routes_no_response() -> None:
    """Spec 021: a security-class verdict at the flow level logs the
    classified line, runs the push-harvest stage (spec 013) and routes
    to the ORIGINAL no_response — the pairing ladder and its retry are
    gone, so the flow's reads stay at exactly two."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(SECURITY_CHAIN),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert len(client.reads) == 2, client.reads
    assert client.harvest_calls == 1, "the push harvest stage still runs"
    assert any(
        m.startswith("security probe: verdict att-error-security: ")
        for m in log.messages
    ), log.messages


async def test_flow_non_security_verdict_routes_no_response() -> None:
    """A permission verdict logs and routes — the UI key stays
    no_response (the original timeout)."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempConnectionError(
                "acknowledged write failed: BleakGATTProtocolError: Write Not Permitted"
            ),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert len(client.reads) == 2, client.reads
    assert any(
        m.startswith("security probe: verdict att-error-permission: ")
        for m in log.messages
    ), log.messages


async def test_flow_accepted_silent_verdict_routes_no_response() -> None:
    """att-accepted-silent: the write was accepted, the engine ignored
    it — the verdict line with the census, no_response."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["errors"] == {"base": "no_response"}, result
    client = FakeProbeClient.instances[-1]
    assert len(client.reads) == 2, client.reads
    assert any(
        m.startswith(
            "security probe: verdict att-accepted-silent "
            "(ack ok, no Modbus answer within 8.0s; census: "
        )
        for m in log.messages
    ), log.messages


async def test_flow_probe_answers_directly_reaches_confirm() -> None:
    """Contract §2 row 3: the acknowledged probe itself answers → the
    test SUCCEEDS (decisive evidence: the write type is the gate)."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            dict(FLOW_REGS),
        ],
    )
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    client = FakeProbeClient.instances[-1]
    assert len(client.reads) == 2, client.reads
    assert any(
        "security probe: acknowledged write answered" in m for m in log.messages
    ), log.messages


async def test_flow_budget_expiry_mid_probe_routes_cannot_connect() -> None:
    """AC-5 (contract §4 pathological path): a probe that hangs inside
    the flow's UNCHANGED 70 s envelope expires the budget MID-PROBE —
    the failure line reports the running stage (security-probe) and
    routes to the existing cannot_connect key."""
    flow = _make_flow(
        script=[
            cf.AquaTempTimeoutError(FIRST_TIMEOUT),
            "hang",
        ],
    )
    original_timeout = cf.TEST_TIMEOUT
    cf.TEST_TIMEOUT = 1.0  # %.0f renders "1" — the pasted line stays greppable
    try:
        with _flow_logs() as log:
            result = await flow.async_step_device(user_input={"address": ADDRESS})
            result = await _drive_progress(flow, result)
    finally:
        cf.TEST_TIMEOUT = original_timeout
    assert result["type"] == "form" and result["step_id"] == "device", result
    assert result["errors"] == {"base": "cannot_connect"}, result
    assert any(
        "exceeded the 1 s budget" in m and "at stage security-probe" in m
        for m in log.messages
    ), log.messages


async def test_options_flow_has_no_pair_toggle() -> None:
    """Spec 021 (tasks.md D3): the evidence-gated pair_on_connect
    toggle is GONE from the options schema (pruned with the pairing
    ladder). Spec 022 (AC-3, inverted): the four gate keys are GONE
    too — the push options are exactly the poll interval + the
    display unit."""
    flow = cf.AquaTempOptionsFlowHandler()
    flow.config_entry = types.SimpleNamespace(options={}, data={"mode": "push"})
    result = await flow.async_step_init(None)
    assert result["type"] == "form" and result["step_id"] == "init", result
    keys = [getattr(entry, "key", None) for entry in result["data_schema"]]
    assert "pair_on_connect" not in keys, keys
    for key in (
        "push_temp_confirmed",
        "push_control_confirmed",
        "write_control",
        "write_setpoint",
    ):
        assert key not in keys, (key, keys)
    assert set(keys) == {"poll_interval", "temperature_unit"}, keys


# ---------------------------------------------------------------------------
# Part 6 — spec 012: the log-level visibility contract (contract 012 §2/§3)
# ---------------------------------------------------------------------------


async def _drive_probe_flow(script):
    """One flow walkthrough over a scripted probe outcome, returning the
    routing result and the captured (message, levelname) pairs (spec 012
    FR-3: the caps record levels since this part — the suite asserts the
    operator-visible LEVEL, not just the message string)."""
    flow = _make_flow(script=script)
    with _flow_logs() as log:
        result = await flow.async_step_device(user_input={"address": ADDRESS})
        result = await _drive_progress(flow, result)
    return result, list(zip(log.messages, log.levels))


async def test_flow_probe_evidence_lines_are_warning() -> None:
    """Spec 012 FR-1/AC-2 (contract 012 §2): the att-accepted-silent
    walkthrough — the probe entry line and the verdict line are at
    WARNING, and ZERO `security probe:` records exist at any other
    level. The operator's system_log panel stores WARNING+ only
    (research 012 §4): below WARNING the evidence is operator-invisible."""
    result, records = await _drive_probe_flow(
        [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempTimeoutError(FIRST_TIMEOUT)]
    )
    assert result["errors"] == {"base": "no_response"}, result
    evidence = [
        (m, lvl)
        for m, lvl in records
        if m.startswith("security probe:")
    ]
    assert evidence, records
    assert all(lvl == "WARNING" for _m, lvl in evidence), evidence
    # both decisive lines of this walkthrough are captured at WARNING
    assert any(
        m.startswith("security probe: re-sending base read as acknowledged write")
        for m, _lvl in evidence
    ), evidence
    assert any(
        m.startswith("security probe: verdict att-accepted-silent")
        for m, _lvl in evidence
    ), evidence


async def test_flow_all_probe_evidence_lines_are_warning() -> None:
    """Spec 012 FR-1 (contract 012 §2): EVERY probe evidence line the
    flow can emit — the entry line, the classified verdicts, the
    accepted-silent verdict, the Modbus-exception frame and the answered
    write — is at WARNING in every walkthrough that exercises it (zero
    probe records at any other level)."""
    scenarios = [
        ("accepted-silent", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempTimeoutError(FIRST_TIMEOUT)]),
        ("security-verdict", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempConnectionError(SECURITY_CHAIN)]),
        ("permission-verdict", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempConnectionError("acknowledged write failed: BleakGATTProtocolError: Write Not Permitted")]),
        ("modbus-exception", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), cf.AquaTempResponseError("Modbus exception frame")]),
        ("answers-directly", [cf.AquaTempTimeoutError(FIRST_TIMEOUT), dict(FLOW_REGS)]),
    ]
    seen: list[str] = []
    for name, script in scenarios:
        result, records = await _drive_probe_flow(script)
        evidence = [
            (m, lvl)
            for m, lvl in records
            if m.startswith("security probe:")
        ]
        assert evidence, (name, records)
        assert all(lvl == "WARNING" for _m, lvl in evidence), (name, evidence)
        seen.extend(m for m, _lvl in evidence)
    # every contract 012 §2 evidence shape was exercised at WARNING
    prefixes = (
        "security probe: re-sending base read as acknowledged write",
        "security probe: verdict att-error-security: ",
        "security probe: verdict att-error-permission: ",
        "security probe: verdict att-accepted-silent",
        "security probe: acknowledged write answered by a Modbus exception frame",
        "security probe: acknowledged write answered — the Write",
    )
    for prefix in prefixes:
        assert any(m.startswith(prefix) for m in seen), prefix


async def test_census_lines_stay_debug_counterexample() -> None:
    """Spec 012 counter-example (contract 012 §1.2, amended by
    contract 018-B for the block line): the high-volume census
    diagnostics stay at DEBUG — a 16-push-per-window stream must not
    become WARNING noise (the counter-example discipline stands;
    grep-stable formats unchanged; spec 018 FR-4 moved the per-frame
    block line INFO -> DEBUG — re-pinned here)."""
    push = _build_block_frame(1001)  # a CRC-valid non-matching push
    fake = _RecordingBleakClientBase(answer_frames=[push], answer_on_write=1)
    client = _make_probe_client(fake)
    with _patch_const("REQUEST_TIMEOUT", 0.2), _capture_logs() as cap:
        try:
            await client.read_registers(7, 45)
        except aqua_client.AquaTempTimeoutError:
            pass
    census = [
        (m, lvl)
        for m, lvl in zip(cap.messages, cap.levels)
        if m.startswith("block push observed")
    ]
    assert census, cap.messages
    assert all(lvl == "DEBUG" for _m, lvl in census), census


async def test_flow_build_marker_logged_once_first() -> None:
    """Spec 012 FR-2/AC-3 (contract 012 §3): every connection test
    starts with EXACTLY ONE build-marker WARNING — the FIRST captured
    record, naming the running build so a stale-module install (the
    files never loaded) is always detectable from the panel."""
    result, records = await _drive_probe_flow([dict(FLOW_REGS)])
    assert result["type"] == "form" and result["step_id"] == "confirm", result
    marker = f"connection test to {ADDRESS}: build {cf.BUILD_VERSION}"
    matches = [(m, lvl) for m, lvl in records if m == marker]
    assert matches == [(marker, "WARNING")], [r for r in records if "build" in r[0]]
    assert records[0] == (marker, "WARNING"), records[:3]


async def test_build_version_matches_manifest() -> None:
    """Spec 012 FR-3c (contract 012 §3.3 — the drift pin): the marker's
    version is read from manifest.json at module import — the marker can
    never lie about the running build, and never breaks the flow
    ('unknown' only on a guarded read failure)."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert cf.BUILD_VERSION == manifest["version"], (
        cf.BUILD_VERSION,
        manifest["version"],
    )
    assert cf.BUILD_VERSION != "unknown"


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_classifier_table_every_contract_row,
        test_probe_bytes_equal_and_response_only_delta,
        test_acknowledged_write_failure_chain_survives,
        test_normal_read_cycle_no_acknowledged_writes,
        test_pairing_surface_pruned,
        test_probe_security_verdict_routes_none_no_pairing,
        test_probe_non_security_verdict_logged,
        test_probe_accepted_silent_verdict,
        test_probe_answers_directly,
        test_probe_modbus_exception_frame_logged,
        test_flow_success_path_runs_no_probe,
        test_flow_security_verdict_routes_no_response,
        test_flow_non_security_verdict_routes_no_response,
        test_flow_accepted_silent_verdict_routes_no_response,
        test_flow_probe_answers_directly_reaches_confirm,
        test_flow_budget_expiry_mid_probe_routes_cannot_connect,
        test_options_flow_has_no_pair_toggle,
        test_flow_probe_evidence_lines_are_warning,
        test_flow_all_probe_evidence_lines_are_warning,
        test_census_lines_stay_debug_counterexample,
        test_flow_build_marker_logged_once_first,
        test_build_version_matches_manifest,
    ]
    failures = 0
    for test in tests:
        try:
            asyncio.run(test())
        except (AssertionError, AttributeError, TypeError) as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
            continue
        print(f"PASS  {test.__name__}")
    print()
    if failures:
        print(f"FAILED: {failures}/{len(tests)} spec 011 tests")
        return 1
    print(f"PASSED: {len(tests)}/{len(tests)} spec 011 tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())