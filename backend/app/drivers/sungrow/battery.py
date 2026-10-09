"""Sungrow Batteriespeicher (SBR/SBH) am SH-Hybrid-Wechselrichter.

Für reines Monitoring ist dieses Gerät nicht nötig – der Treiber
`sungrow_sh` liefert SoC und Batterieleistung bereits mit. Es wird
gebraucht, sobald MinePower die Batterie **aktiv steuern** soll:
Netzladen bei günstigem Preis, manuelles Laden/Entladen per Knopfdruck,
Sperre während eines fremden Netzladefensters.

Registerkarte (1-basiert wie in der Sungrow-Doku 'Communication Protocol of
Residential Hybrid Inverter", beim Zugriff −1):

  13001 (Input)  Running-State (Bit 1 = lädt, Bit 2 = entlädt)
  13022 (Input)  Batterie-Leistung W (Richtung über Running-State)
  13023 (Input)  Batterie-SoC 0,1 %
  13024 (Input)  Batterie-SoH 0,1 %
  13025 (Input)  Batterie-Temperatur 0,1 °C
  13050 (Hold)   EMS-Modus: 0 = Eigenverbrauch, 2 = Zwangsmodus,
                 3 = externes EMS, 4 = VPP
  13051 (Hold)   Zwangs-Befehl: 0xAA = laden, 0xBB = entladen, 0xCC = stopp
  13052 (Hold)   Zwangs-Leistung W
  13058 (Hold)   Max-SoC 0,1 %   (Ladeobergrenze)
  13059 (Hold)   Min-SoC 0,1 %   (Entladeuntergrenze / Reserve)

**Korrektur gegenüber früheren Versionen:** Max-/Min-SoC standen hier eine
Adresse zu tief (13057/13058). Ein 'Reserve auf 20 %" landete damit im
Max-SoC-Register und begrenzte die *Ladung* auf 20 % – eine Batterie, die
scheinbar grundlos nicht mehr lädt. Weil die Registerkarte je nach
Firmware abweichen kann, wird vor jedem SoC-Schreiben geprüft, ob die
gelesenen Werte zusammenpassen (Max ≥ 50 %, Min ≤ Max). Passt es nicht,
wird nicht geschrieben.

Schreibzugriffe werden grundsätzlich zurückgelesen: Sungrow quittiert
gesperrte Register je nach Firmware ohne Fehler, übernimmt den Wert aber
nicht. Ohne Readback sähe das wie ein Erfolg aus. Jeder Zugriff landet
zusätzlich im Schreibjournal der Verbindung (Diagnose).
"""
from __future__ import annotations

import time

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
REG_MAX_SOC = 13058         # Holding, 0,1 %
REG_MIN_SOC = 13059         # Holding, 0,1 %

#: Block, der für den Modus-Readback gelesen wird (13050 … 13059)
CONTROL_BLOCK_START = REG_EMS_MODE
CONTROL_BLOCK_LEN = REG_MIN_SOC - REG_EMS_MODE + 1

BIT_BAT_CHARGING = 0x0002
BIT_BAT_DISCHARGING = 0x0004

EMS_SELF_CONSUMPTION = 0
EMS_FORCED = 2
EMS_EXTERNAL = 3
EMS_VPP = 4
CMD_CHARGE = 0xAA
CMD_DISCHARGE = 0xBB
CMD_STOP = 0xCC

EMS_LABELS = {
    EMS_SELF_CONSUMPTION: "Eigenverbrauch",
    EMS_FORCED: "Zwangsmodus",
    EMS_EXTERNAL: "externes EMS",
    EMS_VPP: "VPP",
}
CMD_LABELS = {CMD_CHARGE: "laden", CMD_DISCHARGE: "entladen", CMD_STOP: "stopp"}

#: Holding-Register nicht in jedem Takt lesen: Der WiNet-S verkraftet nur
#: wenige Anfragen pro Sekunde. Nach jedem Schreibzugriff wird sofort neu
#: gelesen (Cache verworfen), sonst reicht dieser Abstand.
CONTROL_READ_INTERVAL_S = 15.0

ACTIVE_CONTROL_FIELD = ConfigField(
    key="allow_active_control",
    label="Aktive Batteriesteuerung erlauben",
    type=FieldType.BOOLEAN,
    default=False,
    required=False,
    help="Erlaubt MinePower, den Betriebsmodus des Speichers zu schreiben: Netzladen bei "
         "günstigem Preis, manuelles Laden/Entladen per Knopfdruck, Sperre während eines "
         "Netzladefensters. Beim Beenden, nach Ablauf eines manuellen Befehls und beim "
         "Neustart setzt MinePower den Wechselrichter auf Eigenverbrauch zurück. Im "
         "Wechselrichter muss dafür nichts umgestellt werden.",
)

MAX_POWER_FIELD = ConfigField(
    key="max_power_w",
    label="Max. Lade-/Entladeleistung (W)",
    type=FieldType.NUMBER,
    default=5000,
    required=False,
    help="Obergrenze für Zwangsladen/-entladen. Liegt sie über dem, was dein Modell kann, "
         "lehnt der Wechselrichter den Befehl ab (Meldung 'Wert außerhalb des erlaubten "
         "Bereichs') – dann hier den Wert aus dem Datenblatt eintragen.",
)


@register
class SungrowBattery(SungrowDriverMixin, BatteryDriver):
    meta = DriverMeta(
        id="sungrow_battery",
        name="Sungrow Batterie SBR/SBH (steuerbar)",
        category=DeviceCategory.BATTERY,
        description="SBR-/SBH-Speicher am SH-Hybrid (gleiche IP wie der Wechselrichter). "
                    "Nötig für aktive Steuerung: Netzladen bei günstigem Preis, manuelles "
                    "Laden/Entladen, Sperre während eines Netzladefensters. Für reines "
                    "Monitoring reicht der SH-Treiber.",
        capabilities={"battery_monitor", "battery_control"},
        maturity=Maturity.BETA,
        fields=SUNGROW_COMMON_FIELDS + [ACTIVE_CONTROL_FIELD, MAX_POWER_FIELD],
        notes="Registerkarte nach Sungrow-Doku (SH-RT). Vor jedem SoC-Schreiben wird geprüft, "
              "ob die gelesenen Grenzen plausibel sind.",
    )

    #: Höchste Reserve, die der SH-Wechselrichter als Min-SoC annimmt
    #: (Register 13059: 0–50 %). Am 04.10. lehnte er 55 % mit 'interner
    #: Fehler' ab – danach blieb er bei 5 %. Darüber hält MinePower die
    #: Reserve selbst (Entladesperre).
    reserve_soc_max = 50.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = make_connection(config, name="Sungrow Batterie")
        self._control_regs: list[int] | None = None
        self._control_read_at = -1e9
        self._control_error: str | None = None

    @property
    def max_power_w(self) -> int:
        try:
            return max(100, int(float(self.config.get("max_power_w") or 5000)))
        except (TypeError, ValueError):
            return 5000

    # ------------------------------------------------------------ Lesen

    async def _control_block(self, force: bool = False) -> list[int] | None:
        """EMS-Modus, Zwangsbefehl/-leistung und SoC-Grenzen (13050–13059).

        Ein Fehler hier ist kein Gerätefehler: Manche WiNet-S-Firmware gibt
        Holding-Register nur eingeschränkt heraus. Dann fehlt nur der
        Modus-Readback, die Messwerte kommen trotzdem."""
        now = time.monotonic()
        if not force and self._control_regs is not None and now - self._control_read_at < CONTROL_READ_INTERVAL_S:
            return self._control_regs
        try:
            self._control_regs = await self.conn.read_holding(CONTROL_BLOCK_START - 1, CONTROL_BLOCK_LEN)
            self._control_error = None
        except Exception as exc:  # noqa: BLE001 – Nebenwert, Messung geht vor
            self._control_regs = None
            self._control_error = str(exc)
        self._control_read_at = now
        return self._control_regs

    @staticmethod
    def _decode_control(regs: list[int]) -> dict:
        ems = regs[REG_EMS_MODE - CONTROL_BLOCK_START]
        cmd = regs[REG_FORCE_CMD - CONTROL_BLOCK_START]
        power = regs[REG_FORCE_POWER - CONTROL_BLOCK_START]
        max_soc = regs[REG_MAX_SOC - CONTROL_BLOCK_START] * 0.1
        min_soc = regs[REG_MIN_SOC - CONTROL_BLOCK_START] * 0.1
        if ems == EMS_FORCED:
            mode = {
                CMD_CHARGE: BatteryMode.FORCE_CHARGE,
                CMD_DISCHARGE: BatteryMode.FORCE_DISCHARGE,
            }.get(cmd, BatteryMode.HOLD)
        else:
            # Eigenverbrauch, externes EMS und VPP: Der Wechselrichter
            # entscheidet selbst – aus Sicht von MinePower 'auto".
            mode = BatteryMode.AUTO
        return {"ems": ems, "cmd": cmd, "power": power, "max_soc": max_soc,
                "min_soc": min_soc, "mode": mode}

    async def read_data(self) -> BatteryData:
        state = u16(await self.conn.read_input(REG_RUNNING_STATE - 1, 1))
        regs = await self.conn.read_input(REG_BATTERY_POWER - 1, 4)
        power = float(u16(regs, 0))
        if state & BIT_BAT_DISCHARGING:
            power = -power
        elif not (state & BIT_BAT_CHARGING):
            power = 0.0

        extra: dict = {"Zustand (SoH)": f"{u16(regs, 2) * 0.1:.1f} %"}
        mode = BatteryMode.AUTO
        mode_known = False
        max_soc = min_soc = None
        control = await self._control_block()
        if control is not None:
            c = self._decode_control(control)
            mode, mode_known = c["mode"], True
            max_soc, min_soc = round(c["max_soc"], 1), round(c["min_soc"], 1)
            extra["EMS-Modus"] = f"{EMS_LABELS.get(c['ems'], 'unbekannt')} ({c['ems']})"
            if c["ems"] == EMS_FORCED:
                extra["Zwangsbefehl"] = f"{CMD_LABELS.get(c['cmd'], hex(c['cmd']))}, {c['power']} W"
            extra["SoC-Grenzen im WR"] = f"{min_soc:.0f} – {max_soc:.0f} %"
        elif self._control_error:
            extra["Modus-Readback"] = f"nicht lesbar ({self._control_error})"

        return BatteryData(
            soc=checked(
                "battery_soc", u16(regs, 1) * 0.1,
                source="Sungrow Batterie-SoC (Register 13023)",
                scale_hint="Erwartet werden 0–100 %. Weicht der Wert stark ab, stimmt die "
                           "Registerbelegung dieses Firmware-Stands nicht.",
            ),
            power=checked("battery_power", power, source="Sungrow Batterie-Leistung (Register 13022)"),
            mode=mode,
            mode_known=mode_known,
            max_soc=max_soc,
            min_soc=min_soc,
            temperature_c=sanitized("battery_temperature", s16(regs, 3) * 0.1),
            status="ok",
            extra=extra,
        )

    # ------------------------------------------------------------ Steuerung

    def _require_control(self) -> None:
        if not self.control_enabled():
            raise DeviceRejected(
                "Aktive Batteriesteuerung ist für dieses Gerät nicht freigeschaltet. "
                "Zum Aktivieren: Geräte → Sungrow Batterie bearbeiten → 'Aktive "
                "Batteriesteuerung erlauben'."
            )

    async def set_mode(self, mode: BatteryMode, power_w: float | None = None) -> None:
        """Betriebsmodus schreiben – mit Readback, Rückfall und Fail-safe.

        Reihenfolge ist Absicht: Beim Einschalten zuerst Leistung und Befehl,
        dann den EMS-Modus – sonst führt der Wechselrichter für einen Moment
        den *alten* Befehl aus (z. B. entladen statt laden). Beim Zurücksetzen
        umgekehrt: erst Eigenverbrauch, dann den Befehl auf 'stopp".

        **Rückfall bei Fehler:** Scheitert ein Schreibzugriff mitten in der
        Folge (Timeout, abgelehnter Wert), steht der Wechselrichter womöglich
        halb umgestellt da – etwa Zwangsmodus mit altem Befehl. Dann wird
        sofort auf Eigenverbrauch zurückgesetzt und der Fehler weitergereicht.
        Ein halber Befehl ist gefährlicher als keiner."""
        self._require_control()
        try:
            if mode == BatteryMode.AUTO:
                await self.conn.write_register(REG_EMS_MODE - 1, EMS_SELF_CONSUMPTION, verify=True)
                try:
                    # Hygiene: Manche Firmware normalisiert den Befehl im
                    # Eigenverbrauch selbst. Zurückgelesen wird trotzdem, damit
                    # das Schreibjournal nicht 'Readback: null" zeigt – ein
                    # abweichender Wert ist hier aber kein Fehler.
                    await self.conn.write_register(REG_FORCE_CMD - 1, CMD_STOP, verify=True)
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                if mode in (BatteryMode.FORCE_CHARGE, BatteryMode.FORCE_DISCHARGE):
                    watts = int(min(self.max_power_w, max(100.0, float(power_w or self.max_power_w))))
                    cmd = CMD_CHARGE if mode == BatteryMode.FORCE_CHARGE else CMD_DISCHARGE
                    await self.conn.write_register(REG_FORCE_POWER - 1, watts, verify=True)
                    await self.conn.write_register(REG_FORCE_CMD - 1, cmd, verify=True)
                else:  # HOLD
                    await self.conn.write_register(REG_FORCE_CMD - 1, CMD_STOP, verify=True)
                await self.conn.write_register(REG_EMS_MODE - 1, EMS_FORCED, verify=True)
            except Exception:
                await self._rollback()
                raise
        finally:
            # Nächster read_data liest den echten Zustand frisch aus.
            self._control_read_at = -1e9

    async def _rollback(self) -> None:
        """Fail-safe nach einem gescheiterten Befehl: Eigenverbrauch."""
        try:
            await self.conn.write_register(REG_EMS_MODE - 1, EMS_SELF_CONSUMPTION, verify=True)
            await self.conn.write_register(REG_FORCE_CMD - 1, CMD_STOP)
        except Exception:  # noqa: BLE001 – Rückfall ist best effort, der Fehler oben zählt
            pass

    async def set_reserve_soc(self, soc: int) -> None:
        self._require_control()
        regs = await self._control_block(force=True)
        if regs is None:
            raise DeviceRejected(
                "SoC-Grenzen des Wechselrichters nicht lesbar – ohne Plausibilitätsprüfung "
                "wird die Reserve nicht geschrieben."
            )
        c = self._decode_control(regs)
        # Plausibilität der Registerkarte: Eine Ladeobergrenze unter 50 % oder
        # eine Untergrenze darüber heißt fast sicher, dass die Adressen für
        # diesen Firmware-Stand nicht stimmen. Dann lieber nichts schreiben
        # als die Ladegrenze zu verstellen.
        if not (50.0 <= c["max_soc"] <= 100.0) or c["min_soc"] > c["max_soc"]:
            raise DeviceRejected(
                f"SoC-Register unplausibel (Max {c['max_soc']:.0f} %, Min {c['min_soc']:.0f} %) – "
                f"Registerkarte passt vermutlich nicht zu diesem Firmware-Stand. Reserve wird "
                f"nicht geschrieben."
            )
        target = max(0, min(int(soc), int(c["max_soc"]), int(self.reserve_soc_max)))
        try:
            await self.conn.write_register(REG_MIN_SOC - 1, target * 10, verify=True)
        finally:
            self._control_read_at = -1e9
