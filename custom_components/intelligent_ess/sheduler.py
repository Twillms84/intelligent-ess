import datetime
import logging

_LOGGER = logging.getLogger(__name__)

def calculate_strategy(options, hass_states, ai_profile=None):
    """Zentrale Entscheidungslogik für Timer, KI und Strategie."""
    try:
        now_dt = datetime.datetime.now()
        now_time = now_dt.time()
        current_hour = now_dt.hour

        def get_time_from_config(key):
            """Holt die Zeit aus der Config."""
            return options.get(key)

        def is_in_time_range(start_key, end_key, active_key, label):
            """Prüft, ob der Schalter AN ist UND die aktuelle Uhrzeit im Slot liegt."""
            # 1. SCHALTER PRÜFEN
            is_enabled = options.get(active_key, False)
            if not is_enabled:
                return False

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
                
                # Inaktiver Slot
                if s == e:
                    return False

                if s <= e:
                    is_active = s <= now_time <= e
                else: 
                    is_active = now_time >= s or now_time <= e
                
                if is_active:
                    _LOGGER.info("%s AKTIV: Schalter ist AN und Zeit passt (%s bis %s, Jetzt: %s)", label, start_str, end_str, now_time.strftime('%H:%M'))
                return is_active

            except Exception as ex:
                _LOGGER.error("Fehler beim Zeit-Parsen (%s/%s): %s", start_key, end_key, ex)
                return False

        # --- 1. PRÜFUNG: MANUELLES LADEN (Slot 1 & 2) ---
        if is_in_time_range("man_charge_s1_start", "man_charge_s1_end", "man_charge_s1_enabled", "LADE-SLOT 1") or \
           is_in_time_range("man_charge_s2_start", "man_charge_s2_end", "man_charge_s2_enabled", "LADE-SLOT 2"):
            return "LADEN", "Manueller Lade-Timer aktiv", False

        # --- 2. PRÜFUNG: MANUELLE SPERRE (Slot 1) ---
        if is_in_time_range("man_hold_s1_start", "man_hold_s1_end", "man_hold_s1_enabled", "SPERR-SLOT 1"):
            # Geändert von SPERRE zu HOLD für Konsistenz mit Sensor/Coordinator
            return "HOLD", "Manuelle Entladesperre aktiv", True

        # --- 3. PRÜFUNG: KI-AUTOMATIK (Tibber) ---
        if options.get("auto_charge_enabled", True) and ai_profile:
            # ai_profile ist eine Liste mit 24 Einträgen (0 oder 1)
            if current_hour < len(ai_profile) and ai_profile[current_hour] == 1:
                return "LADEN", "KI-Preisoptimierung empfiehlt Ladung", False

        # --- 4. STANDARD: NORMALBETRIEB ---
        # Geändert von AUTO zu NORMAL für Konsistenz
        return "NORMAL", "Normalbetrieb (PV/Batterie)", False

    except Exception as e:
        _LOGGER.error("Schwerer Fehler im Scheduler: %s", e)
        return "NORMAL", f"Scheduler Fehler: {e}", False