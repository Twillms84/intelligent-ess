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

## 1.0.6 – Intelligenter Tages-Einsatzplaner (statt Einzel-Peak)

Die SmartHold/SmartCharge-Logik war zu simpel: Sie prüfte nur eine einzelne
Morgen-Preisspitze und sperrte dann stumpf bis dahin – ohne den Verlauf der
ganzen Preiskurve, den Bedarf je Abschnitt oder die PV-Nachladung.

Neu: ein echter **Tages-Einsatzplaner** (`planner.py`):
- Plant über den gesamten Prognosehorizont (Preis + PV-Ertrag + Verbrauch je Stunde).
- Teilt die begrenzte Akku-Energie den **teuersten** Stunden zu, die sie noch
  decken kann – per Vorwärtssimulation inkl. PV-Nachladung (keine Energie aus
  der Zukunft für frühere Stunden).
- **Entladesperre (HOLD)** greift jetzt gezielt nur für die günstigen Stunden
  VOR einer späteren teuren Stunde, für die die Energie knapp ist – nicht mehr
  pauschal „21–7 Uhr".
- **Netzladen (CHARGE)** nur, wenn die teuren Stunden sonst mangels Energie
  nicht gedeckt sind, und dann in den günstigsten Stunden davor.
- Skaliert mit dem Ladestand: viel Akku → kaum Eingriff; wenig Akku → gezieltes
  Sparen/Laden.

Der **Fahrplan** zeigt jetzt den ganzen Tagesplan: „Laden", „Akku sparen",
„Akku nutzen" als Zeitfenster.

Hinweis: wirkt erst nach HACS-Update auf 1.0.6 + HA-Neustart.

### Vorschau & KI-Check nutzen jetzt denselben Plan
- Die **Automatik-Vorschau** zeigt den Tagesplan des Einsatzplaners (Laden /
  Akku sparen / Akku nutzen) statt der alten Einzel-Peak-Gates.
- Der **KI Strategie-Check** erfindet keine eigenen Uhrzeiten mehr. Er bekommt
  den fertigen, optimierten Tagesplan samt Stundentabelle (Preis/PV/Bedarf je
  Stunde) und erklärt ihn verständlich plus Plausibilitätsprüfung. Damit ist er
  mindestens so schlüssig wie die Rechenlogik – kein widersprüchlicher Unsinn
  mehr wie "Akku 9:35 leer, lade um 14:00".

## 1.0.7 – Aufräumen & Options-Bugfix

Bereinigung redundanter/überflüssiger Elemente:
- **Entfernt:** Sensor „Letztes Ereignis" (wurde nie befüllt), Button
  „Automatik-Vorschau" (doppelt zum Fahrplan-Sensor), die Anzeige-Flags
  `smartcharge_erlaubt`/`smarthold_erlaubt` am Action-Sensor (seit dem Planer
  ohne Funktion). Toter Code im Coordinator/Scheduler entfernt.
- **Keine Doppel-Bedienung mehr:** Standardverbrauch, Min. SOC und
  Lade-Preisvorteil sind nur noch als Number-Entitäten live regelbar, nicht
  mehr zusätzlich im Options-Dialog.
- **`price_delta_threshold` sinnvoll verdrahtet** (statt gelöscht): dient jetzt
  als „Min. Preisabstand für Sperre" – der Planer spart den Akku nur, wenn der
  Preisabstand zur späteren teuren Stunde größer als dieser Wert ist. Verhindert
  unnötige Zyklen für Centbeträge.

Bugfix:
- **Options-Dialog überschrieb bisher die gesamte Konfiguration** beim Speichern
  (`data=user_input`) und löschte dabei Hauptschalter, manuelle Zeitfenster und
  Number-Werte. Jetzt wird korrekt in die bestehenden Optionen gemergt.

## 1.0.8 – Versions-/Release-Korrektur

- manifest-Version an den Release-Tag angeglichen (HACS liest die Version aus
  der manifest.json, nicht aus dem Tag-Namen). Kein Code-Change.

## 1.0.9 – Realistische Ladeleistung

- Neue Einstellung **„Max. Ladeleistung (Netz)"** (kW = kWh pro Stunde). Der
  Planer weiß dadurch, wie viel in einer Stunde tatsächlich in den Akku fließen
  kann, statt anzunehmen, der Akku sei in einer Stunde voll.
- **Mehrstündiges Laden:** Der Planer belegt jetzt so viele günstige Stunden
  zum Netzladen, wie nötig sind, um die teuren Bedarfsstunden zu decken –
  begrenzt durch die Ladeleistung und die Akku-Kapazität. Vorher wurde oft nur
  eine einzige Stunde geplant, die in der Praxis nicht ausreicht.

Beispiel: Bei 0,8 kW Ladeleistung plant der Planer 4 Ladestunden, bei 3 kW
reicht ihm eine – je nach benötigter Energiemenge.

## 1.0.10 – Automatik-Vorschau-Button zurück

- Der Button **„Automatik-Vorschau"** ist wieder da (auf Wunsch). Er zeigt den
  aktuellen Tagesplan des Einsatzplaners als Benachrichtigung – inklusive der
  realistischen, von der Ladeleistung abhängigen Ladefenster. Dieselben Infos
  stehen weiterhin dauerhaft am Fahrplan-Sensor.

## 1.0.11 – Ersparnis zurücksetzen

- Neuer Button **„Ersparnis zurücksetzen"**: setzt alle Spar-Töpfe
  (Solar/Hold/Load/Gesamt) auf 0 und speichert das sofort.

Hinweis: Die Berechnung der Load-Ersparnis ist weiterhin fehlerhaft und lässt
den Wert über die Zeit unrealistisch ansteigen. Der Reset schafft einen sauberen
Startpunkt, behebt aber noch nicht die Ursache.

## 1.0.12 – Netzladen per Switch oder Number

- Die Lade-Steuerung ist jetzt flexibel: Du kannst für das Netzladen wahlweise
  eine **Switch-Entität**, eine **Number-Entität** oder **beides** hinterlegen.
  - `battery_charge_switch` (optional): wird ein-/ausgeschaltet.
  - `battery_charge_number` (optional): wird beim Laden auf den An-Wert gesetzt,
    sonst auf den Aus-Wert (`battery_charge_number_on` / `..._off`, z. B.
    Ladestrom 40 A und 0 A).
- Der Lade-Switch ist damit nicht mehr Pflicht. Bestehende Konfigurationen mit
  Switch laufen unverändert weiter.

## 1.0.13 – Energie-Projektion bis Tagesende

- Der Planer simuliert jetzt den **Akkuverlauf entlang des Plans** und macht die
  Tagesbilanz sichtbar: **Tiefststand** (wann/wie viel), **„voraussichtlich leer
  um HH:MM"** und **Restladung am Tagesende**.
- Neue **Rest-des-Tages-Bilanz**: nutzbarer Akku + Rest-PV heute − Rest-Bedarf
  heute = Überschuss/Defizit in kWh.
- Alles im Fahrplan sichtbar (Text + Attribute `prognose`, `akku_leer_um`,
  `akku_tagesende_kwh`, `rest_bilanz_kwh`). Da der Plan ohnehin jede
  Aktualisierung ab dem aktuellen Ladestand neu gerechnet wird, passt sich die
  Prognose laufend an die Realität an.

## 1.0.19 – Tages-Vorschau & geschätzte Ersparnis

- **Automatische Tages-Vorschau (morgens/abends):** Die Integration postet jetzt
  zu konfigurierbaren Zeiten von selbst die Automatik-Vorschau als
  Benachrichtigung – derselbe Inhalt wie beim manuellen Vorschau-Button (Plan +
  SoC-Prognose), ohne dass man reinschauen muss. Standard: 07:00 und 18:30, in
  den Optionen änderbar; Feld leeren = die jeweilige Benachrichtigung aus.
- **Geschätzte Ersparnis in der Vorschau:** Vorschau und Tages-Benachrichtigung
  zeigen jetzt die geschätzte Netz-Arbitrage-Ersparnis des Plans: günstig
  geladene kWh, in den teuersten Stunden genutzt, abzüglich Ladeverlust
  (~10 % Round-Trip). Ausgewiesen als „~X € (aus Y kWh Netzladung, abzgl. Z kWh
  Ladeverlust)". PV-/Bestandsenergie fließt bewusst nicht ein – nur der
  Kaufen-vs-Nutzen-Teil.

## 1.0.18 – Netzladen per Schalter oder Number konfigurierbar

- **Netzladen per Schalter ODER Number – jetzt auch nachträglich konfigurierbar:**
  Der Config-Flow unterstützte bisher nur den Lade-Schalter; die vom Coordinator
  längst nutzbare Number-Entität (`battery_charge_number` mit AN-/AUS-Wert) war
  nirgends einstellbar. Jetzt sind Schalter und Number-Entität inkl. AN-/AUS-Wert
  sowohl im Setup als auch nachträglich über die Optionen wählbar. Der Schalter
  ist dabei optional – man kann wahlweise den Schalter, die Number oder beides
  hinterlegen.

## 1.0.17 – Reserve-Schutz + korrekte Prognose-Zeiten

- **Reserve-Schutz (Entladesperre am Mindest-SOC):** Ist der nutzbare Akku am
  Boden (SoC auf/unter der eingestellten Nacht-Reserve), sperrt die Automatik
  jetzt von sich aus die Entladung, damit der Wechselrichter den Akku nicht bis
  zu seiner eigenen Abschalt-SOC leerzieht. Laden bleibt erlaubt. Gilt nur bei
  aktiver Automatik – bei ausgeschalteter Automatik („AUS heißt AUS") greift die
  Integration nicht ein.
- **Prognose-Zeiten korrigiert:** Der SoC-Streifen zeigt jetzt den Stand zu
  BEGINN jeder Stunde. Wird z. B. 23:00–00:00 geladen, steht 23:00 noch auf dem
  leeren Akku und erst 00:00 auf der geladenen Energie – vorher war der Wert um
  eine Stunde zu früh eingetragen. Zudem meint „leer ab …" jetzt das Leerlaufen
  entlang des Plans, nicht den (ggf. schon leeren) Startzustand.
- **Stündliches Verbrauchsprofil statt Tagesschnitt:** Fehlte für eine Stunde an
  einem bestimmten Wochentag noch kein gelernter Wert, fiel der Planer bisher auf
  den flachen `default_usage`-Tagesschnitt zurück. Jetzt wird zuerst der Schnitt
  DIESER Stunde über alle Wochentage genutzt – der Verbrauch bleibt also
  stündlich geformt (Nacht-Tief, Morgen-/Abendspitze). Der flache Wert greift nur
  noch, wenn für die Stunde gar nichts gelernt wurde. Betrifft Planer-Horizont,
  Nachtbedarf, Restbedarf und Tagesprofil gleichermaßen.
- **Batterieladung fließt nicht mehr ins KI-Profil:** Die Verbrauchsberechnung
  zieht die Batterieladung (`bat_in`) bereits ab – das funktioniert aber nur,
  wenn der Akku im Energy Dashboard hinterlegt ist. Neu: optionales Feld
  **„Batterie-Ladesensor (Energie)"** in der Konfiguration. Ist es gesetzt, wird
  dieser Zähler zusätzlich abgezogen, sodass Netz-/PV-Ladung garantiert nicht als
  Hausverbrauch gelernt wird – auch ohne vollständiges Energy-Dashboard-Setup.
  Leer = unverändertes Verhalten.

## 1.0.16 – Grenzwert inklusive + Akku-Prognose in der Vorschau

- **Lade-Grenzwert jetzt inklusive (≥ statt >):** „10 ct Preisvorteil" lädt jetzt
  auch für eine genau 10 ct teurere Stunde, nicht erst ab 10,1 ct. Vorher blieben
  teure Stunden ungenutzt aus dem Netz versorgt, obwohl günstiges Vorladen den
  Round-Trip genau gedeckt hätte (z. B. Nacht 28 ct → Morgen 38 ct wurde
  übersprungen). Spart real Geld bei glatten Preisabständen.
- **Akku-Prognose in der Automatik-Vorschau:** Die Vorschau zeigt jetzt den
  SoC-Verlauf entlang des Plans – Tiefpunkt (+ Uhrzeit), ob/wann der Akku leer
  wird, Tagesende-Stand und einen kompakten Stundenstreifen (nächste ~14 h,
  Uhrzeit = kWh). Damit ist sichtbar, WANN und WIEVIEL der Akku genutzt wird –
  man kann die Plausibilität des Plans direkt selbst prüfen.

## 1.0.15 – Echtes „AUS" + Hauptschalter sichtbar

- **„AUS heißt AUS" jetzt wörtlich (Totstellen):** Ist die Automatik aus UND
  kein manuelles Zeitfenster aktiv, schreibt die Integration nicht mehr jede
  Minute auf die Hardware. Beim Umschalten auf AUS wird **einmalig** sauber
  freigegeben (Wechselrichter entsperrt, Laden aus, Smart-Switches aus) – damit
  nichts gesperrt oder ladend hängenbleibt – danach **kein Schreibzugriff
  mehr**. Der Wechselrichter lässt sich im AUS-Zustand völlig frei von Hand
  bedienen; die Integration schreibt nicht mehr dagegen. Vorher wurde das
  WR-Limit im AUS-Zustand minütlich auf den Freigabe-Wert zurückgesetzt – das
  fühlte sich wie „Eigenleben" an.
- **Hauptschalter „Ladeautomatik aktiv" ist jetzt ein Bedienelement** (vorher in
  der Kategorie Konfiguration/Diagnose versteckt). Er erscheint damit direkt bei
  den Schaltern (Steuerung), wo der Master hingehört.

## 1.0.14 – Weiter vorausschauen (36h-Horizont)

- **Planungshorizont von 24 h auf 36 h** erweitert. Vorher fiel – je nach
  Uhrzeit – die teure Abendspitze des Folgetags aus dem Fenster; der Planer sah
  kein lohnendes Ladeziel und plante fälschlich „nichts laden". Jetzt wird die
  morgige Abendspitze erkannt und, wenn die PV sie nicht deckt, günstiges
  Nachladen (z. B. im Mittagstal) dafür eingeplant.
- **Kein Sparen eines leeren Akkus mehr:** HOLD wird nur gesetzt, wenn an der
  Stelle überhaupt Akku-Energie vorhanden ist. Behebt das sinnlose
  „Akku sparen" bei fast leerem Speicher.
- **Ladeplanung repariert (Kern-Bug):** Die Lade-Suche brach bisher ab, sobald
  die EINE teuerste ungedeckte Stunde nicht nachladbar war (z. B. weil direkt
  davor keine Stunde den nötigen Preisvorsprung hatte). Damit ging die komplette
  Nacht-Arbitrage verloren – bei realistischem „Lade-Preisvorteil" (≈10 ct)
  wurde praktisch nie geladen, obwohl der Akku fast leer war. Jetzt werden ALLE
  offenen Bedarfsstunden nach Preis absteigend als Ladeziel geprüft; günstiges
  Nachladen in der Nacht für die teuren Stunden am Folgetag wird korrekt
  eingeplant.
