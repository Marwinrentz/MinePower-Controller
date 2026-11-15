"""Sungrow AC-Wallbox (AC011E-01, AC22E-01) über Modbus TCP.

**Experimentell, ohne Hardware geprüft.** Die Registerkarte stammt aus der
Referenzimplementierung in evcc (charger/sungrow.go, MIT-Lizenz), die an
echten AC011E/AC22E betrieben wird; Code wurde nicht übernommen.

Bis 3.2.0 stand hier ein anderes Layout (Status, Leistung und Sollwerte als
Block ab einer einstellbaren 'Register-Basis'), das sich an keiner Quelle
belegen ließ. Nach der Karte unten hätte es die Freigabe verkehrt herum
geschrieben (1 = Stopp) und den Sollstrom ins Register 'Verfügbarkeit'.

Adressen wie auf der Leitung (0-basiert), Standard-Unit-ID 248:

  Holding (lesen/schreiben)
    21202  Sollstrom 0,1 A (u16), 6,0 A … Nennstrom
    21203  Phasenumschaltung: 0 = dreiphasig, 1 = einphasig
    21210  Verfügbarkeit: 0 = gesperrt, 1 = verfügbar
    21211  Fernsteuerung: 0 = Start, 1 = Stopp

  Input (nur lesen)
    21269  Phasen-Zustand: 0 = dreiphasig, 1 = einphasig
    21299  Gesamtenergie Wh (u32, niederwertiges Wort zuerst)
    21301/03/05  Spannung L1–L3 0,1 V
    21307  Wirkleistung W (u32, niederwertiges Wort zuerst)
    21309  Energie des Ladevorgangs Wh (u32)
    21316  Zustand: 1 frei, 2 bereit (angesteckt), 3 lädt, 4 pausiert durch
           die Box, 5 pausiert durch das Fahrzeug, 6 beendet, 8 gesperrt,
           9 Störung

Die Phasenumschaltung nimmt die Box nur an, während nicht geladen wird; der
Regler pausiert dafür selbst.
"""
from __future__ import annotations

from ..base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    Maturity,
    WallboxData,
    WallboxDriver,
    WallboxState,
)
from ..modbus_util import u16, u32
from ..registry import register
from ..validation import CommandNotApplied, ImplausibleValue, checked, sanitized
from .common import SUNGROW_COMMON_FIELDS, SungrowDriverMixin, make_connection

# Holding
REG_SET_CURRENT = 21202
REG_PHASE_SWITCH = 21203
REG_AVAILABILITY = 21210
REG_REMOTE_CONTROL = 21211
# Input
REG_PHASE_STATE = 21269
REG_VOLTAGE_L1 = 21301
REG_ACTIVE_POWER = 21307
REG_SESSION_ENERGY = 21309
REG_STATE = 21316

REMOTE_START = 0
REMOTE_STOP = 1

STATE_MAP = {
    1: WallboxState.IDLE,
    2: WallboxState.CONNECTED,
    3: WallboxState.CHARGING,
    4: WallboxState.CONNECTED,   # pausiert durch die Box (z. B. unsere Sperre)
    5: WallboxState.CONNECTED,   # pausiert durch das Fahrzeug
    6: WallboxState.COMPLETE,
    7: WallboxState.CONNECTED,   # reserviert
    8: WallboxState.ERROR,       # gesperrt
    9: WallboxState.ERROR,
}
STATE_LABELS = {1: "frei", 2: "bereit", 3: "lädt", 4: "pausiert (Box)", 5: "pausiert (Fahrzeug)",
                6: "beendet", 7: "reserviert", 8: "gesperrt", 9: "Störung"}

_FIELDS = [f.model_copy(update={"default": 248, "help": "Modbus-Geräteadresse der Wallbox, Standard: 248."})
           if f.key == "unit_id" else f for f in SUNGROW_COMMON_FIELDS]


@register
class SungrowWallbox(SungrowDriverMixin, WallboxDriver):
    meta = DriverMeta(
        id="sungrow_wallbox",
        name="Sungrow AC-Wallbox AC011E / AC22E",
        category=DeviceCategory.WALLBOX,
        description="Sungrow AC-Wallbox über Modbus TCP: Ladefreigabe, Ladestrom in "
                    "0,1-A-Schritten, Phasenumschaltung, Leistung und Energie.",
        capabilities={"phase_switch", "write_test"},
        maturity=Maturity.EXPERIMENTAL,
        notes="Registerkarte nach der Referenzimplementierung in evcc, nicht an Hardware "
              "geprüft. Vor dem Betrieb 'Verbindung testen' mit Schreibtest ausführen.",
        fields=_FIELDS + [
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                        help="16 A bei der AC011E, 32 A bei der AC22E."),
            ConfigField(key="phase_switch", label="Phasenumschaltung nutzen", type=FieldType.BOOLEAN,
                        default=False, required=False,
                        help="Erlaubt dem Regler, zwischen ein- und dreiphasigem Laden zu wechseln."),
        ],
    )

    min_current = 6.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        if not config.get("unit_id"):
            config = {**config, "unit_id": 248}
        self.conn = make_connection(config, name="Sungrow Wallbox")
        try:
            self.max_current = max(6.0, min(32.0, float(config.get("max_current") or 16)))
        except (TypeError, ValueError):
            self.max_current = 16.0

    # ------------------------------------------------------------ Lesen

    async def read_data(self) -> WallboxData:
        raw_state = u16(await self.conn.read_input(REG_STATE, 1))
        if raw_state not in STATE_MAP:
            raise ImplausibleValue(
                f"Sungrow Wallbox: Zustand {raw_state} an Register {REG_STATE} ist unbekannt "
                f"(erwartet 1–9). Unit-ID (Standard 248) und Modell prüfen."
            )
        # Leistung und Vorgangsenergie liegen nebeneinander (21307–21310)
        regs = await self.conn.read_input(REG_ACTIVE_POWER, 4)
        power = checked("wallbox_power", float(u32(regs, 0)),
                        source=f"Sungrow Wallbox Wirkleistung (Register {REG_ACTIVE_POWER})")
        energy_wh = u32(regs, 2)
        current_set = phases = voltage = None
        try:
            current_set = sanitized("wallbox_current", u16(await self.conn.read_holding(REG_SET_CURRENT, 1)) * 0.1)
            phases = 1 if u16(await self.conn.read_input(REG_PHASE_STATE, 1)) == 1 else 3
            volts = u16(await self.conn.read_input(REG_VOLTAGE_L1, 1)) * 0.1
            voltage = volts if 180.0 <= volts <= 270.0 else None
        except Exception:  # noqa: BLE001 – Nebenwerte; Zustand und Leistung zählen
            pass
        return WallboxData(
            state=STATE_MAP[raw_state],
            power=power,
            energy_session_kwh=sanitized("energy_kwh", energy_wh / 1000.0),
            current_set=current_set,
            phases_active=phases,
            voltage=voltage,
            extra={"Zustand der Box": f"{STATE_LABELS.get(raw_state, '?')} ({raw_state})"},
        )

    # ------------------------------------------------------------ Schreiben

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        value = int(round(max(self.min_current, min(float(amps), self.max_current)) * 10))
        await self.conn.write_register(REG_SET_CURRENT, value, verify=True)

    async def start_charging(self) -> None:
        # Erst verfügbar machen: Eine gesperrte Box nimmt den Startbefehl nicht an.
        await self.conn.write_register(REG_AVAILABILITY, 1)
        await self.conn.write_register(REG_REMOTE_CONTROL, REMOTE_START)

    async def stop_charging(self) -> None:
        await self.conn.write_register(REG_REMOTE_CONTROL, REMOTE_STOP)

    async def set_phases(self, phases: int) -> None:
        if not self.config.get("phase_switch"):
            raise NotImplementedError("Phasenumschaltung an dieser Box nicht aktiviert")
        await self.conn.write_register(REG_PHASE_SWITCH, 1 if phases < 2 else 0, verify=True)

    async def test_command(self) -> dict | None:
        """Sollstrom auf den aktuellen Wert setzen und zurücklesen – ändert am
        Ladeverhalten nichts, beweist aber, dass die Box Sollwerte annimmt."""
        try:
            return await super().test_command()
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc)}
