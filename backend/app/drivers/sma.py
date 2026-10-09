"""SMA über Modbus TCP (SMA-eigene Registerkarte).

Modbus muss im Gerät aktiviert sein (Installateur-Zugang → Externe
Kommunikation → Modbus → TCP-Server). Standard-Unit-ID: 3.

  30775-30776  AC-Wirkleistung gesamt W (s32)
  30535-30536  Tagesertrag Wh (u32)
  30513-30516  Gesamtertrag Wh (u64)
  30845-30846  Batterie-Ladezustand % (u32)      – Sunny Boy Storage / Hybrid
  31393-31394  Batterie-Ladeleistung W (u32)     – Sunny Boy Storage / Hybrid
  31395-31396  Batterie-Entladeleistung W (u32)  – Sunny Boy Storage / Hybrid
  30865-30866  Netzbezug W (u32)                 – mit Energy Meter / Home Manager
  30867-30868  Netzeinspeisung W (u32)           – mit Energy Meter / Home Manager

SMA markiert 'kein Wert' mit 0x80000000 bzw. 0xFFFFFFFF – nachts liefert der
Wechselrichter also nicht 0 W, sondern diesen Marker. Wer den nicht abfängt,
bekommt Fantasiewerte in die Regelung; deshalb läuft jeder Wert hier durch
`nan_to_none`.
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
from .modbus_util import ModbusConnection, nan_to_none, s32, u32, u64
from .registry import register
from .validation import DriverError, checked, sanitized

REG_AC_POWER = 30775
REG_DAILY_YIELD = 30535
REG_TOTAL_YIELD = 30513
REG_BATTERY_SOC = 30845
REG_BATTERY_CHARGE = 31393
REG_BATTERY_DISCHARGE = 31395
REG_GRID_IMPORT = 30865
REG_GRID_EXPORT = 30867

SMA_FIELDS = [
    ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.21",
                help="IP des SMA-Geräts. Modbus TCP muss im Gerät aktiviert sein "
                     "(Installateur-Zugang → Externe Kommunikation → Modbus → TCP-Server)."),
    ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=502,
                help="Modbus-TCP-Port, Standard 502."),
    ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=3,
                help="SMA-Standard: 3. Beim Sunny Home Manager abweichend – bei 'Zielgerät "
                     "nicht erreichbar' auch 1 und 2 probieren."),
]


class _SmaBase:
    def __init__(self, config: dict) -> None:
        super().__init__(config)  # type: ignore[call-arg]
        self.conn = ModbusConnection(
            host=str(config.get("host", "")).strip(),
            port=int(config.get("port") or 502),
            unit_id=int(config.get("unit_id") or 3),
            name=self.meta.name,  # type: ignore[attr-defined]
        )

    async def connect(self) -> None:
        await self.conn.connect()

    async def disconnect(self) -> None:
        await self.conn.close()

    async def _u32(self, register: int) -> float | None:
        """Optionales u32-Register lesen; SMA-NaN-Marker → None."""
        try:
            regs = await self.conn.read_holding(register, 2)
        except Exception:  # noqa: BLE001 – nicht jedes Modell hat jedes Register
            return None
        return nan_to_none(float(u32(regs, 0, "big")))


@register
class SmaInverter(_SmaBase, InverterDriver):
    meta = DriverMeta(
        id="sma_inverter",
        name="SMA Wechselrichter / Sunny Boy Storage (Modbus TCP)",
        category=DeviceCategory.INVERTER,
        description="SMA Sunny Boy, Sunny Tripower (auch Tripower X) und Sunny Boy Storage über "
                    "Modbus TCP. Liefert PV-Leistung und Erträge; bei Speichergeräten zusätzlich "
                    "Batterie-Ladestand und -leistung (nur lesend – SMA regelt die Batterie selbst). "
                    "Für die Netzmessung das SMA Energy Meter / den Home Manager als Zähler anlegen.",
        capabilities={"battery_monitor"},
        fields=SMA_FIELDS,
        notes="Registerkarte gemäß SMA-Modbus-Liste. Batterie- und Zählerregister existieren nur "
              "auf Geräten mit Speicher bzw. Energy Meter – fehlende Register werden übersprungen.",
    )

    async def read_data(self) -> InverterData:
        power_regs = await self.conn.read_holding(REG_AC_POWER, 2)
        raw = nan_to_none(float(s32(power_regs, 0, "big")))
        power = 0.0 if raw is None else max(0.0, raw)  # Marker nachts → 0 W
        daily = await self._u32(REG_DAILY_YIELD)
        try:
            total_regs = await self.conn.read_holding(REG_TOTAL_YIELD, 4)
            total = nan_to_none(float(u64(total_regs, 0, "big")))
        except Exception:  # noqa: BLE001
            total = None

        soc = await self._u32(REG_BATTERY_SOC)
        charge = await self._u32(REG_BATTERY_CHARGE)
        discharge = await self._u32(REG_BATTERY_DISCHARGE)
        battery_power = None
        if charge is not None or discharge is not None:
            battery_power = (charge or 0.0) - (discharge or 0.0)

        return InverterData(
            pv_power=checked("pv_power", power, source="SMA Register 30775"),
            daily_yield_kwh=sanitized("energy_kwh", None if daily is None else daily / 1000.0),
            total_yield_kwh=sanitized("energy_kwh", None if total is None else total / 1000.0),
            battery_soc=sanitized("battery_soc", soc),
            battery_power=sanitized("battery_power", battery_power),
            status="ok",
        )


@register
class SmaMeter(_SmaBase, MeterDriver):
    meta = DriverMeta(
        id="sma_meter",
        name="SMA Energy Meter / Sunny Home Manager (via Wechselrichter)",
        category=DeviceCategory.METER,
        description="Netzmessung des SMA Energy Meters bzw. Sunny Home Managers, gelesen über die "
                    "Modbus-Register des Wechselrichters (gleiche IP wie der Wechselrichter). "
                    "Bezug und Einspeisung liegen bei SMA in getrennten Registern und werden hier "
                    "zu einer vorzeichenbehafteten Netzleistung zusammengefasst.",
        maturity=Maturity.EXPERIMENTAL,
        fields=SMA_FIELDS,
        notes="Nur verfügbar, wenn am Wechselrichter ein SMA Energy Meter / Home Manager "
              "eingebunden ist. Der Verbindungstest zeigt sofort, ob die Register antworten.",
    )

    async def read_data(self) -> MeterData:
        imported = await self._u32(REG_GRID_IMPORT)
        exported = await self._u32(REG_GRID_EXPORT)
        if imported is None and exported is None:
            raise DriverError(
                "Die SMA-Zählerregister (30865/30867) antworten nicht. Am Wechselrichter ist "
                "vermutlich kein Energy Meter / Sunny Home Manager eingebunden – dann bitte "
                "einen anderen Netzzähler verwenden."
            )
        grid = (imported or 0.0) - (exported or 0.0)
        return MeterData(
            grid_power=checked("grid_power", grid, source="SMA Register 30865/30867"),
            energy_import_kwh=None,
            energy_export_kwh=None,
        )
