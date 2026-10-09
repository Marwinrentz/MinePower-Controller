"""Wallboxen über MQTT: openWB (Software 1.9, Modus 'Nur Ladepunkt') und
ein generischer Treiber für beliebige Topics. Experimentell."""
from __future__ import annotations

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WallboxData, WallboxDriver, WallboxState
from .generic_mqtt import BROKER, MqttSubscriber, path, topic
from .registry import register
from .wallbox_common import MAX_CURRENT_FIELD, PHASES_FIELD, Heartbeat, f, phases_of, wallbox_data

WALLBOX = DeviceCategory.WALLBOX


class _MqttWallbox(Heartbeat, WallboxDriver):
    sub: MqttSubscriber
    first_topic: str

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_current = f(config.get("max_current"), 16.0) or 16.0
        self.phases = phases_of(config)
        self._amps = self.min_current

    def clamp(self, amps: float) -> float:
        return max(self.min_current, min(float(amps), self.max_current))

    async def connect(self) -> None:
        await self.sub.start()
        self.start_heartbeat()

    async def disconnect(self) -> None:
        self.stop_heartbeat()
        await self.sub.stop()

    async def _probe(self):  # noqa: D401 – Verbindungstest wartet auf den ersten Wert
        await self.sub.wait_first(self.first_topic)
        return await super()._probe()


@register
class OpenWbMqtt(_MqttWallbox):
    meta = DriverMeta(
        id="openwb_mqtt", name="openWB 1.9 'Nur Ladepunkt' (MQTT)", category=WALLBOX,
        description="openWB series1/2 mit Software 1.9 im Modus 'Nur Ladepunkt' über den MQTT-Broker der openWB.",
        capabilities={"write_test"},
        notes="In der openWB: Einstellungen → Modulkonfiguration → 'Nur Ladepunkt'. Broker = IP der openWB.",
        fields=[*BROKER, ConfigField(key="chargepoint", label="Ladepunkt", type=FieldType.SELECT, default="1",
                                     options=[{"value": "1", "label": "1"}, {"value": "2", "label": "2"}]),
                MAX_CURRENT_FIELD],
    )
    heartbeat_s = 10.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.lp = 2 if str(config.get("chargepoint", "1")) == "2" else 1
        base = f"openWB/lp/{self.lp}"
        self.t = {"plug": f"{base}/boolPlugStat", "charge": f"{base}/boolChargeStat", "power": f"{base}/W",
                  "i1": f"{base}/APhase1", "i2": f"{base}/APhase2", "i3": f"{base}/APhase3"}
        self.first_topic = self.t["plug"]
        self.current_topic = "openWB/set/isss/" + ("Lp2Current" if self.lp == 2 else "Current")
        self.sub = MqttSubscriber(config, list(self.t.values()), "openWB")
        self._enabled = False

    async def heartbeat(self) -> None:
        await self.sub.publish("openWB/set/isss/heartbeat", "1", retain=True)

    async def read_data(self) -> WallboxData:
        plugged = self.sub.get(self.t["plug"], None, "Stecker") > 0
        charging = self.sub.get(self.t["charge"], None, "Laden") > 0
        state = (WallboxState.CHARGING if charging else WallboxState.CONNECTED) if plugged else WallboxState.IDLE
        currents = [self.sub.get_optional(self.t[k], None, "Strom") or 0.0 for k in ("i1", "i2", "i3")]
        return wallbox_data(state, self.sub.get(self.t["power"], None, "Ladeleistung"),
                            current_set=self._amps if self._enabled else 0.0, currents=currents, source="openWB W")

    async def _current(self, amps: int) -> None:
        await self.sub.publish(self.current_topic, str(amps), retain=True)

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self._current(int(self._amps))
        self._enabled = True

    async def start_charging(self) -> None:
        await self._current(int(self._amps))
        self._enabled = True

    async def stop_charging(self) -> None:
        await self._current(0)
        self._enabled = False


@register
class MqttWallbox(_MqttWallbox):
    meta = DriverMeta(
        id="mqtt_wallbox", name="Wallbox über MQTT (generisch)", category=WALLBOX,
        description="Werte aus MQTT-Topics, Steuerung über Befehls-Topics.",
        capabilities={"write_test"},
        fields=[*BROKER,
                topic("power_topic", "Topic Ladeleistung (W)", placeholder="wallbox/power"), path("power_path", "Pfad Ladeleistung"),
                topic("plugged_topic", "Topic Fahrzeug verbunden", required=False), path("plugged_path", "Pfad verbunden"),
                topic("soc_topic", "Topic Fahrzeug-Ladestand", required=False), path("soc_path", "Pfad Ladestand"),
                topic("current_topic", "Topic Ladestrom setzen", placeholder="wallbox/set/current"),
                topic("enable_topic", "Topic Laden ein/aus", placeholder="wallbox/set/enable"),
                ConfigField(key="payload_on", label="Nutzlast Ein", default="1"),
                ConfigField(key="payload_off", label="Nutzlast Aus", default="0"),
                MAX_CURRENT_FIELD, PHASES_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.first_topic = config.get("power_topic", "")
        self.sub = MqttSubscriber(config, [config.get(k, "") for k in ("power_topic", "plugged_topic", "soc_topic")],
                                  "MQTT-Wallbox")
        self._enabled = False

    async def read_data(self) -> WallboxData:
        power = self.sub.get(self.config["power_topic"], self.config.get("power_path"), "Ladeleistung")
        plugged = self.sub.get_optional(self.config.get("plugged_topic"), self.config.get("plugged_path"), "verbunden")
        soc = self.sub.get_optional(self.config.get("soc_topic"), self.config.get("soc_path"), "Ladestand")
        if power > 100:
            state = WallboxState.CHARGING
        elif plugged is not None and plugged <= 0:
            state = WallboxState.IDLE
        else:
            state = WallboxState.CONNECTED
        return wallbox_data(state, power, current_set=self._amps if self._enabled else 0.0, soc=soc, source="MQTT")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self.sub.publish(self.config["current_topic"], str(int(self._amps)))

    async def start_charging(self) -> None:
        await self.sub.publish(self.config["enable_topic"], str(self.config.get("payload_on") or "1"))
        self._enabled = True

    async def stop_charging(self) -> None:
        await self.sub.publish(self.config["enable_topic"], str(self.config.get("payload_off") or "0"))
        self._enabled = False
