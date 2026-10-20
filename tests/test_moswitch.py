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
