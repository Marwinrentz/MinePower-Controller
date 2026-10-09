"""Wärmepumpe über SG-Ready (zwei Schaltkontakte).

SG-Ready-Zustände (Kontakt 1 / Kontakt 2):

    1 / 0  Betriebssperre (EVU-Sperre) – wird von MinePower nie gesetzt
    0 / 0  Normalbetrieb
    0 / 1  Einschaltempfehlung (Überschuss)
    1 / 1  Einschaltbefehl – wird von MinePower nie gesetzt

MinePower schaltet nur zwischen Normalbetrieb (aus) und
Einschaltempfehlung (ein). Die Wärmepumpe entscheidet selbst, ob sie
anläuft. Leistung: gemessen (falls der Schaltaktor misst) oder die
eingestellte Leistung bei Einschaltempfehlung.
"""
from __future__ import annotations

import asyncio

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WaterHeaterData, WaterHeaterDriver
from .homeassistant import TOKEN, URL, HomeAssistant, entity
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, checked

POWER_FIELD = ConfigField(key="rated_power", label="Leistung bei Einschaltempfehlung (W)", type=FieldType.NUMBER,
                          default=2000)
SG_NOTES = "Kontakt 1 = Sperre, Kontakt 2 = Einschaltempfehlung. MinePower setzt nie die Sperre."


class _SgReady(WaterHeaterDriver):
    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.rated_power = float(config.get("rated_power") or 2000)

    async def _get(self) -> tuple[bool, bool, float | None]:
        raise NotImplementedError

    async def _set(self, contact1: bool, contact2: bool) -> None:
        raise NotImplementedError

    async def read_data(self) -> WaterHeaterData:
        c1, c2, measured = await self._get()
        on = c2 and not c1
        power = measured if measured is not None else (self.rated_power if on else 0.0)
        mode = {(False, False): "Normalbetrieb", (False, True): "Einschaltempfehlung",
                (True, False): "Betriebssperre", (True, True): "Einschaltbefehl"}[(c1, c2)]
        return WaterHeaterData(power=checked("heater_power", power, source="SG-Ready"), is_on=on,
                               extra={"SG-Ready": mode})

    async def set_power(self, watts: float) -> None:
        turn_on = watts >= self.rated_power * 0.5
        await self._set(False, turn_on)
        await asyncio.sleep(0.4)
        c1, c2, _ = await self._get()
        if c1 or c2 != turn_on:
            raise CommandNotApplied("SG-Ready: Kontaktzustand weicht vom Befehl ab")

    async def test_command(self) -> dict | None:
        try:
            await self.set_power(0.0)
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc), "sent": "Normalbetrieb", "readback": "abweichend"}
        return {"ok": True, "message": "Normalbetrieb gesetzt und bestätigt", "sent": "Normalbetrieb",
                "readback": "Normalbetrieb"}


@register
class SgReadyShelly(_SgReady):
    meta = DriverMeta(
        id="sgready_shelly", name="Wärmepumpe SG-Ready über Shelly (2 Kanäle)", category=DeviceCategory.WATER_HEATER,
        description="SG-Ready über einen zweikanaligen Shelly (Gen2+, z. B. Pro 2, Plus 2PM).",
        capabilities={"relay", "sg_ready", "write_test"},
        notes=SG_NOTES,
        fields=[ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.13"),
                ConfigField(key="channel_1", label="Kanal Kontakt 1", type=FieldType.NUMBER, default=0),
                ConfigField(key="channel_2", label="Kanal Kontakt 2", type=FieldType.NUMBER, default=1),
                POWER_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="Shelly SG-Ready")
        self.ch1 = int(float(config.get("channel_1") or 0))
        self.ch2 = int(float(config.get("channel_2") if config.get("channel_2") not in (None, "") else 1))

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def _get(self) -> tuple[bool, bool, float | None]:
        a = await self.http.get_json("/rpc/Switch.GetStatus", {"id": self.ch1})
        b = await self.http.get_json("/rpc/Switch.GetStatus", {"id": self.ch2})
        # Misst der Shelly, gilt die Messung (auch 0 W: Wärmepumpe läuft nicht an)
        measured = None
        if "apower" in a or "apower" in b:
            measured = float(a.get("apower") or 0) + float(b.get("apower") or 0)
        return bool(a.get("output")), bool(b.get("output")), measured

    async def _set(self, contact1: bool, contact2: bool) -> None:
        await self.http.get_json("/rpc/Switch.Set", {"id": self.ch1, "on": contact1})
        await self.http.get_json("/rpc/Switch.Set", {"id": self.ch2, "on": contact2})


@register
class SgReadyHomeAssistant(_SgReady):
    meta = DriverMeta(
        id="sgready_homeassistant", name="Wärmepumpe SG-Ready über Home Assistant",
        category=DeviceCategory.WATER_HEATER,
        description="SG-Ready über zwei Schalt-Entitäten in Home Assistant.",
        capabilities={"relay", "sg_ready", "write_test"},
        notes=SG_NOTES,
        fields=[URL, TOKEN, entity("switch_1", "Entität Kontakt 1", placeholder="switch."),
                entity("switch_2", "Entität Kontakt 2", placeholder="switch."),
                entity("power_entity", "Entität Leistung", required=False), POWER_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ha = HomeAssistant(config, "Home Assistant")

    async def connect(self) -> None:
        await self.ha.http.connect()

    async def disconnect(self) -> None:
        await self.ha.http.close()

    async def _get(self) -> tuple[bool, bool, float | None]:
        a, _ = await self.ha.state(self.config["switch_1"])
        b, _ = await self.ha.state(self.config["switch_2"])
        measured = await self.ha.power(self.config["power_entity"]) if self.config.get("power_entity") else None
        return a.lower() == "on", b.lower() == "on", measured

    async def _set(self, contact1: bool, contact2: bool) -> None:
        await self.ha.switch(self.config["switch_1"], contact1)
        await self.ha.switch(self.config["switch_2"], contact2)
