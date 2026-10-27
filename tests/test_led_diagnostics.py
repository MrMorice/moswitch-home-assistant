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


def wire_packet(nonce=1, high=200, transitions=300, max_gap=78, core_hz=78000000):
    base = struct.pack("<BBBBIIHBB", 2, 7, 32, 0, 3250000, 1, nonce, 7, 0)
    details = struct.pack("<BBBB9I", 1, 4, 4, 16, 39000000, 2000, high,
                          transitions, max_gap, 72000, 25000 if high else 0xFFFFFFFF,
                          0x20002, core_hz)
    return base + details


def test_wire_probe_distinguishes_pin_activity_from_spi_completion():
    status = parse_ring(wire_packet())["wire_probe"]
    assert status["transfer_complete"] and status["output_activity_observed"] is True
    assert status["coverage_sufficient"] and status["gpio_mode"] == 4
    assert status["routing_matches_expected"]
    assert status["tx_route"] == 0x20002
    # Successful SPI completion alone must not count as a waveform at the pin.
    low = parse_ring(wire_packet(high=0, transitions=0))["wire_probe"]
    assert low["transfer_complete"] and low["output_activity_observed"] is False
    assert low["first_high_us"] is None
    high = parse_ring(wire_packet(high=2000, transitions=0))["wire_probe"]
    assert high["output_activity_observed"] is False
    # A scheduling gap which could hide the payload makes a negative inconclusive.
    gap = parse_ring(wire_packet(high=0, transitions=0, max_gap=78000))["wire_probe"]
    assert gap["output_activity_observed"] is None and not gap["coverage_sufficient"]
    for bad in (wire_packet()[:-1], wire_packet() + b"x", wire_packet(core_hz=0),
                wire_packet(high=2001), wire_packet(transitions=2001)):
        assert parse_ring(bad) is None


def edge_packet(edges=384, levels=252, faults=0, timer_hz=39000000, variant=0, nonce=1):
    base = struct.pack("<BBBBIIHBB", 3, 7, 32, 0, 3250000, 1, nonce, 7, 0)
    return base + struct.pack("<BBBB9I", variant + 1, levels, 4, 16, 39000000, edges, (edges + 1) // 2,
                              12 if edges else 0, 36 if edges else 0,
                              24 if edges else 0, 48 if edges else 0, faults, timer_hz)


def test_hardware_edge_capture_requires_working_gpio_self_test_for_negative():
    p = parse_ring(edge_packet())["wire_probe"]
    assert p["hardware_capture"] and p["coverage_sufficient"]
    assert p["edge_count"] == 384 and p["output_activity_observed"] is True
    assert p["payload_matches_expected"] and p["pulse_timing_within_profile"]
    assert p["min_high_us"] == .308 and p["max_high_us"] == .923
    assert parse_ring(edge_packet(variant=2))["wire_probe"]["transport_variant"] == 2
    low = parse_ring(edge_packet(edges=0, levels=60))["wire_probe"]
    assert low["coverage_sufficient"] and low["output_activity_observed"] is False
    assert low["min_high_us"] is None and not low["payload_matches_expected"]
    for flags, faults in ((4, 8), (60, 16), (60, 32), (60, 64)):
        p = parse_ring(edge_packet(edges=0, levels=flags, faults=faults))["wire_probe"]
        assert not p["coverage_sufficient"] and p["output_activity_observed"] is None
    for bad in (edge_packet(edges=513), edge_packet(timer_hz=0)):
        assert parse_ring(bad) is None


@pytest.mark.asyncio
async def test_wire_transport_variant_is_explicit_and_device_confirmed():
    cluster = SimpleNamespace()
    device = SimpleNamespace(ieee="one", endpoints={ENDPOINT: SimpleNamespace(in_clusters={0: cluster})})
    runtime = SimpleNamespace(ring_waiters={}, ring_locks={}, listen_switch=lambda entry: device)
    entry = SimpleNamespace(entry_id="one", runtime_data=runtime)

    async def request(*args, **kwargs):
        assert args[3] == 7
        ring_reply(runtime, "one", edge_packet(variant=args[4], nonce=args[5]))

    cluster.request = request
    assert (await ring_action(entry, "wire_check", 2))["wire_probe"]["transport_variant"] == 2
    for bad in (-1, 3, True):
        with pytest.raises(ValueError):
            await ring_action(entry, "wire_check", bad)


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
async def test_wire_reply_uses_device_nonce_and_operation():
    future = asyncio.get_running_loop().create_future()
    runtime = SimpleNamespace(ring_waiters={"one": (5, 7, future)})
    ring_reply(runtime, "other", wire_packet(5))
    ring_reply(runtime, "one", wire_packet(4))
    ring_reply(runtime, "one", packet(5, 6))
    assert not future.done()
    ring_reply(runtime, "one", wire_packet(5))
    assert future.result()["wire_probe"]["output_activity_observed"] is True


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
        if op == OPS["wire_check"]:
            ring_reply(runtime, "one", wire_packet(nonce, high=0, transitions=0))
            return
        ring_reply(runtime, "one", packet(nonce, op, flags=flags, brightness=arg if op == OPS["brightness"] else 32, result=result))

    cluster.request = request
    with pytest.raises(ValueError):
        await ring_action(entry, "enable")
    assert not runtime.ring_waiters
    assert (await ring_action(entry, "confirm_supply"))["supply_confirmed"]
    assert (await ring_action(entry, "enable"))["enabled"]
    assert (await ring_action(entry, "test"))["test_active"]
    assert (await ring_action(entry, "brightness", 12))["brightness"] == 12
    assert (await ring_action(entry, "wire_check"))["wire_probe"]["output_activity_observed"] is False
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
