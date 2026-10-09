"""Generische MQTT-Treiber.

Für Geräte, die ihre Werte per MQTT veröffentlichen (openDTU, AhoyDTU,
Zigbee2MQTT, ioBroker, Node-RED, EVCC, openWB …). Je Wert ein Topic; der
Inhalt ist eine Zahl oder JSON (Wert per Pfad).

Ein Hintergrund-Task je Gerät hält das Abonnement und merkt sich den
letzten Wert je Topic. Ist ein Pflichtwert älter als 'max. Alter", gilt das
Gerät als offline – ein Broker, der nichts mehr liefert, darf die Regelung
nicht mit eingefrorenen Werten weiterlaufen lassen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .base import (
    BatteryData, BatteryDriver, ConfigField, DeviceCategory, DriverMeta, FieldType, InverterData,
    InverterDriver, MeterData, MeterDriver, WaterHeaterData, WaterHeaterDriver,
)
from .generic_common import INVERT_FIELD, RATED_FIELD, SCALE_FIELD, extract, flag, number, parse_payload
from .registry import register
from .validation import CommandNotApplied, ConnectionProblem, DriverError, checked, sanitized

log = logging.getLogger(__name__)

BROKER = [
    ConfigField(key="host", label="Broker", placeholder="192.0.2.6"),
    ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=1883),
    ConfigField(key="username", label="Benutzer", required=False),
    ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD, required=False),
    ConfigField(key="max_age_s", label="Max. Alter (s)", type=FieldType.NUMBER, default=60),
]


def topic(key: str, label: str, *, required: bool = True, placeholder: str = "") -> ConfigField:
    return ConfigField(key=key, label=label, required=required, placeholder=placeholder or None)


def path(key: str, label: str) -> ConfigField:
    return ConfigField(key=key, label=label, required=False, help="JSON-Pfad; leer = Zahl")


class MqttSubscriber:
    """Abonniert Topics und hält den jeweils letzten Wert."""

    def __init__(self, config: dict, topics: list[str], name: str) -> None:
        self.host = str(config.get("host") or "").strip()
        self.port = int(float(config.get("port") or 1883))
        self.username = config.get("username") or None
        self.password = config.get("password") or None
        self.max_age = float(config.get("max_age_s") or 60)
        self.topics = [t for t in topics if t]
        self.name = name
        self.values: dict[str, tuple[float, Any]] = {}
        self._task: asyncio.Task | None = None
        self._client = None
        self._error: str | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None

    def _client_args(self) -> dict:
        return {"hostname": self.host, "port": self.port, "username": self.username, "password": self.password}

    async def _run(self) -> None:
        import aiomqtt

        while True:
            try:
                async with aiomqtt.Client(**self._client_args()) as client:
                    self._client = client
                    self._error = None
                    for t in self.topics:
                        await client.subscribe(t)
                    async for msg in client.messages:
                        self.values[str(msg.topic)] = (time.monotonic(), parse_payload(msg.payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 – Broker weg: später erneut
                self._error = str(exc) or exc.__class__.__name__
                self._client = None
                await asyncio.sleep(5)

    def get(self, topic_: str, path_: str | None, what: str) -> float:
        item = self.values.get(topic_)
        if item is None:
            raise ConnectionProblem(f"{self.name}: keine Daten auf {topic_}"
                                    + (f" ({self._error})" if self._error else ""))
        at, payload = item
        if time.monotonic() - at > self.max_age:
            raise ConnectionProblem(f"{self.name}: {topic_} seit {time.monotonic() - at:.0f} s ohne neuen Wert")
        return number(extract(payload, path_), what=what)

    def get_optional(self, topic_: str | None, path_: str | None, what: str) -> float | None:
        if not topic_:
            return None
        try:
            return self.get(topic_, path_, what)
        except DriverError:
            return None

    async def publish(self, topic_: str, payload: str, *, retain: bool = False) -> None:
        if self._client is None:
            raise ConnectionProblem(f"{self.name}: keine Verbindung zum Broker")
        await self._client.publish(topic_, payload, retain=retain)

    async def wait_first(self, topic_: str, timeout: float = 8.0) -> None:
        """Für den Verbindungstest: auf den ersten Wert warten."""
        end = time.monotonic() + timeout
        while topic_ not in self.values and time.monotonic() < end:
            await asyncio.sleep(0.2)


class _MqttBase:
    config: dict
    sub: MqttSubscriber
    first_topic: str

    async def connect(self) -> None:
        await self.sub.start()

    async def disconnect(self) -> None:
        await self.sub.stop()

    async def _probe(self):  # noqa: D401 – Verbindungstest wartet auf den ersten Wert
        await self.sub.wait_first(self.first_topic)
        return await super()._probe()  # type: ignore[misc]

    def _v(self, tkey: str, pkey: str, what: str, *, scale: bool = True) -> float:
        value = self.sub.get(self.config[tkey], self.config.get(pkey), what)
        return value * float(self.config.get("scale") or 1.0) if scale else value

    def _opt(self, tkey: str, pkey: str, what: str, *, scale: bool = True) -> float | None:
        value = self.sub.get_optional(self.config.get(tkey), self.config.get(pkey), what)
        if value is None:
            return None
        return value * float(self.config.get("scale") or 1.0) if scale else value


@register
class MqttMeter(_MqttBase, MeterDriver):
    meta = DriverMeta(
        id="mqtt_meter", name="MQTT (generisch)", category=DeviceCategory.METER,
        description="Netzleistung aus einem MQTT-Topic.",
        fields=[*BROKER, topic("grid_topic", "Topic Netzleistung", placeholder="haus/zaehler/leistung"),
                path("grid_path", "Pfad Netzleistung"), SCALE_FIELD, INVERT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.first_topic = config.get("grid_topic", "")
        self.sub = MqttSubscriber(config, [self.first_topic], "MQTT-Zähler")

    async def read_data(self) -> MeterData:
        value = self._v("grid_topic", "grid_path", "Netzleistung")
        if flag(self.config.get("invert")):
            value = -value
        return MeterData(grid_power=checked("grid_power", value, source=self.config["grid_topic"]))


@register
class MqttInverter(_MqttBase, InverterDriver):
    meta = DriverMeta(
        id="mqtt_inverter", name="MQTT (generisch)", category=DeviceCategory.INVERTER,
        description="PV-Leistung, optional Netz- und Batteriewerte, aus MQTT-Topics.",
        capabilities={"battery_monitor"},
        fields=[*BROKER, topic("pv_topic", "Topic PV-Leistung", placeholder="solar/ac/power"),
                path("pv_path", "Pfad PV-Leistung"), SCALE_FIELD,
                topic("grid_topic", "Topic Netzleistung", required=False), path("grid_path", "Pfad Netzleistung"),
                topic("soc_topic", "Topic Batterie-Ladestand", required=False), path("soc_path", "Pfad Ladestand"),
                topic("battery_topic", "Topic Batterieleistung", required=False),
                path("battery_path", "Pfad Batterieleistung"),
                ConfigField(key="battery_invert", label="Batterie: Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein")],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.first_topic = config.get("pv_topic", "")
        self.sub = MqttSubscriber(config, [config.get(k, "") for k in
                                           ("pv_topic", "grid_topic", "soc_topic", "battery_topic")], "MQTT-WR")

    async def read_data(self) -> InverterData:
        pv = self._v("pv_topic", "pv_path", "PV-Leistung")
        grid = self._opt("grid_topic", "grid_path", "Netzleistung")
        soc = self._opt("soc_topic", "soc_path", "Ladestand", scale=False)
        bat = self._opt("battery_topic", "battery_path", "Batterieleistung")
        if bat is not None and flag(self.config.get("battery_invert")):
            bat = -bat
        return InverterData(
            pv_power=checked("pv_power", pv, source=self.config["pv_topic"]),
            grid_power=checked("grid_power", grid) if grid is not None else None,
            battery_soc=sanitized("battery_soc", soc),
            battery_power=checked("battery_power", bat) if bat is not None else None,
        )


@register
class MqttBattery(_MqttBase, BatteryDriver):
    meta = DriverMeta(
        id="mqtt_battery", name="MQTT (generisch, lesend)", category=DeviceCategory.BATTERY,
        description="Ladestand und Leistung aus MQTT-Topics. Nur lesend.",
        capabilities={"battery_monitor"},
        fields=[*BROKER, topic("soc_topic", "Topic Ladestand"), path("soc_path", "Pfad Ladestand"),
                topic("battery_topic", "Topic Leistung"), path("battery_path", "Pfad Leistung"), SCALE_FIELD,
                ConfigField(key="battery_invert", label="Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein")],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.first_topic = config.get("soc_topic", "")
        self.sub = MqttSubscriber(config, [config.get("soc_topic", ""), config.get("battery_topic", "")],
                                  "MQTT-Batterie")

    async def read_data(self) -> BatteryData:
        soc = self._v("soc_topic", "soc_path", "Ladestand", scale=False)
        power = self._v("battery_topic", "battery_path", "Batterieleistung")
        if flag(self.config.get("battery_invert")):
            power = -power
        return BatteryData(soc=checked("battery_soc", soc), power=checked("battery_power", power))


@register
class MqttRelayHeater(_MqttBase, WaterHeaterDriver):
    meta = DriverMeta(
        id="mqtt_relay_heater", name="Schaltaktor über MQTT (generisch)", category=DeviceCategory.WATER_HEATER,
        description="Ein/Aus über ein Befehls-Topic, Zustand aus einem Status-Topic.",
        capabilities={"relay", "write_test"},
        fields=[*BROKER, topic("command_topic", "Topic Befehl", placeholder="heizstab/set"),
                ConfigField(key="payload_on", label="Nutzlast Ein", default="ON"),
                ConfigField(key="payload_off", label="Nutzlast Aus", default="OFF"),
                topic("state_topic", "Topic Zustand", placeholder="heizstab/state"), path("state_path", "Pfad Zustand"),
                topic("power_topic", "Topic Leistung", required=False), path("power_path", "Pfad Leistung"),
                RATED_FIELD],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.first_topic = config.get("state_topic", "")
        self.sub = MqttSubscriber(config, [config.get("state_topic", ""), config.get("power_topic", "")],
                                  "MQTT-Schaltaktor")
        self.rated_power = float(config.get("rated_power") or 3000)

    async def read_data(self) -> WaterHeaterData:
        on = self._v("state_topic", "state_path", "Zustand", scale=False) > 0
        measured = self._opt("power_topic", "power_path", "Leistung", scale=False)
        power = measured if measured is not None else (self.rated_power if on else 0.0)
        return WaterHeaterData(power=checked("heater_power", power), is_on=on)

    async def set_power(self, watts: float) -> None:
        turn_on = watts >= self.rated_power * 0.5
        payload = str(self.config.get("payload_on" if turn_on else "payload_off") or ("ON" if turn_on else "OFF"))
        await self.sub.publish(self.config["command_topic"], payload)
        await asyncio.sleep(1.0)
        state = await self.read_data()
        if state.is_on != turn_on:
            raise CommandNotApplied("MQTT: Zustand-Topic bestätigt den Schaltbefehl nicht")

    async def test_command(self) -> dict | None:
        try:
            await self.set_power(0.0)
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc), "sent": "aus", "readback": "ein"}
        return {"ok": True, "message": "Aus-Befehl übernommen", "sent": "aus", "readback": "aus"}
