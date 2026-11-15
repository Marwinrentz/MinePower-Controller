"""go-e Charger (Gemini, HOMEfix, HOME+) über lokale HTTP-API v2.

Die lokale API muss in der go-e-App aktiviert sein (Internet → API).
Doku: https://github.com/goecharger/go-eCharger-API-v2

Wichtig für zuverlässiges Stellen: Die Box übernimmt `amp`/`frc`/`psm` nur,
wenn sie nicht gerade selbst im Eco-/PV-Modus regelt (`fsp`, interner
Lademodus). Deshalb liest der Treiber nach jedem Stellbefehl zurück und
meldet im Klartext, wenn die Box den Wert verworfen hat.
"""
from __future__ import annotations

import asyncio

from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    WallboxData,
    WallboxDriver,
    WallboxState,
)
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, DriverError, checked, sanitized

# car: 1 = frei, 2 = lädt, 3 = wartet auf Fahrzeug, 4 = beendet
#: car: 0 unbekannt/Fehler, 1 frei, 2 lädt, 3 wartet auf Fahrzeug, 4 fertig, 5 Fehler
CAR_STATES = {1: WallboxState.IDLE, 2: WallboxState.CHARGING, 3: WallboxState.CONNECTED, 4: WallboxState.COMPLETE,
              5: WallboxState.ERROR}
#: frc (forceState): 0 = neutral (die Box entscheidet nach ihrer eigenen
#: Logik), 1 = aus, 2 = ein.
FRC_OFF = 1
FRC_ON = 2

# err: 0/1 = ok, sonst Fehlerzustand der Box
ERROR_CODES = {
    2: "FI-Fehler (Fehlerstromschutz ausgelöst)",
    3: "Phasenfehler",
    4: "Überspannung",
    5: "Überstrom",
    6: "Diodenfehler im Fahrzeug",
    7: "PP-Widerstand ungültig (Ladekabel)",
    8: "Statusfehler",
    9: "Überhitzung",
}


@register
class GoeCharger(WallboxDriver):
    meta = DriverMeta(
        id="goe_charger",
        name="go-e Charger (HTTP-API v2)",
        category=DeviceCategory.WALLBOX,
        description="go-e Charger über die lokale HTTP-API v2 (in der go-e-App aktivieren). "
                    "Unterstützt stufenlosen Ladestrom, Start/Stopp und Phasenumschaltung.",
        capabilities={"phase_switch", "rfid", "write_test"},
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.60",
                        help="IP des go-e Chargers im lokalen Netz. In der go-e-App muss unter "
                             "Internet → API die lokale HTTP-API (v2) aktiviert sein."),
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                        help="Hardware-Limit (11 kW = 16 A, 22 kW = 32 A). Nie höher setzen als "
                             "die Absicherung des Anschlusses."),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="go-e Charger")
        self.max_current = float(config.get("max_current") or 16)

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def _status(self, keys: str) -> dict:
        data = await self.http.get_json("/api/status", {"filter": keys})
        if not isinstance(data, dict):
            raise DriverError(
                "go-e antwortet nicht im erwarteten Format. Ist die lokale HTTP-API v2 "
                "aktiviert (go-e-App → Internet → API)? Ältere Firmware spricht nur API v1."
            )
        return data

    async def _set(self, **kwargs) -> None:
        await self.http.get_json("/api/set", {k: v for k, v in kwargs.items()})

    async def read_data(self) -> WallboxData:
        s = await self._status("car,alw,amp,nrg,wh,psm,frc,trx,err")

        err = int(s.get("err") or 0)
        if err > 1:
            raise DriverError(
                f"go-e meldet einen Fehlerzustand: {ERROR_CODES.get(err, f'Fehlercode {err}')}. "
                f"Die Box lädt erst wieder, wenn der Fehler behoben und quittiert ist."
            )

        nrg = s.get("nrg") or []
        power = float(nrg[11]) if len(nrg) > 11 else 0.0  # Gesamtleistung in W
        phases = _phases(nrg, power, s.get("psm"))
        return WallboxData(
            state=CAR_STATES.get(int(s.get("car") or 1), WallboxState.IDLE),
            power=checked("wallbox_power", power, source="go-e nrg[11]"),
            current_set=sanitized("wallbox_current", _f(s.get("amp"))),
            phases_active=phases,
            energy_session_kwh=sanitized("energy_kwh", _f(s.get("wh")) / 1000.0),
            rfid_tag=str(s["trx"]) if s.get("trx") else None,
            extra={"Freigabe": "ja" if s.get("alw") else "nein"},
        )

    # ------------------------------------------------------------ Stellgrößen

    async def set_current(self, amps: float) -> None:
        target = int(round(max(0.0, min(amps, self.max_current))))
        await self._set(amp=target)
        await self._verify("amp", target, tolerance=1,
                           hint="Die Box regelt vermutlich noch selbst (Eco-/PV-Modus in der "
                                "go-e-App auf 'Standard/Neutral' stellen).")

    async def start_charging(self) -> None:
        # 2 = ein. Bis 3.2.0 wurde 0 (neutral) geschrieben; dann entscheidet
        # die Box nach ihrer eigenen Logik und lädt im Eco- oder Zeitplanmodus
        # trotz Freigabe nicht. Ein externer Regler muss ausdrücklich
        # einschalten (so auch die Referenzimplementierung in evcc).
        await self._set(frc=FRC_ON)
        await self._verify("frc", FRC_ON, hint="Ladefreigabe wurde nicht übernommen.")

    async def stop_charging(self) -> None:
        await self._set(frc=FRC_OFF)
        await self._verify("frc", FRC_OFF, hint="Stopp-Befehl wurde nicht übernommen.")

    async def set_phases(self, phases: int) -> None:
        target = 2 if phases >= 2 else 1
        await self._set(psm=target)
        await self._verify("psm", target,
                           hint="Phasenumschaltung nicht übernommen – manche Modelle schalten "
                                "nur bei getrenntem Fahrzeug um.")

    async def _verify(self, key: str, expected: float, *, tolerance: float = 0.0, hint: str = "") -> None:
        """Readback: go-e quittiert Stellbefehle auch dann mit 200 OK, wenn sie
        intern verworfen werden (falscher Lademodus, Fahrzeug nicht bereit)."""
        await asyncio.sleep(0.5)
        s = await self._status(key)
        got = _f(s.get(key))
        if abs(got - float(expected)) > tolerance:
            raise CommandNotApplied(
                f"go-e hat '{key}={expected}' nicht übernommen (Gerät meldet {got:g}). {hint}".strip()
            )


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _phases(nrg: list, power: float, psm) -> int | None:
    """Tatsächlich genutzte Phasen. ``psm`` ist nur die Vorgabe (0 = Auto,
    1 = einphasig, 2 = dreiphasig); ein einphasiges Fahrzeug lädt auch bei
    'dreiphasig' auf einer Phase. Unter Last zählen deshalb die gemessenen
    Phasenströme (nrg[4..6]); ohne Last gilt die Vorgabe, bei 'Auto' ist die
    Phasenzahl unbekannt."""
    if power > 500 and len(nrg) > 6:
        try:
            active = sum(1 for amps in nrg[4:7] if float(amps) > 1.0)
        except (TypeError, ValueError):
            active = 0
        if active:
            return 3 if active >= 2 else 1
    try:
        mode = int(psm)
    except (TypeError, ValueError):
        return None
    return {1: 1, 2: 3}.get(mode)
