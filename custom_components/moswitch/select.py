"""Native per-device selection of double-tap behavior."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr

from .rooms import device_area


async def async_setup_entry(hass, entry, async_add_entities):
    entity = DoubleAction(entry)
    entity.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([entity])


class DoubleAction(SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "double_action"
    _attr_icon = "mdi:gesture-double-tap"
    _attr_options = ["media", "custom", "none"]

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.unique_id}_double_action"

    @property
    def current_option(self):
        return self.entry.options.get("double_action", "media")

    async def async_select_option(self, option):
        if option not in self.options:
            raise ValueError("Ungültige Doppeltipp-Aktion")
        options = {**self.entry.options, "double_action": option}
        area = device_area(self.hass, self.entry.data["device_id"])
        if area in self.entry.runtime_data.rooms:
            options["scene_order"] = list(self.entry.runtime_data.rooms[area]["scene_order"])
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_added_to_hass(self):
        self.async_on_remove(self.entry.add_update_listener(self._options_updated))

    async def _options_updated(self, hass, entry):
        self.async_write_ha_state()
