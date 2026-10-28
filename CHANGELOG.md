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

## 1.0.3 – Hauptschalter, Vorschau & stabilere Entladesperre

### Ladeautomatik ist jetzt ein echter Hauptschalter
- Ist „Ladeautomatik aktiv" AUS, unternimmt die Integration **nichts** mehr von
  selbst: kein SmartCharge, kein (autonomes) SmartHold, keine Smart-Switches.
  Der Wechselrichter wird in den Normalzustand freigegeben, Laden aus, eine
  aktive Sperre wird gelöst. Behebt das „Eigenleben" trotz deaktivierter Automatik.
- **Hinweis:** Der Hauptschalter ist standardmäßig AUS. Nach dem Update muss er
  einmal bewusst eingeschaltet werden, damit die Automatik arbeitet.

### Vorschau vor der Aktivierung
- Beim Einschalten von „Ladeautomatik aktiv" erscheint automatisch eine
  Benachrichtigung mit dem geplanten Verhalten (SmartCharge-Fenster,
  SmartHold-Status, Kennzahlen) – ohne dass etwas ausgeführt wird.
- Neuer Button **„Automatik-Vorschau"** zeigt denselben Dry-Run jederzeit an.

### Zuverlässigere Entladesperre
- WR-Limit wird jetzt robust gesetzt: Re-Assert bei Abweichung, 1× Wiederholung
  bei Fehler, **Warnung im Log** statt stillem Verschlucken, blockierender
  Service-Call. Nicht verfügbare Limit-Entität wird deutlich geloggt.
- **Anti-Flatter-Latch** für autonomes SmartHold: Einmal ausgelöst, bleibt die
  Sperre bis zur Morgen-Peak-Stunde aktiv (max. 10 h) und springt nicht mehr
  minütlich an/aus, wenn Werte um die Schwelle schwanken. Fällt automatisch,
  sobald der Akku leer ist oder der Peak vorbei ist.

## 1.0.4 – Detaillierter Fahrplan

- Der Sensor **„Fahrplan"** zeigt jetzt im Klartext, was die Automatik vorhat:
  aktuelle Aktion, nächste geplante Aktion, konkrete Lade- und Sperrfenster
  sowie die Kennzahlen (nutzbarer Akku, PV-Rest/-Prognose, Nacht-Defizit,
  Preis-Tief/-Hoch mit Uhrzeiten).
- Neue Sensor-Attribute (`fahrplan_text`, `modus`, `aktion`, `naechste_aktion`,
  `ladefenster`, `sperrfenster`, `smartcharge_erlaubt`, `smarthold_erlaubt`
  und alle Kennzahlen einzeln) für Dashboard-Karten und Automationen.
- Ist die Ladeautomatik AUS, steht das klar im Fahrplan („AUS – keine Eingriffe").

Hinweis: Diese Verbesserungen wirken nur, wenn Home Assistant die Integration
in Version 1.0.4 tatsächlich lädt (HACS-Update + HA-Neustart). Ein Bearbeiten
des Git-Repos allein ändert die laufende Instanz nicht.

## 1.0.5 – Klares Drei-Ebenen-Steuermodell

Komplette Neuordnung der Steuerung, damit "AUS" wirklich AUS heißt und die
Kontrolle beim Nutzer bleibt:

- **Ebene 1 – Manuelle Zeitfenster** (Laden Slot 1/2, Entladesperre): gelten
  IMMER, unabhängig von der Automatik. Feste, selbst eingetragene Fenster.
- **Ebene 2 – Automatik** (Schalter „Ladeautomatik aktiv"): nur wenn AN greifen
  SmartCharge, autonomes SmartHold und das Überschuss-Schalten der Smart-Switches.
- **AUS heißt AUS:** Ist die Automatik aus und kein manuelles Fenster aktiv,
  unternimmt die Integration KEINE Eigenaktion. Der Wechselrichter wird
  freigegeben. Empfehlungen werden weiterhin berechnet und angezeigt.
- **Neuer Button „Empfehlung übernehmen":** schreibt die aktuelle Empfehlung
  (Lade-/Sperrfenster) einmalig in die manuellen Zeitfenster – ohne Dauer-Automatik.
- Fahrplan zeigt jetzt zusätzlich die **Empfehlung** separat an (`empfehlung`),
  auch wenn die Automatik aus ist, plus Quelle der aktiven Aktion (manuell/Automatik).

Wichtig: Diese Logik wirkt erst, wenn Home Assistant die Integration in 1.0.5
tatsächlich lädt (HACS-Update auf 1.0.5 + HA-Neustart). Reines Bearbeiten des
Git-Repos ändert die laufende Instanz nicht.
