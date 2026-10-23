"""Describe recognized gestures in the existing ZHA device's activity log."""

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import TOUCH_FEEDBACK


@callback
def async_describe_events(hass, async_describe_event):
    registry = dr.async_get(hass)
    messages = {
        "single_press": "Einfach-Tipp erkannt",
        "double_press": "Doppeltipp erkannt",
        "hold": "Halten erkannt",
    }

    @callback
    def describe(event):
        device = registry.async_get(event.data["device_id"])
        name = (device.name_by_user or device.name) if device else None
        return {
            "name": name or "MoSwitch",
            "message": messages.get(event.data.get("gesture"), "Bedienung erkannt"),
        }

    # HA's device logbook selects event domains from the owning config entry.
    # The physical MoSwitch belongs to ZHA, not to this helper integration.
    async_describe_event("zha", "moswitch_gesture", describe)

    @callback
    def describe_calibration(event):
        device = registry.async_get(event.data["device_id"])
        name = (device.name_by_user or device.name) if device else None
        feedback = TOUCH_FEEDBACK.get(event.data.get("feedback"), "Status aktualisiert")
        count = event.data.get("learning_taps", 0)
        return {"name": name or "MoSwitch", "message": f"Kalibrierung: {feedback} ({count}/10)"}

    async_describe_event("zha", "moswitch_calibration", describe_calibration)

