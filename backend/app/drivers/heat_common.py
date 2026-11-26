"""Gemeinsames für Wärmepumpen und Klimaanlagen (seit 3.4).

Wärmepumpen und Klimaanlagen sind für MinePower Wärmelasten wie ein
Heizstab – mit zwei Unterschieden:

* Sie **regeln ihre Temperatur selbst**. MinePower gibt bei Überschuss frei
  (einschalten, Warmwasser-Einmalladung, Sollwert anheben) und nimmt die
  Freigabe zurück, wenn der Überschuss fehlt. Wie viel sie dann wirklich
  ziehen, entscheidet das Gerät.
* Ihr Verdichter verträgt kein Takten. Mindestlaufzeit und Mindestpause
  stehen deshalb in der Regelung (WaterHeaterSettings.min_on_s/min_off_s),
  die Treiber bringen über :func:`app.core.loop.heat_defaults` sinnvolle
  Vorgaben mit.

Rücknahme der Freigabe ist immer vorsichtig: Ein Gerät, das der Nutzer
selbst eingeschaltet hat, schaltet MinePower nicht ab – nur, was MinePower
eingeschaltet oder verstellt hat, stellt es wieder zurück.
"""
from __future__ import annotations

from typing import Any

from .base import ConfigField, FieldType

KIND_OPTIONS = [
    {"value": "heat_pump", "label": "Wärmepumpe"},
    {"value": "climate", "label": "Klimaanlage"},
    {"value": "water", "label": "Warmwasser / Heizstab"},
    {"value": "heating", "label": "Heizung"},
]


def kind_field(default: str) -> ConfigField:
    return ConfigField(key="heat_kind", label="Art des Geräts", type=FieldType.SELECT, default=default,
                       options=KIND_OPTIONS, required=False,
                       help="Nur für Anzeige und Vorgaben (Symbol, Verdichterschutz).")


def rated_field(default: float, label: str = "Leistungsaufnahme im Betrieb (W)") -> ConfigField:
    return ConfigField(key="rated_power", label=label, type=FieldType.NUMBER, default=default,
                       help="Elektrische Leistung, wenn das Gerät läuft. Gilt, solange es keine Messung liefert; "
                            "MinePower gibt erst frei, wenn mindestens so viel Überschuss da ist.")


#: Raumtemperatur für Klimaanlagen und Raumthermostate (°C). Ein Raum ist kein
#: Warmwasserspeicher: 60 °C wie beim Heizstab wären hier unsinnig bis
#: gefährlich, Heizen wie Kühlen bewegt sich zwischen 16 und 30 °C.
ROOM_TEMP_MIN_C = 16.0
ROOM_TEMP_MAX_C = 30.0
ROOM_TEMP_DEFAULT_C = 22.0

#: Treiber, deren 'Solltemperatur bei Überschuss' eine Raumtemperatur ist.
#: Wert: Funktion config -> bool (gilt nur für diese Einstellung).
ROOM_SETPOINT_DRIVERS = {
    "daikin_local": lambda cfg: True,
    "daikin_onecta": lambda cfg: str(cfg.get("target") or "climate") != "dhw",
    "homey_pro": lambda cfg: str(cfg.get("heat_kind") or "") == "climate",
}


def surplus_is_room(driver_id: str, config: dict) -> bool:
    """Ist die Überschuss-Solltemperatur dieses Geräts eine Raumtemperatur?"""
    rule = ROOM_SETPOINT_DRIVERS.get(driver_id)
    return bool(rule and rule(config or {}))


def room_temp(value: Any, default: float = ROOM_TEMP_DEFAULT_C) -> float:
    """Raum-Solltemperatur in 16–30 °C halten (auch für Altdaten wie 55 °C)."""
    t = num(value, default)
    # Weit darüber (z. B. 55 °C aus einem Warmwasser-Eintrag) ist kein
    # Raumwunsch, sondern ein Versehen: dann gilt die Vorgabe statt 30 °C.
    if t is None or t <= 0 or t > ROOM_TEMP_MAX_C + 5:
        t = default
    return max(ROOM_TEMP_MIN_C, min(ROOM_TEMP_MAX_C, float(t)))


def num(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "-", "--"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def wants_on(watts: float, rated: float) -> bool:
    """Relais-Logik wie beim Heizstab: ab halber Nennleistung gilt 'frei'."""
    return watts >= max(1.0, rated * 0.5)
