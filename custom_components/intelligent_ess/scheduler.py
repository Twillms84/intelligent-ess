import datetime
import logging

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


def _slot_active(options, start_key, end_key, active_key, label):
    """Prüft, ob ein Zeitfenster-Schalter AN ist UND die aktuelle Uhrzeit im Slot liegt."""
    if not options.get(active_key, False):
        return False

    start_str = options.get(start_key)
    end_str = options.get(end_key)
    if not start_str or not end_str:
        return False

    try:
        now_time = dt_util.now().time()
        s_parts = list(map(int, str(start_str).split(':')[:2]))
        e_parts = list(map(int, str(end_str).split(':')[:2]))
        s = datetime.time(s_parts[0], s_parts[1])
        e = datetime.time(e_parts[0], e_parts[1])

        if s == e:  # Inaktiver Slot
            return False

        if s <= e:
            is_active = s <= now_time <= e
        else:  # Über Mitternacht
            is_active = now_time >= s or now_time <= e

        if is_active:
            _LOGGER.info("%s AKTIV (%s–%s, jetzt %s)", label, start_str, end_str, now_time.strftime('%H:%M'))
        return is_active
    except Exception as ex:
        _LOGGER.error("Fehler beim Zeit-Parsen (%s/%s): %s", start_key, end_key, ex)
        return False


def evaluate_manual_windows(options):
    """EBENE 1 – vom Nutzer eingetragene feste Zeitfenster.

    Diese gelten IMMER, unabhängig vom Automatik-Hauptschalter.
    Rückgabe: (strat, msg, lock_needed) oder None, wenn kein Fenster aktiv ist.
    """
    if _slot_active(options, "man_charge_s1_start", "man_charge_s1_end", "man_charge_s1_enabled", "LADE-SLOT 1") or \
       _slot_active(options, "man_charge_s2_start", "man_charge_s2_end", "man_charge_s2_enabled", "LADE-SLOT 2"):
        return "LADEN", "Manuelles Lade-Zeitfenster aktiv", False

    if _slot_active(options, "man_hold_s1_start", "man_hold_s1_end", "man_hold_s1_enabled", "SPERR-SLOT 1"):
        return "HOLD", "Manuelle Entladesperre aktiv", True

    return None


def evaluate_automatic(ai_profile):
    """EBENE 2 – automatische KI-Preisoptimierung (nur bei aktivierter Automatik).

    Rückgabe: (strat, msg, lock_needed) oder None.
    """
    if not ai_profile:
        return None
    current_hour = dt_util.now().hour
    if current_hour < len(ai_profile) and ai_profile[current_hour] == 1:
        return "LADEN", "KI-Preisoptimierung empfiehlt Ladung", False
    return None


def calculate_strategy(options, hass_states, ai_profile=None):
    """Kompatibilitäts-Wrapper: manuelle Fenster zuerst, dann Automatik.

    Hinweis: Der Coordinator schichtet seit v1.0.5 selbst (manuell / automatik /
    aus). Diese Funktion bleibt für Rückwärtskompatibilität erhalten.
    """
    try:
        manual = evaluate_manual_windows(options)
        if manual is not None:
            return manual

        if options.get("auto_charge_enabled", False):
            auto = evaluate_automatic(ai_profile)
            if auto is not None:
                return auto

        return "NORMAL", "Normalbetrieb (PV/Batterie)", False
    except Exception as e:
        _LOGGER.error("Schwerer Fehler im Scheduler: %s", e)
        return "NORMAL", f"Scheduler Fehler: {e}", False
