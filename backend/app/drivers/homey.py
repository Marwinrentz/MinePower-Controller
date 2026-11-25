"""Homey Pro: beliebiges Homey-Gerät als Wärmelast (Wärmepumpe, Klima,
Heizstab-Steckdose, Thermostat).

Homey Pro (ab Modell 2023) bietet eine lokale Web-API mit API-Schlüssel
(Homey-Web-App → Einstellungen → API-Schlüssel, Rechte 'Geräte' lesen und
steuern). MinePower nutzt nur zwei Aufrufe:

    GET /api/manager/devices/device/<id>
        → capabilitiesObj.onoff / measure_power / measure_temperature /
          target_temperature (jeweils {"value": …})
    PUT /api/manager/devices/device/<id>/capability/<capability>  {"value": …}

Damit lässt sich jedes Gerät steuern, das Homey kennt – auch Wärmepumpen
und Klimaanlagen, für die es in Homey eine App gibt (Daikin, Viessmann,
Mitsubishi, Panasonic …).

Bei Überschuss (Konfiguration):

* ``onoff``  – einschalten, ohne Überschuss wieder aus (nur, wenn MinePower
               eingeschaltet hat).
* ``target`` – Solltemperatur (target_temperature) auf den Überschusswert
               stellen und danach auf den vorherigen Wert zurück.

Leistung: measure_power des Geräts oder eines eigenen Messgeräts (z. B.
Zwischenstecker, Shelly in Homey); ohne Messung die Leistungsaufnahme aus
der Konfiguration, solange das Gerät an bzw. freigegeben ist.
Experimentell: nie an echter Hardware.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WaterHeaterData, WaterHeaterDriver
from .heat_common import kind_field, num, rated_field, wants_on
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, DeviceRejected, checked, sanitized

PATH = "/api/manager/devices/device/"


def cap(device: Any, name: str) -> Any:
    """Wert einer Homey-Capability oder None."""
    caps = (device or {}).get("capabilitiesObj") or {}
    entry = caps.get(name)
    return entry.get("value") if isinstance(entry, dict) else None


@register
class HomeyPro(WaterHeaterDriver):
    meta = DriverMeta(
        id="homey_pro", name="Homey Pro (Gerät schalten)", category=DeviceCategory.WATER_HEATER,
        description="Wärmepumpe, Klimaanlage, Heizstab oder Thermostat über Homey Pro (lokale API): "
                    "bei Überschuss einschalten oder Solltemperatur anheben.",
        capabilities={"relay", "write_test"},
        notes="API-Schlüssel in der Homey-Web-App anlegen (Einstellungen → API-Schlüssel, Geräte steuern). "
              "Die Geräte-ID zeigt 'Verbindung testen' bei leerem Feld. Ausgeschaltet wird nur, "
              "was MinePower eingeschaltet hat.",
        fields=[
            ConfigField(key="host", label="IP-Adresse des Homey Pro", placeholder="192.0.2.50"),
            ConfigField(key="api_key", label="API-Schlüssel", type=FieldType.PASSWORD),
            ConfigField(key="device_id", label="Geräte-ID", required=False,
                        placeholder="z. B. 0b5a…-…",
                        help="Leer lassen und 'Verbindung testen': MinePower listet alle Geräte mit ID."),
            ConfigField(key="action", label="Bei Überschuss", type=FieldType.SELECT, default="onoff",
                        options=[{"value": "onoff", "label": "Einschalten (und danach wieder aus)"},
                                 {"value": "target", "label": "Solltemperatur verschieben"}]),
            ConfigField(key="surplus_temp", label="Solltemperatur bei Überschuss (°C)", type=FieldType.NUMBER,
                        required=False, default=23, help="Nur für 'Solltemperatur verschieben'."),
            ConfigField(key="power_device_id", label="Geräte-ID des Messgeräts (optional)", required=False,
                        help="Falls das Gerät selbst keine Leistung meldet (measure_power)."),
            rated_field(1500),
            kind_field("heat_pump"),
        ],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="Homey Pro",
                               headers={"Authorization": f"Bearer {str(config.get('api_key') or '').strip()}"})
        self.device_id = str(config.get("device_id") or "").strip()
        self.power_id = str(config.get("power_device_id") or "").strip()
        self.action = "target" if config.get("action") == "target" else "onoff"
        self.surplus_temp = num(config.get("surplus_temp"), 23.0) or 23.0
        self.rated_power = num(config.get("rated_power"), 1500.0) or 1500.0
        self.heat_kind = str(config.get("heat_kind") or "heat_pump")
        self._switched_on = False
        self._saved_target: float | None = None

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def device_list(self) -> list[tuple[str, str]]:
        """(Name, ID) aller Geräte – damit niemand IDs aus der Adresszeile suchen muss."""
        res = await self.http.get_json(PATH)
        items = res.values() if isinstance(res, dict) else res if isinstance(res, list) else []
        out = [(str(d.get("name") or "?"), str(d.get("id") or "")) for d in items if isinstance(d, dict)]
        return sorted((n, i) for n, i in out if i)

    async def _device(self, device_id: str) -> dict:
        if not device_id:
            found = await self.device_list()
            listing = "; ".join(f"{n} = {i}" for n, i in found[:40]) or "keine"
            raise DeviceRejected(f"Homey: Anmeldung klappt. Jetzt die Geräte-ID eintragen – gefunden: {listing}")
        res = await self.http.get_json(PATH + device_id)
        if not isinstance(res, dict):
            raise DeviceRejected("Homey: unerwartete Antwort – Geräte-ID prüfen")
        return res

    async def _put(self, capability: str, value: Any) -> None:
        await self.http.request("PUT", f"{PATH}{self.device_id}/capability/{capability}",
                                json={"value": value}, want="none")

    def _released(self) -> bool:
        return self._switched_on or self._saved_target is not None

    async def read_data(self) -> WaterHeaterData:
        dev = await self._device(self.device_id)
        onoff = cap(dev, "onoff")
        on = bool(onoff) if onoff is not None else self._released()
        measured = num(cap(dev, "measure_power"))
        if self.power_id:
            measured = num(cap(await self._device(self.power_id), "measure_power"))
        power = measured if measured is not None else (self.rated_power if on else 0.0)
        extra: dict[str, Any] = {"Homey-Gerät": dev.get("name") or self.device_id}
        target = num(cap(dev, "target_temperature"))
        if target is not None:
            extra["Solltemperatur"] = f"{target:.1f} °C"
        if dev.get("available") is False:
            extra["Hinweis"] = "Homey meldet das Gerät als nicht verfügbar"
        return WaterHeaterData(power=checked("heater_power", max(0.0, power), source="Homey"), is_on=on,
                               temperature_c=sanitized("water_temperature", num(cap(dev, "measure_temperature"))),
                               extra=extra)

    async def set_power(self, watts: float) -> None:
        dev = await self._device(self.device_id)
        on_wanted = wants_on(watts, self.rated_power)
        if self.action == "onoff":
            current = cap(dev, "onoff")
            if current is None:
                raise DeviceRejected("Homey: Gerät hat keinen Ein/Aus-Schalter (onoff) – "
                                     "'Solltemperatur verschieben' wählen")
            if on_wanted and not current:
                self._switched_on = True
                await self._put("onoff", True)
            elif not on_wanted:
                if self._switched_on and current:
                    await self._put("onoff", False)
                self._switched_on = False
            else:
                return
            await asyncio.sleep(0.5)
            after = cap(await self._device(self.device_id), "onoff")
            if on_wanted and not after:
                raise CommandNotApplied("Homey: Einschaltbefehl nicht übernommen")
            return
        target = num(cap(dev, "target_temperature"))
        if target is None:
            raise DeviceRejected("Homey: Gerät hat keine Solltemperatur (target_temperature)")
        if on_wanted:
            if abs(target - self.surplus_temp) > 0.05:
                if self._saved_target is None:
                    self._saved_target = target
                await self._put("target_temperature", self.surplus_temp)
        elif self._saved_target is not None:
            if abs(target - self._saved_target) > 0.05:
                await self._put("target_temperature", self._saved_target)
            self._saved_target = None

    async def test_command(self) -> dict | None:
        dev = await self._device(self.device_id)
        name = dev.get("name") or self.device_id
        caps = sorted((dev.get("capabilitiesObj") or {}).keys())
        ok = ("onoff" in caps) if self.action == "onoff" else ("target_temperature" in caps)
        return {"ok": ok, "message": (f"'{name}' gefunden, Fähigkeiten: {', '.join(caps) or 'keine'}"
                                      + ("" if ok else " – die gewählte Aktion fehlt")),
                "sent": "nur gelesen", "readback": name}
