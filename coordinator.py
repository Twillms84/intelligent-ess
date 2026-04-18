import logging
import json
import os
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .smart_charging import SmartCharging
from .smart_discharging import SmartDischarging
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
        self.profile_path = hass.config.path("intelligent_ess_profiles.json")
        self.savings_path = hass.config.path("intelligent_ess_savings.json")
        
        # Initialisierung der Datenstruktur mit den 4 Spar-Töpfen
        self.data = {
            "house_kw": 0.0,
            "net_watt": 0.0,
            "strat": "NORMAL",
            "strat_msg": "Initialisierung...",
            "rest_night": 0.0,
            "morning_reserve": 0.0,
            "fahrplan": "Warte auf Daten...",
            "forecast_next_hour": 0.0,
            "forecast_details": {},
            "savings": {
                "total": 0.0, 
                "solar": 0.0,   # 1. Solarersparnis
                "hold": 0.0,    # 2. Smartholdersparnis
                "load": 0.0     # 3. Smartloadersparnis
            },
            "samples": []
        }
        self._load_savings()

    def _load_savings(self):
        """Lädt Ersparnisse beim Start."""
        if os.path.exists(self.savings_path):
            try:
                with open(self.savings_path, 'r') as f:
                    saved = json.load(f)
                    # Migrations-Check: Falls alte Struktur vorhanden, Felder sicherstellen
                    for key in ["solar", "hold", "load", "total"]:
                        if key not in saved: saved[key] = 0.0
                    self.data["savings"].update(saved)
            except Exception as e:
                _LOGGER.error("Fehler beim Laden der Ersparnisse: %s", e)

    async def _async_update_data(self):
        config = {**self.entry.data, **self.entry.options}
        current = self._get_raw_states(config)
        
        if not current: return self.data

        if self.last_readings:
            deltas = {k: current[k] - self.last_readings[k] for k in current if k in self.last_readings}
            
            if any(v < -0.001 or v > 1.2 for v in deltas.values()):
                self.last_readings = current
                return self.data

            # Hausverbrauch ermitteln
            house_kwh = max(0, deltas["pv"] + deltas["grid_in"] + deltas["bat_dis"] - deltas["grid_out"] - deltas["bat_chg"])
            self.data["house_kw"] = round(house_kwh * 60, 3)
            self.data["net_watt"] = round((deltas["grid_in"] - deltas["grid_out"]) * 60000, 0)
            self.data["samples"].append(house_kwh)

            # Finanz-Update (Neu mit 4 Töpfen)
            self._update_finances(config, deltas, house_kwh)

            # Logik-Check (Forecast & Strategie)
            await self._run_logic_cycle(config, current)

            # Smart Learning & Save
            now = dt_util.now()
            if now.minute in [0, 15, 30, 45] and self.data["samples"]:
                samples_to_save = list(self.data["samples"])
                self.data["samples"] = []
                await self.hass.async_add_executor_job(
                    ProfileManager.update_profile, self.profile_path, now, samples_to_save, config
                )
                await self.hass.async_add_executor_job(self._save_savings_to_disk)

        self.last_readings = current
        return self.data

    async def _run_logic_cycle(self, config, current):
        try:
            now = dt_util.now()
            soc = current.get("bat_soc", 0)
            cap = float(config.get("battery_capacity", 15.0))
            min_soc = float(config.get("min_soc_reserve", 10.0))
            kwh_now = max(0, (cap * (soc - min_soc)) / 100)
            
            p_state = self.hass.states.get(config.get("tibber_price_sensor", ""))
            prices = p_state.attributes.get("data", []) if p_state else []

            # Detaillierter Forecast (Fenster bis morgen früh wird im ProfileManager berechnet)
            detailed_data = await self.hass.async_add_executor_job(
                ProfileManager.get_detailed_forecast, self.profile_path, now
            )
            self.data["forecast_next_hour"] = detailed_data["next_hour"]
            self.data["forecast_details"] = detailed_data["hourly_details"]
            self.data["rest_night"] = round(sum(detailed_data["hourly_details"].values()), 2)
            
            self.data["morning_reserve"] = round(self.data["rest_night"] * 0.2, 2)

            # Strategie-Entscheidung
            should_charge, c_msg = SmartCharging.calculate_charge_strategy(
                config, soc, kwh_now, self.data["rest_night"], 0.0, prices
            )
            
            if should_charge:
                self.data["strat"], self.data["strat_msg"] = "LADEN", c_msg
            else:
                strat, d_msg = SmartDischarging.calculate_discharge_strategy(
                    config, soc, kwh_now, self.data["morning_reserve"], prices
                )
                self.data["strat"], self.data["strat_msg"] = strat, d_msg

            self.data["fahrplan"] = f"Bedarf: {self.data['rest_night']}kWh | {self.data['strat']}"
        except Exception as e:
            _LOGGER.error("Fehler im Logik-Zyklus: %s", e)

    def _update_finances(self, config, deltas, house_kwh):
        """Berechnet Ersparnisse getrennt nach Solar, Hold und Load."""
        try:
            p_state = self.hass.states.get(config.get("tibber_price_sensor", ""))
            cur_p = float(p_state.state) if p_state and p_state.state not in ['unknown', 'unavailable'] else 0.3
            fin = self.data["savings"]

            # 1. Solarersparnis (PV Eigenverbrauch)
            # Was wir direkt verbraucht haben, ohne es aus dem Netz zu ziehen
            solar_kwh = max(0, house_kwh - deltas["grid_in"] - deltas["bat_dis"])
            fin["solar"] += (solar_kwh * cur_p)

            # 2. Smartholdersparnis (Verhinderter teurer Netzbezug)
            if self.data["strat"] == "HOLD":
                # Wir schätzen die Ersparnis durch das Verschieben auf die Peak-Stunde
                # Wir nehmen konservativ eine Differenz von 10 Cent an oder berechnen sie
                fin["hold"] += (house_kwh * 0.10) 

            # 3. Smartloadersparnis (Arbitrage durch Billigstrom)
            if self.data["strat"] == "LADEN":
                avg_day_price = 0.30 
                diff = max(0, avg_day_price - cur_p)
                fin["load"] += (deltas["bat_chg"] * diff)

            # 4. Summe
            fin["total"] = fin["solar"] + fin["hold"] + fin["load"]
        except:
            pass

    def _save_savings_to_disk(self):
        try:
            with open(self.savings_path, 'w') as f:
                json.dump(self.data["savings"], f)
        except: pass

    def _get_raw_states(self, config):
        try:
            pv_ids = config.get("pv_production_sensor", [])
            if isinstance(pv_ids, str): pv_ids = [pv_ids]
            return {
                "pv": sum(float(self.hass.states.get(i).state) for i in pv_ids if self._is_valid(i)),
                "grid_in": float(self.hass.states.get(config.get("grid_consumption_sensor")).state),
                "grid_out": float(self.hass.states.get(config.get("grid_export_sensor")).state),
                "bat_chg": float(self.hass.states.get(config.get("bat_charge_sensor")).state),
                "bat_dis": float(self.hass.states.get(config.get("bat_discharge_sensor")).state),
                "bat_soc": float(self.hass.states.get(config.get("battery_soc_sensor")).state)
            }
        except: return None

    def _is_valid(self, eid):
        if not eid: return False
        s = self.hass.states.get(eid)
        return s and s.state not in ['unknown', 'unavailable', 'none']