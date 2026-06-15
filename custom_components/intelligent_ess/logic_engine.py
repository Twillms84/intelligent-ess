"""Hilfslogik fuer das intelligente Schalten von Ueberschuss-Verbrauchern.

Hinweis: Die fruehere statische `calculate_strategy` wurde entfernt. Die
zentrale Strategie-Entscheidung liegt ausschliesslich in `scheduler.py`, damit
es nur eine Quelle der Wahrheit gibt.
"""

from homeassistant.util import dt as dt_util


class ESSLogicEngine:
    @staticmethod
    def smart_switch_control(net_watt, threshold, timers, switches, hass_states):
        """Schaltet Ueberschuss-Verbraucher gestaffelt ein/aus.

        net_watt: aktuelle Netzleistung (negativ = Einspeisung/Ueberschuss,
                  positiv = Bezug).
        threshold: Einschalt-Schwelle (z.B. -1000 W Ueberschuss).
        timers:    dict {entity_id: einschalt_timestamp} fuer Mindestlaufzeit.
        switches:  Liste der zu steuernden Schalter-Entitaeten.
        hass_states: hass.states (zum Lesen des aktuellen Schalterzustands).

        Rueckgabe: Liste von (entity_id, "turn_on"|"turn_off")-Aktionen,
        maximal eine Aktion pro Aufruf (sanftes Ein-/Ausschalten).
        """
        actions = []
        if not switches:
            return actions

        now = dt_util.utcnow().timestamp()

        if net_watt < threshold:  # Ueberschuss vorhanden -> einen Verbraucher zuschalten
            for s in switches:
                state = hass_states.get(s)
                # Sicherheitsabfrage, falls Entitaet offline ist
                if state and state.state == "off":
                    actions.append((s, "turn_on"))
                    return actions  # nur einen pro Zyklus einschalten

        elif net_watt > 100:  # Netzbezug -> zuletzt zugeschalteten Verbraucher wieder abwerfen
            for s in reversed(switches):
                state = hass_states.get(s)
                # Mindestlaufzeit von 5 Minuten respektieren (Schutz vor Takten)
                if state and state.state == "on" and (now - timers.get(s, 0)) > 300:
                    actions.append((s, "turn_off"))
                    return actions

        return actions
