"""Minimal org.bluez.Agent1 — the pairing ladder's rung C (spec 011 FR-3).

The headless bluetoothd on a Home Assistant OS box registers NO pairing
agent, and BlueZ's Device1.Pair() then fails by documented design
(research 011 §3.3, official D-Bus docs: "In case there is no application
agent and also no default agent present, this method will fail"). This
module registers the smallest possible agent — capability
``NoInputNoOutput``, every request handler auto-approving — so a Just
Works pairing can complete with no desktop and no terminal.

dbus-fast is bleak's own D-Bus dependency on Linux (spec 011 AC-6): no
new requirement is ever added, and every import here is guarded —
hosts without the library (non-Linux backends, stripped test
environments) simply report rung C unavailable (AC-8).

Safety (contract 011 §7): the agent auto-approves NoInputNoOutput Just
Works flows only. It never offers a real PIN, never performs numeric
comparison — a peripheral demanding MITM-protected pairing fails, which
is the correct outcome and out of scope. The agent is always
unregistered from the caller's finally (best-effort, DEBUG on failure).
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

#: D-Bus object path the agent is exported at (namespaced to the
#: integration; any valid, non-conflicting path works).
AGENT_PATH = "/org/aqua_temp_ble/pairing_agent"

#: The auto-approve capability — the Just Works class (contract 011 §3,
#: research 011 §10.2: an external NoInputNoOutput agent is the
#: documented fix for the agent-less bluetoothd Pair() failure).
CAPABILITY = "NoInputNoOutput"

try:  # guarded on purpose: rung C is optional, never a requirement
    from dbus_fast import BusType, Message  # noqa: F401
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method

    DBUS_FAST_AVAILABLE = True
except ImportError:  # pragma: no cover — hosts without dbus-fast
    DBUS_FAST_AVAILABLE = False
    MessageBus = None  # type: ignore[assignment]


if DBUS_FAST_AVAILABLE:

    class MinimalAgent(ServiceInterface):
        """org.bluez.Agent1 with every request auto-approved (contract
        011 §3.3 — the handlers log and pass through)."""

        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self) -> None:
            _LOGGER.debug("BlueZ agent: Release")

        @method()
        async def RequestPinCode(self, device: "o") -> "s":
            _LOGGER.info(
                "BlueZ agent: RequestPinCode for %s — auto-approve", device
            )
            return "000000"

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s") -> None:
            _LOGGER.info(
                "BlueZ agent: DisplayPinCode %s pin=%s", device, pincode
            )

        @method()
        async def RequestPasskey(self, device: "o") -> "u":
            _LOGGER.info(
                "BlueZ agent: RequestPasskey for %s — auto-approve", device
            )
            return 0

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:
            _LOGGER.info(
                "BlueZ agent: DisplayPasskey %s passkey=%d entered=%d",
                device,
                passkey,
                entered,
            )

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u") -> None:
            _LOGGER.info(
                "BlueZ agent: RequestConfirmation %s passkey=%d — auto-approve",
                device,
                passkey,
            )

        @method()
        def RequestAuthorization(self, device: "o") -> None:
            _LOGGER.info(
                "BlueZ agent: RequestAuthorization %s — auto-approve", device
            )

        @method()
        def AuthorizeService(self, device: "o", uuid: "s") -> None:
            _LOGGER.info(
                "BlueZ agent: AuthorizeService %s uuid=%s — auto-approve",
                device,
                uuid,
            )


def _agent_manager_message(member: str, signature: str, body: list) -> "Message":
    """One org.bluez.AgentManager1 call as a dbus_fast Message."""
    return Message(
        destination="org.bluez",
        path="/org/bluez",
        interface="org.bluez.AgentManager1",
        member=member,
        signature=signature,
        body=body,
    )


async def register_agent() -> object | None:
    """Connect to the system bus, export the agent, register it and
    request it as the default agent (the headless bluetoothd has none —
    research 011 §10.2).

    Returns the connected bus (the token ``unregister_agent`` expects),
    or None when dbus_fast is unavailable or the registration fails —
    callers log that as ``pairing: unavailable: <reason>``, never raise.
    """
    if not DBUS_FAST_AVAILABLE:
        _LOGGER.debug(
            "dbus_fast not installed — rung C unavailable (spec 011 FR-3)"
        )
        return None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        bus.export(AGENT_PATH, MinimalAgent())
        await bus.call(
            _agent_manager_message("RegisterAgent", "os", [AGENT_PATH, CAPABILITY])
        )
        await bus.call(
            _agent_manager_message("RequestDefaultAgent", "o", [AGENT_PATH])
        )
        _LOGGER.info(
            "BlueZ agent registered at %s (capability %s) and requested "
            "as the default agent",
            AGENT_PATH,
            CAPABILITY,
        )
        return bus
    except Exception as exc:  # noqa: BLE001 — rung C is best-effort, never fatal
        _LOGGER.debug(
            "BlueZ agent registration failed: %s: %s", type(exc).__name__, exc
        )
        return None


async def unregister_agent(bus: object | None) -> None:
    """Best-effort teardown — always called from the caller's finally
    (contract 011 §7: the ladder never leaves an agent behind)."""
    if bus is None:
        return
    try:
        await bus.call(
            _agent_manager_message("UnregisterAgent", "o", [AGENT_PATH])
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug(
            "BlueZ agent unregister failed: %s: %s", type(exc).__name__, exc
        )
    try:
        await bus.disconnect()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug(
            "BlueZ agent bus disconnect failed: %s: %s", type(exc).__name__, exc
        )