from datetime import datetime, timedelta

class SmartDischarging:
    @staticmethod
    def calculate_discharge_strategy(config, current_soc, kwh_now, morning_demand, prices):
        """
        Entscheidet, ob die Entladung gestoppt werden muss (Akku-Sperre).
        """
        if not prices:
            return "NORMAL", "Betrieb über Akku."

        cur_p = prices[0]['price_per_kwh']
        
        # 1. Peak-Preise finden (nächste 12 Stunden)
        future_prices = [p['price_per_kwh'] for p in prices[1:13]]
        if not future_prices:
            return "NORMAL", "Keine Zukunftspreise."
            
        max_future_p = max(future_prices)
        
        # 2. Preis-Differenz-Check
        # Lohnt es sich, den Akku jetzt zu leeren, oder lieber für später sparen?
        price_diff = (max_future_p - cur_p) * 100 # Differenz in ct
        threshold = float(config.get("charge_delta_threshold", 10.0))

        # 3. Reserve-Check
        # Wenn der Akku-Stand unter den Bedarf für die teuren Morgenstunden fällt
        if kwh_now <= morning_demand:
            if price_diff > threshold:
                return "SPERREN", f"SPERREN: Akku ({round(kwh_now,1)}kWh) wird für Peak ({round(max_future_p*100,1)}ct) reserviert."

        return "NORMAL", "Akku darf entladen."