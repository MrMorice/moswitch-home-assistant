"""A single readable device status; learning details stay in the assistant."""

import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zigpy.exceptions import ZigbeeException

from .const import DOMAIN, TOUCH_FAILURES, TOUCH_FEEDBACK


async def async_setup_entry(hass, entry, async_add_entities):
    entity = TouchStatus(entry)
    entity.device_entry = dr.async_get(hass).async_get(entry.data["device_id"])
    async_add_entities([entity])


class TouchStatus(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_icon = "mdi:gesture-tap"

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.unique_id}_touch_calibration"
        self._attr_name = "Status"
        self._query_failed_at = 0

    @property
    def available(self):
        runtime = self.entry.runtime_data
        if runtime.is_updating(self.entry):
            return True
        received = runtime.touch_received_at.get(self.entry.entry_id)
        return (received is not None and received > self._query_failed_at
                and time.monotonic() - received < 120)

    @property
    def native_value(self):
        if self.entry.runtime_data.is_updating(self.entry):
            return "Firmwareupdate"
        status = self.entry.runtime_data.touch_status.get(self.entry.entry_id)
        if status is None:
            return None
        if status["state"] == 9:
            return "Scheibe loslassen, dann Ruhemessung starten"
        if status["state"] == 10:
            return "Startprüfung" if status.get("startup_check") in (1, 3, 4) else "Einlernen pausiert Schaltaktionen"
        if status["state"] == 3 and status.get("test_mode"):
            return "Eingelernt – Einrichtung abschließen"
        if status["state"] in (11, 12, 13):
            return {11: "Zehn Tipps erfasst – jetzt zwei Sekunden halten",
                    12: "Weiter halten",
                    13: "Kontakt erfasst – jetzt loslassen"}[status["state"]]
        if status["state"] == 7:
            if feedback := TOUCH_FEEDBACK.get(status.get("feedback")):
                return f"{feedback} ({status['learning_taps']}/10)"
            return f"Bitte tippen: {status['learning_taps']}/10"
        if status["state"] == 4 and (reason := TOUCH_FAILURES.get(status.get("failure_reason"))):
            return f"Kalibrierung fehlgeschlagen: {reason}"
        return (
            "Nicht kalibriert",
            "Vorbereitung",
            "Kalibriert gerade",
            "Bereit",
            "Kalibrierung fehlgeschlagen",
            "Messfehler",
            "Speichern fehlgeschlagen",
            "Bitte zehnmal tippen",
            "Abgebrochen",
        )[status["state"]]

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state,
        ))

    async def async_update(self):
        try:
            await self.entry.runtime_data.query_touch(self.entry)
            self._query_failed_at = 0
        except (
            HomeAssistantError,
            ZigbeeException,
            TimeoutError,
            ValueError,
            KeyError,
            RuntimeError,
        ):
            # Old firmware rejects the command. Never display an ACK as success.
            self._query_failed_at = time.monotonic()
