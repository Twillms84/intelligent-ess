import datetime
import logging

_LOGGER = logging.getLogger(__name__)

def calculate_strategy(options, hass_states):
    """Zentrale Entscheidungslogik für Timer und Strategie."""
    try:
        now = datetime.datetime.now().time()

        def get_time_from_config(key):
            """Holt die Zeit aus der Config."""
            val = options.get(key)
            if not val:
                return None
            return val

        def is_in_time_range(start_key, end_key, active_key, label):
            """Prüft, ob der Schalter AN ist UND die aktuelle Uhrzeit im Slot liegt."""
            # 1. SCHALTER PRÜFEN: Ist der Slot über das Dashboard eingeschaltet?
            is_enabled = options.get(active_key, False)
            if not is_enabled:
                return False # Schalter aus -> Slot ignorieren

            # 2. ZEIT PRÜFEN
            start_str = get_time_from_config(start_key)
            end_str = get_time_from_config(end_key)
            
            if not start_str or not end_str:
                return False
                
            try:
                # Wir erwarten "HH:MM:SS" oder "HH:MM"
                s_parts = list(map(int, start_str.split(':')[:2]))
                e_parts = list(map(int, end_str.split(':')[:2]))
                s = datetime.time(s_parts[0], s_parts[1])
                e = datetime.time(e_parts[0], e_parts[1])
                
                # Falls die Zeiten gleich sind (z.B. 00:00 bis 00:00), ist der Slot inaktiv
                if s == e:
                    return False

                is_active = False
                if s <= e:
                    is_active = s <= now <= e
                else: 
                    is_active = now >= s or now <= e
                
                if is_active:
                    _LOGGER.info("%s AKTIV: Schalter ist AN und Zeit passt (%s bis %s, Jetzt: %s)", label, start_str, end_str, now.strftime('%H:%M'))
                return is_active

            except Exception as ex:
                _LOGGER.error("Fehler beim Zeit-Parsen (%s/%s): %s", start_key, end_key, ex)
                return False

        # --- 1. PRÜFUNG: LADEN (Slot 1 & 2) ---
        # Hier nutzen wir nun exakt die Namen aus deiner switch.py!
        if is_in_time_range("man_charge_s1_start", "man_charge_s1_end", "man_charge_s1_enabled", "LADE-SLOT 1") or \
           is_in_time_range("man_charge_s2_start", "man_charge_s2_end", "man_charge_s2_enabled", "LADE-SLOT 2"):
            return "LADEN", "Lade-Timer aktiv", False

        # --- 2. PRÜFUNG: SPERRE (Slot 1) ---
        if is_in_time_range("man_hold_s1_start", "man_hold_s1_end", "man_hold_s1_enabled", "SPERR-SLOT 1"):
            return "SPERRE", "Entladesperre aktiv", True

        # --- 3. STANDARD: AUTO ---
        return "AUTO", "Normalbetrieb (PV/Batterie)", False

    except Exception as e:
        _LOGGER.error("Schwerer Fehler im Scheduler: %s", e)
        return "AUTO", f"Scheduler Fehler: {e}", False