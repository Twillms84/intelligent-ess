from datetime import datetime, timedelta

class SmartDischarging:
    @staticmethod
    def calculate_discharge_strategy(config, current_soc, kwh_now, morning_demand, prices):
        """
        Entscheidet, ob die Entladung gestoppt werden muss (Akku-Sperre / HOLD).
        """
        if not prices:
            return "NORMAL", "Betrieb über Akku."

        # Fehlertolerantes Auslesen des aktuellen Preises (unterstützt 'price' und 'price_per_kwh')
        cur_p = prices[0].get('price_per_kwh', prices[0].get('price', 0.30))
        
        # 1. Peak-Preise finden (nächste 12 Stunden)
        future_prices = [p.get('price_per_kwh', p.get('price', cur_p)) for p in prices[1:13]]
        if not future_prices:
            return "NORMAL", "Keine Zukunftspreise."
            
        max_future_p = max(future_prices)
        
        # 2. Preis-Differenz-Check
        price_diff = (max_future_p - cur_p) * 100 # Differenz in ct
        threshold = float(config.get("charge_delta_threshold", 10.0))

        # 3. Reserve-Check & Preis-Check
        # Wir blockieren den Akku (HOLD), wenn das Preis-Delta enorm ist, 
        # ODER wenn die Kapazität nicht für den teuren Morgen reicht und ein Peak ansteht.
        if price_diff >= threshold:
            return "HOLD", f"HOLD: Strom extrem günstig. Akku wird für Peak ({round(max_future_p*100,1)}ct) geschont."
            
        elif kwh_now <= morning_demand and price_diff > 0:
            return "HOLD", f"HOLD: Akku ({round(kwh_now,1)}kWh) reicht sonst nicht für Morgen-Peak ({round(max_future_p*100,1)}ct)."

        return "NORMAL", "Akku darf entladen."