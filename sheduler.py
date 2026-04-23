import datetime
import logging

_LOGGER = logging.getLogger(__name__)

def calculate_strategy(options, hass_states):
    """
    Zentrale Entscheidungslogik. 
    Wird vom Coordinator jede Minute aufgerufen.
    """
    try:
        now = datetime.datetime.now().time()
        
        # 1. Timer aus den Hass-States auslesen
        # (Hier musst du sicherstellen, dass die Entity-IDs zu deinen Helfern passen)
        def is_timer_active(start_key, end_key):
            start_time = options.get(start_key)
            end_time = options.get(end_key)
            if not start_time or not end_time:
                return False
            
            # Umwandlung der Strings in Zeit-Objekte
            try:
                s = datetime.time(*map(int, start_time.split(':')[:2]))
                e = datetime.time(*map(int, end_time.split(':')[:2]))
                if s <= e:
                    return s <= now <= e
                else: # Über Mitternacht
                    return now >= s or now <= e
            except:
                return False

        # 2. Prioritäten prüfen
        # Prio 1: Laden (SmartLoader)
        if is_timer_active("charge_start_time", "charge_end_time"):
            return "LADEN", "SmartLoader aktiv (Timer)", False

        # Prio 2: Sperren (SmartHolder / Entladestopp)
        if is_timer_active("hold_start_time", "hold_end_time"):
            return "SPERRE", "SmartHolder aktiv (Timer)", True

        # Prio 3: Standard (Normalbetrieb)
        return "AUTO", "Normalbetrieb (PV/Batterie)", False

    except Exception as e:
        _LOGGER.error("Fehler im Scheduler: %s", e)
        return "AUTO", f"Fehler: {e}", False
        
async def _async_update_data(self):
        """Zentraler Update-Zyklus."""
        try:
            # 1. Setup & Datenladen
            if not getattr(self, '_savings_loaded', False):
                await self.hass.async_add_executor_job(self._load_savings)
                self._savings_loaded = True

            config = {**self.entry.data, **self.entry.options}
            current = self._get_raw_states(config)
            if not current: return self.data

            now = dt_util.now()

            # 2. FINANZEN & VERBRAUCH
            if self.last_readings:
                deltas = {k: current[k] - self.last_readings[k] for k in current if k in self.last_readings}
                if not any(v < -0.001 or v > 1.2 for v in deltas.values()):
                    house_kwh = max(0, deltas["pv"] + deltas["grid_in"] + deltas["bat_dis"] - deltas["grid_out"] - deltas["bat_chg"])
                    self.data["house_kw"] = round(house_kwh * 60, 3)
                    self.data["samples"].append(house_kwh)
                    self._update_finances(config, deltas, house_kwh)

            # 3. FORECASTS (Wiederhergestellt)
            rest_daily = await self.hass.async_add_executor_job(lambda: self.profile_manager.get_daily_rest_demand(now))
            cur_rem, next_full = await self.hass.async_add_executor_job(lambda: self.profile_manager.get_hour_forecasts(now))
            
            prices = await self._get_tibber_prices()
            
            self.data.update({
                "rest_demand_daily": round(rest_daily, 2),
                "forecast_current_hour": round(cur_rem, 3),
                "forecast_next_hour": round(next_full, 3),
                "prices": prices,
                "morning_reserve": round(rest_daily * 0.2, 2)
            })

            # 4. STRATEGIE ÜBER SCHEDULER BERECHNEN
            strat, msg, lock_needed = calculate_strategy(self.entry.options, self.hass.states)
            
            self.data["strat"] = strat
            self.data["strat_msg"] = msg
            self.data["discharge_lock_active"] = lock_needed
            self.data["fahrplan"] = f"Status: {strat} | Bedarf: {round(rest_daily, 1)}kWh"

            # 5. HARDWARE-STEUERUNG (Sicher & Bus-schonend)
            limit_entity = config.get("wr_limit_entity")
            if limit_entity:
                unlock_val = float(config.get("wr_unlock_value", 100.0))
                target_limit = 0.0 if lock_needed else unlock_val
                
                ent_state = self.hass.states.get(limit_entity)
                if ent_state and ent_state.state not in ['unknown', 'unavailable', 'none']:
                    try:
                        current_val = float(ent_state.state)
                        if abs(current_val - target_limit) > 0.1:
                            await self.hass.services.async_call(
                                "number", "set_value", 
                                {"entity_id": limit_entity, "value": target_limit}
                            )
                            _LOGGER.info("WR-Limit angepasst: %s -> %s (%s)", current_val, target_limit, strat)
                    except ValueError: pass

            # 6. STATUS-EVENT LOGGING
            if strat != self.data.get("last_active_strat"):
                self.data["last_event"] = f"[{now.strftime('%H:%M:%S')}] {strat}: {msg}"
                self.data["last_active_strat"] = strat

            # 7. SPEICHERN & LEARNING
            if len(self.data["samples"]) >= 15:
                samples_to_save = list(self.data["samples"])
                self.data["samples"] = []
                await self.hass.async_add_executor_job(self.profile_manager.update_profile, now, samples_to_save, config)
                await self.hass.async_add_executor_job(self._save_savings_to_disk)

            self.last_readings = current
            return self.data

        except Exception as e:
            _LOGGER.error("Schwerer Fehler im Coordinator-Update: %s", e)
            raise UpdateFailed(f"Update fehlgeschlagen: {e}")