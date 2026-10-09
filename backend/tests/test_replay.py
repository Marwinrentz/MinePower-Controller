"""Teil D: Die 7-Tage-Historie aus der Diagnose durch alte und neue Regelung.

`tools/replay.py` baut aus dem 30-min-Verlauf und den Ereignissen eine
Minuten-Simulation und lässt dieselbe Woche durch beide Fassungen laufen.
Die Tabelle wird mit `pytest -s` ausgegeben (und steht im Abschlussbericht).

Dazu ein Sekunden-Test für das Start/Stopp-Flattern vom 07.10. 14:25–14:56
UTC – im 30-min-Raster ist es nicht sichtbar.
"""
from pathlib import Path

import pytest

from app.core.regulation import ChargeContext, WallboxController, WallboxSettings
from app.drivers.base import WallboxData, WallboxState
from tools.replay import compare, markdown

FIXTURE = Path(__file__).parent / "fixtures" / "diagnose_7d.json"


@pytest.fixture(scope="module")
def rows():
    out = compare(FIXTURE)
    print("\n" + markdown(out))
    return {r.label: r for r in out}


def test_new_regulation_never_grid_charges_battery_unrequested(rows):
    old = rows["bis 2.15 (Modell)"]
    assert old.grid_to_battery_unrequested_kwh > 10          # 01.10. und 08.10. belegt
    for label, kpi in rows.items():
        if label.startswith("2.16"):
            assert kpi.grid_to_battery_unrequested_kwh == 0.0, label


def test_same_floor_is_cheaper_and_more_autarkic(rows):
    """Fairer Vergleich: dieselbe Untergrenze (5 %, wie der Wechselrichter
    sie in der alten Fassung hatte)."""
    old, new = rows["bis 2.15 (Modell)"], rows["2.16 (Reserve 5 %)"]
    assert new.cost_eur < old.cost_eur
    assert new.import_kwh < old.import_kwh
    assert new.autarky_pct > old.autarky_pct


def test_reserve_is_kept(rows):
    """Reserve 55 %: Der Speicher fällt nicht mehr bis 5 % (er startet die
    Woche bei 11 % und lädt dann nur noch auf)."""
    old, new = rows["bis 2.15 (Modell)"], rows["2.16 (Reserve 55 %)"]
    assert old.soc_min <= 5.5
    assert new.hours_below_reserve < old.hours_below_reserve / 4


def test_expert_option_keeps_battery_out_of_grid_loads(rows):
    assert rows["2.16 (Reserve 20 %, alles gesperrt)"].battery_to_grid_loads_kwh < 0.5


# ------------------------------------------------------------ Flattern 07.10.

def _flapping_run(old: bool) -> tuple[int, float]:
    """40 min bei 0,5–3,5 kW Überschuss (Wolken, 07.10. 14:25 UTC: 2 kW PV).
    Auto dreiphasig (min. 5 A = 3,45 kW). Speicher deckt jede Lücke.
    Rückgabe: (Starts, aus dem Speicher ins Auto in kWh)."""
    import math

    t = [0.0]
    ctrl = WallboxController(WallboxSettings(mode="pv_only", min_current=5, max_current=16,
                                             phases_mode="fixed3", start_threshold_w=1400,
                                             start_delay_s=60, stop_delay_s=180,
                                             min_pause_s=0 if old else 300), clock=lambda: t[0])
    if old:
        ctrl._observe_phases = lambda data: None
    car_w = 0.0
    starts = 0
    battery_kwh = 0.0
    discharge_since = None
    for _ in range(240):                                  # 10-s-Takt
        surplus = 2000 + 1500 * math.sin(t[0] / 120)
        if old:
            ctrl._phases_seen = 1                         # Fehlannahme der alten Fassung
        deficit = max(0.0, car_w - surplus)
        export = max(0.0, surplus - car_w)
        if deficit > 250:
            discharge_since = t[0] if discharge_since is None else discharge_since
        else:
            discharge_since = None
        protected = discharge_since is not None and t[0] - discharge_since >= 30
        pool = max(0.0, car_w + export - deficit)
        amps = car_w / 690 if car_w else 0.0
        state = WallboxState.CHARGING if car_w else WallboxState.CONNECTED
        data = WallboxData(state=state, power=car_w, current_set=amps or None, phases_active=3, voltage=230)
        d = ctrl.decide(pool, data, ChargeContext(house_limit_a=63, battery_handled_globally=True,
                                                  battery_protected=protected))
        new_w = max(5.0, d.current_a) * 690 if d.enable else 0.0
        if new_w and not car_w:
            starts += 1
        car_w = new_w
        battery_kwh += max(0.0, car_w - surplus) * 10 / 3600 / 1000
        t[0] += 10
    return starts, battery_kwh


def test_flapping_of_07_10_is_gone():
    old_starts, old_battery = _flapping_run(old=True)
    new_starts, new_battery = _flapping_run(old=False)
    print(f"\nFlattern 07.10.: alt {old_starts} Starts / {old_battery:.2f} kWh aus dem Speicher, "
          f"neu {new_starts} Starts / {new_battery:.2f} kWh")
    assert old_starts >= 3
    assert new_starts == 0          # 2 kW reichen für ein dreiphasiges Auto nicht
    assert new_battery == 0.0
