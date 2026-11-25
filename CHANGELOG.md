# Changelog

Format: eine Version je Abschnitt, Einträge gruppiert in Neu, Geändert und
Behoben, ein Eintrag je Zeile. Die App zeigt diese Datei unter „Changelog“
und nach einem Update als Hinweisfenster.

## 3.5.4 (2026-11-25)

### Behoben
- Viessmann: Anlagensuche nutzt /iot/v2/equipment/installations, weil Viessmann /iot/v1 abgeschaltet hat (HTTP 410 GONE)

## 3.5.3 (2026-11-25)

### Behoben
- Viessmann: Anmeldung und API auf die neuen Adressen iam./api.viessmann-climatesolutions.com umgestellt, weil es die alten viessmann.com-Adressen nicht mehr gibt
- „Mit Viessmann verbinden“ zeigt nicht mehr „Seite nicht erreichbar“

## 3.5.2 (2026-11-25)

### Neu
- Geräteauswahl per Liste: Nach „Verbindung testen“ zeigt das Formular alle Geräte des Kontos (z. B. alle Daikin-Klimageräte mit Raumnamen) zum Antippen statt einer ID zum Abtippen
- Mehrere Geräte auf einmal: Jedes angetippte Gerät wird beim Speichern ein eigener Eintrag mit Raumnamen und einzeln gesteuert; schon angelegte Geräte sind ausgegraut

## 3.5.1 (2026-11-25)

### Geändert
- Daikin Onecta zeigt Geräte mit dem Raumnamen aus der Onecta-App, z. B. „Daikin Wohnzimmer (dx4)“
- Neuer Live-Wert „Geräte im Konto“ mit Anzahl und Namen aller Daikin-Geräte; „Verbindung testen“ nennt Anzahl, Namen und IDs

## 3.5.0 (2026-11-25)

### Neu
- Treiber Daikin Onecta (Cloud): Klimaanlagen mit neuerem WLAN-Adapter (BRP069C) und Daikin Altherma (Raumheizung oder Warmwasserspeicher): bei Überschuss einschalten, Sollwert verstellen oder Powerful/Warmwasser-Boost
- Knopf „Mit Viessmann verbinden“ und „Mit Daikin verbinden“ im Geräteformular: Anmeldung beim Hersteller im Browser, MinePower trägt das Token selbst ein, kein Skript mehr nötig
- Schritt-für-Schritt-Anleitung im Geräteformular für alle Geräte mit Token, Passwort oder API-Schlüssel (Home Assistant, Homey, MQTT, Tasmota, Easee, Zaptec, Wattpilot, SMA, OCPP, Tesla, Viessmann, Daikin …), mit Links zur passenden Seite
- Homey Pro: Geräte-ID leer lassen und „Verbindung testen“, MinePower listet dann alle Homey-Geräte mit ID

### Geändert
- Daikin Onecta erlaubt nur 200 Anfragen am Tag: mehrere Geräte am selben Konto teilen sich Anmeldung und Abfrage, Befehle nur bei Änderung, Pause bei erschöpftem Kontingent
- Rotierende Refresh-Tokens (Daikin) werden verschlüsselt im Datenverzeichnis gemerkt und überstehen Neustarts

## 3.4.0 (2026-11-25)

### Neu
- Wärmepumpen und Klimaanlagen: Überschuss geht auch in Wärme; MinePower gibt das Gerät frei, die Temperatur regelt es selbst
- Treiber Viessmann-Wärmepumpe (ViCare-API): Warmwasser-Einmalladung oder höhere Warmwasser-Solltemperatur bei Überschuss
- Treiber Homey Pro: beliebiges Homey-Gerät bei Überschuss einschalten oder Solltemperatur verschieben
- Treiber Daikin-Klimaanlage (lokaler WLAN-Adapter, Airbase): bei Überschuss heizen oder kühlen
- Verdichterschutz: Mindestlaufzeit und Mindestpause je Gerät (Vorgabe 10 / 5 min für Wärmepumpe und Klima)
- Hilfsskript backend/tools/vicare_token.py für das Viessmann-Refresh-Token
- Simulation: Wärmepumpe mit SG-Ready-Freigabe und Pufferspeicher, im Demo-Modus zwischen Auto und Heizstab
- Energiefluss: jedes Auto und jedes Wärmegerät mit eigenem Bild und eigener Leitung (bis vier, darüber „+n weitere“); neue Bilder für Wärmepumpe und Klimaanlage
- Übersicht: Verlauf mit eigener Linie je Auto und Wärmegerät
- Wand-Ansicht: ein Balken je Auto und Wärmegerät

### Geändert
- Seite Warmwasser heißt Wärme und zeigt alle Wärmegeräte gleichzeitig: Kacheln oben, darunter die Details jedes Geräts, sortiert nach Reihenfolge
- Seite Auto zeigt alle Autos gleichzeitig statt Umschalter
- Mehrere Wärmegeräte laufen gleichzeitig nach der Reihenfolge (Platz 1 zuerst, der Rest an die nächsten)
- „Warum?“ nennt jedes Gerät einzeln, bei mehreren mit Namen
- SG-Ready-Wärmepumpen gelten als selbstregelnd mit Verdichterschutz
- Ein-/Aus-Geräte (Relais, SG-Ready) werden nie mehr als modulierend geregelt, auch wenn das gespeichert war

### Behoben
- Wärmepumpe im eigenen Normalbetrieb wurde als „folgt der Regelung nicht“ gemeldet
- Statistik: Kennzahlen, Ladevorgänge und Diagrammachsen auf Deutsch mit Dezimalkomma und Tausenderpunkt (vorher z. B. "7,000" und "38.4 kWh")
- Diagramme: Monats- und Wochentagsnamen auf Deutsch ("Okt" statt "Oct"), Tagesachse als "09.10."
- Wand-Ansicht: Strompreis mit Dezimalkomma
- Simulation: Warmwasser heizte etwa 1000-mal zu schnell (nach Boost in Sekunden über 75 °C bis 100 °C); jetzt rund 14 K pro Stunde bei 3 kW
- Simulation: Standverlust des Warmwasserspeichers 3,6 K pro Tag statt pro Stunde

## 3.3.1 (2026-11-18)

### Geändert
- Energiefluss neu gezeichnet: isometrische Bilder auf Kacheln (Sonne, Netz, Speicher, Haus, Auto, Warmwasser) statt Symbolkästen
- Energiefluss: Leitungen werden mit der Leistung dicker und schneller, Pfeile zeigen die Richtung, inaktive Leitungen sind nur gepunktet
- Energiefluss: Füllstand von Speicher und Auto sowie Wassertemperatur als Plakette; Speicher-Füllstand und Thermometer auch im Bild

## 3.3.0 (2026-11-18)

### Neu
- Einstellungen: Tab Sicherung mit täglicher Datensicherung und Wiederherstellen
- Diagnose: Antwortzeit und Fehlerzähler je Gerät, Verbindungstest, Protokoll der Anwendung mit Filter
- Diagnose-Export als ZIP, anonymisiert (keine Zugangsdaten, Adressen und Namen ersetzt)
- Plausibilitätsprüfung: Energiebilanz, Einspeisung ohne Erzeugung, PV nachts
- Verlauf als CSV; Strompreis, Einspeisevergütung und CO2 einstellbar
- Benachrichtigungen: Testnachricht je Kanal, Webhook als Kanal
- Eigenes Passwort ändern (Einstellungen, Konto)
- Webapp startet schneller und zeigt bei Serverausfall die Oberfläche statt einer Fehlerseite (bei HTTPS)
- TRUSTED_PROXIES: Login-Sperre sieht hinter einem Reverse-Proxy die echte Adresse

### Geändert
- Beim Beenden kommen erst Speicher, Wallbox und Heizstab in einen sicheren Zustand, dann werden die Messwerte geschrieben
- Wallbox (außer bei Sofort laden) und Heizstab werden beim Beenden ausgeschaltet, wenn MinePower sie eingeschaltet hat
- Messwerte gehen bei einem Datenbankausfall in eine Spool-Datei und werden danach ohne Lücke nachgeschrieben
- Datenbankschema wird beim Start automatisch angehoben, vorher Rohsicherung der Konfiguration
- Anwendung läuft im Container ohne root, Dateisystem nur lesbar
- CORS und API-Dokumentation nur noch auf Wunsch (CORS_ORIGINS, API_DOCS)
- Gespeicherte Gerätepasswörter gehen nie an eine geänderte Adresse; nach Änderung der Adresse Passwort neu eingeben
- Nach einer Wiederherstellung muss sich jeder neu anmelden
- Bibliotheken aktualisiert (keine bekannten Sicherheitslücken mehr), Diagramme sehen aus wie bisher

### Behoben
- Anmelde-Token stand in der Adresse der Live-Verbindung und damit im Zugriffsprotokoll
- Tokens von vor einem Passwortwechsel blieben gültig; offene Live-Verbindungen enden jetzt bei Passwortwechsel, Sperre und Löschen
- Nach einem Stromausfall konnte eine leere Schlüsseldatei den Start dauerhaft verhindern
- Hängende Datenbank hielt den Regeltakt an, die Wallbox lud so lange mit dem letzten Strom weiter
- Hängender Zähler: Regelung lief bis zu 18 s mit einem alten Messwert, jetzt Sicherheits-Stopp nach 12 s
- Hängende Geräte bremsten den Regeltakt um je 5 Sekunden
- Großer Rückstand an Messwerten wurde nach einem Abbruch nie fertig nachgeschrieben
- Weiterleitungen und DNS-Namen konnten den Schutz vor Metadatendiensten umgehen
- Zugangsdaten in Fehlermeldungen mit Traceback standen im Klartext im Protokoll
- Ladevorgang überlebt Neustart und Geräteänderung
- Jahresauswertung zählt eingespielte Stundenwerte, Bezug und Einspeisung stimmen
- Treiber nach Herstellerdoku korrigiert: Sungrow, my-PV, go-e, Fronius, Huawei, SMA, Tesla BLE, OCPP
- Hardy Barth Salia zeigte die 10-fache Ladeleistung
- Wallboxen mit kurzem Watchdog-Timeout (KEBA, Webasto, Vestel, Heidelberg, Amperfied, EM2GO, Pulsares, OBO) fielen auf ihren Sicherheitsstrom zurück; das Lebenszeichen richtet sich jetzt nach dem Timeout im Gerät
- Mennekes Amtron 4You/4Business: Verbindung brach in jedem Takt ab (Ladestand-Register)
- Bender: ungültige Zählerwerte nach einem Neustart der Box wurden als Leistung angezeigt
- KEBA P40: Freigabe wurde falsch erkannt; P40 mit Produktcode 0 lässt sich als P40 einstellen
- Alfen meldete ohne Fahrzeug einen Fehler
- Mennekes Amtron Compact bleibt nicht mehr im Fallback-Zustand hängen
- ABL eMH1: Freischalten nach dem Aufwachen ging manchmal verloren

## 3.2.0 (2026-10-21)

### Neu
- Tarif: Automatische Grenze. Die Preisgrenze liegt immer auf dem tiefsten Preis der nächsten 24 Stunden, Auto, Warmwasser und Netzladen laden genau dann
- Automatische Grenze einstellbar: Mindestdauer des günstigen Fensters (Standard 2 Stunden) und Toleranz
- Tarif-Seite zeigt die wirksame Grenze, den Tiefstpreis und wie lange es günstig ist
- iOS-App für iPhone und iPad (Quellcode im Ordner ios)

### Geändert
- Fehlen Preise für die nächsten 6 Stunden, gilt weiter die feste Grenze

### Behoben
- Tarif-Einstellungen (Anbieter, Grenze, Automatik) wirkten erst nach bis zu 30 Minuten; jetzt sofort nach dem Speichern
- KI-Zugangsschlüssel (MCP) gingen verloren, wenn die Datenbank aus der automatischen Sicherung wiederhergestellt wurde; die Sicherung enthält jetzt ihre Prüfsummen (nie den Schlüssel selbst)

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
