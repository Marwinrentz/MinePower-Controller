# Audit-Protokoll 3.3.0

Auftrag: Security-Audit, Stabilität, Treiber-Verifikation und
Überarbeitung der Oberfläche. Je Phase: was gefunden, was geändert, was
offen. Ausgangsstand: Version 3.2.0 (Commit cec25a7), 2382 Backend-Tests.

Prüfumgebung: Backend-Tests mit SQLite, zusätzlich ein eigener Docker-Stack
(`mp-audit`, TimescaleDB wie im Betrieb) für alles, was SQLite nicht zeigt.
Nichts wurde an echten Geräten geprüft.

## Phase 1 – Security

Einzelheiten und Schweregrade: [SECURITY.md](SECURITY.md).

**Gefunden** (25 Befunde, 2 hoch, 11 mittel, 12 niedrig):

- Das Anmelde-Token stand in der WebSocket-Adresse und damit im
  Zugriffsprotokoll.
- Geräte ließen sich mit beliebigen Strom- und Leistungswerten anlegen und
  importieren; geprüft wurde nur beim Ändern.
- Keine Sicherheits-Kopfzeilen, CORS für alle Ursprünge, API-Dokumentation
  ohne Anmeldung.
- Tokens überlebten einen Passwortwechsel; kein Nutzer konnte sein eigenes
  Passwort ändern; der letzte Admin konnte sich aussperren.
- SMTP ohne Zertifikatsprüfung; Benachrichtigungen blockierten den
  Regelkreis bis zu 15 s und verschluckten Fehler.
- Der Import löschte Ladevorgänge und Zugangsdaten.
- Container lief als root; Abhängigkeiten nicht gepinnt; 7 Lücken laut
  `npm audit`.

**Geändert:**

- `security.py`: Token mit Ausstellzeit, Widerruf über
  `users.password_changed_at`, Login-Bremse je Konto und je Adresse mit
  begrenztem Speicher, gleiche Antwortzeit für unbekannte Konten.
- `api/auth.py`: eigenes Passwort ändern, Rollenprüfung, Schutz des letzten
  Admins, Setup einzeln, fehlgeschlagene Anmeldungen im Protokoll.
- `api/status.py`, `services/ws.py`: WebSocket-Anmeldung per erster
  Nachricht oder Kopfzeile, Obergrenze für Verbindungen, Sendezeitlimit.
- `main.py`: Kopfzeilen samt Content-Security-Policy, CORS nur auf Wunsch,
  API-Dokumentation abschaltbar.
- `core/logsafe.py` (neu): schwärzt Zugangsdaten in allen Protokollzeilen.
- `core/regulation.py`: Der Regler begrenzt jeden gespeicherten Strom-,
  Leistungs- und Temperaturwert selbst.
- `api/devices.py`, `api/system.py`: Einstellungen beim Anlegen und Import
  prüfen; Import aktualisiert an Ort und Stelle.
- `drivers/netguard.py` (neu): Metadatendienste als Geräteadresse gesperrt.
- `services/notifications.py`: Versand im Hintergrund, Zertifikatsprüfung,
  Ergebnis je Kanal, neuer Kanal Webhook.
- Oberfläche: Token nicht mehr in der Adresse, Farbschema-Skript als Datei
  (wegen der Content-Security-Policy), Einstellungen → Konto.
- iOS: Anmeldung per erster Nachricht, Rückfall für Server vor 3.3.0.
- Docker: Nutzer ohne Rechte, nur lesbares Dateisystem, Capabilities
  entfernt, Healthcheck im Image, exakte Versionen, Node 22.
- Abhängigkeiten: echarts 6, react-router 7, vite 8.
- Datenbank: Spalten `users.password_changed_at` und `users.prefs`
  (Alembic-Revision 0005; die Anwendung ergänzt sie beim Start selbst).

**Geprüft:**

- 2424 Backend-Tests grün, davon 42 neu (`tests/test_security.py`), unter
  anderem: Jeder Endpunkt unter `/api/` außer vier bewusst offenen verlangt
  eine Anmeldung.
- Am laufenden Container: Prozess mit UID 1000 ohne Capabilities,
  Dateisystem nur lesbar, vorhandene Schlüsseldatei von root wird übernommen;
  Kopfzeilen vorhanden; `/docs` aus; kein CORS; Pfad-Ausbruch liefert nur die
  Startseite; Login-Bremse greift nach 8 Versuchen; WebSocket-Anmeldung auf
  allen drei Wegen; das Token erscheint nicht im Protokoll.
- `pip-audit` und `npm audit` ohne Befund, `tsc` und Frontend-Build grün,
  131 Tests des iOS-Kits grün.

**Offen:** siehe SECURITY.md, Abschnitt „Offen“ (TLS, MQTT-Broker ohne
Anmeldung, OCPP ohne Anmeldung, Zwei-Faktor).

## Phase 2 – Datenbank-Robustheit

Ziel: Nach Stromausfall, hartem Abbruch oder verlorener Datenbank gehen
weder Konfiguration noch Verlauf verloren, und niemand muss neu einrichten.

**Gefunden:**

- Messwerte, die die Datenbank nicht annahm, wurden verworfen. Jeder
  Neustart der Datenbank hinterließ eine Lücke im Verlauf; beim Beenden
  fehlten bis zu 15 s.
- Das Schreiben lief im Takt des Regelkreises, ohne Zeitlimit. Eine
  Datenbank, die annimmt, aber nicht antwortet, hätte die Regelung angehalten.
- Auf PostgreSQL ohne TimescaleDB startete die Anwendung nicht: Der
  fehlgeschlagene Versuch, die Erweiterung zu laden, rollte das frisch
  angelegte Schema mit zurück. Bestätigt am bisherigen Image
  (`relation "settings" does not exist`).
- Schema-Änderungen liefen über `create_all` und eine Spaltenliste; Alembic
  lag im Repository, wurde aber nie ausgeführt. Keine Sicherung vor einer
  Änderung.
- Rohdaten wurden nach 90 Tagen gelöscht, ohne dass etwas Gröberes blieb –
  die Jahresansicht zeigte ein Quartal. Keine Kompression.
- Die Sicherung enthielt nur die Konfiguration. Verlauf, Ladevorgänge und
  Ereignisse waren nach dem Verlust der Datenbank weg. Wiederherstellen ging
  nur automatisch bei leerer Datenbank, nicht aus der Oberfläche.
- Die Energie-Auswertung multiplizierte die Summe aller Messwerte mit dem
  mittleren Abstand. Nach einem Ausfall zählte die Lücke als Zeit mit
  Durchschnittsleistung; bei Bezug und Einspeisung wurde der Abstand nur aus
  den Werten einer Richtung bestimmt.
- Eine Konfigurationsänderung, die genau in einen Datenbank-Aussetzer fiel,
  wurde nie geladen; der Takt fiel aus.
- Zwei Sicherungen in derselben Sekunde überschrieben einander (im Test für
  das Wiederherstellen gefunden).
- Starteten Anwendung und Datenbank gleichzeitig, brach der Start ab, solange
  PostgreSQL sein WAL zurückspielte.

**Geändert:**

- `services/measurements.py`: Fehlgeschlagene Pakete gehen in eine
  Spool-Datei im Datenvolume (mit `fsync`) und werden nachgeschrieben, sobald
  die Datenbank wieder da ist. `ON CONFLICT DO NOTHING` auf dem
  Primärschlüssel macht das wiederholbar. Schreiben im Hintergrund mit
  Zeitlimit; beim Beenden ein letzter Versuch.
- `services/audit.py`: Ereignisse aus einem Ausfall werden nachgetragen.
- `db.py`: Zeitlimits für Verbindungsaufbau (10 s) und Anfragen (60 s).
- `startup.py`: wartet auf die Datenbank; Alembic hebt das Schema beim Start
  (`alembic_version`), vorher Rohsicherung der Konfigurationstabellen;
  TimescaleDB-Schritte einzeln in Savepoints; Kompression der Rohdaten nach
  7 Tagen; Stundenaggregat `measurements_hourly` (Mittel, Minimum, Maximum,
  Anteile für Bezug und Einspeisung), das über die Aufbewahrung der Rohdaten
  hinaus bleibt.
- `api/history.py`, `api/statistics.py`: lange Zeiträume aus den
  Stundenwerten; Energie als Summe aus Wert mal Zeit bis zum nächsten Wert,
  höchstens 5 min.
- `services/backup.py`: tägliche Datensicherung (Stundenwerte, Ladevorgänge,
  Ereignisse; gzip, zeilenweise geschrieben) mit eigener Rotation;
  Wiederherstellen von Konfiguration und Verlauf; `fsync` auch auf das
  Verzeichnis; Warnung bei knappem Speicherplatz (unter 1 GB oder 5 %).
- `api/system.py`: `GET/POST /api/system/backups`,
  `POST …/{name}/restore`, `GET …/{name}/download`.
- Oberfläche: Einstellungen → Sicherung zeigt die Dateien, spielt sie ein,
  lädt den Verlauf herunter und nennt Speicherplatz und Datenbankgröße.
- `core/loop.py`: Laden der Konfiguration wird nach einem Fehler wiederholt,
  der Takt läuft mit dem bisherigen Stand weiter.
- `docker-compose.yml`: `fsync`, `synchronous_commit`, `full_page_writes`
  ausdrücklich an; 60 s für das saubere Stoppen der Datenbank.
- Neue Variablen: `BACKUP_DATA_INTERVAL_H` (24), `BACKUP_DATA_KEEP` (7),
  `RAW_RETENTION_DAYS` (90).

Migrationen ergänzen nur (neue Spalten, alle optional). Eine ältere
Programmversion läuft deshalb mit dem neueren Schema weiter; ist die
Datenbank neuer als das Programm, meldet der Start das und arbeitet weiter.

### Stromausfall-Versuche

Am Test-Stack (TimescaleDB pg16, Demo-Anlage, Takt 10 s), jeweils mit
`docker kill -s KILL`, also ohne Gelegenheit zum Aufräumen.

| Versuch | Ergebnis |
|---|---|
| Datenbank abgeschossen, 55 s später gestartet | Anwendung regelte weiter. 78 Messwerte lagen in der Spool-Datei und wurden 12 s nach dem Start nachgeschrieben. Abstand der Messwerte über den Ausfall hinweg: 10,00 s, keine Lücke, kein Duplikat. PostgreSQL: `redo done`. |
| Anwendung abgeschossen und neu gestartet | 5 Geräte, 1 Konto, 8 Einstellungsabschnitte unverändert. |
| Beide gleichzeitig abgeschossen (Stromausfall) | PostgreSQL: `database system was interrupted … redo done … ready`. Anwendung wartete und startete ohne Neustart-Schleife. Konfiguration vollständig, Schema-Stand 0005, größter Abstand im Verlauf 11 s, kein Duplikat. |
| Datenbank-Volume gelöscht | Beim Start automatisch eingespielt: 5 Geräte, Konto, Einstellungen, dazu 52 Stundenwerte und 11 Ereignisse aus der Datensicherung. Die Anmeldung mit dem bisherigen Token blieb gültig. |
| Reines PostgreSQL 16 ohne TimescaleDB | Startet, legt das Schema an, schreibt Messwerte. Das bisherige Image bricht hier ab. |
| Bestehende Datenbank ohne Alembic-Stand | Sicherung `pre-migration-base-to-0005-…json` geschrieben, Revisionen 0001–0005 gelaufen, Hypertable, Kompression und Stundenwerte eingerichtet. |

**Grenzen (nicht behebbar oder bewusst):**

- Was beim harten Abbruch der Anwendung noch im Speicher lag, ist weg –
  höchstens ein Schreibintervall (15 s). Bei einem Stromausfall misst in
  dieser Zeit ohnehin niemand.
- Die Zusage von PostgreSQL gilt nur, wenn der Datenträger `fsync` ehrlich
  beantwortet. USB-Sticks und manche SSDs ohne Stützkondensator tun das
  nicht; für diesen Fall gibt es die Sicherungen auf dem eigenen Volume.
- Nach dem Verlust der Datenbank kommt der Verlauf in Stundenauflösung
  zurück, nicht in Rohauflösung. Höchstens ein Tag fehlt (Abstand der
  Datensicherung, einstellbar).
- Die Spool-Datei fasst rund zehn Tage (256 MB). Danach werden neue Werte
  verworfen und gezählt.

**Geprüft:** 2447 Backend-Tests grün, davon 23 neu
(`tests/test_durability.py`): Ausfall mit Neustart der Anwendung
dazwischen, abgebrochenes Nachschreiben, abgeschnittene Spool- und
Sicherungsdateien, Rundlauf Sicherung → leere Datenbank → Wiederherstellung
mit neu vergebenen Geräte-IDs, Migration einer Datenbank im alten Stand.

**Offen:**

- Das Compose-Image der Datenbank ist `timescale/timescaledb:latest-pg16`.
  Auf eine feste Version zu pinnen wäre sauberer, kann aber bestehende
  Installationen brechen, deren Datenbank schon eine neuere
  Erweiterungsversion trägt. Empfehlung: bei Neuinstallationen pinnen.
- Eine Sicherung außer Haus (anderer Rechner, Cloud) gibt es nicht. Die
  Dateien im Volume `backup_data` lassen sich mit jedem Werkzeug abholen.
- Der Diagnosebereich zeigt den Zustand des Messwert-Puffers noch nicht
  (kommt in Phase 4).

## Phase 3 – Treiber

Einzelheiten, Quellen und Stand je Treiber: [DRIVERS.md](DRIVERS.md).

Vorgehen: Register, Datentypen, Skalierung, Vorzeichen und Befehle aus dem
Code gezogen und gegen Online-Quellen gehalten – Herstellerdokumente
(my-PV), Referenzimplementierungen (evcc), gepflegte Registerkarten
(Sungrow: mkaiser, SunGather) und die Quelltexte der Gegenstelle
(TeslaBleHttpProxy). Für die 20 Modbus-Wallbox-Profile lief ein
automatischer Abgleich der Registeradressen gegen die evcc-Quellen.

**Gefunden** (17 Fehler in 10 Treibern), die wichtigsten:

- Sungrow SH/Batterie: Batterieleistung seit Firmware Herbst 2024 mit
  Vorzeichen; vorzeichenlos gelesen wurden aus 3 kW Entladen 62 kW.
- Sungrow Zähler an der SG-Serie: Bezug und Einspeisung vertauscht.
- Sungrow Wallbox: Registerlayout nicht belegbar, Start und Stopp vertauscht.
- my-PV: Status 9 als Fehler angezeigt; Leistung des AC•THOR über HTTP
  nicht gelesen; AC ELWA-E über Modbus nicht lesbar.
- go-e: Freigabe nur „neutral“ statt „ein“; Phasenzahl aus der Vorgabe
  statt aus der Messung.
- Fronius: Batterie-Ladestand aus einem Feld gelesen, das es nicht gibt.
- Huawei: Speicherleistung aus dem Register des Ladestands gelesen.
- SMA: Batterieentladung erschien als Sonnenstrom.
- Tesla über BLE-Proxy: Wecken ging an einen Pfad, den der Proxy nicht hat.
- OCPP: Phasenleistungen überschrieben einander; Profil ohne Startzeit;
  jede Kennung durfte sich verbinden.

**Geändert:** alle Funde behoben (Liste in DRIVERS.md). Sungrow-Wallbox neu
geschrieben. OCPP nimmt nur eingerichtete Kennungen an und kennt ein
optionales Passwort.

**Geprüft:** 2476 Backend-Tests grün, davon 29 neu
(`tests/test_driver_protocols.py`): je Treiber nachgestellte
Geräteantworten für Adresse, Datentyp, Skalierung und Vorzeichen; OCPP mit
echter WebSocket-Verbindung auf localhost.

**Nicht verifiziert:**

- Nichts wurde an Hardware geprüft. Die Korrekturen stützen sich auf
  Dokumentation und Referenzimplementierungen.
- Modbus-Wallboxen: nur Registeradressen abgeglichen.
- HTTP-, MQTT- und Cloud-Wallboxen, SG-Ready, generische Treiber: nicht
  gegen Dokumentation geprüft.
- Zwei Annahmen sind als solche markiert: Summe der Leistungen beim
  AC•THOR 9s, Zwangsleistung der Sungrow-Batterie in Watt.

**Hinweis für bestehende Anlagen:** Die Korrekturen ändern Messwerte dort,
wo sie vorher falsch waren (Sungrow SG-Zähler, Huawei-Speicher, SMA mit
Speicher, Fronius-Ladestand, OCPP dreiphasig). go-e schaltet beim Start
jetzt ausdrücklich ein. Wer eine Sungrow-Wallbox eingerichtet hat, prüft
die Unit-ID (Standard jetzt 248) mit „Verbindung testen“.
