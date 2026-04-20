from datetime import datetime, timedelta

class SmartCharging:
    @staticmethod
    def calculate_charge_strategy(config, current_soc, kwh_now, rest_demand_24h, solar_forecast, prices):
        if not prices:
            return False, "Keine Preisdaten vorhanden."

        # Preise aus dem 'data' Attribut extrahieren (deine Sensor-Struktur)
        # Wir nehmen die nächsten 96 Einträge (15min * 96 = 24h)
        future_prices = [p.get('price_per_kwh') for p in prices[:96] if p.get('price_per_kwh') is not None]
        
        if not future_prices:
            return False, "Preisliste leer oder falsches Format."

        cur_p = future_prices[0]
        sorted_prices = sorted(future_prices)
        
        # 'Günstig' sind die billigsten 15% der nächsten 24h
        cheap_threshold = sorted_prices[min(len(sorted_prices)-1, 14)] 
        is_cheap_now = cur_p <= (cheap_threshold + 0.002)

        expected_energy = kwh_now + solar_forecast
        deficit = rest_demand_24h - expected_energy

        if deficit > 0.5 and is_cheap_now:
            return True, f"LADEN: Defizit {round(deficit,1)}kWh, Preis günstig ({round(cur_p*100,1)}ct)."
            
        return False, "Kein Netz-Laden notwendig."