# Architektur-Übersicht

```
┌─────────────────────────────────────────────────────────────────┐
│  frontend  React + TS + Vite · Design-System (hell/dunkel) ·    │
│  i18n de/en · Dashboard (WS-Live) · Wizard · Geräte ·           │
│  Statistik · Diagnose · mobile Tab-Navigation                   │
└──────────────┬──────────────────────────────────────────────────┘
               │ REST /api/* + WebSocket /api/ws (JWT)
┌──────────────┴──────────────────────────────────────────────────┐
│  backend (FastAPI, async)                                       │
│                                                                 │
│  api/          Auth · Devices · Settings · Status · History ·   │
│                Sessions · Events · System · Statistics          │
│  core/         ControlLoop (Background-Task)                    │
│                regulation.py – reine, testbare Regelungslogik   │
│  services/     WS-Manager · Measurement-Buffer · Audit ·        │
│                Tariff (Tibber/aWATTar) · Forecast · Notifier ·  │
│                MQTT-Publisher (optional)                        │
│  drivers/      Registry + Plugin-Discovery                      │
│    ├── base.py         5 Interfaces + DriverMeta/ConfigField    │
│    ├── validation.py   Plausibilitätsprüfung + Fehlertypen      │
│    ├── modbus_util.py  Modbus: Lock, Reconnect, Readback, Pool  │
│    ├── http_util.py    HTTP: Session-Reuse, Reconnect, Klartext │
│    ├── simulation.py   komplette Fake-Anlage (Demo-Modus)       │
│    ├── sungrow/        SG · SH · SBR/SBH · DTSU666 · Wallbox    │
│    ├── tesla/          BLE-Proxy-Client · Fleet-API-Client      │
│    ├── sunspec.py      herstellerübergreifend (Kostal, GoodWe …)│
│    ├── huawei.py · fronius.py · sma.py · goe.py · shelly.py     │
│    ├── mypv.py · generic_modbus.py · ocpp_driver.py             │
│    └── registry.py     @register + /app/plugins-Discovery       │
└──────────────┬──────────────────────────────────────────────────┘
               │ SQLAlchemy async (asyncpg)
┌──────────────┴──────────────────────────────────────────────────┐
│  db: TimescaleDB (PostgreSQL) – Config relational,              │
│  measurements als Hypertable (Retention 90 Tage)                │
└─────────────────────────────────────────────────────────────────┘
```

## Der Regelkreis (core/loop.py)

Jeder Tick (Default 3 s):

1. **Lesen** – alle aktivierten Geräte parallel (`asyncio.gather`), je Gerät
   Timeout; 3 Fehlversuche → offline + Auto-Reconnect + Benachrichtigung.
2. **Budget** – `budget = Σ P_steuerbar − P_netz(geglättet) − Sollwert`.
   Innerhalb des Totbands wird nicht nachgeregelt (kein Flattern). Das
   Totband ist **adaptiv**, siehe unten.
3. **Verteilen** – entlang der Prioritätskette. Zwei Pools:
   * `pool_pv`: der eigentliche Überschuss.
   * `pool_bat`: aktuelle Batterie-**Lade**leistung – steht nur Lasten zu,
     die in der Kette **vor** der Batterie stehen (der Hybrid-WR regelt
     seine Ladung selbst herunter, wenn die Last zugreift).
   * `ev_lock_soc`: unterhalb dieses SoC wird Batterie-**Ent**ladung nicht
     fürs Auto verwendet (Budget des Autos wird reduziert).

   Die Pools werden mit der **tatsächlich abgenommenen** Leistung verrechnet
   (`WallboxDecision.power_w`), nicht mit dem ungerundeten Wunschwert.
4. **Lückenfüller** – siehe unten.
5. **Stellen** – Stellgrößen nur bei Änderung senden (ganze Ampere /
   10-W-Raster). Phasenumschaltung: Umschaltverzögerung + Sperrzeit, vorher
   Ladestopp. Treiber lesen Schreibbefehle zurück; scheitert die Übernahme,
   landet der Klartext in `device.command_error` und damit im Dashboard.
6. **Publizieren** – Snapshot via WebSocket + optional MQTT; Messwerte
   gepuffert als Batch in die Hypertable; Charge-Sessions inkl.
   Solaranteil (marginal: `solar = P_lade − max(0, P_bezug)`).

**Sicherheits-Fallback:** Ist die Netzmessung offline (oder die Regelung
pausiert), werden alle steuerbaren Lasten gestoppt – niemals blind laden.
Keine der Optimierungen unten weicht das auf; sie greifen erst nach diesem
Check.

## Möglichst wenig Überschuss verschenken

Drei Mechanismen, alle in `core/regulation.py` (rein, ohne I/O, testbar):

### 1. Ehrliche Quantisierung

Eine Wallbox kann nur ganze Ampere. Der Regler rundet **ab** – Aufrunden
würde mehr anfordern, als PV liefert, und damit Netzbezug erzeugen. Der
abgeschnittene Rest ist nicht verloren: `WallboxDecision.power_w` meldet die
wirklich abgenommene Leistung, der Loop weiß dadurch, was übrig bleibt, und
reicht es weiter. (Umstellbar auf kaufmännisches Runden über die
Geräteeinstellung `current_rounding`.)

### 2. Lückenfüller

Bleibt nach der Prioritätsverteilung ein Rest übrig, der für die nächste
Last zu klein ist – typisch 180 W, während eine Wallbox mindestens 1380 W
braucht –, geht er an eine **stufenlos modulierbare** Last (my-PV & Co.),
**unabhängig von deren Platz in der Prioritätskette**. Die Alternative wäre
nicht „später“, sondern „verschenkt“.

Relais-Heizstäbe sind ausgenommen: Sie können nur ihre volle Nennleistung
und taugen deshalb nicht als Lückenfüller. Der Mechanismus erhöht nur;
Absenken bleibt der regulären Entscheidung mit ihrer Hysterese vorbehalten.

### 3. Adaptives Totband

`VolatilityTracker` misst die mittlere Änderung der PV-Leistung je Tick:

| Wetterlage | Totband | Begründung |
|---|---|---|
| stabil | 0,5 × konfiguriert | Eng regeln kostet nichts, wenn sich wenig ändert – und verschenkt am wenigsten. |
| wechselhaft | bis 3 × konfiguriert | Sonst jagt der Regler jedem Sprung hinterher; die Start/Stopp-Zyklen kosten mehr, als das weitere Totband verschenkt. |

Begrenzt durch `deadband_min_w` / `deadband_max_w`, abschaltbar über
`adaptive_deadband`. Der wirksame Wert steht live im Snapshot und in der
Diagnose – der Kompromiss ist damit sichtbar, nicht implizit.

### Kennzahl „verschenkter Solarstrom“

`wasted_power()` = Einspeisung über dem Netz-Sollwert. Bei
`grid_target_w = 0` also schlicht die aktuelle Einspeiseleistung. Sie geht
als Feld `waste_power` in die Zeitreihen und erscheint

* live im Dashboard (mit Klartext-Begründung, warum der Überschuss liegen
  blieb – „Wallbox: warte auf Überschuss“, „alle Lasten am Maximum“ …),
* historisch in der Statistik (`wasted_kwh`, Anteil an der Erzeugung und
  Gegenwert in Euro) sowie als eigener Verlaufs-Chart.

Damit ist „keinen Watt verschenken“ messbar statt behauptet – und beim
Tuning sieht man sofort, welche Schwelle im Weg steht.

## Solarprognose aktiv nutzen (services/forecast.py)

Die Prognose ist kein Anzeige-Widget, sondern beeinflusst Entscheidungen:

* **Wolkendurchzug** (`is_transient_dip`): Erzeugung liegt deutlich unter der
  Prognose, die Prognose für die nächste halbe Stunde ist aber weiter hoch →
  laufende Lasten werden gehalten statt abgeschaltet. Ein Neustart kostet
  Start-Hysterese und einen Ladezyklus; das Halten ist billiger. Zeitlich
  begrenzt (`forecast_hold_max_s`, Standard 15 min), damit eine falsche
  Prognose nicht stundenlang Netzstrom zieht.
* **Zielladung** (`expected_surplus_kwh`): Deckt die erwartete Sonne den
  Bedarf bis zur Deadline, bleibt es bei 15 % Zeitreserve – warten lohnt
  sich, jede gewartete Stunde bringt Solarstrom. Reicht sie nicht, wächst
  die Reserve auf bis zum Dreifachen, die Netzladung beginnt also
  vorausschauend früher.

Ohne konfigurierte oder mit veralteter Prognose (> 6 h) verhält sich die
Regelung exakt wie vorher.

## Treiber: was der Kern garantiert

* **Reconnect**: `ModbusConnection` und `HttpDevice` verbinden nach einem
  Transportfehler genau einmal neu und wiederholen die Anfrage. Ein
  Firmware-Update des Geräts wirkt damit wie ein kurzer Aussetzer.
* **Serialisierung**: Ein Lock je Modbus-Verbindung – Pflicht, Modbus
  verträgt keine parallelen Anfragen. Geräte, die mehrere logische Rollen
  hinter einer IP bündeln (Sungrow SH = Wechselrichter + Zähler + Batterie),
  teilen sich über den Verbindungs-Pool **eine** Session inklusive Lock.
* **Readback**: `write_register(..., verify=True)` liest zurück und meldet
  `CommandNotApplied`, wenn das Gerät den Wert stillschweigend verwirft.
  HTTP-Treiber prüfen analog über ihren Status-Endpunkt.
* **Plausibilität**: `validation.checked()` wirft bei physikalisch
  unmöglichen Werten – ein falsches Register fällt damit auf, statt still
  in die Regelung zu laufen. Nebenwerte (Temperaturen, Zählerstände) werden
  über `sanitized()` auf `None` gesetzt, statt das Gerät auszuwerfen.
* **Klartext-Fehler**: Modbus-Exception-Codes und aiohttp-Ausnahmen werden in
  Sätze übersetzt, die sagen, *was zu prüfen ist*.
* **Reifegrad**: `DriverMeta.maturity` (`stable`/`beta`/`experimental`) wird
  im Setup-Assistenten als Badge und Warnbanner angezeigt.

## Batterie: standardmäßig passiv

Hybrid-Wechselrichter (Sungrow SH, Fronius GEN24 Plus, Huawei SUN2000 +
LUNA2000, SMA Sunny Boy Storage, Kostal …) regeln ihre Batterie **selbst**
auf Nulleinspeisung. MinePower liest SoC und Leistung nur mit, um sie in der
Budgetrechnung zu berücksichtigen.

* Ein separates Batteriegerät ist dafür **nicht nötig** – die Hybrid-Treiber
  liefern `battery_soc`/`battery_power` in `InverterData` gleich mit, und der
  Loop baut daraus die Batterieanzeige. Der Assistent sagt das im
  Batterie-Schritt ausdrücklich und bietet „Überspringen“ an.
* Aktive Befehle (Zwangsladen/-entladen, Reserve-SoC) sind eine bewusst zu
  aktivierende Zusatzfunktion: `BatteryDriver.control_enabled()` prüft das
  Konfigurationsfeld `allow_active_control`, und der Loop schreibt zusätzlich
  nur bei gesetztem `manage_reserve`. Im Normalbetrieb bleibt die Batterie
  unangetastet.

## Vorzeichen-Konventionen

| Größe | + | − |
|---|---|---|
| `grid_power` | Netzbezug | Einspeisung |
| `battery_power` | Batterie lädt | Batterie entlädt |

## Lademodi (WallboxController)

| Modus | Verhalten |
|---|---|
| `pv_only` | nur Überschuss; Start-/Stopp-Hysterese, Min-Strom-Haltung |
| `min_pv` | garantierter Mindeststrom, Überschuss on top |
| `fast` | maximale Leistung |
| `schedule` | Zeitfenster (Schedules-Tabelle) → Volllast im Fenster |
| `target` | Deadline-Rechnung aus SoC/Kapazität + Prognose (siehe oben) |
| `price` | Volllast, wenn Preis ≤ Schwelle (Tibber/aWATTar), sonst `pv_only` |

## Downsampling

Rohdaten werden 90 Tage gehalten (`add_retention_policy`). Für längere
Zeiträume empfiehlt sich eine Timescale *Continuous Aggregate*, z. B.:

```sql
CREATE MATERIALIZED VIEW measurements_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time) AS bucket, source, field, avg(value) AS value
FROM measurements GROUP BY bucket, source, field;
SELECT add_continuous_aggregate_policy('measurements_hourly',
  start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');
```

## Frontend-Design-System

`src/theme.css` definiert Tokens (Farben, Abstände, Radien, Bewegung) für
hell und dunkel; `src/components/ui.tsx` die Bausteine (Card, Button,
Segment, Toggle, Banner, Badge, Metric, Bar, AnimatedValue …). Alle Seiten
verwenden ausschließlich diese – deshalb gibt es keinen Stilbruch zwischen
Dashboard, Assistent und Einstellungen.

Bewegung ist funktional: Zahlen zählen weich (`useAnimatedNumber`), der
Energiefluss zeigt Richtung, Stärke und Tempo der realen Leistung,
Zustandswechsel bekommen einen kurzen Impuls (`useFlashOnChange`). Alles
respektiert `prefers-reduced-motion`.

Fehlerzustände sind Teil des Layouts, nicht ein Sonderfall: Sicherheits-Stopp,
offline-Geräte, nicht übernommene Befehle und eine abgerissene
Live-Verbindung erscheinen als Banner am Kopf des Dashboards und zusätzlich
in der jeweiligen Gerätekachel.

Mobil wandert die Navigation in eine Tab-Leiste am unteren Rand (Daumenzone);
Bedienelemente sind dort mindestens 44 px hoch.
