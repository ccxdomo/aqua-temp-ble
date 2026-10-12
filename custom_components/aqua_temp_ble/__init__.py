"""The Aqua Temp BLE integration: setup/unload, options, probe service.

Entry flow (BLUEPRINT §5/§6): create client + coordinator ->
`async_config_entry_first_refresh()` (failure -> ConfigEntryNotReady) ->
forward platforms -> register the domain service once. Unload reverses all
of it; when the last entry is unloaded the service is removed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .aqua_client import AquaTempBleClient, AquaTempError
from .const import DEFAULT_POLL_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import AquaTempCoordinator

_LOGGER = logging.getLogger(__name__)

# Blocks accepted by the probe service (spec 021, tasks.md A3): "base"
# plus the four blocks of the unit's answer burst. On push entries
# "base" means trigger + dump of the pushed store (the unit never
# answers the classic FC03 read — law L1, spec 020); the burst blocks
# answer a query directly. The app's other recovered query blocks
# (1091/1136) are never in the burst — a guaranteed timeout — and are
# gone from the selector. No writes are ever sent to blocks 1001-2090.
PROBE_BLOCKS = {"base", "1001", "1046", "2001", "2046"}

PROBE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): vol.Any(str, [str]),
        vol.Optional("block", default="base"): vol.In(PROBE_BLOCKS),
    }
)

# Spec 016 FR-5: the power-write probe service's schema (the
# probe_registers precedent, minus the block field — the probe reads
# one fixed register set: the 2001-block mirror 2011).
POWER_PROBE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): vol.Any(str, [str]),
    }
)

# Spec 019 FR-7: the setpoint write probe service's schema — the
# power_write_probe service's schema twin (the probe reads one fixed
# register set: the 1046-block mirror 1046).
SETPOINT_PROBE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): vol.Any(str, [str]),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Aqua Temp heat pump from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    address = entry.data["address"]
    client = AquaTempBleClient(address, name=entry.data.get("name"), hass=hass)
    coordinator = AquaTempCoordinator(hass, entry, client)
    hass.data[DOMAIN][entry.entry_id] = {"client": client, "coordinator": coordinator}

    # Raises ConfigEntryNotReady on failure -> HA retries with backoff.
    await coordinator.async_config_entry_first_refresh()

    # Publish the firmware version to the device registry at the first cycle
    # that reports it (entities embed DeviceInfo only once, at add time).
    entry.async_on_unload(_async_track_firmware(hass, entry, coordinator))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one config entry; remove the service when the last one goes."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        try:
            await data["client"].disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("disconnect during unload failed", exc_info=True)
        if not hass.data[DOMAIN]:
            # Spec 016 FR-5 / Spec 019 FR-7: all three domain services go
            # with the last entry (the guarded loop keeps the
            # probe_registers removal behavior-identical — the services
            # are always registered when an entry exists).
            for service in (
                "probe_registers",
                "power_write_probe",
                "setpoint_write_probe",
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option changes without reloading: poll interval (unit is dynamic)."""
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.update_interval = timedelta(
        seconds=entry.options.get("poll_interval", DEFAULT_POLL_INTERVAL)
    )
    # Re-render every entity right away: the display unit is a property read
    # at state-write time, so without this the °C/°F switch would only become
    # visible at the next poll (up to `poll_interval` seconds later).
    coordinator.async_update_listeners()


@callback
def _async_track_firmware(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: AquaTempCoordinator
) -> Callable[[], None]:
    """Publish the firmware to the device registry as soon as it is known.

    HA merges ``DeviceInfo`` into the device registry only when entities are
    added, so a firmware string that arrives later (registers 48..51 read as
    sentinel during the first cycles, or after a vendor firmware update)
    would never reach the registry. This listener patches ``sw_version`` at
    the very cycle that reports it, and stays registered so a firmware
    upgrade on the unit is reflected too.
    """
    device_registry = dr.async_get(hass)
    address = entry.data["address"]

    @callback
    def _async_update_firmware() -> None:
        data = coordinator.data
        if data is None or data.firmware is None:
            return
        device = device_registry.async_get_device(identifiers={(DOMAIN, address)})
        if device is None or device.sw_version == data.firmware:
            return
        device_registry.async_update_device(device.id, sw_version=data.firmware)

    return coordinator.async_add_listener(_async_update_firmware)


def _resolve_probe_coordinators(
    hass: HomeAssistant, device_ids: list[str] | None
) -> dict[str, AquaTempCoordinator]:
    """Resolve the target coordinators for a probe service call (the
    probe_registers precedent): explicit device_ids -> their entries;
    no ids -> every configured entry. Raises HomeAssistantError with the
    same messages for an unknown device, a foreign device and an empty
    integration (shared by both probe services — spec 016 FR-5)."""
    coordinators: dict[str, AquaTempCoordinator] = {}
    if device_ids:
        registry = dr.async_get(hass)
        for device_id in device_ids:
            device = registry.async_get(device_id)
            if device is None:
                raise HomeAssistantError(f"unknown device: {device_id}")
            for entry_id in device.config_entries:
                if entry_id in hass.data.get(DOMAIN, {}):
                    coordinators[entry_id] = hass.data[DOMAIN][entry_id][
                        "coordinator"
                    ]
        if not coordinators:
            raise HomeAssistantError(
                f"device {device_ids[0]} is not set up by {DOMAIN}"
            )
    else:
        for entry_id, data in hass.data.get(DOMAIN, {}).items():
            coordinators[entry_id] = data["coordinator"]
    if not coordinators:
        raise HomeAssistantError(f"no {DOMAIN} devices configured")
    return coordinators


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain-level probe services exactly once."""
    if not hass.services.has_service(DOMAIN, "probe_registers"):

        async def _async_probe_registers(call: ServiceCall) -> dict[str, Any]:
            """Read a raw register block and log it for calibration (§4)."""
            block = str(call.data.get("block", "base"))
            device_ids = call.data.get("device_id")
            if isinstance(device_ids, str):
                device_ids = [device_ids]
            coordinators = _resolve_probe_coordinators(hass, device_ids)

            result: dict[str, Any] = {}
            for coordinator in coordinators.values():
                address = coordinator.config_entry.data["address"]
                try:
                    probe = await coordinator.async_probe_block(block)
                except AquaTempError as err:
                    _LOGGER.error(
                        "probe of %s (block %s) failed: %s", address, block, err
                    )
                    result[address] = {"block": block, "error": str(err)}
                else:
                    result.update(probe)
            return result

        hass.services.async_register(
            DOMAIN,
            "probe_registers",
            _async_probe_registers,
            schema=PROBE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.debug("service %s.probe_registers registered", DOMAIN)

    # Spec 016 FR-5: the power-write probe service — the owner-triggered
    # diff-revert proof (quickstart 016 §1's Developer-Tools path; the
    # diagnostic button is the other surface). Registered independently
    # so a reload that already carries probe_registers still gains the
    # new service.
    if not hass.services.has_service(DOMAIN, "power_write_probe"):

        async def _async_power_write_probe(call: ServiceCall) -> dict[str, Any]:
            """Run the power-write proof probe on the target device(s)."""
            device_ids = call.data.get("device_id")
            if isinstance(device_ids, str):
                device_ids = [device_ids]
            coordinators = _resolve_probe_coordinators(hass, device_ids)

            result: dict[str, Any] = {}
            for coordinator in coordinators.values():
                address = coordinator.config_entry.data["address"]
                try:
                    result[address] = await coordinator.async_power_write_probe()
                except AquaTempError as err:
                    # A connect/trigger transport failure — the probe logs
                    # its stage WARNING line itself; this is the service
                    # report (the probe_registers precedent).
                    _LOGGER.error(
                        "power write probe of %s failed: %s", address, err
                    )
                    result[address] = {"error": str(err)}
            return result

        hass.services.async_register(
            DOMAIN,
            "power_write_probe",
            _async_power_write_probe,
            schema=POWER_PROBE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.debug("service %s.power_write_probe registered", DOMAIN)

    # Spec 019 FR-7: the setpoint write probe service — the owner-triggered
    # two-phase proof (quickstart 019 §1's Developer-Tools path; the
    # diagnostic button is the other surface). Registered independently
    # so a reload that already carries the sibling services still gains
    # the new one (the power_write_probe precedent).
    if not hass.services.has_service(DOMAIN, "setpoint_write_probe"):

        async def _async_setpoint_write_probe(call: ServiceCall) -> dict[str, Any]:
            """Run the two-phase setpoint proof probe on the target device(s)."""
            device_ids = call.data.get("device_id")
            if isinstance(device_ids, str):
                device_ids = [device_ids]
            coordinators = _resolve_probe_coordinators(hass, device_ids)

            result: dict[str, Any] = {}
            for coordinator in coordinators.values():
                address = coordinator.config_entry.data["address"]
                try:
                    result[address] = await coordinator.async_setpoint_write_probe()
                except AquaTempError as err:
                    # A connect/trigger transport failure — the probe logs
                    # its stage WARNING line itself; this is the service
                    # report (the power_write_probe precedent).
                    _LOGGER.error(
                        "setpoint write probe of %s failed: %s", address, err
                    )
                    result[address] = {"error": str(err)}
            return result

        hass.services.async_register(
            DOMAIN,
            "setpoint_write_probe",
            _async_setpoint_write_probe,
            schema=SETPOINT_PROBE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.debug("service %s.setpoint_write_probe registered", DOMAIN)