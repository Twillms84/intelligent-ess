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
        self._savings_loaded = False

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
        
        if not self._savings_loaded:
            await self.hass.async_add_executor_job(self._load_savings)
            self._savings_loaded = True
        
        config = {**self.entry.data, **self.entry.options}
        current = self._get_raw_states(config)
        
        if not current: 
            return self.data

        if self.last_readings:
            deltas = {k: current[k] - self.last_readings[k] for k in current if k in self.last_readings}
            
            # Ausreißer ignorieren
            if any(v < -0.001 or v > 1.2 for v in deltas.values()):
                self.last_readings = current
                return self.data

            # Hausverbrauch ermitteln
            house_kwh = max(0, deltas["pv"] + deltas["grid_in"] + deltas["bat_dis"] - deltas["grid_out"] - deltas["bat_chg"])
            self.data["house_kw"] = round(house_kwh * 60, 3)
            self.data["net_watt"] = round((deltas["grid_in"] - deltas["grid_out"]) * 60000, 0)
            
            # Sample hinzufügen
            self.data["samples"].append(house_kwh)

            # Finanz-Update
            self._update_finances(config, deltas, house_kwh)

            # Logik-Check (Forecast & Strategie)
            await self._run_logic_cycle(config, current)

            # Smart Learning & Save (Trigger über Menge, nicht über Zeit!)
            now = dt_util.now()
            if len(self.data["samples"]) >= 15:
                samples_to_save = list(self.data["samples"])
                self.data["samples"] = []
                
                _LOGGER.debug("Speichere %s Samples ins Profil", len(samples_to_save))
                
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
            
            # --- TIBBER PREIS DATA FIX ---
            price_entity = config.get("tibber_export_sensor")
            self.data["prices_raw"] = []
            
            if price_entity:
                p_state = self.hass.states.get(price_entity)
                if p_state:
                    prices_list = p_state.attributes.get("data", [])
                    if isinstance(prices_list, list) and len(prices_list) > 0:
                        self.data["prices_raw"] = prices_list
                        _LOGGER.info("COORDINATOR: %s Preis-Slots aus %s geladen.", len(prices_list), price_entity)
                    else:
                        _LOGGER.warning("COORDINATOR: Attribut 'data' in %s ist leer!", price_entity)
            
            prices = self.data["prices_raw"]

            # --- RESTLICHE LOGIK ---
            options = self.config_entry.options
            entry_data = self.config_entry.data
            fallback_hourly = float(options.get("default_usage", entry_data.get("default_usage", 0.6)))

            # 1. Täglicher Restbedarf (rollierend 24h)
            rest_daily = await self.hass.async_add_executor_job(
                ProfileManager.get_daily_rest_demand, self.profile_path, now, fallback_hourly
            )
            self.data["rest_demand_daily"] = round(rest_daily, 2)
            self.data["morning_reserve"] = round(rest_daily * 0.2, 2)

            # 2. Stunden-Forecasts
            cur_rem, next_full = await self.hass.async_add_executor_job(
                ProfileManager.get_hour_forecasts, self.profile_path, now, fallback_hourly
            )
            self.data["forecast_current_hour"] = round(cur_rem, 2)
            self.data["forecast_next_hour"] = round(next_full, 2)

            # Solar-Prognose abrufen
            solar_fc = 0.0
            solar_fc_entity_id = config.get("solar_forecast_sensor", "")
            if solar_fc_entity_id:
                fc_state = self.hass.states.get(solar_fc_entity_id)
                if fc_state and fc_state.state not in ['unknown', 'unavailable', 'none']:
                    try:
                        solar_fc = float(fc_state.state) 
                    except ValueError: pass

            # Strategie-Entscheidung (Interne Logik)
            should_charge, c_msg = SmartCharging.calculate_charge_strategy(
                config, soc, kwh_now, rest_daily, solar_fc, prices
            )
            
            if should_charge:
                self.data["strat"], self.data["strat_msg"] = "LADEN", c_msg
            else:
                strat, d_msg = SmartDischarging.calculate_discharge_strategy(
                    config, soc, kwh_now, self.data["morning_reserve"], prices
                )
                self.data["strat"], self.data["strat_msg"] = strat, d_msg

            self.data["fahrplan"] = f"Bedarf 24h: {self.data['rest_demand_daily']}kWh | PV: {round(solar_fc,1)}kWh"

            # --- AUTOMATISCHE KI-AUSFÜHRUNG ---
            ki_decision = self.data.get("ki_charge_decision", "NO")
            ki_start_time = self.data.get("ki_charge_start", "00:00")
            
            # 1. Prüfen, ob der Nutzer die Automatik erlaubt hat
            # Erstelle in HA einen 'input_boolean.intelligent_ess_automatik'
            auto_allowed = self.hass.states.get("input_boolean.intelligent_ess_automatik")
            is_auto_on = auto_allowed and auto_allowed.state == "on"

            if ki_decision == "YES" and is_auto_on:
                current_time = now.strftime("%H:%M")
                
                # Vergleich: Ist es Zeit zu laden?
                if current_time == ki_start_time:
                    charge_switch = config.get("battery_charge_switch")
                    if charge_switch:
                        _LOGGER.warning("AUTOPILOT AKTIV: %s", self.data.get("ki_reason", "KI-Entscheidung"))
                        await self.hass.services.async_call("switch", "turn_on", {"entity_id": charge_switch})
            # Automatischer Stopp bei vollem Akku
            if soc >= 95 and charge_switch:
                s_state = self.hass.states.get(charge_switch)
                if s_state and s_state.state == "on":
                    _LOGGER.info("KI-AUTOPILOT: Akku voll (%s%%), schalte Laden aus.", soc)
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": charge_switch})

            # --- AUTOMATISCHER KI-TRIGGER (Jeden Tag um 14:05 Uhr) ---
            if now.hour == 14 and now.minute == 5:
                # Wir lösen den Button-Press-Service aus
                button_entity = f"button.{self.config_entry.entry_id}_ki_button"
                _LOGGER.info("KI-AUTOPILOT: Trigger tägliche Analyse via %s", button_entity)
                await self.hass.services.async_call("button", "press", {"entity_id": button_entity})

        except Exception as e:
            _LOGGER.error("Fehler im Logik-Zyklus: %s", e)

    def _update_finances(self, config, deltas, house_kwh):
        """Berechnet Ersparnisse getrennt nach Solar, Hold und Load."""
        try:
            p_state = self.hass.states.get(config.get("tibber_price_sensor", ""))
            cur_p = float(p_state.state) if p_state and p_state.state not in ['unknown', 'unavailable'] else 0.30
            prices = p_state.attributes.get("data", []) if p_state else []
            fin = self.data["savings"]

            # 1. Solarersparnis (PV Eigenverbrauch)
            eigenverbrauch_kwh = max(0, house_kwh - deltas["grid_in"])
            fin["solar"] += (eigenverbrauch_kwh * cur_p)

            # 2. Smartholdersparnis (Verhinderter teurer Netzbezug)
            if self.data["strat"] == "HOLD":
                future_prices = [p.get('price_per_kwh', p.get('price', cur_p)) for p in prices[1:13]]
                max_future_p = max(future_prices) if future_prices else cur_p
                hold_diff = max(0, max_future_p - cur_p)
                fin["hold"] += (house_kwh * hold_diff)

            # 3. Smartloadersparnis (Arbitrage durch Billigstrom)
            if self.data["strat"] == "LADEN":
                day_prices = [p.get('price_per_kwh', p.get('price', cur_p)) for p in prices[:24]]
                avg_day_price = sum(day_prices) / len(day_prices) if day_prices else 0.30
                load_diff = max(0, avg_day_price - cur_p)
                fin["load"] += (deltas["bat_chg"] * load_diff)

            # 4. Summe
            fin["total"] = fin["solar"] + fin["hold"] + fin["load"]
        except Exception as e:
            _LOGGER.error("Fehler beim Finanz-Update: %s", e)

    def _save_savings_to_disk(self):
        try:
            with open(self.savings_path, 'w') as f:
                json.dump(self.data["savings"], f)
        except: pass

    def _get_raw_states(self, config):
        try:
            # Hilfsfunktion zum sicheren Auslesen
            def safe_float(entity_id):
                if not entity_id:
                    return 0.0
                state_obj = self.hass.states.get(entity_id)
                if state_obj and state_obj.state not in ['unknown', 'unavailable', 'none']:
                    try:
                        return float(state_obj.state)
                    except ValueError:
                        return 0.0
                return 0.0

            pv_ids = config.get("pv_production_sensor", [])
            if isinstance(pv_ids, str): pv_ids = [pv_ids]
            
            return {
                "pv": sum(safe_float(i) for i in pv_ids),
                "grid_in": safe_float(config.get("grid_consumption_sensor")),
                "grid_out": safe_float(config.get("grid_export_sensor")),
                "bat_chg": safe_float(config.get("bat_charge_sensor")),
                "bat_dis": safe_float(config.get("bat_discharge_sensor")),
                "bat_soc": safe_float(config.get("battery_soc_sensor"))
            }
        except Exception as e:
            _LOGGER.error("Allgemeiner Fehler bei Sensorabfrage: %s", e)
            return None

    def _is_valid(self, eid):
        if not eid: return False
        s = self.hass.states.get(eid)
        return s and s.state not in ['unknown', 'unavailable', 'none']