"""Explicit touch calibration; acceptance does not imply successful completion."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zigpy.exceptions import ZigbeeException

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    device = dr.async_get(hass).async_get(entry.data["device_id"])
    entities = [CalibrateTouch(entry), CancelTouch(entry), BaselineTouch(entry)]
    for entity in entities:
        # Reuse the selected physical device. Registering its identifiers with
        # a second config entry would create a separate device in HA 2026.10.
        entity.device_entry = device
    async_add_entities(entities)


class CalibrateTouch(ButtonEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:gesture-tap"
    _attr_should_poll = False

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.unique_id}_calibrate_touch"
        self._attr_name = f"{entry.title} Touch kalibrieren"

    async def async_press(self):
        try:
            await self.entry.runtime_data.calibrate_touch(self.entry)
        except (
            HomeAssistantError,
            ZigbeeException,
            TimeoutError,
            ValueError,
            KeyError,
            RuntimeError,
        ) as err:
            raise HomeAssistantError(
                "Touchkalibrierung nicht bestätigt: "
                f"{type(err).__name__}: {str(err) or 'Zeitüberschreitung bei der Zigbee-Anfrage'}. "
                "Der Befehl kann das Gerät bereits erreicht haben; Touchstatus prüfen."
            ) from err


class CancelTouch(CalibrateTouch):
    _attr_icon = "mdi:close-circle-outline"

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = f"{entry.unique_id}_cancel_touch"
        self._attr_name = f"{entry.title} Kalibrierung abbrechen"

    async def async_press(self):
        try:
            await self.entry.runtime_data.cancel_touch(self.entry)
        except (
            HomeAssistantError,
            ZigbeeException,
            TimeoutError,
            ValueError,
            KeyError,
            RuntimeError,
        ) as err:
            raise HomeAssistantError(
                f"Abbruch nicht bestätigt: {type(err).__name__}: {str(err) or 'Zigbee-Zeitüberschreitung'}"
            ) from err


class BaselineTouch(CalibrateTouch):
    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = f"{entry.unique_id}_baseline_touch"
        self._attr_name = f"{entry.title} Touch-Ruhemessung starten"

    @property
    def available(self):
        status = self.entry.runtime_data.touch_status.get(self.entry.entry_id, {})
        return status.get("protocol_version", 0) >= 5 and status.get("state") == 9

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}", self.async_write_ha_state,
        ))

    async def async_press(self):
        try:
            await self.entry.runtime_data.baseline_touch(self.entry)
        except (ZigbeeException, TimeoutError, ValueError, KeyError, RuntimeError) as err:
            raise HomeAssistantError("Ruhemessung nicht gestartet. Erst Touch kalibrieren wählen.") from err
