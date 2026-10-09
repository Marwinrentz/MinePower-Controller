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
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.30",
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


# ---------------------------------------------------------------- Registerprofil

from .base import BatteryData, BatteryDriver, InverterData, InverterDriver  # noqa: E402

DTYPES = [{"value": "float32", "label": "float32"}, {"value": "s32", "label": "int32"},
          {"value": "u32", "label": "uint32"}, {"value": "s16", "label": "int16"},
          {"value": "u16", "label": "uint16"}]


def _conn_fields() -> list[ConfigField]:
    return [
        ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.31"),
        ConfigField(key="port", label="Port", type=FieldType.NUMBER, default=502),
        ConfigField(key="unit_id", label="Unit-ID", type=FieldType.NUMBER, default=1),
        ConfigField(key="register_type", label="Register-Typ", type=FieldType.SELECT, default="holding",
                    options=[{"value": "holding", "label": "Holding (FC03)"},
                             {"value": "input", "label": "Input (FC04)"}]),
        ConfigField(key="word_order", label="Word-Reihenfolge", type=FieldType.SELECT, default="big",
                    options=[{"value": "big", "label": "Big-Endian"}, {"value": "little", "label": "Little-Endian"}]),
    ]


def _value_fields(prefix: str, label: str, *, required: bool, dtype: str = "s16") -> list[ConfigField]:
    return [
        ConfigField(key=f"{prefix}_register", label=f"{label}: Register", type=FieldType.NUMBER, required=required,
                    help="0-basierte Adresse"),
        ConfigField(key=f"{prefix}_type", label=f"{label}: Datentyp", type=FieldType.SELECT, default=dtype,
                    options=DTYPES, required=False),
        ConfigField(key=f"{prefix}_scale", label=f"{label}: Faktor", type=FieldType.NUMBER, default=1.0,
                    required=False),
    ]


def decode(regs: list[int], dtype: str, order: str) -> float:
    if dtype == "float32":
        return f32(regs, 0, order)
    if dtype == "s32":
        return float(s32(regs, 0, order))
    if dtype == "u32":
        return float(u32(regs, 0, order))
    if dtype == "s16":
        return float(s16(regs))
    return float(u16(regs))


class _ProfileBase:
    config: dict
    conn: ModbusConnection

    def _connection(self, config: dict, name: str) -> ModbusConnection:
        return ModbusConnection(host=str(config.get("host", "")).strip(), port=int(float(config.get("port") or 502)),
                                unit_id=int(float(config.get("unit_id") or 1)), name=name)

    async def connect(self) -> None:
        await self.conn.connect()

    async def disconnect(self) -> None:
        await self.conn.close()

    async def _read(self, prefix: str) -> float | None:
        cfg = self.config
        if cfg.get(f"{prefix}_register") in (None, ""):
            return None
        dtype = cfg.get(f"{prefix}_type") or "s16"
        count = 2 if dtype in ("float32", "s32", "u32") else 1
        address = int(float(cfg[f"{prefix}_register"]))
        regs = (await self.conn.read_input(address, count) if cfg.get("register_type") == "input"
                else await self.conn.read_holding(address, count))
        return decode(regs, dtype, cfg.get("word_order") or "big") * float(cfg.get(f"{prefix}_scale") or 1.0)


@register
class GenericModbusInverter(_ProfileBase, InverterDriver):
    meta = DriverMeta(
        id="generic_modbus_inverter", name="Modbus TCP generisch (Registerprofil)", category=DeviceCategory.INVERTER,
        description="Wechselrichter mit frei konfigurierbaren Registern für PV, Netz und Batterie.",
        capabilities={"battery_monitor"},
        fields=[*_conn_fields(), *_value_fields("pv", "PV-Leistung", required=True, dtype="u32"),
                *_value_fields("grid", "Netzleistung", required=False, dtype="s32"),
                *_value_fields("soc", "Batterie-Ladestand", required=False, dtype="u16"),
                *_value_fields("battery", "Batterieleistung", required=False, dtype="s32"),
                ConfigField(key="battery_invert", label="Batterie: Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein")],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = self._connection(config, "Modbus-Wechselrichter")

    async def read_data(self) -> InverterData:
        pv = await self._read("pv") or 0.0
        grid = await self._read("grid")
        soc = await self._read("soc")
        bat = await self._read("battery")
        if bat is not None and self.config.get("battery_invert"):
            bat = -bat
        return InverterData(
            pv_power=checked("pv_power", pv, source="Register PV"),
            grid_power=checked("grid_power", grid, source="Register Netz") if grid is not None else None,
            battery_soc=checked("battery_soc", soc, source="Register Ladestand") if soc is not None else None,
            battery_power=checked("battery_power", bat, source="Register Batterie") if bat is not None else None,
        )


@register
class GenericModbusBattery(_ProfileBase, BatteryDriver):
    meta = DriverMeta(
        id="generic_modbus_battery", name="Modbus TCP generisch (Registerprofil, lesend)",
        category=DeviceCategory.BATTERY,
        description="Speicher mit frei konfigurierbaren Registern für Ladestand und Leistung. Nur lesend.",
        capabilities={"battery_monitor"},
        fields=[*_conn_fields(), *_value_fields("soc", "Ladestand", required=True, dtype="u16"),
                *_value_fields("battery", "Leistung", required=True, dtype="s32"),
                ConfigField(key="battery_invert", label="Vorzeichen umkehren", type=FieldType.BOOLEAN,
                            default=False, required=False, help="+ muss Laden sein")],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = self._connection(config, "Modbus-Speicher")

    async def read_data(self) -> BatteryData:
        soc = await self._read("soc") or 0.0
        power = await self._read("battery") or 0.0
        if self.config.get("battery_invert"):
            power = -power
        return BatteryData(soc=checked("battery_soc", soc, source="Register Ladestand"),
                           power=checked("battery_power", power, source="Register Leistung"))
