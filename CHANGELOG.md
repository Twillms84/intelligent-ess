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
