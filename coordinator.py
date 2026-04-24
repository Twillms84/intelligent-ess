import logging
import json
import os
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .sheduler import calculate_strategy
from .analytics import update_forecasts_and_finances, get_raw_states, get_tibber_prices, get_solar_forecast
from .profile_manager import ProfileManager

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
        self.savings_path = hass.config.path("intelligent_ess_savings.json")
        storage_path = hass.config.path("custom_components/intelligent_ess")
        self.profile_manager = ProfileManager(storage_path)
        self._savings_loaded = False

        self.data = {
            "house_kw": 0.0,
            "net_watt": 0.0,
            "strat": "NORMAL",
            "strat_msg": "Initialisierung...",
            "rest_demand_daily": 0.0,
            "forecast_current_hour": 0.0,
            "forecast_next_hour": 0.0,
            "morning_reserve": 0.0,
            "fahrplan": "Warte auf Daten...",
            "savings": {"total": 0.0, "solar": 0.0, "hold": 0.0, "load": 0.0},
            "samples": []
        }

    async def _async_update_data(self):
        try:
            # 1. DATEI BEIM START LADEN
            if not self._savings_loaded:
                await self.hass.async_add_executor_job(self._load_savings)
                self._savings_loaded = True

            # 2. DATEN-AKQUISE (Jetzt über analytics.py)
            config = {**self.entry.data, **self.entry.options}
            current = get_raw_states(self.hass, config)
            if not current: 
                return self.data
                
            now = dt_util.now()

            # 3. VERBRAUCHS-BERECHNUNG (Deltas für Finanzen)
            house_kwh = 0.0
            deltas = {}
            if self.last_readings:
                deltas = {k: current[k] - self.last_readings[k] for k in current if k in self.last_readings}
                # Plausibilitäts-Check
                if not any(v < -0.001 or v > 1.2 for v in deltas.values()):
                    house_kwh = max(0, deltas["pv"] + deltas["grid_in"] + deltas["bat_dis"] - deltas["grid_out"] - deltas["bat_chg"])
                    self.data["house_kw"] = round(house_kwh * 60, 3)
                    self.data["samples"].append(house_kwh)

            # 4. ANALYTICS (Angepasste Parameterübergabe!)
            # Wir übergeben explizit den profile_manager, current_savings und current_strat
            analytics_results = await update_forecasts_and_finances(
                self.hass, 
                self.profile_manager, 
                config, 
                deltas, 
                house_kwh, 
                self.data["savings"], 
                self.data.get("strat")
            )
            self.data.update(analytics_results)

            # 5. SCHEDULER (Wir nutzen 'config' statt nur 'options', damit alle Keys gefunden werden)
            strat, msg, lock_needed = calculate_strategy(config, self.hass.states)
            self.data.update({
                "strat": strat,
                "strat_msg": msg,
                "discharge_lock_active": lock_needed,
                "fahrplan": f"Status: {strat} | Bedarf: {self.data['rest_demand_daily']}kWh"
            })

            # 6. HARDWARE-STEUERUNG
            await self._handle_hardware_control(config, lock_needed, strat)

            # 7. SPEICHERN & LEARNING (Alle 15 Min)
            if len(self.data["samples"]) >= 15:
                samples = list(self.data["samples"])
                self.data["samples"] = []
                await self.hass.async_add_executor_job(self.profile_manager.update_profile, now, samples)
                await self.hass.async_add_executor_job(self._save_savings_to_disk)

            self.last_readings = current
            return self.data

        except Exception as e:
            _LOGGER.error("Fehler im Coordinator: %s", e)
            raise UpdateFailed(f"Update fehlgeschlagen: {e}")

    # --- HELPER METHODEN ---
    async def _handle_hardware_control(self, config, lock_needed, strat):
        # 1. Haupt-Limit (Sperre/Freigabe)
        limit_entity = config.get("wr_limit_entity")
        if limit_entity:
            target_limit = 0.0 if lock_needed else float(config.get("wr_unlock_value", 100.0))
            ent_state = self.hass.states.get(limit_entity)
            
            if ent_state and ent_state.state not in ['unknown', 'unavailable', 'none']:
                try:
                    if abs(float(ent_state.state) - target_limit) > 0.1:
                        await self.hass.services.async_call(
                            "number", "set_value", {"entity_id": limit_entity, "value": target_limit}
                        )
                        _LOGGER.info("WR-Limit angepasst -> %s (Grund: %s)", target_limit, strat)
                except ValueError: 
                    pass

        # 2. Spezifische Lade-Steuerung (Falls aktiv)
        # Hier prüfen wir, ob wir gerade im Modus "LADEN" sind
        charge_control_entity = config.get("battery_charge_switch") # Musst du ggf. in der Config anlegen
        if charge_control_entity:
            if strat == "LADEN":
                # Beispiel: Schalter einschalten oder Ladewert setzen
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": charge_control_entity}
                )
            else:
                # Sicherstellen, dass Laden aus ist, wenn nicht im Lade-Slot
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": charge_control_entity}
                )

    def _load_savings(self):
        if os.path.exists(self.savings_path):
            try:
                with open(self.savings_path, 'r') as f:
                    saved = json.load(f)
                    self.data["savings"].update(saved)
            except Exception: 
                pass

    def _save_savings_to_disk(self):
        try:
            with open(self.savings_path, 'w') as f:
                json.dump(self.data["savings"], f)
        except Exception: 
            pass