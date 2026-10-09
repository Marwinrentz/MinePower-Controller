"""Sungrow Energiezähler (DTSU666 o. ä.) – gelesen über den Wechselrichter.

Der DTSU666 hängt am RS485 des Sungrow-WR; seine Netzmessung ist über die
Register des Wechselrichters verfügbar (1-basierte Doku-Nummern):
  SH-Serie: 13010 Export-Leistung W (s32, + = Einspeisung)
  SG-Serie: 5083  Export-Leistung W (s32, + = Einspeisung)
grid_power (unsere Konvention: + = Bezug) = −Export.

Hinweis: Beim SH liefert bereits der Wechselrichter-Treiber `sungrow_sh` die
Netzleistung mit. Ein separates Zählergerät ist dann optional – es macht die
Netzmessung aber unabhängig vom Wechselrichter-Gerät sichtbar und ist die
sauberere Wahl, wenn der Zähler die Führungsgröße der Regelung sein soll.
"""
from __future__ import annotations

from ..base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    Maturity,
    MeterData,
    MeterDriver,
)
from ..modbus_util import s32
from ..registry import register
from ..validation import checked
from .common import SUNGROW_COMMON_FIELDS, SungrowDriverMixin, make_connection

REG_EXPORT_SH = 13010
REG_EXPORT_SG = 5083


@register
class SungrowMeter(SungrowDriverMixin, MeterDriver):
    meta = DriverMeta(
        id="sungrow_meter",
        name="Sungrow Energiezähler (DTSU666 am WR)",
        category=DeviceCategory.METER,
        description="Netzmessung des am Sungrow-Wechselrichter angeschlossenen Zählers "
                    "(DTSU666). Gleiche IP wie der Wechselrichter eintragen.",
        fields=SUNGROW_COMMON_FIELDS + [
            ConfigField(key="series", label="Wechselrichter-Serie", type=FieldType.SELECT, default="sh",
                        options=[{"value": "sh", "label": "SH-Serie (Hybrid)"},
                                 {"value": "sg", "label": "SG-Serie (String)"}],
                        help="Bestimmt die Registeradresse der Netzmessung: SH liest 13010, SG liest 5083."),
        ],
        maturity=Maturity.STABLE,
        notes="An einem DTSU666 hinter einem SH-Wechselrichter im Dauerbetrieb erprobt. "
              "Die SG-Variante (Register 5083) ist nicht an echter Hardware getestet.",
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = make_connection(config, name="Sungrow Zähler")
        self.register = REG_EXPORT_SH if config.get("series", "sh") == "sh" else REG_EXPORT_SG

    async def read_data(self) -> MeterData:
        regs = await self.conn.read_input(self.register - 1, 2)
        export = checked(
            "grid_power", float(s32(regs)),
            source=f"Sungrow Export-Leistung (Register {self.register})",
            scale_hint="Passt der Wert nicht, ist vermutlich die falsche Wechselrichter-Serie gewählt.",
        )
        return MeterData(grid_power=-export)
