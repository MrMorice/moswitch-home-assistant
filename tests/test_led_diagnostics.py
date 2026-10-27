import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import Unauthorized

from custom_components.moswitch.const import ENDPOINT
from custom_components.moswitch.led_diagnostics import (
    OPS,
    parse_ring,
    ring_action,
    ring_reply,
    ws_ring,
)
from custom_components.moswitch.runtime import GestureListener


def packet(nonce=1, operation=6, flags=0, brightness=32, error=0, result=0):
    return struct.pack("<BBBBIIHBB", 1, flags, brightness, error, 3250000, 1, nonce, operation, result)


def test_ring_packet_and_admin():
    assert parse_ring(packet(flags=15))["test_active"]
    for data in (None, b"", packet()[:-1], packet(flags=16), packet(brightness=65), packet(operation=7), packet(result=2)):
        assert parse_ring(data) is None
    with pytest.raises(Unauthorized):
        ws_ring(None, SimpleNamespace(user=SimpleNamespace(is_admin=False)), {})


@pytest.mark.asyncio
async def test_ring_reply_matches_device_nonce_operation():
    future = asyncio.get_running_loop().create_future()
    runtime = SimpleNamespace(ring_waiters={"one": (5, 1, future)})
    listener = GestureListener(runtime, "one")
    listener.cluster_command(1, 0x83, packet(4, 1))
    listener.cluster_command(1, 0x83, packet(5, 6))
    GestureListener(runtime, "other").cluster_command(1, 0x83, packet(5, 1))
    assert not future.done()
    listener.cluster_command(1, 0x83, packet(5, 1, flags=7))
    assert future.result()["enabled"]
    listener.cluster_command(1, 0x83, packet(5, 1, flags=7))


@pytest.mark.asyncio
async def test_commands_supply_and_disable_confirmation():
    cluster = SimpleNamespace()
    device = SimpleNamespace(ieee="one", endpoints={ENDPOINT: SimpleNamespace(in_clusters={0: cluster})})
    runtime = SimpleNamespace(ring_waiters={}, ring_locks={}, listen_switch=lambda entry: device)
    entry = SimpleNamespace(entry_id="entry", runtime_data=runtime)
    powered = False
    calls = []

    async def request(*args, **kwargs):
        nonlocal powered
        _, command_id, _, op, arg, nonce = args
        assert command_id == 0x4D and kwargs["expect_reply"] is False
        calls.append(op)
        result = 0
        flags = 1 if powered else 0
        if op == OPS["confirm_supply"]:
            assert arg == 0xA5
            powered = True
            flags = 1
        if op == OPS["enable"]:
            if not powered:
                result = 1
            else:
                flags = 7
        if op == OPS["disable"]:
            flags = 5  # Black frame is still in flight; handler must query completion.
        if op == OPS["test"]:
            flags = 15 if powered else 0
            result = 0 if powered else 1
        if op == OPS["forget_supply"]:
            powered = False
            flags = 0
        ring_reply(runtime, "one", packet(nonce, op, flags=flags, brightness=arg if op == OPS["brightness"] else 32, result=result))

    cluster.request = request
    with pytest.raises(ValueError):
        await ring_action(entry, "enable")
    assert not runtime.ring_waiters
    assert (await ring_action(entry, "confirm_supply"))["supply_confirmed"]
    assert (await ring_action(entry, "enable"))["enabled"]
    assert (await ring_action(entry, "test"))["test_active"]
    assert (await ring_action(entry, "brightness", 12))["brightness"] == 12
    assert not (await ring_action(entry, "disable"))["started"]
    assert calls[-2:] == [OPS["disable"], OPS["status"]]
    assert not (await ring_action(entry, "forget_supply"))["supply_confirmed"]
    cluster.request = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await ring_action(entry, "status")
    assert not runtime.ring_waiters
    for value in (-1, 65, True, None):
        with pytest.raises(ValueError):
            await ring_action(entry, "brightness", value)
