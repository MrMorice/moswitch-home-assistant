import asyncio
import base64
import hashlib
import struct
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from zigpy.zcl import foundation

from custom_components.moswitch import candidate_ota as candidate


@pytest.fixture(autouse=True)
def legacy_transfer_mock(monkeypatch):
    async def transfer(device, image, progress_callback, job, profile):
        return await device.update_firmware(image=image, progress_callback=progress_callback)
    monkeypatch.setattr(candidate, "transfer_image", transfer)
    monkeypatch.setattr(candidate, "control", AsyncMock(return_value={"result": 0}))
    monkeypatch.setattr(candidate, "ring_action", AsyncMock(return_value={"error": 0}))


def ota_file(version=candidate.VERSION, manufacturer=0xFFF1):
    payload = b"test-gbl"
    header = struct.pack("<IHHHHHIH32sI", 0x0BEEF11E, 0x0100, 56, 0,
                         manufacturer, 1, version, 2, b"MoSwitch v1", 62 + len(payload))
    return header + struct.pack("<HI", 0, len(payload)) + payload


def allow(monkeypatch, data):
    monkeypatch.setattr(candidate, "SIZE", len(data))
    monkeypatch.setattr(candidate, "SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(candidate, "CANDIDATES", {candidate.VERSION: {"source": candidate.SOURCE_VERSION, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}})


def test_candidate_identity_and_integrity(monkeypatch):
    data = ota_file()
    allow(monkeypatch, data)
    image = candidate.decode_candidate(base64.b64encode(data).decode())
    assert image.version == candidate.VERSION
    assert image.firmware.serialize() == data
    for bad in ("!", "a" * 500001, base64.b64encode(data[:-1]).decode()):
        with pytest.raises(ValueError):
            candidate.decode_candidate(bad)
    # Even an allowlisted byte stream must have the expected OTA identity.
    for bad in (ota_file(version=1), ota_file(manufacturer=1), data + b"trailing"):
        allow(monkeypatch, bad)
        with pytest.raises(ValueError):
            candidate.decode_candidate(base64.b64encode(bad).decode())


def setup_entry():
    cluster = SimpleNamespace(read_attributes=AsyncMock(return_value=({"current_file_version": candidate.SOURCE_VERSION}, {})))
    device = SimpleNamespace(ieee="00:11:22:33:44:55:66:77", ota_in_progress=False,
                             find_cluster=lambda **kwargs: cluster,
                             update_firmware=AsyncMock(return_value=foundation.Status.SUCCESS))
    runtime = SimpleNamespace(ota_active_entries=set(), touch_quiet=set(), touch_received_at={"one": 0}, touch_status={},
                              listen_switch=lambda entry: device)

    async def quiet(entry, enabled):
        assert enabled
        runtime.touch_received_at[entry.entry_id] = time.monotonic()
        runtime.touch_status[entry.entry_id] = {"test_mode": True, "state": 10}

    async def query(entry):
        runtime.touch_received_at[entry.entry_id] = time.monotonic()
        runtime.touch_status[entry.entry_id] = {"test_mode": False, "state": 3}
        runtime.touch_quiet.discard(entry.entry_id)  # Same as a real normal-mode report.

    runtime.query_touch = AsyncMock(side_effect=query)
    runtime.test_touch = AsyncMock(side_effect=quiet)
    return SimpleNamespace(entry_id="one", runtime_data=runtime), device, cluster


@pytest.mark.asyncio
async def test_target_update_requires_quiet_and_preserves_guard():
    entry, device, _ = setup_entry()
    job = {}

    async def update(**kwargs):
        assert entry.runtime_data.touch_status["one"]["test_mode"]
        assert kwargs["image"].version == candidate.VERSION
        kwargs["progress_callback"](10, 20, 50)
        assert job["percent"] == 50
        return foundation.Status.SUCCESS

    device.update_firmware.side_effect = update
    await candidate.install(entry, SimpleNamespace(version=candidate.VERSION), job)
    assert job["state"] == "complete" and job["percent"] == 100
    assert "one" not in entry.runtime_data.touch_quiet
    assert job["startup_status_confirmed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("busy", "wrong_version", "quiet_failed", "ota_failed"))
async def test_failed_upload_stays_quiet(failure):
    entry, device, cluster = setup_entry()
    if failure == "busy":
        device.ota_in_progress = True
    elif failure == "wrong_version":
        cluster.read_attributes.return_value = ({"current_file_version": 1}, {})
    elif failure == "quiet_failed":
        entry.runtime_data.test_touch.side_effect = TimeoutError("lost quiet confirmation")
    else:
        device.update_firmware.return_value = foundation.Status.FAILURE
    job = {}
    await candidate.install(entry, SimpleNamespace(version=candidate.VERSION), job)
    assert job["state"] == "failed" and "error" in job
    assert "one" in entry.runtime_data.touch_quiet
    if failure != "ota_failed":
        device.update_firmware.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_does_not_activate():
    entry, device, _ = setup_entry()
    device.update_firmware.side_effect = asyncio.CancelledError()
    job = {}
    with pytest.raises(asyncio.CancelledError):
        await candidate.install(entry, SimpleNamespace(version=candidate.VERSION), job)
    assert job["state"] == "interrupted" and "one" in entry.runtime_data.touch_quiet


def test_upload_is_admin_only_and_device_scoped(monkeypatch):
    import inspect
    from unittest.mock import Mock

    from homeassistant.exceptions import Unauthorized

    from custom_components.moswitch import panel

    for handler in (candidate.ws_install, candidate.ws_status):
        with pytest.raises(Unauthorized):
            handler(None, SimpleNamespace(user=SimpleNamespace(is_admin=False)), {})
    entries = {key: SimpleNamespace(entry_id=key, runtime_data=SimpleNamespace(ota_active_entries=set())) for key in ("one", "two")}
    monkeypatch.setattr(panel, "panel_entry", lambda hass, key: entries[key])
    monkeypatch.setattr(candidate, "decode_candidate", lambda data: SimpleNamespace(version=candidate.VERSION))
    tasks = []

    def create(coroutine, name):
        tasks.append(name)
        coroutine.close()  # Reservation and routing test; transfer is tested separately.

    hass = SimpleNamespace(data={candidate.DATA: {}}, async_create_background_task=create)
    conn = SimpleNamespace(send_result=Mock(), send_error=Mock())
    install = inspect.unwrap(candidate.ws_install)
    install(hass, conn, {"id": 1, "entry_id": "one", "image": "pinned"})
    assert hass.data[candidate.DATA]["one"]["state"] == "preparing"
    assert "two" not in hass.data[candidate.DATA]
    install(hass, conn, {"id": 2, "entry_id": "one", "image": "pinned"})
    assert len(tasks) == 1
    assert conn.send_error.call_args.args[1] == "candidate_rejected"
    inspect.unwrap(candidate.ws_status)(hass, conn, {"id": 3, "entry_id": "two"})
    assert conn.send_result.call_args.args == (3, {"state": "idle"})


@pytest.mark.asyncio
async def test_post_update_query_failure_keeps_action_guard():
    entry, device, _ = setup_entry()
    entry.runtime_data.query_touch.side_effect = TimeoutError("no fresh startup report")
    job = {}
    await candidate.install(entry, SimpleNamespace(version=candidate.VERSION), job)
    assert job["state"] == "complete" and not job["startup_status_confirmed"]
    assert "one" in entry.runtime_data.touch_quiet


@pytest.mark.asyncio
async def test_pinned_59_recovery_requires_ack_and_nonce_matched_reply():
    entry, device, _ = setup_entry()
    entry.runtime_data.test_touch = AsyncMock()  # ACK succeeds, broken build has no fresh touch report.
    job = {}
    await candidate.install(entry, SimpleNamespace(version=candidate.VERSION), job)
    candidate.ring_action.assert_awaited_once_with(entry, "status")
    assert job["state"] == "complete" and "pinned59-to61" in job["quiet_confirmation"]


@pytest.mark.asyncio
async def test_pinned_recovery_refuses_missing_second_physical_reply():
    entry, device, _ = setup_entry()
    candidate.ring_action.side_effect = TimeoutError("no nonce reply")
    job = {}
    await candidate.install(entry, SimpleNamespace(version=candidate.VERSION), job)
    assert job["state"] == "failed"
    device.update_firmware.assert_not_awaited()
