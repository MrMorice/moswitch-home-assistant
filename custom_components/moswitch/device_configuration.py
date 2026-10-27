"""Point an existing ZHA device at its own MoSwitch options entry."""

from urllib.parse import quote

from homeassistant.helpers import device_registry as dr

PREFIX = "homeassistant://config/devices/device/"


def configuration_url(entry):
    """Open the assistant over this physical device, without leaving its page."""
    return (PREFIX + quote(entry.data["device_id"], safe="")
            + "?moswitch_settings=" + quote(entry.entry_id, safe=""))


def attach_configuration_link(hass, entry):
    """Update only the existing physical device; keep its ZHA ownership."""
    registry = dr.async_get(hass)
    device = registry.async_get(entry.data["device_id"])
    if device is not None and device.manufacturer == "MoSwitch":
        registry.async_update_device(device.id, configuration_url=configuration_url(entry))


def remove_configuration_link(hass, entry):
    """Remove only our own pointer when this MoSwitch entry is deleted."""
    registry = dr.async_get(hass)
    device = registry.async_get(entry.data["device_id"])
    if device is not None and device.configuration_url == configuration_url(entry):
        registry.async_update_device(device.id, configuration_url=None)


def device_name(hass, entry):
    device = dr.async_get(hass).async_get(entry.data["device_id"])
    if device is None:
        return entry.title
    return device.name_by_user or device.name or entry.title
