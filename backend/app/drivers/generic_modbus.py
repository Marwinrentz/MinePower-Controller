"""Generischer Modbus-Zähler (Eastron SDM630 u. v. a.) – frei konfigurierbar.

Der Auffangtreiber für alles, wofür es keinen spezialisierten gibt. Weil
hier per Definition niemand die Registerbelegung kennt, ist die
Plausibilitätsprüfung besonders wichtig: Eine vertauschte Word-Reihenfolge
liefert keinen Fehler, sondern Werte im Megawatt-Bereich – und die würden
die Regelung sofort in die falsche Richtung schicken.
"""
from __future__ import annotations

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, MeterData, MeterDriver
from .modbus_util import ModbusConnection, f32, s16, s32, u16, u32
from .registry import register
from .validation import checked


@register
class GenericModbusMeter(MeterDriver):
    meta = DriverMeta(
        id="generic_modbus_meter",
        name="Generischer Modbus-Zähler",
        category=DeviceCategory.METER,
        description="Beliebiger Modbus-TCP-Zähler: Register, Datentyp und Skalierung frei "
                    "konfigurierbar. Vorbelegung passt für Eastron SDM630 (Gesamtwirkleistung).",
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.168.1.30",
                        help="IP des Modbus-TCP-Geräts oder -Gateways."),
            ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=502,
                        help="Modbus-TCP-Port, Standard 502."),
            ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=1,
                        help="Modbus-Slave-Adresse des Zählers."),
            ConfigField(key="register", label="Register-Adresse", type=FieldType.NUMBER, default=52,
                        help="0-basierte Adresse der Gesamtwirkleistung. SDM630: 52 (Input, float32)."),
            ConfigField(key="register_type", label="Register-Typ", type=FieldType.SELECT, default="input",
                        options=[{"value": "input", "label": "Input (FC04)"},
                                 {"value": "holding", "label": "Holding (FC03)"}],
                        help="Welcher Modbus-Funktionscode zum Lesen verwendet wird."),
            ConfigField(key="data_type", label="Datentyp", type=FieldType.SELECT, default="float32",
                        options=[{"value": "float32", "label": "float32"},
                                 {"value": "s32", "label": "int32 (signed)"},
                                 {"value": "u32", "label": "uint32"},
                                 {"value": "s16", "label": "int16 (signed)"},
                                 {"value": "u16", "label": "uint16"}],
                        help="Datentyp des Leistungsregisters."),
            ConfigField(key="word_order", label="Word-Reihenfolge", type=FieldType.SELECT, default="big",
                        options=[{"value": "big", "label": "Big-Endian (High zuerst)"},
                                 {"value": "little", "label": "Little-Endian (Low zuerst)"}],
                        help="Nur für 32-Bit-Typen relevant."),
            ConfigField(key="scale", label="Skalierungsfaktor", type=FieldType.NUMBER, default=1.0,
                        help="Multiplikator auf den Rohwert (z. B. 0.1 bei 0,1-W-Auflösung)."),
            ConfigField(key="invert", label="Richtung invertieren", type=FieldType.BOOLEAN, default=False,
                        help="Aktivieren, falls Einspeisung als Bezug erscheint (+ muss Bezug sein)."),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = ModbusConnection(
            host=str(config.get("host", "")).strip(),
            port=int(config.get("port") or 502),
            unit_id=int(config.get("unit_id") or 1),
            name="Modbus-Zähler",
        )

    async def connect(self) -> None:
        await self.conn.connect()

    async def disconnect(self) -> None:
        await self.conn.close()

    async def read_data(self) -> MeterData:
        cfg = self.config
        dtype = cfg.get("data_type", "float32")
        count = 2 if dtype in ("float32", "s32", "u32") else 1
        address = int(cfg.get("register") or 0)
        if cfg.get("register_type", "input") == "holding":
            regs = await self.conn.read_holding(address, count)
        else:
            regs = await self.conn.read_input(address, count)
        order = cfg.get("word_order", "big")
        if dtype == "float32":
            value = f32(regs, 0, order)
        elif dtype == "s32":
            value = float(s32(regs, 0, order))
        elif dtype == "u32":
            value = float(u32(regs, 0, order))
        elif dtype == "s16":
            value = float(s16(regs))
        else:
            value = float(u16(regs))
        value *= float(cfg.get("scale") or 1.0)
        if cfg.get("invert"):
            value = -value
        return MeterData(
            grid_power=checked(
                "grid_power", value,
                source=f"Modbus-Register {address} ({dtype})",
                scale_hint="Typische Ursachen: falscher Datentyp, vertauschte Word-Reihenfolge "
                           "oder ein fehlender Skalierungsfaktor (z. B. 0.01 bei 10-mW-Auflösung).",
            )
        )
