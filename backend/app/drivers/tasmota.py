"""Tasmota: Zähler mit SML-Lesekopf und Schaltaktoren (HTTP-API ``/cm``).

* Zähler: ``cm?cmnd=Status 10`` → ``StatusSNS``; der Pfad hängt vom
  SML-Skript ab (z. B. ``SML.Power``, ``MT175.P``).
* Schaltaktor: ``cm?cmnd=Power<n> ON|OFF``, Zustand ``cm?cmnd=Power<n>``,
  Leistung (falls gemessen) aus ``StatusSNS.ENERGY.Power``.
"""
from __future__ import annotations

import asyncio

from .base import (
    ConfigField, DeviceCategory, DriverMeta, FieldType, MeterData, MeterDriver, WaterHeaterData,
    WaterHeaterDriver,
)
from .generic_common import INVERT_FIELD, RATED_FIELD, SCALE_FIELD, extract, flag, number
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, checked

HOST = ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.12")
USER = ConfigField(key="user", label="Benutzer", required=False, default="admin")
PASSWORD = ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD, required=False)


class _Tasmota:
    config: dict
    http: HttpDevice

    def _client(self, config: dict) -> HttpDevice:
        return HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="Tasmota")

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def cmd(self, command: str) -> dict:
        params = {"cmnd": command}
        if self.config.get("password"):
            params.update(user=self.config.get("user") or "admin", password=self.config["password"])
        return await self.http.get_json("/cm", params)


@register
class TasmotaMeter(_Tasmota, MeterDriver):
    meta = DriverMeta(
        id="tasmota_meter", name="Tasmota (SML-Lesekopf)", category=DeviceCategory.METER,
        description="Netzleistung aus dem SML-Skript eines Tasmota-Lesekopfs.",
        fields=[HOST, ConfigField(key="power_path", label="Pfad Leistung", placeholder="SML.Power",
                                  help="unter StatusSNS, je nach Skript"),
                SCALE_FIELD, INVERT_FIELD, USER, PASSWORD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = self._client(config)

    async def read_data(self) -> MeterData:
        data = await self.cmd("Status 10")
        value = number(extract(data, f"StatusSNS.{self.config['power_path']}"), what="Leistung")
        value *= float(self.config.get("scale") or 1.0)
        if flag(self.config.get("invert")):
            value = -value
        return MeterData(grid_power=checked("grid_power", value, source="Tasmota"))


@register
class TasmotaRelayHeater(_Tasmota, WaterHeaterDriver):
    meta = DriverMeta(
        id="tasmota_relay_heater", name="Schaltaktor Tasmota", category=DeviceCategory.WATER_HEATER,
        description="Heizstab oder andere Last über ein Tasmota-Relais.",
        capabilities={"relay", "write_test"},
        fields=[HOST, ConfigField(key="relay", label="Relais-Nr.", type=FieldType.NUMBER, default=1),
                RATED_FIELD, USER, PASSWORD],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = self._client(config)
        self.rated_power = float(config.get("rated_power") or 3000)
        self.relay = int(float(config.get("relay") or 1))

    async def read_data(self) -> WaterHeaterData:
        state = await self.cmd(f"Power{self.relay}")
        on = str(state.get(f"POWER{self.relay}", state.get("POWER", "OFF"))).upper() == "ON"
        measured = None
        try:
            sns = await self.cmd("Status 10")
            measured = number(extract(sns, "StatusSNS.ENERGY.Power"))
        except Exception:  # noqa: BLE001 – Relais ohne Messung
            measured = None
        power = measured if measured is not None else (self.rated_power if on else 0.0)
        return WaterHeaterData(power=checked("heater_power", power, source="Tasmota"), is_on=on)

    async def set_power(self, watts: float) -> None:
        turn_on = watts >= self.rated_power * 0.5
        await self.cmd(f"Power{self.relay} {'ON' if turn_on else 'OFF'}")
        await asyncio.sleep(0.3)
        state = await self.read_data()
        if state.is_on != turn_on:
            raise CommandNotApplied("Tasmota: Schaltbefehl nicht übernommen")

    async def test_command(self) -> dict | None:
        try:
            await self.set_power(0.0)
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc), "sent": "aus", "readback": "ein"}
        return {"ok": True, "message": "Aus-Befehl übernommen", "sent": "aus", "readback": "aus"}
