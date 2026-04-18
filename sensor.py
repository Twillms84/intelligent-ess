from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    """Setzt die Sensoren basierend auf dem Coordinator-Update-Zyklus auf."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Sicherstellen, dass Daten vorhanden sind
    await coordinator.async_config_entry_first_refresh()
    
    async_add_entities([
        IntelligentESSActionSensor(coordinator),
        IntelligentESSConsumptionSensor(coordinator),
        IntelligentESSGenericSensor(coordinator, "Restbedarf", "rest_night", "kWh"),
        IntelligentESSGenericSensor(coordinator, "Nachtreserve", "morning_reserve", "kWh"),
        IntelligentESSGenericSensor(coordinator, "Fahrplan", "fahrplan", None),
        IntelligentESSForecastSensor(coordinator),
        
        # Die 4 neuen Spar-Sensoren
        IntelligentESSSavingsSensor(coordinator, "Solar-Ersparnis", "solar"),
        IntelligentESSSavingsSensor(coordinator, "Hold-Ersparnis", "hold"),
        IntelligentESSSavingsSensor(coordinator, "Load-Ersparnis", "load"),
        IntelligentESSSavingsSensor(coordinator, "Gesamt-Ersparnis", "total"),
    ])

class IntelligentESSBase(CoordinatorEntity, SensorEntity):
    """Basis-Klasse für alle Sensoren mit Device-Verknüpfung."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": "Intelligent ESS",
            "manufacturer": "Gemini AI Custom",
            "model": "Modular V3"
        }

class IntelligentESSActionSensor(IntelligentESSBase):
    """Sensor für die aktuelle Strategie (LADEN, HOLD, NORMAL)."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligent ESS Action"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_action"

    @property
    def native_value(self):
        return self.coordinator.data.get("strat", "NORMAL")

    @property
    def extra_state_attributes(self):
        return {"grund": self.coordinator.data.get("strat_msg", "")}

class IntelligentESSGenericSensor(IntelligentESSBase):
    """Universal-Sensor für einfache Werte wie Restbedarf oder Fahrplan."""
    def __init__(self, coordinator, name, key, unit):
        super().__init__(coordinator)
        self._attr_name = f"Intelligent ESS {name}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._key = key
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self):
        val = self.coordinator.data.get(self._key)
        if val is None:
            return "Warte..." if not self._attr_native_unit_of_measurement else 0.0
        return val

class IntelligentESSConsumptionSensor(IntelligentESSBase):
    """Sensor für den aktuellen Hausverbrauch in kW."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligent ESS Hausverbrauch"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_house_kw"
        self._attr_native_unit_of_measurement = "kW"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("house_kw", 0.0)

class IntelligentESSSavingsSensor(IntelligentESSBase):
    """Spezialisierter Sensor für die finanziellen Ersparnisse."""
    def __init__(self, coordinator, name, key):
        super().__init__(coordinator)
        self._attr_name = f"Intelligent ESS {name}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_savings_{key}"
        self._key = key
        self._attr_native_unit_of_measurement = "€"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        # Greift auf das "savings" Dictionary im Coordinator zu
        savings_dict = self.coordinator.data.get("savings", {})
        return round(float(savings_dict.get(self._key, 0.0)), 2)

class IntelligentESSForecastSensor(IntelligentESSBase):
    """Sensor für die Verbrauchsprognose mit stündlichen Details."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligent ESS Forecast Verbrauch"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_forecast_v"
        self._attr_native_unit_of_measurement = "kWh"

    @property
    def native_value(self):
        # Zeigt den Verbrauch der nächsten Stunde
        return self.coordinator.data.get("forecast_next_hour", 0.0)

    @property
    def extra_state_attributes(self):
        # Liefert die Details bis zum Zielzeitpunkt (morgens) in die Attribute
        return {
            "stündliche_prognose": self.coordinator.data.get("forecast_details", {}),
            "bereitstellung": "Profile Learning"
        }