"""Plausibilitätsprüfung der Messwerte.

Der Regler rechnet mit dem, was die Geräte melden. Ist ein Vorzeichen
vertauscht oder zählt ein Wechselrichter die Batterieentladung als
Sonnenstrom, regelt er sauber – auf falschen Zahlen. Solche Fehler fallen
nicht als Ausfall auf, wohl aber an Lagen, die es physikalisch nicht gibt.

Diese Prüfung hält fest, wie lange eine solche Lage ununterbrochen besteht,
und meldet sie erst nach einigen Minuten: Einzelne Takte dürfen abweichen,
weil die Geräte nicht gleichzeitig gelesen werden.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    hint: str
    #: So lange muss die Lage bestehen, bevor sie gemeldet wird (s)
    after_s: float


CHECKS = {
    "balance": Check(
        "balance", "Energiebilanz geht nicht auf",
        "Vorzeichen prüfen: Netzzähler (+ = Bezug), Batterie (+ = laden). Ist ein Gerät doppelt "
        "angelegt oder misst ein Zähler nur einen Teil der Anlage?", 300.0),
    "export_without_source": Check(
        "export_without_source", "Einspeisung ohne Erzeugung",
        "Der Netzzähler meldet Einspeisung, obwohl weder PV noch Batterie liefern. Vermutlich ist "
        "die Zählrichtung vertauscht (Geräte → Zähler → Richtung invertieren).", 300.0),
    "pv_at_night": Check(
        "pv_at_night", "PV-Leistung mitten in der Nacht",
        "Der Wechselrichter meldet nachts Erzeugung. Meist zählt er die Entladung der Batterie "
        "als Sonnenstrom – Treiber und Gerätetyp prüfen.", 600.0),
}

#: Schwellen (W): darunter gilt eine Abweichung als Messrauschen
BALANCE_W = 150.0
EXPORT_W = 200.0
NO_PV_W = 50.0
NIGHT_PV_W = 100.0
#: Stunden (Ortszeit), in denen keine Sonne scheint – außer nördlich des Polarkreises
NIGHT_HOURS = (0, 1, 2)


class PlausibilityTracker:
    def __init__(self) -> None:
        self._since: dict[str, float] = {}
        self._detail: dict[str, str] = {}

    def update(self, *, now: float, pv: float, grid: float | None, battery_power: float | None,
               loads: float, local_hour: int) -> None:
        """Mit den Werten eines Takts füttern. `now` ist eine monotone Uhr,
        `battery_power` + = laden, `loads` die Summe der gesteuerten Lasten."""
        active: dict[str, str] = {}
        if grid is not None:
            bat = battery_power or 0.0
            house = grid + pv - loads - bat
            if house < -BALANCE_W:
                active["balance"] = (
                    f"Netz {grid:+.0f} W + PV {pv:.0f} W − Lasten {loads:.0f} W − Batterie {bat:+.0f} W "
                    f"ergibt einen Hausverbrauch von {house:.0f} W. Ein Haus kann nichts erzeugen.")
            discharging = battery_power is not None and battery_power < -100.0
            if grid < -EXPORT_W and pv < NO_PV_W and not discharging:
                active["export_without_source"] = (
                    f"Netz {grid:+.0f} W (Einspeisung) bei PV {pv:.0f} W"
                    + ("" if battery_power is None else f" und Batterie {battery_power:+.0f} W") + ".")
        if pv > NIGHT_PV_W and local_hour in NIGHT_HOURS:
            active["pv_at_night"] = f"PV {pv:.0f} W um {local_hour} Uhr."
        for key in list(self._since):
            if key not in active:
                del self._since[key]
                self._detail.pop(key, None)
        for key, detail in active.items():
            self._since.setdefault(key, now)
            self._detail[key] = detail

    def findings(self, now: float) -> list[dict]:
        """Lagen, die schon länger als ihre Wartezeit bestehen."""
        out = []
        for key, since in self._since.items():
            check = CHECKS[key]
            if now - since >= check.after_s:
                out.append({"key": key, "title": check.title, "detail": self._detail.get(key, ""),
                            "hint": check.hint, "since_s": round(now - since)})
        return out
