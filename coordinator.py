import logging
import json
import os
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
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
            # 1. Datei beim ersten Durchlauf sicher laden
            if not getattr(self, '_savings_loaded', False):
                await self.hass.async_add_executor_job(self._load_savings)
                self._savings_loaded = True

            # 2. Config sicher zusammenführen
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

                # --- START SMART DISCHARGE LOGIK ---                
                # 1. Status des Master-Switches und der manuellen Timer abrufen
                # Die IDs basieren auf deiner Integration 'intelligent_ess'
                domain = "intelligent_ess" # Der Name deiner Integration
                switch_id = f"switch.{domain}_man_hold_s1_enabled"
                start_id  = f"time.{domain}_man_hold_s1_start"
                end_id    = f"time.{domain}_man_hold_s1_end"

                switch_state = self.hass.states.get(switch_id)
                # Master-Aktivierung: Nur wenn Switch existiert UND auf 'on' steht
                is_enabled = switch_state is not None and switch_state.state == "on"

                # Manuelle Timer sicher auslesen
                s1_start = get_timer_value(self.hass, start_id)
                s1_end   = get_timer_value(self.hass, end_id)

                # Liste der aktiven Timer erstellen
                active_timers = []
                if s1_start and s1_end:
                    active_timers.append({"start": s1_start, "end": s1_end})

                # KI-Timer hinzufügen (falls im Speicher vorhanden)
                ai_timers = self.data.get("ai_timers", [])
                active_timers.extend(ai_timers)

                # 2. Daten für die Logik-Datei (smart_discharging.py) schnüren
                logic_input = {
                    "smart_discharge_enabled": is_enabled,
                    "discharge_timers": active_timers
                }
                
                # Strategie berechnen (inkl. Mitternachts-Check)
                result = calculate_discharge_strategy(logic_input)
                
                # Status für Dashboard-Sensoren speichern
                self.data["discharge_lock_active"] = result["discharge_locked"]
                self.data["discharge_lock_reason"] = result["reason"]

                # 3. Hardware-Steuerung: Wechselrichter-Limit setzen
                limit_entity = config.get("wr_limit_entity")
                unlock_value = config.get("wr_unlock_value", 80)

                if limit_entity:
                    target_val = 0.0 if result["discharge_locked"] else float(unlock_value)
                    current_limit_state = self.hass.states.get(limit_entity)
                    
                    # Nur senden, wenn die Entität existiert und der Wert abweicht
                    if current_limit_state:
                        try:
                            if float(current_limit_state.state) != target_val:
                                _LOGGER.info(
                                    "Intelligent ESS Schaltung: %s auf %s W (Grund: %s)", 
                                    limit_entity, target_val, result["reason"]
                                )
                                await self.hass.services.async_call(
                                    "number", "set_value",
                                    {"entity_id": limit_entity, "value": target_val}
                                )
                        except ValueError:
                            _LOGGER.error("Ungültiger numerischer Zustand für %s", limit_entity)
                # --- ENDE SMART DISCHARGE LOGIK ---

                # Logik-Check (Forecast & Strategie - hier werden ggf. die ai_timers befüllt)
                await self._run_logic_cycle(config, current)

                # Smart Learning & Save
                now = dt_util.now()
                if len(self.data["samples"]) >= 15:
                    samples_to_save = list(self.data["samples"])
                    self.data["samples"] = []
                    
                    await self.hass.async_add_executor_job(
                        self.profile_manager.update_profile, now, samples_to_save, config
                    )
                    await self.hass.async_add_executor_job(self._save_savings_to_disk)

            self.last_readings = current
            return self.data

        except Exception as e:
            _LOGGER.error("Fehler im Update-Zyklus (_async_update_data): %s", e)
            raise UpdateFailed(f"Update fehlerhaft: {e}")

    async def _run_logic_cycle(self, config, current):
        try:
            now = dt_util.now()
            soc = current.get("bat_soc", 0)
            cap = float(config.get("battery_capacity", 15.0))
            min_soc = float(config.get("min_soc_reserve", 10.0))
            kwh_now = max(0, (cap * (soc - min_soc)) / 100)
            
            # --- 1. Tibber-Preise sicher abrufen ---
            # Nutzt die etablierte Methode, die 'today' und 'tomorrow' ausliest
            # und das Abstürzen bei leeren Listen verhindert.
            prices = await self._get_tibber_prices()

            # --- 2. Detaillierter Forecast über die neuen Funktionen ---
            # WICHTIG: Wir rufen self.profile_manager (die Instanz) auf, nicht die Klasse!
            # async_add_executor_job verlagert den Datei/DB-Zugriff, damit HA nicht blockiert.
            default_usage = float(config.get("default_usage", 0.85))
            
            rest_daily = await self.hass.async_add_executor_job(
                self.profile_manager.get_daily_rest_demand, now, default_usage
            )
            self.data["rest_demand_daily"] = rest_daily
            self.data["morning_reserve"] = round(rest_daily * 0.2, 2)

            cur_rem, next_full = await self.hass.async_add_executor_job(
                self.profile_manager.get_hour_forecasts, now
            )
            self.data["forecast_current_hour"] = cur_rem
            self.data["forecast_next_hour"] = next_full
            # -------------------------------------------------------------

            # --- 3. Solar-Prognose abrufen ---
            # Nutzt unsere sichere Float-Methode ohne manuelles try/except
            solar_fc_entity_id = config.get("solar_forecast_sensor", "")
            solar_fc = self._get_safe_float(solar_fc_entity_id)

            # --- 4. Strategie-Entscheidung ---
            logic_input = {
                "smart_discharge_enabled": is_enabled,
                "discharge_timers": active_timers
            }
            
            should_charge, c_msg = calculate_smart_charge(
                config, soc, kwh_now, rest_daily, solar_fc, prices
            )
            
            if should_charge:
                self.data["strat"], self.data["strat_msg"] = "LADEN", c_msg
            else:
                # 2. Prüfen, ob entladen gesperrt werden soll (Smart Discharging)
                # Hier rufen wir die neue Logik-Funktion auf
                # Wir übergeben hier das logic_input Paket von vorhin oder die Einzelwerte
                res = calculate_discharge_strategy(logic_input)
                
                self.data["strat"] = "SPERRE" if res["discharge_locked"] else "AUTO"
                self.data["strat_msg"] = res["reason"]

            self.data["fahrplan"] = f"Bedarf heute: {rest_daily} kWh | {self.data['strat']}"
            
        except Exception as e:
            _LOGGER.error("Fehler im Logik-Zyklus: %s", e)  
    
    async def _get_tibber_prices(self, config=None):
        """Holt die Strompreise aus dem get_chartdata Sensor-Attribut 'data'."""
        # Wenn keine Config übergeben wird, bauen wir sie uns selbst aus dem Eintrag:
        if config is None:
            config = {**self.entry.data, **self.entry.options}
            
        # Sensor-ID aus der Config holen.
        sensor_id = config.get("tibber_export_sensor")
        
        prices = []
        state_obj = self.hass.states.get(sensor_id)
        
        if state_obj and state_obj.state not in ['unknown', 'unavailable', 'none', 'pending', 'error']:
            # Prüfen, ob das Attribut 'data' existiert
            if 'data' in state_obj.attributes:
                raw_data = state_obj.attributes['data']
                
                for entry in raw_data:
                    prices.append({
                        "start_time": entry.get("start_time"),
                        "price_per_kwh": entry.get("price_per_kwh"),
                        "startsAt": entry.get("start_time"),
                        "total": entry.get("price_per_kwh")
                    })
                    
        return prices
    
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
        # Hilfsfunktion für absolut sicheres Auslesen eines Sensors
        def _get_safe_value(config_key):
            entity_id = config.get(config_key)
            if not entity_id:
                return 0.0  # Nicht konfiguriert
                
            state_obj = self.hass.states.get(entity_id)
            # Prüfen, ob das Objekt existiert und der Status gültig ist
            if state_obj and state_obj.state not in ['unknown', 'unavailable', 'none']:
                try:
                    return float(state_obj.state)
                except ValueError:
                    return 0.0
            
            # Optional: Hier könnte man loggen, welcher Sensor genau fehlt
            # _LOGGER.warning("Sensor %s (%s) nicht gefunden oder unavailable!", config_key, entity_id)
            return 0.0

        try:
            # 1. PV-Sensoren sicher abfragen (da es eine Liste sein kann)
            pv_ids = config.get("pv_production_sensor", [])
            if isinstance(pv_ids, str): 
                pv_ids = [pv_ids]
            
            pv_total = 0.0
            for i in pv_ids:
                if i and self._is_valid(i):
                    s_obj = self.hass.states.get(i)
                    if s_obj and s_obj.state not in ['unknown', 'unavailable', 'none']:
                        try:
                            pv_total += float(s_obj.state)
                        except ValueError:
                            pass

            # 2. Werte sicher in das Dictionary laden
            return {
                "pv": pv_total,
                "grid_in": _get_safe_value("grid_consumption_sensor"),
                "grid_out": _get_safe_value("grid_export_sensor"),
                "bat_chg": _get_safe_value("bat_charge_sensor"),
                "bat_dis": _get_safe_value("bat_discharge_sensor"),
                "bat_soc": _get_safe_value("battery_soc_sensor")
            }

        except Exception as e:
            _LOGGER.error("Allgemeiner Fehler bei Sensorabfrage: %s", e)
            return None

    def _is_valid(self, eid):
        if not eid: return False
        s = self.hass.states.get(eid)
        return s and s.state not in ['unknown', 'unavailable', 'none']
    
    def _get_safe_float(self, entity_id):
        """Holt einen Sensorwert abhörsicher ohne NoneType-Crash."""
        if not entity_id:
            return 0.0
            
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ["unknown", "unavailable"]:
            return 0.0
            
        try:
            return float(state.state)
        except ValueError:
            return 0.0