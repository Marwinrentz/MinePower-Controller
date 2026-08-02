"""Sungrow Batteriespeicher (SBR/SBH) am SH-Hybrid-Wechselrichter.

**Dieses Gerät ist für den Normalbetrieb nicht erforderlich.** Der SH-Hybrid
regelt seine Batterie selbst auf Nulleinspeisung, und der Treiber
`sungrow_sh` liefert SoC und Batterieleistung bereits mit. Ein eigenes
Batteriegerät lohnt sich nur, wenn man die Batterie in der Prioritätskette
ausdrücklich einsortieren oder – als bewusst gewählte Zusatzfunktion – aktiv
steuern will (Zwangsladen/-entladen, Reserve-SoC).

Lesen über Input-Register, Steuern über Holding-Register des Hybrid-WR
(Registernummern 1-basiert wie in der Sungrow-Doku, beim Zugriff −1):
  13022 (Input)  Batterie-Leistung W (Richtung via Running-State 13001)
  13023 (Input)  Batterie-SoC 0,1 %
  13024 (Input)  Batterie-SoH 0,1 %
  13025 (Input)  Batterie-Temperatur 0,1 °C
  13050 (Hold)   EMS-Modus: 0 = Eigenverbrauch, 2 = Zwangsmodus
  13051 (Hold)   Zwangs-Befehl: 0xAA = laden, 0xBB = entladen, 0xCC = stopp
  13052 (Hold)   Zwangs-Leistung W
  13058 (Hold)   Min-SoC (Reserve) 0,1 %

Schreibzugriffe werden grundsätzlich zurückgelesen: Sungrow quittiert
gesperrte Register je nach Firmware ohne Fehler, übernimmt den Wert aber
nicht. Ohne Readback sähe das wie ein Erfolg aus.
"""
from __future__ import annotations

from ..base import (
    BatteryData,
    BatteryDriver,
    BatteryMode,
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    Maturity,
)
from ..modbus_util import s16, u16
from ..registry import register
from ..validation import DeviceRejected, checked, sanitized
from .common import SUNGROW_COMMON_FIELDS, SungrowDriverMixin, make_connection

REG_RUNNING_STATE = 13001   # Input, Bitfeld
REG_BATTERY_POWER = 13022   # Input, W
REG_BATTERY_SOC = 13023     # Input, 0,1 %
REG_BATTERY_TEMP = 13025    # Input, 0,1 °C

REG_EMS_MODE = 13050        # Holding
REG_FORCE_CMD = 13051       # Holding
REG_FORCE_POWER = 13052     # Holding
REG_MAX_SOC = 13057         # Holding, 0,1 %
REG_MIN_SOC = 13058         # Holding, 0,1 %

BIT_BAT_CHARGING = 0x0002
BIT_BAT_DISCHARGING = 0x0004

EMS_SELF_CONSUMPTION = 0
EMS_FORCED = 2
CMD_CHARGE = 0xAA
CMD_DISCHARGE = 0xBB
CMD_STOP = 0xCC

ACTIVE_CONTROL_FIELD = ConfigField(
    key="allow_active_control",
    label="Erweitert: aktive Batteriesteuerung erlauben",
    type=FieldType.BOOLEAN,
    default=False,
    required=False,
    help="Standard: aus. Dein Hybrid-Wechselrichter regelt die Batterie selbst – für die "
         "Nulleinspeisung ist keine aktive Steuerung nötig, MinePower liest nur mit. "
         "Einschalten nur für Sonderfälle (Zwangsladen vor einem Sturm, Reserve-SoC "
         "aus MinePower nachführen). Aktive Schreibzugriffe können je nach Firmware "
         "abgelehnt werden oder mit der Eigenverbrauchsoptimierung des Wechselrichters "
         "konkurrieren.",
)


@register
class SungrowBattery(SungrowDriverMixin, BatteryDriver):
    meta = DriverMeta(
        id="sungrow_battery",
        name="Sungrow Batterie SBR/SBH (optional, Monitoring)",
        category=DeviceCategory.BATTERY,
        description="Optional: SBR-/SBH-Speicher als eigenes Gerät, gelesen über den SH-Hybrid "
                    "(gleiche IP wie der Wechselrichter). Nicht erforderlich – der SH-Treiber "
                    "liefert Ladestand und Leistung bereits mit. Sinnvoll nur, wenn die Batterie "
                    "in der Prioritätskette einsortiert oder bewusst aktiv gesteuert werden soll.",
        capabilities={"battery_monitor", "battery_control"},
        maturity=Maturity.BETA,
        fields=SUNGROW_COMMON_FIELDS + [ACTIVE_CONTROL_FIELD],
        notes="Aktive Batteriebefehle sind Zusatzfunktion, kein Automatismus: Im Normalbetrieb "
              "bleibt die Batterie unangetastet.",
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = make_connection(config, name="Sungrow Batterie")

    async def read_data(self) -> BatteryData:
        state = u16(await self.conn.read_input(REG_RUNNING_STATE - 1, 1))
        regs = await self.conn.read_input(REG_BATTERY_POWER - 1, 4)
        power = float(u16(regs, 0))
        if state & BIT_BAT_DISCHARGING:
            power = -power
        elif not (state & BIT_BAT_CHARGING):
            power = 0.0
        return BatteryData(
            soc=checked(
                "battery_soc", u16(regs, 1) * 0.1,
                source="Sungrow Batterie-SoC (Register 13023)",
                scale_hint="Erwartet werden 0–100 %. Weicht der Wert stark ab, stimmt die "
                           "Registerbelegung dieses Firmware-Stands nicht.",
            ),
            power=checked("battery_power", power, source="Sungrow Batterie-Leistung (Register 13022)"),
            temperature_c=sanitized("battery_temperature", s16(regs, 3) * 0.1),
            status="ok",
            extra={"Zustand (SoH)": f"{u16(regs, 2) * 0.1:.1f} %"},
        )

    # ------------------------------------------------------------ Steuerung

    def _require_control(self) -> None:
        if not self.control_enabled():
            raise DeviceRejected(
                "Aktive Batteriesteuerung ist für dieses Gerät nicht freigeschaltet. "
                "Sie ist im Normalbetrieb auch nicht nötig – der Wechselrichter regelt die "
                "Batterie selbst. Zum Aktivieren: Gerät bearbeiten → „Erweitert: aktive "
                "Batteriesteuerung erlauben“."
            )

    async def set_mode(self, mode: BatteryMode, power_w: float | None = None) -> None:
        self._require_control()
        if mode == BatteryMode.AUTO:
            await self.conn.write_register(REG_EMS_MODE - 1, EMS_SELF_CONSUMPTION, verify=True)
            return
        await self.conn.write_register(REG_EMS_MODE - 1, EMS_FORCED, verify=True)
        if mode == BatteryMode.FORCE_CHARGE:
            await self.conn.write_register(REG_FORCE_POWER - 1, int(power_w or 5000), verify=True)
            await self.conn.write_register(REG_FORCE_CMD - 1, CMD_CHARGE, verify=True)
        elif mode == BatteryMode.FORCE_DISCHARGE:
            await self.conn.write_register(REG_FORCE_POWER - 1, int(power_w or 5000), verify=True)
            await self.conn.write_register(REG_FORCE_CMD - 1, CMD_DISCHARGE, verify=True)
        else:  # HOLD
            await self.conn.write_register(REG_FORCE_CMD - 1, CMD_STOP, verify=True)

    async def set_reserve_soc(self, soc: int) -> None:
        self._require_control()
        await self.conn.write_register(REG_MIN_SOC - 1, int(soc * 10), verify=True)
