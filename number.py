from homeassistant.components.number import NumberEntity, NumberMode
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        IntelligentESSNumber(entry, "safety_buffer", "Sicherheitsfaktor", 1.0, 2.0, 0.05, "multiplier"),
        IntelligentESSNumber(entry, "default_usage", "Standardverbrauch", 0.1, 5.0, 0.05, "kW"),
        IntelligentESSNumber(entry, "min_soc_reserve", "Min. SOC (Nacht-Reserve)", 5.0, 50.0, 1.0, "%"),
        IntelligentESSNumber(entry, "price_delta_threshold", "Preis-Differenz Limit", 0.0, 20.0, 0.5, "ct"),
    ])

class IntelligentESSNumber(NumberEntity):
    def __init__(self, entry, key, name, min_v, max_v, step, unit):
        self._entry = entry
        self._key = key
        self._attr_name = f"Intelligent ESS {name}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_min_value = min_v
        self._attr_native_max_value = max_v
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def native_value(self):
        # Liest aus options, Fallback auf data
        return self._entry.options.get(self._key, self._entry.data.get(self._key))

    async def async_set_native_value(self, value):
        new_options = {**self._entry.options, self._key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)