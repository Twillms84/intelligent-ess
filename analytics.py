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
            except: return 0.0
        return 0.0

    # PV Produktion (Liste oder String)
    pv_ids = config.get("pv_production_sensor", [])
    if isinstance(pv_ids, str): pv_ids = [pv_ids]
    pv_total = sum(_get_val(i) for i in pv_ids)

    return {
        "pv": pv_total,
        "grid_in": _get_val(config.get("grid_consumption_sensor")),
        "grid_out": _get_val(config.get("grid_export_sensor")),
        "bat_chg": _get_val(config.get("bat_charge_sensor")),
        "bat_dis": _get_val(config.get("bat_discharge_sensor")),
        "bat_soc": _get_val(config.get("battery_soc_sensor"))
    }

async def update_forecasts_and_finances(hass, coordinator, config, current_now, deltas, house_kwh):
    """Berechnet Forecasts und Finanzen."""
    now = datetime.datetime.now()
    
    # 1. Forecasts
    rest_daily = await hass.async_add_executor_job(lambda: coordinator.profile_manager.get_daily_rest_demand(now))
    cur_rem, next_full = await hass.async_add_executor_job(lambda: coordinator.profile_manager.get_hour_forecasts(now))

    # 2. Finanzen
    savings = coordinator.data.get("savings", {"solar": 0.0, "hold": 0.0, "load": 0.0, "total": 0.0})
    
    p_state = hass.states.get(config.get("tibber_price_sensor", ""))
    cur_p = float(p_state.state) if p_state and p_state.state not in ['unknown', 'unavailable'] else 0.30
    prices = p_state.attributes.get("data", []) if p_state else []

    # Solarersparnis
    eigen_kwh = max(0, house_kwh - deltas.get("grid_in", 0))
    savings["solar"] += (eigen_kwh * cur_p)

    # Hold/Load Ersparnis (nur wenn Strategie aktiv)
    strat = coordinator.data.get("strat")
    if strat == "SPERRE":
        future_prices = [p.get('price_per_kwh', p.get('price', cur_p)) for p in prices[1:13]]
        max_p = max(future_prices) if future_prices else cur_p
        savings["hold"] += (house_kwh * max(0, max_p - cur_p))
    elif strat == "LADEN":
        day_prices = [p.get('price_per_kwh', p.get('price', cur_p)) for p in prices[:24]]
        avg_p = sum(day_prices) / len(day_prices) if day_prices else 0.30
        savings["load"] += (deltas.get("bat_chg", 0) * max(0, avg_p - cur_p))

    savings["total"] = savings["solar"] + savings["hold"] + savings["load"]

    return {
        "rest_demand_daily": round(rest_daily, 2),
        "forecast_current_hour": round(cur_rem, 3),
        "forecast_next_hour": round(next_full, 3),
        "morning_reserve": round(rest_daily * 0.2, 2),
        "savings": savings
    }

async def get_tibber_prices(hass, config):
    """Holt die Strompreise aus dem Tibber Sensor-Attribut 'data'."""
    sensor_id = config.get("tibber_export_sensor")
    if not sensor_id:
        return []
        
    prices = []
    state_obj = hass.states.get(sensor_id)
    
    if state_obj and state_obj.state not in ['unknown', 'unavailable', 'none', 'pending', 'error']:
        if 'data' in state_obj.attributes:
            raw_data = state_obj.attributes['data']
            for entry in raw_data:
                prices.append({
                    "start_time": entry.get("start_time"),
                    "price_per_kwh": entry.get("price_per_kwh"),
                    "startsAt": entry.get("start_time"),
                    "total": entry.get("price_per_kwh")
                })