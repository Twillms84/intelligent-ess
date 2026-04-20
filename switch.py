from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    """Setzt die Schalter für Automatik und manuelle Slots auf."""
    switches = [
        # Dein ursprünglicher Schalter
        IntelligentESSSwitch(entry, "auto_charge_enabled", "Ladeautomatik (KI)"),
        # Die neuen Schalter für die manuellen Slots
        IntelligentESSSwitch(entry, "man_charge_s1_enabled", "Laden Slot 1 Aktiv"),
        IntelligentESSSwitch(entry, "man_charge_s2_enabled", "Laden Slot 2 Aktiv"),
        IntelligentESSSwitch(entry, "man_hold_s1_enabled", "Entladesperre Slot 1 Aktiv"),
        IntelligentESSSwitch(entry, "man_hold_s2_enabled", "Entladesperre Slot 2 Aktiv"),
    ]
    async_add_entities(switches)

class IntelligentESSSwitch(SwitchEntity):
    def __init__(self, entry, key, name):
        self._entry = entry
        self._key = key
        self._attr_name = f"Intelligent ESS {name}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def is_on(self):
        """Holt den Status immer aktuell aus den Entry-Options."""
        return self._entry.options.get(self._key, False)

    async def async_turn_on(self, **kwargs):
        """Schaltet die Option auf True."""
        new_opts = {**self._entry.options, self._key: True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)

    async def async_turn_off(self, **kwargs):
        """Schaltet die Option auf False."""
        new_opts = {**self._entry.options, self._key: False}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)