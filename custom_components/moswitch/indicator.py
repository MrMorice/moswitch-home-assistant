"""Persistent status-light settings, confirmed by the device."""

import asyncio
import secrets
import struct
import time

from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import Entity
from zigpy import types as t

from .const import DOMAIN, ENDPOINT, MANUFACTURER

OPS = {"disable": 0, "enable": 1, "confirm_supply": 3, "brightness": 5, "status": 6}


class RingCommand(t.Struct):
    operation: t.uint8_t
    value: t.uint8_t
    nonce: t.uint16_t


def parse_ring(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) != 16:
        return None
    version, flags, brightness, error, _baud, _frames, nonce, operation, result = struct.unpack("<BBBBIIHBB", payload)
    if version != 1 or flags > 15 or brightness > 64 or operation not in OPS.values() or result > 1:
        return None
    return {"supply_confirmed": bool(flags & 1), "enabled": bool(flags & 2),
            "started": bool(flags & 4), "brightness": brightness, "error": error,
            "nonce": nonce, "operation": operation, "result": result}


def ring_reply(runtime, ieee, payload):
    status = parse_ring(payload)
    if status is None:
        return
    for entry in runtime.entries.values():
        if entry.unique_id == ieee:
            runtime.indicator_status[entry.entry_id] = status
            runtime.indicator_received_at[entry.entry_id] = time.monotonic()
            async_dispatcher_send(runtime.hass, f"{DOMAIN}_{entry.entry_id}")
    waiter = runtime.ring_waiters.get(ieee)
    if waiter and waiter[:2] == (status["nonce"], status["operation"]) and not waiter[2].done():
        waiter[2].set_result(status)


async def indicator_action(entry, action, value=0):
    if action not in OPS or type(value) is not int or not 0 <= value <= 64:
        raise ValueError("Ungültige Statuslicht-Einstellung")
    runtime = entry.runtime_data
    device = runtime.listen_switch(entry)
    if getattr(device, "ota_in_progress", False):
        raise HomeAssistantError("Bitte das Firmwareupdate abwarten")
    ieee = str(device.ieee)
    lock = runtime.ring_locks.setdefault(ieee, asyncio.Lock())

    async def command(operation, argument=0):
        nonce = secrets.randbelow(65535) + 1
        future = asyncio.get_running_loop().create_future()
        runtime.ring_waiters[ieee] = (nonce, operation, future)
        try:
            await device.endpoints[ENDPOINT].in_clusters[0].request(
                False, 0x4D, RingCommand, operation, argument, nonce,
                manufacturer=MANUFACTURER, expect_reply=False, ask_for_ack=True,
                disable_default_response=True,
            )
            status = await asyncio.wait_for(future, 10)
            if status["result"] or status["error"]:
                raise HomeAssistantError("Statuslicht-Einstellung vom Gerät abgelehnt")
            return status
        finally:
            runtime.ring_waiters.pop(ieee, None)
            if not future.done():
                future.cancel()

    async with lock:
        # Enabling declares a powered LED rail; it does not measure its voltage.
        if action == "enable":
            await command(OPS["confirm_supply"], 0xA5)
        status = await command(OPS[action], value)
        if action == "brightness" and status["brightness"] != value:
            raise HomeAssistantError("Helligkeit wurde nicht übernommen")
        if action == "enable" and not (status["enabled"] and status["started"]):
            raise HomeAssistantError("Statuslicht wurde nicht eingeschaltet")
        if action == "disable" and status["enabled"]:
            raise HomeAssistantError("Statuslicht wurde nicht ausgeschaltet")
        return status


class IndicatorEntity(Entity):
    """Shared confirmed state for the two status-light preferences."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(self, entry):
        self.entry = entry

    @property
    def status(self):
        return self.entry.runtime_data.indicator_status.get(self.entry.entry_id, {})

    @property
    def available(self):
        received = self.entry.runtime_data.indicator_received_at.get(self.entry.entry_id)
        return received is not None and time.monotonic() - received < 180 and not self.status.get("error")

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state,
        ))
