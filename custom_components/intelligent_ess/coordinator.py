import logging
import json
import os
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .scheduler import calculate_strategy
# WICHTIG: Hier 'async_get_raw_states' importieren!
from .analytics import update_forecasts_and_finances, async_get_raw_states, get_tibber_prices, get_solar_forecast
from .profile_manager import ProfileManager
from .logic_engine import ESSLogicEngine
from .strategy import evaluate_strategy

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
        
        # 1. Config aus data und options zusammenführen
        config = dict(entry.data)
        if hasattr(entry, "options"):
            config.update(entry.options)
            
        # 2. Den neuen ProfileManager mit hass und config starten (storage_path ist weg!)
        self.profile_manager = ProfileManager(hass, config)
        
        self._savings_loaded = False
        self._last_learning_date = None # Merkt sich, wann die KI zuletzt trainiert wurde
        self._update_cycles = 0         # Zähler für das Speichern der Savings
        self._switch_timers = {}        # Einschalt-Zeitpunkte der Smart-Switches

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
            "savings": {"total": 0.0, "solar": 0.0, "hold": 0.0, "load": 0.0}
        }

    async def _async_update_data(self):
        try:
            # 1. DATEI BEIM START LADEN (Finanzielle Ersparnisse)
            if not self._savings_loaded:
                await self.hass.async_add_executor_job(self._load_savings)
                self._savings_loaded = True

            # 2. KI-PROFIL TRAINIEREN (1x täglich oder beim ersten Start)
            current_date = dt_util.now().date()
            if self._last_learning_date != current_date:
                _LOGGER.debug("Starte KI-Profil-Training aus Long-Term-Statistics...")
                await self.profile_manager.async_update_learning_profile()
                self._last_learning_date = current_date

            # 3. DATEN-AKQUISE
            config = {**self.entry.data, **self.entry.options}
            # WICHTIG: Hier await nutzen!
            current = await async_get_raw_states(self.hass, config)
            if not current: 
                return self.data
                
            # 4. DELTA-BERECHNUNG (Für Analytics/Finanzen)
            deltas = {}
            house_kwh = 0.0
            
            if self.last_readings:
                # Deltas der fortlaufenden Zähler ermitteln
                deltas = {k: current[k] - self.last_readings[k] for k in current if k in self.last_readings}
                
                # Plausibilitäts-Check (Ausreißer filtern, wie vorher im ProfileManager)
                if not any(v < -0.001 or v > 1.2 for v in deltas.values()):
                    house_kwh = max(0, (
                        deltas.get("pv", 0) + deltas.get("grid_in", 0) + 
                        deltas.get("bat_dis", 0) - deltas.get("grid_out", 0) - 
                        deltas.get("bat_chg", 0)
                    ))
                    self.data["house_kw"] = round(house_kwh * 60, 3) # Fürs Frontend hochrechnen

                    # Netto-Netzleistung (W): positiv = Bezug, negativ = Einspeisung/Ueberschuss
                    grid_delta_kwh = deltas.get("grid_in", 0) - deltas.get("grid_out", 0)
                    self.data["net_watt"] = round(grid_delta_kwh * 60000, 1)
                else:
                    _LOGGER.warning("Unplausible Zähler-Deltas erkannt. Überspringe Finanzen für diese Minute.")
                    deltas = {} 
                    house_kwh = 0.0

            # 5. ANALYTICS (Finanzen & Restbedarf-Vorhersagen aus neuem ProfileManager)
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

            # --- NEU: Tagesprofil für das Dashboard laden ---
            now = dt_util.now()
            daily_profile = self.profile_manager.get_full_day_profile(now, config.get("default_usage", 0.85))
            self.data["daily_profile"] = daily_profile
            self.data["expected_daily_total"] = round(sum(daily_profile), 2)
            # ------------------------------------------------

            # 5b. STRATEGIE-GATES (deterministisch) berechnen
            gates = self._evaluate_gates(config, current, now)
            self.data["gates"] = gates

            # 6. SCHEDULER
            # KI-Lade-Fahrplan (24h 0/1-Raster) aus Preisen, Bedarf und PV ableiten
            ai_profile = self.profile_manager.calculate_best_profile(self.data, config)
            # SmartCharge-Gate: Nur laden, wenn prognostiziert zu wenig PV kommt.
            if not gates["smartcharge_allowed"]:
                ai_profile = [0] * 24
            self.data["ai_charge_profile"] = ai_profile

            strat, msg, lock_needed = calculate_strategy(config, self.hass.states, ai_profile)

            # SmartHold-Gate (autonom): nur greifen, wenn keine hoehere Prioritaet
            # (manuelles Laden/Sperren, KI-Timer) bereits aktiv ist.
            if strat == "NORMAL" and gates["smarthold_allowed"]:
                strat = "HOLD"
                msg = (
                    f"SmartHold: Nachtreserve fehlt ({gates['nacht_defizit']} kWh), "
                    "Morgenpreis hoch – Akku wird gespart."
                )
                lock_needed = True

            self.data.update({
                "strat": strat,
                "strat_msg": msg,
                "discharge_lock_active": lock_needed,
                "fahrplan": f"Status: {strat} | Bedarf: {self.data.get('rest_demand_daily', 0)}kWh"
            })

            # 7. HARDWARE-STEUERUNG
            await self._handle_hardware_control(config, lock_needed, strat)

            # 8. REGELMÄSSIGES SPEICHERN (Alle 15 Minuten)
            self._update_cycles += 1
            if self._update_cycles >= 15:
                self._update_cycles = 0
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
            lock_value = float(config.get("wr_lock_value", 0.0))
            unlock_value = float(config.get("wr_unlock_value", 80.0))
            target_limit = lock_value if lock_needed else unlock_value
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
        charge_control_entity = config.get("battery_charge_switch")
        if charge_control_entity:
            if strat == "LADEN":
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": charge_control_entity}
                )
            else:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": charge_control_entity}
                )

        # 3. Smart-Switches: Ueberschuss-Verbraucher gestaffelt schalten
        await self._handle_smart_switches(config)

    async def _handle_smart_switches(self, config):
        switches = config.get("smart_switches") or []
        if not switches:
            return

        threshold = float(config.get("smart_switch_threshold", -1000))
        net_watt = self.data.get("net_watt", 0.0)

        actions = ESSLogicEngine.smart_switch_control(
            net_watt, threshold, self._switch_timers, switches, self.hass.states
        )

        for entity_id, action in actions:
            await self.hass.services.async_call(
                "switch", action, {"entity_id": entity_id}
            )
            if action == "turn_on":
                self._switch_timers[entity_id] = dt_util.utcnow().timestamp()
            else:
                self._switch_timers.pop(entity_id, None)
            _LOGGER.info("Smart-Switch %s -> %s (Netto: %s W)", entity_id, action, net_watt)

    def _evaluate_gates(self, config, current, now):
        """Sammelt die Basiswerte und berechnet die SmartCharge/SmartHold-Gates."""
        # Autarkie-Stunde aus der Analytics-Berechnung ableiten (Fallback 8 Uhr).
        autarky_str = self.data.get("autarky_time_tomorrow", "08:00")
        autarky_hour = 8
        if isinstance(autarky_str, str) and ":" in autarky_str:
            try:
                autarky_hour = int(autarky_str.split(":")[0])
            except ValueError:
                autarky_hour = 8

        default_usage = float(config.get("default_usage", 0.85))
        night_demand = self.profile_manager.get_night_demand(now, autarky_hour, default_usage)
        self.data["night_demand"] = night_demand

        readings = current or self.last_readings or {}
        soc = readings.get("bat_soc", 0)

        return evaluate_strategy(
            soc=soc,
            capacity=float(config.get("battery_capacity", 15.0)),
            min_soc=float(config.get("min_soc_reserve", 10.0)),
            solar_remaining=self.data.get("solar_remaining", 0.0),
            pv_tomorrow_total=self.data.get("pv_tomorrow_total", 0.0),
            night_demand=night_demand,
            expected_daily_total=self.data.get("expected_daily_total", 0.0),
            ai_price_summary=self.data.get("ai_price_summary", {}) or {},
            current_price=self.data.get("current_price", 0.0),
            price_delta_threshold=float(config.get("price_delta_threshold", 5.0)),
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