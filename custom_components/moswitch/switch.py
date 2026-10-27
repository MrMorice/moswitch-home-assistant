"""Explicit quiet test session, including held-electrode diagnosis."""

import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zigpy.exceptions import ZigbeeException

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    entity = TouchTestMode(entry)
    entity.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([entity])


class TouchTestMode(SwitchEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_icon = "mdi:test-tube"

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.unique_id}_touch_test_mode"
        self._attr_name = f"{entry.title} Touch-Testmodus"

    @property
    def available(self):
        runtime = self.entry.runtime_data
        status = runtime.touch_status.get(self.entry.entry_id, {})
        return (status.get("protocol_version", 0) >= 5 and
                time.monotonic() - runtime.touch_received_at.get(self.entry.entry_id, 0) < 120)

    @property
    def is_on(self):
        return self.entry.runtime_data.touch_status.get(self.entry.entry_id, {}).get("test_mode", False)

    async def async_turn_on(self, **kwargs):
        await self.set_mode(True)

    async def async_turn_off(self, **kwargs):
        await self.set_mode(False)

    async def set_mode(self, enabled):
        try:
            await self.entry.runtime_data.test_touch(self.entry, enabled)
        except (ZigbeeException, TimeoutError, ValueError, KeyError, RuntimeError) as err:
            raise HomeAssistantError("Touch-Testmodus nicht bestätigt") from err

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state,
        ))
