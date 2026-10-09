"""Entscheidungstabelle (Auftrag Teil B): jede Kombination aus

* Auto:        Aus / PV only / PV + Preis / Schnell
* Warmwasser:  gesperrt / PV only / PV + Preis / Boost
* Batterie:    Automatik / Entladung gesperrt / Laden / komplett gesperrt
* Umfeld:      Preis günstig/teuer × SoC niedrig/hoch × Prognose gut/schlecht
               × Auto angesteckt/weg/offline

wird durch dieselben reinen Funktionen geschickt, die auch der Control-Loop
nutzt (battery_policy). Geprüft werden Invarianten – Dinge, die in KEINER
Kombination passieren dürfen – plus einige Zeilen mit fester Erwartung.
"""
import itertools

import pytest

from app.core.battery_policy import (
    BatteryInputs, BatteryIntent, heater_grid_intent, plan_battery, wallbox_grid_intent,
)
from app.drivers.base import WallboxState

CAR = ["off", "pv_only", "pv_price", "fast"]
WATER = ["off", "pv_only", "pv_price", "boost"]
BATTERY = [None, BatteryIntent.NO_DISCHARGE, BatteryIntent.CHARGE, BatteryIntent.HOLD]
PRICE = [True, False]
SOC = [15.0, 85.0]
FORECAST = [30.0, 0.5]          # kWh erwarteter Überschuss: gut / schlecht
PRESENCE = ["plugged", "away", "offline"]

RESERVE = 20.0
GRID_TARGET = 50.0


def site(car, water, battery, cheap, soc, forecast, presence, grid_charge_enabled=True):
    """Ein Takt, wie ihn der Loop zusammensetzt (ohne Geräte)."""
    manual = battery
    # Wie im Loop: Bei manuellem Entladen laden Preis-Lasten nur PV.
    load_cheap = cheap and manual != BatteryIntent.DISCHARGE
    car_mode = "pv_only" if car in ("off", "fast") else car
    override = {"off": "stop", "fast": "fast"}.get(car)
    if presence == "offline":
        car_intent = None                       # Gerät offline → keine Absicht
    else:
        car_intent = wallbox_grid_intent(
            mode=car_mode, override=override,
            state=WallboxState.CONNECTED if presence == "plugged" else WallboxState.IDLE,
            reachable=True, price_cheap=load_cheap,
        )
    water_intent = heater_grid_intent(
        mode="off" if water == "off" else ("pv_only" if water == "boost" else water),
        boost=water == "boost", temperature_c=45.0, target_c=58.0, price_cheap=load_cheap,
    )
    intents = (("Auto", car_intent), ("Warmwasser", water_intent))
    price_loads = tuple(n for n, why in intents if why == "günstiger Strom")
    other = tuple(n for n, why in intents if why and why != "günstiger Strom")
    plan = plan_battery(BatteryInputs(
        soc=soc, reserve_soc=RESERVE, inverter_holds_reserve=True,
        price_cheap=cheap, grid_charge_enabled=grid_charge_enabled, grid_charge_soc=GRID_TARGET,
        forecast_surplus_kwh=forecast, capacity_kwh=10.0,
        grid_loads=price_loads, other_loads=other, manual=manual, grid_budget_w=8000.0,
    ))
    return car_intent, water_intent, plan


ALL = list(itertools.product(CAR, WATER, BATTERY, PRICE, SOC, FORECAST, PRESENCE))


def test_matrix_is_complete():
    assert len(ALL) == 4 * 4 * 4 * 2 * 2 * 2 * 3


@pytest.mark.parametrize("car,water,battery,cheap,soc,forecast,presence", ALL)
def test_invariants(car, water, battery, cheap, soc, forecast, presence):
    car_intent, water_intent, plan = site(car, water, battery, cheap, soc, forecast, presence)

    # 1. Läuft eine Last, weil der Netzstrom günstig ist, entlädt der
    #    Speicher NIE in sie hinein (Abnahmekriterium).
    if "günstiger Strom" in (car_intent, water_intent):
        assert plan.intent in (BatteryIntent.NO_DISCHARGE, BatteryIntent.HOLD, BatteryIntent.CHARGE), \
            (car, water, battery, cheap, soc, presence, plan)

    # 2. Ein Preis-Fenster gibt es nur bei günstigem Preis.
    if car_intent == "günstiger Strom" or water_intent == "günstiger Strom":
        assert cheap

    # 3. 'Aus" und 'PV only" ziehen nie absichtlich Netzstrom.
    if car in ("off", "pv_only"):
        assert car_intent is None
    if water in ("off", "pv_only"):
        assert water_intent is None

    # 4. Ein Auto, das weg oder offline ist, öffnet nie ein Fenster – auch
    #    nicht 'Schnell" (Fall 08.10.: volles/schlafendes Auto öffnete ein
    #    Fenster, der Speicher wurde dafür aus dem Netz geladen).
    if presence != "plugged":
        assert car_intent is None

    # 5. Netzladen des Speichers nur, wenn bestellt: manuell oder per
    #    Schalter bei günstigem Preis, unter dem Ziel, Sonne reicht nicht.
    if plan.intent == BatteryIntent.CHARGE and battery != BatteryIntent.CHARGE:
        assert cheap and soc < GRID_TARGET and forecast < (GRID_TARGET - soc) / 100 * 10.0
        assert plan.source == "grid_charge"

    # 6. Manuell schlägt alles.
    if battery is not None:
        assert plan.intent == battery and plan.source == "manual"


@pytest.mark.parametrize("car,water,cheap,soc,forecast,presence",
                         list(itertools.product(CAR, WATER, PRICE, SOC, FORECAST, PRESENCE)))
def test_without_grid_charge_switch_battery_never_charges_from_grid(car, water, cheap, soc, forecast, presence):
    _c, _w, plan = site(car, water, None, cheap, soc, forecast, presence, grid_charge_enabled=False)
    assert plan.intent != BatteryIntent.CHARGE


# ------------------------------------------------------------ feste Zeilen

def test_row_cheap_both_price_modes_battery_protected():
    car, water, plan = site("pv_price", "pv_price", None, True, 85.0, 0.5, "plugged")
    assert car == water == "günstiger Strom"
    assert plan.intent == BatteryIntent.NO_DISCHARGE
    assert "Auto" in plan.reason


def test_row_battery_locked_water_off_car_fast():
    """Leitfrage: 'Batterie sperren" + 'Warmwasser gesperrt" + Auto volle Last."""
    car, water, plan = site("fast", "off", BatteryIntent.HOLD, False, 60.0, 0.5, "plugged")
    assert car == "Sofort laden"
    assert water is None
    assert plan.intent == BatteryIntent.HOLD


def test_row_grid_charge_only_when_sun_will_not_do_it():
    _c, _w, bad = site("pv_only", "pv_only", None, True, 15.0, 0.5, "away")
    _c, _w, good = site("pv_only", "pv_only", None, True, 15.0, 30.0, "away")
    assert bad.intent == BatteryIntent.CHARGE
    assert good.intent == BatteryIntent.AUTO and "Sonne" in good.reason


def test_row_expensive_nothing_special():
    car, water, plan = site("pv_price", "pv_price", None, False, 85.0, 0.5, "plugged")
    assert car is None and water is None
    assert plan.intent == BatteryIntent.AUTO


def test_row_manual_discharge_pauses_price_loads():
    """Manuelles Entladen + Auto auf 'PV + Preis" bei günstigem Preis hieße:
    Der Speicher füllt das Auto. Preis-Lasten laden dann nur Sonnenstrom."""
    car, water, plan = site("pv_price", "pv_price", BatteryIntent.DISCHARGE, True, 85.0, 0.5, "plugged")
    assert car is None and water is None
    assert plan.intent == BatteryIntent.DISCHARGE


def test_row_reserve_is_held_when_inverter_cannot():
    plan = plan_battery(BatteryInputs(soc=19.0, reserve_soc=20.0, inverter_holds_reserve=False))
    assert plan.intent == BatteryIntent.NO_DISCHARGE and plan.source == "reserve"
    plan = plan_battery(BatteryInputs(soc=19.0, reserve_soc=20.0, inverter_holds_reserve=True))
    assert plan.intent == BatteryIntent.AUTO


def test_row_own_program_uses_battery_unless_expert_option():
    """ELWA-Programm um 6 Uhr: Netzstrom ist dann nicht günstig. Der Speicher
    darf helfen (bis zur Reserve) – ihn zu sperren kostete in der Wiedergabe
    der Diagnose-Woche mehr, als es sparte. Experten können es abschalten."""
    plan = plan_battery(BatteryInputs(soc=80.0, foreign_loads=("ELWA (Geräteprogramm)",)))
    assert plan.intent == BatteryIntent.AUTO
    plan = plan_battery(BatteryInputs(soc=80.0, foreign_loads=("ELWA",), protect_other_loads=True))
    assert plan.intent == BatteryIntent.NO_DISCHARGE and plan.source == "foreign_loads"


def test_row_fast_charging_may_use_battery_unless_expert_option():
    plan = plan_battery(BatteryInputs(soc=80.0, other_loads=("Auto (Sofort laden)",)))
    assert plan.intent == BatteryIntent.AUTO
    plan = plan_battery(BatteryInputs(soc=80.0, other_loads=("Auto (Sofort laden)",), protect_other_loads=True))
    assert plan.intent == BatteryIntent.NO_DISCHARGE


def test_grid_charge_hysteresis_at_target():
    base = dict(soc=48.5, price_cheap=True, grid_charge_enabled=True, grid_charge_soc=50.0)
    assert plan_battery(BatteryInputs(**base)).intent == BatteryIntent.AUTO        # knapp unter Ziel: nicht neu starten
    assert plan_battery(BatteryInputs(**base, was_grid_charging=True)).intent == BatteryIntent.CHARGE
    assert plan_battery(BatteryInputs(**{**base, "soc": 50.0}, was_grid_charging=True)).intent == BatteryIntent.AUTO
