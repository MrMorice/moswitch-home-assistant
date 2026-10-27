"""Reversible migration to compact native configuration and diagnosis."""

from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

LAYOUT_VERSION = 2
NATIVE_ENTITIES = {
    "touch_parameter_baseline_us", "touch_parameter_press_delta_us",
    "touch_parameter_release_delta_us", "touch_test_mode", "touch_contact",
    "touch_measurement_baseline_us", "touch_measurement_idle_noise_us",
    "touch_measurement_learning_taps",
}


def migrate_entity_layout(hass, entry):
    previous = entry.data.get("ui_layout_version", 0)
    if previous >= LAYOUT_VERSION:
        return
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or entity.device_id != entry.data["device_id"]:
            continue
        suffix = entity.unique_id.removeprefix(f"{entry.unique_id}_")
        changes = {}
        if suffix.startswith("touch_measurement_") or suffix == "touch_contact":
            changes["entity_category"] = EntityCategory.DIAGNOSTIC
        if suffix in NATIVE_ENTITIES:
            if entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                changes["disabled_by"] = None
        elif previous < 1:
            legacy = suffix.startswith(("touch_parameter_", "touch_measurement_")) or suffix in (
                "calibrate_touch", "baseline_touch", "cancel_touch",
            )
            if legacy and entity.disabled_by is None:
                changes["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
        if changes:
            registry.async_update_entity(entity.entity_id, **changes)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "ui_layout_version": LAYOUT_VERSION})
