# Treiber: Quellen, Prüfstand, Einschränkungen

Stand: Version 3.3.0. Für jeden Treiber steht hier, gegen welche Quelle er
geprüft wurde, was dabei herauskam und was offen ist.

**Nichts davon wurde in diesem Durchgang an Hardware geprüft.** Die Spalte
„Stand“ sagt deshalb nur, wie gut die Registerkarte bzw. die Schnittstelle
belegt ist:

| Stand | Bedeutung |
|---|---|
| Betrieb | läuft an einer Anlage im Dauerbetrieb (Angabe aus dem Treiber), zusätzlich gegen die Dokumentation abgeglichen |
| Doku | Adressen, Datentypen, Skalierung und Vorzeichen gegen Herstellerdokumentation oder Referenzimplementierung abgeglichen, mit nachgestellten Antworten getestet |
| Abgleich | Registeradressen gegen die Referenzimplementierung abgeglichen, sonst ungeprüft |
| unverifiziert | keine belastbare Quelle geprüft; nur mit gefälschter Gegenstelle getestet |

Ohne Hardware testen: Der Demo-Modus (`DEMO_MODE=true`) bringt simulierte
Geräte jeder Klasse mit (`sim_*`). Für die echten Treiber stellen die Tests
unter `backend/tests/test_driver_protocols.py`, `test_wallboxes.py`,
`test_generic_drivers.py` und `test_drivers.py` die Gegenstelle nach
(Modbus-Register, HTTP-Antworten, WebSocket).

## Was die Prüfung gefunden hat

| Treiber | Fund | Stand |
|---|---|---|
| Sungrow SH, Sungrow Batterie | Register 13022 (Batterieleistung) ist seit der Firmware vom Herbst 2024 vorzeichenbehaftet. Vorzeichenlos gelesen wurde aus −3000 W der Wert 62536, mit Entlade-Bit also 62 kW Entladung. | behoben: als int16 gelesen, Richtung aus Vorzeichen oder Zustandsbits. Beide Firmware-Stände getestet. |
| Sungrow Zähler (SG-Serie) | Register 5083 zählt Bezug positiv, 13010 (SH) Einspeisung positiv. Beide wurden negiert; bei der SG-Serie waren Bezug und Einspeisung vertauscht. | behoben |
| Sungrow SH | Gesamtertrag aus 5004 mit Faktor 1 gelesen. Beim SH trägt das Register 0,1 kWh und enthält die Batterieentladung. | behoben: PV-Erträge aus 13002/13003 |
| Sungrow Wallbox | Das Registerlayout (Block ab „Register-Basis“) ließ sich an keiner Quelle belegen. Nach der belegten Karte hätte es Start und Stopp vertauscht geschrieben. | neu geschrieben nach evcc |
| Sungrow SH, Zähler | Kennung „kein Wert“ (0x7FFFFFFF) wurde als Leistung weitergereicht und fiel erst als „unplausibel“ auf. | behoben: klare Meldung „Zähler nicht angeschlossen“ |
| my-PV | Status 9 wurde als „Fehler“ angezeigt. Beim AC•THOR heißt 9 „Betrieb“, beim ELWA „Setup“. Fehler liegen ab 200. | behoben |
| my-PV (HTTP) | Die Leistung wurde nur aus `power_elwa2` bzw. `power` gelesen. Der AC•THOR meldet `power_act`; dort kam 0 W an. | behoben, Schlüssel je Gerätefamilie |
| my-PV (Modbus) | Register 1074 wurde immer gelesen. Der AC ELWA-E hat es nicht; das Gerät galt als gestört. | behoben: Leistung aus 1000, 1074 nur zusätzlich |
| go-e | Freigabe mit `frc=0` (neutral). Im Eco- oder Zeitplanmodus lädt die Box dann trotz Freigabe nicht. | behoben: `frc=2` (ein) |
| go-e | Phasenzahl aus `psm` (Vorgabe). Ein einphasiges Fahrzeug an einer dreiphasigen Box wurde als dreiphasig gerechnet; bei „Auto“ als einphasig. | behoben: unter Last aus den Phasenströmen |
| Fronius | Ladestand aus `Site.StateOfCharge_Relative` gelesen. Das Feld gibt es dort nicht; der Ladestand blieb leer. | behoben: `Inverters.<n>.SOC` |
| Huawei | Speicherleistung aus 37760 gelesen. Dort steht der Ladestand; die Leistung liegt in 37765. | behoben |
| SMA | Mit Speicher enthält die AC-Leistung (30775) die Batterieentladung und erschien nachts als Sonnenstrom. | behoben: mit Speicher zählt die DC-Leistung (30773, 30961) |
| Tesla (BLE-Proxy) | Der Weckbefehl ging an `…/wake_up`. Diesen Pfad kennt nur die Fleet-API; der Proxy erwartet `…/command/wake_up`. | behoben |
| OCPP | Leistung je Phase: Jede Phase überschrieb die vorige, eine dreiphasige Ladung erschien mit einem Drittel. | behoben: Phasen werden summiert |
| OCPP | Ladeprofil vom Typ „Absolute“ ohne Startzeit. | behoben: `startSchedule` gesetzt |
| OCPP | Jede beliebige Kennung durfte sich verbinden; keine Anmeldung möglich. | behoben: nur eingerichtete Kennungen, optional Passwort (HTTP Basic, Sicherheitsprofil 1) |

## Wechselrichter, Zähler, Batterie

### Sungrow SH, SG, Zähler, Batterie

- **Quellen:**
  [mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant/blob/main/modbus_sungrow.yaml),
  [evcc sungrow-hybrid](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/sungrow-hybrid.yaml),
  [evcc sungrow-inverter](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/sungrow-inverter.yaml),
  [SunGather-Registerliste](https://github.com/bohdan-s/SunGather/blob/main/SunGather/registers-sungrow.yaml),
  [SungrowClient](https://github.com/bohdan-s/SungrowClient).
- **Geprüft:** 5017 DC-Leistung (u32, niederwertiges Wort zuerst), 5008
  Temperatur, 13001 Zustandsbits, 13002/13003 PV-Erträge (0,1 kWh), 13010
  Export (s32), 13022/13023 Batterieleistung und Ladestand, 13024/13025
  Zustand und Temperatur der Batterie, 13050–13052 EMS-Modus, Zwangsbefehl
  (0xAA/0xBB/0xCC) und Leistung, 13058/13059 Max-/Min-SoC, 5083 Zähler (SG).
- **Stand:** SH und Zähler am SH: Betrieb. SG und Zähler am SG: Doku.
  Batterie-Steuerung: Doku (Beta).
- **Einschränkungen:** Der WiNet-S verträgt nur wenige Anfragen je Sekunde;
  alle Sungrow-Geräte einer Adresse teilen sich deshalb eine Verbindung.
  Die Zwangsleistung (13052) ist je nach Modell in Watt oder Prozent
  dokumentiert; der Treiber schreibt Watt (am SH10RT so bestätigt, laut
  Quelle). Die Batterieleistung in Register 5214 (s32), die Sungrow
  inzwischen empfiehlt, wird nicht genutzt.

### Sungrow AC-Wallbox (AC011E, AC22E)

- **Quelle:** [evcc charger/sungrow.go](https://github.com/evcc-io/evcc/blob/master/charger/sungrow.go).
- **Geprüft:** 21202 Sollstrom (0,1 A), 21203 Phasen, 21210 Verfügbarkeit,
  21211 Fernsteuerung (0 Start, 1 Stopp), 21307 Leistung, 21309
  Vorgangsenergie, 21316 Zustand. Unit-ID 248.
- **Stand:** Doku. Einstellungen aus älteren Versionen („Register-Basis“)
  werden ignoriert.

### my-PV AC•THOR, AC ELWA-E, AC ELWA 2

- **Quellen:**
  [my-PV Documentation Controls AC ELWA-E](https://download.my-pv.com/acelwa_e/Documentation%20Controls%20AC%20ELWA-E%20EN230725.pdf),
  [Betriebsanleitung AC•THOR](https://solar-distribution.baywa-re.de/out/media/04-000472_acthor_doc_en_a0020900.pdf)
  (Statuscodes), [evcc charger/mypv.go](https://github.com/evcc-io/evcc/blob/master/charger/mypv.go).
- **Geprüft:** `/control.html?power=n`, `/data.jsn`; Register 1000
  Leistung (lesen = Ist-Leistung), 1001 Temperatur 0,1 °C, 1003 Status, 1004
  Zeitüberwachung 10–600 s, 1070 Steuerungstyp (1 HTTP, 2 Modbus TCP).
- **Stand:** Modbus TCP: Betrieb an einem Gerät der Familie (Angabe aus dem
  Treiber, ohne Modellbezeichnung). HTTP und die Unterschiede zwischen den
  Modellen: Doku. Der Schlüssel `power_ac9` des AC•THOR 9s ist nicht belegt.
- **Einschränkungen:** Das Gerät nimmt Sollwerte nur an, wenn sein
  Steuerungstyp passt. Register 1000 lässt sich nicht als Rückmeldung des
  Sollwerts lesen. Ohne erneuten Sollwert schaltet das Gerät nach der
  Zeitüberwachung ab (der Treiber setzt sie auf 600 s).

### Fronius (Solar API)

- **Quelle:** [evcc fronius-solarapi-v1](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/fronius-solarapi-v1.yaml),
  Fronius Solar API V1 (`GetPowerFlowRealtimeData`).
- **Geprüft:** `Site.P_PV` (nachts null), `Site.P_Grid` (+ Bezug),
  `Site.P_Akku` (+ Entladen, wird gedreht), `Inverters.<n>.SOC`,
  `E_Day`/`E_Total` in Wh.
- **Stand:** Doku. **Einschränkung:** `E_Day` liefert der GEN24 nicht.

### SMA

- **Quellen:** [evcc sma-inverter-modbus](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/sma-inverter-modbus.yaml),
  [evcc sma-hybrid](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/sma-hybrid.yaml).
- **Geprüft:** 30775 AC-Leistung (s32), 30773/30961 DC-Leistung, 30513
  Gesamtertrag (u64 Wh), 30535 Tagesertrag, 30845 Ladestand, 31393/31395
  Lade-/Entladeleistung, 30865/30867 Bezug/Einspeisung; Kennungen für
  „kein Wert“; Unit-ID 3; höherwertiges Wort zuerst.
- **Stand:** Doku.

### Huawei SUN2000 / LUNA2000

- **Quelle:** [evcc huawei-sun2000-hybrid](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/huawei-sun2000-hybrid.yaml).
- **Geprüft:** 32064 DC-Leistung, 32106/32114 Erträge (Faktor 0,01), 37113
  Zähler (> 0 = Einspeisung), 37765 Speicherleistung (> 0 = laden), 37760
  Ladestand (Faktor 0,1).
- **Stand:** Doku. **Einschränkung:** Der SUN2000 lässt nur eine
  Modbus-Verbindung zu und braucht nach dem Verbindungsaufbau eine Pause.

### Victron GX

- **Quelle:** [evcc victron-energy](https://github.com/evcc-io/evcc/blob/master/templates/definition/meter/victron-energy.yaml)
  (CCGX-Registerliste, Unit-ID 100).
- **Geprüft:** 808–813 PV an AC, 850 PV an DC, 820–822 Netz je Phase
  (int16), 842 Batterieleistung (+ laden), 843 Ladestand.
- **Stand:** Doku.

### SunSpec (Wechselrichter, Zähler)

- **Quelle:** SunSpec-Modelle 1, 101–103, 111–113, 201–204, 211–214, 802.
- **Geprüft:** Kennung „SunS“ an 40000/50000/0, Modellkette, Leistung mit
  Skalierungsfaktor, „nicht implementiert“ (0x8000).
- **Stand:** Abgleich der Offsets. **Einschränkung:** Hersteller legen
  Modell 802 unterschiedlich aus; der Ladestand wird nur übernommen, wenn er
  plausibel ist. Die Zählrichtung des Zählers ist je Hersteller verschieden
  und deshalb umschaltbar.

### Shelly 3EM / Pro 3EM, Shelly-Relais

- **Quelle:** Shelly API Gen1 (`/status`, `/relay/0`) und Gen2+
  (`EM.GetStatus`, `Switch.GetStatus`, `Switch.Set`).
- **Stand:** Doku. **Einschränkungen:** Geräte mit Passwort (Digest) werden
  nicht unterstützt. Der Pro 3EM im Einphasen-Profil (`EM1`) wird nicht
  gelesen.

### Generische Treiber (HTTP/JSON, MQTT, Modbus-Registerprofil, Home Assistant, Tasmota)

- Adressen, Pfade und Skalierung gibt der Betreiber vor.
- **Stand:** unverifiziert an Geräten; die Umrechnung ist getestet
  (`tests/test_generic_drivers.py`).

## Wallboxen und Fahrzeuge

### Tesla (BLE-Proxy oder Fleet-API)

- **Quellen:** [TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy)
  (README und `internal/api/routes/routes.go`), Tesla Fleet API.
- **Geprüft:** `GET …/vehicle_data?endpoints=charge_state`,
  `POST …/command/{charge_start, charge_stop, set_charging_amps,
  set_charge_limit, wake_up}`; Fleet: `POST …/wake_up`; Basisadressen EU/NA.
- **Stand:** BLE-Proxy: Betrieb. Fleet-API: Doku.
- **Einschränkungen:**
  - Der Proxy bestätigt einen Befehl, sobald er eingereiht ist, nicht wenn
    das Fahrzeug ihn ausgeführt hat (`wait=true` wird nicht genutzt, weil
    der Befehl dann bis zu einer halben Minute blockiert). Ob er wirkt,
    zeigt der nächste gelesene Zustand.
  - Fleet-API: Die meisten Fahrzeuge verlangen signierte Befehle (Vehicle
    Command Protocol). Direkt an die Fleet-API geschickte Befehle werden
    dann abgelehnt; nötig ist ein `tesla-http-proxy` als Basisadresse.
    Abfragen und Befehle sind bei Tesla kostenpflichtig; der Treiber fragt
    höchstens einmal je Minute ab.

### go-e Charger (API v2)

- **Quellen:** [go-eCharger-API-v2](https://github.com/goecharger/go-eCharger-API-v2),
  [evcc charger/go-e.go](https://github.com/evcc-io/evcc/blob/master/charger/go-e.go).
- **Geprüft:** `car` (1 frei, 2 lädt, 3 wartet, 4 fertig, 5 Fehler), `amp`,
  `nrg[11]` Gesamtleistung, `nrg[4..6]` Phasenströme, `wh`, `psm` (1/2),
  `frc` (1 aus, 2 ein).
- **Stand:** Doku.

### OCPP 1.6J

- **Quelle:** OCPP 1.6 (BootNotification, StatusNotification, MeterValues,
  Start/StopTransaction, RemoteStart/StopTransaction, SetChargingProfile),
  Sicherheitsprofil 1 aus dem OCPP-Security-Whitepaper.
- **Geprüft:** mit einer echten WebSocket-Verbindung im Test: Anmeldung,
  Zustandsmeldung, Transaktion, Messwerte, Ladeprofil.
- **Stand:** Doku. **Einschränkungen:** OCPP 2.0.1 fehlt. Ohne
  SmartCharging in der Box lässt sich der Strom nicht vorgeben. Ohne
  Passwort gilt Sicherheitsprofil 0 (siehe SECURITY.md). TLS (Profil 2)
  gibt es nicht; dafür einen Proxy davorsetzen.

### KEBA KeContact P30 (UDP)

- **Quelle:** KEBA „KeContact P30 UDP Programmers Guide“.
- **Geprüft:** `report 2` (State, Plug, Curr user), `report 3` (P in mW,
  E pres in 0,1 Wh), `curr <mA>`, `ena 0|1`, Port 7090.
- **Stand:** Doku (aus der Erinnerung an das Dokument, online nicht erneut
  abgerufen).

### Modbus-Wallboxen

cFos Power Brain, Alfen Eve, Webasto Next, Vestel EVC04, Bender CC612/613,
Phoenix CHARX und EV-ETH, KEBA (Modbus), Heidelberg Energy Control,
Amperfied, Mennekes Amtron Compact, ABL eMH1, EM2GO, innogy eBox, Siemens
VersiCharge, SolaX EVC, Peblar, Pulsares, KSE, OBO Bettermann.

- **Quelle:** die jeweilige Datei unter
  [evcc/charger](https://github.com/evcc-io/evcc/tree/master/charger).
- **Geprüft (3.3.0, evcc-Stand 17.11.2026):** Adressen, Registertyp,
  Datentyp und Wortreihenfolge aller 20 Profile einzeln; kein 0/1-Versatz.
- **Korrigiert in 3.3.0:**
  - Alfen: Status E heißt „kein Fahrzeug“, nicht Fehler.
  - Bender: jeder Messwert ≥ 0x80000000 ist ungültig. Modell (Reg. 142)
    wird einmal gelesen: Amtron 4You/4Business bekommen nie Zugriffe auf
    720/730/740 (die Box trennt sonst die Verbindung), Ladestand dort aus
    2200 ab Protokoll 1.6. Alter Registersatz: Leistung aus den Strömen.
  - KEBA: Lebenszeichen alle Failsafe-Timeout/2 (Reg. 1602), P40-Freigabe
    aus 5004 ≠ 0, P30-Freigabe lokal gemerkt. Modell wählbar für P40 mit
    Produktcode 0.
  - Webasto/Vestel: Lebenszeichen aus dem Kommunikations-Timeout
    (Reg. 2002): Webasto /2, Vestel /4, mindestens 1 s.
  - Heidelberg/Amperfied (257, ms), EM2GO (89, s), Pulsares (0x61, Code),
    OBO (28, ms): lesendes Lebenszeichen im Takt Timeout/2 bzw. /4, auch
    wenn der Regeltakt langsamer ist.
  - Mennekes Compact: Status 6 (Fallback) wird mit 0 A und Freigabe
    zurückgeholt.
  - ABL: Freischalten (0x05 = 0xA1A1) wird zweimal gesendet.
- **Offen (unsicher, nicht geändert):** Webasto Statuscode 2 und FC06 statt
  FC16 auf 5004; Einheit der Sitzungsenergie bei EV-ETH (132) und Mennekes
  Compact (0x0B02); EM2GO-FW-1.4-Stopp-Workaround; 100 ms Abstand bei
  Heidelberg.
- **Stand:** Abgleich mit Referenz, nicht an Hardware geprüft.

### HTTP-, MQTT-, WebSocket- und Cloud-Wallboxen

Hardy Barth eCB1/Salia, SMA EV Charger, EVSE-WiFi, openWB Pro, openWB 1.9
(MQTT), Fronius Wattpilot, Easee, Zaptec.

- **Geprüft (3.3.0):** eCB1, SMA EV Charger, openWB Pro, openWB 1.9 und
  Zaptec passen zu evcc; EVSE-WiFi zur Herstellerdoku.
- **Korrigiert in 3.3.0:** Salia-Leistung kommt in 0,1 W (wurde 10-fach
  angezeigt); Stopp setzt erst 0 A, dann Pause. EVSE-WiFi-Hinweis: auch
  „Remote“ muss aus sein.
- **Offen:** Easee-Phasenzuordnung T3–T5 nur für TN-Netze plausibel.
- **Stand:** Tests mit gefälschter Gegenstelle bestehen, nicht an Hardware
  geprüft. Easee und Zaptec hängen an den Cloud-Diensten der Hersteller und
  deren Abfragegrenzen.

## Wärmepumpe SG-Ready, Schaltaktoren

- Schalten zwei Ausgänge (Shelly Gen2+, Home Assistant) bzw. ein Relais.
- Seit 3.4 als Wärmepumpe gekennzeichnet: selbstregelnd, Verdichterschutz
  10 min Lauf / 5 min Pause als Vorgabe.
- **Stand:** unverifiziert an Geräten.

## Wärmepumpen und Klimaanlagen (3.4)

Alle drei Treiber schalten nur zurück, was MinePower selbst eingeschaltet
oder verstellt hat. Ein Gerät, das der Nutzer von Hand eingeschaltet hat,
bleibt an. Leistung ohne Freigabe (Heizen nach Raumtemperatur) gilt als
Eigenbetrieb, nicht als Ungehorsam.

| Treiber | Quelle | Was bei Überschuss passiert | Stand |
|---|---|---|---|
| Viessmann (`viessmann_vicare`) | Viessmann Developer Portal, Endpunkte `/iot/v2/features/…` (Umstellung 30.04.2025), Feature-Namen `heating.dhw.sensors.temperature.dhwCylinder` (neu) bzw. `…hotWaterStorage` (alt) | `heating.dhw.oneTimeCharge` activate/deactivate oder `heating.dhw.temperature.main` setTargetTemperature | Doku |
| Homey Pro (`homey_pro`) | Homey Web API: `GET /api/manager/devices/device/<id>`, `PUT …/capability/<cap>` | `onoff` oder `target_temperature` | unverifiziert |
| Daikin Onecta (`daikin_onecta`) | Daikin Developer Portal (Onecta-API): `GET /v1/gateway-devices`, `PATCH …/management-points/{mp}/characteristics/{name}`; Token `idp.onecta.daikineurope.com/v1/oidc/token` (Refresh-Token rotiert) | `onOffMode`, `operationMode`, `temperatureControl` mit `path`, `powerfulMode` | Doku |
| Daikin (`daikin_local`) | lokale Adapter-Schnittstelle `/aircon/get_control_info`, `set_control_info`, `get_sensor_info` (Airbase mit Präfix `/skyfi`) | `pow=1` mit Betriebsart/Soll oder nur `stemp` | unverifiziert |

Einschränkungen:

- **Viessmann:** Cloud, Internet nötig; rund 1450 Abfragen am Tag frei,
  daher Abfrage höchstens alle 90–900 s (Vorgabe 120 s). Keine
  Momentanleistung in der API – läuft der Verdichter, gilt die
  eingestellte Leistungsaufnahme. Refresh-Token über den Knopf „Mit
  Viessmann verbinden“ oder `backend/tools/vicare_token.py` (gilt 180 Tage). Nicht jedes Gerät bietet
  die Einmalladung an (Verbindungstest zeigt es).
- **Homey:** API-Schlüssel mit Recht „Geräte steuern“. Leistung aus
  `measure_power` des Geräts oder eines eigenen Messgeräts, sonst
  Nennleistung, solange an.
- **Daikin (lokal):** nur ältere Adapter mit lokaler Schnittstelle. Neuere
  Adapter (BRP069C) und Altherma über `daikin_onecta`. Keine
  Leistungsmessung: Verdichterfrequenz > 0 bzw. eingeschaltet = Nennleistung.
- **Daikin Onecta:** Cloud, 200 Anfragen am Tag je Client-Anwendung. Abfrage
  alle 600–3600 s (Vorgabe 900 s), ein gemeinsamer Abruf für alle Geräte
  eines Kontos, Befehle nur bei Änderung, danach 2 min keine Abfrage. Bei
  HTTP 429 Pause nach `Retry-After` (sonst 30 min, verdoppelt bis 12 h).
  Das Refresh-Token rotiert bei jeder Erneuerung; das aktuelle liegt
  verschlüsselt unter `<data>/oauth/`. Redirect-URI muss https sein und darf
  nicht localhost heißen. Keine Momentanleistung: an/Boost = Nennleistung.

## Abfrage, Zeitlimits, Wiederverbinden

| Anbindung | Zeitlimit | Verhalten bei Ausfall |
|---|---|---|
| Modbus TCP | 5 s (Sungrow), sonst 3 s | einmal neu verbinden und wiederholen; lehnt das Gerät bewusst ab, kein zweiter Versuch |
| HTTP | 8 s | eine dauerhafte Verbindung je Gerät, einmal neu aufbauen |
| Tesla | 15 s je Anfrage, Abfrage alle 20 s (Proxy) bzw. 60 s (Fleet) | „nicht erreichbar“ ist kein Fehler; der letzte Zustand gilt 2 min weiter |
| OCPP | 15 s je Befehl | bleibt der Heartbeat 900 s aus, gilt die Verbindung als tot |

Der Regelkreis nimmt ein Gerät nach wiederholten Fehlern aus der Regelung
und fragt es mit wachsendem Abstand weiter ab. Fehlt die Netzmessung,
stoppt er alle Lasten (Sicherheitsstopp).

## Werte, die fehlen

Treiber liefern für fehlende Nebenwerte `None` (in der Oberfläche „–“),
keine Null: Temperatur, Ladestand, Erträge, Spannung, Phasenzahl. Ein
unplausibler Hauptwert (Leistung außerhalb des Messbereichs) ist ein Fehler
mit Klartext, kein Messwert.

## Offen

- Kein Treiber wurde in diesem Durchgang an Hardware geprüft.
- Die 20 Modbus-Wallbox-Profile sind nur auf Registeradressen abgeglichen.
- HTTP-, MQTT- und Cloud-Wallboxen sowie SG-Ready sind nicht gegen
  Dokumentation geprüft.
- my-PV AC•THOR 9s: Summe aus `power_act` und `power_ac9` ist eine Annahme.
- Sungrow: Zwangsleistung in Watt oder Prozent je nach Modell.
- Tesla Fleet-API: signierte Befehle nur über einen vorgeschalteten Proxy.
