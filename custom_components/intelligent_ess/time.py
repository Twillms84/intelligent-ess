from datetime import time
from homeassistant.components.time import TimeEntity
from homeassistant.helpers.entity import EntityCategory
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        IntelligentESSTime(entry, "man_charge_s1_start", "Laden Slot 1 Start", EntityCategory.CONFIG),
        IntelligentESSTime(entry, "man_charge_s1_end", "Laden Slot 1 Ende", EntityCategory.CONFIG),
        IntelligentESSTime(entry, "man_charge_s2_start", "Laden Slot 2 Start", EntityCategory.CONFIG),
        IntelligentESSTime(entry, "man_charge_s2_end", "Laden Slot 2 Ende", EntityCategory.CONFIG),
        IntelligentESSTime(entry, "man_hold_s1_start", "Sperre Slot 1 Start", EntityCategory.CONFIG),
        IntelligentESSTime(entry, "man_hold_s1_end", "Sperre Slot 1 Ende", EntityCategory.CONFIG),
    ])

class IntelligentESSTime(TimeEntity):
    """Repräsentiert eine Zeit-Einstellung im Intelligent ESS."""
    _attr_has_entity_name = True

    def __init__(self, entry, key, name, category=None):
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_entity_category = category
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Intelligent ESS",
        }
        # Wir erzwingen die technische Entity-ID für den Coordinator
        self.entity_id = f"time.intelligent_ess_{key}"

    @property
    def native_value(self) -> time:
        """Gibt den Wert aus den Optionen als echtes Zeit-Objekt zurück."""
        val = self._entry.options.get(self._key, "00:00:00")
        
        # Falls es schon ein time-Objekt ist
        if isinstance(val, time):
            return val
            
        # Falls es ein String ist (Normalfall in HA Options)
        try:
            # Behandelt HH:MM:SS und HH:MM
            return time.fromisoformat(val)
        except (ValueError, TypeError):
            # Letzter Rettungsversuch: Falls nur "14:16" ohne Sekunden kommt
            try:
                parts = str(val).split(":")
                if len(parts) >= 2:
                    return time(hour=int(parts[0]), minute=int(parts[1]))
            except Exception:
                pass
            return time(0, 0)

    async def async_set_value(self, value: time) -> None:
        """Speichert die gewählte Zeit sauber als ISO-String ab."""
        # Wir speichern IMMER im Format HH:MM:SS, damit es beim Lesen keine Probleme gibt
        new_opts = {**self._entry.options, self._key: value.isoformat()}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)