# ☀️ MinePower – PV-Überschuss-Ladecontroller

Intelligente Steuerung von **E-Auto-Ladung, Warmwasser und Hausbatterie**
auf Basis von PV-Überschuss. Herstellerübergreifend, ohne Hub-Zwang
(kein Home Assistant / ioBroker nötig), ausgeliefert als Docker-Stack.

**Regelziel:** Netzeinspeisung gegen 0 W – Überschuss geht nach frei
sortierbarer Prioritätskette in Batterie, Auto und Warmwasser statt ins Netz.

## Features

- **Geschlossener Regelkreis** (2–5 s): Totband, gleitende Mittelung,
  Start-/Stopp-Hysterese, Min-Ladestrom-Handling (≥ 6 A), automatische
  **1↔3-Phasenumschaltung** mit Sperrzeit, Hausanschluss-Schutz.
- **Kein Watt verschenken** – drei Mechanismen, die messbar wirken:
  - **Lückenfüller:** Rest-Überschuss, der für die nächste Last in der Kette
    zu klein ist (z. B. 180 W vs. 6 A Mindeststrom), geht an eine stufenlos
    modulierbare Last (Warmwasser) – unabhängig von deren Platz in der Kette.
  - **Adaptives Totband:** bei stabiler Sonne enger regeln (weniger
    verschenken), bei Wolkenwechsel weiter (weniger Start/Stopp-Zyklen).
  - **Kennzahl „verschenkter Solarstrom“** live im Dashboard – inklusive
    Klartext-Begründung, warum der Überschuss liegen blieb – und historisch
    in der Statistik (kWh, Anteil, Gegenwert in €).
- **Solarprognose fließt in Entscheidungen ein**, nicht nur in die Anzeige:
  Bei kurzem Wolkendurchzug wird eine laufende Ladung gehalten statt
  abgeschaltet; Zielladungen beginnen früher, wenn die Sonne bis zur
  Deadline absehbar nicht reicht.
- **Prioritätskette per Drag & Drop** (Batterie → Auto → Warmwasser …),
  Batterie-Reserve, EV-Batteriesperre.
- **Lademodi:** Nur-PV · Min+PV · Sofort · Zeitplan · **Zielladung mit
  Deadline** · **Preisoptimiert** (Tibber / aWATTar).
- **Batterie bleibt passiv:** Hybrid-Wechselrichter regeln ihre Batterie
  selbst auf Nulleinspeisung. MinePower liest SoC und Leistung nur mit (ein
  separates Batteriegerät ist dafür nicht nötig); aktives Zwangsladen bleibt
  eine bewusst zu aktivierende Zusatzfunktion für Sonderfälle.
- **Treiber-Plugin-Architektur** – neue Geräte ohne Kern-Änderung
  ([Guide](docs/adding-drivers.md)). Jeder Treiber bringt Reconnect,
  Plausibilitätsprüfung, Klartext-Fehler und **Readback nach jedem
  Schreibbefehl** mit – „Befehl gesendet“ gilt nie als „Befehl ausgeführt“.
  Startset:
  - **Sungrow komplett:** SG- & SH-Serie (Modbus TCP via WiNet-S),
    SBR/SBH-Batterie inkl. Zwangsladen/Reserve, DTSU666-Zähler,
    AC007/AC011E-Wallbox – ohne Cloud/iSolarCloud.
  - **Tesla als Ladepunkt:** wahlweise **externer TeslaBleHttpProxy**
    (cloud-frei, läuft z. B. auf deinem Pi – wird *nicht* mitbetrieben)
    oder **Fleet-API** (Cloud, zuverlässiger SoC).
  - **SunSpec (herstellerübergreifend):** Kostal Plenticore, GoodWe ET/EH,
    SolarEdge, Delta, SMA Tripower X … Das Gerät wird beim Verbinden nach
    seinem Registeraufbau gefragt – unempfindlich gegen Firmware-Updates.
  - **Hybrid-Wechselrichter mit Batterie-Monitoring:** Fronius GEN24 (Plus),
    SMA Sunny Boy Storage, Huawei SUN2000 + LUNA2000 (experimentell).
  - go-e Charger (HTTP v2), **OCPP 1.6J** (generisch), Shelly 3EM/Pro 3EM,
    SMA Energy Meter / Home Manager, Fronius Smart Meter, generischer
    Modbus-Zähler (SDM630 …), my-PV AC•THOR / ELWA-E, Shelly-Relais-Heizstab.
- **Dashboard:** Systemzustand in einem Satz, animiertes
  Energiefluss-Diagramm (Richtung, Stärke und Tempo folgen der realen
  Leistung), weich zählende Messwerte, Live-Push per WebSocket, historische
  Charts (TimescaleDB). Störungen – Sicherheits-Stopp, offline-Geräte, nicht
  übernommene Befehle – stehen unübersehbar oben, nicht im Log.
- **Setup-Assistent** mit Tooltips an jedem Feld, „Verbindung testen“ mit
  allen Live-Werten im Klartext und optionalem **Steuerbefehl-Test samt
  Readback**; Export/Import als JSON; [Headless-Config](docs/headless.md).
- **Simulations-/Demo-Modus:** komplette Anlage ohne Hardware erlebbar.
- **Statistik:** Autarkie, Eigenverbrauch, Solaranteil je Ladung, Ersparnis,
  CO₂; CSV-Export. **Benachrichtigungen:** ntfy / Telegram / E-Mail.
- **Mehrbenutzer + Rollen** (Admin/Nutzer/Read-only), RFID-Zuordnung,
  Audit-Log, Diagnose-Seite mit Regelgüte-Kennzahlen, **i18n
  Deutsch/Englisch**, durchgängiges Design-System hell/dunkel/system,
  mobil mit Tab-Navigation voll bedienbar.
- Optional: **MQTT-Publish** inkl. Home-Assistant-Auto-Discovery.

## Quickstart (Plug & Play)

Voraussetzung: Docker + Docker Compose (amd64 oder arm64/Raspberry Pi).
**Keine `.env` nötig** – das JWT-Secret wird beim Erststart automatisch
erzeugt und persistiert, die DB ist nur intern erreichbar.

```bash
git clone <repo> minepower && cd minepower
docker compose up -d --build
```

→ **http://localhost:8080** öffnen. Beim Erststart legst du das
Admin-Konto an; danach führt der Assistent durch die Einrichtung
(oder: „Demo-Modus starten" für eine simulierte Anlage).

Noch einfacher ohne lokalen Build – **fertiges Image von Docker Hub**
(`marwinrentz1/minepower:latest`, multi-arch): einfach den Compose-Schnipsel
aus der [Unraid-Anleitung](docs/unraid.md) verwenden (gilt sinngemäß für
jede Docker-Plattform). Härten für Produktion: `.env.example` → `.env`
(eigenes `POSTGRES_PASSWORD` usw.).

| Service | Port | Zweck |
|---|---|---|
| app | `${HTTP_PORT:-8080}` → 8000 | Web-GUI + API + WebSocket (FastAPI serviert das Frontend) |
| db | intern 5432 | TimescaleDB (Volume `db_data`) |
| mqtt (optional) | 1883 | `docker compose --profile mqtt up -d` |

**Unraid:** Schritt-für-Schritt in [docs/unraid.md](docs/unraid.md).

**OCPP-Wallboxen:** Port des OCPP-Treibers (Default 8887) beim
`app`-Service freigeben: `ports: ["8887:8887"]` – Backend-URL in der
Wallbox: `ws://<server-ip>:8887/<charge-point-id>`.

## Tesla-Anbindung (optional)

- **Methode A – BLE-Proxy (empfohlen, cloud-frei):** Du betreibst
  [TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy) bereits
  (typisch: Raspberry Pi am Stellplatz)? Dann im Assistenten nur die
  Proxy-Adresse (`http://<pi-ip>:8080`) + VIN eintragen. Key-Enrollment
  passiert im Proxy, nicht in MinePower. Kein Pi vorhanden? Separates
  Referenz-Compose: [examples/tesla-ble-proxy](examples/tesla-ble-proxy/docker-compose.yml)
  (bewusst **nicht** Teil des Haupt-Stacks).
- **Methode B – Fleet-API (Cloud):** OAuth-Token + Fahrzeug-ID eintragen;
  liefert zuverlässig SoC, ist aber rate-limitiert (konservatives Polling
  ist eingebaut).

## Betrieb

- **Update:** `git pull && docker compose up -d --build`
- **Backup:** GUI → Einstellungen → Sicherung → Export (JSON).
  Komplett-Backup der Zeitreihen: `docker compose exec db pg_dump -U minepower minepower > backup.sql`
- **Restore:** Config-JSON importieren bzw. `psql < backup.sql` in eine frische DB.
- **Logs:** `docker compose logs -f backend` (Log-Level via `LOG_LEVEL`).
- **Diagnose:** GUI → Ereignisse → 🔬 zeigt Geräte-Rohwerte und jede
  Regel-Entscheidung live.
- **Multi-Arch-Build:** `docker buildx build --platform linux/amd64,linux/arm64 …`
  (Basis-Images sind multi-arch).

## Entwicklung

```bash
# Backend (Python 3.12)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload          # braucht laufende DB, siehe .env
python -m pytest                        # Regelungs-Tests (ohne Hardware/DB)

# Frontend
cd frontend && npm install && npm run dev   # Proxy → localhost:8000
```

- [Architektur-Übersicht](docs/architecture.md)
- [Neuen Treiber hinzufügen](docs/adding-drivers.md)
- [Headless-Konfiguration](docs/headless.md)

## Sicherheit & Robustheit

- JWT-Auth (bcrypt-Hashes), Login-Rate-Limit, Rollen bis in die API.
- Geräte-Timeouts crashen den Regelkreis nie; 3 Fehlversuche → offline,
  Auto-Reconnect, Benachrichtigung.
- **Fallback:** Ohne verlässliche Netzmessung werden alle steuerbaren
  Lasten pausiert – es wird nie blind geladen.
- Secrets nur via `.env`; Config-Export enthält Geräte-Zugangsdaten →
  sicher aufbewahren.

## Hinweise / Grenzen

- Treiber tragen einen **Reifegrad** (`stabil` / `Beta` / `experimentell`),
  der im Assistenten als Badge und Warnbanner erscheint. Registerkarten
  folgen den öffentlich dokumentierten Protokollen; einzelne Firmware-Stände
  können abweichen → „Verbindung testen“ zeigt Live-Werte zur Verifikation,
  und unplausible Werte melden sich von selbst statt still in die Regelung zu
  laufen.
  - **Beta:** Sungrow AC-Wallbox (Register-Basis wird beim Test automatisch
    gesucht), Sungrow-Batteriegerät, SMA Energy Meter, SunSpec, OCPP.
  - **Experimentell:** Huawei SUN2000/LUNA2000 – Registerkarte aus der
    Herstellerdoku, nicht an Hardware verifiziert.
- OCPP-Treiber: solide Teilmenge von OCPP 1.6J (SmartCharging-Profil);
  Boxen ohne SmartCharging lassen sich nur ein-/ausschalten – der
  Steuerbefehl-Test im Assistenten zeigt das sofort. OCPP 2.0.1 ist
  vorbereitet, aber noch nicht implementiert.
- PDF-Report: aktuell CSV-Export; PDF steht auf der Roadmap.

> „MinePower" ist ein Platzhalter-Produktname.
