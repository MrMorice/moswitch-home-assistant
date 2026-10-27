"""One-time reversible cleanup of the old MoSwitch debug controls."""

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

LAYOUT_VERSION = 1


def migrate_entity_layout(hass, entry):
    if entry.data.get("ui_layout_version", 0) >= LAYOUT_VERSION:
        return
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        # Never alter ZHA's firmware, Identify, RSSI or another switch's entities.
        if entity.platform != DOMAIN or entity.device_id != entry.data["device_id"]:
            continue
        suffix = entity.unique_id.removeprefix(f"{entry.unique_id}_")
        legacy = suffix.startswith(("touch_parameter_", "touch_measurement_")) or suffix in (
            "calibrate_touch", "baseline_touch", "cancel_touch", "touch_test_mode", "touch_contact",
        )
        if legacy and entity.disabled_by is None:
            registry.async_update_entity(entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "ui_layout_version": LAYOUT_VERSION})
