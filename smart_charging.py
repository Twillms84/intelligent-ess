from datetime import datetime, timedelta

class SmartCharging:
    @staticmethod
    def calculate_charge_strategy(config, current_soc, kwh_now, rest_demand_24h, solar_forecast, prices):
        """
        Entscheidet, ob der Akku aktiv geladen werden muss (Netz -> Akku).
        """
        if not prices:
            return False, "Keine Preisdaten vorhanden."

        cur_p = prices[0]['price_per_kwh']
        
        # 1. Analyse der günstigsten Stunden im 24h Fenster
        sorted_prices = sorted([p['price_per_kwh'] for p in prices[:24]])
        # Wir definieren 'günstig' als die untersten 20% der verfügbaren Preise
        cheap_threshold = sorted_prices[min(len(sorted_prices)-1, 4)] 
        
        is_cheap_now = cur_p <= (cheap_threshold * 1.05) # Kleiner Puffer

        # 2. Defizit-Check: Reicht das, was wir haben + was die Sonne bringt?
        # Wir addieren Akku-Inhalt und Solar-Prognose
        expected_energy = kwh_now + solar_forecast
        deficit = rest_demand_24h - expected_energy

        # 3. Entscheidung
        if deficit > 0.5: # Wenn mehr als 0.5kWh fehlen
            if is_cheap_now:
                return True, f"LADEN: Defizit {round(deficit,1)}kWh und Strompreis günstig ({round(cur_p*100,1)}ct)."
            
        return False, "Kein Netz-Laden notwendig."