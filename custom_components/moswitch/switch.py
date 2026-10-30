"""Status-light preference on the existing physical device."""

from datetime import timedelta

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers import device_registry as dr

from .indicator import IndicatorEntity, indicator_action

SCAN_INTERVAL = timedelta(seconds=60)


async def async_setup_entry(hass, entry, async_add_entities):
    entity = StatusLight(entry)
    entity.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([entity], update_before_add=True)


class StatusLight(IndicatorEntity, SwitchEntity):
    _attr_icon = "mdi:led-on"
    _attr_name = "Statuslicht"
    _attr_should_poll = True

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = f"{entry.unique_id}_status_light"

    @property
    def is_on(self):
        return self.status.get("enabled")

    async def async_turn_on(self, **kwargs):
        await indicator_action(self.entry, "enable")

    async def async_turn_off(self, **kwargs):
        await indicator_action(self.entry, "disable")

    async def async_update(self):
        await indicator_action(self.entry, "status")
