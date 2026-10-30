"""Status-light brightness preference."""

import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .indicator import IndicatorEntity, indicator_action


async def async_setup_entry(hass, entry, async_add_entities):
    entity = StatusLightBrightness(entry)
    entity.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([entity])


class StatusLightBrightness(IndicatorEntity, NumberEntity):
    _attr_should_poll = False
    _attr_name = "Statuslicht Helligkeit"
    _attr_icon = "mdi:brightness-6"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = f"{entry.unique_id}_status_light_brightness"

    @property
    def native_value(self):
        value = self.status.get("brightness")
        return round(value * 100 / 64) if value is not None else None

    async def async_set_native_value(self, value):
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise HomeAssistantError("Helligkeit muss zwischen 0 und 100 % liegen")
        await indicator_action(self.entry, "brightness", round(value * 64 / 100))
