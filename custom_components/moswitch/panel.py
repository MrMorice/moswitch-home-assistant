"""Device-scoped settings panel on the existing physical ZHA device."""

import time
from pathlib import Path

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.script import async_validate_actions_config

from .config_flow import options_schema
from .const import DOMAIN
from .device_configuration import device_name
from .number import PARAMETERS
from .protocol import TUNING_BOUNDS, TUNING_FIELDS
from .rooms import device_area, room_entities
from .touch_wizard import COMMAND_ERRORS, confirmed_touch_command, learning_message

PANEL_DATA = f"{DOMAIN}_panel"
ACTIVE_STATES = (1, 2, 7, 9, 11, 12, 13)
TUNING_LABELS = ("Entprellung beim Berühren", "Entprellung beim Loslassen", "Medianfilter",
                 "Aussetzer-Toleranz", "Ruhezeit nach Loslassen")


def panel_entry(hass, entry_id):
    runtime = hass.data.get(DOMAIN)
    entry = runtime.entries.get(entry_id) if runtime else None
    if entry is None:
        raise ValueError("Dieser MoSwitch ist nicht geladen. Bitte die Integration prüfen.")
    device = dr.async_get(hass).async_get(entry.data["device_id"])
    if device is None or device.manufacturer != "MoSwitch":
        raise ValueError("Das zugeordnete MoSwitch-Gerät fehlt.")
    return entry


def snapshot(hass, entry, *, area_id=None):
    runtime = entry.runtime_data
    device_id = entry.data["device_id"]
    area_id = area_id or device_area(hass, device_id)
    status = runtime.touch_status.get(entry.entry_id, {})
    received = runtime.touch_received_at.get(entry.entry_id)
    options = dict(entry.options)
    if area_id in runtime.rooms:
        options["scene_order"] = runtime.rooms[area_id]["scene_order"]
    lights = []
    for item in room_entities(hass, area_id, "light") if area_id else []:
        if item.platform in ("group", "template"):
            continue
        state = hass.states.get(item.entity_id)
        lights.append({"entity_id": item.entity_id,
                       "name": (state.name if state else item.name) or item.entity_id,
                       "direct": item.platform == "zha"})
    parameters = []
    for key, label in PARAMETERS:
        minimum = status.get("release_delta_us", 1) + 1 if key == "press_delta_us" else 1
        maximum = {"baseline_us": min(1000, 1999 - status.get("press_delta_us", 2)),
                   "press_delta_us": 1999 - status.get("baseline_us", 1),
                   "release_delta_us": max(1, status.get("press_delta_us", 2) - 1)}[key]
        parameters.append({"key": key, "label": label.removeprefix("Touch-"), "unit": "µs",
                           "min": minimum, "max": maximum, "step": 1})
    for key, label, bounds in zip(TUNING_FIELDS, TUNING_LABELS, TUNING_BOUNDS, strict=True):
        parameters.append({"key": key, "label": label, "unit": "" if key == "filter_samples" else "ms",
                           "min": bounds[0], "max": 30 if key == "gap_grace_ms" and status.get("protocol_version", 0) < 7 else bounds[1], "step": 2 if key == "filter_samples" else 1})
    return {"entry_id": entry.entry_id, "device_id": device_id, "name": device_name(hass, entry),
            "area_id": area_id, "areas": sorted([{"value": a.id, "label": a.name}
                for a in ar.async_get(hass).async_list_areas()], key=lambda a: a["label"]),
            "lights": lights, "options": options,
            "schema": vol.to_field_list(options_schema(hass, device_id, options, area_id=area_id), custom_serializer=cv.custom_serializer),
            "touch": status, "fresh": received is not None and time.monotonic() - received < 120,
            "quiet": entry.entry_id in runtime.touch_quiet or status.get("test_mode", False),
            "health": runtime.health.get(entry.entry_id, "Einrichtung ausstehend"),
            "message": learning_message(status), "parameters": parameters}


async def save_settings(hass, entry, area_id, options):
    """Validate before changing the physical device or its per-switch actions."""
    if ar.async_get(hass).async_get_area(area_id) is None:
        raise ValueError("Bitte einen vorhandenen Raum auswählen.")
    options = options_schema(hass, entry.data["device_id"], options)(options)
    # Validate membership for the proposed area before mutating the registry.
    for key, domain in (("players", "media_player"), ("scene_order", "scene")):
        allowed = {e.entity_id for e in room_entities(hass, area_id, domain)}
        if not set(options.get(key, [])) <= allowed:
            raise ValueError("Mediengeräte und Szenen müssen zum ausgewählten Raum gehören.")
    await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(options.get("actions", [])))
    registry = dr.async_get(hass)
    if device_area(hass, entry.data["device_id"]) != area_id:
        registry.async_update_device(entry.data["device_id"], area_id=area_id)
    hass.config_entries.async_update_entry(entry, options=options)
    # Explicitly synchronize even when only the room, rather than options, changed.
    await entry.runtime_data.options_updated(entry)


async def async_register(hass):
    if PANEL_DATA in hass.data:
        return
    from homeassistant.components import frontend, panel_custom

    from .candidate_ota import register as register_candidate_ota

    hass.data[PANEL_DATA] = {"sessions": {}}
    await hass.http.async_register_static_paths([StaticPathConfig(
        "/moswitch_static/device-settings.js", str(Path(__file__).with_name("device-settings.js")), False,
    ), StaticPathConfig(
        "/moswitch_static/device-dialog.js", str(Path(__file__).with_name("device-dialog.js")), False,
    )])
    frontend.add_extra_js_url(hass, "/moswitch_static/device-dialog.js?v=0.1.17")
    await panel_custom.async_register_panel(
        hass, frontend_url_path="moswitch", webcomponent_name="moswitch-device-settings",
        module_url="/moswitch_static/device-settings.js?v=0.1.17", require_admin=True,
    )
    register_candidate_ota(hass)
    for handler in (ws_subscribe, ws_action, ws_save, ws_preview, ws_info):
        websocket_api.async_register_command(hass, handler)


@websocket_api.websocket_command({vol.Required("type"): "moswitch/device/subscribe",
                                 vol.Required("entry_id"): str, vol.Required("session"): str})
@websocket_api.require_admin
@callback
def ws_subscribe(hass, connection, msg):
    try:
        entry = panel_entry(hass, msg["entry_id"])
    except ValueError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    session = msg["session"]
    sessions = hass.data[PANEL_DATA]["sessions"]

    @callback
    def changed():
        owned = sessions.get(session)
        if (owned and not owned.get("pending")
                and entry.runtime_data.touch_status.get(entry.entry_id, {}).get("state") not in ACTIVE_STATES):
            owned["owns"] = False
        connection.send_event(msg["id"], snapshot(hass, entry))

    unsub = async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}", changed)
    # A session belongs to this subscription and connection, never another tab.
    sessions[session] = {"entry": entry.entry_id, "connection": connection, "owns": False}

    @callback
    def close():
        unsub()
        owned = sessions.pop(session, None)
        if owned and owned["owns"] and (owned.get("pending") or entry.runtime_data.touch_status.get(entry.entry_id, {}).get("state") in ACTIVE_STATES):
            hass.async_create_task(_cancel_closed(entry))

    connection.subscriptions[msg["id"]] = close
    connection.send_result(msg["id"])
    changed()


async def _cancel_closed(entry):
    if any(s["entry"] == entry.entry_id and s["owns"]
           for s in entry.runtime_data.hass.data[PANEL_DATA]["sessions"].values()):
        return
    try:
        await entry.runtime_data.cancel_touch(entry)
    except COMMAND_ERRORS:
        pass  # Firmware remains quiet even if the cancellation cannot be delivered.


def _session(hass, connection, msg):
    entry = panel_entry(hass, msg["entry_id"])
    session = hass.data[PANEL_DATA]["sessions"].get(msg["session"])
    if session is None or session["entry"] != entry.entry_id or session["connection"] is not connection:
        raise ValueError("Die Verbindung zur Einstellungsseite wurde geschlossen. Bitte neu öffnen.")
    return entry, session


@websocket_api.websocket_command({vol.Required("type"): "moswitch/device/action",
    vol.Required("entry_id"): str, vol.Required("session"): str,
    vol.Required("action"): vol.In(("refresh", "prepare", "baseline", "cancel", "diagnose", "activate", "parameter")),
    vol.Optional("key"): str, vol.Optional("value"): vol.All(int)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_action(hass, connection, msg):
    session = None
    try:
        entry, session = _session(hass, connection, msg)
        runtime = entry.runtime_data
        command = msg["action"]
        status = runtime.touch_status.get(entry.entry_id, {})
        if command == "prepare":
            if any(s["entry"] == entry.entry_id and s["owns"] and s is not session
                   for s in hass.data[PANEL_DATA]["sessions"].values()):
                raise ValueError("Das Einlernen ist bereits in einem anderen Fenster geöffnet.")
            session["owns"] = True
            session["pending"] = True
            await confirmed_touch_command(hass, entry, runtime.query_touch, lambda s: True)
            status = runtime.touch_status[entry.entry_id]
            if status.get("protocol_version", 0) < 7 or not status.get("frontend_active"):
                raise ValueError("Zum Einlernen sind eine aktive Touchmessung und Firmware ab 0.1.36 erforderlich.")
            if hass.data[PANEL_DATA]["sessions"].get(msg["session"]) is not session:
                raise ValueError("Die Einstellungsseite wurde geschlossen.")
            session["owns"] = True
            await confirmed_touch_command(hass, entry, runtime.calibrate_touch, lambda s: s.get("state") == 9)
        elif command == "baseline":
            if not session["owns"] or status.get("state") != 9:
                raise ValueError("Bitte zuerst das Einlernen vorbereiten.")
            await confirmed_touch_command(hass, entry, runtime.baseline_touch, lambda s: s.get("state") in (1, 2, 7, 4, 5, 6))
        elif command == "parameter":
            await runtime.set_touch_parameter(entry, msg["key"], msg["value"])
        elif command == "activate":
            if status.get("state") in ACTIVE_STATES or not status.get("calibrated"):
                raise ValueError("Bitte das Einlernen zuerst erfolgreich abschließen.")
            await confirmed_touch_command(hass, entry, runtime.test_touch,
                                         lambda s: s.get("state") == 3 and not s.get("test_mode"), False)
            session["owns"] = False
        else:
            method, expected, args = {
                "refresh": (runtime.query_touch, lambda s: True, ()),
                "cancel": (runtime.cancel_touch, lambda s: s.get("state") not in ACTIVE_STATES, ()),
                "diagnose": (runtime.test_touch, lambda s: s.get("test_mode"), (True,)),
            }[command]
            await confirmed_touch_command(hass, entry, method, expected, *args)
            if command == "cancel":
                session["owns"] = False
        connection.send_result(msg["id"], snapshot(hass, entry))
    except (*COMMAND_ERRORS, vol.Invalid) as err:
        connection.send_error(msg["id"], "command_failed", str(err) or "Keine aktuelle Bestätigung vom Schalter erhalten. Bitte Verbindung prüfen.")
    finally:
        if session:
            session["pending"] = False
            if entry.runtime_data.touch_status.get(entry.entry_id, {}).get("state") not in ACTIVE_STATES:
                session["owns"] = False
        # Also cancel a prepare command that finished after its tab disconnected.
        if (session and session["owns"]
                and hass.data[PANEL_DATA]["sessions"].get(msg["session"]) is not session
                and entry.runtime_data.touch_status.get(entry.entry_id, {}).get("state") in ACTIVE_STATES):
            await _cancel_closed(entry)



@websocket_api.websocket_command({vol.Required("type"): "moswitch/device/save",
    vol.Required("entry_id"): str, vol.Required("area_id"): str, vol.Required("options"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass, connection, msg):
    try:
        entry = panel_entry(hass, msg["entry_id"])
        await save_settings(hass, entry, msg["area_id"], msg["options"])
        connection.send_result(msg["id"], snapshot(hass, entry))
    except (HomeAssistantError, ValueError, vol.Invalid) as err:
        connection.send_error(msg["id"], "invalid_settings", str(err))


@websocket_api.websocket_command({vol.Required("type"): "moswitch/device/preview",
    vol.Required("entry_id"): str, vol.Required("area_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_preview(hass, connection, msg):
    try:
        entry = panel_entry(hass, msg["entry_id"])
        if ar.async_get(hass).async_get_area(msg["area_id"]) is None:
            raise ValueError("Raum nicht gefunden")
        connection.send_result(msg["id"], snapshot(hass, entry, area_id=msg["area_id"]))
    except ValueError as err:
        connection.send_error(msg["id"], "not_found", str(err))


@websocket_api.websocket_command({vol.Required("type"): "moswitch/device/info",
                                 vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_info(hass, connection, msg):
    """Resolve the physical device before opening its browser-local dialog."""
    try:
        entry = panel_entry(hass, msg["entry_id"])
        connection.send_result(msg["id"], {
            "entry_id": entry.entry_id, "device_id": entry.data["device_id"],
            "name": device_name(hass, entry),
        })
    except ValueError as err:
        connection.send_error(msg["id"], "not_found", str(err))
