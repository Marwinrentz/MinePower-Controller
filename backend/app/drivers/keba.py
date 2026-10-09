"""KEBA KeContact P30 (c-/x-Serie) über die UDP-Schnittstelle (Port 7090).

Befehle laut KEBA 'UDP Programmers Guide":

    report 2    Zustand (State, Plug, Max curr …)
    report 3    Messwerte (P in mW, I1–I3 in mA, E pres in 0,1 Wh)
    curr <mA>   Ladestrom 6000–63000 mA
    ena 1|0     Laden freigeben / sperren

Die Wallbox antwortet an den UDP-Port 7090 des Absenders. MinePower muss
deshalb selbst auf Port 7090 empfangen (bei Docker: ``7090:7090/udp``
freigeben). In der Wallbox muss 'UDP-Schnittstelle" (DIP-Schalter bzw.
Web-Oberfläche) aktiv sein.
"""
from __future__ import annotations

import asyncio
import json
import logging

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WallboxData, WallboxDriver, WallboxState
from .registry import register
from .validation import ConnectionProblem, checked

log = logging.getLogger(__name__)


class _Endpoint(asyncio.DatagramProtocol):
    """Gemeinsamer UDP-Empfänger je lokalem Port, verteilt Antworten je Absender."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.waiters: dict[str, asyncio.Queue] = {}

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        queue = self.waiters.get(addr[0])
        if queue is not None:
            queue.put_nowait(data.decode(errors="replace"))


_ENDPOINTS: dict[int, _Endpoint] = {}
_LOCK = asyncio.Lock()


async def _endpoint(port: int) -> _Endpoint:
    async with _LOCK:
        ep = _ENDPOINTS.get(port)
        if ep is None or ep.transport is None or ep.transport.is_closing():
            loop = asyncio.get_running_loop()
            _, ep = await loop.create_datagram_endpoint(_Endpoint, local_addr=("0.0.0.0", port))
            _ENDPOINTS[port] = ep
        return ep


@register
class KebaP30(WallboxDriver):
    meta = DriverMeta(
        id="keba_p30", name="KEBA KeContact P30 (UDP)", category=DeviceCategory.WALLBOX,
        description="KEBA P30 c-/x-Serie über die UDP-Schnittstelle.",
        capabilities={"write_test"},
        notes="UDP-Schnittstelle in der Wallbox aktivieren; Port 7090/udp zum Container freigeben.",
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.15"),
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16),
            ConfigField(key="phases", label="Phasen", type=FieldType.SELECT, default="3",
                        options=[{"value": "1", "label": "1"}, {"value": "3", "label": "3"}]),
            ConfigField(key="local_port", label="Lokaler UDP-Port", type=FieldType.NUMBER, default=7090,
                        required=False),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.host = str(config.get("host", "")).strip()
        self.max_current = float(config.get("max_current") or 16)
        self.phases = 1 if str(config.get("phases", "3")) == "1" else 3
        self.local_port = int(float(config.get("local_port") or 7090))
        self._lock = asyncio.Lock()
        self._ep: _Endpoint | None = None

    async def connect(self) -> None:
        self._ep = await _endpoint(self.local_port)

    async def disconnect(self) -> None:
        if self._ep is not None:
            self._ep.waiters.pop(self.host, None)

    async def _send(self, command: str, *, expect_json: bool) -> dict | str:
        if self._ep is None or self._ep.transport is None:
            await self.connect()
        assert self._ep is not None and self._ep.transport is not None
        async with self._lock:
            queue: asyncio.Queue = asyncio.Queue()
            self._ep.waiters[self.host] = queue
            self._ep.transport.sendto(command.encode(), (self.host, 7090))
            try:
                while True:
                    text = await asyncio.wait_for(queue.get(), timeout=3.0)
                    if not expect_json:
                        return text
                    if text.strip().startswith("{"):
                        return json.loads(text)
            except asyncio.TimeoutError:
                raise ConnectionProblem(f"KEBA {self.host}: keine Antwort auf '{command}' (UDP 7090)") from None
            finally:
                await asyncio.sleep(0.12)  # KEBA: Mindestabstand zwischen Befehlen

    async def read_data(self) -> WallboxData:
        r2 = await self._send("report 2", expect_json=True)
        r3 = await self._send("report 3", expect_json=True)
        assert isinstance(r2, dict) and isinstance(r3, dict)
        state, plug = int(r2.get("State", 0)), int(r2.get("Plug", 0))
        power = float(r3.get("P", 0)) / 1000.0
        if state == 4:
            st = WallboxState.ERROR
        elif state == 3:
            st = WallboxState.CHARGING
        elif plug >= 5:
            st = WallboxState.CONNECTED
        else:
            st = WallboxState.IDLE
        currents = [float(r3.get(f"I{i}", 0)) for i in (1, 2, 3)]
        active = sum(1 for c in currents if c > 1000) or None
        return WallboxData(
            state=st, power=checked("wallbox_power", power, source="KEBA report 3"),
            current_set=float(r2.get("Curr user", 0)) / 1000.0,
            phases_active=active, voltage=float(r3.get("U1") or 0) or None,
            energy_session_kwh=float(r3.get("E pres", 0)) / 10000.0,
        )

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        ma = int(max(6.0, min(amps, self.max_current)) * 1000)
        await self._send(f"curr {ma}", expect_json=False)

    async def start_charging(self) -> None:
        await self._send("ena 1", expect_json=False)

    async def stop_charging(self) -> None:
        await self._send("ena 0", expect_json=False)
