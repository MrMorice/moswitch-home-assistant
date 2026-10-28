"""Exercise room selection, protocol and actions against real HA registries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    assert len(parameters) == 8 and len(measurements) == 10
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
    assert threshold.native_value == 20
    assert hass.states.get(threshold.entity_id) is None  # Debug entities are opt-in.
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
    assert contact.is_on is True
    assert "Kontakt erkannt" in hass.states.get(status.entity_id).state
    report(4, False, 5, 5, 0, 1000)
    await hass.async_block_till_done()
    assert contact.is_on is False
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
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await test_mode.async_turn_off()
    runtime.test_touch.assert_not_awaited()
    runtime.calibrate_touch.side_effect = RuntimeError("busy")

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


def v7_packet(state=7, contacts=0, progress=0, remaining=0, feedback=2, hold_ms=0, gap_ms=50):
    import struct

    packet = bytearray(v6_packet(state, contacts, progress, remaining, feedback))
    packet[0] = 7
    packet[26:28] = b"\1\1"
    packet[63] = gap_ms
    packet.extend(struct.pack("<IHH", 1, hold_ms, 0))
    return bytes(packet)


def test_acmp_protocol_verification_and_backend_isolation():
    from custom_components.moswitch.touch_wizard import learning_message

    for state, feedback, hold in ((11, 9, 2000), (12, 10, 900), (13, 11, 0)):
        status = protocol.touch_status(v7_packet(state, 10, 10, feedback=feedback, hold_ms=hold))
        assert status["measurement_backend"] == "acmp"
        assert status["gap_grace_ms"] == 50
        assert status["hold_remaining_ms"] == hold
        assert learning_message(status)
    assert "noch nicht gespeichert" in learning_message(protocol.touch_status(v7_packet(11, 10, 10)))
    assert "liegen lassen" in learning_message(protocol.touch_status(v7_packet(12, 10, 10)))
    assert "loslassen" in learning_message(protocol.touch_status(v7_packet(13, 10, 10)))
    assert protocol.touch_status(v7_packet(11, 10, 9)) is None
    assert protocol.touch_status(v7_packet(12, 10, 10, hold_ms=2001)) is None
    assert protocol.touch_status(v7_packet(gap_ms=101)) is None
    broken = bytearray(v7_packet())
    broken[26] = 0
    assert protocol.touch_status(broken) is None
    assert protocol.touch_status(v5_packet(tuning=(8, 50, 3, 50, 100))) is None


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
    runtime.touch_report(IEEE, protocol.touch_status(v7_packet(state=10)))
    flow = MoSwitchOptionsFlow()
    flow.hass, flow.handler = hass, one.entry_id
    async def report(state):
        runtime.touch_report(IEEE, protocol.touch_status(v7_packet(state=state)))
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
    runtime.touch_report(IEEE, protocol.touch_status(v7_packet(contacts=3, progress=2, remaining=88)))
    await asyncio.sleep(0)
    result = await flow.async_step_touch_watch()
    assert '2 von 10' in result['description_placeholders']['status']
    assert not task.done()
    for state, hold in ((11, 2000), (12, 1000), (13, 0)):
        runtime.touch_report(IEEE, protocol.touch_status(v7_packet(state=state, contacts=10, progress=10, hold_ms=hold)))
        await asyncio.sleep(0)
        assert not task.done()  # Ten tips alone must never finish/save the flow.
        result = await flow.async_step_touch_watch()
        assert result['type'] == 'progress'
    runtime.touch_report(IEEE, protocol.touch_status(v7_packet(state=3, contacts=10, progress=10)))
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
        packet = bytearray(v7_packet(state=3, contacts=10, progress=10))
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


@pytest.mark.asyncio
async def test_device_configuration_links_are_per_switch_without_duplicate_devices(hass):
    from custom_components.moswitch import async_remove_entry
    from custom_components.moswitch.config_flow import MoSwitchOptionsFlow
    from custom_components.moswitch.device_configuration import (
        attach_configuration_link,
        configuration_url,
    )

    area = ar.async_get(hass).async_create('Wohnzimmer').id
    first = device(hass, area)
    second = device(hass, ieee='00:12:4b:00:01:02:03:05')
    registry = dr.async_get(hass)
    registry.async_update_device(first.id, name_by_user='Links am Sofa')
    first_owner = first.config_entry_id
    second_owner = second.config_entry_id
    one = entry('moswitch', {'device_id': first.id}, unique_id=IEEE)
    two = entry('moswitch', {'device_id': second.id}, unique_id='00:12:4b:00:01:02:03:05')
    for config in (one, two):
        hass.config_entries._entries[config.entry_id] = config
        attach_configuration_link(hass, config)
    assert len(registry.devices) == 2
    first = registry.async_get(first.id)
    second = registry.async_get(second.id)
    assert first.config_entry_id == first_owner and first.area_id == area
    assert second.config_entry_id == second_owner
    assert first.configuration_url == configuration_url(one)
    assert second.configuration_url == configuration_url(two)
    assert first.configuration_url != second.configuration_url
    assert one.entry_id in first.configuration_url and two.entry_id not in first.configuration_url
    flow = MoSwitchOptionsFlow()
    flow.hass, flow.handler = hass, one.entry_id
    result = await flow.async_step_init()
    assert result['description_placeholders']['device'] == 'Links am Sofa'
    result = await flow.async_step_touch_setup()
    assert result['description_placeholders']['device'] == 'Links am Sofa'
    await async_remove_entry(hass, one)
    assert registry.async_get(first.id).configuration_url is None
    assert registry.async_get(second.id).configuration_url == configuration_url(two)
    assert len(registry.devices) == 2
    registry.async_update_device(second.id, configuration_url='https://example.com/other')
    await async_remove_entry(hass, two)
    assert registry.async_get(second.id).configuration_url == 'https://example.com/other'


def panel_config(hass, physical):
    from custom_components.moswitch.panel import PANEL_DATA
    config = entry('moswitch', {'device_id': physical.id}, unique_id=next(
        value for domain, value in physical.identifiers if domain == 'zha'))
    hass.config_entries._entries[config.entry_id] = config
    runtime = hass.data.setdefault('moswitch', Runtime(hass))
    runtime.entries[config.entry_id] = config
    config.runtime_data = runtime
    hass.data[PANEL_DATA] = {'sessions': {}}
    return config


@pytest.mark.asyncio
async def test_panel_snapshot_scopes_schema_and_names_to_selected_switch(hass):
    import json
    import time

    from custom_components.moswitch.panel import panel_entry, snapshot

    area_a = ar.async_get(hass).async_create('Sofa').id
    area_b = ar.async_get(hass).async_create('Küche').id
    one = panel_config(hass, device(hass, area_a))
    two = panel_config(hass, device(hass, area_b, ieee='00:12:4b:00:01:02:03:05'))
    player = entity(hass, 'media_player', 'kitchen_speaker', area=area_b)
    entity(hass, 'scene', 'sofa_evening', area=area_a)
    light = entity(hass, 'light', 'sofa_light', area=area_a)
    one.runtime_data.touch_status[one.entry_id] = {'state': 3, 'calibrated': True, 'baseline_us': 18}
    one.runtime_data.touch_received_at[one.entry_id] = time.monotonic()
    first = snapshot(hass, one)
    second = snapshot(hass, two)
    assert first['fresh'] and not second['fresh']
    assert first['touch']['baseline_us'] == 18 and second['touch'] == {}
    assert first['area_id'] == area_a and second['area_id'] == area_b
    assert first['lights'][0]['entity_id'] == light.entity_id
    assert player.entity_id not in json.dumps(first['schema'])
    assert player.entity_id in json.dumps(second['schema'])
    preview = snapshot(hass, one, area_id=area_b)
    assert player.entity_id in json.dumps(preview['schema'])
    assert dr.async_get(hass).async_get(one.data['device_id']).area_id == area_a
    assert panel_entry(hass, two.entry_id) is two
    with pytest.raises(ValueError):
        panel_entry(hass, 'unknown')
    json.dumps(first)  # All websocket fields must be serializable.


@pytest.mark.asyncio
async def test_panel_saves_area_and_options_for_exactly_one_device(hass):
    from custom_components.moswitch.panel import save_settings

    area_a = ar.async_get(hass).async_create('Sofa').id
    area_b = ar.async_get(hass).async_create('Küche').id
    one = panel_config(hass, device(hass, area_a))
    two = panel_config(hass, device(hass, area_a, ieee='00:12:4b:00:01:02:03:05'))
    one.runtime_data.options_updated = AsyncMock()
    player = entity(hass, 'media_player', 'kitchen_speaker', area=area_b)
    options = {'double_action': 'media', 'players': [player.entity_id], 'actions': [], 'scene_order': []}
    with pytest.raises(ValueError):
        await save_settings(hass, one, area_a, options)
    with pytest.raises(ValueError):
        await save_settings(hass, one, 'missing', options)
    assert dr.async_get(hass).async_get(one.data['device_id']).area_id == area_a
    await save_settings(hass, one, area_b, options)
    assert dr.async_get(hass).async_get(one.data['device_id']).area_id == area_b
    assert dr.async_get(hass).async_get(two.data['device_id']).area_id == area_a
    assert one.options == options and not two.options
    one.runtime_data.options_updated.assert_awaited_once_with(one)


@pytest.mark.asyncio
async def test_panel_session_and_admin_validation(hass):
    from homeassistant.exceptions import Unauthorized

    from custom_components.moswitch.panel import PANEL_DATA, _session, ws_subscribe

    config = panel_config(hass, device(hass))
    owner = SimpleNamespace(user=SimpleNamespace(is_admin=True))
    another = SimpleNamespace(user=SimpleNamespace(is_admin=True))
    hass.data[PANEL_DATA]['sessions']['s'] = {'entry': config.entry_id, 'connection': owner, 'owns': False}
    msg = {'entry_id': config.entry_id, 'session': 's'}
    assert _session(hass, owner, msg)[0] is config
    with pytest.raises(ValueError):
        _session(hass, another, msg)
    with pytest.raises(Unauthorized):
        ws_subscribe(hass, SimpleNamespace(user=SimpleNamespace(is_admin=False)), msg)


@pytest.mark.asyncio
async def test_panel_disconnect_cancels_only_owned_active_learning(hass):
    from unittest.mock import Mock

    from custom_components.moswitch.panel import ws_subscribe

    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.cancel_touch = AsyncMock()
    runtime.touch_status[config.entry_id] = {'state': 7}
    conn = SimpleNamespace(user=SimpleNamespace(is_admin=True), subscriptions={}, send_result=Mock(), send_event=Mock())
    ws_subscribe(hass, conn, {'id': 1, 'entry_id': config.entry_id, 'session': 'a'})
    conn.subscriptions[1]()
    await hass.async_block_till_done()
    runtime.cancel_touch.assert_not_awaited()
    ws_subscribe(hass, conn, {'id': 2, 'entry_id': config.entry_id, 'session': 'b'})
    hass.data['moswitch_panel']['sessions']['b']['owns'] = True
    conn.subscriptions[2]()
    await hass.async_block_till_done()
    runtime.cancel_touch.assert_awaited_once_with(config)
    runtime.cancel_touch.reset_mock()
    runtime.touch_status[config.entry_id] = {'state': 3, 'test_mode': True}
    ws_subscribe(hass, conn, {'id': 3, 'entry_id': config.entry_id, 'session': 'c'})
    hass.data['moswitch_panel']['sessions']['c']['owns'] = True
    conn.subscriptions[3]()
    await hass.async_block_till_done()
    runtime.cancel_touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_entity_cleanup_preserves_ids_and_user_choices_and_zha(hass):
    from custom_components.moswitch.entity_layout import migrate_entity_layout

    physical = device(hass)
    config = panel_config(hass, physical)
    registry = er.async_get(hass)
    def create(suffix, platform='moswitch', owner=config.entry_id):
        return registry.async_get_or_create('sensor', platform, f'{IEEE}_{suffix}',
            config_entry=hass.config_entries.async_get_entry(owner), device_id=physical.id)
    number = create('touch_parameter_baseline_us')
    reading = create('touch_measurement_raw_us')
    status = create('touch_calibration')
    zha = create('firmware_update', 'zha', physical.config_entry_id)
    user = create('touch_contact')
    registry.async_update_entity(user.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    migrate_entity_layout(hass, config)
    assert registry.async_get(number.entity_id).disabled_by is None
    assert registry.async_get(reading.entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(status.entity_id).disabled_by is None
    assert registry.async_get(zha.entity_id).disabled_by is None
    assert registry.async_get(user.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    registry.async_update_entity(number.entity_id, disabled_by=None)
    migrate_entity_layout(hass, config)
    assert registry.async_get(number.entity_id).disabled_by is None  # Respect later re-enabling.
    assert config.data['ui_layout_version'] == 2


@pytest.mark.asyncio
async def test_old_controls_are_disabled_by_default_but_status_remains(hass):
    from custom_components.moswitch.binary_sensor import TouchContact
    from custom_components.moswitch.button import CalibrateTouch
    from custom_components.moswitch.number import TouchParameter
    from custom_components.moswitch.sensor import TouchMeasurement, TouchStatus
    from custom_components.moswitch.switch import TouchTestMode

    config = panel_config(hass, device(hass))
    for item in [CalibrateTouch(config), TouchMeasurement(config, 'raw_us', 'Raw', 'µs')]:
        assert not item.entity_registry_enabled_default
    for item in [TouchContact(config), TouchParameter(config, 'baseline_us', 'Baseline'),
                 TouchMeasurement(config, 'baseline_us', 'Baseline', 'µs'),
                 TouchMeasurement(config, 'learning_taps', 'Fortschritt', None),
                 TouchTestMode(config), TouchStatus(config)]:
        assert item.entity_registry_enabled_default



@pytest.mark.asyncio
async def test_closed_panel_does_not_start_learning_after_query(hass):
    import inspect
    import time
    from unittest.mock import Mock

    from custom_components.moswitch.panel import PANEL_DATA, ws_action

    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    conn = SimpleNamespace(send_result=Mock(), send_error=Mock())
    session = {'entry': config.entry_id, 'connection': conn, 'owns': False}
    hass.data[PANEL_DATA]['sessions']['s'] = session
    async def query(config):
        runtime.touch_status[config.entry_id] = {'state': 3, 'protocol_version': 7, 'frontend_active': True}
        runtime.touch_received_at[config.entry_id] = time.monotonic()
        hass.data[PANEL_DATA]['sessions'].pop('s')
    runtime.query_touch = query
    runtime.calibrate_touch = AsyncMock()
    await inspect.unwrap(ws_action)(hass, conn, {
        'id': 1, 'entry_id': config.entry_id, 'session': 's', 'action': 'prepare'})
    runtime.calibrate_touch.assert_not_awaited()
    conn.send_error.assert_called_once()


@pytest.mark.asyncio
async def test_panel_activation_requires_explicit_command_and_fresh_normal_report(hass):
    import inspect
    import time
    from unittest.mock import Mock

    from custom_components.moswitch.panel import PANEL_DATA, ws_action

    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.touch_status[config.entry_id] = {'state': 10, 'calibrated': True, 'test_mode': True}
    conn = SimpleNamespace(send_result=Mock(), send_error=Mock())
    hass.data[PANEL_DATA]['sessions']['s'] = {'entry': config.entry_id, 'connection': conn, 'owns': False}
    async def normal(config, enabled):
        assert not enabled
        runtime.touch_status[config.entry_id] = {'state': 3, 'calibrated': True, 'test_mode': False}
        runtime.touch_received_at[config.entry_id] = time.monotonic()
    async def query(config):
        runtime.touch_received_at[config.entry_id] = time.monotonic()
    runtime.query_touch = AsyncMock(side_effect=query)
    runtime.test_touch = AsyncMock(side_effect=normal)
    await inspect.unwrap(ws_action)(hass, conn, {
        'id': 1, 'entry_id': config.entry_id, 'session': 's', 'action': 'activate'})
    runtime.test_touch.assert_awaited_once_with(config, False)
    conn.send_error.assert_not_called()
    assert conn.send_result.call_args.args[1]['touch']['test_mode'] is False


@pytest.mark.asyncio
async def test_dialog_layout_restores_only_native_controls_and_respects_user_disabling(hass):
    from homeassistant.const import EntityCategory

    from custom_components.moswitch.entity_layout import migrate_entity_layout

    config = panel_config(hass, device(hass))
    hass.config_entries.async_update_entry(config, data={**config.data, 'ui_layout_version': 1})
    registry = er.async_get(hass)
    native = registry.async_get_or_create('number', 'moswitch', f'{IEEE}_touch_parameter_press_delta_us',
        config_entry=config, device_id=config.data['device_id'], disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    user = registry.async_get_or_create('switch', 'moswitch', f'{IEEE}_touch_test_mode',
        config_entry=config, device_id=config.data['device_id'], disabled_by=er.RegistryEntryDisabler.USER)
    raw = registry.async_get_or_create('sensor', 'moswitch', f'{IEEE}_touch_measurement_raw_us',
        config_entry=config, device_id=config.data['device_id'], disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    migrate_entity_layout(hass, config)
    assert registry.async_get(native.entity_id).disabled_by is None
    assert registry.async_get(user.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(raw.entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(raw.entity_id).entity_category is EntityCategory.DIAGNOSTIC
    registry.async_update_entity(native.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    migrate_entity_layout(hass, config)
    assert registry.async_get(native.entity_id).disabled_by is er.RegistryEntryDisabler.USER


@pytest.mark.asyncio
async def test_native_double_action_preserves_other_switch_settings(hass):
    from custom_components.moswitch.select import DoubleAction

    config = panel_config(hass, device(hass))
    options = {'double_action': 'media', 'players': ['media_player.test'], 'actions': [], 'scene_order': ['scene.test']}
    hass.config_entries.async_update_entry(config, options=options)
    selector = DoubleAction(config)
    selector.hass = hass
    await selector.async_select_option('none')
    assert selector.current_option == 'none'
    assert dict(config.options) == {**options, 'double_action': 'none'}
    with pytest.raises(ValueError):
        await selector.async_select_option('wrong')
    assert selector.current_option == 'none'


@pytest.mark.asyncio
async def test_dialog_info_checks_admin_and_resolves_device(hass):
    import inspect
    from unittest.mock import Mock

    from homeassistant.exceptions import Unauthorized

    from custom_components.moswitch.device_configuration import configuration_url
    from custom_components.moswitch.panel import ws_info

    config = panel_config(hass, device(hass))
    conn = SimpleNamespace(user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock())
    await inspect.unwrap(ws_info)(hass, conn, {'id': 1, 'entry_id': config.entry_id})
    assert conn.send_result.call_args.args[1]['device_id'] == config.data['device_id']
    assert configuration_url(config).startswith('homeassistant://config/devices/device/'+config.data['device_id'])
    assert '?moswitch_settings='+config.entry_id in configuration_url(config)
    with pytest.raises(Unauthorized):
        ws_info(hass, SimpleNamespace(user=SimpleNamespace(is_admin=False)), {})
    await inspect.unwrap(ws_info)(hass, conn, {'id': 2, 'entry_id': 'missing'})
    conn.send_error.assert_called_once()


@pytest.mark.asyncio
async def test_native_test_mode_waits_for_confirmed_normal_report(hass):
    import time

    from custom_components.moswitch.switch import TouchTestMode

    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.touch_status[config.entry_id] = {'state': 10, 'calibrated': True, 'test_mode': True}
    async def normal(config, enabled):
        runtime.touch_status[config.entry_id] = {'state': 3, 'calibrated': True, 'test_mode': False}
        runtime.touch_received_at[config.entry_id] = time.monotonic()
    runtime.test_touch = AsyncMock(side_effect=normal)
    control = TouchTestMode(config)
    control.hass = hass
    await control.async_turn_off()
    runtime.test_touch.assert_awaited_once_with(config, False)
    assert not control.is_on


@pytest.mark.asyncio
async def test_native_double_tap_change_keeps_shared_room_scene_order(hass):
    from custom_components.moswitch.select import DoubleAction

    area = ar.async_get(hass).async_create('Wohnzimmer').id
    config = panel_config(hass, device(hass, area))
    config.runtime_data.rooms[area] = {'scene_order': ['scene.latest']}
    hass.config_entries.async_update_entry(config, options={'double_action': 'media', 'scene_order': ['scene.old']})
    control = DoubleAction(config)
    control.hass = hass
    await control.async_select_option('none')
    assert config.options['scene_order'] == ['scene.latest']


def test_hold_retry_keeps_taps_and_failure_explanation_is_not_duplicated():
    from custom_components.moswitch.touch_wizard import learning_message

    status = protocol.touch_status(v7_packet(11, 10, 10, feedback=8, hold_ms=2000))
    text = learning_message(status)
    assert "wiederholen" in text and "zehn Tipps bleiben erhalten" in text
    assert "gültige Messungen" in text and "noch nicht gespeichert" in text
    failed = learning_message({"state": 4, "failure_reason": 7})
    assert failed == "Einlernen beendet: Haltetest unterbrochen. Die bisherigen Werte bleiben erhalten."


def v8_packet(*, locked=False, normal=False):
    packet = bytearray(v7_packet(state=3, contacts=10, progress=10))
    packet[0] = 8
    packet[2] = 17 if normal else 29 | (32 if locked else 0)
    return bytes(packet)


def test_v8_reports_permanent_action_lock_and_rejects_inconsistent_flags():
    from custom_components.moswitch.touch_wizard import learning_message

    for locked in (False, True):
        status = protocol.touch_status(v8_packet(locked=locked))
        assert status["protocol_version"] == 8 and status["actions_locked"] is locked
        assert status["calibrated"] and status["test_mode"]
        assert ("dauerhaft" in learning_message(status)) is locked
    assert not protocol.touch_status(v8_packet(normal=True))["test_mode"]
    bad = bytearray(v8_packet(normal=True))
    bad[2] |= 32
    assert protocol.touch_status(bad) is None
    bad = bytearray(v8_packet())
    bad[2] |= 64
    assert protocol.touch_status(bad) is None
    bad = bytearray(v8_packet())
    bad[0] = 7
    bad[2] |= 32
    assert protocol.touch_status(bad) is None  # Legacy reserved flags stay strict.


@pytest.mark.asyncio
async def test_runtime_does_not_send_activation_to_permanently_locked_firmware(hass, monkeypatch):
    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.touch_status[config.entry_id] = protocol.touch_status(v8_packet(locked=True))
    runtime.listen_switch = lambda entry: object()
    normal = AsyncMock()
    monkeypatch.setattr(protocol, "normal_touch", normal)
    with pytest.raises(HomeAssistantError, match="dauerhaft"):
        await runtime.test_touch(config, False)
    normal.assert_not_awaited()
    assert config.entry_id in runtime.touch_quiet


@pytest.mark.asyncio
async def test_panel_activation_checks_fresh_lock_state_before_command(hass, monkeypatch):
    import inspect
    import time
    from unittest.mock import Mock

    from custom_components.moswitch.panel import PANEL_DATA, ws_action

    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.touch_status[config.entry_id] = protocol.touch_status(v8_packet(locked=False))
    async def query(entry):
        runtime.touch_status[entry.entry_id] = protocol.touch_status(v8_packet(locked=True))
        runtime.touch_received_at[entry.entry_id] = time.monotonic()
    runtime.query_touch = AsyncMock(side_effect=query)
    normal = AsyncMock()
    monkeypatch.setattr(protocol, "normal_touch", normal)
    conn = SimpleNamespace(send_result=Mock(), send_error=Mock())
    hass.data[PANEL_DATA]["sessions"]["s"] = {"entry":config.entry_id,"connection":conn,"owns":False}
    await inspect.unwrap(ws_action)(hass,conn,{"id":1,"entry_id":config.entry_id,"session":"s","action":"activate"})
    normal.assert_not_awaited()
    assert "dauerhaft" in conn.send_error.call_args.args[-1]


@pytest.mark.asyncio
async def test_options_flow_does_not_offer_activation_for_locked_firmware(hass):
    from custom_components.moswitch.config_flow import MoSwitchOptionsFlow

    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.touch_status[config.entry_id] = protocol.touch_status(v8_packet(locked=True))
    flow = MoSwitchOptionsFlow()
    flow.hass, flow.handler = hass, config.entry_id
    flow._touch_error = None
    runtime.test_touch = AsyncMock()
    result = await flow.async_step_touch_result()
    choices = next(iter(result["data_schema"].schema.values())).config["options"]
    assert choices == ["retry", "keep_test"]
    assert "dauerhaft" in result["description_placeholders"]["status"]
    await flow.async_step_touch_result({"action":"activate"})
    runtime.test_touch.assert_not_awaited()
