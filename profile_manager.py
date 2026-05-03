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
    Bezieht historische Daten direkt aus der Home Assistant Long-Term-Statistics (LTS) Datenbank,
    sodass keine lokale JSON-Datei oder manuelle Delta-Berechnung mehr nötig ist.
    """

    def __init__(self, hass, config):
        self.hass = hass
        self.config = config
        self.learned_profile = {}  # Wird beim asynchronen Update gefüllt

    async def async_update_learning_profile(self, days_back=28):
        """
        Holt die stündlichen LTS-Daten der letzten 28 Tage (4 Wochen) aus der 
        Energie-Datenbank und berechnet den durchschnittlichen Hausverbrauch 
        für jeden Wochentag und jede Stunde.
        (Sollte idealerweise 1x täglich nachts aufgerufen werden)
        """
        end = dt_util.utcnow()
        start = end - timedelta(days=days_back)
        
        # 1. Alle relevanten Sensoren aus der Config sammeln
        stat_ids = set()
        for key in ["grid_consumption_sensor", "grid_export_sensor", "bat_charge_sensor", "bat_discharge_sensor"]:
            sensor = self.config.get(key)
            if sensor:
                stat_ids.add(sensor)
                
        pv_sensors = self.config.get("pv_production_sensor", [])
        if isinstance(pv_sensors, list):
            stat_ids.update(pv_sensors)
        elif pv_sensors:
            stat_ids.add(pv_sensors)

        if not stat_ids:
            _LOGGER.warning("Keine Sensoren konfiguriert. Kann kein Profil lernen.")
            return

        try:
            # 2. Asynchrone Datenbank-Abfrage der Stunden-Deltas ("change")
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
            
            # 3. Das Profil aus den abgefragten Daten bauen
            self._build_profile_from_stats(stats)
            _LOGGER.info("KI-Profil erfolgreich aus den Energie-Dashboard-Daten der letzten %s Tage trainiert.", days_back)
            
        except Exception as e:
            _LOGGER.error("Fehler beim Abrufen der LTS-Daten für das Profil: %s", e)

    def _build_profile_from_stats(self, stats):
        """Interne Methode: Verarbeitet die rohen DB-Statistiken zu einem sauberen Stunden-Profil."""
        grid_in_sensor = self.config.get("grid_consumption_sensor")
        if not grid_in_sensor or grid_in_sensor not in stats:
            return

        def get_change(s_id, t_key):
            """Hilfsfunktion, um den 'change' Wert (kWh Zuwachs) für einen Zeitstempel zu holen."""
            if not s_id or s_id not in stats: return 0.0
            for entry in stats[s_id]:
                if entry.get("start") == t_key:
                    return entry.get("change", 0.0)
            return 0.0

        raw_profiles = defaultdict(lambda: defaultdict(list))
        
        pv_sensors = self.config.get("pv_production_sensor", [])
        if not isinstance(pv_sensors, list):
            pv_sensors = [pv_sensors] if pv_sensors else []

        # Wir nehmen den Netzbezug als Taktgeber für die verfügbaren Stunden
        for entry in stats[grid_in_sensor]:
            t_key = entry["start"]
            
            # Zeitstempel umwandeln (HA speichert meist ms als Timestamp)
            ts = t_key / 1000 if t_key > 1e11 else t_key
            try:
                dt_utc = dt_util.utc_from_timestamp(ts)
                dt_local = dt_util.as_local(dt_utc)
            except Exception:
                continue

            # Die Werte für diese exakte Stunde holen
            grid_in = get_change(self.config.get("grid_consumption_sensor"), t_key)
            grid_out = get_change(self.config.get("grid_export_sensor"), t_key)
            bat_in = get_change(self.config.get("bat_charge_sensor"), t_key)
            bat_out = get_change(self.config.get("bat_discharge_sensor"), t_key)
            pv_total = sum(get_change(s, t_key) for s in pv_sensors)

            # Echter Hausverbrauch in dieser Stunde:
            house_kwh = max(0.0, grid_in + pv_total + bat_out - grid_out - bat_in)

            # Einsortieren nach Wochentag (0-6) und Stunde (0-23)
            wd = str(dt_local.weekday())
            hr = str(dt_local.hour)
            raw_profiles[wd][hr].append(house_kwh)

        # Durchschnittswerte berechnen und speichern
        new_profile = {}
        for wd in range(7):
            new_profile[str(wd)] = {}
            for hr in range(24):
                vals = raw_profiles[str(wd)][str(hr)]
                if vals:
                    new_profile[str(wd)][str(hr)] = round(sum(vals) / len(vals), 3)
                else:
                    new_profile[str(wd)][str(hr)] = None  # None triggert später den default_usage

        self.learned_profile = new_profile

    def get_profile_value(self, dt_obj, default_usage=0.85):
        """Hilfsfunktion, um den gelernten Wert für ein beliebiges datetime-Objekt sicher auszulesen."""
        if not self.learned_profile:
            return default_usage
            
        wd = str(dt_obj.weekday())
        hr = str(dt_obj.hour)
        val = self.learned_profile.get(wd, {}).get(hr)
        
        return float(val) if val is not None else default_usage

    def get_daily_rest_demand(self, now, default_usage=0.85, solar_start_hour=8):
        """Berechnet den Restbedarf von JETZT bis zum nächsten Sonnenaufgang (z.B. 08:00 Uhr)."""
        total_rest = 0.0
        try:
            # 1. Rest der aktuellen Stunde berechnen (anteilig der verbleibenden Minuten)
            val_this_hour = self.get_profile_value(now, default_usage)
            remaining_factor = (60 - now.minute) / 60
            total_rest += val_this_hour * remaining_factor

            # 2. Stunden bis zum Ziel (nächster Morgen)
            target_time = now + timedelta(hours=1)
            target_time = target_time.replace(minute=0, second=0, microsecond=0)

            # Endzeitpunkt festlegen (entweder heute oder morgen 08:00 Uhr)
            end_time = now.replace(hour=solar_start_hour, minute=0, second=0, microsecond=0)
            if now.hour >= solar_start_hour:
                end_time += timedelta(days=1)

            # Iteriere über den Tageswechsel hinweg, bis das Ziel erreicht ist
            while target_time < end_time:
                total_rest += self.get_profile_value(target_time, default_usage)
                target_time += timedelta(hours=1)

            return round(total_rest, 2)
        except Exception as e:
            _LOGGER.error("Fehler beim Berechnen des Restbedarfs: %s", e)
            return 5.0

    def get_hour_forecasts(self, now, default_usage=0.85):
        """Liefert Forecast für aktuelle Stunde (herunterlaufend) und nächste Stunde (voll)."""
        try:
            # Aktuelle Stunde (anteilig)
            val0 = self.get_profile_value(now, default_usage)
            rem_factor = (60 - now.minute) / 60
            current_hour_rem = round(val0 * rem_factor, 2)

            # Nächste Stunde
            next_time = now + timedelta(hours=1)
            next_hour_full = round(self.get_profile_value(next_time, default_usage), 2)

            return current_hour_rem, next_hour_full
        except:
            return 0.3, default_usage

    def calculate_best_profile(self, data, options):
        """
        Diese Methode verknüpft die historischen Daten mit der Tibber-Logik,
        um den Ladeplan für die Batterie zu erstellen.
        """
        profile = [0] * 24
        prices = data.get("prices", [])
        solar = data.get("solar_forecast", {})
        
        def_usage = float(options.get("default_usage", 0.85))
        advantage = float(options.get("charge_delta_threshold", 5.0))

        if not prices or not options.get("auto_charge_enabled", True):
            return profile

        avg_price = sum(p['total'] for p in prices[:24]) / len(prices[:24])

        for p_info in prices[:24]:
            # Tibber Zeit in lokales datetime Objekt umwandeln
            p_time = datetime.fromisoformat(p_info['startsAt'].replace("Z", "+00:00"))
            p_time = dt_util.as_local(p_time)
            
            # Gelernten Bedarf für diese Stunde holen
            predicted_hr = self.get_profile_value(p_time, def_usage)
            
            # Solarprognose abziehen
            solar_val = solar.get(p_time.strftime("%Y-%m-%d %H:00:00"), 0)
            net_need = predicted_hr - solar_val

            # Wenn wir nach PV noch Strom brauchen UND der Preis günstig ist -> Lade-Flag setzen
            if net_need > 0 and p_info['total'] < (avg_price - advantage):
                profile[p_time.hour] = 1

        return profile
    
    def get_full_day_profile(self, now, default_usage=0.85):
        """
        Gibt die Prognose für alle 24 Stunden des übergebenen Tages 
        als Liste zurück (Index 0 = 00:00 Uhr, Index 23 = 23:00 Uhr).
        """
        profile = []
        for hour in range(24):
            # Einen Zeitstempel für jede Stunde des aktuellen Tages bauen
            dt_target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            val = self.get_profile_value(dt_target, default_usage)
            profile.append(round(val, 3))
        return profile