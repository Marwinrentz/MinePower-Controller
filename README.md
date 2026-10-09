<img src="frontend/public/logo.svg" width="88" alt="MinePower">

# MinePower

[![License](https://img.shields.io/badge/license-proprietär%20%2F%20proprietary-lightgrey)](LICENSE)
[![Docker Image](https://img.shields.io/docker/v/marwinrentz1/minepower?sort=semver&label=docker%20hub)](https://hub.docker.com/r/marwinrentz1/minepower)
[![Build](https://github.com/Marwinrentz/MinePower-Controller/actions/workflows/docker.yml/badge.svg)](https://github.com/Marwinrentz/MinePower-Controller/actions/workflows/docker.yml)

**[Deutsch](#deutsch) · [English](#english)**

---

<a name="deutsch"></a>

# Deutsch

Überschussbasierte Ladesteuerung für E-Auto, Warmwasser und Hausbatterie.
MinePower liest die Netzleistung und verteilt den verfügbaren PV-Überschuss
nach einer frei sortierbaren Prioritätskette auf die steuerbaren Verbraucher
im Haus. Ziel ist eine Einspeisung möglichst nahe an 0 W, ohne Komfort oder
Anlagensicherheit zu opfern.

Die Anwendung läuft als Docker-Stack (FastAPI-Backend, React-Frontend,
TimescaleDB), braucht keine Cloud-Anbindung und keinen Smart-Home-Hub. Geräte
werden über eine Plugin-Architektur angebunden; ein neuer Treiber ist eine
einzelne Python-Datei ohne Änderungen am Kern.

> **Zum Lizenzmodell.** Der Quelltext ist einsehbar, MinePower ist aber nicht
> Open Source: Forken, Verändern und Betreiben für eigene Zwecke ist erlaubt,
> die Weitergabe an Dritte nicht. Rückmeldungen sind ausdrücklich erwünscht —
> siehe [Lizenz und Rechtliches](#lizenz-und-rechtliches).

## Inhalt

- [Funktionsweise des Regelkreises](#funktionsweise-des-regelkreises)
- [Unterstützte Geräte](#unterstützte-geräte)
- [Installation](#installation)
- [Tesla-Anbindung](#tesla-anbindung)
- [Betrieb](#betrieb)
- [Entwicklung](#entwicklung)
- [Sicherheit](#sicherheit)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Lizenz und Rechtliches](#lizenz-und-rechtliches)

## Funktionsweise des Regelkreises

Der Regelkreis läuft in einem festen Takt und rechnet in jedem Durchgang aus,
wie viel Leistung den steuerbaren Verbrauchern insgesamt zur Verfügung steht:

```
budget = Σ(Leistung aller steuerbaren Lasten) − Netzleistung (geglättet) − Sollwert
```

Innerhalb eines Totbands um den Sollwert wird nicht nachgeregelt, um Flattern
durch Messrauschen zu vermeiden. Das Totband ist adaptiv: Bei stabiler
Erzeugung wird enger geregelt, bei starkem Wolkenwechsel weiter, um unnötige
Start-/Stopp-Zyklen zu vermeiden.

### Der Hausspeicher hat Vorrang

```
Topf für Auto und Warmwasser = budget − Entladeleistung
```

Die Korrektur ist bewusst **einseitig**, und das ist der entscheidende Punkt.
Die Budgetformel kennt nur den Netzpunkt, und dort steht eine Bilanz:

* Was der Speicher **entlädt**, steckt voll im Budget, kommt aber nicht aus
  der Sonne. Ein Speicher, der gerade einspringt, sieht am Netzpunkt aus wie
  ein ausgeglichenes Haus. Ohne diesen Abzug würden Auto und Warmwasser ihn
  leerziehen, während die Anzeige Überschuss meldet. → abziehen.
* Was der Speicher **lädt**, ist am Netzpunkt bereits verbraucht und taucht im
  Budget gar nicht mehr auf. → nichts abziehen; es gehört ihm ohnehin schon.

Ein zweiter Abzug der Ladeleistung ist deshalb kein zusätzlicher Schutz,
sondern ein Rechenfehler mit Rückkopplung: Je weniger die Lasten nehmen, desto
mehr lädt der Speicher, desto größer der Abzug. Bei 6 kW Sonne, 0,7 kW Haus
und einem Speicher, der nur 2 kW aufnimmt, blieb dem Auto am Ende nichts — die
freien 3,3 kW gingen vollständig ins Netz.

Den Haushalt versorgt die Batterie davon unberührt weiter — geschützt wird nur
gegen die *steuerbaren* Lasten.

**Abregeln statt abschalten.** Eine kleine Entladung beendet keine laufende
Ladung. Sie wird vom Budget abgezogen, die Last regelt herunter — mehr nicht.
Erst ab `battery_drain_limit_w` (Standard 250 W), und erst nach 30 s
Bestätigung, wird hart gestoppt. Ein Nothalt kostet danach die volle
Startschwelle und eine Minute Startverzögerung; er ist um ein Vielfaches
teurer als die paar Wattstunden, die er spart. Mit einem Schwellwert von
50 W ohne Bestätigungszeit beendete jede Lastspitze im Haus die Ladung, und
der Regler taktete sich im Minutenrhythmus selbst aus.

Batterien werden dabei nicht aktiv angesteuert. Hybrid-Wechselrichter regeln
ihre Batterie bereits selbst auf Nulleinspeisung; MinePower liest Ladestand
und Leistung nur mit. Aktives Zwangsladen oder eine Reserve-SoC-Vorgabe
bleiben eine bewusst zu aktivierende Zusatzfunktion für Sonderfälle.

### Ampere-Regelung nach Messung

Der Sollstrom einer Wallbox wird nicht mit dem Nennwert von 230 V je Phase
gerechnet, sondern mit dem Verhältnis, das sich aus Messung und gesetztem
Ladestrom tatsächlich ergibt. Reale Netzspannung, Phasenzahl und Ladeverluste
stecken damit im Umrechnungsfaktor. Ein starr gerechneter Sollstrom liegt
sonst systematisch daneben und erzeugt genau das, was vermieden werden soll:
Einspeisung oder Bezug aus dem Speicher. Erhöht wird in begrenzten Schritten,
weil ein Fahrzeug einem neuen Sollstrom erst nach einigen Sekunden folgt;
gesenkt wird ohne Begrenzung.

Dafür muss die *gemessene* Leistung fein genug aufgelöst sein. Tesla meldet
`charger_power` als **ganzzahligen kW-Wert**: 6 A einphasig sind real 1380 W,
gemeldet wird „1". Dieser Wert ging bisher in die Budgetrechnung ein — ein
Fehler von bis zu ±500 W, der in beide teuren Richtungen wirkt. Gerechnet wird
deshalb aus `charger_actual_current × charger_voltage × Phasen`; der kW-Wert
bleibt nur Rückfall, wenn kein Strom gemeldet wird.

Zwei weitere Fallen stecken in derselben Rechnung:

* **Phasenzahl.** Tesla meldet in Europa `charger_phases = 2` für
  dreiphasiges Laden — zweiphasig gibt es nicht, gemeint ist „mehrphasig".
  Ungeprüft übernommen fehlt in jeder Leistungsrechnung ein Drittel, und der
  Regler fordert das Dreifache an, was die Sonne hergibt.
* **Mindeststrom der Hardware.** Ein Tesla nimmt erst ab 5 A an, viele
  Wallboxen erst ab 6 A. Wird weniger kommandiert, klemmt das Gerät nach oben
  und zieht mehr, als zugeteilt war. Der Regler übernimmt den Mindest- und
  Höchststrom deshalb vom Treiber und rechnet mit den echten Grenzen. Konkret
  heißt das auch: Dreiphasig sind 6 A keine 1,38 kW, sondern 4,14 kW — so viel
  Überschuss muss vorliegen, bevor überhaupt gestartet wird.

Gemessen wird dabei **nur im eingeschwungenen Zustand**: erst wenn derselbe
Sollstrom 45 Sekunden unverändert anliegt. Wer während der Rampe misst, teilt
eine noch kleine Leistung durch den schon großen Sollstrom und lernt einen zu
kleinen Faktor. Genau der ist teuer — der Regler fordert daraufhin zu viele
Ampere an, das Fahrzeug zieht mehr als die Sonne hergibt, der Speicher deckt
die Lücke, und der Batterie-Schutz beendet die Ladung. Plausibilitätsgrenzen
allein reichen dagegen nicht, weil ein Rampenmesswert oft noch innerhalb der
Toleranz liegt. Nach unten begrenzt zusätzlich die Netzspannung nach EN 50160
(230 V ±10 %) — weniger kann eine echte Messung nicht ergeben.

### Drei Mechanismen gegen verschenkten Strom

**Lückenfüller.** Eine Wallbox kann nur ganze Ampere-Stufen laden; der
abgerundete Rest ginge sonst ins Netz. Kann ihn eine stufenlos modulierbare
Last aufnehmen — etwa ein my-PV-Heizstab —, bekommt sie ihn, unabhängig von
ihrem Platz in der Prioritätskette. Die Alternative wäre nicht „später",
sondern „verschenkt".

**Ehrliche Quantisierung.** Der Regler rundet den Ladestrom immer ab, nie auf,
und meldet die tatsächlich abgenommene Leistung zurück. Nur so weiß der
Regelkreis, wie viel wirklich übrig bleibt.

**Aktive Prognose.** Bei einem kurzen Wolkendurchzug mit absehbarer Erholung
wird eine laufende Ladung gehalten statt abgeschaltet — ein Neustart kostet
über die Hysterese mehr Überschuss als das Halten. Reicht die vorhergesagte
Sonne bis zur Deadline einer Zielladung nicht, beginnt die Netzladung früher.

Wie gut das gelingt, ist messbar: Jeder Takt erfasst, wie viel Überschuss über
dem Netz-Sollwert eingespeist wurde, ohne dass eine Last ihn aufgenommen hat —
mit Begründung im Klartext.

### Preisoptimiertes Laden sperrt den Hausspeicher

Wallbox und Heizstab kennen die Betriebsart **Preisoptimiert**: Liegt der
Börsenstrompreis unter der Günstig-Schwelle aus den Einstellungen, laden sie
aus dem Netz; darüber schalten sie auf reines Überschussladen zurück. Die
Sonne wird also nicht verschenkt, nur weil die Börse teuer ist.

Der entscheidende Teil ist, **woher** der Strom in dieser Stunde kommt. Ein
Hybrid-Wechselrichter kennt keinen Strompreis — er sieht am Netzpunkt eine
neue Last und deckt sie aus der Batterie. Ohne Gegenmaßnahme lädt das Auto
also nicht mit billigem Netzstrom, sondern mit dem eigenen, teuer geladenen
Speicher: einmal geladen, einmal entladen, zweimal Wirkungsgrad verloren, und
abends steht das Haus ohne Reserve da.

Für die Dauer des günstigen Fensters wird der Speicher deshalb gesperrt —
wahlweise **Sperren** (weder laden noch entladen) oder **Vollladen** (aus dem
günstigen Netz laden, aber nicht entladen). Beides über `set_mode`, ersatzweise
über eine Reserve-SoC-Vorgabe von 100 %, damit auch Wechselrichter ohne
Modus-Register mitmachen. Endet das Fenster, geht der Speicher zurück auf
Automatik; beim Beenden von MinePower ebenfalls — es bleibt kein gesperrter
Wechselrichter zurück.

Lässt sich der Speicher gar nicht steuern (reines Monitoring) und entlädt er
gerade, wird das Netzladen **ausgesetzt** statt ihn leerzuziehen, mit
Begründung im Klartext auf dem Dashboard. Wer das anders will — etwa weil der
Wechselrichter ohnehin schon auf „nicht entladen" steht —, schaltet
`price_allow_battery_drain` am Gerät frei.

## Unterstützte Geräte

Die letzte Spalte ist die wichtigste. **Nur vier Anbindungen liefen je gegen
echte Hardware.** Alle übrigen wurden nach Herstellerdokumentation und
Community-Quellen geschrieben und sind nie an einem physischen Gerät gelaufen.
Registerbelegungen weichen in der Praxis regelmäßig ab — bei Offsets,
Skalierung und Vorzeichen. Solche Treiber sind in der Oberfläche als
*Experimentell* gekennzeichnet und sollten vor dem Dauerbetrieb über
„Verbindung testen" mit den tatsächlich gelesenen Werten abgeglichen werden.

| Gerät | Kategorie | Protokoll | An echter Hardware erprobt |
|---|---|---|---|
| Sungrow SH-Serie | Hybrid-Wechselrichter, inkl. Batterie-Monitoring | Modbus TCP | **Ja** — SH-RT, Dauerbetrieb |
| Sungrow DTSU666 (am SH) | Netzzähler | Modbus TCP über WR | **Ja** — Dauerbetrieb |
| my-PV AC·THOR / ELWA-E / ELWA 2 | Warmwasser, stufenlos modulierend | HTTP oder Modbus TCP | **Ja** — AC·THOR, Modbus TCP |
| Tesla (beliebiges Modell) | Fahrzeug als Ladepunkt | BLE-Proxy oder Fleet-API | **Ja** — über BLE-Proxy |
| Sungrow SG-Serie | Wechselrichter | Modbus TCP (WiNet-S) | Nein |
| Sungrow DTSU666 (am SG) | Netzzähler | Modbus TCP über WR | Nein |
| Sungrow SBR/SBH | Batterie, optional aktiv steuerbar | Modbus TCP | Nein |
| Sungrow AC007/AC011E | Wallbox | Modbus TCP | Nein |
| Fronius GEN24 (Plus) | Wechselrichter, inkl. Batterie-Monitoring | Solar API | Nein |
| Fronius Smart Meter | Netzzähler | Solar API | Nein |
| SMA Sunny Boy / Tripower | Wechselrichter, inkl. Speicher-Monitoring | Modbus TCP | Nein |
| SMA Energy Meter / Home Manager | Netzzähler | Modbus TCP | Nein |
| Kostal Plenticore, GoodWe ET/EH, SolarEdge, Delta, SMA Tripower X | Wechselrichter | SunSpec / Modbus TCP | Nein |
| SunSpec-Zähler | Netzzähler | SunSpec / Modbus TCP | Nein |
| Huawei SUN2000 + LUNA2000 | Hybrid-Wechselrichter | Modbus TCP | Nein |
| Generischer Modbus-Zähler (z. B. Eastron SDM630) | Netzzähler | Modbus TCP, frei konfigurierbar | Nein |
| Shelly 3EM / Pro 3EM | Netzzähler | HTTP | Nein |
| Shelly-Relais | Warmwasser (Ein/Aus) | HTTP | Nein |
| go-e Charger | Wallbox | HTTP v2 | Nein |
| Generische Wallbox | Wallbox | OCPP 1.6J | Nein |
| KEBA KeContact P30 (c-/x-series) | Wallbox | UDP 7090 | Nein |
| Victron GX (Cerbo, Venus) | Wechselrichter mit Netz und Batterie (lesend) | Modbus TCP | Nein |
| Generischer Modbus-Wechselrichter / -Batterie | Wechselrichter, Batterie (lesend) | Modbus TCP, Registerprofil | Nein |
| HTTP/JSON (beliebige Quelle) | Netzzähler, Wechselrichter, Batterie (lesend), Schaltaktor | HTTP | Nein |
| MQTT (beliebige Topics) | Netzzähler, Wechselrichter, Batterie (lesend), Schaltaktor | MQTT | Nein |
| Home Assistant | Netzzähler, Wechselrichter, Batterie (lesend), Schaltaktor, Wallbox | REST-API | Nein |
| Tasmota | Netzzähler (SML-Lesekopf), Schaltaktor | HTTP | Nein |
| Wärmepumpe mit SG-Ready | Warmwasser (Einschaltempfehlung) | Shelly Gen2+ oder Home Assistant | Nein |
| cFos Power Brain | Wallbox | Modbus TCP | Nein |
| Alfen Eve Single/Double Pro-line | Wallbox | Modbus TCP | Nein |
| Amperfied connect.home/business/solar | Wallbox | Modbus TCP | Nein |
| Heidelberg Energy Control | Wallbox | Modbus RTU über TCP-Gateway | Nein |
| Webasto Next, Ampure Unite, Vestel EVC04 | Wallbox | Modbus TCP | Nein |
| Bender CC612/CC613 (Mennekes Amtron Xtra/Premium/4You, Walther, Ubitricity) | Wallbox | Modbus TCP | Nein |
| Mennekes Amtron Compact 2.0s | Wallbox | Modbus RTU über TCP-Gateway | Nein |
| Phoenix Contact CHARX SEC, EV-CC/EV-ETH (Wallbe, ESL Walli) | Wallbox | Modbus TCP | Nein |
| KEBA KeContact P30 x / P40 | Wallbox | Modbus TCP | Nein |
| ABL eMH1 | Wallbox | Modbus ASCII über TCP-Gateway | Nein |
| EM2GO Home, innogy/E.ON eBox, Siemens VersiCharge, SolaX EVC, Peblar, Pulsares | Wallbox | Modbus TCP | Nein |
| KSE wallbox, OBO Bettermann | Wallbox | Modbus RTU über TCP-Gateway | Nein |
| Hardy Barth eCB1 und Salia | Wallbox | HTTP | Nein |
| SMA EV Charger 7.4/22 | Wallbox | HTTP | Nein |
| EVSE-WiFi / SimpleEVSE | Wallbox | HTTP | Nein |
| openWB Pro | Wallbox | HTTP | Nein |
| openWB 1.9 'Nur Ladepunkt' | Wallbox | MQTT | Nein |
| Fronius Wattpilot Home/Go | Wallbox | WebSocket | Nein |
| Easee, Zaptec | Wallbox | Cloud-API | Nein |
| Wallbox generisch | Wallbox | HTTP, MQTT | Nein |

Zusätzlich gibt es simulierte Geräte für den Demo-Modus, mit denen sich die
Oberfläche und der Regelkreis ohne jede Hardware ausprobieren lassen.

Erfahrungsberichte zu ungetesteten Geräten sind willkommen — ein
Diagnosebericht aus der laufenden Anlage sagt mehr als jede Fehlerbeschreibung.

## Installation

Voraussetzung ist Docker mit Compose-Plugin, amd64 oder arm64. Eine
`.env`-Datei ist nicht erforderlich; das JWT-Secret wird beim ersten Start
erzeugt und in der Datenbank persistiert.

```bash
git clone https://github.com/Marwinrentz/MinePower-Controller.git minepower
cd minepower
docker compose up -d --build
```

Danach `http://localhost:8080` öffnen. Beim ersten Zugriff wird das
Administrator-Konto angelegt, anschließend führt der Setup-Assistent durch die
Geräte-Einrichtung. Alternativ legt „Demo-Modus starten" eine vollständig
simulierte Anlage an.

Ohne lokalen Build lässt sich das veröffentlichte Image verwenden:

```bash
docker pull marwinrentz1/minepower:latest
```

| Dienst | Port | Zweck |
|---|---|---|
| `app` | `${HTTP_PORT:-8080}` → 8000 | Web-Oberfläche, REST-API, WebSocket; Volume `backup_data` (Sicherung) |
| `db` | intern 5432 | TimescaleDB, Volume `db_data` |
| `mqtt` (optional) | 1883 | `docker compose --profile mqtt up -d` |

Eine fertige Compose-Datei für Unraid liegt unter [docs/unraid.md](docs/unraid.md).

## Tesla-Anbindung

Tesla-Fahrzeuge werden direkt als Ladepunkt angesteuert, unabhängig von der
verwendeten Wallbox. Zwei Wege stehen zur Wahl:

- **BLE-Proxy (cloud-frei).** Voraussetzung ist ein bereits laufender
  [TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy), etwa auf
  einem Raspberry Pi in Bluetooth-Reichweite. MinePower trägt nur dessen
  Adresse und die Fahrzeug-VIN ein; die Schlüssel-Kopplung erfolgt im Proxy
  selbst. Ein Referenz-Compose liegt unter
  [examples/tesla-ble-proxy](examples/tesla-ble-proxy/docker-compose.yml) und
  ist bewusst nicht Teil des Haupt-Stacks.
- **Fleet-API (Cloud).** Erfordert ein OAuth-Token von Tesla, liefert dafür
  zuverlässig den Ladestand. Das Polling ist konservativ ausgelegt, um im
  Rate-Limit zu bleiben.

In beiden Fällen bringt die betreibende Person die Zugangsdaten selbst mit;
MinePower enthält keine. Wer die Fleet-API nutzt, geht damit ein eigenes
Nutzungsverhältnis mit Tesla ein — siehe
[Lizenz und Rechtliches](#lizenz-und-rechtliches).

## Betrieb

**Aktualisieren**

```bash
git pull
docker compose up -d --build
```

**Sichern.** Für die Konfiguration genügt der Export unter Einstellungen →
Sicherung als JSON. Für ein vollständiges Backup inklusive Messwert-Historie:

```bash
docker compose exec db pg_dump -U minepower -d minepower --no-owner --no-privileges > backup.sql
```

**Wiederherstellen.** Konfigurations-JSON über die Oberfläche importieren, oder
für ein vollständiges Restore:

```bash
docker compose up -d db
docker compose exec -T db psql -U minepower -d minepower -c "SELECT timescaledb_pre_restore();"
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U minepower -d minepower < backup.sql
docker compose exec -T db psql -U minepower -d minepower -c "SELECT timescaledb_post_restore();"
docker compose up -d app
```

Die App zuletzt starten: Sie legt beim Start nur dann Standardwerte an, wenn
die Datenbank leer ist.

**Stromausfall.** Ein harter Stromausfall kann die Postgres-Datendatei so
beschädigen, dass sie sich nicht mehr reparieren lässt — das ist keine
MinePower-Eigenheit, sondern trifft jede Datenbank auf Speicher, der `fsync`
nicht ehrlich beantwortet (bei Consumer-SSDs und USB-Datenträgern ohne
Power-Loss-Protection die Regel, nicht die Ausnahme). Für genau diesen Fall
schreibt die App alle sechs Stunden automatisch eine Sicherung von Geräten,
Einstellungen und Konten auf ein **eigenes** Volume
(`backup_data:/app/backups`, unabhängig von `db_data`). Findet sie beim
Start eine leere Datenbank vor, obwohl eine solche Sicherung existiert,
spielt sie diese automatisch zurück — keine manuelle Wiederherstellung
nötig. Bleibt die Postgres-Datendatei nach dem Ausfall komplett
unbrauchbar (der `db`-Dienst startet gar nicht erst), das Datenverzeichnis
löschen und den Stack neu hochfahren: Die App füllt sich beim nächsten
Start selbst wieder aus der letzten lokalen Sicherung.

Für maximale Widerstandsfähigkeit `backup_data` per Bind-Mount auf eine
**andere** physische Platte legen als `db_data` — beide auf demselben
Datenträger schützen nicht vor einem kompletten Laufwerksausfall. Der
Status (Zeitpunkt und Anzahl der Sicherungen) steht unter
`/api/system/backup-status`. Ein Stromausfall ist trotzdem kein Ersatz für
eine USV: Wo möglich, eine mit automatischem, sauberem Container-Shutdown
verwenden — das vermeidet den harten Kill überhaupt.

**Logs:** `docker compose logs -f app` (Log-Level über `LOG_LEVEL`).

**Diagnose.** Die Seite Ereignisse → Diagnose zeigt Geräte-Rohwerte, die
aktuelle Regelgüte und jede Regel-Entscheidung live. Ein Diagnosebericht (JSON)
lässt sich dort zeitraumbezogen exportieren.

### Abweichende Aufbauten

**Unraid.** Statt der benannten Volumes Bind-Mounts auf den Cache-Pool
verwenden — in `docker-compose.yml` bei den Diensten `db` und `app`:

```yaml
    volumes:
      - /mnt/cache/appdata/minepower-db:/var/lib/postgresql/data     # bei db
      - /mnt/cache/appdata/minepower-backups:/app/backups            # bei app
```

Die Sicherung idealerweise auf einen anderen Datenträger als die
Datenbank legen (siehe „Stromausfall" oben) — auf dem Cache-Pool reicht
dafür z. B. ein zweiter, unabhängiger Pfad, sofern der Pool aus mehreren
Platten besteht.

**OCPP-Wallboxen.** Den Treiber-Port zusätzlich freigeben — beim Dienst `app`:

```yaml
    ports:
      - "${HTTP_PORT:-8080}:8000"
      - "8887:8887"
```

Die Wallbox wird dann auf `ws://<server-ip>:8887/<charge-point-id>` als
Backend eingerichtet.

**Headless-Konfiguration.** Eine JSON-Datei einhängen und `HEADLESS_CONFIG`
setzen — beim Dienst `app`:

```yaml
    volumes:
      - ./minepower.json:/config/minepower.json:ro
```

Einzelheiten unter [docs/headless.md](docs/headless.md).

**Produktivbetrieb.** `.env.example` nach `.env` kopieren und ein eigenes
`POSTGRES_PASSWORD` setzen.

## Entwicklung

Eigene Änderungen für den Eigenbedarf sind erlaubt; nur die Weitergabe ist es
nicht (siehe [Lizenz](LICENSE)).

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
hardwareunabhängige Klassenhierarchie implementiert und über eine injizierte
Uhr vollständig testbar — ohne Datenbank, ohne Netzwerk, ohne Wartezeit.

Weiterführende Dokumentation:

- [Architektur-Übersicht](docs/architecture.md)
- [Neuen Treiber hinzufügen](docs/adding-drivers.md)
- [Headless-Konfiguration](docs/headless.md)
- [Installation auf Unraid](docs/unraid.md)

## Sicherheit

- Authentifizierung über JWT mit bcrypt-gehashten Passwörtern. Rollen
  (Administrator, Nutzer, Nur-Lesen) werden bis in die API durchgesetzt, der
  Login ist rate-limitiert.
- Ein einzelner Geräte-Timeout bringt den Regelkreis nicht zum Absturz. Nach
  drei aufeinanderfolgenden Fehlversuchen gilt ein Gerät als offline; die
  Verbindung wird automatisch neu aufgebaut, sobald es wieder antwortet.
- **Ohne verlässliche Netzmessung werden alle steuerbaren Lasten gestoppt.**
  Es wird nie ohne Netzmessung geladen oder geheizt.
- Zugangsdaten werden über Umgebungsvariablen bzw. `.env` konfiguriert. Der
  Konfigurations-Export enthält Geräte-Zugangsdaten im Klartext und sollte
  entsprechend sicher aufbewahrt werden. Der *Diagnosebericht* schwärzt als
  Passwort deklarierte Felder — vor dem Weitergeben trotzdem durchsehen.

## Bekannte Einschränkungen

- Der OCPP-Treiber deckt die für Überschussladen relevante Teilmenge von
  OCPP 1.6J ab (SmartCharging-Profil). Wallboxen ohne SmartCharging lassen
  sich nur ein- und ausschalten, nicht stufenlos regeln. OCPP 2.0.1 fehlt.
- Der überwiegende Teil der Geräteanbindungen ist nie an echter Hardware
  gelaufen (siehe [Gerätetabelle](#unterstützte-geräte)).
- Statistik-Export als CSV; ein PDF-Bericht ist nicht enthalten.
- Die Solarprognose stützt sich auf einen externen Dienst. Fällt er aus,
  regelt MinePower ohne Prognose weiter — die Halte-Logik entfällt dann.

## Lizenz und Rechtliches

Copyright © 2026 Marwin Rentz. Alle Rechte vorbehalten.

Der Quelltext ist einsehbar, MinePower ist aber **nicht Open Source**:

- **Erlaubt:** Herunterladen, Forken, Verändern und Betreiben für eigene
  Zwecke — privat wie im eigenen Gewerbe, auch aus dem offiziellen
  Docker-Image.
- **Nicht erlaubt:** Weitergabe an Dritte, im Original wie verändert. Also
  Veröffentlichen, Verkaufen, Vermieten, Unterlizenzieren oder als
  Dienstleistung bereitstellen.

Vollständiger Text: [LICENSE](LICENSE). Fremdkomponenten und ihre Lizenzen:
[NOTICE](NOTICE).

**Rückmeldungen sind erwünscht.** Fehlerberichte, Messwerte und
Verbesserungsvorschläge gern über die
[Issues](https://github.com/Marwinrentz/MinePower-Controller/issues). Wer eine
Rückmeldung einreicht, räumt dem Rechteinhaber das Recht ein, sie ohne
Vergütung zu verwenden. Bitte keine Zugangsdaten oder Token mitschicken.

**Marken.** Tesla, Sungrow, my-PV, Fronius, SMA, Huawei, Shelly, go-e, KEBA,
Tibber, aWATTar und alle weiteren genannten Bezeichnungen sind Marken ihrer
jeweiligen Inhaber. Sie werden ausschließlich beschreibend verwendet, um
anzugeben, mit welchen Geräten die Software zusammenarbeitet. Es besteht keine
Verbindung zu, Partnerschaft mit oder Billigung durch diese Unternehmen.

**Fremdschnittstellen.** Wer die Tesla Fleet API, Tibber, aWATTar oder
forecast.solar nutzt, schließt dafür ein eigenes Nutzungsverhältnis mit dem
jeweiligen Anbieter und ist an dessen Bedingungen gebunden. MinePower enthält
keine Zugangsdaten; diese bringt die betreibende Person selbst mit.

**Haftung.** Die Software steuert elektrische Verbraucher und wird ohne
Gewährleistung bereitgestellt. Installation und Absicherung elektrischer
Komponenten gehören in die Hände von Fachpersonal.

---

<a name="english"></a>

# English

Surplus-based charge control for electric vehicles, water heating and home
batteries. MinePower reads grid power and distributes the available PV surplus
across the controllable loads in the house along a freely sortable priority
chain. The goal is grid feed-in as close to 0 W as possible, without
sacrificing comfort or plant safety.

The application runs as a Docker stack (FastAPI backend, React frontend,
TimescaleDB). No cloud connection and no smart-home hub are required. Devices
are integrated through a plugin architecture; a new driver is a single Python
file with no changes to the core.

> **On licensing.** The source is publicly viewable, but MinePower is **not
> Open Source**: forking, modifying and running it for your own purposes is
> permitted, passing it on to third parties is not. Feedback is expressly
> welcome — see [License and legal](#license-and-legal).

## Contents

- [How the control loop works](#how-the-control-loop-works)
- [Supported devices](#supported-devices)
- [Installation](#installation-1)
- [Tesla integration](#tesla-integration)
- [Operation](#operation)
- [Development](#development)
- [Security](#security)
- [Known limitations](#known-limitations)
- [License and legal](#license-and-legal)

## How the control loop works

The control loop runs at a fixed interval and calculates, on every pass, how
much power is available to the controllable loads in total:

```
budget = Σ(power of all controllable loads) − grid power (smoothed) − setpoint
```

Within a deadband around the setpoint nothing is adjusted, to avoid chatter
from measurement noise. The deadband is adaptive: with stable generation the
loop regulates more tightly, under rapidly changing clouds more loosely, to
avoid needless start/stop cycles.

### The home battery comes first

Neither the battery's charging nor its discharging power counts as surplus:

```
pool for car and water heating = budget − |battery power|
```

This matters more than it looks. The budget formula only knows the grid
connection point, and there a battery that is covering the shortfall looks
exactly like a balanced house — without this deduction, car and water heating
would drain the battery while the display reports surplus. Conversely,
whatever the battery is currently charging with belongs to the battery and is
not offered to the loads. The household itself is unaffected: only the
*controllable* loads are held back.

Batteries are not actively commanded. Hybrid inverters already regulate their
battery to zero feed-in themselves; MinePower only reads state of charge and
power. Forced charging and reserve-SoC overrides remain an opt-in extra for
special cases.

### Ampere control based on measurement

A wallbox setpoint current is not computed from the nominal 230 V per phase,
but from the ratio that actually results from the measured power and the
commanded current. Real grid voltage, phase count and charging losses are
therefore contained in the conversion factor. A rigidly computed setpoint is
systematically off, and produces precisely what should be avoided: feed-in, or
draw from the battery. Increases are limited per step, because a vehicle only
follows a new setpoint after several seconds; decreases are not limited.

### Three mechanisms against wasted surplus

**Gap filler.** A wallbox can only charge in whole ampere steps; the rounded-off
remainder would otherwise go to the grid. If a continuously modulating load can
absorb it — such as a my-PV heating element — it gets it, regardless of its
position in the priority chain. The alternative is not "later" but "wasted".

**Honest quantisation.** The controller always rounds the charging current
down, never up, and reports the power actually drawn. Only then does the
control loop know what is really left over.

**Active forecast.** During a brief cloud passage with foreseeable recovery, an
ongoing charge is held rather than stopped — a restart costs more surplus via
the hysteresis than holding does. If the forecast sun is not enough to meet a
target-charge deadline, grid charging starts earlier.

How well this works is measurable: every cycle records how much surplus was fed
into the grid above the setpoint without any load absorbing it — including a
plain-language reason.

### Price-optimized charging locks the home battery

Wallbox and heating element both offer a **Price optimized** mode: while the
market price is below the cheap threshold from the settings, they charge from
the grid; above it they fall back to surplus-only charging. Sunshine is not
wasted just because the exchange is expensive.

The decisive part is **where** the power comes from during that hour. A hybrid
inverter knows nothing about electricity prices — it sees a new load at the
grid point and covers it from the battery. Without a countermeasure the car is
therefore not charged with cheap grid power but with your own, expensively
stored energy: charged once, discharged once, efficiency lost twice, and by
evening the house has no reserve left.

For the duration of the cheap window the battery is therefore locked — either
**Lock** (neither charge nor discharge) or **Fill up** (charge from the cheap
grid, never discharge). Both via `set_mode`, falling back to a reserve-SoC of
100 % so inverters without a mode register can join in. When the window closes
the battery returns to automatic; so it does when MinePower shuts down — no
inverter is left locked behind.

If the battery cannot be controlled at all (monitoring only) and is currently
discharging, grid charging is **skipped** rather than draining it, with a
plain-language reason on the dashboard. If you want it anyway — say because the
inverter is already set to never discharge — enable
`price_allow_battery_drain` on the device.

## Supported devices

The last column is the important one. **Only four integrations have ever run
against real hardware.** All others were written from vendor documentation and
community sources and have never talked to a physical device. Register maps
regularly deviate in practice — in offsets, scaling and sign. Such drivers are
marked *Experimental* in the user interface and should be cross-checked against
the actually read values via "Test connection" before productive use.

| Device | Category | Protocol | Verified on real hardware |
|---|---|---|---|
| Sungrow SH series | Hybrid inverter, incl. battery monitoring | Modbus TCP | **Yes** — SH-RT, continuous use |
| Sungrow DTSU666 (on SH) | Grid meter | Modbus TCP via inverter | **Yes** — continuous use |
| my-PV AC·THOR / ELWA-E / ELWA 2 | Water heating, continuously modulating | HTTP or Modbus TCP | **Yes** — AC·THOR, Modbus TCP |
| Tesla (any model) | Vehicle as charge point | BLE proxy or Fleet API | **Yes** — via BLE proxy |
| Sungrow SG series | Inverter | Modbus TCP (WiNet-S) | No |
| Sungrow DTSU666 (on SG) | Grid meter | Modbus TCP via inverter | No |
| Sungrow SBR/SBH | Battery, optionally controllable | Modbus TCP | No |
| Sungrow AC007/AC011E | Wallbox | Modbus TCP | No |
| Fronius GEN24 (Plus) | Inverter, incl. battery monitoring | Solar API | No |
| Fronius Smart Meter | Grid meter | Solar API | No |
| SMA Sunny Boy / Tripower | Inverter, incl. storage monitoring | Modbus TCP | No |
| SMA Energy Meter / Home Manager | Grid meter | Modbus TCP | No |
| Kostal Plenticore, GoodWe ET/EH, SolarEdge, Delta, SMA Tripower X | Inverter | SunSpec / Modbus TCP | No |
| SunSpec meters | Grid meter | SunSpec / Modbus TCP | No |
| Huawei SUN2000 + LUNA2000 | Hybrid inverter | Modbus TCP | No |
| Generic Modbus meter (e.g. Eastron SDM630) | Grid meter | Modbus TCP, freely configurable | No |
| Shelly 3EM / Pro 3EM | Grid meter | HTTP | No |
| Shelly relay | Water heating (on/off) | HTTP | No |
| go-e Charger | Wallbox | HTTP v2 | No |
| Generic wallbox | Wallbox | OCPP 1.6J | No |
| KEBA KeContact P30 (c/x series) | Wallbox | UDP 7090 | No |
| Victron GX (Cerbo, Venus) | Inverter with grid and battery (read only) | Modbus TCP | No |
| Generic Modbus inverter / battery | Inverter, battery (read only) | Modbus TCP, register profile | No |
| HTTP/JSON (any source) | Grid meter, inverter, battery (read only), switch | HTTP | No |
| MQTT (any topics) | Grid meter, inverter, battery (read only), switch | MQTT | No |
| Home Assistant | Grid meter, inverter, battery (read only), switch, wallbox | REST API | No |
| Tasmota | Grid meter (SML reader), switch | HTTP | No |
| Heat pump with SG-Ready | Water heating (switch-on recommendation) | Shelly Gen2+ or Home Assistant | No |
| cFos Power Brain | Wallbox | Modbus TCP | No |
| Alfen Eve Single/Double Pro-line | Wallbox | Modbus TCP | No |
| Amperfied connect.home/business/solar | Wallbox | Modbus TCP | No |
| Heidelberg Energy Control | Wallbox | Modbus RTU via TCP gateway | No |
| Webasto Next, Ampure Unite, Vestel EVC04 | Wallbox | Modbus TCP | No |
| Bender CC612/CC613 (Mennekes Amtron Xtra/Premium/4You, Walther, Ubitricity) | Wallbox | Modbus TCP | No |
| Mennekes Amtron Compact 2.0s | Wallbox | Modbus RTU via TCP gateway | No |
| Phoenix Contact CHARX SEC, EV-CC/EV-ETH (Wallbe, ESL Walli) | Wallbox | Modbus TCP | No |
| KEBA KeContact P30 x / P40 | Wallbox | Modbus TCP | No |
| ABL eMH1 | Wallbox | Modbus ASCII via TCP gateway | No |
| EM2GO Home, innogy/E.ON eBox, Siemens VersiCharge, SolaX EVC, Peblar, Pulsares | Wallbox | Modbus TCP | No |
| KSE wallbox, OBO Bettermann | Wallbox | Modbus RTU via TCP gateway | No |
| Hardy Barth eCB1 and Salia | Wallbox | HTTP | No |
| SMA EV Charger 7.4/22 | Wallbox | HTTP | No |
| EVSE-WiFi / SimpleEVSE | Wallbox | HTTP | No |
| openWB Pro | Wallbox | HTTP | No |
| openWB 1.9 'charge point only' | Wallbox | MQTT | No |
| Fronius Wattpilot Home/Go | Wallbox | WebSocket | No |
| Easee, Zaptec | Wallbox | Cloud API | No |
| Generic wallbox | Wallbox | HTTP, MQTT | No |

Simulated devices are also included for demo mode, so the interface and the
control loop can be explored without any hardware.

Field reports on untested devices are welcome — a diagnostic report from a
running installation says more than any description of the symptoms.

## Installation

Requires Docker with the Compose plugin, amd64 or arm64. No `.env` file is
needed; the JWT secret is generated on first start and persisted in the
database.

```bash
git clone https://github.com/Marwinrentz/MinePower-Controller.git minepower
cd minepower
docker compose up -d --build
```

Then open `http://localhost:8080`. The administrator account is created on
first access, after which the setup wizard guides through device
configuration. Alternatively, "Start demo mode" creates a fully simulated
installation.

To use the published image instead of building locally:

```bash
docker pull marwinrentz1/minepower:latest
```

| Service | Port | Purpose |
|---|---|---|
| `app` | `${HTTP_PORT:-8080}` → 8000 | Web interface, REST API, WebSocket; volume `backup_data` (backups) |
| `db` | internal 5432 | TimescaleDB, volume `db_data` |
| `mqtt` (optional) | 1883 | `docker compose --profile mqtt up -d` |

A ready-made Compose file for Unraid is in [docs/unraid.md](docs/unraid.md).

## Tesla integration

Tesla vehicles are controlled directly as a charge point, independently of the
wallbox in use. Two routes are available:

- **BLE proxy (cloud-free).** Requires an already running
  [TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy), for example
  on a Raspberry Pi within Bluetooth range. MinePower only stores its address
  and the vehicle VIN; key pairing happens in the proxy itself. A reference
  Compose file is in
  [examples/tesla-ble-proxy](examples/tesla-ble-proxy/docker-compose.yml) and
  is deliberately not part of the main stack.
- **Fleet API (cloud).** Requires an OAuth token from Tesla, but reliably
  provides state of charge. Polling is deliberately conservative to stay within
  rate limits.

In both cases the operator supplies the credentials; MinePower contains none.
Using the Fleet API means entering your own relationship with Tesla — see
[License and legal](#license-and-legal).

## Operation

**Updating**

```bash
git pull
docker compose up -d --build
```

**Backup.** For configuration only, the JSON export under Settings → Backup is
enough. For a full backup including the measurement history:

```bash
docker compose exec db pg_dump -U minepower -d minepower --no-owner --no-privileges > backup.sql
```

**Restore.** Import the configuration JSON through the interface, or for a full
restore:

```bash
docker compose up -d db
docker compose exec -T db psql -U minepower -d minepower -c "SELECT timescaledb_pre_restore();"
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U minepower -d minepower < backup.sql
docker compose exec -T db psql -U minepower -d minepower -c "SELECT timescaledb_post_restore();"
docker compose up -d app
```

Start the app last: it only seeds defaults when the database is empty.

**Power outages.** A hard power cut can damage the Postgres data file beyond
repair — that's not a MinePower quirk, it hits any database on storage that
doesn't answer `fsync` honestly (the norm, not the exception, for consumer
SSDs and USB drives without power-loss protection). For exactly this case,
the app writes an automatic backup of devices, settings and accounts to a
**separate** volume every six hours (`backup_data:/app/backups`, independent
of `db_data`). If it finds an empty database at startup even though such a
backup exists, it restores it automatically — no manual step needed. If the
Postgres data file is left completely unusable after the outage (the `db`
service refuses to start at all), delete its data directory and bring the
stack back up: the app repopulates itself from the last local backup on the
next start.

For maximum resilience, bind-mount `backup_data` onto a **different**
physical disk than `db_data` — both on the same drive don't protect against
a full drive failure. Status (timestamp and count of backups) is available
at `/api/system/backup-status`. A power outage is still no substitute for a
UPS: where possible, use one with an automatic, clean container shutdown —
that avoids the hard kill in the first place.

**Logs:** `docker compose logs -f app` (log level via `LOG_LEVEL`).

**Diagnostics.** Events → Diagnostics shows raw device values, the current
regulation quality and every control decision live. A diagnostic report (JSON)
can be exported there for a chosen period.

### Alternative setups

**Unraid.** Use bind mounts onto the cache pool instead of the named volumes —
in `docker-compose.yml` under services `db` and `app`:

```yaml
    volumes:
      - /mnt/cache/appdata/minepower-db:/var/lib/postgresql/data     # under db
      - /mnt/cache/appdata/minepower-backups:/app/backups            # under app
```

Ideally put the backup on a different disk than the database (see "Power
outages" above) — on the cache pool, a second, independent path is enough
if the pool spans more than one drive.

**OCPP wallboxes.** Expose the driver port as well — under service `app`:

```yaml
    ports:
      - "${HTTP_PORT:-8080}:8000"
      - "8887:8887"
```

The wallbox is then configured with `ws://<server-ip>:8887/<charge-point-id>`
as its backend.

**Headless configuration.** Mount a JSON file and set `HEADLESS_CONFIG` — under
service `app`:

```yaml
    volumes:
      - ./minepower.json:/config/minepower.json:ro
```

Details in [docs/headless.md](docs/headless.md).

**Production.** Copy `.env.example` to `.env` and set your own
`POSTGRES_PASSWORD`.

## Development

Your own changes for your own use are permitted; only passing them on is not
(see [LICENSE](LICENSE)).

```bash
# Backend (Python 3.12)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload      # requires a running database
python -m pytest                    # regulation and driver tests, no hardware

# Frontend
cd frontend
npm install
npm run dev                         # dev server proxying to localhost:8000
```

The regulation logic (`backend/app/core/regulation.py`) is implemented as a
pure, hardware-independent class hierarchy and is fully testable through an
injected clock — no database, no network, no waiting.

Further documentation:

- [Architecture overview](docs/architecture.md)
- [Adding a new driver](docs/adding-drivers.md)
- [Headless configuration](docs/headless.md)
- [Installing on Unraid](docs/unraid.md)

## Security

- Authentication via JWT with bcrypt-hashed passwords. Roles (administrator,
  user, read-only) are enforced all the way into the API, and login is rate
  limited.
- A single device timeout never crashes the control loop. After three
  consecutive failures a device counts as offline; the connection is
  re-established automatically once it responds again.
- **Without a reliable grid measurement, all controllable loads are stopped.**
  Charging or heating never happens without grid metering.
- Credentials are configured via environment variables or `.env`. The
  configuration export contains device credentials in clear text and should be
  stored accordingly. The *diagnostic report* redacts fields declared as
  passwords — still review it before sharing.

## Known limitations

- The OCPP driver covers the subset of OCPP 1.6J relevant for surplus charging
  (SmartCharging profile). Wallboxes without SmartCharging can only be switched
  on and off, not modulated. OCPP 2.0.1 is not implemented.
- Most device integrations have never run against real hardware (see
  [supported devices](#supported-devices)).
- Statistics export is CSV; there is no PDF report.
- The solar forecast relies on an external service. If it is unavailable,
  MinePower keeps regulating without it — the hold logic is then inactive.

## License and legal

Copyright © 2026 Marwin Rentz. All rights reserved.

The source is publicly viewable, but MinePower is **not Open Source**:

- **Permitted:** downloading, forking, modifying and running it for your own
  purposes — private or for your own business, including from the official
  Docker image.
- **Not permitted:** passing it on to third parties, original or modified.
  That is: publishing, selling, renting, sublicensing, or providing it as a
  service.

Full text: [LICENSE](LICENSE). Third-party components and their licenses:
[NOTICE](NOTICE).

**Feedback is welcome.** Bug reports, measurements and suggestions via
[Issues](https://github.com/Marwinrentz/MinePower-Controller/issues). By
submitting feedback you grant the copyright holder the right to use it without
compensation. Please do not include credentials or tokens.

**Trademarks.** Tesla, Sungrow, my-PV, Fronius, SMA, Huawei, Shelly, go-e,
KEBA, Tibber, aWATTar and all other names mentioned are trademarks of their
respective owners, used purely descriptively to indicate which devices the
software interoperates with. There is no affiliation with, partnership with, or
endorsement by these companies.

**Third-party interfaces.** Anyone using the Tesla Fleet API, Tibber, aWATTar
or forecast.solar enters into their own relationship with that provider under
its terms. MinePower contains no credentials; the operator supplies them.

**Liability.** The software controls electrical loads and is provided without
warranty. Installation and protection of electrical components belongs in the
hands of qualified personnel.
