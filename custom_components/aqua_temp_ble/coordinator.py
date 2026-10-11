"""DataUpdateCoordinator for Aqua Temp BLE.

Policy (the "don't saturate the PAC" strategy, BLUEPRINT §6):
- exactly one FC03 read of the 45 base registers per poll cycle — never
  per-register reads, no in-cycle retry storms;
- writes are serialized against reads via a single asyncio.Lock, and each
  write is confirmed by one debounced refresh afterwards;
- reconnection happens lazily on the next cycle after a failure (the unit
  accepts a single BLE connection; if the phone app holds it, HA waits).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import const, modbus, registers
from .aqua_client import AquaTempBleClient, AquaTempError

_LOGGER = logging.getLogger(__name__)

# name (registers.MODE_VALUE_TO_NAME) -> HVACMode. The int<->HVAC mapping is
# derived from registers.MODE_VALUE_TO_NAME so the calibration surface stays
# the single source of truth.
MODE_NAME_TO_HVAC: dict[str, HVACMode] = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
}

HVAC_TO_MODE_VALUE: dict[HVACMode, int] = {}
for _value, _name in registers.MODE_VALUE_TO_NAME.items():
    if (_hvac := MODE_NAME_TO_HVAC.get(_name)) is not None:
        HVAC_TO_MODE_VALUE[_hvac] = _value
del _value, _name, _hvac

# High-block queries available through the probe tool (start register).
HIGH_BLOCKS: dict[str, int] = {
    "1001": 1001,
    "1046": 1046,
    "1091": 1091,
    "1136": 1136,
    "2001": 2001,
    "2046": 2046,
}

# Log each unknown Mode value only once per HA session.
_logged_unknown_modes: set[int | None] = set()

# Log one implausible-raw warning per register address per HA session (FR-2,
# decode-contract.md §7). Sentinels are NOT logged — they are normal "sensor
# absent" states, not errors; they stay visible as raw values in the probe
# dumps, which is exactly where they are useful.
_logged_implausible: set[int] = set()


def decode_temp_logged(address: int, raw: int | None) -> float | None:
    """registers.decode_temp + the once-per-address implausible warning.

    Shared by the coordinator snapshot and the sensor platform (the two
    decode consumers) so that whichever decodes an implausible raw first
    logs exactly one WARNING per address per HA session (FR-2).
    """
    value = registers.decode_temp(raw)
    if (
        value is None
        and raw is not None
        and not registers.is_sentinel(raw)
        and address not in _logged_implausible
    ):
        _logged_implausible.add(address)
        _LOGGER.warning(
            "register %d reports implausible raw %d — treated as absent",
            address,
            raw,
        )
    return value


def _scaled(regs: dict[int, int], address: int) -> float | None:
    """Register raw -> scaled value; None when absent, sentinel or implausible."""
    return decode_temp_logged(address, regs.get(address))


@dataclass
class AquaTempData:
    """Decoded snapshot of the 45-register base block."""

    registers: dict[int, int]        # address -> raw signed int16
    power: bool | None               # reg 8 != 0
    boost: bool | None               # reg 11 != 0 (future use)
    mode_value: int | None           # reg 13
    hvac_mode: HVACMode | None       # derived via MODE_VALUE_TO_NAME
    target_temp: float | None        # reg 14 / 10
    min_target: float | None         # min(reg22, reg24) / 10
    max_target: float | None         # max(reg23, reg25) / 10
    inlet_temp: float | None         # reg 26 / 10, None if sentinel
    outlet_temp: float | None        # reg 27 / 10
    ambient_temp: float | None       # reg 28 / 10
    fault_code: int | None           # reg 47
    firmware: str | None             # regs 48..51 joined
    binaries: dict[str, bool | None]  # verified binary regs only

    @classmethod
    def from_regs(cls, regs: dict[int, int]) -> AquaTempData:
        """Decode a raw {register: signed int16} snapshot."""

        def flag(address: int) -> bool | None:
            raw = regs.get(address)
            return None if raw is None else raw != 0

        power = flag(registers.REG_POWER)
        boost = flag(registers.REG_BOOST)
        mode_value = regs.get(registers.REG_MODE)

        hvac_mode: HVACMode | None = None
        if power is False:
            hvac_mode = HVACMode.OFF
        elif power is True:
            name = (
                registers.MODE_VALUE_TO_NAME.get(mode_value)
                if mode_value is not None
                else None
            )
            hvac_mode = MODE_NAME_TO_HVAC.get(name) if name is not None else None
            if hvac_mode is None and mode_value not in _logged_unknown_modes:
                _logged_unknown_modes.add(mode_value)
                _LOGGER.warning(
                    "Unknown Mode register value %r (reg %d) — update "
                    "registers.MODE_VALUE_TO_NAME after calibration "
                    "(docs/CALIBRATION.md §4.4 step M)",
                    mode_value,
                    registers.REG_MODE,
                )

        lows = [
            v
            for v in (
                _scaled(regs, registers.REG_COOL_MIN_TARGET),
                _scaled(regs, registers.REG_HEAT_MIN_TARGET),
            )
            if v is not None
        ]
        highs = [
            v
            for v in (
                _scaled(regs, registers.REG_COOL_MAX_TARGET),
                _scaled(regs, registers.REG_HEAT_MAX_TARGET),
            )
            if v is not None
        ]

        fw_raw = [regs.get(a) for a in range(registers.REG_FIRMWARE_FIRST,
                                             registers.REG_FIRMWARE_LAST + 1)]
        firmware: str | None = None
        if all(r is not None and not registers.is_sentinel(r) for r in fw_raw):
            firmware = ".".join(str(r) for r in fw_raw)  # [V] semantics

        binaries: dict[str, bool | None] = {}
        for d in registers.BINARIES:
            if d.address is None:
                continue  # not calibrated -> entity not created, no data shown
            # Per-RegDef polarity, else the global default (FR-4).
            binaries[d.key] = registers.decode_flag(
                regs.get(d.address), registers.resolve_polarity(d)
            )

        return cls(
            registers=regs,
            power=power,
            boost=boost,
            mode_value=mode_value,
            hvac_mode=hvac_mode,
            target_temp=_scaled(regs, registers.REG_SET_CURRENT_TEMP),
            min_target=min(lows) if lows else None,
            max_target=max(highs) if highs else None,
            inlet_temp=_scaled(regs, registers.REG_INLET_WATER_TEMP),
            outlet_temp=_scaled(regs, registers.REG_OUTLET_WATER_TEMP),
            ambient_temp=_scaled(regs, registers.REG_AMBIENT_TEMP),
            fault_code=regs.get(registers.REG_FAULT1),
            firmware=firmware,
            binaries=binaries,
        )


class AquaTempCoordinator(DataUpdateCoordinator[AquaTempData]):
    """One FC03 read per cycle. Writes are serialized against reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Any,
        client: AquaTempBleClient,
    ) -> None:
        self.client = client
        self._io_lock = asyncio.Lock()
        super().__init__(
            hass,
            _LOGGER,
            name=const.DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get("poll_interval", const.DEFAULT_POLL_INTERVAL)
            ),
            config_entry=entry,
            always_update=False,
        )

    # ------------------------------------------------------------- options
    @property
    def temperature_unit(self) -> str:
        """Display unit from options. The wire is always x10 Celsius. [P]"""
        unit = self.config_entry.options.get(
            "temperature_unit", const.DEFAULT_TEMPERATURE_UNIT
        )
        if str(unit).upper() == "F":
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    # -------------------------------------------------------------- polling
    async def _async_update_data(self) -> AquaTempData:
        """Exactly one 45-register FC03 read per cycle; no retry storm."""
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()  # lazy reconnect, never a loop
            try:
                regs = await self.client.read_registers(
                    registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
                )
            except AquaTempError as err:
                await self.client.disconnect()  # clean slate for next cycle
                raise UpdateFailed(f"read failed: {err}") from err
            return AquaTempData.from_regs(regs)

    async def async_write_register(self, address: int, value: int) -> None:
        """Serialize a write against reads, then confirm with one refresh."""
        # FR-9: the single write funnel guard — before any lock or I/O, so no
        # code path can ever write outside {8, 13, 14} (+19-21 behind the
        # WRITE_MODE_SETPOINT_TOO switch). Installer settings (H/R/P) and the
        # high blocks 1001-2090 are never writable (spec 003 §6).
        registers.ensure_writable(address)
        async with self._io_lock:
            await self.client.write_registers(address, [value])
        await self.async_request_refresh()  # debounced confirmation read

    # ---------------------------------------------------------------- probe
    async def async_probe_block(self, block: str = "base") -> dict[str, Any]:
        """Read a register block and log every register (calibration, §4)."""
        block = str(block)
        if block != "base" and block not in HIGH_BLOCKS:
            raise ValueError(f"unknown block {block!r}")
        async with self._io_lock:
            if not self.client.is_connected:
                await self.client.connect()
            if block == "base":
                try:
                    regs = await self.client.read_registers(
                        registers.BASE_BLOCK_START, registers.BASE_BLOCK_COUNT
                    )
                except AquaTempError:
                    await self.client.disconnect()
                    raise
                return self._log_base_probe(regs)
            start = HIGH_BLOCKS[block]
            try:
                raw = await self.client.query_block(start, registers.BASE_BLOCK_COUNT)
            except AquaTempError:
                await self.client.disconnect()
                raise
            return self._log_raw_probe(block, start, raw)

    def _log_base_probe(self, regs: dict[int, int]) -> dict[str, Any]:
        """Log the base block in the exact probe format (§4.3)."""
        address = self.config_entry.data["address"]
        length = 2 * registers.BASE_BLOCK_COUNT + 5
        _LOGGER.info(
            "AquaTemp probe: %s block=base (FC03 start=%d count=%d) len=%d CRC=OK",
            address,
            registers.BASE_BLOCK_START,
            registers.BASE_BLOCK_COUNT,
            length,
        )
        registers_out: dict[str, Any] = {}
        for offset in range(registers.BASE_BLOCK_COUNT):
            addr = registers.BASE_BLOCK_START + offset
            raw = regs.get(addr, 0)
            line = (
                f"reg {addr:3d} = {raw:6d} (0x{raw & 0xFFFF:04X}) "
                f"| /10 = {raw / 10:7.1f}"
            )
            label = registers.KNOWN_LABELS.get(addr)
            if label:
                line += f"  # {label}"
            _LOGGER.info("  %s", line)
            entry: dict[str, Any] = {
                "raw": raw,
                "hex": f"0x{raw & 0xFFFF:04X}",
                "tenths": round(raw / 10, 1),
            }
            if label:
                entry["label"] = label
            registers_out[str(addr)] = entry
        return {address: {"block": "base", "crc": "OK", "registers": registers_out}}

    def _log_raw_probe(
        self, block: str, start: int, raw: bytes
    ) -> dict[str, Any]:
        """Best-effort log of an unknown-shape high-block response. [V]"""
        address = self.config_entry.data["address"]
        crc = "OK" if len(raw) >= 4 and modbus.validate_crc(raw) else "FAIL"
        _LOGGER.info(
            "AquaTemp probe: %s block=%s (query start=%d count=%d) len=%d CRC=%s",
            address,
            block,
            start,
            registers.BASE_BLOCK_COUNT,
            len(raw),
            crc,
        )
        response: dict[str, Any] = {"block": block, "crc": crc}
        regs: dict[int, int] | None = None
        if (
            len(raw) >= 5
            and raw[0] == modbus.SLAVE_ID
            and raw[1] == modbus.FC_READ_HOLDING
            and len(raw) >= raw[2] + 5
        ):
            try:
                regs = modbus.parse_read_response(
                    bytes(raw[: raw[2] + 5]),
                    start,
                    raw[2] // 2,
                    little_endian=registers.REG_DATA_LITTLE_ENDIAN,
                )
            except modbus.ModbusError as exc:
                _LOGGER.warning("could not parse %s response as FC03: %s", block, exc)
        if regs:
            out: dict[str, Any] = {}
            for addr, value in regs.items():
                line = (
                    f"reg {addr:3d} = {value:6d} (0x{value & 0xFFFF:04X}) "
                    f"| /10 = {value / 10:7.1f}"
                )
                label = registers.KNOWN_LABELS.get(addr)
                if label:
                    line += f"  # {label}"
                _LOGGER.info("  %s", line)
                item: dict[str, Any] = {
                    "raw": value,
                    "hex": f"0x{value & 0xFFFF:04X}",
                    "tenths": round(value / 10, 1),
                }
                if label:
                    item["label"] = label
                out[str(addr)] = item
            response["registers"] = out
        else:
            _LOGGER.info("  raw: %s", raw.hex())
            response["raw"] = raw.hex()
        return {address: response}