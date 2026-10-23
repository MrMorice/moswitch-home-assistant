"""Commissioning diagnostics; the ring is intentionally not a light entity."""

import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zigpy.exceptions import ZigbeeException

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    touch = TouchStatus(entry)
    touch.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([ConfigurationStatus(entry), touch])


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


class TouchStatus(ConfigurationStatus):
    _attr_should_poll = True
    _attr_icon = "mdi:gesture-tap"

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = f"{entry.unique_id}_touch_calibration"
        self._attr_name = f"{entry.title} Touchkalibrierung"
        self._query_failed = False

    @property
    def available(self):
        runtime = self.entry.runtime_data
        received = runtime.touch_received_at.get(self.entry.entry_id)
        return not self._query_failed and received is not None and time.monotonic() - received < 120

    @property
    def native_value(self):
        status = self.entry.runtime_data.touch_status.get(self.entry.entry_id)
        if status is None:
            return None
        if status["state"] == 7:
            return f"Bitte tippen: {status['learning_taps']}/10"
        return (
            "Nicht kalibriert",
            "Vorbereitung",
            "Kalibriert gerade",
            "Kalibriert",
            "Kalibrierung fehlgeschlagen",
            "Messfehler",
            "Speichern fehlgeschlagen",
            "Bitte zehnmal tippen",
            "Abgebrochen",
        )[status["state"]]

    @property
    def extra_state_attributes(self):
        status = self.entry.runtime_data.touch_status.get(self.entry.entry_id, {})
        return {key: value for key, value in status.items() if key != "state"}

    async def async_update(self):
        try:
            await self.entry.runtime_data.query_touch(self.entry)
            self._query_failed = False
        except (
            HomeAssistantError,
            ZigbeeException,
            TimeoutError,
            ValueError,
            KeyError,
            RuntimeError,
        ):
            # Old firmware rejects the command. Never display an ACK as success.
            self._query_failed = True
