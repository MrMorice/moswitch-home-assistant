"""Admin-only, device-scoped installation of pinned quiet test images.

No provider registration, filesystem writes, remote downloads or global OTA index.
The allowlist contains only the locally built 0.1.53–0.1.58 test candidates;57+ verify saved profiles at boot.
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

from .ota_probe import control
from .ota_transport import PROFILES, transfer_image

_LOGGER = logging.getLogger(__name__)
DATA = "moswitch_candidate_ota"
VERSION = 0x0001003A
SOURCE_VERSION = 0x00010039
SHA256 = "059d0ace1234e57deeab3eaa0fc5bb324f19254c9b5e601a72a484afb58c2dbb"
SIZE = 367970
CANDIDATES = {
    VERSION: {"source": SOURCE_VERSION, "sha256": SHA256, "size": SIZE},
    0x00010039: {"source": 0x00010038, "sha256": "9ad75052a68680525be2a56d4afcacd104e91ffd7c7b48396f3bf6bdb83921e5", "size": 367970},
    0x00010038: {"source": 0x00010037, "sha256": "6d026566c52b55b4ef2aa475bf30421198d268ccf94ae682f822cba72695fdb9", "size": 367234},
    0x00010037: {"source": 0x00010036, "sha256": "f0df586721a521fee35ce361146f1dd3ec214992b39811f277e703ff899922a3", "size": 367202},
    0x00010035: {"source": 0x00010034, "sha256": "0fa9cb9eef6fe90b2d2b23768a5b5b5d46a9888904fb6c07e2d2de9d31667bee", "size": 367202},
    0x00010036: {"source": 0x00010035, "sha256": "16c1c87e79de01970261c64e668bd7aabfd8e4d24476d5eca85e7cbcf6de0913", "size": 367202},
}


def decode_candidate(encoded):
    if not isinstance(encoded, str) or len(encoded) > 500000:
        raise ValueError("Testdatei zu groß")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("Ungültige Testdatei") from err
    digest = hashlib.sha256(data).hexdigest()
    selected = next(((version, spec) for version, spec in CANDIDATES.items()
                     if len(data) == spec["size"] and digest == spec["sha256"]), None)
    if selected is None:
        raise ValueError("Nur die festgelegten Testbuilds 0.1.53 bis 0.1.58 sind zugelassen")
    target, spec = selected
    image, remaining = parse_ota_image(data)
    header = image.header
    if (remaining or header.manufacturer_id != 0xFFF1 or header.image_type != 1
            or header.file_version != target or header.image_size != len(data)):
        raise ValueError("Unbekanntes Firmwareprofil")
    metadata = BaseOtaImageMetadata(
        file_version=target, manufacturer_id=0xFFF1, image_type=1,
        checksum="sha256:" + spec["sha256"], file_size=spec["size"],
        manufacturer_names=("MoSwitch",), model_names=("MoSwitch v1",),
        source="MoSwitch isolated quiet test upload",
    )
    return OtaImageWithMetadata(metadata=metadata, firmware=image)


async def install(entry, image, job, profile="large_mac"):
    runtime = entry.runtime_data
    runtime.touch_quiet.add(entry.entry_id)
    try:
        device = runtime.listen_switch(entry)  # Validates model and registry identity.
        if device.ota_in_progress:
            raise ValueError("Firmwareupdate läuft bereits")
        ota = device.find_cluster(cluster_id=Ota.cluster_id, cluster_type=ClusterType.Client)
        async with asyncio.timeout(30):
            values, _ = await ota.read_attributes(["current_file_version"], allow_cache=False)
        source = CANDIDATES[image.version]["source"]
        if values.get("current_file_version") != source:
            raise ValueError(f"Dieser Test erfordert Gerätestand {source:#010x}")
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
        runtime.ota_active_entries.add(entry.entry_id)
        if source >= 0x00010034:
            settings = PROFILES[profile]
            await control(entry, 1, block=settings["block"], spacing=settings["spacing"])
        job.update(state="transferring", device_ieee=str(device.ieee), profile=profile)

        def progress(current, total, percent):
            job.update(bytes_sent=current, total_bytes=total, percent=round(percent, 1))

        result = await transfer_image(device, image, progress, job, profile)
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
        # Keep blocked until a fresh device report confirms its post-update mode.
        runtime.touch_quiet.add(entry.entry_id)
        runtime.ota_active_entries.discard(entry.entry_id)
        if job.get("state") == "complete" and image.version >= 0x00010039:
            before = runtime.touch_received_at.get(entry.entry_id, 0)
            try:
                await runtime.query_touch(entry)
                async with asyncio.timeout(15):
                    while runtime.touch_received_at.get(entry.entry_id, 0) <= before:
                        await asyncio.sleep(0.1)
                job["startup_status_confirmed"] = True
            except Exception as err:
                job["startup_status_confirmed"] = False
                job["startup_query_error"] = type(err).__name__


def register(hass):
    hass.data.setdefault(DATA, {})
    from .ota_probe import register as register_probe
    register_probe(hass)
    websocket_api.async_register_command(hass, ws_install)
    websocket_api.async_register_command(hass, ws_status)


@websocket_api.websocket_command({vol.Required("type"): "moswitch/firmware/install_test",
    vol.Required("entry_id"): str, vol.Required("image"): vol.All(str, vol.Length(max=500000)),
    vol.Optional("profile", default="large_mac"): vol.In(PROFILES)})
@websocket_api.require_admin
@callback
def ws_install(hass, connection, msg):
    from .panel import panel_entry

    try:
        entry = panel_entry(hass, msg["entry_id"])
        jobs = hass.data[DATA]
        if jobs.get(entry.entry_id, {}).get("state") in ("preparing", "transferring"):
            raise ValueError("Firmwareupdate läuft bereits")
        if entry.entry_id in entry.runtime_data.ota_active_entries:
            raise ValueError("OTA-Test läuft bereits")
        image = decode_candidate(msg["image"])
        job = {"state": "preparing", "version": image.version, "percent": 0, "started_at": time.time()}
        jobs[entry.entry_id] = job
        entry.runtime_data.ota_active_entries.add(entry.entry_id)
        hass.async_create_background_task(install(entry, image, job, msg.get("profile", "large_mac")), "MoSwitch candidate OTA")
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
