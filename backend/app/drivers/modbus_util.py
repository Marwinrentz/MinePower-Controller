"""Gemeinsame Modbus-Hilfen für alle Modbus-Treiber (pymodbus 3.x, async).

Alles, was Modbus im Dauerbetrieb zuverlässig macht, steckt hier – damit
jeder Treiber es automatisch bekommt und nicht jeder sein eigenes
Retry-Gebastel erfindet:

* **Ein Lock je Verbindung.** Modbus verträgt keine parallelen Anfragen auf
  derselben Verbindung. Alle Zugriffe (auch die Readbacks nach dem Schreiben)
  laufen serialisiert. Das ist Pflicht für jeden neuen Modbus-Treiber.
* **Automatischer Reconnect.** Bricht die Verbindung weg (Router-Neustart,
  Firmware-Update des Wechselrichters, WLAN-Aussetzer), wird genau einmal
  neu verbunden und die Anfrage wiederholt, bevor ein Fehler nach oben geht.
  Ein Firmware-Update wirkt damit wie ein kurzer Aussetzer statt wie ein
  Dauerausfall.
* **Klartext statt Rohausnahme.** Modbus-Exception-Codes werden in Sätze
  übersetzt, die dem Nutzer sagen, *was zu prüfen ist* (Unit-ID, Register,
  Wertebereich).
* **Readback nach dem Schreiben.** ``write_register(..., verify=True)`` liest
  das Register erneut und meldet :class:`CommandNotApplied`, wenn das Gerät
  den Wert stillschweigend verworfen hat – der häufigste Fall bei falschem
  Betriebsmodus oder gesperrten Registern. 'Befehl gesendet" heißt damit
  nie mehr automatisch 'Befehl ausgeführt".
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import struct
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from .validation import CommandNotApplied, ConnectionProblem, DeviceRejected

log = logging.getLogger(__name__)

WordOrder = Literal["big", "little"]

#: Modbus-Exception-Codes → Klartext mit Handlungshinweis
MODBUS_EXCEPTIONS: dict[int, str] = {
    1: "Funktionscode wird vom Gerät nicht unterstützt (Register-Typ Input/Holding vertauscht?)",
    2: "Registeradresse existiert auf diesem Gerät nicht – Registerkarte bzw. Firmware-Stand prüfen",
    3: "Wert außerhalb des vom Gerät erlaubten Bereichs",
    4: "Gerät meldet einen internen Fehler",
    5: "Gerät bestätigt den Auftrag, braucht aber länger (Acknowledge)",
    6: "Gerät ist beschäftigt – ein anderer Modbus-Master greift vermutlich gleichzeitig zu",
    8: "Speicher-Paritätsfehler im Gerät",
    10: "Gateway: Zielgerät nicht erreichbar – Unit-ID (Slave-Adresse) prüfen",
    11: "Gateway: Zielgerät antwortet nicht – Unit-ID und Verkabelung prüfen",
}

#: Fehler, bei denen ein einmaliger Reconnect sinnvoll ist
_TRANSIENT = (asyncio.TimeoutError, ConnectionError, OSError, EOFError)

#: So viele Schreibzugriffe merkt sich jede Verbindung für die Diagnose.
WRITE_LOG_SIZE = 20


def _unit_kwarg(client) -> str:
    """Name des Slave-Parameters ermitteln (pymodbus benannte ihn über die
    Versionen mehrfach um: unit → slave → device_id). Einmalig pro Client."""
    for method in ("read_holding_registers", "read_input_registers"):
        fn = getattr(client, method, None)
        if fn is None:
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):  # C-Implementierung / Wrapper
            continue
        for name in ("slave", "device_id", "unit"):
            if name in params:
                return name
    return "slave"


class ModbusConnection:
    """Dünner Wrapper um pymodbus: Lock, Reconnect, Klartext-Fehler, Readback."""

    def __init__(
        self,
        host: str = "",
        port: int = 502,
        unit_id: int = 1,
        mode: Literal["tcp", "rtu"] = "tcp",
        serial_port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        timeout: float = 3.0,
        name: str = "Modbus-Gerät",
        framer: Literal["socket", "rtu", "ascii"] | None = None,
        parity: str = "N",
        stopbits: int = 1,
    ) -> None:
        self.unit_id = int(unit_id)
        self.name = name
        self.mode = mode
        self.timeout = float(timeout)
        self._host = host
        self._port = int(port)
        self._serial_port = serial_port
        self._baudrate = int(baudrate)
        #: Rahmenformat: 'socket' (Modbus TCP), 'rtu' (seriell oder RTU über
        #: TCP-Gateway), 'ascii' (Modbus ASCII, z. B. ABL eMH1).
        self.framer = framer or ("rtu" if mode == "rtu" else "socket")
        self._parity = str(parity or "N").upper()[:1]
        self._stopbits = int(stopbits or 1)
        self._lock = asyncio.Lock()
        self.client = self._build_client()
        self._unit_key = _unit_kwarg(self.client)
        #: Die letzten Schreibzugriffe mit Ergebnis – für die Diagnose. Ohne
        #: das bleibt bei 'Batterie lädt nicht" nur Raten, ob überhaupt etwas
        #: gesendet wurde, ob das Gerät es abgelehnt oder still verworfen hat.
        self.write_log: deque[dict[str, Any]] = deque(maxlen=WRITE_LOG_SIZE)

    # ------------------------------------------------------------ Verbindung

    def _build_client(self):
        framer = {"rtu": FramerType.RTU, "ascii": FramerType.ASCII}.get(self.framer, FramerType.SOCKET)
        if self.mode == "rtu":
            # Seriell gibt es kein Socket-Rahmenformat: Vorgabe RTU
            return AsyncModbusSerialClient(
                port=self._serial_port, baudrate=self._baudrate, timeout=self.timeout,
                framer=framer if framer != FramerType.SOCKET else FramerType.RTU,
                parity=self._parity, stopbits=self._stopbits,
            )
        return AsyncModbusTcpClient(host=self._host, port=self._port, timeout=self.timeout, framer=framer)

    def target(self) -> str:
        if self.mode == "rtu":
            return f"{self._serial_port} @ {self._baudrate} Bd, Unit-ID {self.unit_id}"
        return f"{self._host}:{self._port}, Unit-ID {self.unit_id}"

    @property
    def connected(self) -> bool:
        return bool(getattr(self.client, "connected", False))

    async def connect(self) -> None:
        """Idempotent – wird bei jedem Reconnect erneut gerufen."""
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self.connected:
            return
        if not self._host and self.mode == "tcp":
            raise ConnectionProblem(f"{self.name}: keine IP-Adresse konfiguriert")
        try:
            ok = await self.client.connect()
        except Exception as exc:  # noqa: BLE001 – jede Transportausnahme wird übersetzt
            raise ConnectionProblem(
                f"{self.name}: Verbindung zu {self.target()} fehlgeschlagen – {exc}"
            ) from exc
        if not ok or not self.connected:
            raise ConnectionProblem(
                f"{self.name}: keine Verbindung zu {self.target()}. "
                f"Erreichbarkeit, Port und – falls vorhanden – die Option "
                f"'Modbus TCP aktivieren' im Gerät prüfen."
            )

    async def _reset_locked(self) -> None:
        """Verbindung verwerfen und neu aufbauen (nach Transportfehler)."""
        try:
            self.client.close()
        except Exception:  # noqa: BLE001 – ein toter Socket darf hier nichts werfen
            pass
        self.client = self._build_client()
        await self._connect_locked()

    async def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ Zugriffe

    async def read_input(self, address: int, count: int = 1) -> list[int]:
        async with self._lock:
            return await self._registers("input", address, count)

    async def read_holding(self, address: int, count: int = 1) -> list[int]:
        async with self._lock:
            return await self._registers("holding", address, count)

    async def read_coils(self, address: int, count: int = 1) -> list[bool]:
        async with self._lock:
            result = await self._request("coils", address, count=count)
        bits = list(getattr(result, "bits", []) or [])
        if len(bits) < count:
            raise ConnectionProblem(f"{self.name}: unvollständige Antwort auf Coil {address}.")
        return [bool(b) for b in bits[:count]]

    async def write_coil(self, address: int, value: bool) -> None:
        async with self._lock:
            await self._request("write_coil", address, values=[bool(value)])

    async def write_register(
        self, address: int, value: int, *, verify: bool = False, expect: int | None = None
    ) -> int | None:
        """Ein Register schreiben.

        `verify=True` liest das Register direkt danach zurück und wirft
        :class:`CommandNotApplied`, wenn der Wert nicht übernommen wurde.
        `expect` erlaubt einen abweichenden Erwartungswert für Register, die
        den geschriebenen Wert normalisieren (z. B. Kommando- auf Statuscodes).
        """
        value = int(value) & 0xFFFF
        entry: dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # 1-basiert wie in den Herstellerdokus – so lässt sich der Eintrag
            # direkt mit der Registerkarte vergleichen.
            "register": address + 1,
            "value": value,
            "readback": None,
            "ok": False,
            "error": None,
        }
        started = time.monotonic()
        try:
            async with self._lock:
                await self._request("write_register", address, values=[value])
                if not verify:
                    entry["ok"] = True
                    return None
                got = (await self._registers("holding", address, 1))[0]
            entry["readback"] = got
            want = value if expect is None else int(expect) & 0xFFFF
            if got != want:
                raise CommandNotApplied(
                    f"{self.name}: Register {address + 1} nicht übernommen "
                    f"(geschrieben {want}, zurückgelesen {got}). Das Gerät lehnt den Wert ab – "
                    f"meist ist der externe Steuermodus im Gerät nicht aktiv oder das Register gesperrt."
                )
            entry["ok"] = True
            return got
        except Exception as exc:
            entry["error"] = str(exc)
            raise
        finally:
            entry["ms"] = round((time.monotonic() - started) * 1000)
            self.write_log.append(entry)

    async def write_registers(
        self, address: int, values: list[int], *, verify: bool = False
    ) -> list[int] | None:
        payload = [int(v) & 0xFFFF for v in values]
        async with self._lock:
            await self._request("write_registers", address, values=payload)
            if not verify:
                return None
            got = await self._registers("holding", address, len(payload))
        if got != payload:
            raise CommandNotApplied(
                f"{self.name}: Register {address}+{len(payload)} nicht übernommen "
                f"(geschrieben {payload}, zurückgelesen {got})."
            )
        return got

    # ------------------------------------------------------------ Intern

    async def _registers(self, kind: str, address: int, count: int) -> list[int]:
        result = await self._request(kind, address, count=count)
        registers = list(getattr(result, "registers", []) or [])
        if len(registers) < count:
            raise ConnectionProblem(
                f"{self.name}: unvollständige Antwort auf {_op(kind)} {address} "
                f"({len(registers)} statt {count} Register)."
            )
        return registers

    async def _request(self, kind: str, address: int, count: int = 1, values=None, _attempt: int = 0):
        """Eine Modbus-Anfrage – mit genau einem Reconnect-Versuch.

        Muss unter gehaltenem `self._lock` gerufen werden.
        """
        await self._connect_locked()
        try:
            result = await self._call(kind, address, count, values)
        except _TRANSIENT as exc:
            if _attempt == 0:
                log.debug("%s: Transportfehler bei %s %s (%s) – reconnect", self.name, kind, address, exc)
                await self._reset_locked()
                return await self._request(kind, address, count, values, _attempt=1)
            raise ConnectionProblem(
                f"{self.name}: {self.target()} antwortet nicht ({type(exc).__name__}: {exc})."
            ) from exc
        except Exception as exc:  # noqa: BLE001 – pymodbus-interne Fehler übersetzen
            raise ConnectionProblem(f"{self.name}: {_op(kind)} {address} fehlgeschlagen – {exc}") from exc

        if not result.isError():
            return result

        code = getattr(result, "exception_code", None)
        if code is not None:
            # Das Gerät hat bewusst geantwortet – ein Retry ändert nichts.
            raise DeviceRejected(
                f"{self.name}: {_op(kind)} {address} abgelehnt – "
                f"{MODBUS_EXCEPTIONS.get(int(code), f'Modbus-Fehlercode {code}')}."
            )
        if _attempt == 0:
            # Keine gültige Antwort (Framing/Timeout) → einmal neu verbinden.
            await self._reset_locked()
            return await self._request(kind, address, count, values, _attempt=1)
        raise ConnectionProblem(
            f"{self.name}: keine gültige Antwort auf {_op(kind)} {address} ({result})."
        )

    async def _call(self, kind: str, address: int, count: int, values):
        unit = {self._unit_key: self.unit_id}
        if kind == "input":
            return await self.client.read_input_registers(address=address, count=count, **unit)
        if kind == "holding":
            return await self.client.read_holding_registers(address=address, count=count, **unit)
        if kind == "write_register":
            return await self.client.write_register(address=address, value=values[0], **unit)
        if kind == "write_registers":
            return await self.client.write_registers(address=address, values=values, **unit)
        if kind == "coils":
            return await self.client.read_coils(address=address, count=count, **unit)
        if kind == "write_coil":
            return await self.client.write_coil(address=address, value=values[0], **unit)
        raise ValueError(f"Unbekannte Modbus-Operation: {kind}")


# ------------------------------------------------------------ Verbindungs-Pool

_POOL: dict[tuple, ModbusConnection] = {}


def acquire_connection(**kwargs) -> ModbusConnection:
    """Gemeinsame Verbindung je (Host, Port, Unit-ID) – mit Referenzzählung.

    Viele Geräte bündeln mehrere logische Geräte hinter einer Modbus-Adresse:
    Beim Sungrow SH liefert dieselbe IP Wechselrichter, DTSU666-Zähler *und*
    Batterie. Würde jedes davon eine eigene TCP-Verbindung aufmachen, träfe
    es genau die Schwäche solcher Dongles (WiNet-S verkraftet nur sehr wenige
    parallele Modbus-Sessions und antwortet dann sporadisch gar nicht mehr).

    Über den Pool teilen sich diese Treiber eine Verbindung **inklusive deren
    Lock** – die Anfragen werden also sauber serialisiert, statt sich
    gegenseitig die Antworten zu zerschießen.
    """
    key = (
        kwargs.get("mode", "tcp"),
        kwargs.get("host", ""),
        int(kwargs.get("port", 502) or 502),
        int(kwargs.get("unit_id", 1) or 1),
        kwargs.get("serial_port", ""),
        kwargs.get("framer") or "",
    )
    conn = _POOL.get(key)
    if conn is None:
        conn = ModbusConnection(**kwargs)
        conn._pool_key = key  # type: ignore[attr-defined]
        conn._refs = 0  # type: ignore[attr-defined]
        _POOL[key] = conn
    conn._refs += 1  # type: ignore[attr-defined]
    return conn


async def release_connection(conn: ModbusConnection | None) -> None:
    """Gegenstück zu :func:`acquire_connection` – schließt erst beim letzten Nutzer."""
    if conn is None:
        return
    refs = getattr(conn, "_refs", 1) - 1
    conn._refs = max(0, refs)  # type: ignore[attr-defined]
    if refs > 0:
        return
    key = getattr(conn, "_pool_key", None)
    if key is not None:
        _POOL.pop(key, None)
    await conn.close()


def _op(kind: str) -> str:
    return {
        "input": "Input-Register",
        "holding": "Holding-Register",
        "write_register": "Schreiben Register",
        "write_registers": "Schreiben Register-Block",
        "coils": "Coil",
        "write_coil": "Schreiben Coil",
    }.get(kind, kind)


# ------------------------------------------------------------ Decoder

def u16(regs: list[int], idx: int = 0) -> int:
    return regs[idx]


def s16(regs: list[int], idx: int = 0) -> int:
    v = regs[idx]
    return v - 0x10000 if v >= 0x8000 else v


def u32(regs: list[int], idx: int = 0, word_order: WordOrder = "little") -> int:
    hi, lo = (regs[idx + 1], regs[idx]) if word_order == "little" else (regs[idx], regs[idx + 1])
    return (hi << 16) | lo


def s32(regs: list[int], idx: int = 0, word_order: WordOrder = "little") -> int:
    v = u32(regs, idx, word_order)
    return v - 0x1_0000_0000 if v >= 0x8000_0000 else v


def f32(regs: list[int], idx: int = 0, word_order: WordOrder = "big") -> float:
    hi, lo = (regs[idx], regs[idx + 1]) if word_order == "big" else (regs[idx + 1], regs[idx])
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]


def u64(regs: list[int], idx: int = 0, word_order: WordOrder = "big") -> int:
    words = regs[idx : idx + 4]
    if word_order == "little":
        words = list(reversed(words))
    value = 0
    for w in words:
        value = (value << 16) | w
    return value


def s64(regs: list[int], idx: int = 0, word_order: WordOrder = "big") -> int:
    v = u64(regs, idx, word_order)
    return v - (1 << 64) if v >= (1 << 63) else v


#: Sammel-Marker vieler Hersteller für 'Wert nicht verfügbar" (SMA, SunSpec …)
NAN_MARKERS = {0x8000, 0xFFFF, 0x7FFF}
NAN_MARKERS_32 = {0x8000_0000, 0xFFFF_FFFF, 0x7FFF_FFFF}


def nan_to_none(value: float, *, markers: set[int] | None = None) -> float | None:
    """Herstellerspezifische 'kein Wert"-Marker in ``None`` übersetzen."""
    try:
        raw = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if raw in (markers or NAN_MARKERS_32):
        return None
    return value
