from datetime import datetime

def get_timer_value(hass, entity_id):
    """Liest eine Time-Entität sicher aus und fängt Startfehler ab."""
    state = hass.states.get(entity_id)
    if state and state.state not in ['unknown', 'unavailable']:
        # Falls die Entität HH:MM:SS liefert, kürzen wir auf HH:MM
        return state.state[:5] 
    return None

def calculate_discharge_strategy(config, current_time=None):
    """
    Prüft, ob die Entladesperre aktiv sein soll.
    Unterstützt Zeitfenster über Mitternacht hinaus.
    """
    if current_time is None:
        current_time = datetime.now()

    # 1. Haupt-Schalter prüfen
    if not config.get("smart_discharge_enabled", False):
        return {"discharge_locked": False, "reason": "Automatik deaktiviert"}

    # 2. Timer prüfen
    timers = config.get("discharge_timers", [])
    now_str = current_time.strftime("%H:%M")

    for timer in timers:
        start = timer.get("start")
        end = timer.get("end")
        
        if not start or not end:
            continue

        if start > end:
            # Nacht-Modus (z.B. 22:00 bis 04:00)
            if now_str >= start or now_str <= end:
                return {"discharge_locked": True, "reason": f"Nacht-Timer aktiv ({start}-{end})"}
        else:
            # Tag-Modus (z.B. 14:00 bis 16:00)
            if start <= now_str <= end:
                return {"discharge_locked": True, "reason": f"Timer aktiv ({start}-{end})"}

    return {"discharge_locked": False, "reason": "Kein Zeitfenster aktiv"}