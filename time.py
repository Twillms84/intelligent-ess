from datetime import time
from homeassistant.components.time import TimeEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        IntelligentESSTime(entry, "man_charge_s1_start", "Laden Slot 1 Start"),
        IntelligentESSTime(entry, "man_charge_s1_end", "Laden Slot 1 Ende"),
        IntelligentESSTime(entry, "man_charge_s2_start", "Laden Slot 2 Start"),
        IntelligentESSTime(entry, "man_charge_s2_end", "Laden Slot 2 Ende"),
        IntelligentESSTime(entry, "man_hold_s1_start", "Sperre Slot 1 Start"),
        IntelligentESSTime(entry, "man_hold_s1_end", "Sperre Slot 1 Ende"),
    ])

class IntelligentESSTime(TimeEntity):
    def __init__(self, entry, key, name):
        self._entry = entry
        self._key = key
        self._attr_name = f"Intelligent ESS {name}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def native_value(self) -> time:
        """Holt den gespeicherten String und wandelt ihn in ein Zeit-Objekt um."""
        time_str = self._entry.options.get(self._key, "00:00:00")
        try:
            return time.fromisoformat(time_str)
        except ValueError:
            return time(0, 0)

    async def async_set_value(self, value: time) -> None:
        """Speichert die gewählte Zeit als ISO-String in den Options."""
        new_opts = {**self._entry.options, self._key: value.isoformat()}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)