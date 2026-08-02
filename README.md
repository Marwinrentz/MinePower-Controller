# MinePower

Überschussbasierte Ladesteuerung für E-Auto, Warmwasser und Hausbatterie.
MinePower liest die Netzeinspeisung und die verfügbaren PV-Überschüsse aus
und verteilt sie nach einer frei sortierbaren Prioritätskette auf die
steuerbaren Verbraucher im Haus – mit dem Ziel, die Einspeisung ins
öffentliche Netz möglichst nahe an 0 W zu halten, ohne Komfort oder
Anlagensicherheit zu opfern.

Die Anwendung läuft als Docker-Stack (FastAPI-Backend, React-Frontend,
TimescaleDB), braucht keine Cloud-Anbindung und keinen Smart-Home-Hub. Geräte
werden über eine Plugin-Architektur angebunden; ein neuer Treiber ist eine
einzelne Python-Datei ohne Änderungen am Kern.

[![License: proprietary](https://img.shields.io/badge/license-proprietary-lightgrey)](LICENSE)
[![Docker Image](https://img.shields.io/docker/v/marwinrentz1/minepower?sort=semver&label=docker%20hub)](https://hub.docker.com/r/marwinrentz1/minepower)
[![Build](https://github.com/Marwinrentz/MinePower-Controller/actions/workflows/docker.yml/badge.svg)](https://github.com/Marwinrentz/MinePower-Controller/actions/workflows/docker.yml)

## Inhalt

- [Funktionsweise des Regelkreises](#funktionsweise-des-regelkreises)
- [Unterstützte Geräte](#unterstützte-geräte)
- [Installation](#installation)
- [Tesla-Anbindung](#tesla-anbindung)
- [Betrieb](#betrieb)
- [Entwicklung](#entwicklung)
- [Sicherheit](#sicherheit)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Lizenz](#lizenz)

## Funktionsweise des Regelkreises

Der Regelkreis läuft alle zwei bis fünf Sekunden (konfigurierbar). Jeder
Zyklus liest die Messwerte aller aktivierten Geräte parallel ein, berechnet
daraus ein Budget und verteilt es entlang der Prioritätskette:

```
budget = Σ(Leistung aller steuerbaren Lasten) − Netzleistung (geglättet) − Sollwert
```

Innerhalb eines Totbands um den Sollwert wird nicht nachgeregelt, um
Flattern durch Messrauschen zu vermeiden. Das Totband ist adaptiv: Bei
stabiler Erzeugung wird enger geregelt, bei starkem Wolkenwechsel weiter,
um unnötige Start-/Stopp-Zyklen zu vermeiden.

Batterien in der Prioritätskette werden standardmäßig nicht aktiv
angesteuert. Hybrid-Wechselrichter (Sungrow SH, Fronius GEN24 Plus, SMA
Sunny Boy Storage) regeln ihre Batterie bereits selbst auf Nulleinspeisung.
MinePower liest Ladestand und Leistung nur mit, damit Lasten, die in der
Kette vor der Batterie stehen, deren aktuelle Ladeleistung mitnutzen
können – der Wechselrichter reduziert seine eigene Ladung automatisch,
sobald eine andere Last zugreift. Aktives Zwangsladen oder eine
Reserve-SoC-Vorgabe bleiben eine bewusst zu aktivierende Zusatzfunktion für
Sonderfälle.

Drei Mechanismen sollen verhindern, dass Überschuss ungenutzt ins Netz
geht:

**Lückenfüller.** Eine Wallbox kann nur ganze Ampere-Stufen laden; der
Regler rundet dabei grundsätzlich ab, weil Aufrunden Netzbezug erzeugen
würde. Der so entstehende Rest – etwa 180 W, während die nächste Last
mindestens 6 A (1380 W) benötigt – wird nicht verworfen, sondern an eine
stufenlos modulierbare Last (in der Regel Warmwasser) weitergereicht,
unabhängig von deren Position in der Prioritätskette.

**Solarprognose.** Die Anbindung an forecast.solar fließt aktiv in
Entscheidungen ein statt nur zur Anzeige zu dienen. Bricht die Erzeugung
kurzzeitig ein, während die Prognose für die nächste halbe Stunde weiter
hoch ist, wird eine laufende Ladung gehalten statt beendet – ein Neustart
kostet durch die Start-Hysterese mehr Überschuss, als das Halten an
Netzstrom zieht. Bei der Zielladung mit Deadline verschiebt sich die
Sicherheitsreserve vorausschauend, wenn absehbar ist, dass die Sonne bis
zum gewünschten Zeitpunkt nicht ausreicht.

**Kennzahl „verschenkter Solarstrom“.** Einspeisung oberhalb des
Netz-Sollwerts, die keine steuerbare Last aufgenommen hat, wird als eigene
Messgröße geführt – live im Dashboard mit Klartext-Begründung, warum der
Überschuss liegen blieb, und historisch in der Statistik (kWh, Anteil an
der Erzeugung, Gegenwert in Euro).

Fällt die Netzmessung aus oder ist die Regelung manuell pausiert, werden
alle steuerbaren Lasten sicher gestoppt. Dieses Verhalten hat Vorrang vor
allen genannten Optimierungen und wird durch keine davon aufgeweicht.

## Unterstützte Geräte

Jeder Treiber ist mit einem Reifegrad gekennzeichnet, der im Setup-
Assistenten als Hinweis erscheint:

- **Stabil** – Registerkarte bzw. API an realer Hardware verifiziert.
- **Beta** – Grundfunktionen geprüft, einzelne Modell- oder
  Firmware-Varianten können abweichen.
- **Experimentell** – Registerbelegung aus Herstellerdokumentation oder
  Community-Quellen, nicht an Hardware verifiziert.

Unabhängig vom Reifegrad meldet jeder Treiber unplausible Werte und nicht
angenommene Schreibbefehle im Klartext, statt sie stillschweigend zu
verarbeiten.

| Gerät | Kategorie | Protokoll | Reifegrad |
|---|---|---|---|
| Sungrow SG-Serie | Wechselrichter | Modbus TCP (WiNet-S) | Stabil |
| Sungrow SH-Serie | Hybrid-Wechselrichter, inkl. Batterie-Monitoring | Modbus TCP | Stabil |
| Sungrow DTSU666 | Netzzähler | Modbus TCP (über WR) | Stabil |
| Sungrow SBR/SBH | Batterie, optional aktiv steuerbar | Modbus TCP | Beta |
| Sungrow AC007/AC011E | Wallbox | Modbus TCP | Beta |
| Fronius GEN24 (Plus) | Wechselrichter, inkl. Batterie-Monitoring | Solar API | Stabil |
| Fronius Smart Meter | Netzzähler | Solar API | Stabil |
| SMA Sunny Boy / Tripower | Wechselrichter, inkl. Speicher-Monitoring | Modbus TCP | Stabil |
| SMA Energy Meter / Home Manager | Netzzähler | Modbus TCP | Beta |
| Kostal Plenticore, GoodWe ET/EH, SolarEdge, Delta, SMA Tripower X | Wechselrichter | SunSpec / Modbus TCP | Beta |
| SunSpec-Zähler | Netzzähler | SunSpec / Modbus TCP | Beta |
| Huawei SUN2000 + LUNA2000 | Hybrid-Wechselrichter | Modbus TCP | Experimentell |
| Generischer Modbus-Zähler (z. B. Eastron SDM630) | Netzzähler | Modbus TCP, frei konfigurierbar | Stabil |
| Shelly 3EM / Pro 3EM | Netzzähler | HTTP | Stabil |
| Shelly-Relais | Warmwasser (Ein/Aus) | HTTP | Stabil |
| my-PV AC·THOR / ELWA-E / ELWA 2 | Warmwasser, stufenlos modulierend | HTTP oder Modbus TCP | Stabil |
| go-e Charger | Wallbox | HTTP v2 | Stabil |
| Tesla (beliebiges Modell) | Fahrzeug als Ladepunkt | BLE-Proxy oder Fleet-API | Stabil |
| Generische Wallbox | Wallbox | OCPP 1.6J | Beta |

Bei SunSpec fragt der Treiber das Gerät beim Verbinden nach seinem
Registeraufbau ab, statt eine feste Registerkarte vorauszusetzen – das
macht ihn unempfindlich gegen Firmware-Updates, auf Kosten etwas höherer
Verbindungszeit beim ersten Scan.

Ein neuer Treiber erfordert keine Änderung am Regelkern; siehe
[docs/adding-drivers.md](docs/adding-drivers.md).

## Installation

Voraussetzung ist Docker mit Compose-Plugin, amd64 oder arm64. Ein
`.env`-File ist nicht erforderlich – das JWT-Secret wird beim ersten Start
automatisch erzeugt und in der Datenbank persistiert.

```bash
git clone https://github.com/Marwinrentz/MinePower-Controller.git minepower
cd minepower
docker compose up -d --build
```

Danach `http://localhost:8080` öffnen. Beim ersten Zugriff wird das
Administrator-Konto angelegt, anschließend führt der Setup-Assistent durch
die Geräte-Einrichtung. Alternativ legt „Demo-Modus starten“ eine
vollständig simulierte Anlage an, um die Oberfläche ohne Hardware
kennenzulernen.

Ohne lokalen Build lässt sich stattdessen das veröffentlichte Image
verwenden:

```bash
docker pull marwinrentz1/minepower:latest
```

Verfügbare Tags: `latest` sowie versionierte Releases (`2.0.0` und
folgende). Eine fertige Compose-Datei für Unraid liegt unter
[docs/unraid.md](docs/unraid.md).

| Dienst | Port | Zweck |
|---|---|---|
| `app` | `${HTTP_PORT:-8080}` → 8000 | Web-Oberfläche, REST-API, WebSocket |
| `db` | intern 5432 | TimescaleDB, Volume `db_data` |
| `mqtt` (optional) | 1883 | `docker compose --profile mqtt up -d` |

Für OCPP-Wallboxen zusätzlich den Treiber-Port freigeben (Standard 8887):
`ports: ["8887:8887"]`. Die Wallbox wird dann auf
`ws://<server-ip>:8887/<charge-point-id>` als Backend eingerichtet.

Für produktive Installationen empfiehlt sich, `.env.example` nach `.env`
zu kopieren und ein eigenes `POSTGRES_PASSWORD` zu setzen. Headless-
Konfiguration über eine gemountete JSON-Datei ist unter
[docs/headless.md](docs/headless.md) beschrieben.

## Tesla-Anbindung

Tesla-Fahrzeuge werden direkt als Ladepunkt angesteuert, unabhängig von der
verwendeten Wallbox. Zwei Anbindungswege stehen zur Wahl:

- **BLE-Proxy (cloud-frei).** Voraussetzung ist ein bereits laufender
  [TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy), etwa auf
  einem Raspberry Pi in Bluetooth-Reichweite des Fahrzeugs. MinePower trägt
  nur dessen Adresse und die Fahrzeug-VIN ein; die Schlüssel-Kopplung
  erfolgt im Proxy selbst. Ein Referenz-Compose für den Proxy liegt unter
  [examples/tesla-ble-proxy](examples/tesla-ble-proxy/docker-compose.yml)
  und ist bewusst nicht Teil des Haupt-Stacks.
- **Fleet-API (Cloud).** Erfordert ein OAuth-Token von Tesla, liefert dafür
  zuverlässig den Ladestand. Das Polling ist konservativ ausgelegt, um im
  Rate-Limit zu bleiben.

## Betrieb

**Aktualisieren:**

```bash
git pull
docker compose up -d --build
```

**Sichern.** Für die Konfiguration (Geräte, Einstellungen, Prioritäten)
genügt der Export unter Einstellungen → Sicherung als JSON. Für ein
vollständiges Backup inklusive Messwert-Historie:

```bash
docker compose exec db pg_dump -U minepower -d minepower --no-owner --no-privileges > backup.sql
```

**Wiederherstellen.** Konfigurations-JSON über die Oberfläche importieren,
oder für ein vollständiges Restore:

```bash
docker compose up -d db
docker compose exec -T db psql -U minepower -d minepower -c "SELECT timescaledb_pre_restore();"
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U minepower -d minepower < backup.sql
docker compose exec -T db psql -U minepower -d minepower -c "SELECT timescaledb_post_restore();"
docker compose up -d app
```

Die App zuletzt starten: Sie legt beim Start nur dann Standardwerte oder
einen Demo-Zugang an, wenn die Datenbank leer ist.

**Logs:** `docker compose logs -f app` (Log-Level über `LOG_LEVEL`).

**Diagnose:** Die Seite Ereignisse → Diagnose zeigt Geräte-Rohwerte, die
aktuelle Regelgüte (wirksames Totband, Wetterlage) und jede
Regel-Entscheidung live. Ein Diagnosebericht (JSON) lässt sich dort
zeitraumbezogen exportieren.

## Entwicklung

```bash
# Backend (Python 3.12)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload      # benötigt eine laufende Datenbank
python -m pytest                    # Regelungs- und Treiber-Tests, ohne Hardware

# Frontend
cd frontend
npm install
npm run dev                         # Dev-Server mit Proxy auf localhost:8000
```

Die Regelungslogik (`backend/app/core/regulation.py`) ist als reine,
hardwareunabhängige Klassenhierarchie implementiert und vollständig über
eine injizierte Uhr testbar. Neue Verhaltensweisen gehören dort und in die
zugehörige Testdatei, nicht in den Control-Loop selbst.

Weiterführende Dokumentation:

- [Architektur-Übersicht](docs/architecture.md)
- [Neuen Treiber hinzufügen](docs/adding-drivers.md)
- [Headless-Konfiguration](docs/headless.md)
- [Installation auf Unraid](docs/unraid.md)

## Sicherheit

- Authentifizierung über JWT mit bcrypt-gehashten Passwörtern, Rollen
  (Administrator, Nutzer, Nur-Lesen) werden bis in die API durchgesetzt,
  Login ist rate-limitiert.
- Ein einzelner Geräte-Timeout bringt den Regelkreis nicht zum Absturz.
  Nach drei aufeinanderfolgenden Fehlversuchen gilt ein Gerät als offline;
  die Verbindung wird automatisch neu aufgebaut, sobald das Gerät wieder
  antwortet.
- Ohne verlässliche Netzmessung werden alle steuerbaren Lasten gestoppt.
  Es wird nie ohne Netzmessung geladen oder geheizt.
- Zugangsdaten werden ausschließlich über Umgebungsvariablen bzw. `.env`
  konfiguriert. Der Konfigurations-Export enthält Geräte-Zugangsdaten im
  Klartext und sollte entsprechend sicher aufbewahrt werden.

## Bekannte Einschränkungen

- Der OCPP-Treiber deckt die für Überschussladen relevante Teilmenge von
  OCPP 1.6J ab (SmartCharging-Profil für die Stromvorgabe). Wallboxen ohne
  SmartCharging-Unterstützung lassen sich nur ein- und ausschalten, nicht
  stufenlos regeln. OCPP 2.0.1 ist nicht implementiert.
- Als Beta oder experimentell markierte Treiber (siehe Gerätetabelle oben)
  sollten vor dem Dauerbetrieb über „Verbindung testen“ mit den tatsächlich
  gelesenen Werten abgeglichen werden.
- Statistik-Export steht aktuell als CSV zur Verfügung; ein PDF-Bericht ist
  nicht enthalten.

## Lizenz

Proprietär, Copyright © 2026 Marwin Rentz. Der Betrieb einer eigenen
Installation über das offizielle Docker-Image ist gestattet;
Weiterverbreitung, Vermietung oder das Anbieten als Dienstleistung für
Dritte erfordert eine gesonderte Lizenz. Vollständiger Text: [LICENSE](LICENSE).
