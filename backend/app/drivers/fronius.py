"""Fronius (GEN24 / GEN24 Plus, Symo, Primo …) über die lokale Solar API v1.

Der GEN24 Plus ist ein Hybrid-Wechselrichter: Er regelt seine Batterie
selbst auf Nulleinspeisung. MinePower liest SoC und Batterieleistung nur mit
(`P_Akku`, `StateOfCharge_Relative`) – ein separates, aktiv gesteuertes
Batteriegerät ist für die Nulleinspeisung ausdrücklich nicht nötig.

Vorzeichen der Solar API (Site-Objekt):
  P_Grid  + = Bezug, − = Einspeisung   (identisch mit unserer Konvention)
  P_Akku  + = Entladen, − = Laden      (invers zu unserer Konvention!)
  P_PV    Erzeugung (kann nachts null oder fehlend sein)
"""
from __future__ import annotations

from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    InverterData,
    InverterDriver,
    MeterData,
    MeterDriver,
)
from .http_util import HttpDevice
from .registry import register
from .validation import DriverError, checked, sanitized

POWERFLOW = "/solar_api/v1/GetPowerFlowRealtimeData.fcgi"

HOST_FIELD = ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.20",
                         help="IP des Fronius-Wechselrichters. Die Solar API muss aktiv sein – "
                              "bei GEN24 unter Kommunikation → Solar API (ab Firmware 1.14 "
                              "standardmäßig deaktiviert!).")


class _FroniusBase:
    """Gemeinsamer HTTP-Zugriff auf das Powerflow-Objekt."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)  # type: ignore[call-arg]
        self.http = HttpDevice(
            f"http://{str(config.get('host', '')).strip()}", timeout=8.0, name="Fronius"
        )

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def _site(self) -> dict:
        data = await self.http.get_json(POWERFLOW)
        body = ((data or {}).get("Body") or {}).get("Data") or {}
        site = body.get("Site")
        if not isinstance(site, dict):
            raise DriverError(
                "Fronius antwortet, liefert aber kein 'Site'-Objekt. Meist ist die Solar API "
                "deaktiviert (GEN24: Kommunikation → Solar API einschalten) oder die IP gehört "
                "zu einem anderen Gerät."
            )
        return site


@register
class FroniusInverter(_FroniusBase, InverterDriver):
    meta = DriverMeta(
        id="fronius_inverter",
        name="Fronius Wechselrichter / GEN24 (Solar API)",
        category=DeviceCategory.INVERTER,
        description="Fronius GEN24 (Plus), Symo und Primo über die lokale Solar API. Liefert "
                    "PV-Leistung und – mit Fronius Smart Meter – die Netzleistung; beim GEN24 Plus "
                    "zusätzlich Batterie-Ladestand und -leistung (nur lesend, der Wechselrichter "
                    "regelt die Batterie selbst).",
        capabilities={"hybrid", "battery_monitor", "grid_meter"},
        fields=[HOST_FIELD],
    )

    async def read_data(self) -> InverterData:
        site = await self._site()
        grid = site.get("P_Grid")
        akku = site.get("P_Akku")
        soc = site.get("StateOfCharge_Relative")
        # Fronius: P_Akku + = Entladen → Vorzeichen drehen
        battery_power = None if akku is None else -float(akku)
        return InverterData(
            pv_power=max(0.0, checked("pv_power", _f(site.get("P_PV")), source="Fronius P_PV")),
            grid_power=None if grid is None else checked("grid_power", float(grid), source="Fronius P_Grid"),
            daily_yield_kwh=sanitized("energy_kwh", _f(site.get("E_Day")) / 1000.0, zero_is_none=True),
            total_yield_kwh=sanitized("energy_kwh", _f(site.get("E_Total")) / 1000.0, zero_is_none=True),
            battery_power=sanitized("battery_power", battery_power),
            battery_soc=sanitized("battery_soc", None if soc is None else float(soc)),
            status=str(site.get("Mode") or "ok"),
        )

    def _warnings(self, values: dict) -> list[str]:
        out = super()._warnings(values)
        if "Netzleistung" not in values:
            out.append(
                "Es wird keine Netzleistung geliefert – am Wechselrichter ist kein Fronius "
                "Smart Meter aktiv. Dann bitte einen separaten Netzzähler anlegen; ohne "
                "Netzmessung ist keine Überschussregelung möglich."
            )
        return out


@register
class FroniusMeter(_FroniusBase, MeterDriver):
    meta = DriverMeta(
        id="fronius_meter",
        name="Fronius Smart Meter (via Wechselrichter)",
        category=DeviceCategory.METER,
        description="Netzmessung des Fronius Smart Meters, gelesen über die Solar API des "
                    "Wechselrichters (gleiche IP wie der Wechselrichter).",
        fields=[HOST_FIELD],
    )

    async def read_data(self) -> MeterData:
        site = await self._site()
        grid = site.get("P_Grid")
        if grid is None:
            raise DriverError(
                "Am Fronius ist kein Smart Meter aktiv (Feld 'P_Grid' fehlt). Ohne Netzmessung "
                "kann nicht auf Nulleinspeisung geregelt werden – Smart Meter im Wechselrichter "
                "konfigurieren oder einen anderen Netzzähler verwenden."
            )
        return MeterData(grid_power=checked("grid_power", float(grid), source="Fronius P_Grid"))


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
