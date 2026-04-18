from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([IntelligentESSAutoSwitch(entry)])

class IntelligentESSAutoSwitch(SwitchEntity):
    def __init__(self, entry):
        self._entry = entry
        self._attr_name = "Intelligent ESS Ladeautomatik"
        self._attr_unique_id = f"{entry.entry_id}_auto_charge"
        self._attr_is_on = entry.options.get("auto_charge_enabled", False)
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_turn_on(self, **kwargs):
        new_opts = {**self._entry.options, "auto_charge_enabled": True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self._attr_is_on = True

    async def async_turn_off(self, **kwargs):
        new_opts = {**self._entry.options, "auto_charge_enabled": False}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self._attr_is_on = False