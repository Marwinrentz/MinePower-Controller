"""Gemeinsame Bausteine der Wallbox-Treiber.

* ``iec_state``: Ladezustand nach IEC 61851 (A–F) in ``WallboxState``.
* ``regs_text``: ASCII-Text aus Modbus-Registern (zwei Zeichen je Register).
* ``LimitWallbox``: Basis für Boxen, bei denen der Ladestrom zugleich die
  Freigabe ist (0 A = gesperrt). Start schreibt den zuletzt verlangten
  Strom, Stopp schreibt 0. Der Schreibtest ändert nie den Ladezustand.
* Lebenszeichen: Geräte mit Watchdog verlangen regelmäßige Schreib- oder
  Lesezugriffe. Ein Hintergrund-Task sendet sie unabhängig vom Regeltakt.
* Feldvorlagen, damit alle Wallbox-Treiber dieselben Bezeichnungen haben.

Registerbelegungen und Abläufe der Modbus-, HTTP- und Cloud-Treiber folgen
den Herstellerdokumentationen. Wo diese lückenhaft sind, dient der
Quelltext von evcc (MIT-Lizenz, github.com/evcc-io/evcc) als Referenz.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import ConfigField, FieldType, WallboxData, WallboxDriver, WallboxState
from .modbus_util import ModbusConnection

log = logging.getLogger(__name__)

IEC_STATES = {
    "A": WallboxState.IDLE,
    "B": WallboxState.CONNECTED,
    "C": WallboxState.CHARGING,
    "D": WallboxState.CHARGING,
    "E": WallboxState.ERROR,
    "F": WallboxState.ERROR,
}


def iec_state(text: str | None) -> WallboxState:
    """'A', 'B1', 'C2', 'E' … → Zustand. Unbekannt gilt als Fehler."""
    letter = (text or "").strip()[:1].upper()
    return IEC_STATES.get(letter, WallboxState.ERROR)


def regs_text(regs: list[int]) -> str:
    """Register als ASCII (High-Byte zuerst), ohne Nullbytes und Leerzeichen am Rand."""
    raw = b"".join(int(r & 0xFFFF).to_bytes(2, "big") for r in regs)
    return raw.replace(b"\x00", b"").decode("ascii", errors="replace").strip()


def active_phases(currents: list[float], threshold: float = 1.0) -> int | None:
    """Zahl der Phasen mit nennenswertem Strom; None, wenn keine lädt."""
    n = sum(1 for c in currents if c is not None and c > threshold)
    return n or None


def f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- Feldvorlagen

def host_field(placeholder: str = "192.0.2.20", label: str = "IP-Adresse") -> ConfigField:
    return ConfigField(key="host", label=label, placeholder=placeholder)


def port_field(default: int = 502) -> ConfigField:
    return ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=default)


def unit_field(default: int = 1) -> ConfigField:
    return ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=default)


MAX_CURRENT_FIELD = ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                                help="6–32 A, nicht über der Absicherung")
PHASES_FIELD = ConfigField(key="phases", label="Phasen", type=FieldType.SELECT, default="3",
                           options=[{"value": "1", "label": "1"}, {"value": "3", "label": "3"}])
GATEWAY_FIELD = ConfigField(
    key="framer", label="Gateway-Protokoll", type=FieldType.SELECT, default="rtu",
    options=[{"value": "rtu", "label": "Modbus RTU über TCP"},
             {"value": "socket", "label": "Modbus TCP (Gateway setzt um)"}],
    help="RS485-Gateway, z. B. Waveshare, USR-TCP232",
)


def modbus_fields(*, port: int = 502, unit: int = 1, placeholder: str = "192.0.2.20",
                  gateway: bool = False) -> list[ConfigField]:
    fields = [host_field(placeholder, "IP-Adresse (Gateway)" if gateway else "IP-Adresse"),
              port_field(port), unit_field(unit)]
    if gateway:
        fields.append(GATEWAY_FIELD)
    return fields


def phases_of(config: dict) -> int:
    return 1 if str(config.get("phases", "3")) == "1" else 3


# ---------------------------------------------------------------- Basisklassen

class Heartbeat:
    """Lebenszeichen per Hintergrund-Task für Geräte mit Watchdog."""

    #: Abstand der Lebenszeichen in s (0 = Gerät hat keinen Watchdog)
    heartbeat_s: float = 0.0
    _beat_task: asyncio.Task | None = None

    async def heartbeat(self) -> None:
        """Lebenszeichen an das Gerät (Treiber mit Watchdog überschreiben das)."""

    def start_heartbeat(self) -> None:
        if self.heartbeat_s and (self._beat_task is None or self._beat_task.done()):
            self._beat_task = asyncio.create_task(self._beat_loop())

    def stop_heartbeat(self) -> None:
        if self._beat_task is not None:
            self._beat_task.cancel()
            self._beat_task = None

    async def _beat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            try:
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 – nächster Versuch im nächsten Takt
                log.debug("%s: Lebenszeichen fehlgeschlagen: %s", type(self).__name__, exc)


class ModbusWallbox(Heartbeat, WallboxDriver):
    """Modbus-Wallbox mit eigener Verbindung, Lebenszeichen und Strombegrenzung."""

    default_framer = "socket"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_current = f(config.get("max_current"), 16.0) or 16.0
        self.phases = phases_of(config)
        self.conn = ModbusConnection(
            host=str(config.get("host", "")).strip(),
            port=int(f(config.get("port"), 502) or 502),
            unit_id=int(f(config.get("unit_id"), 1)),
            framer=str(config.get("framer") or self.default_framer),
            name=self.meta.name,
        )

    async def connect(self) -> None:
        await self.conn.connect()
        self.start_heartbeat()

    async def disconnect(self) -> None:
        self.stop_heartbeat()
        await self.conn.close()

    def clamp(self, amps: float) -> float:
        return max(self.min_current, min(float(amps), self.max_current))


class LimitWallbox(ModbusWallbox):
    """Ladestrom ist zugleich Freigabe: 0 A sperrt, ≥ 6 A gibt frei."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._amps = self.min_current
        self._enabled = False

    async def write_limit(self, amps: float) -> None:
        raise NotImplementedError

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self.write_limit(self._amps)
        self._enabled = True

    async def start_charging(self) -> None:
        await self.write_limit(self._amps)
        self._enabled = True

    async def stop_charging(self) -> None:
        await self.write_limit(0)
        self._enabled = False

    async def test_command(self) -> dict[str, Any] | None:
        before = await self.read_data()
        if before.current_set:
            return await super().test_command()
        # Gesperrt: Sperre erneut schreiben, damit der Test nie eine Ladung startet
        await self.write_limit(0)
        after = await self.read_data()
        ok = not after.current_set
        return {
            "ok": ok,
            "message": "Sperre (0 A) gesendet, " + ("bestätigt" if ok else
                                                     f"Gerät meldet {f(after.current_set):.1f} A"),
            "sent": 0, "readback": after.current_set,
        }


def wallbox_data(state: WallboxState, power: float, *, current_set: float | None = None,
                 currents: list[float] | None = None, voltage: float | None = None,
                 energy_session_kwh: float | None = None, soc: float | None = None,
                 extra: dict | None = None, source: str = "") -> WallboxData:
    """WallboxData mit Plausibilitätsprüfung der Leistung."""
    from .validation import checked, sanitized

    return WallboxData(
        state=state,
        power=checked("wallbox_power", max(0.0, float(power)), source=source),
        current_set=current_set,
        phases_active=active_phases(currents) if currents else None,
        voltage=voltage if voltage and voltage > 50 else None,
        energy_session_kwh=sanitized("energy_kwh", energy_session_kwh) if energy_session_kwh is not None else None,
        soc=sanitized("vehicle_soc", soc) if soc is not None else None,
        extra=extra or {},
    )
