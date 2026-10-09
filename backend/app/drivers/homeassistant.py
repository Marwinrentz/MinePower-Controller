"""Home Assistant (REST-API) als Brücke zu beliebigen Geräten.

Was Home Assistant kennt, kann MinePower lesen und schalten: Zähler,
Wechselrichter, Speicher, Schaltaktoren und Wallboxen mit Strom-Entität.
Zugang über ein Long-Lived Access Token (Profil → Sicherheit).

* Lesen: ``GET /api/states/<entity_id>`` – Zustand und Einheit (W/kW).
* Schalten: ``POST /api/services/<domain>/turn_on|turn_off``.
* Ladestrom: ``POST /api/services/number/set_value``.
"""
from __future__ import annotations

import asyncio

from .base import (
    BatteryData, BatteryDriver, ConfigField, DeviceCategory, DriverMeta, FieldType, InverterData,
    InverterDriver, MeterData, MeterDriver, WallboxData, WallboxDriver, WallboxState, WaterHeaterData,
    WaterHeaterDriver,
)
from .generic_common import INVERT_FIELD, RATED_FIELD, flag, number, watts
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, DriverError, checked, sanitized

URL = ConfigField(key="url", label="Home-Assistant-URL", placeholder="http://192.0.2.5:8123")
TOKEN = ConfigField(key="token", label="Access-Token", type=FieldType.PASSWORD,
                    help="Long-Lived Access Token")
UNAVAILABLE = ("unavailable", "unknown", "none", "")


def entity(key: str, label: str, *, required: bool = True, placeholder: str = "sensor.") -> ConfigField:
    return ConfigField(key=key, label=label, required=required, placeholder=placeholder)


class HomeAssistant:
    def __init__(self, config: dict, name: str) -> None:
        base = str(config.get("url") or "").rstrip("/")
        self.http = HttpDevice(base, headers={"Authorization": f"Bearer {config.get('token', '')}"}, name=name)

    async def state(self, entity_id: str) -> tuple[str, str | None]:
        data = await self.http.get_json(f"/api/states/{entity_id}")
        state = str(data.get("state", "")).strip()
        if state.lower() in UNAVAILABLE:
            raise DriverError(f"Entität {entity_id}: nicht verfügbar ({state or 'leer'})")
        return state, (data.get("attributes") or {}).get("unit_of_measurement")

    async def value(self, entity_id: str, what: str) -> float:
        state, _unit = await self.state(entity_id)
        return number(state, what=f"{entity_id} ({what})")

    async def power(self, entity_id: str) -> float:
        state, unit = await self.state(entity_id)
        return watts(state, unit, what=entity_id)

    async def call(self, domain: str, service: str, data: dict) -> None:
        await self.http.post_json(f"/api/services/{domain}/{service}", data)

    async def switch(self, entity_id: str, on: bool) -> None:
        domain = entity_id.split(".", 1)[0]
        await self.call(domain, "turn_on" if on else "turn_off", {"entity_id": entity_id})


class _HaBase:
    config: dict
    ha: HomeAssistant

    async def connect(self) -> None:
        await self.ha.http.connect()

    async def disconnect(self) -> None:
        await self.ha.http.close()

    async def _opt_power(self, key: str) -> float | None:
        ent = self.config.get(key)
        return await self.ha.power(ent) if ent else None

    async def _opt_value(self, key: str, what: str) -> float | None:
        ent = self.config.get(key)
        return await self.ha.value(ent, what) if ent else None


@register
class HaMeter(_HaBase, MeterDriver):
    meta = DriverMeta(
        id="ha_meter", name="Home Assistant", category=DeviceCategory.METER,
        description="Netzleistung aus einer Home-Assistant-Entität (W oder kW).",
        fields=[URL, TOKEN, entity("grid_entity", "Entität Netzleistung"), INVERT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ha = HomeAssistant(config, "Home Assistant")

    async def read_data(self) -> MeterData:
        value = await self.ha.power(self.config["grid_entity"])
        if flag(self.config.get("invert")):
            value = -value
        return MeterData(grid_power=checked("grid_power", value, source=self.config["grid_entity"]))


@register
class HaInverter(_HaBase, InverterDriver):
    meta = DriverMeta(
        id="ha_inverter", name="Home Assistant", category=DeviceCategory.INVERTER,
        description="PV-Leistung, optional Netz- und Batteriewerte, aus Home-Assistant-Entitäten.",
        capabilities={"battery_monitor"},
        fields=[URL, TOKEN, entity("pv_entity", "Entität PV-Leistung"),
                entity("grid_entity", "Entität Netzleistung", required=False),
                entity("soc_entity", "Entität Batterie-Ladestand", required=False),
                entity("battery_entity", "Entität Batterieleistung", required=False),
                ConfigField(key="battery_invert", label="Batterie: Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein")],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ha = HomeAssistant(config, "Home Assistant")

    async def read_data(self) -> InverterData:
        pv = await self.ha.power(self.config["pv_entity"])
        grid = await self._opt_power("grid_entity")
        soc = await self._opt_value("soc_entity", "Ladestand")
        bat = await self._opt_power("battery_entity")
        if bat is not None and flag(self.config.get("battery_invert")):
            bat = -bat
        return InverterData(
            pv_power=checked("pv_power", pv, source=self.config["pv_entity"]),
            grid_power=checked("grid_power", grid) if grid is not None else None,
            battery_soc=sanitized("battery_soc", soc),
            battery_power=checked("battery_power", bat) if bat is not None else None,
        )


@register
class HaBattery(_HaBase, BatteryDriver):
    meta = DriverMeta(
        id="ha_battery", name="Home Assistant (lesend)", category=DeviceCategory.BATTERY,
        description="Ladestand und Leistung aus Home-Assistant-Entitäten. Nur lesend.",
        capabilities={"battery_monitor"},
        fields=[URL, TOKEN, entity("soc_entity", "Entität Ladestand"), entity("battery_entity", "Entität Leistung"),
                ConfigField(key="battery_invert", label="Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein")],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ha = HomeAssistant(config, "Home Assistant")

    async def read_data(self) -> BatteryData:
        soc = await self.ha.value(self.config["soc_entity"], "Ladestand")
        power = await self.ha.power(self.config["battery_entity"])
        if flag(self.config.get("battery_invert")):
            power = -power
        return BatteryData(soc=checked("battery_soc", soc), power=checked("battery_power", power))


@register
class HaSwitchHeater(_HaBase, WaterHeaterDriver):
    meta = DriverMeta(
        id="ha_switch_heater", name="Schaltaktor über Home Assistant", category=DeviceCategory.WATER_HEATER,
        description="Heizstab oder andere Last über eine Schalt-Entität (switch, input_boolean).",
        capabilities={"relay", "write_test"},
        fields=[URL, TOKEN, entity("switch_entity", "Entität Schalter", placeholder="switch."),
                entity("power_entity", "Entität Leistung", required=False),
                entity("temperature_entity", "Entität Temperatur", required=False), RATED_FIELD],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ha = HomeAssistant(config, "Home Assistant")
        self.rated_power = float(config.get("rated_power") or 3000)

    async def read_data(self) -> WaterHeaterData:
        state, _ = await self.ha.state(self.config["switch_entity"])
        on = state.lower() == "on"
        measured = await self._opt_power("power_entity")
        temp = await self._opt_value("temperature_entity", "Temperatur")
        power = measured if measured is not None else (self.rated_power if on else 0.0)
        return WaterHeaterData(power=checked("heater_power", power), is_on=on,
                               temperature_c=sanitized("water_temperature", temp))

    async def set_power(self, watts_: float) -> None:
        turn_on = watts_ >= self.rated_power * 0.5
        await self.ha.switch(self.config["switch_entity"], turn_on)
        await asyncio.sleep(0.5)
        state = await self.read_data()
        if state.is_on != turn_on:
            raise CommandNotApplied("Schaltbefehl nicht übernommen (Entität meldet anderen Zustand)")

    async def test_command(self) -> dict | None:
        try:
            await self.set_power(0.0)
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc), "sent": "aus", "readback": "ein"}
        return {"ok": True, "message": "Aus-Befehl übernommen", "sent": "aus", "readback": "aus"}


@register
class HaWallbox(_HaBase, WallboxDriver):
    meta = DriverMeta(
        id="ha_wallbox", name="Wallbox über Home Assistant", category=DeviceCategory.WALLBOX,
        description="Wallbox-Integration in Home Assistant mit Schalter und Ladestrom-Entität "
                    "(z. B. openWB, Easee, Zaptec, Wallbox, Keba).",
        capabilities={"write_test"},
        fields=[URL, TOKEN,
                entity("switch_entity", "Entität Laden ein/aus", placeholder="switch."),
                entity("current_entity", "Entität Ladestrom (A)", placeholder="number."),
                entity("power_entity", "Entität Ladeleistung"),
                entity("plugged_entity", "Entität Fahrzeug verbunden", required=False, placeholder="binary_sensor."),
                entity("soc_entity", "Entität Fahrzeug-Ladestand", required=False),
                ConfigField(key="phases", label="Phasen", type=FieldType.SELECT, default="3",
                            options=[{"value": "1", "label": "1"}, {"value": "3", "label": "3"}]),
                ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16)],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ha = HomeAssistant(config, "Home Assistant")
        self.max_current = float(config.get("max_current") or 16)
        self.phases = 1 if str(config.get("phases", "3")) == "1" else 3

    async def read_data(self) -> WallboxData:
        power = await self.ha.power(self.config["power_entity"])
        switch, _ = await self.ha.state(self.config["switch_entity"])
        current = await self._opt_value("current_entity", "Ladestrom")
        plugged: bool | None = None
        if self.config.get("plugged_entity"):
            state, _ = await self.ha.state(self.config["plugged_entity"])
            plugged = state.lower() in ("on", "true", "connected", "plugged")
        soc = await self._opt_value("soc_entity", "Ladestand")
        if power > 100:
            st = WallboxState.CHARGING
        elif plugged is False:
            st = WallboxState.IDLE
        else:
            st = WallboxState.CONNECTED
        return WallboxData(
            state=st, power=checked("wallbox_power", power), current_set=current if switch.lower() == "on" else 0.0,
            phases_active=self.phases if power > 100 else None, soc=sanitized("vehicle_soc", soc),
        )

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self.ha.call("number", "set_value", {"entity_id": self.config["current_entity"],
                                                   "value": round(min(amps, self.max_current))})

    async def start_charging(self) -> None:
        await self.ha.switch(self.config["switch_entity"], True)

    async def stop_charging(self) -> None:
        await self.ha.switch(self.config["switch_entity"], False)
