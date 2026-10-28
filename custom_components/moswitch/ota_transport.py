"""Device-scoped OTA sender; no global Zigpy changes or manufacturer spoofing."""

import asyncio
import time
from contextlib import asynccontextmanager

from zigpy import types as t
from zigpy.ota.manager import OTAManager
from zigpy.zcl import ClusterType, foundation
from zigpy.zcl.clusters.general import Ota

PROFILES = {
    "baseline": {"block": 48, "spacing": 20, "ack": True},
    "fast": {"block": 48, "spacing": 0, "ack": True},
    "fast_mac": {"block": 48, "spacing": 0, "ack": False},
    "large_mac": {"block": 64, "spacing": 0, "ack": False},
}


async def safe_block_limit(device):
    """Subtract OTA/ZCL and actual source-route overhead from the NCP limit."""
    try:
        radio = device.application._ezsp
        async with asyncio.timeout(5):
            (aps_limit,) = await radio.maximumPayloadLength()
        route = device.application.build_source_route_to(dest=device)
        # Unknown route: retain the existing conservative data size.
        if route is None:
            return 48
        route_bytes = 2 * (len(route) + 1) if route else 0
        return min(64, max(16, int(aps_limit) - 17 - route_bytes))
    except (AttributeError, TypeError, ValueError, TimeoutError):
        return 48


class ScopedOTAManager(OTAManager):
    def __init__(self, device, image, progress_callback, job, block_limit, ack):
        super().__init__(device, image, progress_callback=progress_callback)
        self.job = job
        self.block_limit = block_limit
        self.ack = ack
        self._page_lock = asyncio.Lock()
        self._handlers = set()
        self._closed = False
        self._send_count = self._send_ms = self._send_max_ms = 0
        job.update(server_pages=0, server_recovery_blocks=0, server_send_errors=0,
                   server_block_limit=block_limit, server_aps_ack=ack)

    def __exit__(self, *args):
        self._closed = True
        self._stall_timer.cancel()
        for task in tuple(self._handlers):
            task.cancel()
        return super().__exit__(*args)

    async def drain(self):
        if self._handlers:
            await asyncio.gather(*tuple(self._handlers), return_exceptions=True)

    @asynccontextmanager
    async def handler(self):
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            yield
        finally:
            self._handlers.discard(task)

    async def _image_page_req(self, hdr, command):
        if self._closed:
            return
        async with self.handler():
            await self._page(hdr, command)

    async def _image_block_req(self, hdr, command):
        if self._closed:
            return
        async with self.handler():
            await self._block(hdr, command)

    async def _send_block(self, offset, size, *, tsn=None):
        start = time.monotonic()
        try:
            async with self.device.application.request_priority(t.PacketPriority.LOW):
                await self.ota_cluster.request(
                    general=False, command_id=Ota.ClientCommandDefs.image_block_response.id,
                    schema=Ota.ClientCommandDefs.image_block_response.schema,
                    expect_reply=False, ask_for_ack=self.ack,
                    status=foundation.Status.SUCCESS,
                    manufacturer_code=self.image.firmware.header.manufacturer_id,
                    image_type=self.image.firmware.header.image_type,
                    file_version=self.image.firmware.header.file_version,
                    file_offset=offset, image_data=self._image_data[offset:offset + size],
                    **({"tsn": tsn} if tsn is not None else {}),
                )
        except Exception:
            self.job["server_send_errors"] += 1
            raise
        finally:
            ms = (time.monotonic() - start) * 1000
            self._send_count += 1
            self._send_ms += ms
            self._send_max_ms = max(self._send_max_ms, ms)
            self.job.update(server_packets=self._send_count,
                            mean_delivery_ms=round(self._send_ms / self._send_count, 2),
                            max_delivery_ms=round(self._send_max_ms, 2))
        self._stall_timer.reschedule(60 if offset + size >= len(self._image_data) else 30)
        if self.progress_callback is not None and not self._upgrade_end_future.done():
            self.progress_callback(offset + size, len(self._image_data))

    def _valid_request(self, cmd):
        header = self.image.firmware.header
        return (cmd.manufacturer_code == header.manufacturer_id
                and cmd.image_type == header.image_type
                and cmd.file_version == header.file_version
                and 0 <= cmd.file_offset < len(self._image_data)
                and 0 < cmd.maximum_data_size <= 255)

    async def _page(self, hdr, command):
        if not self._valid_request(command) or not 0 < command.page_size <= 4096:
            return
        # A retry/new page cannot interleave data with an older active page.
        async with self._page_lock:
            self.job["server_pages"] += 1
            self.job.update(client_spacing_ms=command.response_spacing,
                            client_maximum_data_size=command.maximum_data_size,
                            client_page_size=command.page_size)
            offset = command.file_offset
            end = min(offset + command.page_size, len(self._image_data))
            try:
                while offset < end and not self._upgrade_end_future.done():
                    size = min(command.maximum_data_size, self.block_limit, end - offset)
                    await self._send_block(offset, size)
                    offset += size
                    # Respect the client's requested minimum; never override it silently.
                    await asyncio.sleep(command.response_spacing / 1000)
            except Exception:
                # Client retries from its first missing byte, never install partial data.
                return

    async def _block(self, hdr, command):
        if not self._valid_request(command):
            return
        async with self._page_lock:
            self.job["server_recovery_blocks"] += 1
            size = min(command.maximum_data_size, self.block_limit,
                       len(self._image_data) - command.file_offset)
            try:
                await self._send_block(command.file_offset, size, tsn=hdr.tsn)
            except Exception:
                return


async def transfer_image(device, image, progress_callback, job, profile="baseline"):
    if device.ota_in_progress:
        raise ValueError("Firmwareupdate läuft bereits")
    settings = PROFILES[profile]
    limit = min(settings["block"], await safe_block_limit(device))
    device.ota_in_progress = True
    manager = None
    try:
        async with device.fast_poll_mode():
            with ScopedOTAManager(device, image, lambda n, total: progress_callback(
                    n, total, n * 100 / total), job, limit, settings["ack"]) as manager:
                await manager.notify()
                result = await manager.wait()
    finally:
        if manager is not None:
            await manager.drain()
        device.ota_in_progress = False
    if result == foundation.Status.SUCCESS:
        ota = device.find_cluster(cluster_id=Ota.cluster_id, cluster_type=ClusterType.Client)
        # Require the actual running target version, not just an UpgradeEnd ACK.
        for _ in range(20):
            await asyncio.sleep(2)
            try:
                async with asyncio.timeout(4):
                    values, _ = await ota.read_attributes(["current_file_version"], allow_cache=False)
                if values.get("current_file_version") == image.version:
                    job["confirmed_version"] = image.version
                    return result
            except Exception as err:
                job["version_read_error"] = type(err).__name__
        raise ValueError("Übertragung beendet, neue Geräteversion nicht bestätigt")
    return result
