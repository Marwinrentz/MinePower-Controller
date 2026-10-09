# Audit: kommerzieller Release

Stand: Version 2.17.0, Commit f5e0585. Prüfumfang: Backend, Frontend,
Docker, Docs, Tests und die gesamte Git-History (29 Commits).

Die Spalte „Maßnahme“ beschreibt, was in Version 3.0.0 umgesetzt ist.
Offene Entscheidungen stehen am Ende.

## 1. Neutralität und persönliche Daten

| Fund | Ort | Maßnahme |
|---|---|---|
| Heimnetz-IPs der Entwickleranlage (privater Bereich 192.168.x.x) | Simulation, Tests, Test-Fixture, e2e-Fixtures, `docs/analyse-2.16.md` | ersetzt durch Dokumentationsadressen `192.0.2.x` (RFC 5737) |
| Fahrzeug-VIN der Entwickleranlage | `backend/tests/test_diagnostics_settings.py` | ersetzt durch `XP7TEST0000000001` |
| Fahrzeugname und Diagnose-Verlauf der Entwickleranlage | `backend/tests/fixtures/diagnose_7d.json` | Namen und Adressen anonymisiert. Der Leistungsverlauf bleibt als Testdatensatz für die Wiedergabe (`tools/replay.py`). |
| Diagnose-Analyse der Entwickleranlage | `docs/analyse-2.16.md`, `docs/screenshots/2.15`, `docs/screenshots/2.16` | entfernt |
| Private Beispieladressen in Treiber-Platzhaltern und Docs | Treiber, `docs/headless.md`, `docs/adding-drivers.md` | ersetzt durch `192.0.2.x` |
| Altname „SolarCharge“ | `.env.example` (`SOLARCHARGE_IMAGE`), Browser-Speicher (`sc_token`, `sc_user`, `sc_theme`, `sc_lang`) | `MINEPOWER_IMAGE`; Speicherschlüssel `mp_*`, alte Schlüssel werden einmalig übernommen |
| „Sungrow AC SolarCharger“ | `drivers/sungrow/wallbox.py` | Produktname von Sungrow, bleibt |
| Hersteller- und Urheberangaben (Copyright, Docker-Hub-Konto, GitHub-URL) | `README.md`, `LICENSE`, `NOTICE` | gewollt (Anbieterangaben), bleiben |
| Standardwerte | `startup.py`, Treiber | neutral, keine Werte aus der Entwickleranlage |
| feste Annahmen | `battery_policy.py` (10 kWh, wenn die Kapazität unbekannt ist), Netzanschluss fest dreiphasig | Kapazität und Phasenzahl des Hausanschlusses einstellbar; ohne Kapazität kein Prognose-Abgleich |
| Marken-Sonderfälle außerhalb der Treiber | `services/diagnostics.py` (Tesla-Proxy, my-PV), Oberfläche „BLE-Proxy“ | Hinweise wandern in die Treiber-Metadaten (`offline_hint`), Oberfläche neutral („Gateway“) |

### Zugangsdaten

| Fund | Maßnahme |
|---|---|
| Passwort- und Token-Felder der Geräte (z. B. Fleet-API-Token) im Klartext in der DB und in `GET /api/devices` | verschlüsselt gespeichert (Fernet, Schlüssel aus `MINEPOWER_SECRET_KEY` oder Schlüsseldatei im Datenvolume); API liefert `•••` |
| Tarif- und Benachrichtigungs-Tokens im Klartext in der DB, für Admins im Klartext in `GET /api/settings` | verschlüsselt gespeichert; API liefert für alle Rollen `•••`; `•••` beim Speichern behält den alten Wert |
| manueller Export (`/api/system/export`) | ohne Zugangsdaten |
| Logs | keine Ausgabe von Konfigurationen gefunden; Ereignisse enthalten nur Gerätenamen |

### Telemetrie, Image, Lizenzen

- **Telemetrie / Phone-home:** keine. Ausgehende Verbindungen gibt es nur
  zu Diensten, die der Betreiber selbst einrichtet: forecast.solar,
  Tibber, aWATTar, Telegram, ntfy, Tesla Fleet API.
- **Entwicklungsdateien im Image:** `backend/tests`, `backend/tools`,
  `backend/scripts` und `pytest.ini` landeten im Image. Sie sind jetzt per
  `.dockerignore` ausgeschlossen, ebenso `ios/`, `website/`, `examples/`
  und `frontend/e2e/`.
- **Lizenz von MinePower:** MIT (Entscheidung zu 3.0.0, vorher eigene
  Lizenz mit Weitergabeverbot).
- **Lizenzen der Abhängigkeiten:** alle freizügig (MIT, BSD, Apache-2.0,
  PSF, Unlicense), kein GPL/AGPL im ausgelieferten Code. Neu ist
  `cryptography` (Apache-2.0 / BSD). Prüfbedarf besteht nur bei
  Diensten und Laufzeit-Images, siehe Abschnitt „Offen“.
- **Secrets in der Git-History:** keine Tokens, Schlüssel oder Passwörter
  gefunden. Geprüft wurden alle hinzugefügten Zeilen aller Commits auf
  JWT, Bot-Token, API-Key-Muster, private Schlüssel und Passwort-Zuweisungen.
  Persönliche Daten stehen aber weiter in der History: VIN, Heimnetz-IPs,
  Fahrzeugname, Energieverlauf (Commits bdd8310, 11b522c, 6d930f9).

## 2. Geräteklassen und Treiber

| Klasse | Treiber vor diesem Release | Bewertung |
|---|---|---|
| PV-Wechselrichter | Sungrow SH (stabil), Sungrow SG, Fronius (Solar API), SMA, Huawei SUN2000, SunSpec | SunSpec deckt auch SolarEdge, Kostal und viele andere ab |
| Batterie (aktiv steuerbar) | Sungrow (Beta) | Hybrid-WR liefern die Batterie nur lesend (Regel beibehalten) |
| Netz-/Smartmeter | Sungrow DTSU, Fronius, SMA, Huawei, SunSpec, Shelly 3EM, Modbus generisch (1 Register) | gut |
| Wallbox | go-e, OCPP 1.6, Sungrow AC | OCPP deckt Alfen, ABL, Mennekes und viele andere ab |
| Fahrzeug | Tesla (BLE-Proxy / Fleet API) | modelliert als Ladepunkt (Klasse Wallbox) |
| Warmwasser | my-PV AC·THOR/ELWA (stabil), Shelly-Relais | |
| Schaltaktor, Wärmepumpe | nur Shelly-Relais als Heizstab | SG-Ready fehlte |
| Wetter/Prognose | forecast.solar (Dienst) | |
| Dynamischer Tarif | Tibber, aWATTar (Dienst) | |

**Abstraktion:** Regelung und Oberfläche arbeiten nur mit Kategorien und
Capabilities. Markenspezifisch waren nur Diagnosehinweise (siehe oben).

**Ergänzt in 3.0.0** (generisch zuerst):

| Treiber | Klassen |
|---|---|
| HTTP/JSON | Zähler, Wechselrichter, Batterie (lesend), Schaltaktor-Heizstab |
| MQTT | Zähler, Wechselrichter, Batterie (lesend), Schaltaktor-Heizstab |
| Home Assistant (REST) | Zähler, Wechselrichter, Batterie (lesend), Schaltaktor-Heizstab, Wallbox (Strom- und Schalter-Entität) |
| Tasmota | Zähler (SML-Lesekopf), Schaltaktor-Heizstab |
| Modbus TCP generisch mit Registerprofil | Wechselrichter, Batterie (lesend) |
| Victron GX (Modbus TCP) | Wechselrichter mit Batterie und Netzwerten |
| KEBA P30 (UDP) | Wallbox |
| Wärmepumpe SG-Ready | über zwei Schaltausgänge (Shelly Gen2+, Home Assistant) |
| Wallbox generisch über HTTP und MQTT | Wallbox (Status per JSON-Pfad, Steuerung per URL bzw. Topic) |
| Wallboxen Modbus TCP | cFos Power Brain, Alfen Eve, Webasto Next/Ampure Unite, Vestel EVC04, Bender CC612/613 (Mennekes Amtron, Walther, Ubitricity), Phoenix Contact CHARX und EV-ETH (Wallbe, ESL Walli), KEBA P30 x/P40, Amperfied, EM2GO, innogy/E.ON eBox, Siemens VersiCharge, SolaX EVC, Peblar, Pulsares |
| Wallboxen Modbus RTU/ASCII über TCP-Gateway | Heidelberg Energy Control, Mennekes Amtron Compact 2.0s, ABL eMH1, KSE, OBO Bettermann |
| Wallboxen HTTP | Hardy Barth eCB1 und Salia, SMA EV Charger, EVSE-WiFi, openWB Pro |
| Wallbox MQTT | openWB 1.9 im Modus 'Nur Ladepunkt' |
| Wallbox WebSocket | Fronius Wattpilot Home/Go |
| Wallboxen über offizielle Cloud-API | Easee, Zaptec |

Registerkarten und Abläufe nach Herstellerdokumentation; wo sie fehlt, nach
der Referenzimplementierung in evcc (MIT-Lizenz). Kein Code übernommen.
Alle neuen Treiber sind als experimentell markiert und mit gefälschten
Gegenstellen getestet, nicht an Hardware.

**Bewusst weggelassen:**

- Fahrzeug-Cloud-APIs (VW, BMW, Hyundai/Kia, Mercedes, Polestar, Renault).
  Inoffizielle Schnittstellen ändern sich ohne Ankündigung, die
  Nutzungsbedingungen der Hersteller verbieten teils Drittzugriffe, und es
  gibt kein Testgerät. Der Ladestand kommt über die Wallbox (OCPP) oder über
  Home Assistant.
- Tesla Wall Connector Gen 3: Die lokale API ist nur lesend, Ladestrom
  nicht steuerbar.
- Fronius Wattpilot Flex: anderes Anmeldeverfahren, nicht dokumentiert.
- Wallbox Pulsar und weitere Boxen ohne lokale Schnittstelle: über OCPP,
  wo das Gerät es anbietet.
- Serielle Modbus-Adapter direkt am Server (USB-RS485): RS485-Boxen laufen
  über ein Modbus-TCP-Gateway. Das erspart Gerätedurchreichung im Container.
- OCPP 2.0.1: Version 1.6 ist bei den verbreiteten Boxen Standard.
- GoodWe und Growatt mit eigenem Treiber: Abdeckung über
  Modbus-Registerprofil bzw. SunSpec; Registerkarten ohne Testgerät nicht
  verifizierbar.

## 3. Einrichtung

| Befund | Maßnahme |
|---|---|
| keine Prüfung der Treiber-Konfiguration beim Anlegen (Pflichtfelder, Zahlen, Auswahlwerte, Port) | Feldvalidierung mit Feldname und Ursache (422), auch vor dem Verbindungstest |
| Prioritäts-Schritt schrieb Batterien in die Kette (seit 2.16 nicht mehr vorgesehen) | nur Lasten |
| Assistent ließ eine Einrichtung ohne Netzmessung abschließen; die Regelung bleibt dann dauerhaft im Sicherheitsstopp | Hinweis im letzten Schritt |
| leere Emoji-Platzhalter im Assistenten | entfernt |
| fehlende Tests für Kombinationen | Tests für leere DB, nur PV, PV + Batterie, ohne Auto, ohne Warmwasser, nur Zähler, je ein Gerät jeder neuen Marke |
| keine automatische Prüfung, dass API-Schemas und Frontend-Typen übereinstimmen | Test gleicht die Felder ab |

## 4. Mehrere Geräte pro Klasse

| Klasse | Befund | Entscheidung |
|---|---|---|
| Wechselrichter | PV wird summiert; Batteriewerte mehrerer Hybrid-WR: der letzte gewann | mehrere erlaubt; Batteriewerte werden summiert, Ladestand nach Kapazität gewichtet |
| Netzzähler | ein Netzanschlusspunkt; der letzte Zähler gewann | **höchstens 1** |
| Batterie (steuerbares Gerät) | Entladesperre und Hausversorgung werden je Gerät gerechnet; zwei Speicher würden doppelt entladen | **höchstens 1** |
| Wallbox / Fahrzeug | eigener Regler je Gerät, Verteilung über die Prioritätskette | mehrere erlaubt, Reihenfolge einstellbar; Übersicht zeigt jedes Gerät |
| Warmwasser / Heizstab / Wärmepumpe | wie Wallbox | mehrere erlaubt, Reihenfolge einstellbar |

Durchsetzung:

- Backend: 409 mit Code `category_limit`, beim Anlegen und beim Aktivieren.
- Frontend: „Hinzufügen“ ist dann deaktiviert, mit Hinweis.
- Migration: Bestehende zusätzliche Zähler bzw. Batterien werden
  deaktiviert (nicht gelöscht), mit Protokolleintrag.

## 5. Texte

Befund: 919 Textschlüssel, davon 271 ungenutzt; zwei Wörterbücher
(`de.ts` und `de-ui.ts`), wobei das zweite das erste überschrieb. Viele
Hilfetexte waren erklärende Absätze mit Du-Ansprache und Beispielen.
Bestätigungen, Wertregler, Zeitplan, Rollen und Tabellenköpfe hatten
feste deutsche Texte im Code.

Umgesetzt:

- Ein Wörterbuch `frontend/src/i18n/de.ts` (707 Schlüssel, nur genutzte).
  `en.ts` enthält nur noch Schlüssel, die es im Deutschen gibt; fehlende
  Übersetzungen fallen auf Deutsch zurück.
- Hilfetexte auf Einheit, Wertebereich oder eine kurze Zeile gekürzt,
  Seitenuntertitel entfernt, keine Ansprache, keine Emojis.
- Feste Texte in Komponenten, Stores und Hilfsfunktionen laufen über
  `t()` bzw. `tr()` (für Code außerhalb von Komponenten).
- Fehlermeldungen nennen Feld und Ursache (`invalid_config`) bzw. Host
  (`{host} antwortet nicht`).
- Typografische Anführungszeichen in Quelltexten (Frontend und Backend)
  durch gerade ersetzt.

## 6. Version und Changelog

Vorher gab es zwei Versionsquellen (`backend/app/__init__.py`,
`frontend/package.json`). Der Changelog war Fließtext, nicht im Image und
ohne Anzeige in der App.

Jetzt:

- `VERSION` ist die einzige Quelle für Backend, Frontend-Build und
  Docker-Label.
- `CHANGELOG.md` hat ein festes Format und ist im Image.
- Neue Endpunkte: `GET /api/system/changelog`, `POST /api/auth/me/seen`.
- Tab „Changelog“ in der App.
- Hinweisfenster nach Updates; die zuletzt gesehene Version wird je
  Nutzer in der DB gespeichert.

## Offen (Entscheidung nötig)

1. **Git-History:** VIN, Heimnetz-IPs, Fahrzeugname und Energieverlauf der
   Entwickleranlage stehen in alten Commits. Entfernen geht nur durch
   Umschreiben der History, etwa mit `git filter-repo`. Danach ändern sich
   alle Commit-IDs, und Klone sowie Forks müssen neu aufgesetzt werden.
   Das ist nicht rückgängig zu machen und deshalb nicht umgesetzt. Zu
   rotieren ist nichts, weil keine Secrets gefunden wurden.
2. **Tesla Fleet API:** Für eine kommerzielle Nutzung gelten Teslas
   Entwicklerbedingungen inklusive Gebühren. Jeder Kunde braucht einen
   eigenen Developer-Account, oder es läuft über eine Partnervereinbarung.
3. **forecast.solar und aWATTar:** Die kostenlosen Zugänge sind
   Fair-Use-Angebote. Ob sie für ein kommerzielles Produkt zulässig sind,
   ist mit den Anbietern zu klären.
4. **TimescaleDB:** Die Timescale License (TSL) erlaubt den Eigenbetrieb
   beim Kunden. Eine gehostete MinePower-Variante, bei der Datenbank-
   funktionen als Dienst angeboten werden, wäre nicht abgedeckt.
5. **Testdatensatz:** `diagnose_7d.json` enthält den echten Leistungsverlauf
   der Entwickleranlage, anonymisiert. Er kann auf Wunsch durch
   synthetische Daten ersetzt werden.
