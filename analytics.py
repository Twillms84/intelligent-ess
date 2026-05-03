import logging
import datetime
from datetime import timedelta
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

def get_raw_states(hass, config):
    """Holt alle aktuellen Sensorwerte sicher ab."""
    def _get_val(eid):
        if not eid: return 0.0
        s = hass.states.get(eid)
        if s and s.state not in ['unknown', 'unavailable', 'none']:
            try: return float(s.state)
            except ValueError: return 0.0
        return 0.0

    pv_ids = config.get("pv_production_sensor", [])
    if isinstance(pv_ids, str) and pv_ids: 
        pv_ids = [pv_ids]
    pv_total = sum(_get_val(i) for i in pv_ids if i)

    return {
        "pv": pv_total,
        "grid_in": _get_val(config.get("grid_consumption_sensor")),
        "grid_out": _get_val(config.get("grid_export_sensor")),
        "bat_chg": _get_val(config.get("bat_charge_sensor")),
        "bat_dis": _get_val(config.get("bat_discharge_sensor")),
        "bat_soc": _get_val(config.get("battery_soc_sensor"))
    }

def get_tibber_prices(hass, config):
    """
    Holt die Strompreise und filtert veraltete Preise (Vergangenheit) heraus.
    Sorgt dafür, dass die KI nur Daten bekommt, mit denen sie noch arbeiten kann.
    """
    sensor_id = config.get("tibber_export_sensor") 
    if not sensor_id:
        return []
        
    state_obj = hass.states.get(sensor_id)
    if not state_obj or state_obj.state in ['unknown', 'unavailable']:
        return []

    raw_data = state_obj.attributes.get('data', [])
    prices = []
    now = dt_util.now() 
    
    for entry in raw_data:
        start_time_str = entry.get("start_time") or entry.get("startsAt")
        price = entry.get("price_per_kwh") or entry.get("total")
        
        if start_time_str is not None and price is not None:
            try:
                # Parse Zeitstempel (ISO Format)
                start_time = dt_util.parse_datetime(start_time_str)
                # Behalte nur Preise, deren Zeitfenster noch aktiv oder in der Zukunft ist
                if start_time and (start_time + datetime.timedelta(minutes=15)) > now:
                    prices.append({
                        "start_time": start_time_str, 
                        "total": float(price)
                    })
            except Exception:
                continue
                
    return prices

def get_ai_price_summary(prices, hours_ahead=12):
    """
    Erstellt eine kompakte Zusammenfassung der Preis-Highlights für die KI.
    Das spart Token und verhindert Fehlinterpretationen der KI.
    """
    if not prices:
        return {"error": "Keine Preisdaten verfügbar"}
        
    # Check auf 15-Minuten Intervalle
    is_15_min = len(prices) > 48
    items = (hours_ahead * 4) if is_15_min else hours_ahead
    relevant_prices = prices[:items]
    
    if not relevant_prices:
        return {"error": "Keine zukünftigen Preise gefunden"}

    # Extremwerte finden
    min_p = min(relevant_prices, key=lambda x: x['total'])
    max_p = max(relevant_prices, key=lambda x: x['total'])
    avg_p = sum(p['total'] for p in relevant_prices) / len(relevant_prices)
    
    return {
        "min_price": round(min_p['total'] * 100, 2),
        "min_time": dt_util.parse_datetime(min_p['start_time']).strftime('%H:%M'),
        "max_price": round(max_p['total'] * 100, 2),
        "max_time": dt_util.parse_datetime(max_p['start_time']).strftime('%H:%M'),
        "avg_price": round(avg_p * 100, 2),
        "count_intervals": len(relevant_prices)
    }

def get_solar_forecast(hass, config):
    """Holt den verbleibenden Solar-Ertrag für heute/den Restzeitraum."""
    solar_entity = config.get("solar_forecast_sensor")
    if not solar_entity:
        return 0.0
    
    state = hass.states.get(solar_entity)
    if state and state.state not in ["unknown", "unavailable", "none"]:
        try:
            return round(float(state.state), 2)
        except ValueError:
            val = state.attributes.get("estimated_production", 0)
            try:
                return round(float(val), 2)
            except (ValueError, TypeError):
                return 0.0
    return 0.0
    
def calculate_autarky_time_tomorrow(profile_manager, solar_forecast, config):
    """
    Ermittelt die Uhrzeit am morgigen Tag, ab der die PV-Produktion
    voraussichtlich den Hausverbrauch übersteigt.
    """
    # SICHERHEITS-CHECK: Ist solar_forecast überhaupt ein Dictionary (Stundenwerte)?
    # Wenn es eine Zahl (float) ist, haben wir nur eine Summe und keine Stundenwerte!
    if not isinstance(solar_forecast, dict):
        return "Keine Stundenwerte"

    now = dt_util.now()
    tomorrow = now + timedelta(days=1)
    default_usage = float(config.get("default_usage", 0.85))
    
    db = profile_manager._get_db()
    wd = str(tomorrow.weekday())
    
    for hour in range(5, 16): 
        check_time = tomorrow.replace(hour=hour, minute=0, second=0, microsecond=0)
        pv_key = check_time.strftime("%Y-%m-%d %H:00:00")
        
        pv_yield = solar_forecast.get(pv_key, 0.0)
        
        hr_data = db.get(wd, {}).get(str(hour), {})
        demand = sum(float(v) for v in hr_data.values()) / len(hr_data) if hr_data else default_usage
        
        if pv_yield > demand:
            return f"{hour:02d}:00"
            
    return "Nicht erreicht"

async def update_forecasts_and_finances(hass, profile_manager, config, deltas, house_kwh, current_savings, current_strat):
    """Zentrale Recheneinheit für Prognosen, Finanzen und KI-Daten."""
    now = datetime.datetime.now()
    
    # 1. Config-Wert für Grundlast laden (aus deinem Config Flow)
    def_usage = float(config.get("default_usage", 0.85))

    # 2. Prognosen über ProfileManager abrufen (jetzt bis zum nächsten Morgen)
    rest_demand = await hass.async_add_executor_job(
        profile_manager.get_daily_rest_demand, now, def_usage
    )
    cur_rem, next_full = await hass.async_add_executor_job(
        profile_manager.get_hour_forecasts, now, def_usage
    )

    # 3. Preise verarbeiten
    prices = get_tibber_prices(hass, config)
    ai_summary = get_ai_price_summary(prices, hours_ahead=12)
    
    # Aktueller Preis für Berechnungen
    p_state = hass.states.get(config.get("tibber_export_sensor", ""))
    cur_p = 0.30
    if p_state and p_state.state not in ['unknown', 'unavailable', 'none']:
        try: cur_p = float(p_state.state)
        except ValueError: pass

    # 4. Ersparnis-Logik (Finanzielle Statistik)
    savings = dict(current_savings)
    is_15_min = len(prices) > 48 
    items_12h = 48 if is_15_min else 12
    items_24h = 96 if is_15_min else 24

    # Solar-Ersparnis
    eigen_kwh = max(0, house_kwh - deltas.get("grid_in", 0))
    savings["solar"] += (eigen_kwh * cur_p)

    # Sperr-Ersparnis
    if current_strat == "SPERRE":
        future_prices = [p['total'] for p in prices[1:items_12h+1]]
        max_future_p = max(future_prices) if future_prices else cur_p
        if max_future_p > cur_p:
            savings["hold"] += (house_kwh * (max_future_p - cur_p))

    # Lade-Ersparnis
    day_prices = [p['total'] for p in prices[:items_24h]]
    avg_p = sum(day_prices) / len(day_prices) if day_prices else cur_p
    bat_chg = deltas.get("bat_chg", 0)
    bat_dis = deltas.get("bat_dis", 0)

    if current_strat == "LADEN" and bat_chg > 0:
        savings["load"] += (bat_chg * max(0, avg_p - cur_p))
    if bat_dis > 0 and cur_p > avg_p:
        savings["load"] += (bat_dis * (cur_p - avg_p))

    savings["total"] = savings["solar"] + savings["hold"] + savings["load"]
    
    # Solar Forecast
    solar_remaining = get_solar_forecast(hass, config)

    # --- NEU: Forecast für morgen auslesen ---
    tomorrow_ent = config.get("pv_forecast_tomorrow_entity")
    pv_tomorrow_total = 0.0
    if tomorrow_ent:
        state = hass.states.get(tomorrow_ent)
        if state and state.state not in ['unknown', 'unavailable']:
            try:
                pv_tomorrow_total = float(state.state)
            except ValueError:
                pass

    # --- NEU: Autarkie-Zeitpunkt berechnen ---
    # Ruft die Hilfsfunktion (die sich ebenfalls in analytics.py befinden muss) auf
    autarky_time = calculate_autarky_time_tomorrow(profile_manager, solar_remaining, config)

    # 5. Rückgabe des gesamten Datenpakets
    return {
        "rest_demand_daily": round(rest_demand, 2),
        "forecast_current_hour": round(cur_rem, 3),
        "forecast_next_hour": round(next_full, 3),
        "morning_reserve": round(rest_demand * 0.2, 2), # 20% Puffer
        "solar_remaining": solar_remaining,
        "prices": prices,
        "ai_price_summary": ai_summary,
        "savings": {k: round(v, 4) for k, v in savings.items()},
        "pv_tomorrow_total": pv_tomorrow_total,
        "autarky_time_tomorrow": autarky_time
    }