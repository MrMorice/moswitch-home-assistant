"""Bounded admin-only RAM transport comparison; never a flash or install path."""

import asyncio
import secrets
import struct
import time

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from zigpy import types as t

from .const import ENDPOINT, MANUFACTURER
from .ota_transport import PROFILES, safe_block_limit

DATA = "moswitch_ota_probe"


class Control(t.Struct):
    operation: t.uint8_t
    nonce: t.uint16_t
    block: t.uint8_t
    spacing: t.uint8_t
    reserved: t.uint8_t
    total: t.uint32_t
    expected: t.uint32_t


class ProbeData(t.Struct):
    session: t.uint16_t
    offset: t.uint32_t
    payload: t.SerializableBytes


def probe_reply(runtime, ieee, payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) != 40:
        return
    version, op, nonce, result, block, spacing, active = struct.unpack("<BBHBBBB", payload[:8])
    if version != 1 or op > 3 or result > 1 or active > 1 or not 16 <= block <= 64 or spacing > 50:
        return
    waiter = runtime.ota_probe_waiters.get(ieee)
    if waiter and waiter[:2] == (op, nonce) and not waiter[2].done():
        values = struct.unpack("<8I", payload[8:])
        waiter[2].set_result(dict(zip(("received", "hash", "ignored", "device_elapsed_ms",
            "ota_pages", "ota_recovery_blocks", "ota_ignored", "ota_received"), values),
            result=result, block=block, spacing=spacing, active=bool(active)))


async def control(entry, op, nonce=None, block=48, spacing=0, total=0, expected=0):
    runtime = entry.runtime_data
    device = runtime.listen_switch(entry)
    nonce = nonce or secrets.randbelow(65535) + 1
    ieee = str(device.ieee)
    future = asyncio.get_running_loop().create_future()
    if ieee in runtime.ota_probe_waiters:
        raise ValueError("OTA-Diagnoseanfrage läuft bereits")
    runtime.ota_probe_waiters[ieee] = (op, nonce, future)
    try:
        await device.endpoints[ENDPOINT].in_clusters[0].request(
            False, 0x4E, Control, op, nonce, block, spacing, 0, total, expected,
            manufacturer=MANUFACTURER, expect_reply=False, ask_for_ack=True,
            disable_default_response=True)
        response = await asyncio.wait_for(future, 10)
        if response["result"] and op != 3:
            raise ValueError("OTA-Diagnose vom Gerät abgelehnt")
        if op == 1 and (response["block"] != block or response["spacing"] != spacing):
            raise ValueError("OTA-Profil nicht vom Gerät bestätigt")
        return response
    finally:
        runtime.ota_probe_waiters.pop(ieee, None)
        if not future.done():
            future.cancel()


def fnv(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


async def benchmark(entry, job, profile, size):
    runtime = entry.runtime_data
    runtime.ota_active_entries.add(entry.entry_id)
    nonce = secrets.randbelow(65535) + 1
    started = False
    try:
        device = runtime.listen_switch(entry)
        if device.ota_in_progress:
            raise ValueError("Firmwareupdate läuft bereits")
        status = runtime.touch_status.get(entry.entry_id, {})
        if not status.get("test_mode") or status.get("state") != 10:
            raise ValueError("RAM-Test erfordert stillen Diagnosemodus")
        settings = PROFILES[profile]
        block = min(settings["block"], await safe_block_limit(device))
        payload = bytes((i * 73 + 19) & 255 for i in range(size))
        await control(entry, 2, nonce=nonce, total=size)
        started = True
        job.update(state="transferring", block=block, aps_ack=settings["ack"], spacing_ms=settings["spacing"])
        start = time.monotonic()
        delivery = []
        for offset in range(0, size, block):
            tick = time.monotonic()
            await device.endpoints[ENDPOINT].in_clusters[0].request(
                False, 0x4F, ProbeData, nonce, offset, payload[offset:offset + block],
                manufacturer=MANUFACTURER, expect_reply=False,
                ask_for_ack=settings["ack"], disable_default_response=True,
                retries=0)
            delivery.append((time.monotonic() - tick) * 1000)
            job["bytes_sent"] = min(size, offset + block)
            await asyncio.sleep(settings["spacing"] / 1000)
        # Last MAC-only send may finish before the app callback has processed it.
        await asyncio.sleep(.2)
        result = await control(entry, 3, nonce=nonce, expected=fnv(payload))
        started = False
        elapsed = time.monotonic() - start
        verified = not result["result"] and result["received"] == size and result["hash"] == fnv(payload)
        job.update(result, verified=verified, state="complete" if verified else "failed",
                   elapsed_s=round(elapsed, 3), received_Bps=round(result["received"] / elapsed, 1),
                   mean_delivery_ms=round(sum(delivery) / len(delivery), 2), max_delivery_ms=round(max(delivery), 2))
    except asyncio.CancelledError:
        job.update(state="interrupted")
        raise
    except Exception as err:
        job.update(state="failed", error=str(err))
    finally:
        if started:
            try:
                await control(entry, 3, nonce=nonce, expected=0)
            except Exception:
                pass  # Device also expires its RAM-only session after120s.
        runtime.ota_active_entries.discard(entry.entry_id)
        job["finished_at"] = time.time()


def register(hass):
    hass.data.setdefault(DATA, {})
    websocket_api.async_register_command(hass, ws_probe)
    websocket_api.async_register_command(hass, ws_status)


@websocket_api.websocket_command({vol.Required("type"): "moswitch/firmware/benchmark",
    vol.Required("entry_id"): str, vol.Required("profile"): vol.In(PROFILES),
    vol.Optional("size", default=8192): vol.All(int, vol.Range(min=1024, max=32768))})
@websocket_api.require_admin
@callback
def ws_probe(hass, connection, msg):
    from .candidate_ota import DATA as OTA_DATA
    from .panel import panel_entry
    try:
        entry = panel_entry(hass, msg["entry_id"])
        if (entry.entry_id in entry.runtime_data.ota_active_entries
                or hass.data.get(OTA_DATA, {}).get(entry.entry_id, {}).get("state") in ("preparing", "transferring")):
            raise ValueError("OTA-Test oder Installation läuft bereits")
        jobs = hass.data[DATA]
        previous = jobs.get(entry.entry_id, {})
        if previous.get("state") in ("preparing", "transferring"):
            raise ValueError("RAM-Test läuft bereits")
        job = {"state": "preparing", "profile": msg["profile"], "size": msg["size"], "started_at": time.time()}
        jobs[entry.entry_id] = job
        # Reserve before scheduling; two clients cannot race the radio.
        entry.runtime_data.ota_active_entries.add(entry.entry_id)
        hass.async_create_background_task(benchmark(entry, job, msg["profile"], msg["size"]), "MoSwitch RAM OTA probe")
        connection.send_result(msg["id"], dict(job))
    except ValueError as err:
        connection.send_error(msg["id"], "probe_rejected", str(err))


@websocket_api.websocket_command({vol.Required("type"): "moswitch/firmware/benchmark_status", vol.Required("entry_id"): str})
@websocket_api.require_admin
@callback
def ws_status(hass, connection, msg):
    from .panel import panel_entry
    try:
        entry = panel_entry(hass, msg["entry_id"])
        connection.send_result(msg["id"], dict(hass.data[DATA].get(entry.entry_id, {"state": "idle"})))
    except ValueError as err:
        connection.send_error(msg["id"], "not_found", str(err))
