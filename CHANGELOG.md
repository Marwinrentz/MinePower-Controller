# Changelog

Kurzfassung je Version. Ausführliche Begründungen stehen in den jeweiligen Commit-Nachrichten.

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
