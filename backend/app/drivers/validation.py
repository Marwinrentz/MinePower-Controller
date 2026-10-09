"""Defensive Wertprüfung und einheitliche Fehlertypen für alle Treiber.

Hintergrund: Registerbelegungen und Antwortformate schwanken je nach
Firmware-Stand und Modellvariante. Ein falsch gelesenes Register liefert
selten eine Exception – es liefert einen *plausibel aussehenden Unsinn*
(z. B. 65535 W PV-Leistung oder −3 °C Batterietemperatur im Sommer).
Solche Werte sind gefährlicher als ein Fehler, weil die Regelung sie
verarbeitet und Lasten falsch stellt.

Deshalb prüft jeder Treiber gelesene Werte gegen einen physikalisch
sinnvollen Bereich:

* :func:`checked`  – Wert muss im Bereich liegen, sonst klarer Fehler
  (Gerät wird offline gemeldet, Regelung fällt in den sicheren Zustand).
* :func:`sanitized` – Nebenwert (Temperatur, Zähler­stand): außerhalb des
  Bereichs wird ``None`` geliefert statt das ganze Gerät auszuwerfen.

Die Fehlertypen werden im GUI als Klartext angezeigt; deshalb enthalten
ihre Meldungen immer einen Hinweis, *was der Nutzer prüfen soll*.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- Fehlertypen


class DriverError(IOError):
    """Basis aller Treiberfehler. Erbt von IOError, damit bestehender
    Code (und der Control-Loop) unverändert weiterfunktioniert."""


class ConnectionProblem(DriverError):
    """Gerät nicht erreichbar / Verbindung abgebrochen (IP, Port, Kabel, Firmware-Update)."""


class DeviceRejected(DriverError):
    """Das Gerät hat geantwortet, den Befehl aber abgelehnt
    (falscher Betriebsmodus, Register gesperrt, Wert außerhalb Bereich)."""


class CommandNotApplied(DeviceRejected):
    """Der Befehl wurde ohne Fehler quittiert, das Readback zeigt aber einen
    anderen Wert – das Gerät hat ihn also faktisch nicht übernommen."""


class ImplausibleValue(DriverError):
    """Gelesener Wert liegt außerhalb des physikalisch sinnvollen Bereichs –
    fast immer ein falsches Register oder ein abweichender Firmware-Stand."""


# ---------------------------------------------------------------- Prüfungen

#: Physikalisch sinnvolle Bereiche für die Größen, die in die Regelung
#: einfließen. Bewusst großzügig – es geht um "offensichtlicher Unsinn",
#: nicht um Feinvalidierung. Werte in SI-Basiseinheiten des Systems.
RANGES: dict[str, tuple[float, float]] = {
    "pv_power": (-1_000.0, 500_000.0),        # kleine Negativwerte nachts sind normal
    "grid_power": (-500_000.0, 500_000.0),
    "battery_power": (-200_000.0, 200_000.0),
    "battery_soc": (0.0, 100.0),
    "battery_temperature": (-40.0, 90.0),
    "wallbox_power": (-1_000.0, 100_000.0),
    "wallbox_current": (0.0, 200.0),
    "heater_power": (-100.0, 50_000.0),
    "water_temperature": (-10.0, 120.0),
    "vehicle_soc": (0.0, 100.0),
    "energy_kwh": (0.0, 100_000_000.0),
    "phases": (1.0, 3.0),
}


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def checked(kind: str, value: float, *, source: str = "", scale_hint: str = "") -> float:
    """Pflichtwert prüfen. Außerhalb des Bereichs → :class:`ImplausibleValue`.

    `source` benennt Register/Feld für die Fehlermeldung, `scale_hint` gibt
    dem Nutzer einen konkreten nächsten Schritt (z. B. 'Skalierung prüfen").
    """
    lo, hi = RANGES.get(kind, (-float("inf"), float("inf")))
    if not _finite(value):
        raise ImplausibleValue(
            f"{source or kind}: Gerät liefert keinen gültigen Zahlenwert "
            f"(NaN/Unendlich) – Registeradresse und Datentyp prüfen."
        )
    v = float(value)
    if not (lo <= v <= hi):
        hint = f" {scale_hint}" if scale_hint else ""
        raise ImplausibleValue(
            f"{source or kind}: Wert {v:.6g} liegt außerhalb des plausiblen Bereichs "
            f"({lo:.6g} … {hi:.6g}). Meist stimmt die Registeradresse, der Datentyp "
            f"oder die Word-Reihenfolge nicht – bei abweichendem Firmware-Stand "
            f"kann sich die Registerkarte verschoben haben.{hint}"
        )
    return v


def sanitized(kind: str, value: float | None, *, zero_is_none: bool = False) -> float | None:
    """Nebenwert prüfen: außerhalb des Bereichs → ``None`` statt Fehler.

    Für Größen, ohne die die Regelung weiterläuft (Temperaturen, Zählerstände,
    Fahrzeug-SoC). Ein unplausibler Nebenwert darf kein Gerät offline nehmen.
    """
    if value is None or not _finite(value):
        return None
    v = float(value)
    if zero_is_none and v == 0.0:
        return None
    lo, hi = RANGES.get(kind, (-float("inf"), float("inf")))
    return v if lo <= v <= hi else None


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
