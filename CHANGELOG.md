# Changelog

Kurzfassung je Version. Ausführliche Begründungen stehen in den jeweiligen Commit-Nachrichten.

## 2.12.0

Preisoptimiertes Laden zieht jetzt wirklich aus dem Netz — vorher bezahlte
der Hausspeicher die günstige Stunde mit.

- Warmwasser bekommt die Betriebsart „Preisoptimiert" (gab es bisher nur für die Wallbox).
- Sobald ein Gerät preisoptimiert aus dem Netz lädt, wird der Hausspeicher für die Dauer der günstigen Stunde gesperrt: entweder „Sperren" (weder laden noch entladen) oder „Vollladen" (aus dem günstigen Netz laden, aber nicht entladen). Umschaltbar unter Einstellungen → Tarif.
- Ohne diese Sperre deckte ein Hybrid-Wechselrichter die Netzladung aus der Batterie. Der Nutzer sah eine günstige Stunde, bezahlte aber einen Speicher-Rundlauf mit zweifachem Wirkungsgradverlust und stand abends ohne Reserve da.
- Zwei Wege zur Sperre, damit auch Wechselrichter ohne Modus-Register mitmachen: `set_mode(HOLD/FORCE_CHARGE)`, ersatzweise Reserve-SoC auf 100 %.
- Lässt sich der Speicher gar nicht steuern (reines Monitoring) und entlädt gerade, wird das Netzladen ausgesetzt statt den Speicher leerzuziehen. Der Grund steht im Klartext auf dem Dashboard. Wer es trotzdem will, schaltet `price_allow_battery_drain` am Gerät frei.
- Beim Beenden von MinePower wird eine aktive Sperre aufgehoben — kein Wechselrichter bleibt gesperrt zurück.
- Steigt der Preis über die Grenze, endet das Netzladen und das Gerät läuft als reine Überschusslast weiter. Die Sonne wird nicht verschenkt, nur weil die Börse teuer ist.
- **Am PV-Überschussladen ändert sich nichts** — die 111 bestehenden Tests der Überschussregelung laufen unverändert durch.

## 2.11.0

Stromausfälle killen die Postgres-Datendatei ohne Vorwarnung — kein
MinePower-eigener Bug, aber genau der Grund, warum nach einem Ausfall
manchmal alle Geräte weg waren.

- Automatische lokale Sicherung alle 6 Stunden (Geräte, Einstellungen, Konten inkl. Passwort-Hash) auf ein eigenes Volume, getrennt von der Datenbank.
- Findet die App beim Start eine leere Datenbank vor, obwohl eine solche Sicherung existiert, spielt sie sie automatisch zurück — kein manueller Import mehr nötig. Ist Postgres nach dem Ausfall gar nicht mehr startklar, reicht „Datenverzeichnis löschen, Stack neu starten": Die App füllt sich beim nächsten Hochfahren selbst wieder auf.
- Frische Installationen bekommen `--data-checksums` in Postgres, damit eine künftige Beschädigung als klarer Fehler auffällt statt als stille Inkonsistenz.
- Status unter `/api/system/backup-status` (Zeitpunkt, Anzahl).
- README/docs/unraid.md: Empfehlung, das Sicherungs-Volume auf einer anderen physischen Platte als die Datenbank zu binden.

## 2.10.0

Gestaltung überarbeitet. Die Farbpalette bleibt, die Struktur nicht.

- Dashboard neu geordnet: Zustand als ein Satz, dann Kennzahlen mit Rangfolge (Überschuss führt, doppelt breit), dann zwei Spalten bis zum Seitenende — links Energiefluss und Verlauf, rechts die Geräte. Vorher endete die linke Spalte auf halber Höhe und daneben blieb eine leere Fläche.
- Kennzahlen: sieben gleich große Zahlen wurden zu einer führenden plus vier untergeordneten. PV-Ertrag und Autarkiegrad sind zur Statistik gewandert — beides sind 24-Stunden-Rückblicke und haben dort mehr Platz.
- Farbverlauf über der Kopfzeile entfernt: Er nutzte die Energiefarben als Dekoration, obwohl dieselben Farben sonst überall eine feste Bedeutung tragen.
- Statistik: acht gleich große Kacheln wurden ein Raster mit zwei Leitzahlen (Autarkiegrad, Verschenkt). „Ersparnis" ist nicht mehr grün — Grün war dort dieselbe Farbe wie PV-Ertrag und meinte etwas anderes.
- Versalschrift überall abgeschafft (Abschnittstitel, Tabellenköpfe, Diagrammbeschriftungen). Normale Schreibweise liest sich als Wortbild statt Buchstabe für Buchstabe.
- Handy-Dashboard: die vier einzelnen Kennzahlkarten sind ein Block mit Haarlinien geworden; die Begründung zu „Verschenkt" steht als Fußnote direkt darunter statt in einem eigenen Abschnitt.
- Fahrzeug- und Speichergrafik neu gezeichnet. Die Radläufe sind jetzt echte Kreisbögen um die Radmitte — vorher lagen die Räder neben ihren Aussparungen. Leuchten folgen der Karosseriekante statt als graue Vierecke aufzuliegen. Beim Speicher steigen die Wärmefahnen im Wasser auf statt darüber, und die Anschlussrohre führen aus dem Bild statt in der Luft zu enden.
- Alle Verläufe und Masken der Gerätegrafiken bekommen eindeutige Namen. Vorher teilten sich zwei gleichzeitig sichtbare Geräte dieselbe Maske und die Füllung des zweiten brach zusammen.
- Betriebsartenschalter bricht um, statt seitlich abzuschneiden („Preisoptimi…").
- Kennzahlenraster: unvollständige letzte Reihe zeigt Flächenfarbe statt eines grauen Rechtecks.

## 2.9.0

- Neuer Menüpunkt „Fahrzeug": Ladestand, Ladestrom, Betriebsart, manueller Eingriff, Ladegrenze (ganze Prozent statt 5er-Schritte), Wecken-Knopf, Ladezyklen.
- Neuer Menüpunkt „Warmwasser", getrennt vom Fahrzeug: Speichergrafik, Betriebsart, Boost (gesperrt mit Begründung, wenn der Speicher schon über der Boost-Temperatur liegt).
- Optimistisches UI für beide Seiten: gewählter Zustand erscheint sofort, ein dünner Balken zeigt die ausstehende Bestätigung.
- Doppeltes Label „Sofort" behoben (Betriebsart → „Volllast", Eingriff → „Jetzt laden").
- Info-Schalter erklärt jede Betriebsart in einem Satz.
- Anmeldung hält jetzt: Token-Gültigkeit 12 h → 30 Tage, gleitende Verlängerung über `/api/auth/refresh`, Abmelden erst nach Gegenprüfung statt bei jedem einzelnen 401.
- Regelkreis: Toleranzschwelle für „folgt der Regelung nicht" auf eine ganze Stromstufe angehoben (weniger Fehlalarme beim Hochrampen).
- Geräteseite nach Rolle gruppiert (Messung / Steuerbare Lasten / Hausspeicher).
- Energiefluss als Knotendiagramm mit dem Netzpunkt (nicht dem Haus) in der Mitte.
- Handy-Navigation: vier Ziele plus „Mehr"-Blatt statt sieben Reitern in einer Leiste.
- Lange Gerätenamen werden überall gekürzt statt zu überlaufen.

## 2.8.0

- Ladegrenzen-Schieber: ganze Prozent statt 5er-Rasterung (Knopf hing sonst beim Ziehen hinterher).
- Energiefluss zurück auf Knotendarstellung, mit dem Netzpunkt statt dem Haus als Mittelpunkt.
- Geräteseite nach Rolle gruppiert, um Lücken durch unterschiedlich hohe Karten zu vermeiden.
- Fahrzeug und Warmwasser über einen Umschalter getrennt dargestellt.

## 2.7.0

- Erste Fassung der Fahrzeugseite: Ladestand-Grafik, Ladezyklen, geräteseitige Steuerung.
- Wecken-Aktion für Fahrzeuge über Funkanbindung ergänzt.

## 2.6.0

- WebSocket-Schlusscode 4401 korrekt ausgewertet, um unnötige Abmeldungen zu vermeiden.
- Warnflut bei kurzzeitig nicht erreichbarem Fahrzeug reduziert.
- Energiefluss-Diagramm überarbeitet.

## 2.5.0

- Gegenprüfung gegen unkontrolliert laufende Lasten (Geräte, die Befehle ignorieren, werden erkannt und gemeldet).
- WebSocket-Reconnect-Logik korrigiert.

## 2.4.0

- Batterie-Budget-Berechnung korrigiert.
- Ampere-Genauigkeit bei der Ladestromregelung verbessert.
- Taktverhalten der Regelschleife korrigiert.
