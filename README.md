🧠 Intelligent ESS für Home Assistant
Intelligent ESS (Energy Storage System) ist eine smarte, vorausschauende Home Assistant Integration, die das Management von Heimspeichern (Batterien) revolutioniert. Anstatt den Akku einfach "dumm" zu laden, wenn die Sonne scheint, und zu entladen, sobald im Haus Strom gebraucht wird, trifft Intelligent ESS strategische Entscheidungen basierend auf dynamischen Strompreisen (z. B. Tibber), dem individuellen Verbrauchsverhalten und vorausschauenden Prognosen.

Das Ziel: Maximale Autarkie, optimale Ausnutzung von Preisschwankungen und absolute finanzielle Transparenz.

🌟 Was leistet das Projekt? (Core Features)
1. Selbstlernendes Verbrauchsprofil (Smart Learning)
Das System verlässt sich nicht auf statische Schätzwerte. Es lernt im 15-Minuten-Takt das reale Verbrauchsverhalten des Haushalts (Wochentag- und stundengenau).

Der Forecast: Das System berechnet kontinuierlich den exakten Restbedarf bis zum nächsten Morgen (08:00 Uhr), wenn die PV-Anlage voraussichtlich wieder genug Energie liefert.

Nachtreserve: Es wird automatisch ein Sicherheitspuffer (z. B. 20 %) eingeplant, damit der Akku morgens für die Kaffeemaschine nicht leer ist.

2. Smart Charging (Arbitrage-Laden)
Wenn in den Wintermonaten oder bei schlechtem Wetter absehbar ist, dass die Batteriekapazität nicht bis zum nächsten Morgen reicht, wird der Akku aktiv aus dem Netz geladen – aber nur dann, wenn der Strompreis am günstigsten ist.

Das System sucht sich die absoluten Preis-Tiefpunkte in der Nacht, um den fehlenden Restbedarf aufzufüllen.

3. Smart Hold (Peak Shaving & Batterie-Schonung)
Strom ist oft mittags und nachts günstig, aber morgens und in den frühen Abendstunden teuer (Peak-Zeiten).

Wenn der aktuelle Netzstrom extrem günstig ist (z. B. durch viel Windstrom im Netz), stoppt das System die Batterieentladung (Action: HOLD). Das Haus wird kurzzeitig günstig aus dem Netz versorgt, während die wertvolle Batteriekapazität für die teuren Peak-Stunden am Abend oder Morgen "aufgehoben" wird.

4. Transparente Finanz-Analyse (Die 4 Spar-Töpfe)
Im Gegensatz zu Standard-Systemen, die nur eine pauschale "Ersparnis" anzeigen, schlüsselt Intelligent ESS den wirtschaftlichen Erfolg in Echtzeit in vier separate Sensoren auf:

Solar-Ersparnis: Geld, das durch direkten PV-Eigenverbrauch gespart wurde.

Hold-Ersparnis: Geld, das gespart wurde, weil günstiger Netzstrom genutzt und die Batterie für teure Stunden zurückgehalten wurde.

Load-Ersparnis: Gewinn (Arbitrage) durch das gezielte Beladen des Akkus zu Tiefstpreisen.

Gesamt-Ersparnis: Die Summe aus allen smarten Entscheidungen und dem PV-Ertrag.

⚙️ Wie funktioniert es unter der Haube?
Die Integration arbeitet minütlich als "Schaltzentrale" (Coordinator) in Home Assistant:

Daten-Sammeln: Es liest PV-Erzeugung, Netzbezug, Netzeinspeisung sowie Batterieladung/-entladung und berechnet daraus den echten Hausverbrauch.

Prognose erstellen: Die Logic-Engine gleicht den aktuellen Batteriestand mit dem gelernten Restbedarf der kommenden Nacht ab.

Preise auswerten: Die aktuellen und zukünftigen Tibber-Preise werden gescannt.

Entscheidung treffen (Der Fahrplan): Die Integration gibt eine Handlungsanweisung (LADEN, HOLD oder NORMAL) an den Wechselrichter aus, der den Speicher entsprechend steuert.

📊 Verfügbare Sensoren im Home Assistant Dashboard
Mit der Installation erhältst du ein "Intelligent ESS" Gerät in Home Assistant mit folgenden Echtzeit-Werten:

Aktion: Was tut das System gerade und warum? (z.B. "HOLD - Strom ist aktuell günstig, bewahre Akku für Morgen-Peak auf").

Fahrplan: Zeigt den berechneten Bedarf vs. die aktuelle Strategie.

Hausverbrauch (kW): Bereinigter Echtzeit-Verbrauch des Hauses.

Restbedarf (kWh): Exakt berechnete Energie, die bis morgen früh noch benötigt wird.

Forecast Verbrauch (kWh): Erwarteter Verbrauch der nächsten Stunde (inkl. stündlicher Prognose bis zum Morgen als Attribut).

Finanz-Sensoren (€): Alle vier Ersparnis-Töpfe zur Überwachung der Rentabilität.

🎯 Für wen ist dieses Projekt?
Für alle Besitzer einer Photovoltaikanlage mit Heimspeicher, die einen dynamischen Stromtarif (wie Tibber) nutzen und die Kontrolle über ihr Energiemanagement nicht einer undurchsichtigen Cloud überlassen wollen, sondern lokal, datenschutzkonform und maximal effizient im eigenen Home Assistant regeln möchten.
