# Neuen Treiber hinzufügen

Neue Geräte werden **ohne Änderungen am Kern** ergänzt: eine Datei unter
`backend/app/drivers/` (oder ein Unterpaket), Klasse mit `@register` –
fertig. Die Registry lädt beim Start alle Module unter `app/drivers/`
automatisch; externe Plugins können zusätzlich als `.py` in ein nach
`/app/plugins` gemountetes Volume gelegt werden.

> **Erst prüfen: Geht es auch mit SunSpec?** Kostal, GoodWe, SolarEdge,
> Delta, SMA (Tripower X) und weitere sprechen den SunSpec-Modbus-Standard.
> Der Treiber `sunspec_inverter` / `sunspec_meter` fragt das Gerät selbst
> nach seinem Registeraufbau und ist damit unempfindlich gegen
> Firmware-Updates. Eine eigene Registerkarte lohnt sich nur, wenn ein Gerät
> kein SunSpec kann oder herstellerspezifische Werte gebraucht werden.

## 1. Interface wählen

| Kategorie | Basisklasse | Pflichtmethoden |
|---|---|---|
| Wechselrichter | `InverterDriver` | `read_data() -> InverterData` |
| Netzzähler | `MeterDriver` | `read_data() -> MeterData` |
| Wallbox / Fahrzeug | `WallboxDriver` | `read_data()`, `set_current(a)`, `start_charging()`, `stop_charging()` |
| Warmwasser | `WaterHeaterDriver` | `read_data()`, `set_power(w)` |
| Batterie | `BatteryDriver` | `read_data()`; optional `set_mode()`, `set_reserve_soc()` |

Optional je nach Fähigkeit: `set_phases()` (capability `phase_switch`),
`set_charge_limit()` (capability `charge_limit`).

**Hybrid-Wechselrichter** liefern die Batteriewerte direkt in `InverterData`
(`battery_soc`, `battery_power`) – dann braucht der Nutzer *kein* separates
Batteriegerät. Setze zusätzlich die Capabilities `hybrid` und
`battery_monitor`.

## 2. Minimalbeispiel

```python
from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, MeterData, MeterDriver
from .http_util import HttpDevice
from .registry import register
from .validation import checked

@register
class MyMeter(MeterDriver):
    meta = DriverMeta(
        id="my_meter",                      # eindeutig, stabil (landet in der DB)
        name="Mein Zähler (HTTP)",
        category=DeviceCategory.METER,
        description="Kurzbeschreibung für den Setup-Assistenten.",
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.10",
                        help="Wird im GUI als Tooltip angezeigt – immer befüllen!"),
            ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=80),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{config.get('host', '')}", name="Mein Zähler")

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def read_data(self) -> MeterData:
        d = await self.http.get_json("/api/power")
        # + = Netzbezug, − = Einspeisung!
        return MeterData(grid_power=checked("grid_power", float(d["watts"]),
                                            source="Feld 'watts'"))
```

Der Setup-Assistent generiert das Formular automatisch aus `meta.fields`
und bietet „Verbindung testen“ (nutzt `test_connection()` der Basisklasse,
die `_probe()` ruft).

## 3. Pflichten

### Vorzeichen
`grid_power` + = Bezug; `battery_power` + = laden. Bei Herstellern mit
umgekehrter Konvention im Treiber drehen **und** ein Feld
„Richtung invertieren“ anbieten – Wandler werden regelmäßig verkehrt herum
montiert.

### Transport nie selbst bauen
`ModbusConnection` (`modbus_util.py`) bzw. `HttpDevice` (`http_util.py`)
verwenden. Sie liefern Lock, Reconnect, Timeout und Klartext-Fehler.
Insbesondere: **keine parallelen Modbus-Zugriffe** auf eine Verbindung.

Bündelt ein Gerät mehrere Rollen hinter einer IP (Wechselrichter + Zähler +
Batterie), `acquire_connection()`/`release_connection()` benutzen – dann
teilen sich alle Treiber eine Session inklusive Lock. Für Geräte, die nur
eine Modbus-Verbindung erlauben (Huawei, WiNet-S), ist das der Unterschied
zwischen „läuft“ und „fällt sporadisch aus“.

### Gelesene Werte prüfen
Ein falsches Register wirft keine Exception – es liefert plausibel
aussehenden Unsinn, den die Regelung dann verarbeitet.

```python
from .validation import checked, sanitized

pv = checked("pv_power", raw, source="Register 5017",
             scale_hint="Vergleichswert: Erzeugung in der Hersteller-App.")
temp = sanitized("battery_temperature", raw_temp)   # unplausibel → None
```

`checked()` für alles, was in die Regelung geht (Netzleistung, PV, SoC,
Ladeleistung). `sanitized()` für Nebenwerte – ein unplausibler Zählerstand
darf kein Gerät offline nehmen.

### Schreiben heißt nicht ausführen
Jede Stellgröße zurücklesen. Viele Geräte quittieren Befehle, die sie im
falschen Betriebsmodus gar nicht umsetzen.

```python
# Modbus: erledigt der Wrapper
await self.conn.write_register(addr, value, verify=True)   # → CommandNotApplied

# HTTP: nach kurzer Wartezeit Status gegenlesen
await self._set(amp=target)
await asyncio.sleep(0.5)
if abs(await self._read_amp() - target) > 1:
    raise CommandNotApplied("go-e hat den Sollstrom nicht übernommen. …")
```

Die Meldung gehört so formuliert, dass sie dem Nutzer sagt, **was zu tun
ist** („Gerät im Setup auf externen Steuerungstyp stellen“), nicht nur, dass
etwas schiefging.

### Verbindungstest aussagekräftig machen
`_probe()` überschreiben und **alle relevanten Live-Werte mit lesbaren
Beschriftungen** liefern; `_warnings()` für Hinweise, die kein Fehler sind:

```python
async def _probe(self) -> dict:
    values = await super()._probe()
    values["Steuerweg"] = "Modbus TCP"
    return values

def _warnings(self, values: dict) -> list[str]:
    out = super()._warnings(values)      # Reifegrad-Hinweise nicht verlieren
    if self._no_control:
        out.append("Gerät steht auf „No Control“ und ignoriert Sollwerte.")
    return out
```

Steuerbare Geräte erben aus `WallboxDriver`/`WaterHeaterDriver` bereits einen
`test_command()` (unkritischer Befehl + Readback). Überschreiben nur, wenn
das Standardverhalten nicht passt.

### Reifegrad ehrlich angeben
```python
meta = DriverMeta(..., maturity=Maturity.EXPERIMENTAL,
                  notes="Registerkarte aus der Herstellerdoku, nicht an "
                        "Hardware verifiziert. Live-Werte gegenprüfen.")
```
`beta`/`experimental` erzeugen im Assistenten ein sichtbares Warnbanner.
Lieber ehrlich kennzeichnen als jemanden blind vertrauen lassen.

### Fein modulierbare Lasten kennzeichnen
Stufenlose Verbraucher setzen `modulating = True` und die Capability
`fine_modulation`. Der Lückenfüller im Regelkern reicht ihnen Restüberschüsse
zu, die für grob gestufte Lasten zu klein sind. Relais melden
`modulating = False`.

### Sonstiges
* **Exceptions einfach durchwerfen** – der Loop fängt sie, zählt
  Fehlversuche, markiert offline und verbindet neu. Kein eigenes
  Retry-Gebastel im Treiber.
* **`connect()` idempotent** halten; wird bei Reconnects erneut gerufen.
* **Konfig-Werte defensiv parsen** (`int(config.get("port") or 502)`) –
  aus dem GUI kommen Strings und Zahlen gemischt.
* **Batterie nicht aktiv steuern**, außer der Nutzer schaltet es frei:
  `control_enabled()` prüfen, bevor geschrieben wird.

## 4. Testen

* Unit-Test mit gemocktem Transport (siehe `backend/tests/`). Die
  Regelungslogik ist hardwarefrei testbar – neue Verhaltensweisen dort
  ergänzen (`tests/test_surplus_use.py` als Vorlage).
* Live: Gerät im GUI anlegen → „Verbindung testen“ zeigt Rohwerte,
  „Testen inkl. Steuerbefehl“ zusätzlich das Readback.
* Die Diagnose-Seite (`/events` → 🔬) zeigt jeden Tick Rohdaten,
  Regel-Entscheidungen und nicht übernommene Befehle.
