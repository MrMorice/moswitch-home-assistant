"""Admin-only, device-scoped installation of one pinned quiet test image.

No provider registration, filesystem writes, remote downloads or global OTA index.
The allowlist is the locally built 0.1.40 quiet candidate, not arbitrary firmware.
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import time

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from zigpy.ota import OtaImageWithMetadata
from zigpy.ota.image import parse_ota_image
from zigpy.ota.providers import BaseOtaImageMetadata
from zigpy.zcl import ClusterType, foundation
from zigpy.zcl.clusters.general import Ota

_LOGGER = logging.getLogger(__name__)
DATA = "moswitch_candidate_ota"
VERSION = 0x00010028
SOURCE_VERSION = 0x00010026
SHA256 = "ecade8483ec8810ced02dff0fb9518a094b3423da2502bf4b40abe91148f1a76"
SIZE = 354774


def decode_candidate(encoded):
    if not isinstance(encoded, str) or len(encoded) > 500000:
        raise ValueError("Testdatei zu groß")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("Ungültige Testdatei") from err
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("Nur der geprüfte stille Build 0.1.40 ist zugelassen")
    image, remaining = parse_ota_image(data)
    header = image.header
    if (remaining or header.manufacturer_id != 0xFFF1 or header.image_type != 1
            or header.file_version != VERSION or header.image_size != len(data)):
        raise ValueError("Unbekanntes Firmwareprofil")
    metadata = BaseOtaImageMetadata(
        file_version=VERSION, manufacturer_id=0xFFF1, image_type=1,
        checksum="sha256:" + SHA256, file_size=SIZE,
        manufacturer_names=("MoSwitch",), model_names=("MoSwitch v1",),
        source="MoSwitch isolated quiet test upload",
    )
    return OtaImageWithMetadata(metadata=metadata, firmware=image)


async def install(entry, image, job):
    runtime = entry.runtime_data
    runtime.touch_quiet.add(entry.entry_id)
    try:
        device = runtime.listen_switch(entry)  # Validates model and registry identity.
        if device.ota_in_progress:
            raise ValueError("Firmwareupdate läuft bereits")
        ota = device.find_cluster(cluster_id=Ota.cluster_id, cluster_type=ClusterType.Client)
        async with asyncio.timeout(30):
            values, _ = await ota.read_attributes(["current_file_version"], allow_cache=False)
        if values.get("current_file_version") != SOURCE_VERSION:
            raise ValueError("Dieser Test erfordert den bisherigen Gerätestand 0.1.38")
        # Demand a fresh physical report, not a cached test-mode checkbox.
        before = runtime.touch_received_at.get(entry.entry_id, 0)
        await runtime.test_touch(entry, True)
        async with asyncio.timeout(30):
            while True:
                status = runtime.touch_status.get(entry.entry_id, {})
                if (runtime.touch_received_at.get(entry.entry_id, 0) > before
                        and status.get("test_mode") and status.get("state") == 10):
                    break
                await asyncio.sleep(0.25)
        job.update(state="transferring", device_ieee=str(device.ieee))

        def progress(current, total, percent):
            job.update(bytes_sent=current, total_bytes=total, percent=round(percent, 1))

        result = await device.update_firmware(image=image, progress_callback=progress)
        if result != foundation.Status.SUCCESS:
            raise ValueError(f"Zigbee-Übertragung fehlgeschlagen: {result}")
        job.update(state="complete", percent=100)
    except asyncio.CancelledError:
        job.update(state="interrupted", error="HA hat den Transfer beendet")
        raise
    except Exception as err:  # OTA libraries can fail at several transport layers.
        job.update(state="failed", error=str(err))
        _LOGGER.warning("MoSwitch candidate OTA failed for %s: %s", entry.entry_id, err)
    finally:
        job["finished_at"] = time.time()
        # Never release the HA action guard after a test upload or a failed upload.
        runtime.touch_quiet.add(entry.entry_id)


def register(hass):
    hass.data.setdefault(DATA, {})
    websocket_api.async_register_command(hass, ws_install)
    websocket_api.async_register_command(hass, ws_status)


@websocket_api.websocket_command({vol.Required("type"): "moswitch/firmware/install_test",
    vol.Required("entry_id"): str, vol.Required("image"): vol.All(str, vol.Length(max=500000))})
@websocket_api.require_admin
@callback
def ws_install(hass, connection, msg):
    from .panel import panel_entry

    try:
        entry = panel_entry(hass, msg["entry_id"])
        jobs = hass.data[DATA]
        if jobs.get(entry.entry_id, {}).get("state") in ("preparing", "transferring"):
            raise ValueError("Firmwareupdate läuft bereits")
        image = decode_candidate(msg["image"])
        job = {"state": "preparing", "version": VERSION, "percent": 0, "started_at": time.time()}
        jobs[entry.entry_id] = job
        hass.async_create_background_task(install(entry, image, job), "MoSwitch candidate OTA")
    except ValueError as err:
        connection.send_error(msg["id"], "candidate_rejected", str(err))
        return
    connection.send_result(msg["id"], dict(job))


@websocket_api.websocket_command({vol.Required("type"): "moswitch/firmware/test_status",
                                 vol.Required("entry_id"): str})
@websocket_api.require_admin
@callback
def ws_status(hass, connection, msg):
    from .panel import panel_entry

    try:
        entry = panel_entry(hass, msg["entry_id"])
    except ValueError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], dict(hass.data[DATA].get(entry.entry_id, {"state": "idle"})))
