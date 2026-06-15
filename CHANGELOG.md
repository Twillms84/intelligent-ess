# Changelog

## 1.0.1 – Fehlerbehebungen & Aufräumarbeiten

### Kritische Laufzeit-Fehler
- **KI-Button:** Im Prompt verwendete Variablen (`min_p`, `max_p`, `min_t`,
  `max_t`, `avg_p`, `price_summary`) wurden nie definiert → garantierter
  `NameError`. Werte werden jetzt aus `ai_price_summary` abgeleitet, plus
  kompakte 12h-Preisübersicht.
- **KI-Button:** Falscher Daten-Key `autarky_start_tomorrow` →
  `autarky_time_tomorrow` korrigiert.
- **Autarkie-Sensor:** `calculate_autarky_time_tomorrow` bekam einen einzelnen
  Float statt stündlicher Werte und lieferte immer „Keine Stundenwerte". Neuer
  Helfer `get_solar_forecast_hourly` liest Solcast/Forecast.Solar-Attribute.

### Wirkungslose / tote Logik repariert
- **Automatische Preisoptimierung:** `calculate_strategy` wurde ohne
  `ai_profile` aufgerufen → KI-Lade-Branch nie aktiv. Coordinator berechnet nun
  via `calculate_best_profile` einen 24h-Fahrplan und übergibt ihn.
- **Hold-Spartopf:** `analytics` prüfte `"SPERRE"`, System nutzt aber `"HOLD"`
  → Topf blieb immer 0. Angeglichen.
- **calculate_best_profile:** Einheiten-Mismatch (ct vs. €/kWh) behoben,
  korrekter Solar-Key (`solar_hourly`), robustes Zeit-Parsing.
- **Smart-Switches:** `smart_switch_control` war nicht verdrahtet. Coordinator
  berechnet `net_watt` und steuert die konfigurierten Überschuss-Verbraucher.
- **logic_engine:** redundante statische `calculate_strategy` entfernt
  (Scheduler ist alleinige Quelle der Wahrheit).

### Genutzte Konfiguration
- `wr_lock_value` wird verwendet (statt hartkodierter `0.0`).
- KI-Button nutzt den konfigurierten `conversation_agent` statt einer
  hartkodierten Agent-ID.
- Aktueller Preis kommt aus `tibber_price_sensor` (Fallback: Forecast-Sensor).

### Struktur & Qualität
- Doppelter `async_config_entry_first_refresh()` in `sensor.py` entfernt.
- `OptionsFlow` auf aktuelles HA-Muster umgestellt (kein eigenes `__init__`).
- Zeitzonensichere Zeit (`dt_util.now()`) im Scheduler.
- `sheduler.py` → `scheduler.py` umbenannt.
- `de.json` vollständig neu (passt zu Config- und Options-Flow, inkl.
  Options-Schritt und Feldbeschreibungen).
- `manifest.json`: konsistente URLs/codeowner, harte Abhängigkeiten →
  `after_dependencies`, `issue_tracker`, `integration_type`.
- Neu: `.gitignore`, `hacs.json`, `LICENSE`. `__pycache__` und verwaiste
  `.pyc` (`smart_charging`, `smart_discharging`) entfernt.

## 1.0.2 – Strategie-Redesign (SmartCharge / SmartHold)

- Neues zentrales Modul `strategy.py` mit deterministischen Gates:
  - **SmartCharge** nur, wenn prognostiziert zu wenig PV-Ertrag über den Tag
    kommt (Morgen-PV vs. erwarteter Tagesbedarf; Fallback: Nacht-Bilanz).
  - **SmartHold** nur, wenn die Nachtreserve nicht reicht UND der Höchstpreis
    morgens (5–10 Uhr) den aktuellen Preis um mehr als `price_delta_threshold`
    (ct) übersteigt.
- KI-Button neu: arbeitet auf den vorberechneten Basiswerten/Gates, Prompt um
  die zwei Gates herum gebaut. Code-Guards setzen die Gates hart durch – die KI
  kann kein geschlossenes Gate überstimmen; veraltete Timer werden deaktiviert.
- Coordinator: SmartCharge gated den autonomen Lade-Fahrplan, SmartHold wird
  autonom (ohne LLM) angewandt, sofern keine höhere Priorität aktiv ist.
- `price_delta_threshold` reaktiviert (steuert die SmartHold-Preisschwelle).
- Gate-Zustände als Attribute am Action-Sensor sichtbar.
- Entfernt: tote Felder `sun_yield_threshold` und `solar_buy_threshold`.
- `wr_unlock_value`-Default vereinheitlicht (80). `const.py` aufgeräumt.
  Doppelte README im Komponentenordner entfernt.
