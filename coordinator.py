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
        """Zentrale Steuerung basierend auf Zeit-Slots."""
        # Wir importieren die richtige Klasse lokal, um Konflikte zu vermeiden
        from datetime import time as dt_time 
        
        try:
            now = dt_util.now()
            now_t = now.time()
            soc = current.get("bat_soc", 0)
            
            charge_switch = config.get("battery_charge_switch")
            hold_switch = config.get("battery_hold_switch")

            # --- SLOT-LOGIK (Präzise mit Time-Objekten) ---
            def is_in_slot(key_prefix):
                enabled = self.config_entry.options.get(f"{key_prefix}_enabled", False)
                if not enabled:
                    return False
                
                try:
                    # Hole ISO-Zeitstrings aus den Options (Default 00:00:00)
                    start_str = self.config_entry.options.get(f"{key_prefix}_start", "00:00:00")
                    end_str = self.config_entry.options.get(f"{key_prefix}_end", "00:00:00")
                    
                    # Umwandeln in datetime.time Objekte
                    start_t = dt_time.fromisoformat(start_str)
                    end_t = dt_time.fromisoformat(end_str)
                    
                    if start_t <= end_t:
                        # Normaler Zeitraum (z.B. 08:00 - 16:00)
                        return start_t <= now_t < end_t
                    else:
                        # Zeitraum über Mitternacht (z.B. 22:00 - 06:00)
                        return now_t >= start_t or now_t < end_t
                except (ValueError, TypeError) as err:
                    _LOGGER.error("Zeitformat-Fehler in Slot %s: %s", key_prefix, err)
                    return False

            # --- STRATEGIE ERMITTELN ---
            should_charge = is_in_slot("man_charge_s1") or is_in_slot("man_charge_s2")
            should_hold = is_in_slot("man_hold_s1")

            # Sicherheit & Priorität
            if soc >= 95:
                should_charge = False
            if should_charge:
                should_hold = False # Laden sticht Entladesperre

            # --- SCHALTVORGÄNGE (mit State-Check zur API-Schonung) ---
            if charge_switch:
                target = "turn_on" if should_charge else "turn_off"
                curr = self.hass.states.get(charge_switch)
                if curr and curr.state != ("on" if should_charge else "off"):
                    _LOGGER.info("Schalte Laden %s", target)
                    await self.hass.services.async_call("switch", target, {"entity_id": charge_switch})

            if hold_switch:
                target = "turn_on" if should_hold else "turn_off"
                curr = self.hass.states.get(hold_switch)
                if curr and curr.state != ("on" if should_hold else "off"):
                    _LOGGER.info("Schalte Entladesperre %s", target)
                    await self.hass.services.async_call("switch", target, {"entity_id": hold_switch})

            # Daten für Sensoren setzen
            self.data["active_strategy"] = "Laden" if should_charge else ("Sperre" if should_hold else "Normal")

        except Exception as e:
            _LOGGER.error("Fehler im ESS Logic Cycle: %s", e)

            # --- SCHALTVORGÄNGE AUSFÜHREN ---
            
            # Lade-Schalter steuern
            if charge_switch:
                target_charge_state = "turn_on" if should_charge else "turn_off"
                current_charge_state = self.hass.states.get(charge_switch)
                
                if current_charge_state and current_charge_state.state != ("on" if should_charge else "off"):
                    _LOGGER.info("Schalte Laden: %s (Grund: Slot aktiv oder KI-Vorgabe)", target_charge_state)
                    await self.hass.services.async_call(
                        "switch", target_charge_state, {"entity_id": charge_switch}
                    )

            # Hold-Schalter (Entladesperre) steuern
            if hold_switch:
                target_hold_state = "turn_on" if should_hold else "turn_off"
                current_hold_state = self.hass.states.get(hold_switch)

                if current_hold_state and current_hold_state.state != ("on" if should_hold else "off"):
                    _LOGGER.info("Schalte Entladesperre: %s", target_hold_state)
                    await self.hass.services.async_call(
                        "switch", target_hold_state, {"entity_id": hold_switch}
                    )

            # --- STATUS FÜR SENSOR UPDATEN ---
            self.data["active_strategy"] = "Laden" if should_charge else ("Sperre" if should_hold else "Normal")
            self.data["last_logic_run"] = current_time_str

        except Exception as e:
            _LOGGER.error("Fehler im Intelligent ESS Logic Cycle: %s", e)

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