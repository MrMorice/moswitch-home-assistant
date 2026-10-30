"""Remove retired MoSwitch controls without touching ZHA or user entities."""

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

LAYOUT_VERSION = 3
RETIRED = {"configuration", "touch_test_mode", "touch_contact", "calibrate_touch",
           "baseline_touch", "cancel_touch"}


def migrate_entity_layout(hass, entry):
    if entry.data.get("ui_layout_version", 0) >= LAYOUT_VERSION:
        return
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (entity.platform != DOMAIN or entity.device_id != entry.data["device_id"]
                or not entity.unique_id.startswith(prefix)):
            continue
        suffix = entity.unique_id.removeprefix(prefix)
        if suffix in RETIRED or suffix.startswith(("touch_parameter_", "touch_measurement_")):
            registry.async_remove(entity.entity_id)
        elif suffix == "touch_calibration":
            registry.async_update_entity(entity.entity_id, entity_category=None)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "ui_layout_version": LAYOUT_VERSION})
