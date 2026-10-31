"""Correlate real Basic-cluster replies without consuming unsolicited reports."""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest
from zigpy.device import Device
from zigpy.exceptions import DeliveryError
from zigpy.zcl import foundation

from custom_components.moswitch import protocol


def basic_device():
    device = Device(MagicMock(), protocol.t.EUI64.convert("00:12:4b:00:01:02:03:04"), 0x1234)
    endpoint = device.add_endpoint(10)
    return device, endpoint.add_input_cluster(0)


def default_reply(cluster, tsn, command, status=foundation.Status.SUCCESS, manufacturer=protocol.MANUFACTURER,
                  direction=foundation.Direction.Server_to_Client):
    header = foundation.ZCLHeader.general(
        tsn, foundation.GeneralCommand.Default_Response, manufacturer=manufacturer, direction=direction,
    )
    response = foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Default_Response].schema(
        command_id=command, status=status,
    )
    cluster.handle_message(header, response)


def reported_status():
    payload = bytearray(76)
    struct.pack_into("<BBBBIIII", payload, 0, 10, 10, 20, 0, 21, 0, 0, 0)
    payload[26:28] = bytes([1, 4])
    struct.pack_into("<I", payload, 44, 922)
    struct.pack_into("<IBBBBHHIHH", payload, 56, 21, 2, 12, 3, 50, 100, 0, 0xFFFFFFFF, 0, 0)
    return bytes(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("manufacturer", [None, protocol.MANUFACTURER])
async def test_status_with_same_tsn_does_not_steal_command_response(manufacturer):
    device, cluster = basic_device()
    reports = []

    class Reports:
        def cluster_command(self, tsn, command, payload):
            reports.append((tsn, command, payload))

    cluster.add_listener(Reports())
    payload = reported_status()
    assert protocol.touch_status(payload)["raw_us"] == 21

    async def send(general, command, schema, **kwargs):
        assert kwargs["expect_reply"] is False and kwargs["ask_for_ack"] is True
        assert kwargs["disable_default_response"] is False
        header = foundation.ZCLHeader.cluster(
            kwargs["tsn"], 0x81, manufacturer=protocol.MANUFACTURER,
            direction=foundation.Direction.Server_to_Client,
        )
        cluster.handle_message(header, payload)
        default_reply(cluster, kwargs["tsn"], command, manufacturer=manufacturer)

    cluster.request = AsyncMock(side_effect=send)
    await protocol.calibrate_touch(device)
    assert len(reports) == 1 and reports[0][1:] == (0x81, payload)
    assert len(cluster._listeners) == 1  # Only the original telemetry listener remains.


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["command", "tsn", "manufacturer", "direction", "endpoint"])
async def test_unrelated_success_cannot_hide_the_real_rejection(wrong):
    device, cluster = basic_device()

    async def send(general, command, schema, **kwargs):
        args = {"tsn": kwargs["tsn"], "command": command}
        target = cluster
        if wrong == "command":
            args["command"] += 1
        elif wrong == "tsn":
            args["tsn"] = (args["tsn"] + 1) % 256
        elif wrong == "manufacturer":
            args["manufacturer"] = 0x1234
        elif wrong == "direction":
            args["direction"] = foundation.Direction.Client_to_Server
        else:
            target = device.add_endpoint(11).add_input_cluster(0)
        default_reply(target, **args)
        default_reply(cluster, kwargs["tsn"], command, foundation.Status.FAILURE)

    cluster.request = AsyncMock(side_effect=send)
    with pytest.raises(RuntimeError, match="rejected.*FAILURE"):
        await protocol.calibrate_touch(device)
    assert not cluster._listeners


@pytest.mark.asyncio
async def test_missing_default_response_times_out_and_cleans_up(monkeypatch):
    device, cluster = basic_device()
    cluster.request = AsyncMock()
    monkeypatch.setattr(protocol, "COMMAND_REPLY_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await protocol.query_touch(device)
    assert not cluster._listeners


@pytest.mark.asyncio
async def test_cancelled_send_removes_reply_listener():
    device, cluster = basic_device()
    entered = asyncio.Event()

    async def send(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    cluster.request = AsyncMock(side_effect=send)
    task = asyncio.create_task(protocol.query_touch(device))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not cluster._listeners


@pytest.mark.asyncio
async def test_matching_device_reply_is_valid_even_if_aps_confirmation_is_lost():
    device, cluster = basic_device()

    async def send(general, command, schema, **kwargs):
        default_reply(cluster, kwargs["tsn"], command)
        raise DeliveryError("APS confirmation lost")

    cluster.request = AsyncMock(side_effect=send)
    await protocol.query_touch(device)
    assert not cluster._listeners
