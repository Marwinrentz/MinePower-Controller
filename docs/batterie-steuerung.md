# Batteriesteuerung, Preisladen & Warmwasser-Boost

Stand 2.16.0. Beschreibt, wie MinePower den Hausspeicher steuert, wann er aus
dem Netz lädt, wann er nicht entladen darf, wie Eingriffe enden und was bei
Fehlern und Neustarts passiert. Die Entscheidung steckt in einer einzigen,
reinen Funktion: `plan_battery()` in `backend/app/core/battery_policy.py`.

## Voraussetzungen für aktive Batteriesteuerung

1. Ein **eigenes Batteriegerät**, z. B. „Sungrow Batterie SBR/SBH" mit derselben
   IP wie der Wechselrichter. Der SH-Wechselrichter-Treiber allein liefert nur
   Messwerte.
2. Am Batteriegerät **„Aktive Batteriesteuerung erlauben"** einschalten.
3. Für Netzladen bei günstigem Preis zusätzlich *Einstellungen → Batterie →
   Bei günstigem Strom aus dem Netz laden* einschalten (Standard: aus) und
   einen Tarif einrichten.

Ohne Freigabe kann MinePower nichts schreiben. Die Batterieseite und die
Einstellungen sagen das direkt, mit Knopf zur Geräteeinrichtung.

## Die Einstellungen (seit 2.16)

| Einstellung | Wo | Wirkung |
|---|---|---|
| **Reserve** (%) | Batterie → Grundeinstellungen | Die einzige Untergrenze. Wird als Min-SoC in den Wechselrichter geschrieben (Standard), damit auch das Haus dort aufhört. Liegt sie über dem, was der Wechselrichter annimmt (Sungrow SH: 50 %), schreibt MinePower 50 % und hält den Rest selbst per Entladesperre. |
| **Batterie zuerst bis** (%) | Batterie → Grundeinstellungen | Bis zu diesem Ladestand bekommt der Speicher den Sonnenüberschuss zuerst, darüber Auto und Warmwasser (Hysterese 3 %). 100 % = immer zuerst, 0 % = nie. Ersetzt Kettenplatz der Batterie, „Batterie hat Vorrang" und „Freigabe-SoC". |
| **Bei günstigem Strom aus dem Netz laden** | Batterie → Günstig aus dem Netz laden | Speicher lädt bei günstigem Preis aus dem Netz – sonst nie. |
| **Aus dem Netz laden bis** (%) | Batterie → Günstig aus dem Netz laden | Ziel fürs Netzladen. |
| **Nur wenn die Sonne es nicht schafft** | Batterie → Günstig aus dem Netz laden | Prognose prüfen: Reicht der erwartete Überschuss (× 0,8) für das fehlende Stück, wird nicht aus dem Netz geladen. |
| Reserve in den Wechselrichter schreiben | Experte | Standard an. Aus = MinePower hält die Reserve nur selbst. |
| Speicher auch bei „Sofort laden“, Boost und Geräteprogramm sperren | Experte | Standard aus, siehe unten. |
| Speicher versorgt Auto/Warmwasser mit (ab %) | Experte | Ab diesem Ladestand darf der Speicher PV-Laden von Auto und Warmwasser stützen; Standard nie. |
| Höchster Netzbezug beim Netzladen, Überschuss-Lasten abschalten ab, Leistung/Dauer von Hand | Experte | wie bisher |

**Migration:** Bestehende Einstellungen werden beim Start einmal übertragen
(Schema 2). Die alten Werte bleiben als Sicherung im Abschnitt `schema`.
Vorrang an mit Freigabe-SoC X ⇒ „zuerst bis" X % (0 oder 100 ⇒ 100 %); Vorrang
aus ⇒ „zuerst bis" Reserve. Das Netzlade-Ziel übernimmt die alte
Freigabe-Schwelle. „Vollladen im Preisfenster" entfällt ersatzlos, der Takt
wird auf höchstens 10 s gesetzt. Die Batterie verschwindet aus der
Prioritätskette, Preisgrenzen je Gerät entfallen, `manage_reserve` wird an.

## Sungrow SH: verwendete Register

1-basiert wie in der Sungrow-Doku *Communication Protocol of Residential Hybrid
Inverter*; beim Modbus-Zugriff wird 1 abgezogen (Fehlermeldungen nennen die
0-basierte Adresse).

| Register | Art | Bedeutung | Verwendung |
|---|---|---|---|
| 13001 | Input | Running-State (Bit 1 lädt, Bit 2 entlädt) | Richtung der Batterieleistung |
| 13022 | Input | Batterieleistung W | Messung |
| 13023 | Input | SoC 0,1 % | Messung |
| 13050 | Holding | EMS-Modus: 0 Eigenverbrauch, 2 Zwangsmodus | lesen + schreiben |
| 13051 | Holding | Zwangsbefehl: 0xAA (170) laden, 0xBB (187) entladen, 0xCC (204) stopp | lesen + schreiben |
| 13052 | Holding | Zwangsleistung W | lesen + schreiben |
| 13058 | Holding | Max-SoC 0,1 % (Ladeobergrenze) | nur lesen |
| 13059 | Holding | Min-SoC 0,1 % (Reserve), Gerät nimmt 0–50 % | schreiben, gedeckelt auf 50 % |

**So werden die Modi umgesetzt:**

| MinePower | 13050 | 13051 | 13052 |
|---|---|---|---|
| Automatik | 0 | 0xCC | – |
| Komplett gesperrt (hold) | 2 | 0xCC | – |
| Laden | 2 | 0xAA | Leistung |
| Entladen | 2 | 0xBB | Leistung |
| Entladung gesperrt | je nach Lage Automatik, hold oder Entladen mit genau der Hauslast (siehe Entladesperre) | | |

**Schreibreihenfolge:**

* Laden/Entladen/Sperren: 13052 → 13051 → 13050 = 2. Erst dann den
  Zwangsmodus, sonst führt der Wechselrichter kurz den alten Befehl aus.
* Zurück auf Automatik: 13050 = 0 → 13051 = 0xCC.

**Readback, Rollback, Fail-safe:** Jeder Schreibzugriff wird zurückgelesen und
im Schreibjournal protokolliert (Diagnose → `battery_control.devices[].writes`).
Scheitert ein Schritt mitten in der Folge (Timeout, abgelehnter Wert), setzt
der Treiber sofort 13050 = 0 und 13051 = 0xCC – ein halb umgestellter
Wechselrichter ist gefährlicher als keiner. Auch der „Stopp"-Befehl beim
Zurücksetzen wird jetzt zurückgelesen (vorher stand im Journal „13051 = 204,
Readback: null").

> **Annahme:** Die Registerkarte stammt aus der Sungrow-Dokumentation für die
> SH-RT-Serie. Die 50-%-Grenze für 13059 ist aus dem Ereignis vom 04.10.
> abgeleitet (55 % abgelehnt mit „interner Fehler"). Nicht an jeder Firmware
> verifiziert – siehe Testanleitung unten.

## Wer schreibt wann?

Es gibt **genau einen Schreiber**: den Regelkreis. Bedienknöpfe und API setzen
nur den Sollzustand und wecken den Regelkreis sofort (`loop.kick()`).

Sollzustand je Takt – erste passende Zeile gewinnt:

| Rang | Lage | Speicher |
|---|---|---|
| 1 | Befehl von Hand (Batterie-Seite) | wie befohlen, endet immer (Dauer, Ziel-SoC oder „Automatik") |
| 2 | Günstig laden an + Preis günstig + unter Ziel + Sonne reicht laut Prognose nicht + ≥ 200 W Netzbudget frei | lädt aus dem Netz |
| 3 | Eine Last läuft, **weil** der Strom günstig ist (Auto/Warmwasser „Sonne + günstig") | Entladung gesperrt – immer |
| 4 | Sofort laden, Boost, Zeitplan, Zielladung, Geräteprogramm, Auto-Selbststart | Entladung gesperrt nur mit Experten-Schalter |
| 5 | Ladestand an der Reserve, die der Wechselrichter nicht selbst hält | Entladung gesperrt |
| 6 | sonst | Automatik (Eigenverbrauch) |

Reicht in Zeile 2 nur das Netzbudget nicht, heißt es „Netzladen pausiert" und
der Speicher bleibt gegen Entladung gesperrt. Jeder Wechsel wird höchstens
einmal pro Minute ins Protokoll geschrieben, mit Grund.

**Warum Zeile 3 Pflicht ist und Zeile 4 nicht:** Lädt das Auto, weil Netzstrom
gerade billig ist, wäre es Unsinn, den teuer gespeicherten Strom dafür
herzugeben. Bei „Sofort", Boost oder dem ELWA-Programm um 6 Uhr ist der
Netzstrom dagegen nicht billig. Die Wiedergabe der Diagnose-Woche zeigt: Sperrt
man den Speicher auch dafür, bleibt er voll, am Mittag geht die Sonne ins Netz
(+4,8 kWh verschenkt, +6,8 kWh Netzbezug, +1,25 € in der Woche).

### Entladesperre (DischargeGuard)

„Entladung gesperrt" heißt nicht „Speicher aus":

* Deckt die Sonne Haus und Lasten (Einspeisung > 300 W für 60 s), bleibt der
  Speicher in der Automatik und darf laden.
* Fehlt Leistung, entlädt er nur noch so viel, wie das **Haus** braucht
  (Zwangsentladen mit der Hauslast, ab 300 W und nur über der Reserve). Den
  Rest – Auto, Warmwasser – liefert das Netz.
* Ist der Hausbedarf klein, ruht er ganz (hold).
* Der Schutz greift **bevor** die Last anläuft (erwartete Last zählt mit), und
  Wechsel kommen nie öfter als alle 20 s – jeder Wechsel sind drei
  Modbus-Schreibzugriffe.
* Lässt sich der Speicher gar nicht sperren (keine Freigabe, Treiber ohne
  Modus) und ist er nicht leer, startet keine Preis-Last: lieber gar nicht aus
  dem Netz laden als aus dem Speicher.

## Eine Preisgrenze

Es gilt nur noch die Grenze des Tarifs (*Tarif → Günstig, wenn unter*). Auto,
Warmwasser und Batterie folgen ihr. Eigene Grenzen je Gerät gibt es nicht mehr
(vorher: global 20 ct, Tesla 25 ct, ELWA 15 ct – und es war unklar, welche
wirkte). Wo eine Preisgrenze eine Rolle spielt (Auto, Warmwasser, Batterie,
Tarif), steht der aktuelle Wert direkt dabei.

## Eingriffe enden immer

| Eingriff | Endet … |
|---|---|
| Auto „Aus" | beim Abstecken (nur wenn das Auto erreichbar ist) oder nach 4 h |
| Auto „Sofort" | bei voll, beim Abstecken oder nach 12 h |
| Warmwasser-Boost | nach Dauer und/oder Temperatur (siehe unten) |
| Batterie von Hand | nach Dauer (max. 4 h, einstellbar) oder Ziel-SoC |

Die ersten 120 s nach dem Setzen zählen „voll" und „abgesteckt" nicht – ein
gerade gedrücktes „Sofort" endet nicht, weil das Auto noch „voll" meldet.
Jeder laufende Eingriff steht mit Ende in der Oberfläche („Sofort bis 06:31").
Das Ende wird protokolliert.

## Warmwasser-Boost

Einstellbar unter *Einstellungen → Warmwasser-Boost* (je Heizstab):

| Modus | Endet … |
|---|---|
| nach Zeit | nach der Dauer |
| bei Temperatur | bei Zieltemperatur; spätestens nach der Sicherheitsgrenze (Standard 180 min) |
| was zuerst kommt | bei Zieltemperatur oder nach der Dauer |

* **Ohne gültigen Temperaturwert** (Fühler fehlt, NaN, < −10 °C, > 110 °C)
  endet ein Boost immer nach der Dauer – auch im Temperaturmodus.
* **Sicherheitstemperatur 80 °C** beendet jeden Boost, in jedem Modus.
* Zieltemperatur 30–80 °C, Dauer 5–360 min (API lehnt andere Werte ab).
* Ende wird protokolliert: „Boost beendet: <Gerät> – Zeit abgelaufen /
  Temperatur erreicht / manuell gestoppt / Gerät deaktiviert".

**Vorrang:** Ein Boost ist ein ausdrücklicher Wunsch und schlägt Preis,
PV-Überschuss und Ketten-Position. Er ist nur durch Netzanschluss-Budget und
Nennleistung begrenzt. Er sperrt den Hausspeicher standardmäßig nicht (Zeile 4
oben); mit dem Experten-Schalter „Speicher auch bei „Sofort laden“, Boost und
Geräteprogramm sperren“ schon.

### Eigenes Programm des Heizstabs (my-PV)

Meldet die ELWA/AC THOR Status 4 („Boost“), heizt sie in ihrem **eigenen**
Programm (Warmwasser-Sicherstellung, Legionellenschutz oder Boost am Gerät) –
unabhängig von MinePower. Bisher sah das wie „folgt der Regelung nicht“ aus
und die Leistung wurde fälschlich als verfügbarer Überschuss gezählt. Jetzt:

* kurze Meldung „heizt per Geräteprogramm (2,98 kW)“, am Ende Energie und
  Dauer; die lange Erklärung und die Abhilfe stehen in der Geräteeinrichtung
  (Info-Symbol);
* die Leistung gilt nicht als umlenkbar (keine Scheinüberschüsse) und zählt
  als fremde Last: Die Batterie-Entscheidung ändert sich dadurch nicht
  (Zeile 4, nur mit Experten-Schalter gesperrt);
* Abhilfe: im ELWA-Menü die Zeitfenster der Warmwasser-Sicherstellung prüfen
  bzw. abschalten.

### Wärmepuffer

*Warmwasser → Mit Überschuss heizen bis*: Ginge sonst Sonnenstrom ins Netz,
darf das Wasser über die Zieltemperatur hinaus bis zu diesem Wert heizen – nur
mit echtem Überschuss, nie aus dem Netz, ohne Zeitplan. Die Maximaltemperatur
im Gerät selbst muss das erlauben.

## Neustart, Stopp, Fehler (Fail-safe)

| Ereignis | Verhalten |
|---|---|
| Container stoppt / Update | Speicher im Zwangsmodus → Eigenverbrauch (vor dem Beenden; `stop_grace_period: 30s`) |
| Harter Absturz (kill, Stromausfall) | Beim nächsten Start wird ein fremder Zwangsmodus erkannt, gemeldet und zurückgesetzt |
| Manueller Batteriebefehl | endet bei Neustart (bewusst) |
| Warmwasser-Boost | läuft nach Neustart mit derselben Endzeit weiter; abgelaufene werden verworfen |
| Schreibfreigabe entzogen / Gerät gelöscht | Speicher wird vorher mit dem alten Treiber zurückgesetzt |
| Netzmessung fällt aus / Regelung pausiert | Speicher → Eigenverbrauch, Lasten sicher aus |
| Schreibfehler mitten im Befehl | Treiber setzt sofort Eigenverbrauch (Rollback), Fehler an der Kachel |
| Reserve vom Gerät abgelehnt | einmal gemeldet, MinePower hält die Reserve per Entladesperre |

## Testanleitung an der echten Anlage (Sungrow SH + WiNet-S)

Vorher: Batterie-SoC notieren, Hersteller-App offen halten.

1. **Lesen prüfen:** Geräte → Sungrow Batterie → Verbindungstest. Erwartet:
   „EMS-Modus: Eigenverbrauch (0)", „SoC-Grenzen im WR: x – y %" mit
   plausiblen Werten (Max meist 90–100 %, Min meist 5–15 %). **Steht Max
   unter 50 %: nicht weitermachen**, Max-SoC in der App korrigieren – das ist
   vermutlich der Rest des alten Registerfehlers.
2. **Freigabe:** „Aktive Batteriesteuerung erlauben" einschalten, speichern.
3. **Manuell laden:** Dashboard → Batterie → Leistung 1 kW, Dauer 15 min →
   *Laden*. Erwartet innerhalb weniger Sekunden: Kachel „Lädt manuell",
   „Wechselrichter: Zwangsladen", Batterieleistung steigt auf ~1 kW, in der
   Sungrow-App Zwangsmodus. Diagnose → `battery_control.devices[0].writes`:
   drei Einträge (13052, 13051, 13050) mit `ok: true`.
4. **Automatik:** *Automatik* drücken → „Wechselrichter: Eigenverbrauch",
   App zeigt Eigenverbrauch. Writes: 13050 = 0, 13051 = 204 (0xCC).
5. **Entladen:** 1 kW, Ziel = Reserve + 5 % → Leistung negativ; ggf. Einspeisung.
   Nach Test *Automatik*.
6. **Fail-safe Stopp:** *Laden* starten, dann `docker compose stop app`.
   App muss innerhalb weniger Sekunden Eigenverbrauch zeigen.
7. **Fail-safe Absturz:** *Laden* starten, dann `docker kill <container>`.
   Wechselrichter bleibt im Zwangsladen (erwartet). `docker compose up -d` →
   im Ereignisprotokoll „… stand beim Start im Modus „force_charge" …", App
   zeigt kurz darauf Eigenverbrauch.
8. **Preisladen:** *Bei günstigem Strom aus dem Netz laden* an, Tarif-Grenze kurzzeitig über den
   aktuellen Preis, „Laden bis" über den aktuellen SoC, Prognose-Schalter aus
   → Batterie-Seite „lädt aus dem Netz". Grenze zurücksetzen → Automatik.
   Ist der Schalter aus, darf das nie passieren.
8a. **Entladesperre:** Auto auf „Sonne + günstig", Grenze über den aktuellen
   Preis, Auto angesteckt → Auto lädt aus dem Netz, Batterieleistung bleibt
   ≥ −Hauslast (entlädt höchstens fürs Haus), App zeigt Zwangsmodus.
8b. **Reserve:** Reserve 20 % → Writes: 13059 = 200. Reserve 55 % → 13059 = 500
   (gedeckelt), Batterie-Seite zeigt „WR hält 50 %, MinePower bis 55 %“.
9. **Boost:** *Einstellungen → Warmwasser-Boost* „nach Zeit", 5 min →
   Boost starten → Kachel „Boost noch 5 min" → nach 5 min Ereignis
   „Boost beendet … Zeit abgelaufen".

Wird ein Schreibbefehl abgelehnt, steht die Modbus-Meldung im Klartext an der
Kachel und in `writes[].error` (z. B. „Wert außerhalb des erlaubten Bereichs" →
Max. Leistung am Batteriegerät auf den Datenblattwert senken).
