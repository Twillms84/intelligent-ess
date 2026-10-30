"""Tages-Einsatzplaner für den Heimspeicher (Battery Dispatch Planner).

Statt nur eine einzelne Preisspitze zu prüfen, plant dieses Modul über den
gesamten Prognosehorizont: Preis, PV-Ertrag und Verbrauch pro Stunde. Die
begrenzte Akku-Energie wird den TEUERSTEN Stunden zugeteilt, die sie noch
decken kann – unter Berücksichtigung der PV-Nachladung (Kausalität: Energie,
die erst später aus der Sonne kommt, kann nicht für frühere Stunden genutzt
werden).

Daraus ergibt sich pro Stunde eine Aktion:
- DISCHARGE: Akku nutzen (teure Stunde, Energie reicht dafür)
- HOLD:      Akku sparen, Netz versorgt das Haus (günstige Stunde VOR einer
             späteren teuren Stunde, für die die Energie knapp ist)
- CHARGE:    Akku aus dem Netz laden (sehr günstige Stunde, wenn sonst für die
             teuren Stunden zu wenig Energie da wäre)
- IDLE:      nichts Besonderes (kein Restbedarf / nach der letzten teuren Stunde)

Das Modul ist bewusst HA-frei und damit eigenständig testbar.
"""

EPS = 1e-6


def _simulate(horizon, discharge, charge, usable_now, usable_cap, charge_per_hour):
    """Vorwärtssimulation des Akkustands. Gibt (ok, end_soc) zurück.

    ok=False, sobald eine als DISCHARGE geplante Stunde nicht voll aus dem Akku
    gedeckt werden kann.
    """
    soc = usable_now
    ok = True
    for i, h in enumerate(horizon):
        # 1. PV-Überschuss lädt zuerst nach.
        soc = min(usable_cap, soc + h["pv_surplus"])
        # 2. Netzladung in Lade-Stunden.
        if i in charge:
            soc = min(usable_cap, soc + charge_per_hour)
        # 3. Akku-Nutzung in Entlade-Stunden.
        if i in discharge:
            need = h["net_load"]
            if soc + EPS < need:
                ok = False
                soc = 0.0
            else:
                soc -= need
    return ok, soc


def _select_discharge(horizon, charge, usable_now, usable_cap, charge_per_hour):
    """Greedy: teuerste Stunden zuerst als DISCHARGE, solange energetisch machbar."""
    discharge = set()
    need_idx = [i for i, h in enumerate(horizon) if h["net_load"] > EPS]
    # Nach Preis absteigend – die teuersten Stunden zuerst bedienen.
    for i in sorted(need_idx, key=lambda i: horizon[i]["price"], reverse=True):
        trial = discharge | {i}
        ok, _ = _simulate(horizon, trial, charge, usable_now, usable_cap, charge_per_hour)
        if ok:
            discharge = trial
    return discharge


def plan_dispatch(horizon, usable_now, usable_cap,
                  charge_advantage_eur=0.0, charge_per_hour=None, allow_charge=True,
                  min_spread_eur=0.0):
    """Erstellt den Einsatzplan.

    horizon: Liste von dicts (Stunde 0 = aktuelle Stunde), je mit:
        label (str, z.B. "22:00"), price (€/kWh), net_load (kWh, Verbrauch−PV),
        pv_surplus (kWh, PV−Verbrauch).
    usable_now: aktuell nutzbare Akku-Energie (SoC über Reserve) in kWh.
    usable_cap: maximale nutzbare Akku-Energie in kWh (für Ladehub).
    charge_advantage_eur: Mindest-Preisvorteil (€/kWh), damit Netzladen lohnt.
    charge_per_hour: angenommene Netz-Ladeleistung je Stunde (kWh). Default: usable_cap.
    min_spread_eur: Mindest-Preisabstand (€/kWh) zwischen einer gesparten
        (günstigen) Stunde und der späteren teuren Stunde, damit das Sparen
        überhaupt lohnt. Verhindert unnötige Zyklen für Centbeträge.

    Rückgabe: dict mit actions[], current_action, discharge/hold/charge-Indizes,
    sowie zusammengefassten Fenstern.
    """
    n = len(horizon)
    if n == 0:
        return {
            "actions": [], "current_action": "IDLE", "current_reason": "Keine Prognosedaten",
            "discharge": [], "hold": [], "charge": [],
            "windows": {"discharge": "", "hold": "", "charge": ""},
        }

    if charge_per_hour is None:
        charge_per_hour = usable_cap

    # 1. Basis-Auswahl der Entlade-Stunden (ohne Netzladen).
    charge = set()
    discharge = _select_discharge(horizon, charge, usable_now, usable_cap, charge_per_hour)

    # 2. Netzladen: so viele günstige Stunden nachladen, wie nötig, um die teuren
    #    Bedarfsstunden zu decken – begrenzt durch die Ladeleistung (charge_per_hour)
    #    und die Kapazität. Pro Durchgang wird eine günstige Stunde ergänzt, solange
    #    das die Abdeckung teurer Stunden verbessert.
    if allow_charge:
        improved = True
        while improved:
            improved = False
            need_idx = [i for i, h in enumerate(horizon) if h["net_load"] > EPS]
            uncovered = [i for i in need_idx if i not in discharge and i not in charge]
            if not uncovered:
                break
            # Teuerste noch nicht gedeckte Bedarfsstunde als Ziel.
            target = max(uncovered, key=lambda i: horizon[i]["price"])
            target_price = horizon[target]["price"]
            # Günstigste freie Stunde VOR dem Ziel mit ausreichendem Preisvorteil.
            candidates = sorted(
                [i for i in range(target)
                 if i not in charge and i not in discharge
                 and horizon[i]["price"] + charge_advantage_eur < target_price],
                key=lambda i: horizon[i]["price"],
            )
            for c in candidates:
                trial_charge = charge | {c}
                new_discharge = _select_discharge(horizon, trial_charge, usable_now, usable_cap, charge_per_hour)
                if len(new_discharge) > len(discharge):
                    charge = trial_charge
                    discharge = new_discharge
                    improved = True
                    break  # neu bewerten, ob noch Bedarfsstunden offen sind

    # 3. HOLD bestimmen: Bedarfsstunde, die NICHT entladen wird und VOR einer
    #    späteren Entlade-Stunde liegt -> Akku dort sparen. Nur wenn der
    #    Preisabstand zur teuersten späteren Entlade-Stunde groß genug ist
    #    (min_spread_eur), sonst lohnt das Sparen nicht.
    hold = set()
    for i, h in enumerate(horizon):
        if i in discharge or i in charge:
            continue
        if h["net_load"] <= EPS:
            continue
        later_prices = [horizon[j]["price"] for j in discharge if j > i]
        if not later_prices:
            continue
        if (max(later_prices) - h["price"]) > min_spread_eur:
            hold.add(i)

    # 4. Aktionsliste.
    actions = []
    for i, h in enumerate(horizon):
        if i in charge:
            actions.append("CHARGE")
        elif i in discharge:
            actions.append("DISCHARGE")
        elif i in hold:
            actions.append("HOLD")
        else:
            actions.append("IDLE")

    current_action = actions[0]
    reason_map = {
        "CHARGE": "Günstige Stunde – Akku wird aus dem Netz geladen",
        "DISCHARGE": "Teure Stunde – Akku wird genutzt",
        "HOLD": "Akku wird für spätere teure Stunden gespart",
        "IDLE": "Normalbetrieb (PV/Batterie)",
    }

    return {
        "actions": actions,
        "current_action": current_action,
        "current_reason": reason_map[current_action],
        "discharge": sorted(discharge),
        "hold": sorted(hold),
        "charge": sorted(charge),
        "windows": {
            "discharge": _windows(horizon, discharge),
            "hold": _windows(horizon, hold),
            "charge": _windows(horizon, charge),
        },
    }


def _windows(horizon, idx_set):
    """Fasst zusammenhängende Stunden-Indizes zu lesbaren Fenstern zusammen."""
    if not idx_set:
        return ""
    idx = sorted(idx_set)
    parts = []
    start = prev = idx[0]
    for i in idx[1:]:
        if i == prev + 1:
            prev = i
        else:
            parts.append(_fmt(horizon, start, prev))
            start = prev = i
    parts.append(_fmt(horizon, start, prev))
    return ", ".join(parts)


def _fmt(horizon, start, end):
    s = horizon[start]["label"]
    # Ende = Beginn der Folgestunde, wenn vorhanden, sonst +1h aus dem Label.
    if end + 1 < len(horizon):
        e = horizon[end + 1]["label"]
    else:
        try:
            eh = (int(horizon[end]["label"][:2]) + 1) % 24
            e = f"{eh:02d}:00"
        except (ValueError, IndexError):
            e = horizon[end]["label"]
    return f"{s}–{e}"
