"""Generische HTTP/JSON-Treiber.

Für jedes Gerät mit lokaler HTTP-Schnittstelle, das seine Werte als JSON
(oder als nackte Zahl) liefert: eine URL, Werte per JSON-Pfad. Damit lassen
sich viele Zähler, Wechselrichter, Speicher und Schaltaktoren ohne eigenen
Treiber anbinden.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .base import (
    BatteryData, BatteryDriver, ConfigField, DeviceCategory, DriverMeta, FieldType, InverterData,
    InverterDriver, MeterData, MeterDriver, WaterHeaterData, WaterHeaterDriver,
)
from .generic_common import INVERT_FIELD, RATED_FIELD, SCALE_FIELD, extract, flag, number, parse_payload, path_field
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, checked

URL_FIELD = ConfigField(key="url", label="URL", placeholder="http://192.0.2.10/status")
AUTH_FIELD = ConfigField(key="auth_header", label="Authorization-Header", type=FieldType.PASSWORD, required=False,
                         help="z. B. Bearer <Token>")


def _client(config: dict, name: str) -> HttpDevice:
    headers = {"Authorization": str(config["auth_header"])} if config.get("auth_header") else None
    return HttpDevice("", headers=headers, name=name, verify_ssl=not flag(config.get("insecure")))


class _HttpBase:
    config: dict
    http: HttpDevice

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def _fetch(self, key: str = "url") -> Any:
        url = str(self.config.get(key) or "").strip()
        return parse_payload(await self.http.get_text(url))

    def _value(self, data: Any, key: str, *, required: bool = False, what: str = "Wert") -> float | None:
        """Wert per Pfad × Faktor. Optionale Felder ohne Pfad → None;
        Pflichtwert ohne Pfad → die ganze Antwort ist die Zahl."""
        path = self.config.get(key)
        if path in (None, "") and not required:
            return None
        return number(extract(data, path), what=what) * float(self.config.get("scale") or 1.0)


@register
class HttpMeter(_HttpBase, MeterDriver):
    meta = DriverMeta(
        id="http_meter", name="HTTP/JSON (generisch)", category=DeviceCategory.METER,
        description="Netzleistung aus einer HTTP-Antwort (JSON oder Zahl).",
        fields=[URL_FIELD, path_field("power_path", "Pfad Netzleistung", required=False, placeholder="power"),
                SCALE_FIELD, INVERT_FIELD, AUTH_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = _client(config, "HTTP-Zähler")

    async def read_data(self) -> MeterData:
        data = await self._fetch()
        value = self._value(data, "power_path", required=True, what="Netzleistung") or 0.0
        if flag(self.config.get("invert")):
            value = -value
        return MeterData(grid_power=checked("grid_power", value, source="HTTP"))


@register
class HttpInverter(_HttpBase, InverterDriver):
    meta = DriverMeta(
        id="http_inverter", name="HTTP/JSON (generisch)", category=DeviceCategory.INVERTER,
        description="PV-Leistung, optional Netz- und Batteriewerte, aus einer HTTP-Antwort.",
        capabilities={"battery_monitor"},
        fields=[URL_FIELD, path_field("pv_path", "Pfad PV-Leistung", placeholder="pv.power"), SCALE_FIELD,
                path_field("grid_path", "Pfad Netzleistung", required=False),
                path_field("soc_path", "Pfad Batterie-Ladestand", required=False),
                path_field("battery_path", "Pfad Batterieleistung", required=False),
                ConfigField(key="battery_invert", label="Batterie: Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein"),
                AUTH_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = _client(config, "HTTP-Wechselrichter")

    async def read_data(self) -> InverterData:
        data = await self._fetch()
        pv = self._value(data, "pv_path", required=True, what="PV-Leistung") or 0.0
        grid = self._value(data, "grid_path", what="Netzleistung")
        soc = number(extract(data, self.config["soc_path"])) if self.config.get("soc_path") else None
        bat = self._value(data, "battery_path", what="Batterieleistung")
        if bat is not None and flag(self.config.get("battery_invert")):
            bat = -bat
        return InverterData(
            pv_power=checked("pv_power", pv, source="HTTP"),
            grid_power=checked("grid_power", grid, source="HTTP") if grid is not None else None,
            battery_soc=checked("battery_soc", soc, source="HTTP") if soc is not None else None,
            battery_power=checked("battery_power", bat, source="HTTP") if bat is not None else None,
        )


@register
class HttpBattery(_HttpBase, BatteryDriver):
    meta = DriverMeta(
        id="http_battery", name="HTTP/JSON (generisch, lesend)", category=DeviceCategory.BATTERY,
        description="Ladestand und Leistung aus einer HTTP-Antwort. Nur lesend.",
        capabilities={"battery_monitor"},
        fields=[URL_FIELD, path_field("soc_path", "Pfad Ladestand", placeholder="soc"),
                path_field("battery_path", "Pfad Leistung", placeholder="power"), SCALE_FIELD,
                ConfigField(key="battery_invert", label="Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein"),
                AUTH_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = _client(config, "HTTP-Batterie")

    async def read_data(self) -> BatteryData:
        data = await self._fetch()
        soc = number(extract(data, self.config.get("soc_path")), what="Ladestand")
        power = self._value(data, "battery_path", required=True, what="Batterieleistung") or 0.0
        if flag(self.config.get("battery_invert")):
            power = -power
        return BatteryData(soc=checked("battery_soc", soc, source="HTTP"),
                           power=checked("battery_power", power, source="HTTP"))


@register
class HttpRelayHeater(_HttpBase, WaterHeaterDriver):
    meta = DriverMeta(
        id="http_relay_heater", name="Schaltaktor über HTTP (generisch)", category=DeviceCategory.WATER_HEATER,
        description="Ein/Aus über zwei URLs, Zustand optional aus einer dritten.",
        capabilities={"relay", "write_test"},
        fields=[
            ConfigField(key="on_url", label="URL Ein", placeholder="http://192.0.2.11/relay/0?turn=on"),
            ConfigField(key="off_url", label="URL Aus", placeholder="http://192.0.2.11/relay/0?turn=off"),
            ConfigField(key="state_url", label="URL Zustand", required=False,
                        placeholder="http://192.0.2.11/relay/0"),
            path_field("state_path", "Pfad Zustand", required=False, placeholder="ison"),
            path_field("power_path", "Pfad Leistung", required=False),
            RATED_FIELD, AUTH_FIELD,
        ],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = _client(config, "HTTP-Schaltaktor")
        self.rated_power = float(config.get("rated_power") or 3000)
        self._last_on = False

    async def read_data(self) -> WaterHeaterData:
        if not self.config.get("state_url"):
            on = self._last_on
            measured = None
        else:
            data = await self._fetch("state_url")
            on = number(extract(data, self.config.get("state_path"))) > 0
            measured = (number(extract(data, self.config["power_path"]))
                        if self.config.get("power_path") else None)
        power = measured if measured is not None else (self.rated_power if on else 0.0)
        return WaterHeaterData(power=checked("heater_power", power, source="HTTP"), is_on=on)

    async def set_power(self, watts: float) -> None:
        turn_on = watts >= self.rated_power * 0.5
        await self.http.get_text(str(self.config["on_url" if turn_on else "off_url"]))
        self._last_on = turn_on
        if self.config.get("state_url"):
            await asyncio.sleep(0.4)
            state = await self.read_data()
            if state.is_on != turn_on:
                raise CommandNotApplied("Schaltbefehl nicht übernommen (Zustand weicht ab)")

    async def test_command(self) -> dict | None:
        try:
            await self.set_power(0.0)
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc), "sent": "aus", "readback": "ein"}
        return {"ok": True, "message": "Aus-Befehl übernommen", "sent": "aus", "readback": "aus"}
