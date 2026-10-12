"""Binary sensor platform for aqua_temp_ble — the fault exposure
(spec 023).

ONE entity, created for push-mode entries only: the Fault binary
sensor (device_class problem), reading the pushed fault word
register 2074 — the owner's E03 diff-revert (0 -> 256 -> 0,
2026-10-12; research 023 §1). The 021 prune removed registry
placeholders with no pinned register; this platform returns WITHOUT
a registry: one literal entity class, so no dead placeholder can
ever reappear. Classic-mode entries create nothing (register 47 is
unreachable — law L1, spec 020 §5).

The code label rides the attributes as the APP-oracle record: the
vendor's own app names the codes (E03 = "water flow problem" [P
owner, app display 2026-10-12] — registers.PUSHED_FAULT_CODE_APP_
LABELS). It is a code label, never a bit name: which protection bit
8 names stays unproven (contract 023 §3).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator
from .entity import AquaTempEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if str(entry.data.get("mode", "classic")) != "push":
        return  # law L1: the classic fault home (reg 47) is unreachable
    async_add_entities([AquaTempFaultBinarySensor(coordinator, entry)])


class AquaTempFaultBinarySensor(AquaTempEntity, BinarySensorEntity):
    """The fault word 2074 as a problem binary (spec 023, FR-2/FR-3).

    ON when the raw is non-zero, OFF when it reads the healthy 0,
    unknown when the register is absent — never a guessed OFF; a
    stale stream fails the cycle and the entity goes unavailable with
    every other entity (the freshness gate). The attributes carry the
    evidence (raw, hex, the set-bit list, the code string when the
    raw has a PROVEN code, and the vendor app's own label for that
    code when recorded — E03's app label is "water flow problem"
    [P owner, app display 2026-10-12]; a code never read in the app
    carries no label, never a guess).
    """

    _attr_name = "Fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "fault")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return None
        # The 2046-block capture feeds data.registers in push mode
        # (coordinator.from_pushes); 2074 rides it like the temps.
        return registers.decode_fault_flag(
            data.registers.get(registers.REG_PUSHED_FAULT)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None or data.mode != "push":
            return {}
        raw = data.registers.get(registers.REG_PUSHED_FAULT)
        attrs: dict[str, Any] = {"register": registers.REG_PUSHED_FAULT}
        if raw is not None:
            attrs["raw"] = raw
            attrs["hex"] = f"0x{raw & 0xFFFF:04X}"
            attrs["set_bits"] = registers.fault_set_bits(raw)
            code = registers.fault_code(raw)
            if code is not None:
                attrs["code"] = code
                # The app-oracle label (spec 023, parent brief
                # 2026-10-12): the vendor's own app names the code
                # [P owner, app display]. A code with no recorded
                # app label simply carries none (no guessing);
                # never a bit name (contract 023 §3).
                label = registers.fault_code_app_label(raw)
                if label is not None:
                    attrs["code_label"] = label
        return attrs