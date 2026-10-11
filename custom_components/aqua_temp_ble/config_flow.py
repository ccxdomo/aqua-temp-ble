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
    AquaTempResponseError,
    AquaTempTimeoutError,
)
from .const import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    MAX_OTHER_DEVICES,
    NAME_PREFIXES,
    SCAN_WAIT_SECONDS,
    UUID_NOTIFY,
    UUID_WRITE,
)

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
TEST_TIMEOUT = 20.0  # budget for the whole connection test, seconds
DEFAULT_NAME = "Aqua Temp heat pump"

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
        # Progress-step state (specs/004): the asyncio task itself is the
        # done-flag (.done() is authoritative — a stale boolean could be
        # set by a previous attempt's task). Created once per attempt,
        # cleared when the completion is routed to the next step.
        self._test_task: asyncio.Task[None] | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._origin = "user"  # step to return to on a failed test
        self._device_labels: dict[str, str] = {}

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
            return self.async_show_progress_done(next_step_id="confirm")
        if self._origin == "manual":
            return self.async_show_progress_done(next_step_id="manual")
        return self.async_show_progress_done(next_step_id="device")

    async def _async_test(self) -> None:
        """Body of the test progress task — created once per attempt.

        Never raises: every failure path stores an error key in
        _test_error, success stores None. The step's re-entry reads the
        stash after the task completes; task completion is the only
        completion signal (no flag to keep in sync).
        """
        try:
            async with asyncio.timeout(TEST_TIMEOUT):
                # hass is passed so device resolution prefers the HA
                # bluetooth cache before falling back to bleak scanning.
                client = AquaTempBleClient(
                    self._address, name=self._name, hass=self.hass
                )
                await client.connect()
                try:
                    self._regs = await client.read_registers(
                        registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
                    )
                finally:
                    await client.disconnect()
        except AquaTempConnectionError:
            self._test_error = "cannot_connect"
        except AquaTempTimeoutError:
            self._test_error = "no_response"
        except AquaTempResponseError:
            self._test_error = "crc_error"
        except TimeoutError:
            self._test_error = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("unexpected error during connection test")
            self._test_error = "cannot_connect"
        else:
            self._test_error = None

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

    @staticmethod
    def async_get_options_flow(entry: Any) -> AquaTempOptionsFlowHandler:
        # HA calls this getter synchronously (config_entries.OptionsFlowManager),
        # so no @callback marker is needed here.
        return AquaTempOptionsFlowHandler()


class AquaTempOptionsFlowHandler(OptionsFlow):
    """Options: poll interval (10..600 s) and display temperature unit.

    Changes apply without an entry reload (see __init__ listener); the unit
    is read dynamically by the climate entity.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    "poll_interval",
                    default=current.get("poll_interval", DEFAULT_POLL_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
                vol.Required(
                    "temperature_unit",
                    default=current.get("temperature_unit", DEFAULT_TEMPERATURE_UNIT),
                ): vol.In({"C": "Celsius (°C)", "F": "Fahrenheit (°F)"}),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)