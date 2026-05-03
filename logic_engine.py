from datetime import datetime

class ESSLogicEngine:
    @staticmethod
    def calculate_strategy(soc, cap, min_soc, rest_demand, solar_fc, prices):
        """Berechnet die statische Backup-Strategie, falls der KI-Scheduler ausfällt."""
        kwh_now = max(0, (cap * (soc - min_soc)) / 100)
        
        # Preis-Check
        cur_p = prices[0]['price'] if prices else 0.30
        future_prices = [p['price'] for p in prices[1:24]] if prices else []
        m_peak = max(future_prices) if future_prices else cur_p
        is_cheap = cur_p <= (min(future_prices) * 1.15) if future_prices else True

        # Strategie-Entscheidung
        if (kwh_now + solar_fc) < rest_demand:
            if is_cheap: 
                return "LADEN", "Laden: Akku + Solar reicht nicht."
            
            # Geändert von SPERREN zu HOLD für systemweite Konsistenz
            return "HOLD", f"Sperren: Akku sparen für Peak ({round(m_peak*100,1)}ct)."
        
        return "NORMAL", "Betrieb über Akku."

    @staticmethod
    def smart_switch_control(net_watt, threshold, timers, switches, hass_states):
        """Logik für intelligentes Überschuss-Schalten mit Ausfallschutz."""
        actions = []
        now = datetime.now().timestamp()
        
        if net_watt < threshold: # Überschuss
            for s in switches:
                state = hass_states.get(s)
                # Sicherheitsabfrage, falls Entität offline ist
                if state and state.state == "off":
                    actions.append((s, "turn_on"))
                    return actions # Nur einen pro Minute einschalten
                    
        elif net_watt > 100: # Bezug
            for s in reversed(switches):
                state = hass_states.get(s)
                if state and state.state == "on" and (now - timers.get(s, 0)) > 300:
                    actions.append((s, "turn_off"))
                    return actions
                    
        return actions