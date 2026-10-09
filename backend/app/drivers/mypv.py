"""my-PV AC•THOR / AC ELWA-E / AC ELWA 2: modulierender Heizstab.

Das ist die feinste Stellgröße im ganzen System: stufenlos von ~0 W bis zur
Nennleistung. Deshalb ist dieses Gerät der bevorzugte Abnehmer für
Rest-Überschüsse, die für eine Wallbox zu klein sind (Lückenfüller-Logik im
Regelkern, siehe core/regulation.py).

Zwei wählbare Steuerwege (im Setup umschaltbar):

* HTTP-API   – GET /control.html?power=W  (Leistung setzen),
               GET /data.jsn              (Status lesen).
* Modbus TCP – Register 1000 (R/W) = Leistung in W setzen,
               1001 Temp1 (0,1 °C), 1003 Status, 1074 Ist-Leistung.
               (Registerbelegung gemäß my-PV 'AC ELWA 2 – Documentation of
               Controls'; Register 1000 ist ausdrücklich für häufiges
               Schreiben freigegeben.)
               Achtung: Register 1000 zurückzulesen liefert NICHT den zuletzt
               geschriebenen Sollwert, sondern was das Gerät daraus gemacht
               hat. Es taugt deshalb nicht zur Übernahmeprüfung – Begründung
               mit Messwerten in _write_modbus.

WICHTIG: Das Gerät nimmt externe Sollwerte nur an, wenn sein Steuerungstyp
passend gesetzt ist (my-PV-Setup → 'HTTP' bzw. 'Modbus TCP'). Solange der
Status 'No Control' meldet, werden Vorgaben ignoriert – der Treiber erkennt
genau das und meldet es im Klartext, statt stumm ins Leere zu regeln.
"""
from __future__ import annotations

from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    Maturity,
    WaterHeaterData,
    WaterHeaterDriver,
)
from .http_util import HttpDevice
from .modbus_util import ModbusConnection, s16, u16
from .registry import register
from .validation import DeviceRejected, checked, sanitized

# Modbus-Register (0-basiert = 'reale' my-PV-Adresse)
REG_POWER_SET = 1000     # R/W Leistung [W]
REG_TEMP1 = 1001         # R   Temp1 [0,1 °C]
REG_STATUS = 1003        # R   1=No Control 2=Heat 3=Standby 4=Boost 5=Heat finished
REG_POWER_TIMEOUT = 1004  # R/W Watchdog [10–600 s]
REG_CONTROL_TYPE = 1070  # R/W 1=HTTP 2=Modbus TCP
REG_POUT1 = 1074         # R   Ist-Leistung Heizstab [W]

STATUS_LABELS = {
    1: "No Control – Gerät nimmt keine externen Sollwerte an",
    2: "Heizen",
    3: "Standby",
    4: "Boost (eigenes Programm des Geräts)",
    5: "Heizen abgeschlossen",
    9: "Fehler",
}
STATUS_HEATING = {2, 4}
STATUS_NO_CONTROL = 1
#: 'Boost' heißt bei my-PV: Das Gerät heizt nach eigenem Programm mit
#: Netzstrom – Warmwasser-Sicherstellung (Mindesttemperatur in einem
#: Zeitfenster), Legionellenschutz oder ein am Gerät gestarteter Boost.
#: Externe Sollwerte (Register 1000) wirken währenddessen nicht.
STATUS_DEVICE_BOOST = 4
DEVICE_BOOST_TEXT = (
    "Eigenes Programm des Geräts (Warmwasser-Sicherstellung, Legionellenschutz "
    "oder Boost am Gerät)"
)
CONTROL_TYPE_MODBUS = 2

NO_CONTROL_HINT = (
    "Das my-PV-Gerät steht auf 'No Control' und ignoriert externe Sollwerte. "
    "Im my-PV-Setup den Steuerungstyp auf den hier gewählten Weg stellen "
    "(HTTP bzw. Modbus TCP) – sonst heizt es nie mit PV-Überschuss."
)


@register
class MyPvAcThor(WaterHeaterDriver):
    meta = DriverMeta(
        id="mypv_acthor",
        name="my-PV AC•THOR / AC ELWA-E / ELWA 2",
        category=DeviceCategory.WATER_HEATER,
        description="Stufenlos modulierender Warmwasser-Heizstab – die feinste Stellgröße im "
                    "System und damit der ideale Abnehmer für kleine Restüberschüsse. "
                    "Steuerweg wählbar (HTTP oder Modbus TCP). Das Gerät muss in seinem Setup "
                    "auf den passenden externen Steuerungstyp gestellt sein.",
        capabilities={"modulation", "fine_modulation", "temperature", "write_test", "own_program"},
        own_program_hint="Warmwasser-Sicherstellung im Webinterface des Geräts anpassen.",
        maturity=Maturity.STABLE,
        notes=(
            "An einem AC•THOR/ELWA über Modbus TCP im Dauerbetrieb erprobt.\n\n"
            "Geräteprogramm: Meldet das Gerät 'Boost', heizt es nach seiner eigenen "
            "Einstellung – meist Warmwasser-Sicherstellung (heizt mit Netzstrom, wenn das "
            "Wasser im Zeitfenster unter der Mindesttemperatur liegt) oder Legionellenschutz. "
            "MinePower kann das nicht abschalten, zählt die Leistung aber nicht als Überschuss "
            "und zeigt nur 'heizt per Geräte-App/-Programm'.\n\n"
            "Abhilfe: im ELWA-Webinterface die Zeitfenster bzw. die Mindesttemperatur der "
            "Warmwasser-Sicherstellung anpassen oder sie abschalten. Den Legionellenschutz "
            "bewusst lassen oder in die Mittagszeit legen.\n\n"
            "Wärmepuffer: Soll das Wasser mit Überschuss über die Zieltemperatur heizen "
            "('Mit Überschuss heizen bis'), muss die Maximaltemperatur im Gerät das erlauben."
        ),
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.70",
                        help="IP des my-PV-Geräts im lokalen Netz."),
            ConfigField(key="control_method", label="Steuerweg", type=FieldType.SELECT, default="http",
                        options=[{"value": "http", "label": "my-PV HTTP-API"},
                                 {"value": "modbus_tcp", "label": "Modbus TCP"}],
                        help="HTTP: /control.html – Gerät im Setup auf Steuerungstyp 'HTTP' stellen. "
                             "Modbus TCP: Port 502 – Gerät auf 'Modbus TCP' stellen. "
                             "Modbus ist etwas schneller und liefert den Steuerstatus direkt mit."),
            ConfigField(key="rated_power", label="Nennleistung (W)", type=FieldType.NUMBER, default=3000,
                        help="AC•THOR: 3000 W, AC•THOR 9s: 9000 W, AC ELWA-E/ELWA 2: ~3500 W."),
            ConfigField(key="min_power_w", label="Kleinste Stufe (W)", type=FieldType.NUMBER, default=100,
                        required=False,
                        help="Ab dieser Leistung heizt das Gerät sinnvoll. Je kleiner, desto "
                             "feiner lassen sich Restüberschüsse verwerten (my-PV: ~100 W)."),
            ConfigField(key="modbus_port", label="Modbus-Port", type=FieldType.NUMBER, default=502, required=False,
                        help="Nur für Modbus TCP. Standard 502."),
            ConfigField(key="unit_id", label="Modbus Unit-ID", type=FieldType.NUMBER, default=1, required=False,
                        help="Nur für Modbus TCP. Standard 1 (my-PV-Gerätenummer)."),
            ConfigField(key="verify_ssl", label="SSL-Zertifikat prüfen", type=FieldType.BOOLEAN, default=False,
                        required=False, help="Nur für HTTP. Aus lassen – my-PV nutzt selbstsignierte Zertifikate."),
        ],
    )

    modulating = True

    #: Sollwert alle 2 Minuten auffrischen, auch wenn er unverändert ist.
    #:
    #: Das Gerät verwirft die externe Vorgabe, wenn länger als der Watchdog
    #: (Register 1004, hier auf 600 s gesetzt) kein Schreibbefehl kommt, und
    #: heizt dann wieder nach eigener Logik. Der Control-Loop schickt einen
    #: unveränderten Wert aber nur einmal – ohne diese Auffrischung lief der
    #: Heizstab nach dem Abschalten irgendwann von selbst wieder an.
    #: 120 s lassen reichlich Luft für ausgefallene Takte.
    resend_interval_s = 120.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.host = str(config.get("host", "")).strip()
        self.rated_power = float(config.get("rated_power") or 3000)
        self.min_power_w = float(config.get("min_power_w") or 100)
        self.method = config.get("control_method", "http")
        self.http = HttpDevice(
            f"http://{self.host}",
            verify_ssl=bool(config.get("verify_ssl", False)),
            name="my-PV",
        )
        self._conn: ModbusConnection | None = None
        self._last_status: int | None = None

    # ---------------------------------------------------------------- HTTP

    async def _read_http(self) -> WaterHeaterData:
        d = await self.http.get_json("/data.jsn")
        power = _f(d.get("power_elwa2") if d.get("power_elwa2") is not None else d.get("power"))
        status = int(_f(d.get("status")))
        self._last_status = status or None
        temp = sanitized("water_temperature", _f(d.get("temp1")) / 10.0, zero_is_none=True)
        return WaterHeaterData(
            power=checked("heater_power", power, source="my-PV Ist-Leistung"),
            temperature_c=temp,
            is_on=power > 0,
            device_mode=DEVICE_BOOST_TEXT if status == STATUS_DEVICE_BOOST else None,
            extra=self._status_extra(status),
        )

    async def _write_http(self, watts: int) -> None:
        # Steht das Gerät auf 'No Control", quittiert /control.html trotzdem
        # oft mit einer unauffälligen 200-OK-Antwort ohne 'error"/'no control"
        # im Text – der reine Textabgleich unten würde das also nicht bemerken.
        # Deshalb zusätzlich den zuletzt gelesenen Gerätestatus prüfen (wie im
        # Modbus-Pfad): Erst wenn der wirklich 'No Control" meldet, ablehnen.
        # Ohne diese Prüfung merkt sich der Control-Loop einen Sollwert als
        # 'erfolgreich gesendet", obwohl das Gerät ihn nie angenommen hat –
        # und wiederholt ihn danach nicht mehr, auch nachdem der Nutzer die
        # Steuerungsart am Gerät korrigiert hat.
        if self._last_status == STATUS_NO_CONTROL:
            raise DeviceRejected(NO_CONTROL_HINT)
        # Korrekter my-PV-Endpunkt: /control.html (nicht /control)
        body = await self.http.get_text("/control.html", {"power": watts})
        low = body.lower()
        if "error" in low or "no control" in low:
            raise DeviceRejected(NO_CONTROL_HINT)

    # ---------------------------------------------------------------- Modbus

    def _connection(self) -> ModbusConnection:
        if self._conn is None:
            self._conn = ModbusConnection(
                host=self.host,
                port=int(self.config.get("modbus_port") or 502),
                unit_id=int(self.config.get("unit_id") or 1),
                name="my-PV",
            )
        return self._conn

    async def _read_modbus(self) -> WaterHeaterData:
        conn = self._connection()
        # Blocklesung 1000..1003 (Einzelregister-Reads sind bei my-PV unzuverlässig)
        head = await conn.read_holding(REG_POWER_SET, 4)   # power_set, temp1, tmax, status
        pout = await conn.read_holding(REG_POUT1, 2)        # Ist-Leistung
        status = u16(head, 3)
        self._last_status = status
        return WaterHeaterData(
            power=checked("heater_power", float(u16(pout, 0)), source="my-PV Register 1074"),
            temperature_c=sanitized("water_temperature", s16(head, 1) / 10.0, zero_is_none=True),
            is_on=status in STATUS_HEATING,
            device_mode=DEVICE_BOOST_TEXT if status == STATUS_DEVICE_BOOST else None,
            extra=self._status_extra(status),
        )

    async def _write_modbus(self, watts: int) -> None:
        # Register 1000 wird BEWUSST nicht zurückgelesen und verglichen.
        #
        # Die Annahme 'was ich schreibe, lese ich zurück' trifft hier nicht zu:
        # Register 1000 spiegelt nicht den Sollwert, sondern was das Gerät
        # daraus macht. Beobachtet im Feldeinsatz:
        #   geschrieben 3000 → gelesen 2800   (auf reale Heizstableistung begrenzt)
        #   geschrieben 2799 → gelesen 0      (Thermostat/Zieltemperatur greift)
        #   geschrieben 1160 → gelesen 1385   (mehr als angefordert!)
        # Der letzte Fall schließt eine reine Sollwert-Rückmeldung aus – ein
        # Echo kann nicht größer sein als das Geschriebene. Jeder Vergleich
        # gegen dieses Register erzeugt deshalb Fehlalarme im Minutentakt,
        # während das Gerät völlig korrekt arbeitet.
        #
        # Ob das Gerät externe Sollwerte überhaupt annimmt, sagt das
        # Statusregister eindeutig ('No Control') – das ist die verlässliche
        # Prüfung, und die bleibt.
        if self._last_status == STATUS_NO_CONTROL:
            raise DeviceRejected(NO_CONTROL_HINT)
        await self._connection().write_register(REG_POWER_SET, watts)

    def _status_extra(self, status: int) -> dict:
        label = STATUS_LABELS.get(status)
        return {"Gerätestatus": label} if label else {}

    # ---------------------------------------------------------------- Interface

    async def connect(self) -> None:
        if self.method == "modbus_tcp":
            conn = self._connection()
            await conn.connect()
            # Watchdog großzügig setzen (max 600 s), damit ein konstanter
            # Sollwert (Dedup) das Gerät nicht abschaltet – einmalig, Register
            # 1004 ist nicht für Dauer-Writes gedacht.
            try:
                cur = await conn.read_holding(REG_POWER_TIMEOUT, 1)
                if u16(cur, 0) != 600:
                    await conn.write_register(REG_POWER_TIMEOUT, 600)
            except Exception:  # noqa: BLE001 – unkritisch
                pass
        else:
            await self.http.connect()

    async def disconnect(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        await self.http.close()

    async def read_data(self) -> WaterHeaterData:
        if self.method == "modbus_tcp":
            return await self._read_modbus()
        return await self._read_http()

    async def set_power(self, watts: float) -> None:
        # Auf die konfigurierte Nennleistung begrenzen. Begrenzt das Gerät
        # zusätzlich auf seinen realen Heizstab, korrigiert sich das von
        # selbst: Der Regelkreis rechnet mit der GEMESSENEN Leistung
        # (Register 1074), nicht mit dem Wunschwert.
        value = max(0, min(int(watts), int(self.rated_power)))
        if self.method == "modbus_tcp":
            await self._write_modbus(value)
        else:
            await self._write_http(value)

    async def _probe(self) -> dict:
        values = await super()._probe()
        values["Steuerweg"] = "Modbus TCP" if self.method == "modbus_tcp" else "HTTP"
        values["Kleinste Stufe"] = f"{self.min_power_w:.0f} W"
        return values

    def _warnings(self, values: dict) -> list[str]:
        out = super()._warnings(values)
        if self._last_status == STATUS_NO_CONTROL:
            out.append(NO_CONTROL_HINT)
        return out


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
