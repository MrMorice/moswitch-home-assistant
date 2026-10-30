"""Exercise actual zigpy catalog caching without networking or HA restart."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_moswitch import device, panel_config

from custom_components.moswitch.firmware_updates import MoSwitchOtaProvider
from custom_components.moswitch.runtime import Runtime


@pytest.mark.asyncio
async def test_catalog_reloads_after_short_cache_without_restart(monkeypatch):
    provider = MoSwitchOtaProvider(url='https://example.invalid/index.json', manufacturer_ids=[])
    loaded = []
    async def index(_session):
        loaded.append(True)
        yield 'verified-image-metadata'
    monkeypatch.setattr(provider, '_load_index', index)
    assert await provider.load_index() == ['verified-image-metadata']
    assert await provider.load_index() is None
    provider._index_last_updated -= timedelta(minutes=6)
    assert await provider.load_index() == ['verified-image-metadata']
    assert len(loaded) == 2


@pytest.mark.asyncio
async def test_provider_registration_is_independent_of_room_setup(hass, monkeypatch):
    config = panel_config(hass, device(hass))
    register = Mock()
    ota = SimpleNamespace(register_provider=register)
    gateway = SimpleNamespace(application_controller=SimpleNamespace(ota=ota))
    monkeypatch.setattr('custom_components.moswitch.runtime.get_zha_gateway', lambda _: gateway)
    runtime = Runtime(hass)
    runtime.register_ota_provider()
    runtime.register_ota_provider()
    register.assert_called_once()
    assert not config.runtime_data.rooms
    assert isinstance(register.call_args.args[0], MoSwitchOtaProvider)
