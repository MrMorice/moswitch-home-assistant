"""Resolve actual HA room membership without maintaining exclusion lists."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def device_area(hass, device_id: str) -> str | None:
    device = dr.async_get(hass).async_get(device_id)
    return device.area_id if device else None


def entity_area(hass, entry) -> str | None:
    return entry.area_id or (device_area(hass, entry.device_id) if entry.device_id else None)


def room_entities(hass, area_id: str, domain: str):
    return [
        entry
        for entry in er.async_get(hass).entities.values()
        if entry.domain == domain
        and not entry.disabled_by
        and entity_area(hass, entry) == area_id
        and entry.platform != DOMAIN
    ]


def scene_cycle(hass, area_id: str, order: list[str], ignore_label: str) -> list[str]:
    candidates = {
        entry.entity_id
        for entry in room_entities(hass, area_id, "scene")
        if ignore_label not in entry.labels
    }
    # Retain explicit order; append newly added scenes in a deterministic order.
    chosen = list(dict.fromkeys(item for item in order if item in candidates))
    return chosen + sorted(candidates - set(chosen))


def ieee_for_device(hass, device_id: str) -> str:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ValueError("Device missing")
    return next(value for domain, value in device.identifiers if domain == "zha")
