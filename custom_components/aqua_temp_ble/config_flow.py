"""Config flow for aqua_temp_ble: discovery, manual address, live test, confirm."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)

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
    NAME_PREFIXES,
)

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
TEST_TIMEOUT = 20.0  # budget for the whole connection test, seconds
DEFAULT_NAME = "Aqua Temp heat pump"


async def _discovered_devices(hass: Any) -> dict[str, str]:
    """Matching devices from the HA bluetooth cache: {address: label}."""
    devices: dict[str, str] = {}
    for info in bluetooth.async_discovered_service_info(hass, connectable=True):
        name = info.name or ""
        if name.lower().startswith(NAME_PREFIXES):
            devices.setdefault(
                info.address, f"{name} — {info.address} — RSSI {info.rssi}"
            )
    return devices


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
        self._test_done = False
        self._origin = "user"  # step to return to on a failed test

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Choose among discovered matching devices (falls back to manual)."""
        if user_input is not None:
            self._address = user_input["address"]
            self._origin = "user"
            await self.async_set_unique_id(self._address.upper(), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_test()
        devices = await _discovered_devices(self.hass)
        if not devices:
            self._origin = "manual"
            return await self.async_step_manual(errors=errors)
        schema = vol.Schema({vol.Required("address"): vol.In(devices)})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Manual BLE address entry."""
        errors = errors or {}
        if user_input is not None:
            address = user_input["address"].strip()
            if not MAC_RE.match(address):
                errors["address"] = "invalid_address"
            else:
                self._address = address
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
        """Progress step: live connection test (connect + read 45 registers)."""
        if self._test_done:
            self._test_done = False
            error, self._test_error = self._test_error, None
            if error is None:
                return await self.async_step_confirm()
            # Failed -> back to the originating form with a retry.
            if self._origin == "manual":
                return await self.async_step_manual(errors={"base": error})
            return await self.async_step_user(errors={"base": error})

        async def _async_test() -> None:
            try:
                async with asyncio.timeout(TEST_TIMEOUT):
                    client = AquaTempBleClient(self._address, name=self._name)
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
            finally:
                self._test_done = True

        return self.async_show_progress(
            step_id="test",
            progress_task=_async_test(),
            description_placeholders={"address": self._address or ""},
        )

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