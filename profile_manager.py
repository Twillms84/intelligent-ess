import json
import os
import logging
from datetime import timedelta, datetime

_LOGGER = logging.getLogger(__name__)

class ProfileManager:
    """
    Lernendes Profil-Management auf Viertelstunden-Basis.
    Berechnet Restbedarf und Prognosen basierend auf historischem Gewicht.
    """

    def __init__(self, storage_path):
        self.path = os.path.join(storage_path, "usage_stats.json")

    def _get_db(self):
        """Lädt die Datenbank sicher."""
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                _LOGGER.error("Fehler beim Laden der Profildaten: %s", e)
        return {}

    def get_daily_rest_demand(self, now, default_usage=0.6):
        """Berechnet den Restbedarf von JETZT bis 23:59 Uhr."""
        total_rest = 0.0
        try:
            profile = self._get_db()
            current_hour = now.hour
            current_minute = now.minute
            weekday = str(now.weekday())

            # 1. Rest der aktuellen Stunde berechnen
            hour_data = profile.get(weekday, {}).get(str(current_hour), {})
            val_this_hour = sum(float(v) for v in hour_data.values()) / len(hour_data) if hour_data else default_usage
            
            remaining_factor = (60 - current_minute) / 60
            total_rest += val_this_hour * remaining_factor

            # 2. Volle Stunden bis Mitternacht
            for h in range(current_hour + 1, 24):
                hr_data = profile.get(weekday, {}).get(str(h), {})
                val_h = sum(float(v) for v in hr_data.values()) / len(hr_data) if hr_data else default_usage
                total_rest += val_h

            return round(total_rest, 2)
        except Exception as e:
            _LOGGER.error("Fehler beim Restbedarf-Read: %s", e)
            return 5.0

    def get_hour_forecasts(self, now, default_usage=0.6):
        """Liefert Forecast für aktuelle Stunde (herunterlaufend) und nächste Stunde (voll)."""
        try:
            profile = self._get_db()
            weekday = str(now.weekday())
            
            # Aktuelle Stunde (anteilig)
            h0 = str(now.hour)
            data0 = profile.get(weekday, {}).get(h0, {})
            val0 = sum(float(v) for v in data0.values()) / len(data0) if data0 else default_usage
            rem_factor = (60 - now.minute) / 60
            current_hour_rem = round(val0 * rem_factor, 2)

            # Nächste Stunde
            next_time = now + timedelta(hours=1)
            h1 = str(next_time.hour)
            d1 = str(next_time.weekday())
            data1 = profile.get(d1, {}).get(h1, {})
            next_hour_full = round(sum(float(v) for v in data1.values()) / len(data1) if data1 else default_usage, 2)

            return current_hour_rem, next_hour_full
        except:
            return 0.3, 0.6

    def update_profile(self, now, samples, config_default_usage=0.6):
        """Berechnet den neuen gewichteten Durchschnitt (70/30) und speichert ihn."""
        if not samples:
            return
            
        # Projektion auf Stundenbasis
        hourly_kwh_projection = (sum(samples) / len(samples)) * (60 / (len(samples) if len(samples) > 0 else 1))
        
        try:
            db = self._get_db()
            d = str(now.weekday())
            h = str(now.hour)
            m = str((now.minute // 15) * 15) # Viertelstunden-Index
            
            db.setdefault(d, {}).setdefault(h, {})
            
            # Altwert aus DB oder Default
            val_raw = db[d][h].get(m, config_default_usage)
            if isinstance(val_raw, dict): # Falls wir versehentlich ein Dict erwischt haben
                old_val = config_default_usage
            else:
                old_val = float(val_raw)
            
            # LERNEFFEKT: 70% Altwert, 30% neuer Messwert
            db[d][h][m] = round((old_val * 0.7) + (hourly_kwh_projection * 0.3), 3)
            
            with open(self.path, 'w') as f:
                json.dump(db, f, indent=2)
                
            _LOGGER.debug("Profil-Update: Tag %s, %s:%s Uhr -> %s kWh/h", d, h, m, db[d][h][m])
        except Exception as e:
            _LOGGER.error("Fehler beim Profil-Update: %s", e)

    def calculate_best_profile(self, data, options):
        """
        Diese Methode verknüpft die historischen Daten mit der Tibber-Logik.
        """
        profile = [0] * 24
        now = datetime.now()
        prices = data.get("prices", [])
        solar = data.get("solar_forecast", {})
        
        def_usage = float(options.get("default_usage", 0.6))
        advantage = float(options.get("charge_delta_threshold", 5.0))

        if not prices or not options.get("auto_charge_enabled", True):
            return profile

        avg_price = sum(p['total'] for p in prices[:24]) / len(prices[:24])

        for p_info in prices[:24]:
            p_time = datetime.fromisoformat(p_info['startsAt'].replace("Z", "+00:00"))
            
            # Bedarf für diese Stunde basierend auf gelernten Viertelstunden berechnen
            db = self._get_db()
            hr_data = db.get(str(p_time.weekday()), {}).get(str(p_time.hour), {})
            predicted_hr = sum(float(v) for v in hr_data.values()) / len(hr_data) if hr_data else def_usage
            
            solar_val = solar.get(p_time.strftime("%Y-%m-%d %H:00:00"), 0)
            net_need = predicted_hr - solar_val

            if net_need > 0 and p_info['total'] < (avg_price - advantage):
                profile[p_time.hour] = 1

        return profile