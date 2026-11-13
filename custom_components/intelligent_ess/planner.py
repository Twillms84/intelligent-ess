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
            "projection": {"trace": [], "min_kwh": round(usable_now, 2), "min_at": None,
                           "empty_at": None, "end_kwh": round(usable_now, 2)},
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
            # WICHTIG: Nicht nur die EINE teuerste unüberdeckte Stunde prüfen.
            # Lässt sich die teuerste nicht laden (z.B. weil davor keine Stunde
            # günstig genug ist), wären sonst alle übrigen teuren Stunden – und
            # damit die ganze Nacht-Arbitrage – verloren. Darum ALLE offenen
            # Bedarfsstunden nach Preis absteigend als Ziel versuchen.
            for target in sorted(uncovered, key=lambda i: horizon[i]["price"], reverse=True):
                target_price = horizon[target]["price"]
                # Günstigste freie Stunde VOR dem Ziel mit ausreichendem Preisvorteil.
                candidates = sorted(
                    [i for i in range(target)
                     if i not in charge and i not in discharge
                     # Grenzwert inklusive (>=): "10 ct Vorteil" soll auch für
                     # eine genau 10 ct teurere Stunde laden, nicht erst ab 10,1 ct.
                     and horizon[i]["price"] + charge_advantage_eur <= target_price + EPS],
                    key=lambda i: horizon[i]["price"],
                )
                found = False
                for c in candidates:
                    trial_charge = charge | {c}
                    new_discharge = _select_discharge(horizon, trial_charge, usable_now, usable_cap, charge_per_hour)
                    if len(new_discharge) > len(discharge):
                        charge = trial_charge
                        discharge = new_discharge
                        improved = True
                        found = True
                        break
                if found:
                    break  # Plan verändert -> offene Stunden neu bewerten

    # 3. HOLD bestimmen: Bedarfsstunde, die NICHT entladen wird und VOR einer
    #    späteren Entlade-Stunde liegt -> Akku dort sparen. Nur wenn:
    #    - der Preisabstand zur teuersten späteren Entlade-Stunde groß genug ist
    #      (min_spread_eur), sonst lohnt das Sparen nicht, UND
    #    - an der Stelle überhaupt Akku-Energie vorhanden ist (sonst gibt es
    #      nichts zu sparen – ein leerer Akku wird nicht "gehalten").
    hold = set()
    # Verfügbare Energie je Stunde grob mitführen (nur Zuflüsse: Start + PV + Laden).
    avail = usable_now
    for i, h in enumerate(horizon):
        avail = min(usable_cap, avail + h["pv_surplus"])
        if i in charge:
            avail = min(usable_cap, avail + charge_per_hour)

        if i in discharge or i in charge:
            continue
        if h["net_load"] <= EPS:
            continue
        if avail <= EPS:
            continue  # leerer Akku -> nichts zu sparen
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

    # 5. Energie-Projektion: Akkuverlauf entlang des Plans simulieren.
    #    So ist sichtbar, wie viel bis Tagesende bleibt und ob/wann er leer wird.
    projection = _project_soc(horizon, actions, usable_now, usable_cap, charge_per_hour)

    # 6. Geschätzte Netz-Arbitrage-Ersparnis des Plans.
    savings = _estimate_savings(horizon, actions, charge_per_hour)

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
        "projection": projection,
        "savings": savings,
    }


# Round-Trip-Wirkungsgrad (Akku + Wechselrichter). ~10 % Ladeverlust.
ROUNDTRIP_EFFICIENCY = 0.90


def _estimate_savings(horizon, actions, charge_per_hour, efficiency=ROUNDTRIP_EFFICIENCY):
    """Schätzt die Ersparnis der NETZ-Arbitrage des Plans:
    günstig geladene Energie wird (abzüglich Ladeverlust) in den teuersten
    Entladestunden genutzt. Ersparnis = vermiedener teurer Netzbezug − Ladekosten.

    Bewusst konservativ/optimistisch beschriftet als "könnte" – die geladene
    Energie wird den teuersten Entladestunden zugeordnet (Best-Case-Zuordnung).
    PV-/Bestandsenergie fließt NICHT ein, nur der Netz-Kauf-vs-Nutz-Teil.
    """
    charge_idx = [i for i, a in enumerate(actions) if a == "CHARGE"]
    discharge_idx = [i for i, a in enumerate(actions) if a == "DISCHARGE"]
    empty = {"saving_eur": 0.0, "charged_kwh": 0.0, "buy_cost_eur": 0.0,
             "use_value_eur": 0.0, "loss_kwh": 0.0}
    if not charge_idx or not discharge_idx:
        return empty

    charged_kwh = len(charge_idx) * charge_per_hour
    buy_cost = sum(charge_per_hour * horizon[i]["price"] for i in charge_idx)

    # Nach Ladeverlust nutzbare Energie auf die teuersten Entladestunden verteilen.
    usable = charged_kwh * efficiency
    remaining = usable
    use_value = 0.0
    for i in sorted(discharge_idx, key=lambda j: horizon[j]["price"], reverse=True):
        if remaining <= EPS:
            break
        take = min(remaining, horizon[i]["net_load"])
        use_value += take * horizon[i]["price"]
        remaining -= take

    return {
        "saving_eur": round(use_value - buy_cost, 2),
        "charged_kwh": round(charged_kwh, 2),
        "buy_cost_eur": round(buy_cost, 2),
        "use_value_eur": round(use_value, 2),
        "loss_kwh": round(charged_kwh * (1.0 - efficiency), 2),
    }


def _project_soc(horizon, actions, usable_now, usable_cap, charge_per_hour):
    """Simuliert den Akkuverlauf (kWh) entlang des Plans.

    Rückgabe: min_kwh (+ Uhrzeit), empty_at (erste Stunde ~0), end_kwh,
    und der stündliche Verlauf.
    """
    soc = usable_now
    trace = []
    min_kwh = usable_now
    min_at = None
    empty_at = None
    had_energy = usable_now > EPS
    for i, h in enumerate(horizon):
        # WICHTIG: Der eingetragene Wert ist der SoC zu BEGINN der Stunde (also am
        # Label-Zeitpunkt). Beispiel: Wird 23:00–00:00 geladen, zeigt 23:00 noch
        # den leeren Akku und erst 00:00 die geladene Energie – so, wie es der
        # Nutzer liest. (Vorher war es der Stand am Stundenende -> wirkte um 1h
        # zu früh.)
        start_soc = round(soc, 2)
        trace.append({"label": h["label"], "soc_kwh": start_soc})
        if start_soc < min_kwh:
            min_kwh = start_soc
            min_at = h["label"]
        # "Leer ab" meint das Leerlaufen entlang des Plans, nicht den (ggf. schon
        # leeren) Startzustand: erst melden, nachdem überhaupt Energie da war.
        if had_energy and empty_at is None and start_soc <= EPS:
            empty_at = h["label"]

        # Stunde anwenden -> Stand zu Beginn der nächsten Stunde.
        soc = min(usable_cap, soc + h["pv_surplus"])
        act = actions[i] if i < len(actions) else "IDLE"
        if act == "CHARGE":
            soc = min(usable_cap, soc + charge_per_hour)
        elif act == "HOLD":
            pass  # Netz versorgt das Haus, Akku unverändert
        else:  # DISCHARGE oder IDLE: Akku deckt den Restbedarf
            soc = max(0.0, soc - h["net_load"])
        if soc > EPS:
            had_energy = True

    end_kwh = round(soc, 2)
    if end_kwh < min_kwh:
        min_kwh = end_kwh
    return {
        "trace": trace,
        "min_kwh": round(min_kwh, 2),
        "min_at": min_at,
        "empty_at": empty_at,
        "end_kwh": end_kwh,
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
