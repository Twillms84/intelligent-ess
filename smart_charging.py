from datetime import datetime, timedelta

def calculate_charge_strategy(config, current_soc, kwh_now, rest_demand_24h, solar_forecast, prices):
    """
    Berechnet, ob der Akku aus dem Netz geladen werden soll.
    """
    if not prices:
        return False, "Keine Preisdaten vorhanden."

    # Preise extrahieren (nächste 24h / 96 * 15min Slots)
    future_prices = [p.get('price_per_kwh') for p in prices[:96] if p.get('price_per_kwh') is not None]
    
    if not future_prices:
        return False, "Preisliste leer oder falsches Format."

    cur_p = future_prices[0]
    sorted_prices = sorted(future_prices)
    
    # 'Günstig' sind die billigsten 15% der nächsten 24h
    # (Wir nehmen das 15. Perzentil der sortierten Liste)
    threshold_index = max(0, int(len(sorted_prices) * 0.15) - 1)
    cheap_threshold = sorted_prices[threshold_index] 
    
    is_cheap_now = cur_p <= (cheap_threshold + 0.002)

    expected_energy = kwh_now + solar_forecast
    deficit = rest_demand_24h - expected_energy

    if deficit > 0.5 and is_cheap_now:
        return True, f"Netz-Laden: Defizit {round(deficit,1)}kWh, Preis {round(cur_p*100,1)}ct"
        
    return False, "Kein Netz-Laden notwendig."