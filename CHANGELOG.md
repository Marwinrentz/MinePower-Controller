# Changelog

Format: eine Version je Abschnitt, Einträge gruppiert in Neu, Geändert und
Behoben, ein Eintrag je Zeile. Die App zeigt diese Datei unter „Changelog“
und nach einem Update als Hinweisfenster.

## 3.1.1 (2026-10-15)

### Behoben
- Strompreis fehlte nach dem Update: Schlüssel für Zugangsdaten ging ohne Volume für /app/data verloren
- Schlüssel liegt jetzt zusätzlich im Sicherungsordner und überlebt Updates
- Nicht mehr lesbare Zugangsdaten erscheinen leer statt als gesetzt, mit Hinweis im Ereignisprotokoll
- Batterie: Werte in der Übersicht überlappten bei schmaler Breite

## 3.1.0 (2026-10-15)

### Neu
- KI-Assistenten über MCP (Model Context Protocol): Werte lesen, Geräte steuern und einrichten
- MCP-Funktionen: Ladeliste als CSV mit Summen, Zeitfenster, Betriebsarten, Batterie, Einstellungen
- Einstellungen: Tab KI-Assistent mit Zugangsschlüsseln je Anwendung und Rechtestufe
- Anleitungen für Claude, ChatGPT, Grok, Le Chat, Codex, Cursor, VS Code, Windsurf, Gemini CLI, LM Studio, Open WebUI, Continue
- Adresse mit Schlüssel für Konnektoren, die nur eine URL annehmen
- Batterie: Restzeit bis voll bzw. bis zur Reserve

### Geändert
- Batterie: waagerechte Füllstandsanzeige mit Flussrichtung
- Fahrzeug wird schon ab drei Vierteln der Mindestleistung geweckt

### Behoben
- Verschenkter Strom wurde der Batterie zugeschrieben statt dem eigentlichen Grund (z. B. Fahrzeug nicht erreichbar)
- Diagnose meldete bei Geräteprogrammen 'Batterie wird nicht entladen', auch wenn sie mitlief
- Regelung pausieren war auch mit der Rolle 'Nur lesen' möglich
- Unbekannte Betriebsarten wurden ohne Fehlermeldung gespeichert

## 3.0.0 (2026-10-09)

### Neu
- Treiber: HTTP/JSON für Netzzähler, Wechselrichter, Batterie und Schaltaktor
- Treiber: MQTT für Netzzähler, Wechselrichter, Batterie und Schaltaktor
- Treiber: Home Assistant für Netzzähler, Wechselrichter, Batterie, Schaltaktor und Wallbox
- Treiber: Tasmota für SML-Lesekopf und Schaltaktor
- Treiber: Modbus TCP mit Registerprofil für Wechselrichter und Batterie
- Treiber: Victron GX (Modbus TCP)
- Treiber: KEBA KeContact P30 (UDP)
- Treiber: Wärmepumpe über SG-Ready (Shelly, Home Assistant)
- Treiber: Wallbox generisch über HTTP und MQTT
- Treiber: cFos Power Brain, Alfen Eve, Amperfied, Heidelberg Energy Control
- Treiber: Webasto Next, Ampure Unite, Vestel EVC04
- Treiber: Bender CC612/CC613 (Mennekes Amtron, Walther, Ubitricity)
- Treiber: Mennekes Amtron Compact 2.0s
- Treiber: Phoenix Contact CHARX und EV-ETH (Wallbe, ESL Walli)
- Treiber: KEBA P30 x und P40 über Modbus TCP
- Treiber: ABL eMH1, EM2GO, innogy/E.ON eBox, KSE, OBO Bettermann, Peblar, Pulsares
- Treiber: Siemens VersiCharge, SolaX EVC
- Treiber: Hardy Barth eCB1 und Salia, SMA EV Charger, EVSE-WiFi
- Treiber: openWB Pro (HTTP) und openWB 1.9 'Nur Ladepunkt' (MQTT)
- Treiber: Fronius Wattpilot Home/Go
- Treiber: Easee und Zaptec über die offizielle Cloud-API
- Modbus: RS485-Geräte über TCP-Gateway (RTU und ASCII)
- Changelog in der App
- Hinweisfenster nach Updates mit den Änderungen seit dem letzten Besuch
- Einstellung: Phasen des Hausanschlusses
- Einstellung: Batteriekapazität, falls das Gerät sie nicht meldet
- Einrichtung: Hinweis, wenn keine Netzmessung eingerichtet ist
- Seitenleiste: Link zum Projekt auf GitHub
- Changelog: Hinweis zum Melden von Fehlern mit Diagnosebericht
- Gerätekonfiguration: Feldprüfung mit Feldname und Ursache

### Geändert
- Lizenz: MIT
- Zugangsdaten werden verschlüsselt gespeichert
- Zugangsdaten werden in der Oberfläche nur noch maskiert angezeigt
- Export: ohne Zugangsdaten
- Netzzähler: höchstens 1 aktives Gerät
- Batterie: höchstens 1 aktives Gerät; zusätzliche Geräte werden beim Update deaktiviert
- Mehrere Hybrid-Wechselrichter: Batteriewerte werden summiert
- Übersicht: eine Karte je Wallbox und je Warmwassergerät
- Treiberauswahl: Hersteller alphabetisch, generische Treiber am Ende
- Prioritätskette in der Einrichtung: nur Lasten
- Diagnose: Hinweise aus den Treiberdaten statt Herstellernamen im Kern
- Versionsnummer aus einer Datei für Backend, Oberfläche und Image
- Image ohne Tests und Entwicklungswerkzeuge
- Texte der Oberfläche gekürzt
- Beispieladressen auf Dokumentationsbereich 192.0.2.x umgestellt
- Browser-Speicher: Schlüssel umbenannt, Anmeldung bleibt erhalten

### Behoben
- Einrichtung: Prioritäts-Schritt schrieb die Batterie in die Kette
- Unbekannte Batteriekapazität wurde als 10 kWh angenommen
- Netzanschluss wurde immer als dreiphasig gerechnet
- Gerätekonfiguration wurde beim Anlegen nicht geprüft
- Zugangsdaten von Geräten waren über die API im Klartext lesbar
