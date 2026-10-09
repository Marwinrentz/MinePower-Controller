"""Victron Energy GX (Cerbo GX, Venus GX, CCGX) über Modbus TCP.

Liest die Systemwerte (``com.victronenergy.system``, Unit-ID 100) aus der
Victron-Registerliste 'CCGX-Modbus-TCP-register-list":

    808–810  PV AC-gekoppelt am Ausgang L1–L3   uint16  W
    811–813  PV AC-gekoppelt am Eingang L1–L3   uint16  W
    820–822  Netz L1–L3                         int16   W   (+ Bezug)
    842      Batterieleistung                   int16   W   (+ Laden)
    843      Batterie-Ladestand                 uint16  %
    850      PV DC-gekoppelt                    uint16  W

Modbus TCP muss im GX aktiviert sein (Einstellungen → Dienste → Modbus TCP).
Der Speicher wird nur gelesen: Das Energiemanagement macht der GX selbst.
"""
from __future__ import annotations

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, InverterData, InverterDriver
from .modbus_util import ModbusConnection, s16, u16
from .registry import register
from .validation import checked, sanitized


@register
class VictronGx(InverterDriver):
    meta = DriverMeta(
        id="victron_gx", name="Victron GX (Modbus TCP)", category=DeviceCategory.INVERTER,
        description="Victron-System über Cerbo/Venus GX: PV (AC und DC), Netz, Batterie.",
        capabilities={"battery_monitor", "hybrid", "grid_meter"},
        notes="Modbus TCP im GX aktivieren. Batterie nur lesend.",
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.14"),
            ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=502),
            ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=100),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = ModbusConnection(
            host=str(config.get("host", "")).strip(), port=int(float(config.get("port") or 502)),
            unit_id=int(float(config.get("unit_id") or 100)), name="Victron GX",
        )

    async def connect(self) -> None:
        await self.conn.connect()

    async def disconnect(self) -> None:
        await self.conn.close()

    async def read_data(self) -> InverterData:
        pv_ac = await self.conn.read_holding(808, 6)
        grid = await self.conn.read_holding(820, 3)
        bat = await self.conn.read_holding(842, 2)
        pv_dc = await self.conn.read_holding(850, 1)
        pv = sum(_u(pv_ac, i) for i in range(6)) + _u(pv_dc, 0)
        grid_w = sum(_s(grid, i) for i in range(3))
        return InverterData(
            pv_power=checked("pv_power", float(pv), source="Victron 808–813/850"),
            grid_power=checked("grid_power", float(grid_w), source="Victron 820–822"),
            battery_power=checked("battery_power", float(_s(bat, 0)), source="Victron 842"),
            battery_soc=sanitized("battery_soc", float(u16(bat, 1))) if u16(bat, 1) != 0xFFFF else None,
        )


def _u(regs: list[int], i: int) -> int:
    """Nicht vorhandene Quelle (0xFFFF) zählt als 0 W."""
    v = u16(regs, i)
    return 0 if v == 0xFFFF else v


def _s(regs: list[int], i: int) -> int:
    v = regs[i]
    return 0 if v == 0x7FFF else s16(regs, i)
