"""Read bounded diagnostic trace chunks from one quiet, stopped MoSwitch."""

import asyncio
import struct
import time

import probatio as vol
from homeassistant.components import websocket_api
from zigpy import types as t

from .const import ENDPOINT, MANUFACTURER
from .touch_wizard import COMMAND_ERRORS


class TraceOffset(t.Struct):
    offset: t.uint16_t


def parse_trace(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 12:
        return None
    version, count, offset, total, generation, samples = struct.unpack("<BBHHHI", payload[:12])
    if (version != 1 or count > 4 or total > 4096 or offset > total
            or count != min(4, total - offset) or len(payload) != 12 + count * 16):
        return None
    records = []
    for start in range(12, len(payload), 16):
        at, end, repeats, raw, state, flags = struct.unpack("<IIIHBB", payload[start:start + 16])
        if not repeats or state > 13:
            return None
        records.append({"at_ms": at, "end_ms": end, "repeats": repeats,
                        "raw_us": None if raw == 65535 else raw, "state": state, "flags": flags})
    return {"offset": offset, "total_records": total, "generation": generation,
            "total_samples": samples, "records": records}


def trace_reply(runtime, ieee, payload):
    packet = parse_trace(payload)
    waiter = runtime.touch_trace_waiters.get(ieee)
    if packet is not None and waiter and waiter[0] == packet["offset"] and not waiter[1].done():
        waiter[1].set_result(packet)


async def read_chunk(entry, offset):
    runtime = entry.runtime_data
    device = runtime.listen_switch(entry)
    ieee = str(device.ieee)
    lock = runtime.touch_trace_locks.setdefault(ieee, asyncio.Lock())
    async with lock:
        status = runtime.touch_status.get(entry.entry_id, {})
        if (not status.get("test_mode") or status.get("state") in (1, 2, 7, 9, 11, 12, 13)
                or time.monotonic() - runtime.touch_received_at.get(entry.entry_id, 0) >= 120):
            raise ValueError("Aufzeichnung erst nach beendetem Lernlauf im stillen Modus auslesen")
        future = asyncio.get_running_loop().create_future()
        runtime.touch_trace_waiters[ieee] = (offset, future)
        try:
            # Data arrives as an independently validated0x82 event. A ZCL reply
            # waiter can steal that binary response and misread it as an ACK.
            await device.endpoints[ENDPOINT].in_clusters[0].request(
                False,0x4C,TraceOffset,offset,manufacturer=MANUFACTURER,
                expect_reply=False,ask_for_ack=True,disable_default_response=True,
            )
            return await asyncio.wait_for(future, 15)
        finally:
            runtime.touch_trace_waiters.pop(ieee, None)
            if not future.done():
                future.cancel()


@websocket_api.websocket_command({vol.Required("type"): "moswitch/touch/trace",
    vol.Required("entry_id"): str, vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0, max=4096))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_trace(hass, connection, msg):
    from .panel import panel_entry

    try:
        entry = panel_entry(hass, msg["entry_id"])
        packet = await read_chunk(entry, msg["offset"])
    except COMMAND_ERRORS as err:
        connection.send_error(msg["id"], "trace_failed", str(err))
        return
    connection.send_result(msg["id"], packet)
