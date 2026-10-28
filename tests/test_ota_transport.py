import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.moswitch.ota_probe import Control, ProbeData, fnv, probe_reply
from custom_components.moswitch.ota_transport import ScopedOTAManager, safe_block_limit


@asynccontextmanager
async def priority(_):
    yield


def fake_device(limit=82, route=()):
    cluster = SimpleNamespace(request=AsyncMock())
    app = SimpleNamespace(_ezsp=SimpleNamespace(maximumPayloadLength=AsyncMock(return_value=(limit,))),
                          build_source_route_to=lambda **kwargs: route, request_priority=priority)
    return SimpleNamespace(application=app, find_cluster=lambda **kwargs: cluster), cluster


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "limit", "expected"), [((), 82, 64), ((1, 2), 82, 59), (None, 82, 48), ((), 68, 51)])
async def test_route_and_protocol_overhead_limits(route, limit, expected):
    device, _ = fake_device(limit, route)
    assert await safe_block_limit(device) == expected


@pytest.mark.asyncio
async def test_unavailable_radio_falls_back_to_known_block_size():
    assert await safe_block_limit(SimpleNamespace(application=object())) == 48


@pytest.mark.asyncio
async def test_page_respects_requested_size_spacing_and_per_device_ack(monkeypatch):
    device, cluster = fake_device()
    payload = bytes(range(140))
    header = SimpleNamespace(manufacturer_id=0xFFF1, image_type=1, file_version=52)
    image = SimpleNamespace(firmware=SimpleNamespace(header=header, serialize=lambda: payload))
    job = {}
    progress = []
    manager = ScopedOTAManager(device, image, lambda n, total: progress.append(n), job, 64, False)
    sleep = AsyncMock()
    monkeypatch.setattr('custom_components.moswitch.ota_transport.asyncio.sleep', sleep)
    command = SimpleNamespace(manufacturer_code=0xFFF1, image_type=1, file_version=52,
                              file_offset=0, maximum_data_size=64, page_size=128, response_spacing=5)
    await manager._image_page_req(SimpleNamespace(tsn=1), command)
    assert [call.kwargs['file_offset'] for call in cluster.request.call_args_list] == [0, 64]
    assert b''.join(call.kwargs['image_data'] for call in cluster.request.call_args_list) == payload[:128]
    assert all(call.kwargs['ask_for_ack'] is False for call in cluster.request.call_args_list)
    assert [call.args for call in sleep.call_args_list] == [(.005,), (.005,)]
    assert progress == [64, 128]
    assert job['server_packets'] == 2 and not job['server_send_errors']
    # A wrong identity never sends; recovery returns exactly the requested offset.
    command.file_version = 99
    await manager._image_page_req(SimpleNamespace(tsn=2), command)
    assert cluster.request.await_count == 2
    command.file_version = 52
    command.file_offset = 64
    await manager._image_block_req(SimpleNamespace(tsn=3), command)
    assert cluster.request.call_args.kwargs['file_offset'] == 64
    assert cluster.request.call_args.kwargs['tsn'] == 3
    assert job['server_recovery_blocks'] == 1
    manager._stall_timer.cancel()
    manager._upgrade_end_future.cancel()


@pytest.mark.asyncio
async def test_send_failure_is_counted_and_page_is_not_reported_as_delivered():
    device, cluster = fake_device()
    cluster.request.side_effect = TimeoutError()
    header = SimpleNamespace(manufacturer_id=0xFFF1, image_type=1, file_version=52)
    image = SimpleNamespace(firmware=SimpleNamespace(header=header, serialize=lambda: bytes(128)))
    job = {}
    progress = []
    manager = ScopedOTAManager(device, image, lambda *args: progress.append(args), job, 48, True)
    command = SimpleNamespace(manufacturer_code=0xFFF1, image_type=1, file_version=52,
                              file_offset=0, maximum_data_size=48, page_size=128, response_spacing=0)
    await manager._image_page_req(SimpleNamespace(tsn=1), command)
    assert job['server_send_errors'] == 1 and not progress
    assert cluster.request.await_count == 1
    manager._stall_timer.cancel()
    manager._upgrade_end_future.cancel()


def test_probe_wire_size_and_hash():
    assert len(Control(2, 123, 48, 0, 0, 8192, 0).serialize()) == 14
    data = ProbeData(123, 64, b'abc').serialize()
    assert data == b'\x7b\x00\x40\x00\x00\x00abc'
    assert fnv(b'hello') == 0x4F9F2CAB


@pytest.mark.asyncio
async def test_probe_response_requires_matching_device_operation_nonce():
    import struct
    future = asyncio.get_running_loop().create_future()
    runtime = SimpleNamespace(ota_probe_waiters={'one': (3, 123, future)})
    data = struct.pack('<BBHBBBB8I', 1, 3, 123, 0, 48, 0, 0, 8192, 9, 0, 10, 0, 0, 0, 0)
    probe_reply(runtime, 'other', data)
    probe_reply(runtime, 'one', data[:-1])
    assert not future.done()
    probe_reply(runtime, 'one', data)
    assert future.result()['received'] == 8192 and future.result()['hash'] == 9


@pytest.mark.asyncio
async def test_manager_exit_cancels_and_drains_active_page():
    device, cluster = fake_device()
    entered = asyncio.Event()
    async def wait_forever(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    cluster.request.side_effect = wait_forever
    header = SimpleNamespace(manufacturer_id=0xFFF1, image_type=1, file_version=52)
    image = SimpleNamespace(firmware=SimpleNamespace(header=header, serialize=lambda: bytes(128)))
    manager = ScopedOTAManager(device, image, None, {}, 48, True)
    command = SimpleNamespace(manufacturer_code=0xFFF1, image_type=1, file_version=52,
                              file_offset=0, maximum_data_size=48, page_size=128, response_spacing=0)
    page = asyncio.create_task(manager._image_page_req(SimpleNamespace(tsn=1), command))
    await entered.wait()
    manager.__exit__(None, None, None)
    await manager.drain()
    assert page.cancelled() and not manager._handlers
    await manager._image_page_req(SimpleNamespace(tsn=1), command)
    assert cluster.request.await_count == 1
    manager._upgrade_end_future.cancel()


@pytest.mark.asyncio
async def test_final_ram_result_preserves_actual_sender_block(monkeypatch):
    from custom_components.moswitch import ota_probe
    device, cluster = fake_device()
    device.ieee = 'one'
    device.ota_in_progress = False
    device.endpoints = {10: SimpleNamespace(in_clusters={0: cluster})}
    runtime = SimpleNamespace(ota_active_entries=set(), touch_status={'one': {'test_mode': True, 'state': 10}},
                              listen_switch=lambda entry: device)
    entry = SimpleNamespace(entry_id='one', runtime_data=runtime)
    async def control(entry, op, **kwargs):
        if op == 2:
            return {'result': 0}
        return {'result': 0, 'block': 48, 'spacing': 5, 'received': 1024,
                'hash': kwargs['expected'], 'ignored': 0, 'active': False}
    monkeypatch.setattr(ota_probe, 'control', control)
    monkeypatch.setattr(ota_probe.asyncio, 'sleep', AsyncMock())
    job = {}
    await ota_probe.benchmark(entry, job, 'large_mac', 1024)
    assert job['state'] == 'complete' and job['verified']
    assert job['block'] == 64 and job['device_profile_block'] == 48
    assert job['spacing_ms'] == 0 and job['device_profile_spacing_ms'] == 5
    assert cluster.request.await_count == 16
    assert not runtime.ota_active_entries
