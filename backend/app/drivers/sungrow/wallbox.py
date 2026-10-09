"""Sungrow AC-Wallbox (AC007/AC011E, iSolarCharger) über Modbus TCP.

**Beta.** Die Registerbelegung der Sungrow-AC-Charger ist deutlich schmaler
dokumentiert als die der Wechselrichter, und die Basisadresse unterscheidet
sich zwischen Modell- und Firmware-Ständen. Der Treiber ist deshalb bewusst
defensiv gebaut:

* Jeder gelesene Wert wird auf Plausibilität geprüft – ein falscher
  Registerblock fällt sofort mit Klartextmeldung auf, statt der Regelung
  Fantasiewerte unterzuschieben.
* Jeder Schreibbefehl wird zurückgelesen. Die Box quittiert Sollwerte auch
  dann, wenn sie im falschen Betriebsmodus steht und sie gar nicht umsetzt.
* Die Registerbasis ist konfigurierbar, und der Verbindungstest kann die
  gängigen Basisadressen automatisch durchprobieren.

Registerlayout relativ zur Basis (Standard 21200):
  base+0  (Input)  Status: 1 = frei, 2 = verbunden, 3 = lädt, 4 = Fehler
  base+1  (Input)  Ladeleistung W (u32)
  base+3  (Input)  Session-Energie 0,01 kWh (u32)
  base+10 (Hold)   Sollstrom 0,1 A (u16)
  base+11 (Hold)   Ladefreigabe: 0 = stopp, 1 = start
  base+12 (Hold)   Phasen: 1 / 3 (falls von der Box unterstützt)
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
    format_wallbox_values,
)
from ..modbus_util import u16, u32
from ..registry import register
from ..validation import CommandNotApplied, DriverError, ImplausibleValue, checked, sanitized
from .common import SUNGROW_COMMON_FIELDS, SungrowDriverMixin, make_connection

STATE_MAP = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CHARGING, 4: WallboxState.ERROR}

#: Bekannte Basisadressen verschiedener Firmware-Stände – der Verbindungstest
#: probiert sie der Reihe nach durch und meldet, welche plausibel antwortet.
CANDIDATE_BASES = (21200, 21000, 20000)


@register
class SungrowWallbox(SungrowDriverMixin, WallboxDriver):
    meta = DriverMeta(
        id="sungrow_wallbox",
        name="Sungrow AC-Wallbox AC007 / AC011E (Beta)",
        category=DeviceCategory.WALLBOX,
        description="Sungrow iSolarCharger AC-Wallbox über Modbus TCP: Ladefreigabe, "
                    "stufenloser Ladestrom, Status. Beta – die Registerbasis unterscheidet sich "
                    "je nach Firmware und ist deshalb einstellbar; der Verbindungstest findet "
                    "sie automatisch.",
        capabilities={"phase_switch", "write_test"},
        maturity=Maturity.EXPERIMENTAL,
        notes="Vor dem Produktivbetrieb bitte 'Verbindung testen' mit Schreibtest ausführen: "
              "Er zeigt, ob die Box den Sollstrom wirklich übernimmt. Tut sie das nicht, "
              "steht sie meist noch im internen Lademodus statt auf externer Ansteuerung.",
        fields=SUNGROW_COMMON_FIELDS + [
            ConfigField(key="register_base", label="Register-Basis", type=FieldType.NUMBER, default=21200,
                        help="Basisadresse der Wallbox-Register. Nur ändern, wenn der "
                             "Verbindungstest unplausible Werte zeigt – er schlägt die "
                             "passende Basis selbst vor."),
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                        help="Hardware-Limit der Wallbox (11 kW = 16 A, 22 kW = 32 A)."),
            ConfigField(key="phase_switch", label="Phasenumschaltung vorhanden", type=FieldType.BOOLEAN,
                        default=False, required=False,
                        help="Nur aktivieren, wenn die Box 1↔3-phasig umschalten kann. "
                             "Ist sie es nicht, lehnt sie den Befehl ab und die Regelung "
                             "bleibt unnötig im Umschaltversuch hängen."),
        ],
    )

    min_current = 6.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.conn = make_connection(config, name="Sungrow Wallbox")
        self.base = int(config.get("register_base") or 21200)
        self.max_current = float(config.get("max_current") or 16)

    # ------------------------------------------------------------ Lesen

    async def _read_at(self, base: int) -> WallboxData:
        regs = await self.conn.read_input(base, 5)
        raw_state = u16(regs, 0)
        if raw_state not in STATE_MAP:
            raise ImplausibleValue(
                f"Sungrow Wallbox: Statuscode {raw_state} an Register {base} ist unbekannt "
                f"(erwartet 1–4). Die Register-Basis passt vermutlich nicht zu dieser Firmware."
            )
        setpoints = await self.conn.read_holding(base + 10, 3)
        power = checked(
            "wallbox_power", float(u32(regs, 1)),
            source=f"Sungrow Wallbox Ladeleistung (Register {base + 1})",
        )
        current_set = sanitized("wallbox_current", u16(setpoints, 0) * 0.1)
        phases = u16(setpoints, 2)
        return WallboxData(
            state=STATE_MAP[raw_state],
            power=power,
            energy_session_kwh=sanitized("energy_kwh", u32(regs, 3) * 0.01),
            current_set=current_set,
            phases_active=phases if phases in (1, 3) else None,
        )

    async def read_data(self) -> WallboxData:
        return await self._read_at(self.base)

    # ------------------------------------------------------------ Schreiben

    async def set_current(self, amps: float) -> None:
        value = int(round(max(0.0, min(amps, self.max_current)) * 10))
        await self.conn.write_register(self.base + 10, value, verify=True)

    async def start_charging(self) -> None:
        await self.conn.write_register(self.base + 11, 1, verify=True)

    async def stop_charging(self) -> None:
        await self.conn.write_register(self.base + 11, 0, verify=True)

    async def set_phases(self, phases: int) -> None:
        if not self.config.get("phase_switch"):
            raise NotImplementedError("Phasenumschaltung an dieser Box nicht aktiviert")
        await self.conn.write_register(self.base + 12, 3 if phases >= 2 else 1, verify=True)

    # ------------------------------------------------------------ Setup-Hilfen

    async def _probe(self) -> dict:
        """Findet beim Verbindungstest selbst heraus, welche Register-Basis passt."""
        tried: list[str] = []
        for base in (self.base, *[b for b in CANDIDATE_BASES if b != self.base]):
            try:
                data = await self._read_at(base)
            except DriverError as exc:
                tried.append(f"{base}: {exc}")
                continue
            values = format_wallbox_values(data)
            if base != self.base:
                values["⚠ Register-Basis"] = (
                    f"{self.base} liefert keine plausiblen Werte, {base} schon – "
                    f"bitte im Feld 'Register-Basis' auf {base} ändern."
                )
            else:
                values["Register-Basis"] = str(base)
            return values
        raise ImplausibleValue(
            "Keine der bekannten Register-Basen liefert plausible Werte. Details: "
            + " | ".join(tried)
        )

    async def test_command(self) -> dict | None:
        """Sollstrom auf den aktuellen Wert setzen und zurücklesen – ändert am
        Ladeverhalten nichts, beweist aber, dass die Box Sollwerte annimmt."""
        try:
            return await super().test_command()
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc)}
