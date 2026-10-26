"""Exercise room selection, protocol and actions against real HA registries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from zigpy.zcl import foundation

from custom_components.moswitch import protocol
from custom_components.moswitch.config_flow import options_schema, validate_options
from custom_components.moswitch.rooms import room_entities, scene_cycle
from custom_components.moswitch.runtime import GestureListener, Runtime

IEEE = "00:12:4b:00:01:02:03:04"


def entry(domain="zha", data=None, options=None, unique_id=None):
    return ConfigEntry(
        domain=domain,
        title="Test",
        data=data or {},
        options=options or {},
        version=1,
        minor_version=1,
        source="user",
        unique_id=unique_id,
        discovery_keys={},
        subentries_data=[],
    )


@pytest_asyncio.fixture
async def hass(tmp_path):
    h = HomeAssistant(str(tmp_path))
    h.config_entries = ConfigEntries(h, {})
    dr.async_setup(h)
    for registry in (lr, ar, dr, er):
        await registry.async_load(h, load_empty=True)
    yield h
    await h.async_stop()


def device(hass, area=None, ieee=IEEE, manufacturer="MoSwitch"):
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    result = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers={("zha", ieee)},
        manufacturer=manufacturer,
        model="MoSwitch v1",
        name="Test switch",
    )
    if area:
        result = dr.async_get(hass).async_update_device(result.id, area_id=area)
    return result


def entity(hass, domain, name, *, area=None, device_id=None, labels=None):
    registry = er.async_get(hass)
    result = registry.async_get_or_create(
        domain,
        "zha" if device_id else "homeassistant",
        name,
        suggested_object_id=name,
        device_id=device_id,
    )
    return registry.async_update_entity(
        result.entity_id, area_id=area, labels=labels or set(), name=name
    )


@pytest.mark.asyncio
async def test_room_membership_uses_entity_override_and_device_area(hass):
    a = ar.async_get(hass).async_create("Raum A").id
    b = ar.async_get(hass).async_create("Raum B").id
    lamp = device(hass, a)
    first = entity(hass, "light", "one", device_id=lamp.id)
    entity(hass, "light", "two", device_id=lamp.id, area=b)
    entity(hass, "media_player", "speaker", area=a)
    assert [e.entity_id for e in room_entities(hass, a, "light")] == [first.entity_id]


@pytest.mark.asyncio
async def test_scene_filter_preserves_order_and_ignores_label(hass):
    area = ar.async_get(hass).async_create("Wohnzimmer").id
    label = lr.async_get(hass).async_create("MoSwitch ignorieren").label_id
    one = entity(hass, "scene", "one", area=area)
    two = entity(hass, "scene", "two", area=area)
    hidden = entity(hass, "scene", "hidden", area=area, labels={label})
    assert scene_cycle(hass, area, [two.entity_id, hidden.entity_id, one.entity_id], label) == [
        two.entity_id,
        one.entity_id,
    ]


@pytest.mark.asyncio
async def test_atomic_configuration_wire_format_and_failure():
    calls = []

    async def send(general, command, schema, *args, **kwargs):
        calls.append((command, schema(*args).serialize(), kwargs))
        return SimpleNamespace(status=foundation.Status.SUCCESS)

    dev = SimpleNamespace(
        endpoints={10: SimpleNamespace(in_clusters={0: SimpleNamespace(request=send)})}
    )
    targets = [protocol.Target(IEEE, 1, 0x1234)]
    await protocol.configure(dev, 0x1000, targets)
    assert [command for command, _, _ in calls] == [0x40, 0x41, 0x42]
    assert calls[0][1][4:] == bytes.fromhex("0010fe01")
    assert calls[1][1] == bytes.fromhex("0004030201004b1200341201")
    assert all(call[2]["manufacturer"] == 0xFFF1 for call in calls)
    assert protocol.revision(4096, targets) == protocol.revision(4096, targets * 2)
    dev.endpoints[10].in_clusters[0].request = AsyncMock(return_value=SimpleNamespace(status=1))
    with pytest.raises(RuntimeError):
        await protocol.configure(dev, 4096, targets)
    assert dev.endpoints[10].in_clusters[0].request.await_count == 1


@pytest.mark.asyncio
async def test_options_limit_players_to_room(hass):
    area = ar.async_get(hass).async_create("Raum").id
    switch = device(hass, area)
    entity(hass, "media_player", "outside", area=None)
    schema = options_schema(hass, switch.id, {})
    assert schema({"double_action": "none"})["players"] == []
    with pytest.raises(ValueError, match="room_players_only"):
        await validate_options(hass, switch.id, {"players": ["media_player.outside"]})


@pytest.mark.asyncio
async def test_multiple_switches_share_scene_cursor_and_skip_restore(hass):
    area = ar.async_get(hass).async_create("Raum").id
    a = device(hass, area)
    b = device(hass, area, "00:12:4b:00:01:02:03:05")
    first = entity(hass, "scene", "first", area=area)
    second = entity(hass, "scene", "second", area=area)
    runtime = Runtime(hass)
    await runtime.start()
    one = entry("moswitch", {"device_id": a.id}, unique_id=IEEE)
    two = entry("moswitch", {"device_id": b.id}, unique_id="00:12:4b:00:01:02:03:05")
    runtime.entries = {one.entry_id: one, two.entry_id: two}
    runtime.rooms = {
        area: {
            "group": 4096,
            "scene_order": [second.entity_id, first.entity_id],
            "last_scene": None,
        }
    }
    received = []
    hass.services.async_register(
        "scene", "turn_on", lambda call: received.append(call.data["entity_id"])
    )
    await runtime.gesture(IEEE, 2)
    await runtime.gesture(two.unique_id, 2)
    assert received == [second.entity_id, first.entity_id]
    runtime.stop()


@pytest.mark.asyncio
async def test_double_tap_only_calls_selected_media_players(hass):
    area = ar.async_get(hass).async_create("Raum").id
    switch = device(hass, area)
    player = entity(hass, "media_player", "room", area=area)
    outside = entity(hass, "media_player", "elsewhere")
    runtime = Runtime(hass)
    one = entry(
        "moswitch",
        {"device_id": switch.id},
        {"players": [player.entity_id, outside.entity_id]},
        IEEE,
    )
    runtime.entries = {one.entry_id: one}
    received = []
    hass.services.async_register(
        "media_player", "media_play_pause", lambda call: received.append(call.data)
    )
    await runtime.gesture(IEEE, 1)
    assert received == [{"entity_id": [player.entity_id]}]
    # No light.toggle service is registered: the HA path never toggles lighting.


@pytest.mark.asyncio
async def test_listener_deduplicates_only_matching_sequence(hass):
    runtime = SimpleNamespace(hass=hass, gesture=AsyncMock())
    listener = GestureListener(runtime, IEEE)
    for _ in range(2):
        listener.cluster_command(1, 0x80, bytes.fromhex("0100000001"))
    listener.cluster_command(2, 0x80, bytes.fromhex("0200000001"))
    listener.cluster_command(3, 0x80, b"bad")
    await asyncio.sleep(0)
    assert runtime.gesture.await_count == 2


@pytest.mark.asyncio
async def test_commissioning_resolves_endpoint_and_commits_only_after_setup(hass, monkeypatch):
    area = ar.async_get(hass).async_create("Raum mit einem langen Namen").id
    switch = device(hass, area)
    lamp = device(hass, area, "00:12:4b:00:01:02:03:06", "Shelly")
    light = entity(hass, "light", "lamp", device_id=lamp.id)
    send = AsyncMock(return_value=SimpleNamespace(status=foundation.Status.SUCCESS))
    basic = SimpleNamespace(request=send, add_listener=lambda listener: None)
    switch_dev = SimpleNamespace(
        ieee=protocol.t.EUI64.convert(IEEE),
        model="MoSwitch v1",
        manufacturer="MoSwitch",
        endpoints={10: SimpleNamespace(in_clusters={0: basic})},
    )
    onoff = SimpleNamespace(
        configure_reporting=AsyncMock(return_value={0: foundation.Status.SUCCESS})
    )
    lamp_ep = SimpleNamespace(
        in_clusters={4: object(), 6: onoff},
        add_to_group=AsyncMock(return_value=foundation.Status.SUCCESS),
    )
    lamp_dev = SimpleNamespace(
        ieee=protocol.t.EUI64.convert("00:12:4b:00:01:02:03:06"),
        nwk=1234,
        endpoints={1: lamp_ep},
        zdo=SimpleNamespace(Bind_req=AsyncMock(return_value=[0])),
    )
    zha_lamp = SimpleNamespace(
        platform_entities={
            "light": SimpleNamespace(
                unique_id=light.unique_id, identifiers=SimpleNamespace(endpoint_id=1)
            )
        }
    )

    def get_device(ieee=None):
        return switch_dev if str(ieee) == IEEE else lamp_dev

    gateway = SimpleNamespace(
        application_controller=SimpleNamespace(get_device=get_device, groups={}),
        get_device=lambda ieee: zha_lamp,
    )
    monkeypatch.setattr("custom_components.moswitch.runtime.get_zha_gateway", lambda _: gateway)
    runtime = Runtime(hass)
    await runtime.start()
    one = entry("moswitch", {"device_id": switch.id}, unique_id=IEEE)
    runtime.entries = {one.entry_id: one}
    await runtime.commission(one)
    assert len(lamp_ep.add_to_group.call_args.args[1]) <= 16
    assert runtime.health[one.entry_id] == "Bereit für Gerätetest"
    assert send.await_count == 3
    await runtime.commission(one)
    assert send.await_count == 3  # Unchanged config does not rewrite flash every minute.
    runtime.stop()


@pytest.mark.asyncio
async def test_custom_action_is_validated_but_options_stay_json_serializable(hass):
    import json

    area = ar.async_get(hass).async_create("Raum").id
    switch = device(hass, area)
    options = {
        "double_action": "custom",
        "players": [],
        "actions": [{"event": "moswitch_test", "event_data": {"value": "{{ 1 + 1 }}"}}],
    }
    await validate_options(hass, switch.id, options)
    json.dumps(options)
    runtime = Runtime(hass)
    one = entry("moswitch", {"device_id": switch.id}, options, IEEE)
    runtime.entries = {one.entry_id: one}
    received = []
    hass.bus.async_listen("moswitch_test", lambda event: received.append(event.data))
    await runtime.gesture(IEEE, 1)
    await hass.async_block_till_done()
    assert received == [{"value": 2}]


@pytest.mark.asyncio
async def test_gesture_activity_records_every_kind_without_room_or_actions(hass):
    import struct

    from homeassistant.core import Event

    from custom_components.moswitch.logbook import async_describe_events

    runtime = Runtime(hass)
    switch = device(hass)
    one = entry("moswitch", {"device_id": switch.id}, unique_id=IEEE)
    runtime.entries = {one.entry_id: one}
    events = []
    hass.bus.async_listen("moswitch_gesture", lambda event: events.append(event.data))
    descriptions = {}
    async_describe_events(
        hass, lambda domain, name, callback: descriptions.update({name: (domain, callback)})
    )
    domain, describe = descriptions["moswitch_gesture"]
    assert domain == "zha"
    from homeassistant.components.logbook.helpers import async_determine_event_types

    hass.data["logbook"] = SimpleNamespace(external_events=descriptions)
    assert "moswitch_gesture" in async_determine_event_types(hass, None, [switch.id])
    listener = GestureListener(runtime, IEEE)
    for sequence, kind, name, message in [
        (1, 0, "single_press", "Einfach-Tipp erkannt"),
        (2, 1, "double_press", "Doppeltipp erkannt"),
        (3, 2, "hold", "Halten erkannt"),
    ]:
        packet = struct.pack("<IB", sequence, kind)
        listener.cluster_command(sequence, 0x80, packet)
        listener.cluster_command(sequence, 0x80, packet)
        await hass.async_block_till_done()
        # Top-level device_id is what HA Recorder/Logbook uses to select
        # activities in this existing ZHA device's activity panel.
        assert events[-1] == {"device_id": switch.id, "gesture": name, "sequence": sequence}
        assert describe(Event("moswitch_gesture", events[-1])) == {
            "name": "Test switch",
            "message": message,
        }
    assert len(events) == 3
    # No light service is registered: logging Single must never switch in HA.
    runtime.remove(one)
    listener.cluster_command(4, 0x80, struct.pack("<IB", 4, 0))
    await hass.async_block_till_done()
    assert len(events) == 3


@pytest.mark.asyncio
async def test_guided_calibration_entities_reuse_selected_zha_device(hass):
    import logging
    import struct

    from homeassistant.helpers.entity_component import EntityComponent
    from homeassistant.loader import async_setup as setup_loader

    from custom_components.moswitch import binary_sensor, button, number, sensor
    from custom_components.moswitch import switch as touch_switch

    setup_loader(hass)
    switch = device(hass)
    one = entry("moswitch", {"device_id": switch.id}, unique_id=IEEE)
    hass.config_entries._entries[one.entry_id] = one
    runtime = Runtime(hass)
    runtime.entries = {one.entry_id: one}
    runtime.calibrate_touch = AsyncMock()
    runtime.cancel_touch = AsyncMock()
    runtime.query_touch = AsyncMock()
    one.runtime_data = runtime
    components = []
    all_entities = []
    for domain, platform in [("button", button), ("sensor", sensor), ("binary_sensor", binary_sensor), ("number", number), ("switch", touch_switch)]:
        component = EntityComponent(logging.getLogger(__name__), domain, hass)
        component._platforms[domain].config_entry = one
        entities = []
        await platform.async_setup_entry(hass, one, entities.extend)
        await component.async_add_entities(entities)
        components.append(component)
        all_entities.extend(entities)
    start = next(e for e in all_entities if e.unique_id.endswith("_calibrate_touch"))
    cancel = next(e for e in all_entities if e.unique_id.endswith("_cancel_touch"))
    status = next(e for e in all_entities if e.unique_id.endswith("_touch_calibration"))
    contact = next(e for e in all_entities if e.unique_id.endswith("_touch_contact"))
    for entity in (start, cancel, status, contact):
        assert er.async_get(hass).async_get(entity.entity_id).device_id == switch.id
    parameters = [e for e in all_entities if "_touch_parameter_" in e.unique_id]
    measurements = [e for e in all_entities if "_touch_measurement_" in e.unique_id]
    assert len(parameters) == 8 and len(measurements) == 9
    for entity in [*parameters, *measurements]:
        assert er.async_get(hass).async_get(entity.entity_id).device_id == switch.id
    assert len(dr.async_get(hass).devices) == 1
    await start.async_press()
    runtime.calibrate_touch.assert_awaited_once_with(one)
    await cancel.async_press()
    runtime.cancel_touch.assert_awaited_once_with(one)
    listener = GestureListener(runtime, IEEE)
    packet = struct.pack("<BBBBIIIII", 2, 7, 1, 4, 25, 12, 8, 4, 9)
    listener.cluster_command(1, 0x81, packet)
    await hass.async_block_till_done()
    state = hass.states.get(status.entity_id)
    assert state.state == "Bitte tippen: 4/10"
    assert state.attributes["learning_taps"] == 4
    assert state.attributes["weakest_tap_delta_us"] == 9
    assert all(not e.available for e in parameters)
    threshold = next(e for e in measurements if e.key == "press_threshold_us")
    assert hass.states.get(threshold.entity_id).state == "20"
    calibration_events = []
    hass.bus.async_listen("moswitch_calibration", lambda e: calibration_events.append(e.data))
    def report(feedback, pressed, taps, contacts, rejected, duration, version=3):
        packet = struct.pack("<BBBBIIIII", version, 7, 1 | (2 if pressed else 0), taps, 30, 12, 8, 4, 9)
        packet += bytes([feedback, 0, 0, 0])
        packet += struct.pack("<IIIIIII", contacts, rejected, duration, 18, 0, 2, 12)
        listener.cluster_command(2, 0x81, packet)
        listener.cluster_command(2, 0x81, packet)  # Repeated report must not duplicate activity.
    report(3, True, 4, 5, 0, 0)
    await hass.async_block_till_done()
    assert hass.states.get(contact.entity_id).state == "on"
    assert "Kontakt erkannt" in hass.states.get(status.entity_id).state
    report(4, False, 5, 5, 0, 1000)
    await hass.async_block_till_done()
    assert hass.states.get(contact.entity_id).state == "off"
    assert hass.states.get(status.entity_id).state == "Tipp gezählt (5/10)"
    report(6, False, 5, 6, 1, 2100)
    await hass.async_block_till_done()
    assert "länger als 2 Sekunden" in hass.states.get(status.entity_id).state
    assert len(calibration_events) == 3
    report(6, False, 5, 6, 1, 2100, version=4)
    await hass.async_block_till_done()
    assert all(e.available for e in parameters if e.key not in protocol.TUNING_FIELDS)
    press_parameter = next(e for e in parameters if e.key == "press_delta_us")
    assert press_parameter.native_min_value == 5 and press_parameter.native_max_value == 1987
    from homeassistant.core import Event

    from custom_components.moswitch.logbook import async_describe_events
    descriptions = {}
    async_describe_events(hass, lambda domain, name, cb: descriptions.update({name: (domain, cb)}))
    domain, describe = descriptions["moswitch_calibration"]
    assert domain == "zha"
    assert "Tipp gezählt (5/10)" in describe(Event("moswitch_calibration", calibration_events[1]))["message"]
    runtime.test_touch = AsyncMock()
    runtime.baseline_touch = AsyncMock()
    test_mode = next(e for e in all_entities if e.unique_id.endswith("_touch_test_mode"))
    baseline_button = next(e for e in all_entities if e.unique_id.endswith("_baseline_touch"))
    listener.cluster_command(3, 0x81, v5_packet())
    await hass.async_block_till_done()
    assert test_mode.available and test_mode.is_on and all(e.available for e in parameters)
    assert not baseline_button.available
    assert er.async_get(hass).async_get(test_mode.entity_id).device_id == switch.id
    listener.cluster_command(4, 0x81, v5_packet(state=9, flags=29))
    await hass.async_block_till_done()
    assert baseline_button.available and "Ruhemessung starten" in status.native_value
    await baseline_button.async_press()
    runtime.baseline_touch.assert_awaited_once_with(one)
    await test_mode.async_turn_off()
    runtime.test_touch.assert_awaited_once_with(one, False)
    runtime.calibrate_touch.side_effect = RuntimeError("busy")
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await start.async_press()
    runtime.touch_received_at[one.entry_id] -= 121
    assert not status.available
    for component in components:
        for entity in list(component.entities):
            await component.async_remove_entity(entity.entity_id)


@pytest.mark.asyncio
async def test_guided_calibration_wire_commands_and_rejections():
    calls = []

    async def send(general, command, schema, *args, **kwargs):
        calls.append((command, schema(*args).serialize(), kwargs))
        return SimpleNamespace(status=foundation.Status.SUCCESS)

    dev = SimpleNamespace(
        endpoints={10: SimpleNamespace(in_clusters={0: SimpleNamespace(request=send)})}
    )
    await protocol.calibrate_touch(dev)
    await protocol.query_touch(dev)
    await protocol.cancel_touch(dev)
    assert [(c, payload) for c, payload, _ in calls] == [(0x44, b""), (0x45, b""), (0x46, b"")]
    assert all(kwargs["manufacturer"] == 0xFFF1 for _, _, kwargs in calls)
    assert all(kwargs["ask_for_ack"] is True and kwargs["expect_reply"] is True
               and kwargs["disable_default_response"] is False for _, _, kwargs in calls)
    dev.endpoints[10].in_clusters[0].request = AsyncMock(return_value=SimpleNamespace(status=1))
    with pytest.raises(RuntimeError):
        await protocol.calibrate_touch(dev)


@pytest.mark.parametrize(
    "version,state,count,size",
    [(1, 7, 0, 20), (1, 3, 1, 20), (2, 9, 0, 24), (2, 7, 11, 24), (2, 7, 0, 20), (3, 7, 0, 24)],
)
def test_invalid_guided_status(version, state, count, size):
    import struct

    payload = struct.pack("<BBBBIIII", version, state, 0, count, 12, 12, 8, 4) + bytes(size - 20)
    assert protocol.touch_status(payload) is None


def test_legacy_status_still_supported():
    import struct

    data = protocol.touch_status(struct.pack("<BBBBIIII", 1, 3, 1, 0, 12, 12, 8, 4))
    assert data["state"] == 3 and data["calibrated"] and data["protocol_version"] == 1


@pytest.mark.parametrize("feedback,failure,reserved", [(9, 0, 0), (0, 7, 0), (0, 0, 1)])
def test_invalid_v3_diagnostics(feedback, failure, reserved):
    import struct

    payload = struct.pack("<BBBBIIIII", 3, 7, 1, 0, 12, 12, 8, 4, 0)
    payload += bytes([feedback, failure, reserved, 0]) + bytes(28)
    assert protocol.touch_status(payload) is None


@pytest.mark.asyncio
async def test_touch_parameters_wire_and_bounds():
    import struct

    calls = []
    async def send(general, command, schema, *args, **kwargs):
        calls.append((command, schema(*args).serialize()))
        return SimpleNamespace(status=foundation.Status.SUCCESS)
    dev = SimpleNamespace(endpoints={10: SimpleNamespace(in_clusters={0: SimpleNamespace(request=send)})})
    await protocol.set_touch_parameters(dev, 13, 8, 4)
    assert calls == [(0x47, struct.pack("<III", 13, 8, 4))]
    for values in [(0, 8, 4), (1001, 8, 4), (13, 4, 4), (13, 2000, 4), (13, 8, 0), (True, 8, 4)]:
        with pytest.raises(ValueError):
            await protocol.set_touch_parameters(dev, *values)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_touch_parameter_requires_persisted_echo_and_preserves_concurrent_changes(hass, monkeypatch):
    import time

    runtime = Runtime(hass)
    switch = device(hass)
    one = entry("moswitch", {"device_id": switch.id}, unique_id=IEEE)
    runtime.entries = {one.entry_id: one}
    runtime.listen_switch = lambda entry: object()
    status = dict(state=3, calibrated=True, pressed=False, parameters_writable=True,
                  baseline_us=13, press_delta_us=8, release_delta_us=4)
    runtime.touch_report(IEEE, status)
    calls = []
    async def send(dev, baseline, press, release):
        calls.append((baseline, press, release))
    monkeypatch.setattr(protocol, "set_touch_parameters", send)
    task = asyncio.create_task(runtime.set_touch_parameter(one, "press_delta_us", 20))
    await asyncio.sleep(0)
    assert not task.done() and runtime.touch_status[one.entry_id]["press_delta_us"] == 8
    # An old report must not turn an ACK into a false success.
    runtime.touch_report(IEEE, status.copy())
    await asyncio.sleep(0)
    assert not task.done()
    second = asyncio.create_task(runtime.set_touch_parameter(one, "release_delta_us", 10))
    runtime.touch_report(IEEE, dict(status, press_delta_us=20))
    await task
    await asyncio.sleep(0)
    assert calls == [(13, 20, 4), (13, 20, 10)]
    runtime.touch_report(IEEE, dict(status, press_delta_us=20, release_delta_us=10))
    await second
    assert not runtime.touch_parameter_waiters
    # Rejected and unconfirmed commands do not optimistically replace values.
    monkeypatch.setattr(protocol, "set_touch_parameters", AsyncMock(side_effect=RuntimeError("rejected")))
    with pytest.raises(RuntimeError):
        await runtime.set_touch_parameter(one, "baseline_us", 15)
    assert runtime.touch_status[one.entry_id]["baseline_us"] == 13
    assert not runtime.touch_parameter_waiters
    runtime.touch_received_at[one.entry_id] = time.monotonic() - 121
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await runtime.set_touch_parameter(one, "baseline_us", 15)


def v5_packet(state=10, flags=23, tuning=(8, 12, 3, 0, 100)):
    import struct

    return (struct.pack("<BBBBIIIII", 5, state, flags, 0, 30, 12, 8, 4, 0)
            + bytes(32) + struct.pack("<IBBBBHH", 29, *tuning, 0))


def test_v5_separates_raw_filtered_confirmed_contact_and_validates_tuning():
    status = protocol.touch_status(v5_packet())
    assert status["test_mode"] and status["pressed"] and status["measurement_valid"]
    assert status["raw_us"] == 30 and status["filtered_us"] == 29
    assert status["press_debounce_ms"] == 8
    for values in ((0, 12, 3, 0, 100), (8, 201, 3, 0, 100), (8, 12, 2, 0, 100),
                   (8, 12, 9, 0, 100), (8, 12, 3, 31, 100), (8, 12, 3, 0, 501)):
        assert protocol.touch_status(v5_packet(tuning=values)) is None
    assert protocol.touch_status(v5_packet(flags=9)) is None  # Training requires quiet.
    assert protocol.touch_status(v5_packet(state=11)) is None
    assert protocol.touch_status(v5_packet()[:-1]) is None
    assert protocol.touch_status(v5_packet()[:-1] + b"\x01") is None


@pytest.mark.asyncio
async def test_v5_controls_and_tuning_wire_format():
    import struct

    calls = []
    async def send(general, command, schema, *args, **kwargs):
        calls.append((command, schema(*args).serialize()))
        return SimpleNamespace(status=foundation.Status.SUCCESS)
    dev = SimpleNamespace(endpoints={10: SimpleNamespace(in_clusters={0: SimpleNamespace(request=send)})})
    await protocol.diagnose_touch(dev)
    await protocol.baseline_touch(dev)
    await protocol.normal_touch(dev)
    await protocol.set_touch_tuning(dev, 20, 50, 5, 12, 100)
    assert calls == [(0x48, b""), (0x4A, b""), (0x49, b""), (0x4B, struct.pack("<BBBBHH", 20, 50, 5, 12, 100, 0))]
    with pytest.raises(ValueError):
        await protocol.set_touch_tuning(dev, 20, 50, 2, 12, 100)


@pytest.mark.asyncio
async def test_quiet_guard_survives_failed_cancelled_and_successful_training(hass, monkeypatch):
    runtime = Runtime(hass)
    dev = device(hass)
    one = entry("moswitch", {"device_id": dev.id}, unique_id=IEEE)
    runtime.entries = {one.entry_id: one}
    runtime.listen_switch = lambda entry: object()
    monkeypatch.setattr(protocol, "calibrate_touch", AsyncMock())
    monkeypatch.setattr(protocol, "normal_touch", AsyncMock())
    events = []
    hass.bus.async_listen("moswitch_gesture", lambda event: events.append(event.data))
    await runtime.calibrate_touch(one)
    await runtime.gesture(IEEE, 1)
    for state in (9, 1, 2, 7, 4, 8, 6, 3):
        runtime.touch_report(IEEE, protocol.touch_status(v5_packet(state=state, flags=29)))
        for kind in (0, 1, 2):
            await runtime.gesture(IEEE, kind)
    await runtime.test_touch(one, False)
    await runtime.gesture(IEEE, 2)  # ACK cannot clear guard without device status.
    await hass.async_block_till_done()
    assert not events
    runtime.touch_report(IEEE, protocol.touch_status(v5_packet(state=3, flags=17)))
    await runtime.gesture(IEEE, 0)
    await hass.async_block_till_done()
    assert len(events) == 1 and events[0]["gesture"] == "single_press"


@pytest.mark.asyncio
async def test_tuning_persisted_echo_while_held_in_diagnosis(hass, monkeypatch):
    runtime = Runtime(hass)
    dev = device(hass)
    one = entry("moswitch", {"device_id": dev.id}, unique_id=IEEE)
    runtime.entries = {one.entry_id: one}
    runtime.listen_switch = lambda entry: object()
    status = protocol.touch_status(v5_packet())
    runtime.touch_report(IEEE, status)
    setter = AsyncMock()
    monkeypatch.setattr(protocol, "set_touch_tuning", setter)
    task = asyncio.create_task(runtime.set_touch_parameter(one, "gap_grace_ms", 12))
    await asyncio.sleep(0)
    setter.assert_awaited_once()
    runtime.touch_report(IEEE, status)
    await asyncio.sleep(0)
    assert not task.done()
    runtime.touch_report(IEEE, dict(status, gap_grace_ms=12))
    await task
    assert runtime.touch_status[one.entry_id]["pressed"]


@pytest.mark.asyncio
async def test_touch_status_recovers_from_lost_query_ack_on_fresh_push(hass):
    from custom_components.moswitch.sensor import TouchStatus

    runtime = Runtime(hass)
    one = entry("moswitch", unique_id=IEEE)
    runtime.entries = {one.entry_id: one}
    one.runtime_data = runtime
    sensor = TouchStatus(one)
    runtime.touch_report(IEEE, protocol.touch_status(v5_packet()))
    assert sensor.available
    runtime.query_touch = AsyncMock(side_effect=TimeoutError())
    await sensor.async_update()
    assert not sensor.available
    runtime.touch_report(IEEE, protocol.touch_status(v5_packet()))
    assert sensor.available and sensor.native_value == "Diagnose aktiv – Aktionen gesperrt"


@pytest.mark.asyncio
async def test_calibration_button_exposes_actual_failure_and_does_not_claim_old_firmware():
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.moswitch.button import CalibrateTouch

    one = entry("moswitch", unique_id=IEEE)
    one.runtime_data = SimpleNamespace(calibrate_touch=AsyncMock(side_effect=RuntimeError("bad ACK")))
    with pytest.raises(HomeAssistantError, match="bad ACK") as error:
        await CalibrateTouch(one).async_press()
    assert "Firmware ab" not in str(error.value)
    assert "bereits erreicht" in str(error.value)


def v6_packet(state=7, contacts=0, progress=0, remaining=0, feedback=2):
    import struct

    packet = bytearray(v5_packet(state=state, flags=29))
    packet[0] = 6
    packet[3] = progress
    packet[24] = feedback
    packet[28:32] = struct.pack('<I', contacts)
    packet[66:68] = struct.pack('<H', remaining)
    return bytes(packet)


def test_v6_inactivity_clock_and_older_protocols():
    from custom_components.moswitch.touch_wizard import learning_message

    waiting = protocol.touch_status(v6_packet())
    assert waiting['learning_remaining_s'] == 0
    assert 'Zeit lassen' in learning_message(waiting)
    active = protocol.touch_status(v6_packet(contacts=4, progress=3, remaining=89, feedback=7))
    assert active['learning_remaining_s'] == 89
    assert '3 von 10' in learning_message(active) and 'Signal zu schwach' in learning_message(active)
    assert '89 Sekunden' in learning_message(active)
    assert protocol.touch_status(v6_packet(remaining=91, contacts=1)) is None
    assert protocol.touch_status(v6_packet(remaining=90)) is None
    assert protocol.touch_status(v6_packet(state=3, contacts=10, progress=10, remaining=90)) is None
    assert 'learning_remaining_s' not in protocol.touch_status(v5_packet())


@pytest.mark.asyncio
async def test_touch_wizard_uses_reports_and_retains_options(hass):
    from custom_components.moswitch.config_flow import MoSwitchOptionsFlow

    switch = device(hass)
    one = entry('moswitch', {'device_id': switch.id}, {'double_action': 'none'}, IEEE)
    hass.config_entries._entries[one.entry_id] = one
    runtime = Runtime(hass)
    runtime.entries[one.entry_id] = one
    one.runtime_data = runtime
    runtime.touch_report(IEEE, protocol.touch_status(v6_packet(state=10)))
    flow = MoSwitchOptionsFlow()
    flow.hass, flow.handler = hass, one.entry_id
    async def report(state):
        runtime.touch_report(IEEE, protocol.touch_status(v6_packet(state=state)))
    async def query(e):
        await report(10)
    async def prepare(e):
        await report(9)
    async def baseline(e):
        await report(1)
    runtime.query_touch = AsyncMock(side_effect=query)
    runtime.calibrate_touch = AsyncMock(side_effect=prepare)
    runtime.baseline_touch = AsyncMock(side_effect=baseline)
    runtime.cancel_touch = AsyncMock()
    result = await flow.async_step_init()
    assert result['menu_options'] == ['touch_setup', 'actions']
    result = await flow.async_step_touch_setup({})
    assert result['step_id'] == 'touch_baseline'
    runtime.calibrate_touch.assert_awaited_once()
    result = await flow.async_step_touch_baseline({})
    assert result['type'] == 'progress'
    task = result['progress_task']
    await asyncio.sleep(0)
    runtime.touch_report(IEEE, protocol.touch_status(v6_packet(contacts=3, progress=2, remaining=88)))
    await asyncio.sleep(0)
    result = await flow.async_step_touch_watch()
    assert '2 von 10' in result['description_placeholders']['status']
    assert not task.done()
    runtime.touch_report(IEEE, protocol.touch_status(v6_packet(state=3, contacts=10, progress=10)))
    await task
    result = await flow.async_step_touch_watch()
    assert result['step_id'] == 'touch_result'
    result = await flow.async_step_touch_result()
    assert 'gespeichert' in result['description_placeholders']['status']
    assert one.entry_id in runtime.touch_quiet
    result = await flow.async_step_touch_result({'action': 'keep_test'})
    assert result['data'] == {'double_action': 'none'}
    assert one.entry_id in runtime.touch_quiet
    async def activate(e, enabled):
        assert enabled is False
        packet = bytearray(v6_packet(state=3, contacts=10, progress=10))
        packet[2] = 17
        runtime.touch_report(IEEE, protocol.touch_status(packet))
    runtime.test_touch = AsyncMock(side_effect=activate)
    result = await flow.async_step_touch_result({'action': 'activate'})
    assert result['data'] == dict(one.options)
    assert one.entry_id not in runtime.touch_quiet


@pytest.mark.asyncio
async def test_touch_wizard_close_cancels_learning_but_keeps_quiet(hass):
    from custom_components.moswitch.config_flow import MoSwitchOptionsFlow

    switch = device(hass)
    one = entry('moswitch', {'device_id': switch.id}, unique_id=IEEE)
    hass.config_entries._entries[one.entry_id] = one
    runtime = Runtime(hass)
    runtime.entries[one.entry_id] = one
    one.runtime_data = runtime
    runtime.cancel_touch = AsyncMock()
    runtime.touch_report(IEEE, protocol.touch_status(v6_packet()))
    flow = MoSwitchOptionsFlow()
    flow.hass, flow.handler = hass, one.entry_id
    task = asyncio.create_task(flow._watch_touch())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    runtime.cancel_touch.assert_awaited_once_with(one)
    assert one.entry_id in runtime.touch_quiet


@pytest.mark.asyncio
async def test_touch_wizard_rejects_old_firmware_and_reports_command_failure(hass):
    from custom_components.moswitch.config_flow import MoSwitchOptionsFlow

    switch = device(hass)
    one = entry('moswitch', {'device_id': switch.id}, unique_id=IEEE)
    hass.config_entries._entries[one.entry_id] = one
    runtime = Runtime(hass)
    runtime.entries[one.entry_id] = one
    one.runtime_data = runtime
    flow = MoSwitchOptionsFlow()
    flow.hass, flow.handler = hass, one.entry_id
    async def old_query(e):
        runtime.touch_report(IEEE, protocol.touch_status(v5_packet()))
    runtime.query_touch = AsyncMock(side_effect=old_query)
    runtime.calibrate_touch = AsyncMock()
    result = await flow.async_step_touch_setup({})
    assert result['errors'] == {'base': 'touch_upgrade'}
    runtime.calibrate_touch.assert_not_awaited()
    runtime.query_touch = AsyncMock(side_effect=TimeoutError())
    result = await flow.async_step_touch_setup({})
    assert result['errors'] == {'base': 'touch_connection'}
    runtime.calibrate_touch.assert_not_awaited()
