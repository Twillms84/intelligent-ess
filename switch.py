from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    """Setzt alle Schalter der Integration auf."""
    async_add_entities([
        IntelligentESSSwitch(entry, "auto_charge_enabled", "Ladeautomatik (KI)", "mdi:robot-confused"),
        IntelligentESSSwitch(entry, "man_charge_s1_enabled", "Laden Slot 1 Aktiv", "mdi:clock-check"),
        IntelligentESSSwitch(entry, "man_charge_s2_enabled", "Laden Slot 2 Aktiv", "mdi:clock-check"),
        IntelligentESSSwitch(entry, "man_hold_s1_enabled", "Entladesperre Slot 1 Aktiv", "mdi:battery-lock"),
    ])

class IntelligentESSSwitch(SwitchEntity):
    def __init__(self, entry, key, name, icon):
        self._entry = entry
        self._key = key
        self._attr_name = f"Intelligent ESS {name}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def is_on(self) -> bool:
        """Holt den Status IMMER direkt aus den aktuellen Options."""
        # Wir schauen in options, falls nicht da (Ersteinrichtung), schauen wir in data
        return self._entry.options.get(self._key, self._entry.data.get(self._key, False))

    async def async_turn_on(self, **kwargs):
        """Aktualisiert die Options und triggert HA."""
        new_opts = {**self._entry.options, self._key: True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)

    async def async_turn_off(self, **kwargs):
        """Aktualisiert die Options und triggert HA."""
        new_opts = {**self._entry.options, self._key: False}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)