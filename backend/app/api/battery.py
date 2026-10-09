"""Manuelle Batteriesteuerung: Laden, Entladen, Entladung sperren, komplett
sperren – jeweils mit Dauer, und zurück auf Automatik.

Die Endpunkte schreiben NICHTS direkt auf den Wechselrichter. Sie setzen
nur den Sollzustand im Control-Loop und wecken ihn (`loop.kick()`); der
Schreibzugriff passiert im nächsten Takt, Bruchteile einer Sekunde später.
Damit gibt es genau einen Schreiber je Gerät – kein Wettlauf zwischen
Knopfdruck und Automatik, und die Antwort kommt sofort zurück, statt auf
Modbus zu warten.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core import runtime
from ..drivers.base import BatteryData, BatteryMode, DeviceCategory
from ..models import User
from ..security import get_current_user, require_role
from ..services.audit import log_event

router = APIRouter(prefix="/api/battery", tags=["battery"])

#: Grenzen der Eingaben. Leistung nach oben zusätzlich durch das Gerät
#: begrenzt (max_power_w), Dauer durch `battery_manual_max_min`.
MIN_POWER_W = 100
MIN_MINUTES = 5


class BatteryCommand(BaseModel):
    action: Literal["charge", "discharge", "auto", "hold", "no_discharge"]
    power_w: float | None = Field(default=None, gt=0)
    minutes: float | None = Field(default=None, gt=0)
    target_soc: float | None = Field(default=None, ge=0, le=100)
    device_id: int | None = None


def _battery_devices(loop, device_id: int | None):
    devs = [d for d in loop.devices.values()
            if d.category == DeviceCategory.BATTERY.value and d.enabled]
    if device_id is not None:
        devs = [d for d in devs if d.id == device_id]
    return devs


def status_payload(loop) -> dict:
    devices = []
    for dev in _battery_devices(loop, None):
        data = dev.data if isinstance(dev.data, BatteryData) else None
        target = getattr(dev, "battery_target", None)
        conn = getattr(dev.driver, "conn", None)
        devices.append({
            "id": dev.id,
            "name": dev.name,
            "online": dev.online,
            "controllable": loop.battery_controllable(dev),
            "control_enabled": bool(
                not hasattr(dev.driver, "control_enabled") or dev.driver.control_enabled()
            ),
            "soc": data.soc if data else None,
            "power": data.power if data else None,
            "mode": data.mode.value if data and data.mode_known else None,
            "max_soc": data.max_soc if data else None,
            "min_soc": data.min_soc if data else None,
            "target_mode": target[0].value if target else None,
            "target_power_w": target[1] if target else None,
            "command_error": dev.command_error,
            # Letzte Schreibzugriffe (Register, Wert, Readback, Ergebnis)
            "writes": list(getattr(conn, "write_log", []) or [])[-10:],
        })
    return {
        "manual": loop.battery_manual.as_dict() if loop.battery_manual else None,
        "manual_last": loop.battery_manual_last,
        "grid_charging": loop.battery_grid_charging,
        "defaults": {
            "power_w": loop.cfg.battery_manual_power_w,
            "max_minutes": loop.cfg.battery_manual_max_min,
            "reserve_soc": loop.cfg.battery_reserve_soc,
        },
        "devices": devices,
    }


@router.get("/status")
async def battery_status(_: User = Depends(get_current_user)):
    return status_payload(runtime.get_loop())


@router.post("/manual")
async def battery_manual(body: BatteryCommand, user: User = Depends(require_role("user"))):
    loop = runtime.get_loop()

    if body.action == "auto":
        await loop.stop_battery_manual("manuell zurück auf Automatik")
        await log_event("Batterie: zurück auf Automatik", category="battery", user_id=user.id)
        return {"ok": True, **status_payload(loop)}

    devs = _battery_devices(loop, body.device_id)
    if not devs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Kein Batteriegerät angelegt. Für die aktive Steuerung wird ein eigenes Batteriegerät "
            "gebraucht (z. B. 'Sungrow Batterie SBR/SBH') – der Wechselrichter allein liefert "
            "nur Messwerte.",
        )
    for dev in devs:
        if not loop.battery_controllable(dev):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{dev.name}: aktive Steuerung nicht freigeschaltet. Geräte → {dev.name} → "
                f"'Aktive Batteriesteuerung erlauben' einschalten.",
            )
        if not dev.online:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{dev.name} ist nicht erreichbar ({dev.last_error or 'keine Antwort'}) – "
                f"Befehl nicht gesetzt.",
            )

    max_minutes = float(loop.cfg.battery_manual_max_min)
    minutes = float(body.minutes or min(60.0, max_minutes))
    if not MIN_MINUTES <= minutes <= max_minutes:
        raise HTTPException(
            422,
            f"Dauer muss zwischen {MIN_MINUTES} und {max_minutes:.0f} Minuten liegen.",
        )

    reserve = float(loop.cfg.battery_reserve_soc)
    if body.action in ("hold", "no_discharge"):
        # Sperren braucht keine Leistung und kein Ziel – nur eine Dauer.
        loop.start_battery_manual(
            BatteryMode.HOLD if body.action == "hold" else BatteryMode.AUTO, MIN_POWER_W, minutes,
            device_id=body.device_id, intent=body.action,
        )
        label = "komplett gesperrt" if body.action == "hold" else "Entladung gesperrt"
        await log_event(f"Batterie manuell: {label} für {minutes:.0f} min", category="battery", user_id=user.id)
        return {"ok": True, **status_payload(loop)}
    # Leistung zählt nur beim Laden/Entladen. Die Vorgabe aus den
    # Einstellungen kann über der Gerätegrenze liegen (z. B. 7000 W bei 5 kW
    # Speicher) – sie wird dann gedeckelt; nur eine ausdrücklich übergebene
    # Leistung außerhalb des Bereichs ist ein Fehler.
    max_power = loop._battery_max_power(devs)
    if body.power_w is None:
        power = max(float(MIN_POWER_W), min(float(loop.cfg.battery_manual_power_w), max_power))
    else:
        power = float(body.power_w)
    if not MIN_POWER_W <= power <= max_power:
        raise HTTPException(
            422,
            f"Leistung muss zwischen {MIN_POWER_W} W und {max_power:.0f} W liegen.",
        )
    if body.action == "charge":
        mode = BatteryMode.FORCE_CHARGE
        target = float(body.target_soc if body.target_soc is not None else 100)
        if target < 10:
            raise HTTPException(422, "Ziel-SoC muss mindestens 10 % sein.")
    else:
        mode = BatteryMode.FORCE_DISCHARGE
        target = float(body.target_soc if body.target_soc is not None else reserve)
        # Nie unter die Reserve fürs Haus: Die schützt vor einem leeren
        # Speicher bei Netzausfall bzw. am Abend.
        if target < reserve:
            raise HTTPException(
                422,
                f"Untergrenze darf die Reserve ({reserve:.0f} %) nicht unterschreiten.",
            )

    loop.start_battery_manual(mode, power, minutes, target_soc=target, device_id=body.device_id,
                              intent="charge" if mode == BatteryMode.FORCE_CHARGE else "discharge")
    label = "Laden" if mode == BatteryMode.FORCE_CHARGE else "Entladen"
    await log_event(
        f"Batterie manuell: {label} mit {power:.0f} W für {minutes:.0f} min (Ziel {target:.0f} %)",
        category="battery", user_id=user.id,
    )
    return {"ok": True, **status_payload(loop)}
