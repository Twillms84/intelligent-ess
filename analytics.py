# custom_components/intelligent_ess/analytics.py
import logging
import datetime

_LOGGER = logging.getLogger(__name__)

def get_raw_states(hass, config):
    """Holt alle Sensorwerte sicher ab."""
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
    """Holt die Strompreise aus dem Attribut 'data' des gewählten Tibber-Sensors."""
    sensor_id = config.get("tibber_export_sensor") 
    if not sensor_id:
        return []
        
    state_obj = hass.states.get(sensor_id)
    if not state_obj or state_obj.state in ['unknown', 'unavailable']:
        return []

    raw_data = state_obj.attributes.get('data', [])
    prices = []
    for entry in raw_data:
        start_time = entry.get("start_time") or entry.get("startsAt")
        price = entry.get("price_per_kwh") or entry.get("total")
        if start_time is not None and price is not None:
            prices.append({"start_time": start_time, "total": float(price)})
    return prices

def get_solar_forecast(hass, config):
    """Holt den verbleibenden Solar-Ertrag für heute."""
    solar_entity = config.get("solar_forecast_sensor")
    if not solar_entity:
        return 0.0
    
    state = hass.states.get(solar_entity)
    if state and state.state not in ["unknown", "unavailable", "none"]:
        try:
            # Versuche den Haupt-State (Zustand) in eine Zahl zu verwandeln
            return round(float(state.state), 2)
        except ValueError:
            # Falls der Sensor Text ausgibt, versuche ein Attribut zu lesen 
            # (oft bei Forecast.Solar oder Solcast der Fall)
            val = state.attributes.get("estimated_production", 0)
            try:
                return round(float(val), 2)
            except (ValueError, TypeError):
                return 0.0
    return 0.0
    
async def update_forecasts_and_finances(hass, profile_manager, config, deltas, house_kwh, current_savings, current_strat):
    """Berechnet Forecasts und Finanzen basierend auf dynamischen Preisen."""
    now = datetime.datetime.now()
    
    # 1. Forecasts
    rest_daily = await hass.async_add_executor_job(profile_manager.get_daily_rest_demand, now)
    cur_rem, next_full = await hass.async_add_executor_job(profile_manager.get_hour_forecasts, now)

    # 2. Preise holen
    prices = get_tibber_prices(hass, config)
    
    # Aktueller Preis (sicherer Abruf mit Fallback auf 0.30)
    p_state = hass.states.get(config.get("tibber_export_sensor", ""))
    cur_p = 0.30
    if p_state and p_state.state not in ['unknown', 'unavailable', 'none']:
        try:
            cur_p = float(p_state.state)
        except ValueError:
            cur_p = 0.30

    savings = dict(current_savings) 

    # --- DYNAMISCHE INTERVALL-ERKENNUNG ---
    # Hat die Liste mehr als 48 Einträge, sind es sehr wahrscheinlich 15-Minuten-Werte
    is_15_min = len(prices) > 48 
    items_12h = 48 if is_15_min else 12
    items_24h = 96 if is_15_min else 24

    # --- SOLARERSPARNIS ---
    eigen_kwh = max(0, house_kwh - deltas.get("grid_in", 0))
    savings["solar"] += (eigen_kwh * cur_p)

    # --- SPERR-ERSPARNIS (HOLD) ---
    if current_strat == "SPERRE":
        future_prices = [p['total'] for p in prices[1:items_12h+1]] # Schau 12h voraus
        max_future_p = max(future_prices) if future_prices else cur_p
        
        if max_future_p > cur_p:
            savings["hold"] += (house_kwh * (max_future_p - cur_p))

    # --- LADE-ERSPARNIS (LOAD) ---
    day_prices = [p['total'] for p in prices[:items_24h]]
    avg_p = sum(day_prices) / len(day_prices) if day_prices else cur_p

    bat_chg = deltas.get("bat_chg", 0)
    bat_dis = deltas.get("bat_dis", 0)

    if current_strat == "LADEN" and bat_chg > 0:
        savings["load"] += (bat_chg * max(0, avg_p - cur_p))
    
    if bat_dis > 0:
        if cur_p > avg_p:
            savings["load"] += (bat_dis * (cur_p - avg_p))

    savings["total"] = savings["solar"] + savings["hold"] + savings["load"]

    return {
        "rest_demand_daily": round(rest_daily, 2),
        "forecast_current_hour": round(cur_rem, 3),
        "forecast_next_hour": round(next_full, 3),
        "morning_reserve": round(rest_daily * 0.2, 2),
        "savings": {k: round(v, 4) for k, v in savings.items()}
    }

    