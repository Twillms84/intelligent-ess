import json
import os
import logging
from datetime import timedelta

_LOGGER = logging.getLogger(__name__)

class ProfileManager:
    @staticmethod
    def get_forecast_demand(path, now):
        """Summiert den gelernten Bedarf für die nächsten 12 Stunden."""
        total = 0.0
        try:
            if not os.path.exists(path):
                return 5.0  # Sicherheits-Fallback
                
            with open(path, 'r') as f:
                profile = json.load(f)
            
            for i in range(1, 13):
                future = now + timedelta(hours=i)
                d, h = str(future.weekday()), str(future.hour)
                hr_data = profile.get(d, {}).get(h, {})
                
                if hr_data:
                    total += sum(float(v) for v in hr_data.values()) / len(hr_data)
                else:
                    total += 0.6  # Fallback 600W/h
            return round(total, 2)
        except Exception as e:
            _LOGGER.error("Fehler beim Forecast-Read: %s", e)
            return 5.0

    @staticmethod
    def update_profile(path, now, samples, config):
        """Berechnet den neuen gewichteten Durchschnitt und speichert ihn."""
        if not samples:
            return
            
        avg_15m = (sum(samples) / len(samples)) * 60
        
        try:
            db = {}
            if os.path.exists(path):
                with open(path, 'r') as f:
                    db = json.load(f)
            
            d, h, m = str(now.weekday()), str(now.hour), str(now.minute)
            db.setdefault(d, {}).setdefault(h, {})
            
            # Gewichtetes Lernen
            default_usage = float(config.get("default_usage", 0.85))
            old_val = float(db[d][h].get(m, default_usage))
            
            # 70% Historie, 30% Neu
            db[d][h][m] = round((old_val * 0.7) + (avg_15m * 0.3), 3)
            
            with open(path, 'w') as f:
                json.dump(db, f)
            _LOGGER.debug("Profil aktualisiert für %s %s:%s - Wert: %s", d, h, m, db[d][h][m])
        except Exception as e:
            _LOGGER.error("Fehler beim Profil-Update: %s", e)
    
    @staticmethod
    def get_detailed_forecast(path, now, hours=12):
        """Liefert den Verbrauch für die nächsten X Stunden als Liste/Dict."""
        forecast = {"next_hour": 0.0, "hourly_details": {}}
        try:
            if not os.path.exists(path):
                return {"next_hour": 0.5, "hourly_details": {}}
                
            with open(path, 'r') as f:
                profile = json.load(f)
            
            for i in range(0, hours):
                future = now + timedelta(hours=i)
                d, h = str(future.weekday()), str(future.hour)
                hr_data = profile.get(d, {}).get(h, {})
                
                # Durchschnitt der 15-Minuten-Werte dieser Stunde
                val = round(sum(float(v) for v in hr_data.values()) / len(hr_data), 2) if hr_data else 0.5
                
                if i == 0:
                    forecast["next_hour"] = val
                
                # Attribut-Eintrag (z.B. "14:00": 0.8)
                forecast["hourly_details"][f"{future.hour}:00"] = val
                
            return forecast
        except:
            return {"next_hour": 0.5, "hourly_details": {}}