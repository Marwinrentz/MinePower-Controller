# Sicherheit

Stand: Version 3.3.0. Ergebnis der Prüfung von Backend, Oberfläche, Apps,
Docker-Image, Compose-Datei, CI und Abhängigkeiten. Jede Zeile nennt Fund,
Schweregrad und was daraus geworden ist. Offene Punkte stehen am Ende.

Schweregrade: **hoch** (ohne besondere Voraussetzung ausnutzbar oder mit
großem Schaden), **mittel** (braucht Zugang zum Netz oder ein Konto),
**niedrig** (Härtung, geringe Wirkung).

## Befunde

### Anmeldung und Sitzungen

| # | Fund | Grad | Stand |
|---|---|---|---|
| S1 | Das Anmelde-Token stand in der WebSocket-Adresse (`/api/ws?token=…`) und damit in jedem Zugriffsprotokoll des Servers und vorgeschalteter Proxys. Wer `docker logs` lesen kann, konnte sich 30 Tage lang als dieser Nutzer anmelden. | hoch | behoben: Oberfläche und App schicken das Token als erste Nachricht über die offene Verbindung, Apps wahlweise als Kopfzeile. `?token=` wird für ältere Apps noch angenommen, im Protokoll aber geschwärzt. |
| S2 | Tokens galten 30 Tage und ließen sich nicht entwerten. Ein Passwortwechsel beendete bestehende Anmeldungen nicht. | mittel | behoben: Tokens von vor dem letzten Passwortwechsel gelten nicht mehr (auch Tokens im alten Format). |
| S3 | Kein Nutzer konnte sein eigenes Passwort ändern, nur ein Admin für andere. | mittel | behoben: Einstellungen → Konto, mit Abfrage des bisherigen Passworts. |
| S4 | Die Login-Bremse zählte je IP und E-Mail. Viele Konten von einer Adresse aus durchzuprobieren war unbegrenzt möglich; der Zähler wuchs ohne Grenze. | mittel | behoben: zusätzlich je Adresse (30 Fehlversuche in 5 min), Speicher begrenzt, Antwort mit `Retry-After`. |
| S5 | Unbekannte E-Mail-Adressen antworteten schneller als bekannte (kein Hash-Vergleich). | niedrig | behoben: gleiche Rechenzeit in beiden Fällen. |
| S6 | Beim Ändern eines Kontos wurde die Rolle nicht geprüft; der letzte Admin konnte sich herabstufen oder sperren. | mittel | behoben: nur `admin`, `user`, `readonly`; der letzte aktive Admin bleibt. |
| S7 | Zwei gleichzeitige Aufrufe des Erst-Setups legten zwei Admins an. | niedrig | behoben: das Setup läuft einzeln. |
| S8 | Passwörter ohne Obergrenze; bcrypt wertet nur 72 Byte aus. | niedrig | behoben: höchstens 256 Zeichen, ausdrücklich auf 72 Byte gekürzt. |
| S9 | Fehlgeschlagene Anmeldungen standen nicht im Ereignisprotokoll. | niedrig | behoben: mit Adresse, ohne Passwort. |

Passwörter werden mit bcrypt gespeichert (12 Runden). Tokens sind mit HS256
signiert; das Geheimnis wird beim ersten Start erzeugt (64 Hex-Zeichen). Ein
eigenes `JWT_SECRET` unter 32 Zeichen meldet der Start als Warnung.

### HTTP

| # | Fund | Grad | Stand |
|---|---|---|---|
| S10 | Keine Sicherheits-Kopfzeilen. Die Oberfläche ließ sich in fremde Seiten einbetten (Clickjacking auf Schaltflächen wie „Sofort laden“); eingeschleuster Code hätte beliebig nachladen können. | mittel | behoben: `Content-Security-Policy` (Skripte, Stile, Verbindungen nur vom eigenen Ursprung), `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`; API-Antworten mit `Cache-Control: no-store`. |
| S11 | CORS stand auf `*`: Jede Webseite durfte die API aus dem Browser ansprechen (etwa Anmeldeversuche gegen die Anlage im Heimnetz). | mittel | behoben: Standard ist kein CORS. `CORS_ORIGINS` schaltet einzelne Ursprünge frei. |
| S12 | `/docs`, `/redoc` und `/openapi.json` beschrieben jede Schnittstelle ohne Anmeldung. | niedrig | behoben: aus, mit `API_DOCS=true` einschaltbar. |
| S13 | Auslieferung statischer Dateien verglich den Pfad als Textanfang (`/app/static-x` hätte gepasst). Praktisch nicht ausnutzbar, weil es keinen solchen Ordner gibt. | niedrig | behoben: Vergleich über Pfadteile. |

Es gibt keine Cookies. Die Anmeldung läuft über die Kopfzeile
`Authorization`, die ein Browser nie von selbst mitschickt – Cross-Site
Request Forgery ist damit nicht möglich. Das Token liegt im `localStorage`
des Browsers; die Content-Security-Policy verhindert, dass fremder Code es
ausliest. Die Oberfläche setzt nirgends HTML aus Daten zusammen
(`dangerouslySetInnerHTML` kommt nicht vor).

### Eingaben und Steuerbefehle

| # | Fund | Grad | Stand |
|---|---|---|---|
| S14 | Regelungs-Einstellungen eines Geräts (Ströme, Leistungen, Temperaturen) wurden nur beim Ändern geprüft, nicht beim Anlegen und nicht beim Import. Ein Gerät konnte mit 500 A Höchststrom in den Regelkreis kommen. | hoch | behoben: dieselbe Prüfung an allen drei Stellen. Zusätzlich begrenzt der Regler jeden gespeicherten Wert selbst (1–63 A, 0–50 kW, 20–85 °C, Boost 30–80 °C), unabhängig davon, wie er in die Datenbank kam. |
| S15 | Der Import löschte alle Geräte und legte sie neu an. Dabei verschwanden die Ladevorgänge und alle gespeicherten Zugangsdaten; eine fehlerhafte Datei führte zu einem Serverfehler. | mittel | behoben: Geräte werden an Ort und Stelle aktualisiert, die Datei wird vorher vollständig geprüft, Fehler nennen Gerät und Ursache. |
| S16 | Geräteadressen sind frei eingebbar, und der Server ruft sie ab. In einer Cloud-Umgebung ließen sich so die Metadatendienste abfragen (Server-Side Request Forgery). | niedrig | behoben: Adressen der Metadatendienste, `0.0.0.0` und Multicast werden abgelehnt, auch als Ziel einer Weiterleitung. Private Adressen bleiben erlaubt – dort stehen die Geräte. |
| S17 | Längen und Wertebereiche in mehreren Anfragen fehlten (Namen, Fahrzeugdaten, Listen). | niedrig | behoben: Grenzen in allen Schemas. |

SQL wird durchgehend mit gebundenen Parametern ausgeführt. Es gibt keine
Aufrufe von Shell, `eval`, `pickle` oder YAML. Hochgeladen wird nur JSON.

### Zugangsdaten

| # | Fund | Grad | Stand |
|---|---|---|---|
| S18 | SMTP-Versand prüfte das Zertifikat des Mailservers nicht. Das SMTP-Passwort ging an jeden, der sich dazwischenschaltet. | mittel | behoben: Zertifikat und Hostname werden geprüft (abschaltbar für eigene Server mit `smtp_tls_verify: false`). |
| S19 | Benachrichtigungen prüften die Antwort des Dienstes nie; ein abgelehntes Token blieb unbemerkt. Fehlermeldungen konnten die Adresse mit Token enthalten. | niedrig | behoben: Status wird geprüft, Meldungen werden geschwärzt. |
| S20 | Fehlermeldungen und Protokollzeilen konnten Adressen mit Zugangsdaten enthalten (`?apikey=…`, `nutzer:passwort@`). | niedrig | behoben: ein Filter schwärzt alle Protokollausgaben. |

Gerätepasswörter, API-Tokens und Dienst-Tokens liegen verschlüsselt in der
Datenbank (Fernet, Schlüssel aus `MINEPOWER_SECRET_KEY` oder einer
Schlüsseldatei im Datenvolume). Die API liefert sie nie zurück, nur `•••`.
Exporte und der Diagnosebericht enthalten sie nicht. Zugangsschlüssel für
KI-Assistenten sind nur als SHA-256-Hash gespeichert.

### Docker

| # | Fund | Grad | Stand |
|---|---|---|---|
| S21 | Die Anwendung lief als root. | mittel | behoben: Start übergibt die Datenordner und wechselt auf einen Nutzer ohne Rechte (`PUID`/`PGID`, Standard 1000). |
| S22 | Beschreibbares Dateisystem, alle Standard-Capabilities. | niedrig | behoben in `docker-compose.yml`: nur lesbar, `/tmp` als tmpfs, alle Capabilities entfernt bis auf die drei für den Nutzerwechsel, `no-new-privileges`. Geprüft am laufenden Container: UID 1000, keine Capabilities. |
| S23 | Abhängigkeiten mit Versionsbereichen: Jeder Build konnte andere Versionen enthalten. | niedrig | behoben: `requirements.txt` nennt exakte Versionen (aus `requirements.in`), das Frontend baut mit `npm ci` aus der Lockdatei. |
| S24 | Node 20 als Build-Image (seit April 2026 ohne Sicherheitsupdates). | niedrig | behoben: Node 22. |
| S25 | Kein Healthcheck im Image (nur in Compose). | niedrig | behoben. |

### Abhängigkeiten

| Prüfung | Ergebnis |
|---|---|
| `pip-audit -r requirements.txt` | keine bekannten Schwachstellen |
| `npm audit` | vorher 7 (3 hoch, 4 mittel), jetzt 0. Dafür: echarts 5 → 6, react-router 6 → 7, vite 5 → 8. |

## Bewusst so belassen

- **Token im Browser-Speicher.** Üblich für Oberflächen ohne Cookies. Die
  Content-Security-Policy ist der Schutz dagegen.
- **Standardpasswort der Datenbank** (`minepower`), wenn keine `.env`
  existiert. Die Datenbank hat keinen veröffentlichten Port und ist nur aus
  dem Compose-Netz erreichbar. Für den Betrieb `POSTGRES_PASSWORD` setzen.
- **Schlüsseldatei im Sicherungsordner.** Der Schlüssel für Zugangsdaten
  liegt in beiden Volumes, damit er ein Update überlebt, bei dem nur eines
  eingebunden ist (Fehler in 3.1.0). Wer die Volumes lesen kann, kann die
  gespeicherten Zugangsdaten entschlüsseln. Abhilfe: `MINEPOWER_SECRET_KEY`
  setzen und außerhalb der Volumes aufbewahren.
- **`/api/system/health` und `/api/system/setup-state` ohne Anmeldung.**
  Healthcheck und Erststart brauchen sie. Sie nennen Version und ob schon
  ein Konto existiert.
- **Demo-Modus** legt ein Konto mit bekanntem Passwort an. Nur zum
  Ausprobieren, nicht für eine Anlage im Netz.
- **Kein HSTS.** TLS endet am vorgeschalteten Proxy; die Kopfzeile gehört
  dorthin.

## Offen

| Punkt | Grad | Empfehlung |
|---|---|---|
| Kein TLS im Image. Im Heimnetz läuft alles über HTTP. | mittel | Für Zugriff von außen einen Reverse-Proxy mit TLS oder ein VPN davorsetzen. Die App verlangt für entfernte Server bereits HTTPS. |
| Optionaler MQTT-Broker (`--profile mqtt`) nimmt jeden ohne Anmeldung an und veröffentlicht Port 1883. | mittel | Nur im vertrauenswürdigen Netz nutzen oder in `deploy/mosquitto.conf` Benutzer und Passwort einrichten. Nicht geändert, weil bestehende Home-Assistant-Anbindungen sonst brechen. |
| OCPP: Die Wallbox verbindet sich ohne Anmeldung (Sicherheitsprofil 0). Ein Gerät im Netz, das die Kennung kennt, kann sich als Wallbox ausgeben und falsche Messwerte liefern. | mittel | Siehe `docs/DRIVERS.md`. |
| Eine offene WebSocket-Verbindung bleibt nach Passwortwechsel oder Sperrung bis zum nächsten Verbindungsaufbau bestehen (nur lesend). | niedrig | Bei Bedarf Anwendung neu starten. |
| Zwei-Faktor-Anmeldung gibt es nicht. | niedrig | Für den Zugriff von außen ein VPN verwenden. |
| GitHub-Actions sind per Versions-Tag eingebunden, nicht per Commit-Hash. Die Workflows sind derzeit abgeschaltet. | niedrig | Vor dem Wiedereinschalten auf Hashes umstellen. |
| Persönliche Daten in alten Commits (siehe `AUDIT.md`, Abschnitt „Offen“). | niedrig | Entscheidung des Betreibers. |

## Meldung von Schwachstellen

Bitte nicht als öffentliches Issue, sondern direkt an den Anbieter (Kontakt
in der README).
