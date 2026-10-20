"""Commissioning diagnostics; the ring is intentionally not a light entity."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ConfigurationStatus(entry)])


class ConfigurationStatus(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.unique_id}_configuration"
        self._attr_name = f"{entry.title} Einrichtung"

    @property
    def native_value(self):
        return self.entry.runtime_data.health.get(self.entry.entry_id, "Einrichtung ausstehend")[
            :255
        ]

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self.entry.entry_id}",
                self.async_write_ha_state,
            )
        )
