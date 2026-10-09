"""Wiedergabe einer Diagnose-Woche durch alte und neue Regelung.

Aus dem Verlauf (30-min-Raster: PV, Hauslast, Preis) und den Ereignissen
(Anstecken, Eingriffe, Geräteprogramm des Heizstabs, manuelle Batterie-
befehle) wird eine Minuten-Simulation der Anlage gebaut. Dieselbe Woche
läuft zweimal durch die echten Regler-Klassen (WallboxController,
WaterHeaterController) – einmal mit dem Verhalten bis 2.15, einmal mit 2.16.

Was die alte Fassung falsch machte, wird so nachgestellt, wie es im
Protokoll belegt ist:
  * Zwangsladen des Speichers aus dem Netz im „Preisfenster" (01.10. 23–01 Uhr,
    08.10. 08:00–09:05 UTC) trotz „Netzladen aus",
  * „Aus"/„Sofort" ohne Ende,
  * Phasen-Fehlannahme ab 07.10. 04:00 UTC (1-phasig gerechnet, 3-phasig gezogen),
  * Speicher deckt Eigenbetrieb (ELWA-Programm) und „Sofort laden",
  * Reserve nur in MinePower – Wechselrichter entlädt bis 5 %.

**Modellannahmen** (beide Läufe gleich): Speicher 15 kWh / 5 kW / 95 %
je Richtung; Auto dreiphasig 5–16 A, angesteckt rund um belegte Lade-
ereignisse (±, siehe `car_intervals`), je Anstecken 40 kWh Bedarf;
Warmwasser 200 l, 2 × 2,5 kWh Zapfung (06 und 19 Uhr Ortszeit), 1,2 kWh/Tag
Verlust. Die Zahlen sind ein Vergleich unter gleichen Annahmen, keine
Abrechnung.

Aufruf:  python -m tools.replay <diagnose.json>
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from app.core.battery_policy import (
    BatteryInputs, BatteryIntent, DischargeGuard, GuardMode, plan_battery,
)
from app.core.regulation import (
    ChargeContext, RegulationConfig, WallboxController, WallboxSettings,
    WaterHeaterController, WaterHeaterSettings, protected_battery_discharge,
)
from app.drivers.base import WallboxData, WallboxState, WaterHeaterData

BERLIN = ZoneInfo("Europe/Berlin")
STEP_S = 60.0
V = 230.0


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


# ------------------------------------------------------------------ Eingaben

@dataclass
class Week:
    start: datetime
    end: datetime
    pv: list[float]
    house: list[float]
    price: list[float]
    soc0: float
    measured: dict
    events: list[dict]

    def bucket(self, now: datetime) -> int:
        return min(len(self.pv) - 1, max(0, int((now - self.start).total_seconds() // 1800)))


def load(path: str | Path) -> Week:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    hist = data["history"]

    def series(key):
        rows = hist[key]
        if rows and isinstance(rows[0], dict):
            return [(r["time"], r["value"]) for r in rows]
        return [(r[0], r[1]) for r in rows]

    pv = series("pv_power")
    times = [_t(t) for t, _v in pv]

    def values(key, default=0.0):
        return [default if v is None else float(v) for _t0, v in series(key)]

    events = [e for e in data["events"] if e.get("category") in ("control", "device", "waste")]
    events.sort(key=lambda e: e["time"])
    soc = values("battery_soc", 50.0)
    return Week(
        start=times[0], end=times[-1] + timedelta(minutes=30),
        pv=values("pv_power"), house=values("house_power", 500.0), price=values("price_ct", 35.0),
        soc0=soc[0], measured={k: values(k) for k in hist}, events=events,
    )


def car_intervals(week: Week) -> list[tuple[datetime, datetime]]:
    """Wann war das Auto angesteckt? Aus belegten Ereignissen (Anstecken,
    Selbststart, Ladevorgang, wieder erreichbar) und gemessener Ladung:
    jeweils 15 min davor bis 4 h danach, Lücken darunter zusammengefasst."""
    marks = [_t(e["time"]) for e in week.events
             if "Tesla" in e["message"] and any(k in e["message"] for k in (
                 "Ladevorgang", "selbst angefangen", "wieder erreichbar", "startet selbst"))]
    wb = week.measured.get("wallbox_power", [])
    marks += [week.start + timedelta(minutes=30 * i) for i, w in enumerate(wb) if w > 200]
    marks.sort()
    out: list[list[datetime]] = []
    for m in marks:
        a, b = m - timedelta(minutes=15), m + timedelta(hours=4)
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def windows(week: Week, start_marker: str, end_marker: str, who: str) -> list[tuple[datetime, datetime]]:
    out, open_at = [], None
    for e in week.events:
        m = e["message"]
        if who not in m:
            continue
        if start_marker in m and open_at is None:
            open_at = _t(e["time"])
        elif end_marker in m and open_at is not None:
            out.append((open_at, _t(e["time"])))
            open_at = None
    return out


def overrides(week: Week) -> list[tuple[datetime, str]]:
    out = []
    for e in week.events:
        m = e["message"]
        if m.startswith("Override: ") and "Tesla" in m:
            kind = m[len("Override: "):].split(" →")[0]
            if kind in ("fast", "stop", "auto"):
                out.append((_t(e["time"]), kind))
    return out


def boosts(week: Week) -> list[datetime]:
    return [_t(e["time"]) for e in week.events if e["message"].startswith("Override: boost → ")]


def manual_battery(week: Week) -> list[tuple[datetime, datetime, float]]:
    out, start, power = [], None, 0.0
    for e in week.events:
        m = e["message"]
        if m.startswith("Batterie manuell: Laden mit "):
            start = _t(e["time"])
            power = float(m.split("mit ")[1].split(" W")[0])
        elif start is not None and ("Batterie-Laden beendet" in m):
            out.append((start, _t(e["time"]), power))
            start = None
    return out


#: Belegte Zwangsladungen der alten Fassung („vollladen" im Preisfenster)
OLD_FORCED_CHARGE = [
    (datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc), datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)),
    (datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc), datetime(2026, 10, 8, 9, 5, tzinfo=timezone.utc)),
]
#: Ab hier rechnete die alte Fassung 1-phasig (Ereignis 07.10. 04:00:29 UTC)
OLD_PHASE_BUG_FROM = datetime(2026, 10, 7, 4, 0, 29, tzinfo=timezone.utc)


def _inside(now: datetime, spans) -> bool:
    return any(a <= now < b for a, b, *_ in spans)


# ------------------------------------------------------------------ Ergebnis

@dataclass
class Kpi:
    label: str
    waste_kwh: float = 0.0
    import_kwh: float = 0.0
    import_expensive_kwh: float = 0.0
    consumption_kwh: float = 0.0
    cost_eur: float = 0.0
    car_kwh: float = 0.0
    car_starts: int = 0
    short_sessions: int = 0
    grid_to_battery_unrequested_kwh: float = 0.0
    battery_to_grid_loads_kwh: float = 0.0
    soc_min: float = 100.0
    hours_below_reserve: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def autarky_pct(self) -> float:
        return 100.0 * (1 - self.import_kwh / self.consumption_kwh) if self.consumption_kwh else 0.0

    def row(self) -> dict:
        return {
            "Lauf": self.label,
            "verschenkt kWh": round(self.waste_kwh, 1),
            "Netzbezug kWh": round(self.import_kwh, 1),
            "davon teuer kWh": round(self.import_expensive_kwh, 1),
            "Autarkie %": round(self.autarky_pct, 1),
            "Kosten €": round(self.cost_eur, 2),
            "Auto-Starts": self.car_starts,
            "Kurzladungen <15 min": self.short_sessions,
            "Netz→Batterie ungewollt kWh": round(self.grid_to_battery_unrequested_kwh, 1),
            "Batterie→Netzlast kWh": round(self.battery_to_grid_loads_kwh, 1),
            "SoC min %": round(self.soc_min, 1),
            "h unter Reserve": round(self.hours_below_reserve, 1),
        }


# ------------------------------------------------------------------ Simulation

def simulate(week: Week, policy: str, reserve_soc: float = 55.0, protect_all: bool = False) -> Kpi:
    """policy: "old" (bis 2.15) oder "new" (2.16). `protect_all` = Experten-
    Option „Speicher auch für Sofort/Boost/Geräteprogramm sperren"."""
    old = policy == "old"
    cap_kwh, bat_max, eff = 15.0, 5000.0, 0.95
    inverter_min = 5.0 if old else min(reserve_soc, 50.0)
    soc = week.soc0
    clock = [0.0]
    now = week.start

    car_spans = car_intervals(week)
    own_spans = windows(week, "eigenen Programm", "eigenes Programm beendet", "ELWA")
    ovr = overrides(week)
    boost_times = boosts(week)
    manual = manual_battery(week)
    expensive = median(week.price)

    wb = WallboxController(WallboxSettings(mode="pv_only", min_current=5, max_current=16, phases_mode="fixed3",
                                           start_threshold_w=1400, start_delay_s=60, stop_delay_s=180,
                                           min_pause_s=0 if old else 300),
                           clock=lambda: clock[0])
    wb.utcnow = lambda: now
    if old:
        wb._check_override_end = lambda data: None        # alte Fassung: kein Ende
        wb._observe_phases = lambda data: None
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", max_power_w=3000, min_power_w=100,
                                                   target_temp_c=58, surplus_temp_c=62, boost_temp_c=50,
                                                   boost_end_mode="both", boost_duration_min=30),
                               clock=lambda: clock[0])
    cfg = RegulationConfig(battery_reserve_soc=reserve_soc)
    guard = DischargeGuard()

    temp = 50.0
    tank_kwh_per_k = 0.233
    car_need = 0.0
    car_plugged_prev = False
    car_w = heat_w = bat_w = 0.0
    grid_w = 0.0
    kpi = Kpi(label={"old": "bis 2.15 (Modell)",
                     "new": f"2.16 (Reserve {reserve_soc:.0f} %{', alles gesperrt' if protect_all else ''})"}[policy])
    enabled_prev = False
    session_start: datetime | None = None
    next_ovr = 0
    next_boost = 0
    was_grid_charging = False

    while now < week.end:
        i = week.bucket(now)
        pv, house, price = week.pv[i], week.house[i], week.price[i]
        local = now.astimezone(BERLIN)

        # --- Auto da? ---------------------------------------------------
        plugged = _inside(now, car_spans)
        if plugged and not car_plugged_prev:
            car_need = 40.0
        car_plugged_prev = plugged
        state = (WallboxState.IDLE if not plugged
                 else WallboxState.COMPLETE if car_need <= 0
                 else WallboxState.CHARGING if car_w > 100 else WallboxState.CONNECTED)

        # --- Eingriffe aus dem Protokoll ---------------------------------
        while next_ovr < len(ovr) and ovr[next_ovr][0] <= now:
            kind = ovr[next_ovr][1]
            if kind == "auto":
                wb.set_override(None)
            else:
                hours = None if old else (cfg.override_stop_h if kind == "stop" else cfg.override_fast_h)
                wb.set_override(kind, hours=hours)
            next_ovr += 1
        while next_boost < len(boost_times) and boost_times[next_boost] <= now:
            wh.start_boost(now)
            next_boost += 1
        own_program = _inside(now, own_spans)

        # --- Regelung (ein Takt je Minute) ---------------------------------
        bat_discharge = max(0.0, -bat_w)
        controllable = car_w + (0.0 if own_program else heat_w)
        budget = controllable - grid_w
        pool = max(0.0, budget - protected_battery_discharge(soc, bat_w, cfg))

        amps = car_w / (3 * V) if car_w > 0 else 0.0
        data = WallboxData(state=state, power=car_w, current_set=amps or None,
                           phases_active=(1 if old and now >= OLD_PHASE_BUG_FROM else 3), voltage=V)
        if old and now >= OLD_PHASE_BUG_FROM:
            wb._phases_seen = 1
        ctx = ChargeContext(now=local.replace(tzinfo=None), house_limit_a=35, battery_soc=soc,
                            battery_power=bat_w, battery_handled_globally=True,
                            battery_protected=bat_discharge > 250)
        d = wb.decide(pool, data, ctx)
        car_cmd = d.current_a if d.enable else 0.0
        car_alloc = d.power_w if d.enable else 0.0
        hd = wh.decide(max(0.0, pool - car_alloc), WaterHeaterData(power=heat_w, temperature_c=temp), ctx)
        heat_cmd = hd.power_w

        # --- Batterie-Entscheidung ------------------------------------------
        grid_loads = []
        if wb.override == "fast" and plugged and car_need > 0:
            grid_loads.append("Auto (Sofort)")
        if wh.boost:
            grid_loads.append("ELWA (Boost)")
        manual_now = next(((a, b, p) for a, b, p in manual if a <= now < b), None)
        forced_old = old and _inside(now, OLD_FORCED_CHARGE)

        if old:
            if manual_now or forced_old:
                bmode, bpow = "charge", (manual_now[2] if manual_now else 5000.0)
            else:
                bmode, bpow = "auto", 0.0
        else:
            plan = plan_battery(BatteryInputs(
                soc=soc, reserve_soc=reserve_soc, inverter_holds_reserve=reserve_soc <= 50.0,
                price_cheap=False, other_loads=tuple(grid_loads), protect_other_loads=protect_all,
                foreign_loads=("ELWA (Geräteprogramm)",) if own_program else (),
                manual=BatteryIntent.CHARGE if manual_now else None,
                was_grid_charging=was_grid_charging,
            ))
            was_grid_charging = plan.intent == BatteryIntent.CHARGE
            if plan.intent == BatteryIntent.CHARGE:
                bmode, bpow = "charge", manual_now[2] if manual_now else bat_max
            elif plan.intent == BatteryIntent.NO_DISCHARGE:
                house_now = house
                expected = (16 * 3 * V if "Auto (Sofort)" in grid_loads else 0.0) + (3000 if wh.boost else 0.0)
                step = guard.step(clock[0], battery_w=bat_w, grid_w=grid_w, pv_w=pv, house_w=house_now,
                                  expected_load_w=expected, soc=soc, reserve_soc=reserve_soc)
                bmode, bpow = {GuardMode.AUTO: ("auto", 0.0), GuardMode.HOLD: ("hold", 0.0),
                               GuardMode.HOUSE: ("discharge", step.power_w)}[step.mode]
            else:
                guard.reset()
                bmode, bpow = "auto", 0.0

        # --- Physik ---------------------------------------------------------
        car_w = 0.0
        if plugged and car_need > 0 and (car_cmd > 0 or (old and wb.override == "fast")):
            current = max(5.0, min(16.0, car_cmd or 16.0))
            car_w = current * 3 * V
        if own_program:
            heat_w = 2990.0 if temp < 80 else 0.0
        else:
            heat_w = min(heat_cmd, 3000.0) if temp < 80 else 0.0

        residual = pv - house - car_w - heat_w
        room_kwh = (100.0 - soc) / 100.0 * cap_kwh
        avail_kwh = max(0.0, (soc - inverter_min) / 100.0 * cap_kwh)
        h = STEP_S / 3600.0
        if bmode == "auto":
            if residual > 0:
                bat_w = min(residual, bat_max, room_kwh / h * 1000.0 / eff)
            else:
                bat_w = -min(-residual, bat_max, avail_kwh / h * 1000.0 * eff)
        elif bmode == "hold":
            bat_w = 0.0
        elif bmode == "charge":
            bat_w = min(bpow, bat_max, room_kwh / h * 1000.0 / eff)
        else:  # discharge (Hauslast)
            bat_w = -min(bpow, bat_max, avail_kwh / h * 1000.0 * eff)
        if bat_w > 0:
            soc += bat_w * h / 1000.0 * eff / cap_kwh * 100.0
        else:
            soc += bat_w * h / 1000.0 / eff / cap_kwh * 100.0
        soc = max(0.0, min(100.0, soc))
        grid_w = house + car_w + heat_w + bat_w - pv

        # Warmwasser
        draw = 2.5 if local.hour in (6, 19) else 0.0          # kWh je Stunde
        temp += (heat_w * h / 1000.0 - draw * h - 1.2 / 24 * h) / tank_kwh_per_k
        temp = max(15.0, temp)
        if car_w > 0:
            car_need -= car_w * h / 1000.0

        # --- Kennzahlen -----------------------------------------------------
        kwh = abs(grid_w) * h / 1000.0
        kpi.consumption_kwh += (house + car_w + heat_w) * h / 1000.0
        kpi.car_kwh += car_w * h / 1000.0
        if grid_w < 0:
            kpi.waste_kwh += kwh
        else:
            kpi.import_kwh += kwh
            kpi.cost_eur += kwh * price / 100.0
            if price >= expensive:
                kpi.import_expensive_kwh += kwh
        if bat_w > 0 and grid_w > 0 and not manual_now and bmode == "charge" and (old or not was_grid_charging):
            kpi.grid_to_battery_unrequested_kwh += min(bat_w, grid_w) * h / 1000.0
        grid_load_w = (car_w if wb.override == "fast" else 0.0) + (heat_w if (wh.boost or own_program) else 0.0)
        if bat_w < 0 and grid_load_w > 0:
            served_house = min(-bat_w, max(0.0, house - pv))
            kpi.battery_to_grid_loads_kwh += min(-bat_w - served_house, grid_load_w) * h / 1000.0 \
                if -bat_w > served_house else 0.0
        kpi.soc_min = min(kpi.soc_min, soc)
        if soc < reserve_soc - 0.5:
            kpi.hours_below_reserve += h
        enabled = car_w > 0
        if enabled and not enabled_prev:
            kpi.car_starts += 1
            session_start = now
        if not enabled and enabled_prev and session_start is not None:
            if (now - session_start) < timedelta(minutes=15):
                kpi.short_sessions += 1
        enabled_prev = enabled

        clock[0] += STEP_S
        now += timedelta(seconds=STEP_S)
    return kpi


def measured(week: Week, reserve_soc: float = 55.0) -> Kpi:
    """Kennzahlen der echten Woche (aus dem 30-min-Verlauf)."""
    m = week.measured
    kpi = Kpi(label="gemessen (Ist)")
    expensive = median(week.price)
    for i in range(len(week.pv)):
        g = m["grid_power"][i]
        h = 0.5
        if g < 0:
            kpi.waste_kwh += -g * h / 1000.0
        else:
            kpi.import_kwh += g * h / 1000.0
            kpi.cost_eur += g * h / 1000.0 * week.price[i] / 100.0
            if week.price[i] >= expensive:
                kpi.import_expensive_kwh += g * h / 1000.0
        kpi.consumption_kwh += (m["house_power"][i] + m["wallbox_power"][i] + m["water_power"][i]) * h / 1000.0
        kpi.car_kwh += m["wallbox_power"][i] * h / 1000.0
        soc = m["battery_soc"][i]
        kpi.soc_min = min(kpi.soc_min, soc)
        if soc < reserve_soc - 0.5:
            kpi.hours_below_reserve += h
    kpi.short_sessions = sum(
        1 for e in week.events if e["message"].startswith("Ladevorgang beendet")
        and any(f"({x} kWh" in e["message"] for x in ("0.1", "0.2", "0.4", "0.5", "0.7"))
    )
    return kpi


def compare(path: str | Path) -> list[Kpi]:
    week = load(path)
    return [
        measured(week),
        simulate(week, "old"),
        simulate(week, "new", 55.0),
        simulate(week, "new", 20.0),
        simulate(week, "new", 5.0),
        simulate(week, "new", 20.0, protect_all=True),
    ]


def markdown(rows: list[Kpi]) -> str:
    table = [r.row() for r in rows]
    keys = list(table[0])
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    for r in table:
        lines.append("| " + " | ".join(str(r[k]) for k in keys) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parents[1] / "tests/fixtures/diagnose_7d.json")
    print(markdown(compare(src)))
