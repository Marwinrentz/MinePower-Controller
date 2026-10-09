"""Huawei SUN2000 (+ LUNA2000-Speicher, + Smart Power Sensor) über Modbus TCP.

EXPERIMENTELL – die Registerbelegung stammt aus der Huawei-Dokumentation
'Modbus Interface Definitions' und ist hier nicht an echter Hardware
verifiziert. Alle Werte laufen deshalb durch die Plausibilitätsprüfung, und
der Verbindungstest zeigt sie vor der Inbetriebnahme im Klartext.

Registerkarte (Holding-Register, Unit-ID meist 1):
  32064  DC-Eingangsleistung W (s32)          ← PV-Leistung
  32080  AC-Wirkleistung W (s32)
  32114  Tagesertrag kWh (u32, Faktor 0,01)
  32106  Gesamtertrag kWh (u32, Faktor 0,01)
  37113  Zählerleistung W (s32) – Huawei: > 0 = Einspeisung, < 0 = Bezug
  37760  Speicher Lade-/Entladeleistung W (s32) – > 0 laden, < 0 entladen
  37004  Speicher-Ladestand % (u16, Faktor 0,1)

Wichtige Betriebshinweise:
* Huawei erlaubt nur **eine** Modbus-Verbindung gleichzeitig. Deshalb teilen
  sich alle Huawei-Geräte hier eine gepoolte Session. Läuft parallel noch
  eine andere Modbus-Anwendung (FusionSolar-Dongle, Home Assistant), kommt es
  zu sporadischen Ausfällen – das ist eine Eigenheit des Geräts.
* Modbus TCP muss im Dongle freigeschaltet sein (SDongle-Einstellungen).

**Batterie:** Der SUN2000 regelt seinen LUNA2000-Speicher selbst. Hier wird
er ausschließlich gelesen – aktive Steuerung ist für die Nulleinspeisung
nicht nötig und daher bewusst nicht implementiert.
"""
from __future__ import annotations

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
from .modbus_util import ModbusConnection, acquire_connection, release_connection, s32, u16, u32
from .registry import register
from .validation import checked, sanitized

REG_DC_POWER = 32064
REG_AC_POWER = 32080
REG_TOTAL_YIELD = 32106
REG_DAILY_YIELD = 32114
REG_METER_POWER = 37113
REG_BATTERY_POWER = 37760
REG_BATTERY_SOC = 37004

HUAWEI_FIELDS = [
    ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.35",
                help="IP des SUN2000 bzw. des SDongle. Modbus TCP muss im Dongle freigeschaltet "
                     "sein. Achtung: Huawei erlaubt nur eine Modbus-Verbindung gleichzeitig."),
    ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=502,
                help="Modbus-TCP-Port, Standard 502."),
    ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=1,
                help="Standard 1. Bei Anlagen mit mehreren Wechselrichtern am selben Dongle "
                     "aufsteigend (1, 2, 3 …)."),
]


class _HuaweiBase:
    def __init__(self, config: dict) -> None:
        super().__init__(config)  # type: ignore[call-arg]
        self.conn: ModbusConnection = acquire_connection(
            host=str(config.get("host", "")).strip(),
            port=int(config.get("port") or 502),
            unit_id=int(config.get("unit_id") or 1),
            timeout=6.0,  # Huawei antwortet spürbar träge
            name="Huawei SUN2000",
        )

    async def connect(self) -> None:
        await self.conn.connect()

    async def disconnect(self) -> None:
        await release_connection(self.conn)

    async def _optional_s32(self, register: int, scale: float = 1.0) -> float | None:
        try:
            regs = await self.conn.read_holding(register, 2)
        except Exception:  # noqa: BLE001 – Register existiert nicht auf jedem Modell
            return None
        return float(s32(regs, 0, "big")) * scale


@register
class HuaweiSun2000(_HuaweiBase, InverterDriver):
    meta = DriverMeta(
        id="huawei_sun2000",
        name="Huawei SUN2000 / LUNA2000 (experimentell)",
        category=DeviceCategory.INVERTER,
        description="Huawei SUN2000 über Modbus TCP: PV-Leistung, Erträge und – falls ein "
                    "LUNA2000-Speicher vorhanden ist – dessen Ladestand und Leistung (nur "
                    "lesend; der Wechselrichter regelt die Batterie selbst).",
        capabilities={"hybrid", "battery_monitor"},
        maturity=Maturity.EXPERIMENTAL,
        fields=HUAWEI_FIELDS,
        notes="Registerkarte aus der Huawei-Dokumentation, nicht an Hardware verifiziert. "
              "Vor dem Produktivbetrieb die Live-Werte im Verbindungstest gegen die "
              "FusionSolar-App prüfen. Alternative: der SunSpec-Treiber, sofern das Gerät "
              "SunSpec unterstützt.",
    )

    async def read_data(self) -> InverterData:
        dc_regs = await self.conn.read_holding(REG_DC_POWER, 2)
        pv = checked(
            "pv_power", float(s32(dc_regs, 0, "big")),
            source="Huawei Register 32064 (DC-Leistung)",
            scale_hint="Vergleichswert: die aktuelle Erzeugung in der FusionSolar-App.",
        )
        daily = await self._optional_u32(REG_DAILY_YIELD, 0.01)
        total = await self._optional_u32(REG_TOTAL_YIELD, 0.01)
        battery_power = await self._optional_s32(REG_BATTERY_POWER)
        soc = None
        try:
            soc = float(u16(await self.conn.read_holding(REG_BATTERY_SOC, 1))) * 0.1
        except Exception:  # noqa: BLE001 – ohne LUNA2000 existiert das Register nicht
            soc = None
        return InverterData(
            pv_power=max(0.0, pv),
            daily_yield_kwh=sanitized("energy_kwh", daily),
            total_yield_kwh=sanitized("energy_kwh", total),
            battery_power=sanitized("battery_power", battery_power),
            battery_soc=sanitized("battery_soc", soc),
            status="ok",
        )

    async def _optional_u32(self, register: int, scale: float) -> float | None:
        try:
            regs = await self.conn.read_holding(register, 2)
        except Exception:  # noqa: BLE001
            return None
        return float(u32(regs, 0, "big")) * scale


@register
class HuaweiPowerSensor(_HuaweiBase, MeterDriver):
    meta = DriverMeta(
        id="huawei_meter",
        name="Huawei Smart Power Sensor (experimentell)",
        category=DeviceCategory.METER,
        description="Netzmessung des Huawei Smart Power Sensors (DTSU666-H), gelesen über den "
                    "SUN2000 – gleiche IP wie der Wechselrichter eintragen.",
        maturity=Maturity.EXPERIMENTAL,
        fields=HUAWEI_FIELDS + [
            ConfigField(key="invert", label="Richtung invertieren", type=FieldType.BOOLEAN,
                        default=False, required=False,
                        help="Huawei zählt Einspeisung positiv; MinePower rechnet mit "
                             "'+ = Bezug' und dreht das Vorzeichen deshalb bereits um. Diese "
                             "Option nur setzen, wenn der Verbindungstest trotzdem die falsche "
                             "Richtung meldet."),
        ],
        notes="Vorzeichen unbedingt im Verbindungstest prüfen: Bei Bezug muss dort 'Bezug' "
              "stehen. Stimmt das nicht, regelt die Anlage in die falsche Richtung.",
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.sign = 1.0 if config.get("invert") else -1.0  # Huawei: + = Einspeisung

    async def read_data(self) -> MeterData:
        regs = await self.conn.read_holding(REG_METER_POWER, 2)
        raw = checked(
            "grid_power", float(s32(regs, 0, "big")),
            source="Huawei Register 37113 (Zählerleistung)",
        )
        return MeterData(grid_power=self.sign * raw)
