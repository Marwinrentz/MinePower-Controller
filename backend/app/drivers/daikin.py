"""Daikin-Klimaanlage über die lokale WLAN-Schnittstelle (BRP069A/B, Airbase).

Die älteren Daikin-WLAN-Adapter (BRP069A4x/B4x, eingebaut in vielen
Split-Geräten bis etwa 2020) und die Airbase-Adapter (BRP15B61) sprechen
eine einfache HTTP-Schnittstelle im lokalen Netz:

    GET /aircon/get_control_info   pow, mode, stemp, shum, f_rate, f_dir …
    GET /aircon/get_sensor_info    htemp (innen), otemp (außen), cmpfreq …
    GET /aircon/set_control_info?pow=…&mode=…&stemp=…&shum=…&f_rate=…&f_dir=…

Antworten sind 'schluessel=wert'-Listen mit Komma, ``ret=OK`` bei Erfolg.
Beim Setzen müssen alle Grundwerte mitgeschickt werden; deshalb wird der
aktuelle Zustand gelesen und nur geändert, was nötig ist.

Was MinePower mit Überschuss tut (Konfiguration 'Bei Überschuss'):

* ``power``    – einschalten mit Betriebsart und Solltemperatur 'Überschuss'.
                Ohne Überschuss wieder aus – aber nur, wenn MinePower sie
                eingeschaltet hat.
* ``setpoint`` – läuft sie ohnehin, nur die Solltemperatur auf den
                Überschusswert stellen (Heizen höher, Kühlen tiefer) und
                danach zurück auf den vorherigen Wert.

Leistung: Die Schnittstelle liefert keine Momentanleistung. Läuft der
Verdichter (cmpfreq > 0) bzw. ist das Gerät an, gilt die eingestellte
Leistungsaufnahme.

Nicht unterstützt: Geräte mit dem neueren Onecta-Adapter (BRP069C4x,
nur Cloud) und Daikin Altherma (eigene Schnittstelle) – dafür SG-Ready
oder Home Assistant/Homey verwenden. Experimentell: nie an echter Hardware.
"""
from __future__ import annotations

import asyncio

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WaterHeaterData, WaterHeaterDriver
from .heat_common import ROOM_TEMP_MAX_C, ROOM_TEMP_MIN_C, kind_field, num, rated_field, room_temp, wants_on
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, DeviceRejected, checked, sanitized

MODES = {"0": "Automatik", "1": "Automatik", "7": "Automatik", "2": "Entfeuchten", "3": "Kühlen",
         "4": "Heizen", "6": "Lüften"}
MODE_CODES = {"heat": "4", "cool": "3", "auto": "0"}


def parse(text: str) -> dict[str, str]:
    """'ret=OK,pow=1,mode=4' → {'ret': 'OK', 'pow': '1', 'mode': '4'}"""
    out: dict[str, str] = {}
    for part in (text or "").strip().split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


@register
class DaikinLocal(WaterHeaterDriver):
    meta = DriverMeta(
        id="daikin_local", name="Daikin Klimaanlage (WLAN-Adapter, lokal)", category=DeviceCategory.WATER_HEATER,
        description="Split-Klimaanlage über den lokalen Daikin-WLAN-Adapter (BRP069A/B) oder Airbase: "
                    "bei Überschuss heizen oder kühlen.",
        capabilities={"relay", "climate", "write_test"},
        notes="Nur ältere Adapter mit lokaler Schnittstelle (BRP069A/B, Airbase). Onecta-Adapter (BRP069C) "
              "und Altherma werden nicht unterstützt. Ausgeschaltet wird nur, was MinePower eingeschaltet hat.",
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.40"),
            ConfigField(key="variant", label="Adapter", type=FieldType.SELECT, default="wifi", required=False,
                        options=[{"value": "wifi", "label": "WLAN-Adapter (BRP069A/B)"},
                                 {"value": "airbase", "label": "Airbase (BRP15B61)"}]),
            ConfigField(key="action", label="Bei Überschuss", type=FieldType.SELECT, default="power",
                        options=[{"value": "power", "label": "Einschalten (und danach wieder aus)"},
                                 {"value": "setpoint", "label": "Nur Solltemperatur verschieben (Gerät läuft schon)"}]),
            ConfigField(key="surplus_mode", label="Betriebsart bei Überschuss", type=FieldType.SELECT,
                        default="heat", options=[{"value": "heat", "label": "Heizen"},
                                                 {"value": "cool", "label": "Kühlen"},
                                                 {"value": "auto", "label": "Automatik"}]),
            ConfigField(key="surplus_temp", label="Solltemperatur bei Überschuss (°C)", type=FieldType.NUMBER,
                        default=23, help=f"Raumtemperatur {ROOM_TEMP_MIN_C:.0f}–{ROOM_TEMP_MAX_C:.0f} °C. "
                             "Heizen: etwas höher als sonst, Kühlen: etwas tiefer."),
            rated_field(1000),
            kind_field("climate"),
        ],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="Daikin")
        self.prefix = "/skyfi" if config.get("variant") == "airbase" else ""
        self.action = "setpoint" if config.get("action") == "setpoint" else "power"
        self.surplus_mode = MODE_CODES.get(str(config.get("surplus_mode") or "heat"), "4")
        self.surplus_temp = room_temp(config.get("surplus_temp"), 23.0)
        self.rated_power = num(config.get("rated_power"), 1000.0) or 1000.0
        self.heat_kind = str(config.get("heat_kind") or "climate")
        #: Was MinePower verändert hat – nur das wird zurückgenommen.
        self._switched_on = False
        self._saved_temp: str | None = None

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def _get(self, path: str) -> dict[str, str]:
        res = parse(await self.http.get_text(self.prefix + path))
        if res.get("ret", "OK") != "OK":
            raise DeviceRejected(f"Daikin: {path} abgelehnt (ret={res.get('ret')})")
        return res

    async def _control(self) -> dict[str, str]:
        return await self._get("/aircon/get_control_info")

    async def read_data(self) -> WaterHeaterData:
        ctl = await self._control()
        sensor = await self._get("/aircon/get_sensor_info")
        on = ctl.get("pow") == "1"
        freq = num(sensor.get("cmpfreq"))
        running = on and (freq is None or freq > 0)
        power = self.rated_power if running else 0.0
        extra = {"Betriebsart": MODES.get(ctl.get("mode", ""), ctl.get("mode", "?")),
                 "Solltemperatur": f"{ctl.get('stemp', '–')} °C"}
        outside = num(sensor.get("otemp"))
        if outside is not None:
            extra["Außentemperatur"] = f"{outside:.1f} °C"
        if freq is not None:
            extra["Verdichter"] = f"{freq:.0f} Hz"
        return WaterHeaterData(power=checked("heater_power", power, source="Daikin"), is_on=on,
                               temperature_c=sanitized("water_temperature", num(sensor.get("htemp"))),
                               extra=extra)

    async def _set(self, ctl: dict[str, str], **changes: str) -> None:
        params = {k: ctl.get(k, d) for k, d in (("pow", "0"), ("mode", "4"), ("stemp", "22.0"), ("shum", "0"),
                                                 ("f_rate", "A"), ("f_dir", "0"))}
        # Bei 'Lüften'/'Entfeuchten' liefern manche Adapter stemp='--'
        if num(params["stemp"]) is None:
            params["stemp"] = f"{self.surplus_temp:.1f}"
        if num(params["shum"]) is None and params["shum"] not in ("AUTO",):
            params["shum"] = "0"
        params.update(changes)
        res = parse(await self.http.get_text(self.prefix + "/aircon/set_control_info", params))
        if res.get("ret") != "OK":
            raise DeviceRejected(f"Daikin: Befehl abgelehnt (ret={res.get('ret', '?')})")

    async def set_power(self, watts: float) -> None:
        ctl = await self._control()
        target = f"{self.surplus_temp:.1f}"
        if wants_on(watts, self.rated_power):
            if self.action == "power":
                if ctl.get("pow") != "1" or ctl.get("mode") != self.surplus_mode or ctl.get("stemp") != target:
                    if ctl.get("pow") != "1":
                        self._switched_on = True
                    await self._set(ctl, pow="1", mode=self.surplus_mode, stemp=target)
            else:
                if ctl.get("pow") == "1" and ctl.get("stemp") != target:
                    if self._saved_temp is None:
                        self._saved_temp = ctl.get("stemp")
                    await self._set(ctl, stemp=target)
        else:
            if self.action == "power":
                if self._switched_on and ctl.get("pow") == "1":
                    await self._set(ctl, pow="0")
                self._switched_on = False
            elif self._saved_temp is not None:
                if ctl.get("pow") == "1" and ctl.get("stemp") != self._saved_temp:
                    await self._set(ctl, stemp=self._saved_temp)
                self._saved_temp = None
        await asyncio.sleep(0.3)
        if self.action == "power" and wants_on(watts, self.rated_power):
            if (await self._control()).get("pow") != "1":
                raise CommandNotApplied("Daikin: Einschaltbefehl nicht übernommen")

    async def test_command(self) -> dict | None:
        ctl = await self._control()
        # Schreibtest ohne Wirkung: denselben Zustand noch einmal setzen.
        await self._set(ctl)
        return {"ok": True, "message": "Befehl angenommen (Zustand unverändert)", "sent": "aktueller Zustand",
                "readback": "ret=OK"}
