"""Describe recognized gestures in the existing ZHA device's activity log."""

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


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

    async_describe_event(DOMAIN, "moswitch_gesture", describe)
