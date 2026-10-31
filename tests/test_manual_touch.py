"""Adjust real protocol tuples during a confirmed quiet manual session."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from test_moswitch import IEEE, device, panel_config, v8_packet

from custom_components.moswitch import protocol
from custom_components.moswitch.manual_touch import (
    apply_values,
    initial_values,
    pause_for_adjustment,
)
from custom_components.moswitch.panel import PANEL_DATA, snapshot, ws_action


def rig(hass, monkeypatch):
    config = panel_config(hass, device(hass))
    runtime = config.runtime_data
    current = protocol.touch_status(v8_packet())
    current.update(state=7, calibrated=False, pressed=False, baseline_us=0,
                   press_delta_us=0, release_delta_us=0, test_mode=True,
                   frontend_active=True, learning_baseline_us=19, raw_us=20, filtered_us=20,
                   learning_taps=6, detected_contacts=6, idle_noise_us=1)
    calls = []
    def report(**changes):
        current.update(changes)
        runtime.touch_report(IEEE, dict(current))
    async def query(_):
        calls.append('query')
        report()
    async def prepare(_):
        calls.append('prepare')
        report(state=9, test_mode=True)
    async def cancel(_):
        calls.append('cancel')
        report(state=8, test_mode=True, pressed=False)
    async def profile(_device, baseline, press, release):
        assert current['test_mode'] and current['state'] == 8 and not current['pressed']
        calls.append('profile')
        report(state=3, calibrated=True, baseline_us=baseline, press_delta_us=press, release_delta_us=release)
    async def tuning(_device, *values):
        assert current['test_mode'] and current['state'] == 3
        calls.append('tuning')
        report(**dict(zip(protocol.TUNING_FIELDS, values, strict=True)))
    runtime.query_touch = query
    runtime.calibrate_touch = prepare
    runtime.cancel_touch = cancel
    runtime.resume_touch = AsyncMock()
    runtime.get_switch = lambda _: SimpleNamespace(ota_in_progress=False)
    runtime.listen_switch = lambda _: object()
    monkeypatch.setattr(protocol, 'set_touch_parameters', profile)
    monkeypatch.setattr(protocol, 'set_touch_tuning', tuning)
    report()
    return config, current, calls, report


@pytest.mark.asyncio
async def test_begin_stops_automatic_learning_without_writing_or_activating(hass, monkeypatch):
    config, current, calls, _ = rig(hass, monkeypatch)
    await pause_for_adjustment(hass, config)
    assert calls == ['query', 'cancel']
    assert current['state'] == 8 and current['test_mode'] and not current['calibrated']
    values = initial_values(current)
    assert values['baseline_us'] == 20 and values['press_delta_us'] == 4
    config.runtime_data.resume_touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_initial_profile_and_filter_are_confirmed_while_actions_stay_blocked(hass, monkeypatch):
    config, current, calls, _ = rig(hass, monkeypatch)
    await pause_for_adjustment(hass, config)
    values = initial_values(current)
    values.update(press_delta_us=13, release_delta_us=6, release_debounce_ms=20)
    await apply_values(hass, config, values)
    assert calls == ['query', 'cancel', 'query', 'prepare', 'cancel', 'profile', 'tuning']
    assert all(current[k] == value for k, value in values.items())
    assert current['state'] == 3 and current['calibrated'] and current['test_mode']
    assert config.entry_id in config.runtime_data.touch_quiet
    config.runtime_data.resume_touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_manual_values_do_not_interrupt_learning(hass, monkeypatch):
    config, current, calls, _ = rig(hass, monkeypatch)
    values = initial_values(current)
    values['release_delta_us'] = values['press_delta_us']
    with pytest.raises(ValueError):
        await apply_values(hass, config, values)
    assert not calls and current['state'] == 7


@pytest.mark.asyncio
async def test_manual_change_resets_gate_when_finger_was_held(hass, monkeypatch):
    config, current, calls, report = rig(hass, monkeypatch)
    report(state=3, calibrated=True, baseline_us=20, press_delta_us=13, release_delta_us=6, pressed=True)
    await apply_values(hass, config, initial_values(current))
    assert calls[:3] == ['query', 'prepare', 'cancel'] and calls[3:] == ['profile', 'tuning']
    assert not current['pressed'] and current['test_mode']


@pytest.mark.asyncio
async def test_partial_write_is_reported_and_never_activates(hass, monkeypatch):
    config, current, _, _ = rig(hass, monkeypatch)
    async def fail(*args):
        raise RuntimeError('radio failure')
    monkeypatch.setattr(protocol, 'set_touch_tuning', fail)
    with pytest.raises(HomeAssistantError, match='Filter noch nicht bestätigt'):
        await apply_values(hass, config, initial_values(current))
    assert current['calibrated'] and current['test_mode']
    config.runtime_data.resume_touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_session_ownership_and_closing_keep_actions_paused(hass, monkeypatch):
    config, current, _, _ = rig(hass, monkeypatch)
    conn = SimpleNamespace(send_result=Mock(), send_error=Mock())
    sessions = hass.data[PANEL_DATA]['sessions']
    sessions['s'] = {'entry':config.entry_id,'connection':conn,'owns':False}
    action = inspect.unwrap(ws_action)
    await action(hass, conn, {'id':1,'entry_id':config.entry_id,'session':'s','action':'manual'})
    conn.send_error.assert_not_called()
    assert conn.send_result.call_args.args[1]['manual']
    assert sessions['s']['owns'] and sessions['s']['manual']
    other = SimpleNamespace(send_result=Mock(), send_error=Mock())
    sessions['other'] = {'entry':config.entry_id,'connection':other,'owns':False}
    await action(hass, other, {'id':2,'entry_id':config.entry_id,'session':'other','action':'manual'})
    other.send_error.assert_called_once()
    await action(hass, conn, {'id':3,'entry_id':config.entry_id,'session':'s','action':'manual_close'})
    assert not sessions['s']['owns'] and not sessions['s']['manual'] and current['test_mode']
    config.runtime_data.resume_touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_uses_manual_draft_bounds_without_claiming_device_calibrated(hass, monkeypatch):
    config, current, _, _ = rig(hass, monkeypatch)
    values=initial_values(current)
    result=snapshot(hass,config,manual_values=values)
    assert result['manual'] and result['manual_values']==values
    assert not result['touch']['calibrated']
    release=next(p for p in result['parameters'] if p['key']=='release_delta_us')
    assert release['max']==values['press_delta_us']-1


@pytest.mark.asyncio
async def test_matching_device_echo_confirms_values_even_if_radio_ack_is_lost(hass, monkeypatch):
    config, current, _, report = rig(hass, monkeypatch)
    async def acknowledged_by_report(_device, baseline, press, release):
        report(state=3, calibrated=True, baseline_us=baseline,
               press_delta_us=press, release_delta_us=release)
        raise TimeoutError('lost radio ACK')
    monkeypatch.setattr(protocol, 'set_touch_parameters', acknowledged_by_report)
    values = initial_values(current)
    await apply_values(hass, config, values)
    assert all(current[k] == v for k, v in values.items()) and current['test_mode']
    config.runtime_data.resume_touch.assert_not_awaited()
    assert not config.runtime_data.touch_parameter_waiters


@pytest.mark.asyncio
async def test_unmatched_report_does_not_confirm_lost_radio_ack(hass, monkeypatch):
    config, current, _, report = rig(hass, monkeypatch)
    async def wrong_echo(_device, baseline, press, release):
        report(state=3, calibrated=True, baseline_us=baseline,
               press_delta_us=press+1, release_delta_us=release)
        raise TimeoutError('lost radio ACK')
    monkeypatch.setattr(protocol, 'set_touch_parameters', wrong_echo)
    with pytest.raises(HomeAssistantError, match='Werte noch nicht bestätigt'):
        await apply_values(hass, config, initial_values(current))
    assert current['test_mode'] and not config.runtime_data.touch_parameter_waiters
    config.runtime_data.resume_touch.assert_not_awaited()
