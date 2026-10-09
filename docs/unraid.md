# Installation auf Unraid (Plug & Play)

## Voraussetzung (einmalig)

**Apps (Community Applications)** → Plugin **„Docker Compose Manager"**
installieren. Danach gibt es im Docker-Tab unten einen „Compose"-Bereich.

## Weg A – fertiges Image von Docker Hub (empfohlen, kein Build)

Das offizielle Image liegt öffentlich auf Docker Hub:
**`marwinrentz1/minepower:latest`** – kein Login, kein Build, kein Quellcode
auf dem Server nötig.

1. Docker-Tab → **Compose** → **Add New Stack** → Name `minepower`.
2. Stack bearbeiten und **dieses Compose einfügen**:

```yaml
services:
  db:
    image: timescale/timescaledb:latest-pg16
    restart: unless-stopped
    environment:
      POSTGRES_USER: minepower
      POSTGRES_PASSWORD: minepower
      POSTGRES_DB: minepower
      # Nur bei einer frischen Datendatei wirksam: Prüfsummen auf jeder
      # Seite, damit eine Beschädigung nach einem Stromausfall als klarer
      # Fehler auffällt statt als stille Dateninkonsistenz.
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - /mnt/cache/appdata/minepower/db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U minepower -d minepower"]
      interval: 10s
      timeout: 5s
      retries: 10

  app:
    image: marwinrentz1/minepower:latest
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8080:8000"      # Web-GUI-Port bei Bedarf anpassen
      # - "8887:8887"    # nur für OCPP-Wallboxen
    environment:
      DATABASE_URL: postgresql+asyncpg://minepower:minepower@db:5432/minepower
      # DEMO_MODE: "true"   # zum Ausprobieren ohne Hardware
    volumes:
      # Automatische lokale Sicherung (alle 6 h: Geräte, Einstellungen,
      # Konten) – idealerweise auf einem ANDEREN Datenträger als die
      # Datenbank oben, siehe „Stromausfall" unten.
      - /mnt/cache/appdata/minepower/backups:/app/backups
```

3. **Compose Up** klicken. Fertig → `http://<unraid-ip>:8080` öffnen,
   Admin-Konto anlegen, Assistent durchlaufen (oder Demo-Modus starten).

Kein Secret-Gefrickel nötig: Das JWT-Secret erzeugt sich beim Erststart
selbst und wird in der DB persistiert. Die Datenbank ist nur intern im
Stack-Netzwerk erreichbar (kein veröffentlichter Port), daher ist das
Default-Passwort vertretbar – wer mag, ändert es an beiden Stellen.

**Update:** Docker-Tab → Stack → **Compose Down**, dann
`docker compose pull` (oder „Update Stack") und **Compose Up** – bzw. im
Terminal: `docker compose -p minepower pull && docker compose -p minepower up -d`.

## Weg B – ohne GitHub (lokaler Build auf dem Server)

1. Projektordner per SMB nach `\\<unraid>\appdata\minepower` kopieren
   (oder im Unraid-Terminal `git clone` nach `/mnt/user/appdata/minepower`).
2. Terminal (`>_` oben rechts):

```bash
cd /mnt/user/appdata/minepower
docker compose up -d --build
```

Das mitgelieferte `docker-compose.yml` ist ebenfalls Plug & Play (keine
`.env` nötig). Der erste Build dauert ein paar Minuten. Auch hier gilt der
Unraid-Tipp aus dem Compose-Kommentar: DB-Volume auf den Cache-Pool binden
(`/mnt/cache/appdata/...`), nicht auf `/mnt/user/...` (FUSE + PostgreSQL
vertragen sich schlecht).

## Hinweise

- **Autostart:** Die Container haben `restart: unless-stopped` und kommen
  nach einem Neustart automatisch wieder hoch; im Compose Manager lässt
  sich der Stack zusätzlich auf Autostart stellen.
- **Backup:** Der DB-Bind-Mount unter `appdata` wird vom üblichen
  CA-Appdata-Backup miterfasst; zusätzlich gibt es den Config-Export im
  GUI (Einstellungen → Sicherung).
- **Stromausfall:** Die App schreibt zusätzlich alle 6 h selbst eine
  Sicherung nach `/app/backups` und spielt sie automatisch zurück, wenn sie
  beim Start eine leere Datenbank vorfindet – der typische Zustand, nachdem
  eine durch einen Stromausfall beschädigte Postgres-Datendatei gelöscht und
  neu angelegt werden musste. Läuft `db` nach einem Ausfall gar nicht mehr
  an: Datenverzeichnis unter `appdata/minepower/db` löschen, Stack neu
  starten – die App füllt sich selbst wieder auf. Details siehe README unter
  „Betrieb → Stromausfall".
- **Logs:** `docker logs -f minepower-app-1` bzw. im Docker-Tab.
- **Tesla BLE-Proxy:** läuft weiterhin extern (z. B. auf einem Pi am
  Stellplatz) – nicht Teil dieses Stacks; in der App nur die Proxy-URL
  eintragen.
