🧠 Intelligent ESS für Home Assistant
Intelligent ESS (Energy Storage System) ist eine smarte, vorausschauende Home Assistant Integration, die das Management von Heimspeichern (Batterien) revolutioniert. Anstatt den Akku einfach "dumm" zu laden, wenn die Sonne scheint, und zu entladen, sobald im Haus Strom gebraucht wird, trifft Intelligent ESS strategische Entscheidungen basierend auf dynamischen Strompreisen, dem individuellen Verbrauchsverhalten und vorausschauenden Prognosen.

Das Ziel: Maximale Autarkie, optimale Ausnutzung von Preisschwankungen und absolute finanzielle Transparenz.

🌟 Hauptmerkmale (Core Features)
1. Selbstlernendes Verbrauchsprofil (Smart Learning)
Das System verlässt sich nicht auf statische Schätzwerte. Es lernt im 15-Minuten-Takt das reale Verbrauchsverhalten des Haushalts (wochentag- und stundengenau).

Der Forecast: Kontinuierliche Berechnung des exakten Restbedarfs bis zum nächsten Morgen (ca. 08:00 Uhr).

Nachtreserve: Automatische Einplanung eines Sicherheitspuffers (z. B. 20 %), damit die Grundlast (z. B. Kaffeemaschine am Morgen) gesichert ist.

2. Smart Charging (Arbitrage-Laden)
Wenn absehbar ist, dass die PV-Energie nicht bis zum nächsten Morgen reicht (z. B. im Winter), wird der Akku aktiv aus dem Netz geladen – aber nur dann, wenn der Strompreis am günstigsten ist. Das System sucht automatisch die Preis-Tiefpunkte der Nacht.

3. Smart Hold (Peak Shaving & Batterieschonung)
Wenn der aktuelle Netzstrom extrem günstig ist (z. B. bei viel Windkraft im Netz), stoppt das System die Batterieentladung (HOLD). Das Haus wird kurzzeitig günstig aus dem Netz versorgt, während die wertvolle Batteriekapazität für die teuren Peak-Stunden am Morgen oder Abend reserviert bleibt.

4. Transparente Finanz-Analyse (Die 4 Spar-Töpfe)
Echtzeit-Aufschlüsselung des wirtschaftlichen Erfolgs durch vier separate Sensoren:

Solar-Ersparnis: Geld gespart durch direkten PV-Eigenverbrauch.

Hold-Ersparnis: Gewinn durch Nutzung günstigen Netzstroms bei gleichzeitiger Reservierung der Batterie für teure Stunden.

Load-Ersparnis: Arbitrage-Gewinn durch gezieltes Beladen des Akkus zu Tiefstpreisen.

Gesamt-Ersparnis: Die Summe aller smarten Entscheidungen.

📋 Voraussetzungen (Prerequisites)
Damit Intelligent ESS seine volle Wirkung entfalten kann, müssen folgende Voraussetzungen in Home Assistant erfüllt sein:

Dynamische Strompreise: Du benötigst eine Integration, die aktuelle Strompreise liefert (z. B. Tibber, Awattar oder ähnliche).

Preis-Prognose: Die Preis-Integration muss eine Liste der Forecast-Preise (mindestens für die nächsten 12-24 Stunden) bereitstellen.

Schreibzugriff auf den Wechselrichter: Dein Wechselrichter/Speicher-System muss über Home Assistant steuerbar sein (z. B. über Modbus, MQTT oder herstellerspezifische Integrationen), um Befehle zum Laden, Entladen oder Halten (Hold) empfangen zu können.

🔌 Kompatibilität
Intelligent ESS ist herstellerunabhängig konzipiert. Es funktioniert "Out of the box" mit jedem Speicher-System, das als Entität in Home Assistant eingebunden ist, sofern:

Die aktuelle Batterieladung (SOC in % oder kWh) ausgelesen werden kann.

Die Lade- und Entladelogik über Home Assistant Schalter oder Nummern-Entitäten (Registers) gesteuert werden kann.

🚀 Installation
Über HACS (Empfohlen)
Öffne HACS in deinem Home Assistant.

Klicke auf die drei Punkte oben rechts und wähle Benutzerdefinierte Repositories.

Füge die URL dieses Git-Repositories hinzu und wähle als Kategorie Integration.

Suche nach Intelligent ESS und klicke auf Installieren.

Starte Home Assistant neu.

Manuelle Installation
Lade dieses Repository herunter.

Kopiere den Ordner custom_components/intelligent_ess in deinen custom_components-Ordner in Home Assistant.

Starte Home Assistant neu.

📊 Dashboard & Sensoren
Nach der Installation stehen dir unter anderem folgende Sensoren zur Verfügung:

Aktion: Aktueller Status (z. B. LADEN, HOLD, NORMAL).

Fahrplan: Vergleich von berechnetem Bedarf vs. Strategie.

Restbedarf (kWh): Exakte Prognose der benötigten Energie bis zum nächsten Sonnenaufgang.

Finanz-Sensoren (€): Überwachung der Rentabilität in Echtzeit.

(Screenshots folgen in Kürze)

🎯 Für wen ist dieses Projekt?
Für alle Besitzer einer Photovoltaikanlage mit Heimspeicher, die einen dynamischen Stromtarif nutzen und die Kontrolle über ihr Energiemanagement nicht einer undurchsichtigen Cloud überlassen wollen, sondern lokal, datenschutzkonform und maximal effizient im eigenen Home Assistant regeln möchten.
