"""Shipping surface, scoped migration and confirmed status-light preferences."""

import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import entity_registry as er
from test_moswitch import (
    IEEE,
    device,
    panel_config,
)

from custom_components.moswitch.const import DOMAIN
from custom_components.moswitch.entity_layout import migrate_entity_layout
from custom_components.moswitch.indicator import indicator_action, parse_ring, ring_reply
from custom_components.moswitch.number import StatusLightBrightness
from custom_components.moswitch.sensor import TouchStatus


def test_retired_payloads_are_not_status_light_messages():
    packet = struct.pack('<BBBBIIHBB', 1, 7, 32, 0, 3250000, 2, 4, 6, 0)
    assert parse_ring(packet)['enabled']
    assert 'frames' not in parse_ring(packet)
    assert parse_ring(packet + bytes(40)) is None
    for op in (2, 7, 8, 9, 10):
        bad = bytearray(packet)
        bad[14] = op
        assert parse_ring(bad) is None


@pytest.mark.asyncio
async def test_registry_cleanup_only_removes_owned_retired_entities(hass):
    config = panel_config(hass, device(hass))
    registry = er.async_get(hass)
    def create(platform, suffix, prefix=IEEE):
        return registry.async_get_or_create('sensor', platform, f'{prefix}_{suffix}',
            config_entry=config, device_id=config.data['device_id'])
    removed = [create(DOMAIN, suffix) for suffix in ('configuration', 'touch_measurement_raw_us',
        'touch_parameter_baseline_us', 'calibrate_touch', 'touch_test_mode', 'touch_contact')]
    keep = [create('zha', 'touch_contact'), create(DOMAIN, 'touch_calibration'),
            create(DOMAIN, 'user_helper'), create(DOMAIN, 'touch_contact', prefix='another-device')]
    migrate_entity_layout(hass, config)
    assert all(registry.async_get(e.entity_id) is None for e in removed)
    assert all(registry.async_get(e.entity_id) is not None for e in keep)
    assert config.data['ui_layout_version'] == 3
    migrate_entity_layout(hass, config)
    assert all(registry.async_get(e.entity_id) is not None for e in keep)


@pytest.mark.asyncio
async def test_status_does_not_expose_raw_data_and_brightness_uses_saved_scale(hass, monkeypatch):
    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    runtime.touch_status[config.entry_id] = {'state': 3, 'calibrated': True, 'raw_us': 20}
    status = TouchStatus(config)
    assert status.native_value == 'Bereit'
    assert status.extra_state_attributes is None
    runtime.indicator_status[config.entry_id] = {'brightness': 32}
    number = StatusLightBrightness(config)
    assert number.native_value == 50
    call = AsyncMock()
    monkeypatch.setattr('custom_components.moswitch.number.indicator_action', call)
    await number.async_set_native_value(100)
    call.assert_awaited_once_with(config, 'brightness', 64)


@pytest.mark.asyncio
async def test_status_light_settings_wait_for_matching_device_reply(hass):
    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    commands = []
    async def request(_general, _id, _schema, op, value, nonce, **kwargs):
        assert kwargs['disable_default_response']
        commands.append((op, value))
        # A different nonce must not complete the request.
        ring_reply(runtime, IEEE, struct.pack('<BBBBIIHBB', 1, 7, 32, 0, 3250000, 1, nonce ^ 1, op, 0))
        assert not runtime.ring_waiters[IEEE][2].done()
        ring_reply(runtime, IEEE, struct.pack('<BBBBIIHBB', 1, 7, 32, 0, 3250000, 2, nonce, op, 0))
    dev = SimpleNamespace(ieee=IEEE, endpoints={10: SimpleNamespace(in_clusters={0: SimpleNamespace(request=request)})})
    runtime.listen_switch = lambda e: dev
    await indicator_action(config, 'enable')
    assert commands == [(3, 0xA5), (1, 0)]
    assert runtime.indicator_status[config.entry_id]['enabled']
    assert not runtime.ring_waiters
    for op in ('test', 'wire_check', 'boot_status', 'update_preview'):
        with pytest.raises(ValueError):
            await indicator_action(config, op)
    assert len(commands) == 2


@pytest.mark.asyncio
async def test_normal_status_reports_real_update_without_extra_device_queries(hass):
    config = panel_config(hass, device(hass))
    config.runtime_data.get_switch = lambda e: SimpleNamespace(ota_in_progress=True)
    config.runtime_data.listen_switch = lambda e: pytest.fail("No polling during OTA")
    status = TouchStatus(config)
    assert status.available
    assert status.native_value == "Firmwareupdate"
    await status.async_update()
