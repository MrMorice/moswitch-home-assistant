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
from zigpy.zcl import foundation
from zigpy.zdo import types as zdo_t

from . import protocol
from .const import DOMAIN, ENDPOINT, IGNORE_LABEL, MAX_TARGETS, TOUCH_ACTIVATION_LOCKED
from .firmware_updates import MoSwitchOtaProvider
from .indicator import ring_reply
from .rooms import device_area, ieee_for_device, room_entities, scene_cycle

_LOGGER = logging.getLogger(__name__)


class GestureListener:
    def __init__(self, runtime, ieee):
        self.runtime = runtime
        self.ieee = ieee
        self.last = None

    def cluster_command(self, tsn, command_id, args):
        if command_id == 0x83:
            ring_reply(self.runtime, self.ieee, args)
            return
        if command_id == 0x81:
            status = protocol.touch_status(args)
            if status is not None:
                self.runtime.touch_report(self.ieee, status)
            return
        if command_id != 0x80 or not isinstance(args, (bytes, bytearray)) or len(args) != 5:
            return
        sequence, kind = struct.unpack("<IB", args)
        now = time.monotonic()
        if kind not in (0, 1, 2):
            return
        key = (sequence, kind)
        if self.last and self.last[0] == key and now - self.last[1] < 10:
            return
        self.last = (key, now)
        self.runtime.hass.async_create_task(
            self.runtime.gesture(self.ieee, kind, sequence),
            "MoSwitch gesture",
        )


class Runtime:
    def __init__(self, hass):
        self.hass = hass
        self.entries = {}
        self.health = {}
        self.touch_quiet = set()
        self.touch_status = {}
        self.touch_received_at = {}
        self.indicator_status = {}
        self.indicator_received_at = {}
        self.ring_waiters = {}
        self.ring_locks = {}
        self.touch_parameter_locks = {}
        self.touch_parameter_waiters = {}
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
        self.register_ota_provider()
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

    def register_ota_provider(self):
        try:
            controller = get_zha_gateway(self.hass).application_controller
        except (HomeAssistantError, KeyError, ValueError):
            return
        # Limit images by manufacturer/model metadata, not by the Silicon Labs
        # node-descriptor manufacturer code used by the development stack.
        if (
            hasattr(controller, "ota")
            and controller.ota is not self.ota_controller
        ):
            controller.ota.register_provider(
                MoSwitchOtaProvider(
                    url="https://raw.githubusercontent.com/MrMorice/moswitch-home-assistant/main/ota/index.json",
                    manufacturer_ids=[],
                )
            )
            self.ota_controller = controller.ota

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

    def touch_report(self, ieee, status):
        for entry in self.entries.values():
            if entry.unique_id == ieee:
                previous = self.touch_status.get(entry.entry_id)
                if status.get("test_mode") or status["state"] in (1, 2, 7, 9, 10, 11, 12, 13):
                    self.touch_quiet.add(entry.entry_id)
                # A reported normal state is required to release the HA guard.
                else:
                    self.touch_quiet.discard(entry.entry_id)
                feedback = status.get("feedback")
                if previous is not None and status["state"] == 7 and feedback in (3, 4, 5, 6, 7, 8):
                    fields = ("feedback", "learning_taps", "detected_contacts", "rejected_contacts")
                    if any(previous.get(key) != status.get(key) for key in fields):
                        self.hass.bus.async_fire("moswitch_calibration", {
                            "device_id": entry.data["device_id"],
                            **{key: status[key] for key in fields},
                            "duration_ms": status["last_duration_ms"],
                        })
                waiter = self.touch_parameter_waiters.get(entry.entry_id)
                if waiter is not None:
                    expected, future = waiter
                    if not future.done() and status["state"] not in (1, 2, 7, 9, 11, 12, 13) and all(
                        status.get(key) == value for key, value in expected.items()
                    ):
                        future.set_result(None)
                self.touch_status[entry.entry_id] = status
                self.touch_received_at[entry.entry_id] = time.monotonic()
                async_dispatcher_send(self.hass, f"{DOMAIN}_{entry.entry_id}")

    def listen_switch(self, entry):
        self.register_ota_provider()
        device = self.get_switch(entry)
        cluster = device.endpoints[ENDPOINT].in_clusters[0]
        attached = self.listeners.get(entry.entry_id)
        if attached is None or attached[0] is not cluster:
            if attached:
                attached[0].remove_listener(attached[1])
            listener = GestureListener(self, str(device.ieee))
            cluster.add_listener(listener)
            self.listeners[entry.entry_id] = (cluster, listener)
        return device

    async def calibrate_touch(self, entry):
        self.touch_quiet.add(entry.entry_id)
        await protocol.calibrate_touch(self.listen_switch(entry))

    async def resume_touch(self, entry):
        # Keep blocked on command loss; only a subsequent normal-mode report
        # clears the guard. Firmware also guards its direct Zigbee actions.
        self.touch_quiet.add(entry.entry_id)
        if self.touch_status.get(entry.entry_id, {}).get("actions_locked"):
            raise HomeAssistantError(TOUCH_ACTIVATION_LOCKED)
        await protocol.normal_touch(self.listen_switch(entry))

    async def baseline_touch(self, entry):
        self.touch_quiet.add(entry.entry_id)
        await protocol.baseline_touch(self.listen_switch(entry))

    def is_updating(self, entry):
        try:
            return bool(getattr(self.get_switch(entry), "ota_in_progress", False))
        except (HomeAssistantError, KeyError, ValueError, RuntimeError):
            return False

    async def query_touch(self, entry):
        if self.is_updating(entry):
            return
        await protocol.query_touch(self.listen_switch(entry))

    async def cancel_touch(self, entry):
        await protocol.cancel_touch(self.listen_switch(entry))

    async def set_touch_parameter(self, entry, key, value):
        tuning = key in protocol.TUNING_FIELDS
        fields = protocol.TUNING_FIELDS if tuning else ("baseline_us", "press_delta_us", "release_delta_us")
        if key not in fields:
            raise ValueError("Unbekannter Touchparameter")
        lock = self.touch_parameter_locks.setdefault(entry.entry_id, asyncio.Lock())
        async with lock:
            status = self.touch_status.get(entry.entry_id, {})
            received = self.touch_received_at.get(entry.entry_id, 0)
            if not status.get("tuning_writable" if tuning else "parameters_writable"):
                raise HomeAssistantError("Zum Einstellen ist Firmware ab 0.1.14 erforderlich")
            if time.monotonic() - received >= 120 or (not tuning and not status.get("calibrated")):
                raise HomeAssistantError("Keine aktuelle gültige Touchkalibrierung verfügbar")
            if status.get("state") in (1, 2, 7, 9, 11, 12, 13) or (status.get("pressed") and status.get("state") != 10):
                raise HomeAssistantError("Erst Kalibrierung beenden und den Touch loslassen")
            if key == "gap_grace_ms" and value > 30 and status.get("protocol_version", 0) < 7:
                raise HomeAssistantError("Mehr als 30 ms erfordern die ACMP-Firmware ab 0.1.35")
            values = {field: status[field] for field in fields}
            values[key] = value
            validate = protocol.valid_touch_tuning if tuning else protocol.valid_touch_parameters
            if not validate(*(values[field] for field in fields)):
                raise ValueError("Loslassabstand muss kleiner als Einschaltabstand sein")
            future = asyncio.get_running_loop().create_future()
            self.touch_parameter_waiters[entry.entry_id] = (values, future)
            try:
                setter = protocol.set_touch_tuning if tuning else protocol.set_touch_parameters
                await setter(
                    self.listen_switch(entry), *(values[field] for field in fields),
                )
                # A command ACK alone is insufficient: require the device's
                # report of the verified persisted tuple before showing success.
                await asyncio.wait_for(future, timeout=10)
            finally:
                self.touch_parameter_waiters.pop(entry.entry_id, None)
                if not future.done():
                    future.cancel()

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
        if entry.entry_id in self.touch_quiet:
            return
        device = self.listen_switch(entry)
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
        self.set_health(entry, "Bereit")

    async def options_updated(self, entry):
        area = device_area(self.hass, entry.data["device_id"])
        if area and area in self.rooms:
            self.rooms[area]["scene_order"] = list(entry.options.get("scene_order", []))
            await self.store.async_save({"rooms": self.rooms})
        self.schedule_sync()

    async def gesture(self, ieee, kind, sequence=None):
        entry = next((entry for entry in self.entries.values() if entry.unique_id == ieee), None)
        if entry is None or entry.entry_id in self.touch_quiet:
            return
        if kind not in (0, 1, 2):
            return
        device_id = entry.data["device_id"]
        activity = {
            "device_id": device_id,
            "gesture": ("single_press", "double_press", "hold")[kind],
        }
        if sequence is not None:
            activity["sequence"] = sequence
        self.hass.bus.async_fire("moswitch_gesture", activity)
        # Logging works without room assignment or a configured HA action.
        # Single is already handled by firmware and must never switch in HA.
        if kind == 0:
            return
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
        self.touch_quiet.discard(entry.entry_id)
        self.touch_status.pop(entry.entry_id, None)
        self.touch_received_at.pop(entry.entry_id, None)
        waiter = self.touch_parameter_waiters.pop(entry.entry_id, None)
        if waiter is not None and not waiter[1].done():
            waiter[1].set_exception(HomeAssistantError("MoSwitch-Verbindung beendet"))
        self.touch_parameter_locks.pop(entry.entry_id, None)
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
