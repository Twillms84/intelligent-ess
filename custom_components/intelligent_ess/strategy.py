"""Deterministische Strategie-Gates fuer SmartCharge und SmartHold.

Hier werden aus den vorberechneten Basiswerten zwei klare Entscheidungen
abgeleitet. Die KI (Conversation-Agent) feilt nur noch an Timing und Bericht,
darf aber kein Gate ueberschreiben - die hier berechneten Flags sind die harten
Leitplanken.

SmartCharge: nur, wenn prognostiziert NICHT genug PV-Ertrag ueber den Tag kommt.
SmartHold:   nur, wenn die Nachtreserve nicht ausreicht UND der Morgenpreis hoch ist.
"""

# Zeitfenster, in dem ein Preis-Hoch als "Morgen-Peak" zaehlt (Stunden).
MORNING_START = 5
MORNING_END = 10


def evaluate_strategy(
    *,
    soc,
    capacity,
    min_soc,
    solar_remaining,
    pv_tomorrow_total,
    night_demand,
    expected_daily_total,
    ai_price_summary,
    current_price,
    price_delta_threshold,
):
    """Berechnet die SmartCharge/SmartHold-Gates plus Begruendungen.

    Alle Energiewerte in kWh, Preise in EUR/kWh (current_price) bzw. ct
    (ai_price_summary, price_delta_threshold). Rueckgabe: dict mit Flags und
    den zugrunde liegenden Zahlen fuer Prompt und Dashboard.
    """
    summary = ai_price_summary or {}

    usable_battery = max(0.0, (float(soc) - float(min_soc)) / 100.0 * float(capacity))
    # Energie, die ueber Nacht (ohne PV) zur Verfuegung steht.
    pv_available_night = usable_battery + float(solar_remaining or 0.0)

    # --- SmartCharge: reicht der prognostizierte PV-Ertrag fuer den Tag? ---
    pv_tomorrow = float(pv_tomorrow_total or 0.0)
    daily_need = float(expected_daily_total or 0.0)
    if pv_tomorrow > 0.0 and daily_need > 0.0:
        # Morgen-PV gegen den erwarteten Tagesbedarf stellen.
        pv_day_balance = round(pv_tomorrow - daily_need, 2)
        charge_basis = "Tages-PV-Prognose"
    else:
        # Keine brauchbare Morgen-Prognose -> auf die Nacht-Bilanz zurueckfallen.
        pv_day_balance = round(pv_available_night - float(night_demand or 0.0), 2)
        charge_basis = "Nacht-Bilanz (keine Morgen-Prognose)"
    smartcharge_allowed = pv_day_balance < 0

    # --- SmartHold: Nachtreserve unzureichend UND Morgenpreis hoch? ---
    nacht_defizit = round(float(night_demand or 0.0) - pv_available_night, 2)
    reserve_insufficient = nacht_defizit > 0

    current_ct = round(float(current_price or 0.0) * 100, 2)
    max_ct = summary.get("max_price")
    max_time = summary.get("max_time")
    max_hour = None
    if max_time:
        try:
            max_hour = int(str(max_time).split(":")[0])
        except (ValueError, IndexError):
            max_hour = None

    morning_price_high = (
        max_ct is not None
        and max_hour is not None
        and MORNING_START <= max_hour <= MORNING_END
        and (float(max_ct) - current_ct) > float(price_delta_threshold)
    )
    smarthold_allowed = bool(reserve_insufficient and morning_price_high)

    return {
        "usable_battery": round(usable_battery, 2),
        "pv_available_night": round(pv_available_night, 2),
        "pv_day_balance": pv_day_balance,
        "charge_basis": charge_basis,
        "nacht_defizit": max(0.0, nacht_defizit),
        "reserve_insufficient": reserve_insufficient,
        "morning_price_high": morning_price_high,
        "current_price_ct": current_ct,
        "smartcharge_allowed": bool(smartcharge_allowed),
        "smarthold_allowed": smarthold_allowed,
    }
