"""Validated persistent touch parameters, confirmed by the physical device."""

import math
import time

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zigpy.exceptions import ZigbeeException

from .const import DOMAIN
from .protocol import TUNING_BOUNDS, TUNING_FIELDS

PARAMETERS = (
    ("baseline_us", "Touch-Baseline einstellen"),
    ("press_delta_us", "Touch-Abstand Einschalten"),
    ("release_delta_us", "Touch-Abstand Loslassen"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    device = dr.async_get(hass).async_get(entry.data["device_id"])
    entities = [TouchParameter(entry, key, label) for key, label in PARAMETERS]
    entities.extend(TouchTuningParameter(entry, key, label, bounds) for key, label, bounds in zip(
        TUNING_FIELDS,
        ("Touch-Entprellung Einschalten", "Touch-Entprellung Loslassen", "Touch-Medianfilter",
         "Touch-Aussetzer-Toleranz", "Touch-Ruhezeit nach Loslassen"), TUNING_BOUNDS, strict=True,
    ))
    for entity in entities:
        entity.device_entry = device
    async_add_entities(entities)


class TouchParameter(NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "µs"
    _attr_icon = "mdi:gesture-tap"

    def __init__(self, entry, key, label):
        self.entry, self.key = entry, key
        self._attr_unique_id = f"{entry.unique_id}_touch_parameter_{key}"
        self._attr_name = f"{entry.title} {label}"

    @property
    def status(self):
        return self.entry.runtime_data.touch_status.get(self.entry.entry_id, {})

    @property
    def available(self):
        received = self.entry.runtime_data.touch_received_at.get(self.entry.entry_id, 0)
        return (self.status.get("parameters_writable", False)
                and self.status.get("calibrated", False)
                and time.monotonic() - received < 120)

    @property
    def native_value(self):
        return self.status.get(self.key)

    @property
    def native_min_value(self):
        return self.status.get("release_delta_us", 1) + 1 if self.key == "press_delta_us" else 1

    @property
    def native_max_value(self):
        if self.key == "baseline_us":
            return min(1000, 1999 - self.status.get("press_delta_us", 2))
        if self.key == "press_delta_us":
            return 1999 - self.status.get("baseline_us", 1)
        return max(1, self.status.get("press_delta_us", 2) - 1)

    async def async_set_native_value(self, value):
        if not math.isfinite(value) or value != int(value):
            raise HomeAssistantError("Bitte einen ganzzahligen Wert eingeben")
        try:
            await self.entry.runtime_data.set_touch_parameter(self.entry, self.key, int(value))
        except (ZigbeeException, TimeoutError, ValueError, KeyError, RuntimeError) as err:
            raise HomeAssistantError(f"Touchparameter nicht bestätigt: {err}") from err

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state,
        ))


class TouchTuningParameter(TouchParameter):
    def __init__(self, entry, key, label, bounds):
        super().__init__(entry, key, label)
        self.bounds = bounds
        self._attr_native_unit_of_measurement = None if key == "filter_samples" else "ms"
        self._attr_native_step = 2 if key == "filter_samples" else 1

    @property
    def available(self):
        received = self.entry.runtime_data.touch_received_at.get(self.entry.entry_id, 0)
        return self.status.get("tuning_writable", False) and time.monotonic() - received < 120

    @property
    def native_min_value(self):
        return self.bounds[0]

    @property
    def native_max_value(self):
        return 30 if self.key == "gap_grace_ms" and self.status.get("protocol_version", 0) < 7 else self.bounds[1]
