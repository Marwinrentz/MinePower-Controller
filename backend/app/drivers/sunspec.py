"""SunSpec-Modbus – ein Treiber für viele Hersteller.

SunSpec ist der herstellerübergreifende Modbus-Standard, den u. a. Kostal
(Plenticore), SMA (Tripower X, Sunny Boy), Fronius (GEN24), GoodWe (ET/EH),
SolarEdge, Delta und Huawei (teilweise) sprechen. Der entscheidende Vorteil
gegenüber handgepflegten Registerkarten: **Das Gerät beschreibt sich selbst.**

Ablauf:
  1. An einer der Basisadressen (40000, 50000, 0) stehen die zwei Register
     'SunS' (0x5375 0x6E53).
  2. Danach folgt eine Kette von Modellen, je mit Kopf (Modell-ID, Länge).
  3. Wir laufen die Kette ab und merken uns, wo welches Modell liegt.

Damit ist der Treiber unabhängig vom Firmware-Stand: Verschiebt ein Update
die Register, findet der Scan sie beim nächsten Verbindungsaufbau wieder.
Genau deshalb ist SunSpec hier die bevorzugte Art, weitere Marken
anzubinden – statt für jede Firmware eine eigene Registerkarte zu pflegen.

Verwendete Modelle:
  1                Common (Hersteller, Modell, Seriennummer)
  101/102/103      Wechselrichter (Integer + Skalierungsfaktoren)
  111/112/113      Wechselrichter (Float32)
  201–204          Zähler (Integer + Skalierungsfaktoren)
  211–214          Zähler (Float32)
  802              Batterie (DERBattery)
"""
from __future__ import annotations

import logging
from typing import Any

from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    InverterData,
    InverterDriver,
    Maturity,
    MeterData,
    MeterDriver,
)
from .modbus_util import ModbusConnection, acquire_connection, f32, release_connection, s16, u16, u32
from .registry import register
from .validation import DriverError, checked, sanitized

log = logging.getLogger(__name__)

SUNS_MARKER = (0x5375, 0x6E53)  # "SunS"
BASE_ADDRESSES = (40000, 50000, 0)
END_MODEL = 0xFFFF

INVERTER_MODELS_INT = (101, 102, 103)
INVERTER_MODELS_FLOAT = (111, 112, 113)
METER_MODELS_INT = (201, 202, 203, 204)
METER_MODELS_FLOAT = (211, 212, 213, 214)
BATTERY_MODEL = 802

# Offsets innerhalb der Modell-Nutzlast (0-basiert), Integer-Varianten
INV_W, INV_W_SF = 14, 15          # AC-Wirkleistung
INV_WH, INV_WH_SF = 24, 26        # Gesamtertrag (acc32)
INV_DCW, INV_DCW_SF = 31, 32      # DC-Leistung (= PV-Leistung)
# Float-Varianten (111/112/113)
INV_W_F, INV_WH_F, INV_DCW_F = 14, 22, 30

MTR_W, MTR_W_SF = 16, 20          # Wirkleistung gesamt (Modelle 201–204)
MTR_W_F = 16                      # Float-Varianten (211–214)

BAT_SOC = 9                       # DERBattery: State of Charge in %
BAT_W = 40                        # DERBattery: Leistung (herstellerabhängig)

COMMON_FIELDS = [
    ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.25",
                help="IP des Wechselrichters bzw. Zählers. Modbus TCP muss im Gerät aktiviert "
                     "sein – bei Kostal unter Einstellungen → Modbus/SunSpec, bei SMA unter "
                     "Externe Kommunikation."),
    ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=502,
                help="Modbus-TCP-Port, Standard 502. SolarEdge nutzt teils 1502."),
    ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=1,
                help="Modbus-Slave-Adresse. Kostal/GoodWe: 1 · SMA: 3 · SolarEdge: 1. "
                     "Meldet der Test 'Zielgerät nicht erreichbar', hier variieren."),
    ConfigField(key="base_address", label="SunSpec-Basisadresse", type=FieldType.SELECT, default="auto",
                required=False,
                options=[{"value": "auto", "label": "Automatisch suchen (empfohlen)"},
                         {"value": "40000", "label": "40000 (Standard)"},
                         {"value": "50000", "label": "50000"},
                         {"value": "0", "label": "0"}],
                help="Wo die SunSpec-Kennung steht. 'Automatisch' probiert alle drei Varianten – "
                     "nur festlegen, wenn der Scan zu lange dauert."),
]


class _SunSpecBase:
    """Gemeinsamer Modell-Scan für Wechselrichter-, Zähler- und Batteriezugriff."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)  # type: ignore[call-arg]
        self.conn: ModbusConnection = acquire_connection(
            host=str(config.get("host", "")).strip(),
            port=int(config.get("port") or 502),
            unit_id=int(config.get("unit_id") or 1),
            timeout=5.0,
            name="SunSpec-Gerät",
        )
        raw_base = str(config.get("base_address") or "auto")
        self._bases = BASE_ADDRESSES if raw_base == "auto" else (int(raw_base),)
        #: Modell-ID → (Startadresse der Nutzlast, Länge)
        self.models: dict[int, tuple[int, int]] = {}
        self.info: dict[str, str] = {}

    async def connect(self) -> None:
        await self.conn.connect()
        if not self.models:
            await self._scan()

    async def disconnect(self) -> None:
        self.models = {}
        await release_connection(self.conn)

    # ------------------------------------------------------------ Scan

    async def _scan(self) -> None:
        for base in self._bases:
            try:
                marker = await self.conn.read_holding(base, 2)
            except Exception:  # noqa: BLE001 – Basisadresse existiert nicht → nächste probieren
                continue
            if tuple(marker) != SUNS_MARKER:
                continue
            await self._walk(base + 2)
            if self.models:
                log.info(
                    "SunSpec an Basis %s erkannt: Modelle %s",
                    base, sorted(self.models),
                )
                return
        raise DriverError(
            "Keine SunSpec-Kennung gefunden. Entweder spricht das Gerät kein SunSpec, oder "
            "Modbus/SunSpec ist im Gerät nicht aktiviert. Geprüfte Basisadressen: "
            + ", ".join(str(b) for b in self._bases)
        )

    async def _walk(self, address: int) -> None:
        """Modellkette ablaufen. Hart begrenzt, damit ein Gerät mit
        unerwarteten Daten keine Endlosschleife im Regelkreis auslöst."""
        self.models = {}
        for _ in range(64):
            header = await self.conn.read_holding(address, 2)
            model_id, length = u16(header, 0), u16(header, 1)
            if model_id == END_MODEL or length == 0 and model_id == 0:
                return
            self.models[model_id] = (address + 2, length)
            address += 2 + length
            if length > 512:  # unplausible Modelllänge → Kette abbrechen
                return

    async def _payload(self, model_id: int, count: int | None = None) -> list[int]:
        start, length = self.models[model_id]
        return await self.conn.read_holding(start, min(count or length, length))

    def _first(self, candidates) -> int | None:
        return next((m for m in candidates if m in self.models), None)

    async def read_common(self) -> dict[str, str]:
        if 1 not in self.models or self.info:
            return self.info
        regs = await self._payload(1, 66)
        self.info = {
            "Hersteller": _string(regs, 0, 16),
            "Modell": _string(regs, 16, 16),
            "Firmware": _string(regs, 40, 8),
            "Seriennummer": _string(regs, 48, 16),
        }
        return self.info


def _string(regs: list[int], offset: int, words: int) -> str:
    raw = bytearray()
    for reg in regs[offset : offset + words]:
        raw += bytes(((reg >> 8) & 0xFF, reg & 0xFF))
    return raw.decode("ascii", "ignore").strip("\x00 ").strip()


def _scaled(value: float, sf: int) -> float:
    """SunSpec-Skalierungsfaktor anwenden (Zehnerpotenz, int16)."""
    if sf in (0x8000, None):  # 'nicht implementiert'
        return float(value)
    return float(value) * (10.0 ** sf)


@register
class SunSpecInverter(_SunSpecBase, InverterDriver):
    meta = DriverMeta(
        id="sunspec_inverter",
        name="SunSpec-Wechselrichter (Kostal, GoodWe, SolarEdge, Delta, SMA …)",
        category=DeviceCategory.INVERTER,
        description="Herstellerübergreifend über den SunSpec-Modbus-Standard. Das Gerät wird "
                    "beim Verbinden automatisch abgefragt – ohne feste Registerkarte und damit "
                    "unempfindlich gegen Firmware-Updates. Passt für Kostal Plenticore, GoodWe "
                    "ET/EH, SolarEdge, Delta, SMA Tripower X und viele weitere.",
        capabilities={"battery_monitor"},
        maturity=Maturity.EXPERIMENTAL,
        fields=COMMON_FIELDS,
        notes="Der Verbindungstest zeigt Hersteller, Modell und die gefundenen SunSpec-Modelle. "
              "Erscheint dort das erwartete Gerät, stimmt die Anbindung.",
    )

    async def read_data(self) -> InverterData:
        model = self._first(INVERTER_MODELS_FLOAT)
        if model is not None:
            regs = await self._payload(model, 40)
            ac = f32(regs, INV_W_F, "big")
            dc = f32(regs, INV_DCW_F, "big")
            total_wh = f32(regs, INV_WH_F, "big")
        else:
            model = self._first(INVERTER_MODELS_INT)
            if model is None:
                raise DriverError(
                    "Das Gerät liefert kein SunSpec-Wechselrichtermodell (101–103/111–113). "
                    f"Gefunden wurden: {sorted(self.models) or 'keine Modelle'}."
                )
            regs = await self._payload(model, 40)
            ac = _scaled(s16(regs, INV_W), s16(regs, INV_W_SF))
            dc = _scaled(s16(regs, INV_DCW), s16(regs, INV_DCW_SF))
            total_wh = _scaled(u32(regs, INV_WH, "big"), s16(regs, INV_WH_SF))

        # DC-Leistung ist die eigentliche PV-Erzeugung; fehlt sie (manche
        # Hybrid-Geräte melden hier 0), tut es die AC-Leistung.
        pv = dc if dc > 10 else max(0.0, ac)
        return InverterData(
            pv_power=checked("pv_power", pv, source=f"SunSpec Modell {model}"),
            total_yield_kwh=sanitized("energy_kwh", total_wh / 1000.0, zero_is_none=True),
            battery_soc=await self._battery_soc(),
            status="ok",
            extra=await self.read_common(),
        )

    async def _battery_soc(self) -> float | None:
        """Batterie-SoC aus Modell 802 – nur wenn er plausibel ist.

        Die Offsets von Modell 802 werden von Herstellern unterschiedlich
        genau umgesetzt. Lieber gar kein SoC als ein falscher: Ein falscher
        Wert würde in der Budgetrechnung landen."""
        if BATTERY_MODEL not in self.models:
            return None
        try:
            regs = await self._payload(BATTERY_MODEL, BAT_SOC + 1)
        except Exception:  # noqa: BLE001 – Nebenwert
            return None
        return sanitized("battery_soc", float(u16(regs, BAT_SOC)))

    async def _probe(self) -> dict[str, Any]:
        values = await super()._probe()
        values["SunSpec-Modelle"] = ", ".join(str(m) for m in sorted(self.models)) or "keine"
        return values


@register
class SunSpecMeter(_SunSpecBase, MeterDriver):
    meta = DriverMeta(
        id="sunspec_meter",
        name="SunSpec-Zähler (herstellerübergreifend)",
        category=DeviceCategory.METER,
        description="Netzzähler über den SunSpec-Standard – entweder ein eigenständiger Zähler "
                    "oder der am Wechselrichter angeschlossene (dann dieselbe IP eintragen). "
                    "Vorzeichen: positiv = Netzbezug.",
        maturity=Maturity.EXPERIMENTAL,
        fields=COMMON_FIELDS + [
            ConfigField(key="invert", label="Richtung invertieren", type=FieldType.BOOLEAN,
                        default=False, required=False,
                        help="Aktivieren, falls Einspeisung als Bezug erscheint. Der "
                             "Verbindungstest zeigt die aktuelle Richtung im Klartext – "
                             "unbedingt einmal gegenprüfen."),
        ],
        notes="Wichtig: Nach dem Einrichten die Richtung am Verbindungstest verifizieren. "
              "Ein vertauschtes Vorzeichen lässt die Anlage in die falsche Richtung regeln.",
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.sign = -1.0 if config.get("invert") else 1.0

    async def read_data(self) -> MeterData:
        model = self._first(METER_MODELS_FLOAT)
        if model is not None:
            regs = await self._payload(model, MTR_W_F + 2)
            watts = f32(regs, MTR_W_F, "big")
        else:
            model = self._first(METER_MODELS_INT)
            if model is None:
                raise DriverError(
                    "Das Gerät liefert kein SunSpec-Zählermodell (201–204/211–214). Am "
                    "Wechselrichter ist vermutlich kein Zähler eingebunden. Gefunden: "
                    f"{sorted(self.models) or 'keine Modelle'}."
                )
            regs = await self._payload(model, MTR_W_SF + 1)
            watts = _scaled(s16(regs, MTR_W), s16(regs, MTR_W_SF))
        return MeterData(
            grid_power=self.sign * checked(
                "grid_power", watts, source=f"SunSpec Modell {model}"
            )
        )
