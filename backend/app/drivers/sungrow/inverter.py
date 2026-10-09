"""Sungrow Wechselrichter: SG-Serie (String) und SH-Serie (Hybrid).

Registernummern nach offizieller Sungrow-Kommunikationsspezifikation
(1-basiert wie in der Doku; beim Lesen −1):

  SG/SH gemeinsam (Input):
    5001      Gerätetyp-Code (u16)
    5003      Tagesertrag 0,1 kWh (u16)
    5004-5005 Gesamtertrag kWh (u32)
    5008      Innentemperatur 0,1 °C (s16)
    5017-5018 DC-Gesamtleistung W (u32)  ← PV-Leistung
  SH (Hybrid) zusätzlich (Input):
    13001     Running-State (Bit 1 = Batterie lädt, Bit 2 = Batterie entlädt)
    13010     Export-Leistung W (s32, + = Einspeisung)  → grid_power = −Export
    13022     Batterie-Leistung W (u16, Richtung über Running-State)
    13023     Batterie-SoC 0,1 %

Der SH-Treiber liefert SoC und Batterieleistung gleich mit: Für Monitoring
und Budgetrechnung braucht es **kein separates Batteriegerät**. Der
Hybrid-Wechselrichter regelt seine Batterie selbst auf Nulleinspeisung;
MinePower liest sie nur mit.
"""
from __future__ import annotations

from ..base import DeviceCategory, DriverMeta, InverterData, InverterDriver, Maturity
from ..modbus_util import s16, s32, u16, u32
from ..registry import register
from ..validation import checked, sanitized
from .common import SUNGROW_COMMON_FIELDS, SungrowDriverMixin, make_connection

# Registernummern (1-basiert, wie in der Sungrow-Doku) → beim Lesen −1
REG_DEVICE_TYPE = 5001
REG_DAILY_YIELD = 5003
REG_TOTAL_YIELD = 5004
REG_TEMPERATURE = 5008
REG_DC_POWER = 5017
REG_SH_RUNNING_STATE = 13001
REG_SH_EXPORT_POWER = 13010
REG_SH_BATTERY_POWER = 13022
REG_SH_BATTERY_SOC = 13023

BIT_BAT_CHARGING = 0x0002
BIT_BAT_DISCHARGING = 0x0004


class _SungrowInverterBase(SungrowDriverMixin, InverterDriver):
    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = make_connection(config, name=self.meta.name)

    async def _read_common(self) -> dict:
        """PV-Leistung + Erträge – bei SG und SH identisch."""
        # 5017-5018 (DC-Leistung) und 5003-5005 (Erträge) liegen weit
        # auseinander; zwei Blöcke sind trotzdem billiger als sechs Einzel-Reads.
        pv_regs = await self.conn.read_input(REG_DC_POWER - 1, 2)
        yields = await self.conn.read_input(REG_DAILY_YIELD - 1, 3)
        pv = checked(
            "pv_power", float(u32(pv_regs)),
            source="Sungrow DC-Leistung (Register 5017)",
            scale_hint="Bei völlig unplausiblen Werten Unit-ID und Firmware-Stand prüfen.",
        )
        return {
            "pv_power": max(0.0, pv),
            "daily_yield_kwh": sanitized("energy_kwh", u16(yields, 0) * 0.1),
            "total_yield_kwh": sanitized("energy_kwh", float(u32(yields, 1))),
        }

    async def _temperature(self) -> float | None:
        try:
            regs = await self.conn.read_input(REG_TEMPERATURE - 1, 1)
        except Exception:  # noqa: BLE001 – Nebenwert, nie kritisch
            return None
        return sanitized("battery_temperature", s16(regs) * 0.1)


@register
class SungrowSGInverter(_SungrowInverterBase):
    meta = DriverMeta(
        id="sungrow_sg",
        name="Sungrow SG-Serie (String-Wechselrichter)",
        category=DeviceCategory.INVERTER,
        description="Sungrow String-Wechselrichter über Modbus TCP (WiNet-S) oder LAN. "
                    "Liefert PV-Leistung und Erträge.",
        fields=SUNGROW_COMMON_FIELDS,
    )

    async def read_data(self) -> InverterData:
        common = await self._read_common()
        return InverterData(status="ok", extra={"Innentemperatur": await self._temperature()}, **common)


@register
class SungrowSHInverter(_SungrowInverterBase):
    meta = DriverMeta(
        id="sungrow_sh",
        name="Sungrow SH-Serie (Hybrid mit Batterie)",
        category=DeviceCategory.INVERTER,
        description="Sungrow Hybrid (SH5.0RT, SH10RT …) über Modbus TCP. Liefert PV-Leistung, "
                    "Netzleistung (integrierter DTSU666-Zähler) sowie Batterie-Ladestand und "
                    "-leistung. Ein separates Batteriegerät ist dafür nicht nötig – der "
                    "Wechselrichter regelt seine Batterie selbst.",
        capabilities={"hybrid", "battery_monitor", "grid_meter"},
        fields=SUNGROW_COMMON_FIELDS,
        maturity=Maturity.STABLE,
        notes="Registerkarte an einem SH-RT-Modell im Dauerbetrieb erprobt. Weicht ein "
              "Firmware-Stand ab, melden die Live-Werte im Verbindungstest das sofort "
              "als unplausibel.",
    )

    async def read_data(self) -> InverterData:
        common = await self._read_common()
        export_regs = await self.conn.read_input(REG_SH_EXPORT_POWER - 1, 2)
        export = checked(
            "grid_power", float(s32(export_regs)),
            source="Sungrow Export-Leistung (Register 13010)",
        )
        state = u16(await self.conn.read_input(REG_SH_RUNNING_STATE - 1, 1))
        bat_regs = await self.conn.read_input(REG_SH_BATTERY_POWER - 1, 2)
        power = float(u16(bat_regs, 0))
        if state & BIT_BAT_DISCHARGING:
            power = -power
        elif not (state & BIT_BAT_CHARGING):
            power = 0.0
        soc = sanitized("battery_soc", u16(bat_regs, 1) * 0.1)
        return InverterData(
            grid_power=-export,  # Export positiv → Netzkonvention: − = Einspeisung
            status="ok",
            battery_soc=soc,
            battery_power=sanitized("battery_power", power),
            extra={
                "Innentemperatur": await self._temperature(),
                "Betriebszustand": f"0x{state:04X}",
            },
            **common,
        )

    def _warnings(self, values: dict) -> list[str]:
        out = super()._warnings(values)
        out.append(
            "Batterie: Ladestand und Leistung liest dieser Treiber mit. Soll MinePower die "
            "Batterie aktiv steuern (Netzladen bei günstigem Preis, Laden/Entladen per "
            "Knopfdruck), zusätzlich das Gerät 'Sungrow Batterie SBR/SBH' mit derselben IP "
            "anlegen und dort die aktive Steuerung erlauben."
        )
        return out
