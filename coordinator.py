import logging
import json
import os
import datetime
from datetime import timedelta  # <--- Diese Zeile fehlt!

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .scheduler import calculate_strategy
from .analytics import update_forecasts_and_finances
from .profile_manager import ProfileManager
from .smart_charging import calculate_charge_strategy as calculate_smart_charge
from .smart_discharging import calculate_discharge_strategy, get_timer_value

_LOGGER = logging.getLogger(__name__)
class IntelligentESSCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, entry):
        super().__init__(
            hass, 
            _LOGGER, 
            name=DOMAIN, 
            update_interval=timedelta(minutes=1)
        )
        self.entry = entry
        self.last_readings = {}
        self.profile_path = hass.config.path("intelligent_ess_profiles.json")
        self.savings_path = hass.config.path("intelligent_ess_savings.json")
        storage_path = hass.config.path("custom_components/intelligent_ess")
        self.profile_manager = ProfileManager(storage_path)
        self.power_samples = []
        self._savings_loaded = False

        # Initialisierung der Datenstruktur mit den aktualisierten Forecast-Schlüsseln
        self.data = {
            "house_kw": 0.0,
            "net_watt": 0.0,
            "strat": "NORMAL",
            "strat_msg": "Initialisierung...",
            "rest_demand_daily": 0.0,        # NEU: Ersetzt rest_night
            "forecast_current_hour": 0.0,    # NEU: Herunterlaufender Wert aktuelle Stunde
            "forecast_next_hour": 0.0,
            "morning_reserve": 0.0,
            "fahrplan": "Warte auf Daten...",
            "savings": {
                "total": 0.0, 
                "solar": 0.0,
                "hold": 0.0,
                "load": 0.0
            },
            "samples": []
        }

    def _load_savings(self):
        """Lädt Ersparnisse beim Start."""
        if os.path.exists(self.savings_path):
            try:
                with open(self.savings_path, 'r') as f:
                    saved = json.load(f)
                    # Migrations-Check: Falls alte Struktur vorhanden, Felder sicherstellen
                    for key in ["solar", "hold", "load", "total"]:
                        if key not in saved:
                            saved[key] = 0.0
                    self.data["savings"].update(saved)
            except Exception as e:
                _LOGGER.error("Fehler beim Laden der Ersparnisse: %s", e)
    
    async def _async_update_data(self):
        try:
            # --- 1. DATEN-AKQUISE ---
            config = {**self.entry.data, **self.entry.options}
            current = self._get_raw_states(config)
            if not current: return self.data
            now = dt_util.now()

            # --- 2. ANALYTICS (Ausgelagert) ---
            # Berechnet Forecasts, Preise & Bedarfe
            analytics_results = await update_forecasts_and_finances(self.hass, self, config, now)
            self.data.update(analytics_results)

            # --- 3. SCHEDULER (Ausgelagert) ---
            # Entscheidet über die Strategie basierend auf Timern
            strat, msg, lock_needed = calculate_strategy(self.entry.options, self.hass.states)
            
            self.data.update({
                "strat": strat,
                "strat_msg": msg,
                "discharge_lock_active": lock_needed,
                "fahrplan": f"Status: {strat} | Bedarf: {self.data['rest_demand_daily']}kWh"
            })

            # --- 4. HARDWARE-STEUERUNG ---
            await self._handle_hardware_control(config, lock_needed, strat)

            self.last_readings = current
            return self.data

        except Exception as e:
            _LOGGER.error("Fehler im Coordinator: %s", e)
            raise UpdateFailed(f"Update fehlgeschlagen: {e}")

    async def _handle_hardware_control(self, config, lock_needed, strat):
        """Separate Methode nur für die WR-Schaltung."""
        limit_entity = config.get("wr_limit_entity")
        if not limit_entity: return

        unlock_val = float(config.get("wr_unlock_value", 100.0))
        target_limit = 0.0 if lock_needed else unlock_val
        
        ent_state = self.hass.states.get(limit_entity)
        if ent_state and ent_state.state not in ['unknown', 'unavailable', 'none']:
            try:
                if abs(float(ent_state.state) - target_limit) > 0.1:
                    await self.hass.services.async_call(
                        "number", "set_value", 
                        {"entity_id": limit_entity, "value": target_limit}
                    )
                    _LOGGER.info("Hardware: %s -> %s (%s)", limit_entity, target_limit, strat)
            except ValueError: pass

    def _save_savings_to_disk(self):
        try:
            with open(self.savings_path, 'w') as f:
                json.dump(self.data["savings"], f)
        except: pass

