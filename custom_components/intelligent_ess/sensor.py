from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    """Setzt die Sensoren basierend auf dem Coordinator-Update-Zyklus auf."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Sicherstellen, dass Daten vorhanden sind
    await coordinator.async_config_entry_first_refresh()
    
    async_add_entities([
        IntelligentESSActionSensor(coordinator),
        IntelligentESSConsumptionSensor(coordinator),
        IntelligentESSEventSensor(coordinator), 
        
        # Nutzt den Key 'rest_demand_daily'
        IntelligentESSGenericSensor(coordinator, "Restbedarf Heute", "rest_demand_daily", "kWh"),
        IntelligentESSGenericSensor(coordinator, "Nachtreserve", "morning_reserve", "kWh"),
        IntelligentESSGenericSensor(coordinator, "Fahrplan", "fahrplan", None),
        
        # Die aufgeteilten Forecast-Sensoren
        IntelligentESSForecastSensorCurrent(coordinator),
        IntelligentESSForecastSensorNext(coordinator),
        
        # Die 4 Spar-Sensoren
        IntelligentESSSavingsSensor(coordinator, "Solar-Ersparnis", "solar"),
        IntelligentESSSavingsSensor(coordinator, "Hold-Ersparnis", "hold"),
        IntelligentESSSavingsSensor(coordinator, "Load-Ersparnis", "load"),
        IntelligentESSSavingsSensor(coordinator, "Gesamt-Ersparnis", "total"),

        IntelligentESSTodayProfileSensor(coordinator),
        IntelligentESSAutarkyTimeSensor(coordinator), # <- Geändert: Braucht nur noch den Coordinator!
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

# --- NEU: Aufgeteilte Forecast-Sensoren ---

class IntelligentESSForecastSensorCurrent(IntelligentESSBase):
    """Sensor für die Verbrauchsprognose der aktuellen (herunterlaufenden) Stunde."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligent ESS Forecast Aktuelle Stunde"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_forecast_current_hour"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("forecast_current_hour", 0.0)

class IntelligentESSForecastSensorNext(IntelligentESSBase):
    """Sensor für die Verbrauchsprognose der nächsten vollen Stunde."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligent ESS Forecast Nächste Stunde"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_forecast_next_hour"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("forecast_next_hour", 0.0)

class IntelligentESSEventSensor(IntelligentESSBase):
    """Sensor für das Ereignis-Logbook (Diagnose-Kategorie)."""
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligent ESS Letztes Ereignis"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_event"
        self._attr_icon = "mdi:history"
        # Dies gruppiert den Sensor in der UI unter 'Diagnose'
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Gibt das letzte Ereignis aus dem Coordinator zurück."""
        return self.coordinator.data.get("last_event", "Keine Ereignisse")

class IntelligentESSAutarkyTimeSensor(IntelligentESSBase):
    """Sensor, der die Uhrzeit der voraussichtlichen Autarkie am morgigen Tag anzeigt."""
    
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Autarkie Start Morgen"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_autarky_time_tomorrow"
        self._attr_icon = "mdi:solar-power"

    @property
    def native_value(self):
        """Gibt die berechnete Uhrzeit (z.B. '08:00') oder 'Nicht erreicht' zurück."""
        if not self.coordinator.data:
            return "Warte auf Daten..."
        return self.coordinator.data.get("autarky_time_tomorrow", "Unbekannt")

    @property
    def extra_state_attributes(self):
        """Zusätzliche Attribute für Analyse-Zwecke (Gesamt-PV für morgen)."""
        if not self.coordinator.data:
            return {}
        return {
            "pv_forecast_tomorrow_kwh": self.coordinator.data.get("pv_tomorrow_total", 0.0)
        }

class IntelligentESSTodayProfileSensor(IntelligentESSBase):
    """Sensor, der den Tagesbedarf als State und das 24h-Profil als Attribut hat."""
    
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Intelligenter Tagesbedarf Profil"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_daily_profile"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_icon = "mdi:chart-bar"

    @property
    def native_value(self):
        # Der State ist der erwartete Gesamtverbrauch des Tages
        return self.coordinator.data.get("expected_daily_total", 0.0)

    @property
    def extra_state_attributes(self):
        # Das Attribut 'hourly_data' enthält unsere Liste mit den 24 Stundenwerten
        return {
            "hourly_data": self.coordinator.data.get("daily_profile", [])
        }