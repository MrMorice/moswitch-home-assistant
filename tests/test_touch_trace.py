import asyncio
import struct
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.moswitch import touch_trace
from custom_components.moswitch.runtime import GestureListener


def packet(offset=0, total=1, generation=1):
    count = min(4, total - offset)
    return struct.pack("<BBHHHI", 1, count, offset, total, generation, total) + b"".join(
        struct.pack("<IIIHBB", 100, 110, 5, 65535, 7, 2) for _ in range(count))


def test_trace_packet_bounds_and_records():
    p = touch_trace.parse_trace(packet())
    assert p["total_records"] == 1 and p["generation"] == 1
    assert p["records"][0] == {"at_ms": 100, "end_ms": 110, "repeats": 5, "raw_us": None, "state": 7, "flags": 2}
    assert touch_trace.parse_trace(packet(total=4096))["total_records"] == 4096
    for bad in (None, b"", packet()[:-1], packet() + b"x", packet(total=4097), b"\x02" + packet()[1:]):
        assert touch_trace.parse_trace(bad) is None


@pytest.mark.asyncio
async def test_trace_device_and_offset_routing():
    future = asyncio.get_running_loop().create_future()
    runtime = SimpleNamespace(touch_trace_waiters={"one": (4, future)})
    listener = GestureListener(runtime, "one")
    listener.cluster_command(1, 0x82, packet(0, 5))
    GestureListener(runtime, "other").cluster_command(1, 0x82, packet(4, 5))
    assert not future.done()
    listener.cluster_command(1, 0x82, packet(4, 5))
    listener.cluster_command(1, 0x82, packet(4, 5))  # Duplicate must not resolve twice.
    assert future.result()["offset"] == 4


@pytest.mark.asyncio
async def test_read_chunk_safety_and_cleanup(monkeypatch):
    device = SimpleNamespace(ieee="one")
    runtime = SimpleNamespace(touch_trace_waiters={}, touch_trace_locks={},
                              touch_status={"entry": {"state": 8, "test_mode": True}},
                              touch_received_at={"entry": time.monotonic()},
                              listen_switch=lambda entry: device)
    entry = SimpleNamespace(entry_id="entry", runtime_data=runtime)

    async def request(*args):
        assert args[0] is device and args[1] == 0x4C
        touch_trace.trace_reply(runtime, "one", packet())

    monkeypatch.setattr(touch_trace.protocol, "request", request)
    assert (await touch_trace.read_chunk(entry, 0))["total_records"] == 1
    assert not runtime.touch_trace_waiters
    for state, quiet in ((7, True), (3, False)):
        runtime.touch_status["entry"] = {"state": state, "test_mode": quiet}
        with pytest.raises(ValueError):
            await touch_trace.read_chunk(entry, 0)
    runtime.touch_status["entry"] = {"state": 8, "test_mode": True}
    monkeypatch.setattr(touch_trace.protocol, "request", AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError):
        await touch_trace.read_chunk(entry, 0)
    assert not runtime.touch_trace_waiters


def test_trace_requires_admin():
    from homeassistant.exceptions import Unauthorized

    with pytest.raises(Unauthorized):
        touch_trace.ws_trace(None, SimpleNamespace(user=SimpleNamespace(is_admin=False)), {})
