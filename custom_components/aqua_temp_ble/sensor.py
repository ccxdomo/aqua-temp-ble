"""Sensor platform for aqua_temp_ble — registry-driven.

Entities come from registers.SENSORS; entries whose address is None (not yet
verified live) are skipped entirely, so no unconfirmed data can ever appear
under a label. A pinned address must also lie inside the polled base block
(7..51) — outside it the entity is skipped with a loud warning (FR-7:
high-block polling is a future phase).

Spec 013 (FR-5/FR-6) adds the pushed-plane surface: entries with
mode == "push" get the mirrored base keys (inlet/outlet/ambient —
values from the pushed registers only once the calibration gate is
open, `unknown` until then) plus the push-only T-family keys behind
the same gate, plus the push_stream diagnostic. fault_code, firmware
and the climate entity are NOT created in push mode — their data has
no identified pushed home [P/V] (contract 013 §4.4). Classic entries
keep today's exact creation rules (US-6).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers
from .const import DOMAIN
from .coordinator import AquaTempCoordinator, decode_temp_logged
from .entity import AquaTempEntity
from .registers import RegDef

_LOGGER = logging.getLogger(__name__)

SENSOR_NAMES: dict[str, str] = {
    "inlet_water_temp": "Inlet water temperature",
    "outlet_water_temp": "Outlet water temperature",
    "ambient_temp": "Ambient temperature",
    "suction_temp": "Suction temperature",
    "coil_temp": "Coil temperature",
    "exhaust_temp": "Exhaust temperature",
    "fault_code": "Fault code",
    "firmware": "Firmware",
    # Spec 013 FR-6: the push-stream health diagnostic (push-mode entries).
    "push_stream": "Push stream",
}


def _base_block_end() -> int:
    """Last polled base-block register (7..51) — for FR-7 gating messages."""
    return registers.BASE_BLOCK_START + registers.BASE_BLOCK_COUNT - 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquaTempCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if str(entry.data.get("mode", "classic")) == "push":
        # Spec 013 FR-5/FR-6 (contract 013 §4): the pushed-plane entity
        # surface — monitoring only. The mirrored base keys are created
        # (decoding `unknown` until the calibration gate opens, contract
        # 013 §4.3); the push-only T-family keys are created only when
        # the gate is open; fault_code/firmware have no pushed home [P]
        # and are NOT created (§4.4); the push_stream diagnostic always
        # rides along (FR-6).
        confirmed = coordinator.push_temp_confirmed
        entities: list[AquaTempSensorEntity | AquaTempPushStreamEntity] = []
        for regdef in registers.SENSORS:
            if regdef.key not in registers.PUSHED_TEMPS:
                _LOGGER.debug(
                    "sensor %s skipped in push mode: no pushed register "
                    "(spec 013 FR-5)",
                    regdef.key,
                )
                continue
            if regdef.key not in registers.PUSHED_MIRRORS and not confirmed:
                _LOGGER.debug(
                    "sensor %s skipped: push temperatures not confirmed "
                    "yet (quickstart 013 §2)",
                    regdef.key,
                )
                continue
            entities.append(AquaTempSensorEntity(coordinator, entry, regdef))
        entities.append(AquaTempPushStreamEntity(coordinator, entry))
        async_add_entities(entities)
        return
    entities = []
    for regdef in registers.SENSORS:
        if regdef.address is None:
            _LOGGER.debug(
                "sensor %s skipped: register not calibrated yet", regdef.key
            )
            continue
        if not registers.in_base_block(regdef.address):
            _LOGGER.warning(
                "sensor %s skipped: register %d is outside the polled base "
                "block %d..%d; high-block polling is not implemented "
                "(spec 003 §8)",
                regdef.key,
                regdef.address,
                registers.BASE_BLOCK_START,
                _base_block_end(),
            )
            continue
        entities.append(AquaTempSensorEntity(coordinator, entry, regdef))
    async_add_entities(entities)


class AquaTempSensorEntity(AquaTempEntity, SensorEntity):
    """One temperature/fault/firmware sensor driven by a RegDef."""

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
        regdef: RegDef,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, regdef.key)
        self._regdef = regdef
        self._attr_name = SENSOR_NAMES.get(regdef.key, regdef.key)
        self._attr_device_class = regdef.device_class
        self._attr_native_unit_of_measurement = regdef.unit
        self._attr_state_class = regdef.state_class
        self._attr_icon = regdef.icon
        if regdef.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | int | str | None:
        data = self.coordinator.data
        if data is None:
            return None
        if data.mode == "push":
            # Spec 013 FR-5 (contract 013 §4.3): the pushed-plane read.
            # The key's register comes from registers.PUSHED_TEMPS at
            # render time — the single source of truth (a permuted
            # calibration outcome is a one-line edit there). Unconfirmed
            # raws never reached the entity plane (from_pushes withheld
            # them) -> `unknown`; the shared decode helper handles the
            # sentinels + plausibility on this plane identically (no
            # second decoder, contract 013 §7.2).
            address = registers.PUSHED_TEMPS.get(self._regdef.key)
            if address is None:
                return None
            return decode_temp_logged(address, data.registers.get(address))
        if self._regdef.key == "firmware":
            # Special-case: regs 48..51 joined as "a.b.c.d". [V] semantics
            return data.firmware
        raw = data.registers.get(self._regdef.address)
        if self._regdef.device_class == "temperature":
            # FR-1/FR-2: both sentinel conventions + plausibility guard ->
            # state "unknown"; the shared helper warns once per address.
            return decode_temp_logged(self._regdef.address, raw)
        if raw is None or raw == registers.SENTINEL_RAW:
            return None  # register absent or sentinel -> state "unknown"
        if self._regdef.scale == 1.0:
            return raw
        return raw / self._regdef.scale


class AquaTempPushStreamEntity(AquaTempEntity, SensorEntity):
    """The push-stream health diagnostic (spec 013 FR-6, contract 013 §6.3).

    State: `on` while the T-block store entry is fresh (age within the
    freshness window), `off` when it went stale between cycles;
    unavailable rides the coordinator's UpdateFailed (a stale stream
    fails the cycle honestly — the entities go unavailable together).
    Attributes: the mode, the per-start push counts and the first/last
    push ages — U-3 (the unproven long-run stream behavior, research
    013 §7) becomes observable by the owner without a terminal.
    """

    def __init__(
        self,
        coordinator: AquaTempCoordinator,
        entry: ConfigEntry,
    ) -> None:
        AquaTempEntity.__init__(self, coordinator, entry, "push_stream")
        self._attr_name = SENSOR_NAMES["push_stream"]
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:bluetooth"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.mode != "push" or data.push_last_seen is None:
            return None
        age = self.coordinator.now() - data.push_last_seen
        return "on" if age <= self.coordinator.push_freshness_window() else "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        now = self.coordinator.now()
        attrs: dict[str, Any] = {
            "mode": data.mode,
            "per_start_counts": {
                str(start): count for start, count in sorted(data.push_counts.items())
            },
        }
        if data.push_first_seen is not None:
            attrs["first_push_age_s"] = round(now - data.push_first_seen, 1)
        if data.push_last_seen is not None:
            attrs["last_push_age_s"] = round(now - data.push_last_seen, 1)
        return attrs