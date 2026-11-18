# Funktionen: Bestand, Vorschläge, Entscheidung

Stand: Version 3.3.0. Bewertet nach Nutzen für den Betreiber und Aufwand
(S = Stunden, M = ein bis zwei Tage, L = mehr). Umgesetzt wurde, was bei
kleinem Aufwand am meisten bringt.

## Schon vorhanden (vor 3.3.0)

| Funktion | Stand |
|---|---|
| Benachrichtigungen über ntfy und Telegram | vorhanden; E-Mail im Backend, ohne Oberfläche |
| MQTT mit Home-Assistant-Erkennung (nur Messwerte) | vorhanden |
| Dynamische Tarife (Tibber, aWATTar), automatische Preisgrenze | vorhanden |
| Sicherheitsstopp ohne Netzmessung, Rückfall der Batterie auf Eigenverbrauch | vorhanden |
| Statistik: Autarkie, Eigenverbrauch, Ersparnis, CO₂, verschenkter Solarstrom | vorhanden |
| CSV-Export der Ladevorgänge | vorhanden |
| Deutsch und Englisch | vorhanden |
| KI-Assistent über MCP | vorhanden |

## In 3.3.0 umgesetzt

| Funktion | Nutzen | Aufwand | Anmerkung |
|---|---|---|---|
| Testnachricht je Kanal | hoch: Ein falsches Token fiel bisher erst auf, wenn die Meldung im Ernstfall fehlte | S | Einstellungen → Benachrichtigungen |
| Webhook als Kanal | mittel: bindet beliebige Dienste an (Home Assistant, n8n, eigene Skripte) | S | POST mit `title`, `message`, `source` |
| Regelkreis-Überwachung im Healthcheck | hoch: Ein stehender Regelkreis fiel nicht auf, solange die Oberfläche antwortete | S | `/api/system/ready`, Docker meldet „unhealthy“ |
| Diagnose: Antwortzeit und Fehlerzähler je Gerät, Verbindungstest | hoch für die Fehlersuche | S | Diagnose → Geräte |
| Diagnose: Protokoll mit Filter | hoch: bisher nur über `docker logs` | S | Diagnose → Protokoll |
| Diagnose: Plausibilitätsprüfung | hoch: findet vertauschte Vorzeichen, die sonst als falsche Regelung erscheinen | M | Energiebilanz, Einspeisung ohne Erzeugung, PV nachts |
| Diagnose-Export als ZIP, anonymisiert | hoch für Support | M | ohne Zugangsdaten, Adressen und Gerätenamen ersetzt |
| Verlauf als CSV | mittel | S | Statistik → Verlauf (CSV) |
| Strompreis, Einspeisevergütung und CO₂ einstellbar | mittel: Ersparnis rechnete mit festen 32 ct | S | Einstellungen → Auswertung |
| Ladevorgang überlebt einen Neustart | hoch: Updates während einer Ladung teilten den Vorgang | S | |
| Sicherungen einspielen, Speicherplatz-Warnung | hoch | M | Phase 2 |

## Vorgeschlagen, nicht umgesetzt

| Funktion | Nutzen | Aufwand | Warum nicht jetzt |
|---|---|---|---|
| Steuern über MQTT (Modus, Boost, Sofort laden) | hoch für Home-Assistant-Nutzer | M | Braucht einen Broker mit Anmeldung; der mitgelieferte nimmt jeden an (siehe SECURITY.md). Erst die Absicherung, dann Befehle. Über Home Assistant geht Steuern heute schon per REST oder MCP. |
| E-Mail-Benachrichtigung in der Oberfläche | mittel | S | Das Backend kann es; es fehlen fünf Felder. Zurückgestellt, weil ntfy, Telegram und Webhook denselben Zweck erfüllen. |
| Push-Mitteilungen der iOS-App | hoch | L | Braucht einen Apple-Entwicklerzugang und einen Zustelldienst außerhalb der Anlage; widerspricht dem Grundsatz „nur der eigene Server“. |
| Zwei-Faktor-Anmeldung | mittel | M | Im Heimnetz selten nötig; für den Zugriff von außen ist ein VPN die bessere Antwort. |
| Lastmanagement über mehrere Wallboxen mit Phasenbilanz | mittel | L | Der Hausanschluss wird heute als Summe geschützt, nicht je Phase. Braucht Phasenströme vom Zähler, die nicht jeder Treiber liefert. |
| Wetterabhängige Prognose mit eigener Lernkurve | mittel | L | forecast.solar deckt den Bedarf; eine eigene Kurve braucht Monate an Daten je Anlage. |
| Tarif-Anbieter ENTSO-E / EPEX direkt | mittel | M | Tibber und aWATTar decken DE/AT; weitere Länder auf Nachfrage. |
| Abrechnung je Nutzer (RFID) als PDF | niedrig | M | CSV-Export reicht für die private Abrechnung. |
| Sicherung außer Haus (S3, WebDAV) | mittel | M | Die Dateien liegen im Volume und lassen sich mit vorhandenen Werkzeugen abholen. |
| OCPP 2.0.1, TLS für OCPP | mittel | L | 1.6 ist bei verbreiteten Boxen Standard; TLS über einen Proxy. |

## Grundsätze der Auswahl

- Was im Ernstfall fehlt, kommt zuerst: Meldungen, die ankommen;
  Diagnose, die die Ursache zeigt; ein Healthcheck, der Stillstand erkennt.
- Keine Funktion, die Daten aus dem Haus schickt, ohne dass der Betreiber
  sie selbst einrichtet.
- Keine neue Abhängigkeit für eine Nebenfunktion.
