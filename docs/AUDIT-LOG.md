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
