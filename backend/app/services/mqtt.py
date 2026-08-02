"""Optionaler MQTT-Publisher für externe Integration.

Publiziert den Live-Snapshot unter `<base>/status/...` (Retained) und
Home-Assistant-Auto-Discovery-Configs unter `homeassistant/sensor/...`.
Rein optional – die App funktioniert vollständig ohne Broker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(self, url: str, base_topic: str = "minepower") -> None:
        parsed = urlparse(url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 1883
        self.base = base_topic.rstrip("/")
        self._client = None
        self._discovery_sent = False
        self._last_publish = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_connected(self):
        import aiomqtt

        if self._client is None:
            self._client = aiomqtt.Client(self.host, self.port, identifier="minepower")
            await self._client.__aenter__()
            log.info("MQTT verbunden: %s:%s", self.host, self.port)
        return self._client

    async def publish_snapshot(self, snapshot: dict) -> None:
        now = time.monotonic()
        if now - self._last_publish < 5.0:  # Broker nicht fluten
            return
        self._last_publish = now
        async with self._lock:
            try:
                client = await self._ensure_connected()
                if not self._discovery_sent:
                    await self._send_discovery(client)
                    self._discovery_sent = True
                for key in ("pv_power", "grid_power", "house_power", "surplus", "wallbox_power", "water_power"):
                    if snapshot.get(key) is not None:
                        await client.publish(f"{self.base}/status/{key}", str(snapshot[key]), retain=True)
                battery = snapshot.get("battery")
                if battery:
                    await client.publish(f"{self.base}/status/battery_soc", str(battery["soc"]), retain=True)
                    await client.publish(f"{self.base}/status/battery_power", str(battery["power"]), retain=True)
                await client.publish(f"{self.base}/status/snapshot", json.dumps(snapshot, default=str), retain=True)
            except Exception as exc:  # noqa: BLE001
                log.debug("MQTT-Publish fehlgeschlagen: %s", exc)
                self._client = None  # Reconnect beim nächsten Versuch

    async def _send_discovery(self, client) -> None:
        sensors = {
            "pv_power": ("PV-Leistung", "W", "power"),
            "grid_power": ("Netzleistung", "W", "power"),
            "house_power": ("Hausverbrauch", "W", "power"),
            "battery_soc": ("Batterie SoC", "%", "battery"),
            "battery_power": ("Batterie-Leistung", "W", "power"),
            "wallbox_power": ("Ladeleistung", "W", "power"),
        }
        device = {"identifiers": ["minepower"], "name": "MinePower", "manufacturer": "MinePower"}
        for key, (name, unit, dclass) in sensors.items():
            payload = {
                "name": name,
                "state_topic": f"{self.base}/status/{key}",
                "unit_of_measurement": unit,
                "device_class": dclass,
                "unique_id": f"minepower_{key}",
                "device": device,
            }
            await client.publish(f"homeassistant/sensor/minepower/{key}/config", json.dumps(payload), retain=True)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._client = None
