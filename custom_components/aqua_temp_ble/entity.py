"""Shared entity base for the aqua_temp_ble platforms."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AquaTempCoordinator


class AquaTempEntity(CoordinatorEntity[AquaTempCoordinator]):
    """Base entity: CoordinatorEntity + DeviceInfo + unique_id."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        address = entry.data["address"]
        self._address = address
        self._attr_unique_id = f"{address.upper()}_{key}"
        info: dict[str, Any] = {
            "identifiers": {(DOMAIN, address)},
            "name": entry.title,
            "manufacturer": "PHNIX",
            "model": "Aqua Temp (BlueNRG)",
        }
        if coordinator.data is not None and coordinator.data.firmware:
            info["sw_version"] = coordinator.data.firmware
        self._attr_device_info = DeviceInfo(**info)