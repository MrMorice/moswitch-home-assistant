"""Select a MoSwitch and configure its HA-only actions."""

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.script import async_validate_actions_config

from .const import DOMAIN
from .device_configuration import device_name
from .rooms import device_area, ieee_for_device, room_entities
from .touch_wizard import TouchWizard


def options_schema(hass, device_id, options, *, area_id=None):
    area_id = area_id or device_area(hass, device_id)
    players = (
        [entry.entity_id for entry in room_entities(hass, area_id, "media_player")]
        if area_id
        else []
    )
    scenes = [entry.entity_id for entry in room_entities(hass, area_id, "scene")] if area_id else []
    return vol.Schema(
        {
            vol.Required(
                "double_action", default=options.get("double_action", "media")
            ): selector.SelectSelector(
                {
                    "options": ["media", "custom", "none"],
                    "translation_key": "double_action",
                }
            ),
            vol.Optional("players", default=options.get("players", [])): selector.EntitySelector(
                {
                    "filter": {"domain": "media_player"},
                    "include_entities": players,
                    "multiple": True,
                }
            ),
            vol.Optional("actions", default=options.get("actions", [])): selector.ActionSelector(),
            vol.Optional(
                "scene_order", default=options.get("scene_order", [])
            ): selector.EntitySelector(
                {
                    "filter": {"domain": "scene"},
                    "include_entities": scenes,
                    "multiple": True,
                }
            ),
        }
    )


async def validate_options(hass, device_id, options):
    area_id = device_area(hass, device_id)
    if not area_id:
        raise ValueError("assign_area")
    allowed = {entry.entity_id for entry in room_entities(hass, area_id, "media_player")}
    if not set(options.get("players", [])) <= allowed:
        raise ValueError("room_players_only")
    scenes = {entry.entity_id for entry in room_entities(hass, area_id, "scene")}
    if not set(options.get("scene_order", [])) <= scenes:
        raise ValueError("room_scenes_only")
    await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(options.get("actions", [])))


class MoSwitchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            device_id = user_input["device_id"]
            device = dr.async_get(self.hass).async_get(device_id)
            if device is None or device.manufacturer != "MoSwitch":
                errors["base"] = "invalid_device"
            elif not device_area(self.hass, device_id):
                errors["base"] = "assign_area"
            else:
                await self.async_set_unique_id(ieee_for_device(self.hass, device_id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device.name_by_user or device.name or "MoSwitch",
                    data={"device_id": device_id},
                    options={
                        "double_action": "media",
                        "players": [],
                        "scene_order": [],
                        "actions": [],
                    },
                )
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("device_id"): selector.DeviceSelector(
                        {
                            "filter": {
                                "integration": "zha",
                                "manufacturer": "MoSwitch",
                            },
                        }
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MoSwitchOptionsFlow()


class MoSwitchOptionsFlow(TouchWizard, config_entries.OptionsFlow):
    def _current_options(self, device_id):
        options = dict(self.config_entry.options)
        runtime = self.hass.data.get(DOMAIN)
        area = device_area(self.hass, device_id)
        if runtime and area in runtime.rooms:
            options["scene_order"] = runtime.rooms[area]["scene_order"]
        return options

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["touch_setup", "actions"],
            description_placeholders={"device": device_name(self.hass, self.config_entry)},
        )

    async def async_step_actions(self, user_input=None):
        device_id = self.config_entry.data["device_id"]
        errors = {}
        if user_input is not None:
            try:
                await validate_options(self.hass, device_id, user_input)
            except ValueError as err:
                errors["base"] = str(err)
            except vol.Invalid:
                errors["base"] = "invalid_actions"
            else:
                return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="actions",
            errors=errors,
            data_schema=options_schema(self.hass, device_id, self._current_options(device_id)),
        )
