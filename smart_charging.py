from datetime import datetime, timedelta

class SmartCharging:
    @staticmethod
    def calculate_charge_strategy(config, current_soc, kwh_now, rest_demand_24h, solar_forecast, prices):
        """
        Entscheidet, ob der Akku aktiv geladen werden muss (Netz -> Akku).
        """
        if not prices:
            return False, "Keine Preisdaten vorhanden."

        # Fehlertolerantes Auslesen
        cur_p = prices[0].get('price_per_kwh', prices[0].get('price', 0.30))
        
        # 1. Analyse der günstigsten Stunden im 24h Fenster
        future_prices = [p.get('price_per_kwh', p.get('price', cur_p)) for p in prices[:24]]
        if not future_prices:
            return False, "Keine Zukunftspreise für Analyse vorhanden."

        sorted_prices = sorted(future_prices)
        
        # Wir definieren 'günstig' als die untersten 20% der verfügbaren Preise (max. Index 4)
        cheap_threshold = sorted_prices[min(len(sorted_prices)-1, 4)] 
        
        # Absoluter Puffer von 0.5 Cent (0.005 €), um das Problem mit negativen Preisen zu umgehen
        is_cheap_now = cur_p <= (cheap_threshold + 0.005)

        # 2. Defizit-Check: Reicht das, was wir haben + was die Sonne bringt?
        expected_energy = kwh_now + solar_forecast
        deficit = rest_demand_24h - expected_energy

        # 3. Entscheidung
        if deficit > 0.5: # Wenn mehr als 0.5kWh fehlen
            if is_cheap_now:
                return True, f"LADEN: Defizit {round(deficit,1)}kWh und Strompreis günstig ({round(cur_p*100,1)}ct)."
            
        return False, "Kein Netz-Laden notwendig."