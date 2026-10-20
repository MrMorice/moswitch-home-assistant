"""MoSwitch room configuration on top of ZHA."""

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .runtime import Runtime

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass, entry):
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        runtime = Runtime(hass)
        await runtime.start()
        hass.data[DOMAIN] = runtime
    runtime.entries[entry.entry_id] = entry
    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    runtime.schedule_sync()
    return True


async def async_options_updated(hass, entry):
    await entry.runtime_data.options_updated(entry)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = entry.runtime_data
    runtime.remove(entry)
    if not runtime.entries:
        runtime.stop()
        hass.data.pop(DOMAIN, None)
    return True
