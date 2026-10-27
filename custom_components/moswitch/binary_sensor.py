"""Live physical contact feedback on the existing ZHA device."""

import time

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    entity = TouchContact(entry)
    entity.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([entity])


class TouchContact(BinarySensorEntity):
    _attr_entity_registry_enabled_default = False
    _attr_should_poll = False
    _attr_icon = "mdi:gesture-tap"

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.unique_id}_touch_contact"
        self._attr_name = f"{entry.title} Touchkontakt"

    @property
    def available(self):
        received = self.entry.runtime_data.touch_received_at.get(self.entry.entry_id)
        return received is not None and time.monotonic() - received < 120

    @property
    def is_on(self):
        status = self.entry.runtime_data.touch_status.get(self.entry.entry_id, {})
        return bool(status.get("pressed"))

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state,
        ))
