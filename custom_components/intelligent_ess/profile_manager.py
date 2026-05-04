import logging
from datetime import timedelta, datetime
from collections import defaultdict

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.recorder import get_instance
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

class ProfileManager:
    """
    Lernendes Profil-Management auf Stunden-Basis.
    Bezieht historische Daten direkt aus der Home Assistant Long-Term-Statistics (LTS) Datenbank
    und greift dabei automatisch auf die im Energy Dashboard konfigurierten Zähler zu.
    """

    def __init__(self, hass, config):
        self.hass = hass
        self.config = config
        self.learned_profile = {}

    def _get_db(self):
        return self.learned_profile

    async def async_update_learning_profile(self, days_back=28):
        _LOGGER.debug("Starte Profil-Training für die letzten %s Tage...", days_back)
        end = dt_util.utcnow()
        start = end - timedelta(days=days_back)
        
        stat_ids = set()
        sensor_map = {"grid_in": [], "grid_out": [], "pv": [], "bat_in": [], "bat_out": []}

        # 1. Sensoren automatisch aus dem Energy Dashboard ermitteln
        try:
            from homeassistant.components.energy.data import async_get_manager
            manager = await async_get_manager(self.hass)
            
            if manager and manager.data:
                prefs = manager.data
                for source in prefs.get("energy_sources", []):
                    stype = source.get("type")
                    if stype == "solar":
                        if source.get("stat_energy_from"):
                            sensor_map["pv"].append(source.get("stat_energy_from"))
                    elif stype == "grid":
                        # Home Assistant speichert das Grid-Setup manchmal als Liste (flow_from) oder direkt. Wir prüfen beides!
                        if "flow_from" in source:
                            for flow in source.get("flow_from", []):
                                if flow.get("stat_energy_from"): sensor_map["grid_in"].append(flow.get("stat_energy_from"))
                        elif "stat_energy_from" in source:
                            sensor_map["grid_in"].append(source.get("stat_energy_from"))

                        if "flow_to" in source:
                            for flow in source.get("flow_to", []):
                                if flow.get("stat_energy_to"): sensor_map["grid_out"].append(flow.get("stat_energy_to"))
                        elif "stat_energy_to" in source:
                            sensor_map["grid_out"].append(source.get("stat_energy_to"))
                            
                    elif stype == "battery":
                        if source.get("stat_energy_from"):
                            sensor_map["bat_out"].append(source.get("stat_energy_from")) # Entladen
                        if source.get("stat_energy_to"):
                            sensor_map["bat_in"].append(source.get("stat_energy_to"))    # Laden

            for ids in sensor_map.values():
                for sid in ids:
                    if sid: stat_ids.add(sid)

            _LOGGER.debug("Extrahierte Sensoren: %s", sensor_map)

        except Exception as e:
            _LOGGER.warning("Fehler beim Auslesen des Energy Dashboards: %s", e)

        if not stat_ids:
            _LOGGER.warning("Scanner hat keine Sensoren gefunden. Abbruch.")
            return

        try:
            _LOGGER.debug("Frage LTS-Datenbank ab für %s IDs...", len(stat_ids))
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                stat_ids,
                "hour",
                None,
                {"change"}
            )
            
            if not stats:
                _LOGGER.warning("Datenbankabfrage lieferte keine Daten zurück!")
                return

            self._build_profile_from_stats(stats, sensor_map)
            
        except Exception as e:
            _LOGGER.error("Fehler beim Abrufen der LTS-Daten: %s", e)

    def _build_profile_from_stats(self, stats, sensor_map):
        # Wir suchen einen Grid-Sensor, der TATSÄCHLICH Daten in der Datenbank hat
        main_grid_sensor = None
        for s in sensor_map["grid_in"]:
            if s in stats and len(stats[s]) > 0:
                main_grid_sensor = s
                break
        
        if not main_grid_sensor:
            _LOGGER.warning("Abbruch: Keiner der 'grid_in' Sensoren hat historische Daten in der LTS-Datenbank!")
            # Alternativ-Versuch: Nimm den ersten PV-Sensor, falls Netz komplett leer ist
            for s in sensor_map["pv"]:
                if s in stats and len(stats[s]) > 0:
                    main_grid_sensor = s
                    _LOGGER.warning("Nutze PV-Sensor '%s' als Notfall-Taktgeber.", main_grid_sensor)
                    break

        if not main_grid_sensor:
            _LOGGER.warning("Absoluter Abbruch: Überhaupt kein Sensor hat historische Daten!")
            return
            
        _LOGGER.debug("Nutze '%s' als Zeit-Taktgeber für die Auswertung.", main_grid_sensor)

        def get_change(s_id, t_key):
            if not s_id or s_id not in stats: return 0.0
            for entry in stats[s_id]:
                if entry.get("start") == t_key:
                    return entry.get("change", 0.0)
            return 0.0

        raw_profiles = defaultdict(lambda: defaultdict(list))
        data_points_processed = 0
        
        for entry in stats[main_grid_sensor]:
            t_key = entry["start"]
            
            ts = t_key / 1000 if t_key > 1e11 else t_key
            try:
                dt_utc = dt_util.utc_from_timestamp(ts)
                dt_local = dt_util.as_local(dt_utc)
            except Exception:
                continue

            grid_in = sum(get_change(s, t_key) for s in sensor_map["grid_in"])
            grid_out = sum(get_change(s, t_key) for s in sensor_map["grid_out"])
            bat_in = sum(get_change(s, t_key) for s in sensor_map["bat_in"])
            bat_out = sum(get_change(s, t_key) for s in sensor_map["bat_out"])
            pv_total = sum(get_change(s, t_key) for s in sensor_map["pv"])

            house_kwh = max(0.0, grid_in + pv_total + bat_out - grid_out - bat_in)

            wd = str(dt_local.weekday())
            hr = str(dt_local.hour)
            raw_profiles[wd][hr].append(house_kwh)
            data_points_processed += 1

        _LOGGER.debug("Es wurden %s Stunden-Datenpunkte verarbeitet.", data_points_processed)

        new_profile = {}
        for wd in range(7):
            new_profile[str(wd)] = {}
            for hr in range(24):
                vals = raw_profiles[str(wd)][str(hr)]
                if vals:
                    new_profile[str(wd)][str(hr)] = round(sum(vals) / len(vals), 3)
                else:
                    new_profile[str(wd)][str(hr)] = None

        self.learned_profile = new_profile
        _LOGGER.info("KI-Profil erfolgreich aus den Energie-Dashboard-Daten der letzten 28 Tage trainiert.")
        _LOGGER.debug("Beispiel-Wert gelernt (Mo 20:00 Uhr): %s kWh", self.learned_profile.get("0", {}).get("20"))

    def get_profile_value(self, dt_obj, default_usage=0.85):
        if not self.learned_profile:
            return float(default_usage)
            
        wd = str(dt_obj.weekday())
        hr = str(dt_obj.hour)
        val = self.learned_profile.get(wd, {}).get(hr)
        
        return float(val) if val is not None else float(default_usage)

    def get_daily_rest_demand(self, now, default_usage=0.85, solar_start_hour=8):
        total_rest = 0.0
        try:
            val_this_hour = self.get_profile_value(now, default_usage)
            remaining_factor = (60 - now.minute) / 60
            total_rest += val_this_hour * remaining_factor

            target_time = now + timedelta(hours=1)
            target_time = target_time.replace(minute=0, second=0, microsecond=0)

            end_time = now.replace(hour=solar_start_hour, minute=0, second=0, microsecond=0)
            if now.hour >= solar_start_hour:
                end_time += timedelta(days=1)

            while target_time < end_time:
                total_rest += self.get_profile_value(target_time, default_usage)
                target_time += timedelta(hours=1)

            return round(total_rest, 2)
        except Exception as e:
            _LOGGER.error("Fehler beim Berechnen des Restbedarfs: %s", e)
            return 5.0

    def get_hour_forecasts(self, now, default_usage=0.85):
        try:
            val0 = self.get_profile_value(now, default_usage)
            rem_factor = (60 - now.minute) / 60
            current_hour_rem = round(val0 * rem_factor, 2)

            next_time = now + timedelta(hours=1)
            next_hour_full = round(self.get_profile_value(next_time, default_usage), 2)

            return current_hour_rem, next_hour_full
        except:
            return 0.3, float(default_usage)

    def calculate_best_profile(self, data, options):
        profile = [0] * 24
        prices = data.get("prices", [])
        solar = data.get("solar_forecast", {})
        
        def_usage = float(options.get("default_usage", 0.85))
        advantage = float(options.get("charge_delta_threshold", 5.0))

        if not prices or not options.get("auto_charge_enabled", True):
            return profile

        avg_price = sum(p['total'] for p in prices[:24]) / len(prices[:24])

        for p_info in prices[:24]:
            start_time_str = p_info.get("start_time") or p_info.get("startsAt")
            p_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            p_time = dt_util.as_local(p_time)
            
            predicted_hr = self.get_profile_value(p_time, def_usage)
            solar_val = solar.get(p_time.strftime("%Y-%m-%d %H:00:00"), 0)
            net_need = predicted_hr - solar_val

            if net_need > 0 and p_info['total'] < (avg_price - advantage):
                profile[p_time.hour] = 1

        return profile
    
    def get_full_day_profile(self, now, default_usage=0.85):
        profile = []
        for hour in range(24):
            dt_target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            val = self.get_profile_value(dt_target, default_usage)
            profile.append(round(val, 3))
        return profile