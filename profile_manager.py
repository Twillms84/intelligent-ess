import json
import os
import logging
from datetime import timedelta, datetime

_LOGGER = logging.getLogger(__name__)

class ProfileManager:
    @staticmethod
    def get_daily_rest_demand(path, now):
        """
        Berechnet den Restbedarf von JETZT bis 23:59 Uhr.
        Läuft über den Tag kontinuierlich gegen 0.
        """
        total_rest = 0.0
        try:
            profile = {}
            if os.path.exists(path):
                with open(path, 'r') as f:
                    profile = json.load(f)

            current_hour = now.hour
            current_minute = now.minute
            weekday = str(now.weekday())

            # 1. Rest der aktuellen Stunde berechnen
            hour_data = profile.get(weekday, {}).get(str(current_hour), {})
            # Wenn keine Daten, Fallback 0.6 kWh/h, sonst Durchschnitt der Viertelstunden
            val_this_hour = sum(float(v) for v in hour_data.values()) / len(hour_data) if hour_data else 0.6
            
            # Anteil der verbleibenden Stunde (z.B. um 14:15 noch 75% der Stunde)
            remaining_factor = (60 - current_minute) / 60
            total_rest += val_this_hour * remaining_factor

            # 2. Alle vollen Stunden bis Mitternacht aufsummieren
            for h in range(current_hour + 1, 24):
                hr_data = profile.get(weekday, {}).get(str(h), {})
                val_h = sum(float(v) for v in hr_data.values()) / len(hr_data) if hr_data else 0.6
                total_rest += val_h

            return round(total_rest, 2)
        except Exception as e:
            _LOGGER.error("Fehler beim Restbedarf-Read: %s", e)
            return 5.0

    @staticmethod
    def get_hour_forecasts(path, now):
        """
        Liefert Forecast für aktuelle Stunde (herunterlaufend) und nächste Stunde (voll).
        """
        try:
            profile = {}
            if os.path.exists(path):
                with open(path, 'r') as f:
                    profile = json.load(f)
            
            weekday = str(now.weekday())
            
            # Aktuelle Stunde (anteilig)
            h0 = str(now.hour)
            data0 = profile.get(weekday, {}).get(h0, {})
            val0 = sum(float(v) for v in data0.values()) / len(data0) if data0 else 0.6
            rem_factor = (60 - now.minute) / 60
            current_hour_rem = round(val0 * rem_factor, 2)

            # Nächste Stunde (voll)
            next_time = now + timedelta(hours=1)
            h1 = str(next_time.hour)
            d1 = str(next_time.weekday())
            data1 = profile.get(d1, {}).get(h1, {})
            next_hour_full = round(sum(float(v) for v in data1.values()) / len(data1) if data1 else 0.6, 2)

            return current_hour_rem, next_hour_full
        except:
            return 0.3, 0.6

    @staticmethod
    def update_profile(path, now, samples, config):
        """Berechnet den neuen gewichteten Durchschnitt und speichert ihn."""
        if not samples:
            return
            
        # Durchschnitt der letzten 15 Minuten als Stunden-Projektion
        # Wenn in 15 Min 0.2kWh verbraucht wurden, ist die Projektion 0.8kWh/h
        hourly_kwh_projection = (sum(samples) / len(samples)) * (60 / (len(samples) if len(samples) > 0 else 1))
        
        try:
            db = {}
            if os.path.exists(path):
                with open(path, 'r') as f:
                    db = json.load(f)
            
            d = str(now.weekday())
            h = str(now.hour)
            # Wir runden auf die Viertelstunde (0, 15, 30, 45) für saubere Indizes
            m = str((now.minute // 15) * 15)
            
            db.setdefault(d, {}).setdefault(h, {})
            
            default_usage = float(config.get("default_usage", 0.6))
            old_val = float(db[d][h].get(m, default_usage))
            
            # LERNEFFEKT: 70% Altwert, 30% neuer Messwert
            db[d][h][m] = round((old_val * 0.7) + (hourly_kwh_projection * 0.3), 3)
            
            with open(path, 'w') as f:
                json.dump(db, f, indent=2) # indent für bessere Lesbarkeit beim Debuggen
                
            _LOGGER.debug("Profil-Update: %s Tag %s, %s:%s Uhr -> %s kWh/h", d, h, m, db[d][h][m])
        except Exception as e:
            _LOGGER.error("Fehler beim Profil-Update: %s", e)