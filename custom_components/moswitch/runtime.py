"""Room commissioning and HA-only actions; never execute Single in HA."""

import asyncio
import logging
import struct
import time
from datetime import timedelta

from homeassistant.components.zha.helpers import get_zha_gateway
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import (
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.storage import Store
from zigpy import types as t
from zigpy.exceptions import ZigbeeException
from zigpy.ota.providers import RemoteZigpyProvider
from zigpy.zcl import foundation
from zigpy.zdo import types as zdo_t

from . import protocol
from .const import DOMAIN, ENDPOINT, IGNORE_LABEL, MAX_TARGETS
from .rooms import device_area, ieee_for_device, room_entities, scene_cycle

_LOGGER = logging.getLogger(__name__)


class GestureListener:
    def __init__(self, runtime, ieee):
        self.runtime = runtime
        self.ieee = ieee
        self.last = None

    def cluster_command(self, tsn, command_id, args):
        if command_id != 0x80 or not isinstance(args, (bytes, bytearray)) or len(args) != 5:
            return
        sequence, kind = struct.unpack("<IB", args)
        now = time.monotonic()
        key = (sequence, kind)
        if self.last and self.last[0] == key and now - self.last[1] < 10:
            return
        self.last = (key, now)
        if kind not in (1, 2):
            return
        self.runtime.hass.async_create_task(
            self.runtime.gesture(self.ieee, kind),
            "MoSwitch gesture",
        )


class Runtime:
    def __init__(self, hass):
        self.hass = hass
        self.entries = {}
        self.health = {}
        self.rooms = {}
        self.store = Store(hass, 1, DOMAIN)
        self.lock = asyncio.Lock()
        self.scene_locks = {}
        self.listeners = {}
        self.signatures = {}
        self.unsubs = []
        self.pending = None
        self.ignore_label = ""
        self.ota_controller = None
        self.active = True

    async def start(self):
        saved = await self.store.async_load() or {}
        self.rooms = saved.get("rooms", {})
        labels = lr.async_get(self.hass)
        label = labels.async_get_label_by_name(IGNORE_LABEL)
        if label is None:
            label = labels.async_create(IGNORE_LABEL)
        self.ignore_label = label.label_id
        for event in (
            "device_registry_updated",
            "entity_registry_updated",
            "area_registry_updated",
            "label_registry_updated",
        ):
            self.unsubs.append(self.hass.bus.async_listen(event, self.schedule_sync))
        self.unsubs.append(
            async_track_time_interval(
                self.hass,
                self.async_sync,
                timedelta(seconds=60),
            )
        )

    def schedule_sync(self, _event=None):
        if self.pending:
            self.pending()
        self.pending = async_call_later(self.hass, 2, self.async_sync)

    async def async_sync(self, _now=None):
        self.pending = None
        if not self.active:
            return
        async with self.lock:
            for entry in list(self.entries.values()):
                try:
                    await self.commission(entry)
                except (
                    HomeAssistantError,
                    ZigbeeException,
                    TimeoutError,
                    ValueError,
                    KeyError,
                    RuntimeError,
                ) as err:
                    self.set_health(entry, f"Nicht bereit: {err}")
                    _LOGGER.warning("MoSwitch %s commissioning failed: %s", entry.title, err)
            await self.store.async_save({"rooms": self.rooms})

    def set_health(self, entry, text):
        self.health[entry.entry_id] = text
        async_dispatcher_send(self.hass, f"{DOMAIN}_{entry.entry_id}")

    def get_switch(self, entry):
        ieee = ieee_for_device(self.hass, entry.data["device_id"])
        gateway = get_zha_gateway(self.hass)
        device = gateway.application_controller.get_device(ieee=t.EUI64.convert(ieee))
        if device.manufacturer != "MoSwitch" or device.model != "MoSwitch v1":
            raise ValueError("Unbekanntes MoSwitch-Firmwareprofil")
        return device

    def targets(self, area, gateway):
        targets = set()
        entries = room_entities(self.hass, area, "light")
        for entry in entries:
            if entry.platform in ("group", "template"):
                # Virtual aggregations are not an additional physical lamp.
                continue
            if entry.platform != "zha" or not entry.device_id:
                raise ValueError(f"Keine direkte ZHA-Lampe: {entry.entity_id}")
            ieee = ieee_for_device(self.hass, entry.device_id)
            zha_device = gateway.get_device(t.EUI64.convert(ieee))
            if zha_device is None:
                raise ValueError(f"ZHA-Gerät fehlt: {entry.entity_id}")
            entity = next(
                (
                    entity
                    for entity in zha_device.platform_entities.values()
                    if entity.unique_id == entry.unique_id
                ),
                None,
            )
            endpoint_id = entity.identifiers.endpoint_id if entity else None
            if endpoint_id is None:
                raise ValueError(f"Licht-Endpunkt unbekannt: {entry.entity_id}")
            device = gateway.application_controller.get_device(ieee=t.EUI64.convert(ieee))
            endpoint = device.endpoints[endpoint_id]
            if 6 not in endpoint.in_clusters:
                raise ValueError(f"On/Off fehlt: {entry.entity_id}")
            targets.add(protocol.Target(ieee, endpoint_id, int(device.nwk)))
        if not 1 <= len(targets) <= MAX_TARGETS:
            raise ValueError(f"Raum benötigt 1–{MAX_TARGETS} physische Lampen")
        return sorted(targets)

    async def commission(self, entry):
        device = self.get_switch(entry)
        ieee = str(device.ieee)
        cluster = device.endpoints[ENDPOINT].in_clusters[0]
        attached = self.listeners.get(entry.entry_id)
        if attached is None or attached[0] is not cluster:
            if attached:
                attached[0].remove_listener(attached[1])
            listener = GestureListener(self, ieee)
            cluster.add_listener(listener)
            self.listeners[entry.entry_id] = (cluster, listener)
        area = device_area(self.hass, entry.data["device_id"])
        if not area:
            await protocol.clear(device)
            self.signatures.pop(entry.entry_id, None)
            self.set_health(entry, "Raum zuweisen")
            return
        gateway = get_zha_gateway(self.hass)
        try:
            targets = self.targets(area, gateway)
        except ValueError:
            if self.signatures.get(entry.entry_id, (None,))[0] != area:
                await protocol.clear(device)
            raise
        if area not in self.rooms:
            used = {room["group"] for room in self.rooms.values()}
            used.update(gateway.application_controller.groups)
            group_id = next(i for i in range(0x1000, 0xFFF8) if i not in used)
            self.rooms[area] = {
                "group": group_id,
                "scene_order": [],
                "last_scene": None,
            }
            # Persist allocation before changing physical devices.
            await self.store.async_save({"rooms": self.rooms})
        # Limit images by manufacturer/model metadata, not by the Silicon Labs
        # node-descriptor manufacturer code used by the development stack.
        if (
            hasattr(gateway.application_controller, "ota")
            and gateway.application_controller.ota is not self.ota_controller
        ):
            gateway.application_controller.ota.register_provider(
                RemoteZigpyProvider(
                    url="https://raw.githubusercontent.com/MrMorice/moswitch-home-assistant/main/ota/index.json",
                    manufacturer_ids=[],
                )
            )
            self.ota_controller = gateway.application_controller.ota
        group = self.rooms[area]["group"]
        value = protocol.revision(group, targets)
        signature = (area, value)
        if self.signatures.get(entry.entry_id) == signature:
            return
        self.set_health(entry, "Einrichtung läuft")
        for target in targets:
            lamp = gateway.application_controller.get_device(ieee=t.EUI64.convert(target.ieee))
            lamp_ep = lamp.endpoints[target.endpoint]
            if 4 in lamp_ep.in_clusters:
                status = await lamp_ep.add_to_group(group, f"MS {group:04x}")
                if status not in (foundation.Status.SUCCESS, foundation.Status.DUPLICATE_EXISTS):
                    raise RuntimeError(f"Gruppenbeitritt fehlgeschlagen: {target.ieee}: {status}")
            # Reports go to the switch directly, independent of HA.
            destination = zdo_t.MultiAddress()
            destination.addrmode = 3
            destination.ieee = device.ieee
            destination.endpoint = ENDPOINT
            response = await lamp.zdo.Bind_req(lamp.ieee, target.endpoint, 6, destination)
            if response[0] != zdo_t.Status.SUCCESS:
                raise RuntimeError(f"Status-Binding fehlgeschlagen: {target.ieee}")
            await lamp_ep.in_clusters[6].configure_reporting(0, 0, 300, 1)
        await protocol.configure(device, group, targets)
        self.signatures[entry.entry_id] = signature
        self.set_health(entry, "Bereit für Gerätetest")

    async def options_updated(self, entry):
        area = device_area(self.hass, entry.data["device_id"])
        if area and area in self.rooms:
            self.rooms[area]["scene_order"] = list(entry.options.get("scene_order", []))
            await self.store.async_save({"rooms": self.rooms})
        self.schedule_sync()

    async def gesture(self, ieee, kind):
        entry = next((entry for entry in self.entries.values() if entry.unique_id == ieee), None)
        if entry is None:
            return
        device_id = entry.data["device_id"]
        area = device_area(self.hass, device_id)
        if not area:
            return
        try:
            if kind == 1:
                mode = entry.options.get("double_action", "media")
                if mode == "media":
                    allowed = {
                        entity.entity_id
                        for entity in room_entities(self.hass, area, "media_player")
                    }
                    players = [p for p in entry.options.get("players", []) if p in allowed]
                    if players:
                        # One action per gesture. Multi-player semantics currently
                        # follow HA's per-player media_play_pause implementation.
                        await self.hass.services.async_call(
                            "media_player",
                            "media_play_pause",
                            {"entity_id": players},
                            blocking=True,
                        )
                elif mode == "custom":
                    validated = await async_validate_actions_config(
                        self.hass, cv.SCRIPT_SCHEMA(entry.options.get("actions", []))
                    )
                    script = Script(self.hass, validated, entry.title, DOMAIN)
                    await script.async_run(
                        {"device_id": device_id, "area_id": area}, context=Context()
                    )
            elif kind == 2:
                async with self.scene_locks.setdefault(area, asyncio.Lock()):
                    if area not in self.rooms:
                        return
                    room = self.rooms[area]
                    scenes = scene_cycle(self.hass, area, room["scene_order"], self.ignore_label)
                    if not scenes:
                        return
                    current = room.get("last_scene")
                    index = (scenes.index(current) + 1) % len(scenes) if current in scenes else 0
                    await self.hass.services.async_call(
                        "scene",
                        "turn_on",
                        {"entity_id": scenes[index]},
                        blocking=True,
                    )
                    room["last_scene"] = scenes[index]
                    await self.store.async_save({"rooms": self.rooms})
        except HomeAssistantError, ZigbeeException, TimeoutError, ValueError:
            _LOGGER.exception("MoSwitch action failed for %s", entry.title)
            self.set_health(entry, "Aktion fehlgeschlagen; siehe Protokoll")

    def remove(self, entry):
        self.entries.pop(entry.entry_id, None)
        attached = self.listeners.pop(entry.entry_id, None)
        if attached:
            attached[0].remove_listener(attached[1])
        self.signatures.pop(entry.entry_id, None)

    def stop(self):
        self.active = False
        if self.pending:
            self.pending()
        for unsub in self.unsubs:
            unsub()
