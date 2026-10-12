"""Config flow for aqua_temp_ble: menu-driven discovery, device list,
manual address, live test, confirm.

State machine (normative): specs/002-config-flow-scanning/contracts/
config-flow-states.md — the first step is a menu (device / scan / manual),
the full discovered-device list is a plain form, the rescan is a bounded
progress step, then the existing live-test and confirm steps close the
flow. No pseudo-entries ever appear inside a dropdown.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import translation

from . import registers
from .aqua_client import (
    AquaTempBleClient,
    AquaTempConnectionError,
    AquaTempDeviceNotFoundError,
    AquaTempResponseError,
    AquaTempTimeoutError,
    exc_chain,
    run_security_probe,
)
from .const import (
    DEFAULT_PAIR_ON_CONNECT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TEMPERATURE_UNIT,
    DEFAULT_WRITE_CONTROL,
    DOMAIN,
    MAX_OTHER_DEVICES,
    NAME_PREFIXES,
    PUSH_HARVEST_TIMEOUT,
    SCAN_WAIT_SECONDS,
    UUID_NOTIFY,
    UUID_WRITE,
)

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
# Budget for the whole connection test, seconds (spec 006 FR-3 supersedes
# spec 005 §3). The arithmetic: 2 x 20 s bleak-retry-connector-hardcoded
# connect attempts (brc's BLEAK_TIMEOUT, research 005 §3.2) + brc
# inter-attempt backoff + 2 s settle after subscribe (spec 006 FR-1) +
# 8 s REQUEST_TIMEOUT for the base read + margin. The vendor app itself
# retries connects before giving up (research 006 §2.2) — attempt 2
# catches transient refusals (phone releasing the single BLE link,
# stale bluez state). The cannot_connect routing key for budget expiry
# is unchanged (spec 005).
TEST_TIMEOUT = 70.0
DEFAULT_NAME = "Aqua Temp heat pump"


def _manifest_version() -> str:
    """The integration's manifest version — the build marker (spec
    012 FR-2, contract 012 §3): the panel must always identify which
    build produced the lines below. Guarded: the marker never breaks
    the flow, and never lies (a test pins equality with the manifest).
    """
    try:
        import json
        from pathlib import Path
        return json.loads(
            Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
        )["version"]
    except Exception:  # noqa: BLE001 — marker must never break the flow
        return "unknown"


BUILD_VERSION = _manifest_version()

# English fallbacks for the runtime-translated menu option labels (spec
# 002 FR-1/FR-13: a menu is used precisely because menu labels are
# translatable, unlike dropdown option values). Live instances translate
# them through strings.json (config.step.<menu_id>.menu_options.<key>)
# read at runtime via translation.async_get_translations; the fallback
# covers any lookup failure (research O-2: key format only verifiable on
# a live instance).
MENU_LABELS_EN = {
    "device": "Select a discovered device",
    "scan": "Scan again",
    "manual": "Enter the Bluetooth address manually",
}
# Label shown for a device that never advertised a name (info.name falls
# back to its address). Dropdown option values are raw code strings that
# strings.json cannot translate (FR-1 rationale), so the contract fixes
# the exact English text (contracts/device-list-format.md §5, FR-5).
UNNAMED_LABEL = "Unnamed device"


def _build_device_list(
    infos: Iterable[Any], configured: set[str], no_name_text: str
) -> tuple[dict[str, str], dict[str, int]]:
    """Build the tiered, labeled device list from a discovery snapshot.

    Pure function of its inputs — no I/O, no scans, no hidden state
    (contract: specs/002-config-flow-scanning/contracts/device-list-format.md).

    Rules:
    - Addresses already configured (compared as upper-cased unique ids) are
      excluded from the list.
    - Tier 1 ("recognized"): the advertised name starts with a NAME_PREFIXES
      entry (case-insensitive) OR the advertisement exposes the ff01/ff02
      service UUIDs (opportunistic secondary signal — never hides a device).
    - Tier 2 ("other devices"): everything else, unnamed devices included.
      HA synthesizes `name = local_name or device.name or address`, so a
      device whose name equals its address never advertised a name; it is
      labeled with `no_name_text`.
    - Each tier is sorted by RSSI (nearest first); a missing or non-int RSSI
      sorts as worst (-200) but the label shows what the history holds.
    - Tier 2 is capped at MAX_OTHER_DEVICES entries; tier 1 is never capped.

    Returns ``(labels, counts)``: labels is an insertion-ordered
    ``{address: label}`` dict — insertion order is the dropdown order — and
    counts provides "recognized" and "total" for the user menu description
    placeholders.
    """
    recognized: list[tuple[int, str, str, Any]] = []
    others: list[tuple[int, str, str, Any]] = []
    for info in infos:
        address = info.address
        if address.upper() in configured:
            continue  # already configured — never listed
        name = info.name or address
        uuids = [str(uuid).lower() for uuid in (info.service_uuids or [])]
        is_recognized = name.lower().startswith(NAME_PREFIXES) or (
            UUID_WRITE in uuids or UUID_NOTIFY in uuids
        )
        # Defensive sort key: bleak types RSSI as optional.
        rssi_key = info.rssi if isinstance(info.rssi, int) else -200
        entry = (rssi_key, address, name, info.rssi)
        if is_recognized:
            recognized.append(entry)
        else:
            others.append(entry)
    recognized.sort(key=lambda item: item[0], reverse=True)
    others.sort(key=lambda item: item[0], reverse=True)
    del others[MAX_OTHER_DEVICES:]  # cap the "other devices" tier
    labels: dict[str, str] = {}
    for _rssi_key, address, name, rssi in recognized + others:
        display = name if name != address else no_name_text
        labels[address] = f"{display} — {address} — RSSI {rssi}"
    counts = {
        "recognized": len(recognized),
        "total": len(recognized) + len(others),
    }
    return labels, counts


def _probe_placeholders(address: str | None, regs: dict[int, int]) -> dict[str, str]:
    """Values shown on the confirm step, decoded from the test read."""

    def temp(addr: int) -> str:
        raw = regs.get(addr)
        if raw is None:
            return "unknown"
        if raw == registers.SENTINEL_RAW:
            return "sensor absent"
        return f"{raw / registers.TEMP_SCALE:.1f} °C"

    power_raw = regs.get(registers.REG_POWER)
    mode_raw = regs.get(registers.REG_MODE)
    mode = (
        registers.MODE_VALUE_TO_NAME.get(mode_raw)
        if mode_raw is not None
        else None
    )
    return {
        "address": address or "?",
        "power": (
            "unknown"
            if power_raw is None
            else ("on" if power_raw != 0 else "off")
        ),
        "mode": mode or (f"unknown ({mode_raw})" if mode_raw is not None else "unknown"),
        "inlet": temp(registers.REG_INLET_WATER_TEMP),
        "outlet": temp(registers.REG_OUTLET_WATER_TEMP),
        "ambient": temp(registers.REG_AMBIENT_TEMP),
    }


class AquaTempConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the aqua_temp_ble config flow."""

    VERSION = 1
    MINOR_VERSION = 0

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None
        self._regs: dict[int, int] | None = None
        self._test_error: str | None = None
        # Spec 013 FR-3: set by the push-harvest stage — the test
        # SUCCEEDED on the unit's pushed blocks (mode "push"); the
        # confirm step and the created entry read it.
        self._push_mode: bool = False
        # Spec 011 FR-2 (contract 011 §2): the running test stage —
        # stamped BEFORE each stage runs (the 007 conventions extended
        # to the probe stages); failure lines report the running stage,
        # never a later one.
        self._stage = "connect"
        # Progress-step state (specs/004): the asyncio task itself is the
        # done-flag (.done() is authoritative — a stale boolean could be
        # set by a previous attempt's task). Created once per attempt,
        # cleared when the completion is routed to the next step.
        self._test_task: asyncio.Task[None] | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._origin = "user"  # step to return to on a failed test
        self._device_labels: dict[str, str] = {}

    def _set_test_stage(self, stage: str) -> None:
        """Stage stamp callback for the security-probe helper (spec 011
        FR-2, contract 011 §2): the helper reports `security-probe`,
        `pair` and `first-read-retry` before each stage runs so the
        test-level failure lines name the stage the cancellation hit
        (contract 011 §4's pathological budget case)."""
        self._stage = stage

    async def _async_menu_labels(
        self, step_id: str, keys: tuple[str, ...]
    ) -> dict[str, str]:
        """Menu option labels for the current language, EN literals as fallback.

        Lookup key: component.<domain>.config.step.<menu_id>.menu_options.<key>
        (plan.md §6; switchbot strings precedent at 2024.12).
        """
        labels = {key: MENU_LABELS_EN[key] for key in keys}
        try:
            translations = await translation.async_get_translations(
                self.hass,
                self.hass.config.language or "en",
                "config",
                {DOMAIN},
                config_flow=True,
            )
        except Exception:  # noqa: BLE001 — labels must never break the flow
            _LOGGER.debug("could not load %s config translations", DOMAIN, exc_info=True)
            return labels
        for key in keys:
            text = translations.get(
                f"component.{DOMAIN}.config.step.{step_id}.menu_options.{key}"
            )
            if isinstance(text, str) and text:
                labels[key] = text
        return labels

    async def _async_build_device_labels(self) -> tuple[dict[str, str], dict[str, int]]:
        """Rebuild the device list from the current discovery history.

        Called on every user-menu visit and every device-form entry
        (contracts/config-flow-states.md §3): counts stay current and a
        return after a failed test re-reads the history.
        """
        return _build_device_list(
            bluetooth.async_discovered_service_info(self.hass, connectable=True),
            {unique_id.upper() for unique_id in self._async_current_ids()},
            UNNAMED_LABEL,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: a menu — pick a device, scan again, or go manual.

        A menu, never a dropdown with sentinel entries (spec 002 FR-1:
        dropdown option values are not translatable, menu labels are).
        Menu selections are routed by HA to async_step_<option key>; this
        step is therefore never re-entered with user_input.

        Empty discovery history -> the same step returns the no-devices
        presentation (pairing guidance + the two remaining actions,
        contracts/config-flow-states.md §2 note: no_devices is a menu
        step_id, not a separate step method).
        """
        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            return self.async_abort(reason="no_bluetooth_adapter")
        self._device_labels, counts = await self._async_build_device_labels()
        if not self._device_labels:
            return self.async_show_menu(
                step_id="no_devices",
                menu_options=await self._async_menu_labels(
                    "no_devices", ("scan", "manual")
                ),
            )
        return self.async_show_menu(
            step_id="user",
            menu_options=await self._async_menu_labels(
                "user", ("device", "scan", "manual")
            ),
            description_placeholders={
                "recognized": counts["recognized"],
                "total": counts["total"],
            },
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """The full discovered-device list — real addresses only, no actions.

        Every pseudo-entry is gone: the actions live in the user menu
        (FR-1/FR-4). The list is rebuilt on each entry (with user_input
        unset) so a return after a failed test shows the current history.
        """
        if user_input is not None:
            self._address = user_input["address"]
            self._name = None  # stale name from the bluetooth path must not leak
            self._origin = "device"
            await self.async_set_unique_id(self._address.upper(), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_test()
        if errors is None and self._test_error is not None:
            # A routed test failure re-renders this form through the
            # manager (user_input=None): display the stashed error once,
            # then clear it (specs/004 FR-4 — internal routing no longer
            # passes errors=; the stash is the only transport).
            errors = {"base": self._test_error}
            self._test_error = None
        self._device_labels, _counts = await self._async_build_device_labels()
        if not self._device_labels:
            # Edge case: the history drained between the menu and this
            # visit. Never render a device form without devices — the
            # menu step re-renders with its no-devices presentation. A
            # menu cannot display errors either, so drop any stashed
            # error: it must not leak into a later form render (E-6).
            self._test_error = None
            return await self.async_step_user()
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required("address"): vol.In(self._device_labels)}
            ),
            errors=errors,
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bounded rescan: grace delay for new advertisers, then back to user.

        HA's scanners already run continuously (research 002 Q3); the flow
        never starts a scan itself — the wait only gives a unit that just
        entered pairing mode time to advertise. Same progress contract as
        async_step_test (specs/004): one task per attempt, the SAME task on
        re-entries while it runs, progress_done to the user menu on
        completion. A bare sleep cannot fail except by cancellation (flow
        removed), so no error handling is needed. Repeated clicks are
        harmless (E-11): while a wait runs, a re-entry re-arms nothing.
        """
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(
                asyncio.sleep(SCAN_WAIT_SECONDS)
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scan",
                progress_task=self._scan_task,
                description_placeholders={"seconds": SCAN_WAIT_SECONDS},
            )
        self._scan_task = None
        return self.async_show_progress_done(next_step_id="user")

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Manual BLE address entry."""
        errors = errors or {}
        if user_input is None and self._test_error is not None:
            # A routed test failure re-renders this form through the
            # manager (user_input=None): display the stashed error once,
            # then clear it (specs/004 FR-4 — internal routing no longer
            # passes errors=; the stash is the only transport).
            errors = {"base": self._test_error}
            self._test_error = None
        if user_input is not None:
            address = user_input["address"].strip()
            if not MAC_RE.match(address):
                errors["address"] = "invalid_address"
            else:
                self._address = address
                self._name = None  # stale name from the bluetooth path must not leak
                self._origin = "manual"
                await self.async_set_unique_id(address.upper(), raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return await self.async_step_test()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required("address"): str}),
            errors=errors,
        )

    async def async_step_bluetooth(self, discovery_info: Any) -> ConfigFlowResult:
        """Passive discovery: HA saw a matching advertiser."""
        name = discovery_info.name or ""
        if not name.lower().startswith(NAME_PREFIXES):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address
        self._name = name
        self._origin = "user"
        await self.async_set_unique_id(self._address.upper(), raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return await self.async_step_test()

    async def async_step_test(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress step: live connection test (connect + read 45 registers).

        Progress-step contract (specs/004): HA re-enters this step when the
        progress task completes, and that re-entry may only return
        async_show_progress or async_show_progress_done — any other
        result raises ValueError in data_entry_flow and leaves the dialog
        stuck on the spinner. The task is created once per attempt and kept
        as the reference; re-entries while it runs return the SAME task
        object (HA's manager no-ops on an unchanged task).
        """
        if self._test_task is None:
            # New attempt: reset the error stash so a stale error from a
            # previous attempt can never surface on the next form render.
            self._test_error = None
            self._test_task = self.hass.async_create_task(self._async_test())

        if not self._test_task.done():
            return self.async_show_progress(
                step_id="test",
                progress_action="test",
                progress_task=self._test_task,
                description_placeholders={"address": self._address or ""},
            )

        # Task complete -> HA re-entered the step. Clear the reference so
        # the next attempt starts fresh, then hand routing back to the
        # manager: it — not this step — calls async_step_<next>(None).
        # Success -> confirm; failure -> the origin form ("manual" for a
        # manual entry, the device list otherwise, including the dormant
        # bluetooth path whose origin is "user": menus cannot display
        # errors). The stashed _test_error is consumed by that form.
        self._test_task = None
        if self._test_error is None:
            # Spec 013 FR-3: a push-mode success routes to the push
            # confirm step (the monitoring-only note + the calibration
            # checkbox, quickstart 013 §2); the classic success keeps
            # today's exact confirm step (US-6).
            return self.async_show_progress_done(
                next_step_id="confirm_push" if self._push_mode else "confirm"
            )
        if self._origin == "manual":
            return self.async_show_progress_done(next_step_id="manual")
        return self.async_show_progress_done(next_step_id="device")

    async def _async_test(self) -> None:
        """Body of the test progress task — created once per attempt.

        Never raises: every failure path stores an error key in
        _test_error AND logs one diagnosable line (spec 005 FR-2: no
        failure maps to a UI key without a log naming the address and the
        real exception — the production incident's silent path). The
        step's re-entry reads the stash after the task completes; task
        completion is the only completion signal (no flag to keep in
        sync).

        Stage + elapsed instrumentation (spec 007 FR-3, research 007
        §8): every failure line also reports the stage the test died in
        and the elapsed since entry, against the TEST_TIMEOUT budget
        (contract 007 §3.3) — log-only, the UI keys and their mapping
        are unchanged (spec 007 NFR-2).

        Spec 011 FR-2/FR-4 (contract 011 §2): on a first-read timeout the
        test runs the evidence-driven stages — the security-probe (the
        SAME base read re-sent as an ATT Write Request, so the unit's
        silent refusal becomes an ATT error: research 011 §2.1/§12 X1),
        the pairing ladder ONLY on an att-error-security verdict, then
        exactly ONE first-read retry after a succeeded/skipped pair.
        All inside the UNCHANGED TEST_TIMEOUT envelope (contract 011
        §4); the verdicts live in the log and the UI keys stay
        untouched (FR-2.5).
        """
        # Test-level stage label + elapsed (spec 007 FR-3, research 007
        # §8): the label advances as the body progresses — "connect"
        # while connect() runs, "first-read" for the base read, and the
        # spec-011 probe stages (security-probe / pair / first-read-
        # retry) stamped BEFORE each runs via self._stage (contract 011
        # §2). The "disconnect" label is set only after the teardown
        # completes, so a failure raised by the READ still reports the
        # read's stage (the failure site, not the subsequent cleanup).
        t_test = time.monotonic()
        # Spec 012 FR-2 (contract 012 §3): the test's first line names
        # the running build at WARNING — the panel the operator reads
        # stores only WARNING+ (research 012 §4), and a marker mismatch
        # on an "updated" install means the files never loaded (the
        # stale-module world, research 012 §5).
        _LOGGER.warning(
            "connection test to %s: build %s", self._address, BUILD_VERSION
        )
        self._stage = "connect"
        self._push_mode = False  # spec 013 FR-3: reset per attempt
        try:
            async with asyncio.timeout(TEST_TIMEOUT):
                # hass is passed so device resolution reads the HA
                # bluetooth caches explicitly (never raw scans inside
                # Home Assistant — spec 005 FR-7).
                client = AquaTempBleClient(
                    self._address, name=self._name, hass=self.hass
                )
                # max_attempts=2 (spec 006 FR-3): the vendor app itself
                # retries connects before giving up (research 006 §2.2);
                # inside the 70 s budget a second 20 s attempt now fits and
                # catches transient refusals — the phone releasing the
                # single BLE link mid-attempt, or stale bluez state cleared
                # by the first attempt's cleanup.
                await client.connect(max_attempts=2)
                self._stage = "first-read"
                regs: dict[int, int] | None = None
                try:
                    # Spec 009 FR-2 (contract 009 §5): the bounded
                    # app-parity re-send — the identical vendor frame,
                    # 2 s apart, up to 3 extra sends, all inside the
                    # unchanged 8.0 s request window (the app's 2 s
                    # Duration may be a poll cadence, research 009 §1.3).
                    regs = await client.read_registers(
                        registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT,
                        resend_every=2.0, resend_max=3,
                    )
                except AquaTempTimeoutError as first_timeout:
                    # Spec 011 FR-2 (contract 011 §2): the first-read
                    # timeout triggers the probe stages on the SAME live
                    # session (strictly after the read's deadline, before
                    # the disconnect in the finally below). The helper
                    # never raises AquaTempTimeoutError and returns the
                    # answered regs or None; None re-raises the ORIGINAL
                    # timeout so the UI keeps its no_response key and its
                    # advice (FR-2.5: UI keys unchanged).
                    regs = await run_security_probe(
                        client,
                        retry_resend_every=2.0,
                        retry_resend_max=3,
                        on_stage=self._set_test_stage,
                    )
                    if regs is None:
                        # Spec 013 FR-3 (contract 013 §2): the push-harvest
                        # stage — ONLY after the read timed out AND the
                        # probe stayed silent (the att-accepted-silent
                        # world, research 013 §0.5): a pure-listening window
                        # on the already-subscribed notify pipe. ZERO
                        # frames sent (contract 013 §1) — nothing for the
                        # application-layer gate to drop. A CRC-valid push
                        # of the T-family block (start 2046, data-model
                        # 013 §6) makes the test SUCCEED with mode "push"
                        # (US-1); no push re-raises the ORIGINAL timeout:
                        # the no_response key + message stay byte-identical
                        # (FR-3, US-6).
                        if not await self._async_push_harvest(client):
                            raise first_timeout
                finally:
                    await client.disconnect()
                self._regs = regs
                self._stage = "disconnect"
        except AquaTempDeviceNotFoundError as exc:
            # Order matters: an AquaTempError but NOT an AquaTempConnectionError
            # — catching it first keeps the mapping exact (plan 005 §2).
            self._test_error = "not_found"
            # Stage "resolve" (contract 007 §3.3): this exception can
            # only come from the resolution tiers — the device was never
            # found (research 007 §8 signature table).
            _LOGGER.warning(
                "connection test to %s: device not found: %s "
                "at stage resolve after %.1f s (budget %.0f s)",
                self._address,
                exc,
                time.monotonic() - t_test,
                TEST_TIMEOUT,
            )
        except AquaTempConnectionError as exc:
            self._test_error = "cannot_connect"
            # Stage from the body's progress (contract 007 §3.3): the
            # connect() call as a whole reports "connect"; the precise
            # sub-stage of a post-setup failure is carried by the
            # aqua_client ERROR line (contract 007 §3.2, research 007
            # §8) — this line is the test-level view. Spec 011: a
            # first-read-retry failure reports its own stage via
            # self._stage (contract 011 §2).
            _LOGGER.warning(
                "connection test to %s failed: %s "
                "at stage %s after %.1f s (budget %.0f s)",
                self._address,
                exc_chain(exc),
                self._stage,
                time.monotonic() - t_test,
                TEST_TIMEOUT,
            )
        except AquaTempTimeoutError as exc:
            self._test_error = "no_response"
            # Stage "first-read" (contract 007 §3.3): the request
            # deadline only expires during the base read.
            _LOGGER.warning(
                "connection test to %s: no response: %s "
                "at stage first-read after %.1f s (budget %.0f s)",
                self._address,
                exc_chain(exc),
                time.monotonic() - t_test,
                TEST_TIMEOUT,
            )
        except AquaTempResponseError as exc:
            self._test_error = "crc_error"
            # Stage "first-read" (contract 007 §3.3): an invalid frame
            # only surfaces during the base read.
            _LOGGER.warning(
                "connection test to %s: bad response: %s "
                "at stage first-read after %.1f s (budget %.0f s)",
                self._address,
                exc_chain(exc),
                time.monotonic() - t_test,
                TEST_TIMEOUT,
            )
        except TimeoutError:
            # The asyncio.timeout budget expired and cancelled the
            # connect/read in flight — the pre-005 code swallowed this
            # silently (research 005 §4.2). The budget value and the
            # likely causes go to the log; the user-facing key stays
            # cannot_connect (its advice is the correct action).
            self._test_error = "cannot_connect"
            # Stage = the phase the cancellation hit (contract 007
            # §3.3): "connect" or "first-read" — or, since spec 011,
            # a probe stage (security-probe / pair / first-read-retry:
            # contract 011 §4's accepted pathological expiry mid-probe
            # — the label was current when asyncio.timeout fired,
            # research 007 §8).
            _LOGGER.warning(
                "connection test to %s exceeded the %.0f s budget — two "
                "connect attempts and the read did not complete in time. "
                "The unit is likely not accepting the connection (single "
                "BLE link held by the vendor app?) or is out of range. "
                "at stage %s after %.1f s (budget %.0f s)",
                self._address,
                TEST_TIMEOUT,
                self._stage,
                time.monotonic() - t_test,
                TEST_TIMEOUT,
            )
        except Exception:  # noqa: BLE001
            # Stage "unknown" (contract 007 §3.3): an unexpected
            # exception has no reliable site — the stage label says so
            # instead of guessing (research 007 §8).
            _LOGGER.exception(
                "unexpected error during connection test "
                "at stage unknown after %.1f s (budget %.0f s)",
                time.monotonic() - t_test,
                TEST_TIMEOUT,
            )
            self._test_error = "cannot_connect"
        else:
            self._test_error = None

    async def _async_push_harvest(self, client: Any) -> bool:
        """The push-harvest stage (spec 013 FR-3, contract 013 §2.3).

        Runs ONLY after the first read timed out AND the security probe
        returned None (the att-accepted-silent world — the 0.8.0
        production trace, research 013 §1): a pure-listening window on
        the already-subscribed notify pipe. ZERO frames are sent
        (contract 013 §1) — the firmware's application-layer gate has
        nothing to drop. Success criterion: >= 1 CRC-valid push of the
        T-family block (start 2046, data-model 013 §6) -> True (the
        test SUCCEEDS with mode "push", US-1). No 2046 within the
        window -> False and the caller re-raises its ORIGINAL first-read
        timeout: the no_response key + message stay byte-identical
        (FR-3, US-6). The evidence lines are WARNING (contract 013 §2.3
        — decisive-once, the panel keeps WARNING+ only, contract 012
        §2); the success summary's T-window raws are the calibration
        data source (quickstart 013 §2).
        """
        # Stage label per the 007/011 conventions (contract 011 §2):
        # stamped BEFORE the stage runs so a budget expiry mid-harvest
        # reports the stage it hit, never a later one.
        self._stage = "push-harvest"
        _LOGGER.warning(
            "push harvest: listening %.0f s for block pushes (no frames sent)",
            PUSH_HARVEST_TIMEOUT,
        )
        caps = await client.harvest_pushes(
            {1001, 1046, 2001, 2046},  # the four observed starts [P] (research 013 §2)
            PUSH_HARVEST_TIMEOUT,
        )
        t_cap = caps.get(registers.PUSH_TEMP_BLOCK_START)
        if t_cap is None:
            _LOGGER.warning(
                "push harvest: no block pushes within %.0f s",
                PUSH_HARVEST_TIMEOUT,
            )
            return False
        raws = t_cap.registers
        _LOGGER.warning(
            "push harvest: T-block captured (start=%d ×%d); "
            "T-window raws: 2046=%d 2047=%d 2048=%d 2049=%d 2050=%d 2051=%d",
            registers.PUSH_TEMP_BLOCK_START,
            t_cap.count,
            raws.get(2046, 0),
            raws.get(2047, 0),
            raws.get(2048, 0),
            raws.get(2049, 0),
            raws.get(2050, 0),
            raws.get(2051, 0),
        )
        self._push_mode = True
        return True

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Summary of the test read + friendly device name."""
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({vol.Required("name", default=DEFAULT_NAME): str}),
                description_placeholders=_probe_placeholders(
                    self._address, self._regs or {}
                ),
            )
        return self.async_create_entry(
            title=user_input["name"],
            data={"address": self._address, "name": user_input["name"]},
            options={
                "poll_interval": DEFAULT_POLL_INTERVAL,
                "temperature_unit": DEFAULT_TEMPERATURE_UNIT,
            },
        )

    async def async_step_confirm_push(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The push-mode confirm step (spec 013 FR-3/FR-5).

        Same summary placeholders as the classic confirm step — all
        "unknown" here (no base answer exists in push mode; no
        unconfirmed register is ever displayed under a certain name,
        contract 013 §4.3) — plus the push-mode note (the confirm-step
        UI string, FR-3/FR-6.1): monitoring entities only; control,
        the fault code and the firmware version are absent until the
        request path revives (spec 013 §6 — no promise of control).
        The `push_temp_confirmed` checkbox is the calibration gate
        (FR-5, quickstart 013 §2): the owner checks it ONLY after the
        side-by-side run matched the app's T-family display (contract
        013 §5); a mismatch is reported and pins nothing.
        The `push_control_confirmed` checkbox is the CONTROL gate
        (spec 015 FR-6, contract 015-A §4): the owner checks it ONLY
        after the power/mode diff-revert (quickstart 015 §1/§5) matched
        the app's display — it unlocks the READ-ONLY climate (FR-8);
        a mismatch closes the gate and pins nothing.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_push",
                data_schema=vol.Schema(
                    {
                        vol.Required("name", default=DEFAULT_NAME): str,
                        vol.Optional("push_temp_confirmed", default=False): bool,
                        vol.Optional("push_control_confirmed", default=False): bool,
                    }
                ),
                description_placeholders=_probe_placeholders(
                    self._address, self._regs or {}
                ),
            )
        return self.async_create_entry(
            title=user_input["name"],
            data={
                "address": self._address,
                "name": user_input["name"],
                # Spec 013 FR-3: the entry's read plane — "push" entries
                # never send request frames (the coordinator's push mode,
                # FR-4); classic entries keep today's exact shape (no
                # mode field, US-6).
                "mode": "push",
            },
            options={
                "poll_interval": DEFAULT_POLL_INTERVAL,
                "temperature_unit": DEFAULT_TEMPERATURE_UNIT,
                # Spec 013 FR-5: the calibration gate, stored on the
                # entry (contract 013 §5.3 — the shipped default False).
                "push_temp_confirmed": bool(
                    user_input.get("push_temp_confirmed", False)
                ),
                # Spec 015 FR-6: the control confirmation gate — the
                # shipped default False (contract 015-A §4); checking it
                # unlocks the READ-ONLY push-mode climate after a reload.
                "push_control_confirmed": bool(
                    user_input.get("push_control_confirmed", False)
                ),
            },
        )

    @staticmethod
    def async_get_options_flow(entry: Any) -> AquaTempOptionsFlowHandler:
        # HA calls this getter synchronously (config_entries.OptionsFlowManager),
        # so no @callback marker is needed here.
        return AquaTempOptionsFlowHandler()


class AquaTempOptionsFlowHandler(OptionsFlow):
    """Options: poll interval (10..600 s), display temperature unit and
    the evidence-gated pair_on_connect toggle (spec 011 FR-6).

    Changes apply without an entry reload (see __init__ listener); the unit
    is read dynamically by the climate entity.

    pair_on_connect (spec 011 FR-6, default OFF): when enabled, every
    establish requests pairing (the client's connect(pair=True)
    threading). It ships OFF and is to be enabled ONLY after a
    connection test produced the `security probe: answered after
    pairing` evidence (quickstart 011 §2) — enabling it without that
    proof is a contract violation (the option exists to be earned,
    contract 011 §1.5). A backend that cannot pair degrades to a plain
    connect with a WARNING — the option never breaks the poll path
    (FR-7).
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options
        schema: dict[Any, Any] = {
            vol.Required(
                "poll_interval",
                default=current.get("poll_interval", DEFAULT_POLL_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
            vol.Required(
                "temperature_unit",
                default=current.get("temperature_unit", DEFAULT_TEMPERATURE_UNIT),
            ): vol.In({"C": "Celsius (°C)", "F": "Fahrenheit (°F)"}),
            # Spec 011 FR-6: the evidence-gated pairing lever — OFF
            # until the connection test proved the bond gate (the
            # `security probe: answered after pairing` line).
            vol.Required(
                "pair_on_connect",
                default=current.get("pair_on_connect", DEFAULT_PAIR_ON_CONNECT),
            ): bool,
        }
        if str(self.config_entry.data.get("mode", "classic")) == "push":
            # Spec 013 FR-5 (contract 013 §5.3): the calibration gate's
            # post-setup flip — the owner matches the captured T-window
            # against the app's T-family display (quickstart 013 §2),
            # then reloads so the temperature entities appear. Classic
            # entries keep today's exact options form (US-6).
            schema[
                vol.Required(
                    "push_temp_confirmed",
                    default=current.get("push_temp_confirmed", False),
                )
            ] = bool
            # Spec 015 FR-6 (contract 015-A §4): the CONTROL gate's
            # post-setup flip — the owner re-verifies the pinned control
            # homes (power 2011 / mode 2019, quickstart 015 §5) against
            # the app's display, then reloads so the READ-ONLY climate
            # appears (spec 015 FR-8).
            schema[
                vol.Required(
                    "push_control_confirmed",
                    default=current.get("push_control_confirmed", False),
                )
            ] = bool
            # Spec 017 FR-1 (contract 017 §2): the write-control
            # ACTIVATION — the owner's act alone, never automatic. The
            # checkbox ships False (the integration stays
            # byte-identical to 0.12.0); its description names the
            # required probe verdict (the `power write probe:
            # EXECUTED + REVERSIBLE` line, contract 016 §4's discipline
            # surfaced to the UI). Checking it opens the climate's
            # power-control surface (FR-5): the entities read the
            # coordinator's gate live, so saving re-renders them — no
            # Home Assistant restart needed (the push_control_confirmed
            # precedent for the option's lifetime; the global
            # registers.WRITE_CONTROL_PROVEN switch stays the
            # alternative path).
            schema[
                vol.Required(
                    "write_control",
                    default=current.get("write_control", DEFAULT_WRITE_CONTROL),
                )
            ] = bool
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))