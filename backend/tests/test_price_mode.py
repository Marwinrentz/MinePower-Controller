"""Tests für preisoptimiertes Netzladen (Modus 'price").

Der Kern ist nicht 'lädt bei billigem Strom" – das war schon vorher da –,
sondern **woher** der Strom dann kommt. Ohne gesperrten Hausspeicher deckt
ein Hybrid-Wechselrichter die neue Last aus der Batterie: Der Nutzer sieht
eine günstige Stunde, bezahlt aber einen Speicher-Rundlauf mit doppeltem
Wirkungsgradverlust und steht abends ohne Reserve da. Diese Tests halten
fest, dass genau das nicht passiert.

Zweiter Punkt: Das PV-Überschussladen darf davon unberührt bleiben. Sobald
der Preis wieder über der Grenze liegt, verhält sich 'price" exakt wie
'pv_only" – auch das ist hier festgenagelt.
"""
from app.core.regulation import (
    ChargeContext,
    WallboxController,
    WallboxSettings,
    WaterHeaterController,
    WaterHeaterSettings,
    price_window_open,
)
from app.drivers.base import WallboxState, WallboxData, WaterHeaterData


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def wallbox(clock, **overrides) -> WallboxController:
    return WallboxController(
        WallboxSettings(**{"mode": "price", "min_current": 6, "max_current": 16,
                           "phases_mode": "fixed3",
                           "start_delay_s": 60, "stop_delay_s": 180, **overrides}),
        clock=clock,
    )


def heater(**overrides) -> WaterHeaterController:
    return WaterHeaterController(
        WaterHeaterSettings(**{"mode": "price", "min_power_w": 100, "max_power_w": 3000,
                               "target_temp_c": 60, **overrides}),
        clock=FakeClock(),
    )


def plugged(power=0.0) -> WallboxData:
    return WallboxData(state=WallboxState.CONNECTED, power=power)


# ------------------------------------------------------------ price_window_open


def test_window_open_below_limit():
    assert price_window_open(ChargeContext(price_ct=9.0, cheap_hour=True), 15.0) is True


def test_window_closed_above_limit():
    assert price_window_open(ChargeContext(price_ct=22.0, cheap_hour=False), 15.0) is False


def test_global_limit_follows_tariff_decision():
    """Ohne eigene Grenze (None) gilt die Entscheidung des Tarif-Dienstes –
    dieselbe Grenze wie für die Batterie und alle anderen Geräte."""
    assert price_window_open(ChargeContext(price_ct=20.0, cheap_hour=True), None) is True
    assert price_window_open(ChargeContext(price_ct=20.0, cheap_hour=False), None) is False


def test_one_limit_for_everything():
    """Seit 2.16 gibt es nur die Grenze im Tarif. Geräte tragen keine eigene
    mehr – alte Felder werden beim Laden ignoriert."""
    assert WallboxSettings.from_dict({"price_limit_ct": 25, "price_limit_mode": "global"}).mode == "pv_only"
    assert not hasattr(WaterHeaterSettings(), "price_limit_ct")


def test_global_limit_reason_names_tariff_limit():
    wb = WallboxController(WallboxSettings(mode="pv_price", phases_mode="fixed3"), clock=FakeClock())
    d = wb.decide(0.0, plugged(), ChargeContext(price_ct=20.0, cheap_hour=True,
                                                price_limit_ct=24.0, house_limit_a=63))
    assert d.enable
    assert "20.0 ct" in d.reason and "24.0 ct" in d.reason


def test_window_closed_without_tariff():
    """Ohne konfigurierten Tarif gibt es keinen Preis und damit kein Fenster –
    nicht etwa ein dauerhaft offenes."""
    assert price_window_open(ChargeContext(price_ct=None), 15.0) is False


# ------------------------------------------------------------ Wallbox


def test_wallbox_charges_full_in_cheap_window():
    wb = wallbox(FakeClock())
    d = wb.decide(0.0, plugged(), ChargeContext(price_ct=8.0, cheap_hour=True, price_limit_ct=15.0, house_limit_a=63))
    assert d.enable
    assert d.current_a == 16
    # Die Begruendung nennt Preis und Grenze im Klartext. 'guenstiger Tarif"
    # allein liess den Nutzer raten, ob seine Schwelle ueberhaupt greift.
    assert "8.0 ct" in d.reason and "15.0 ct" in d.reason


def test_wallbox_refuses_grid_charge_when_battery_would_cover_it():
    """Der eigentliche Bugfix: Speicher entlädt, lässt sich nicht sperren →
    kein Netzladen. Sonst füllt die günstige Stunde das Auto aus dem
    Hausspeicher statt aus dem Netz."""
    wb = wallbox(FakeClock())
    d = wb.decide(0.0, plugged(), ChargeContext(
        price_ct=8.0, cheap_hour=True, house_limit_a=63, battery_drain_unblockable=True,
    ))
    assert not d.enable
    assert "lässt sich nicht sperren" in d.reason


def test_wallbox_charges_anyway_when_user_allows_battery_drain():
    wb = wallbox(FakeClock(), price_allow_battery_drain=True)
    d = wb.decide(0.0, plugged(), ChargeContext(
        price_ct=8.0, cheap_hour=True, house_limit_a=63, battery_drain_unblockable=True,
    ))
    assert d.enable
    assert d.current_a == 16


def test_wallbox_charges_when_battery_locked_successfully():
    """Speicher gesperrt → `battery_drain_unblockable` ist False, laden ist
    unbedenklich."""
    wb = wallbox(FakeClock())
    d = wb.decide(0.0, plugged(), ChargeContext(
        price_ct=8.0, cheap_hour=True, house_limit_a=63, battery_locked=True,
        battery_drain_unblockable=False,
    ))
    assert d.enable


def test_wallbox_falls_back_to_surplus_when_price_rises():
    """'dann stoppen" heißt: Netzladen endet – nicht, dass die Sonne verfällt.
    Über der Preisgrenze verhält sich der Regler wie im Modus pv_only."""
    clock = FakeClock()
    wb = wallbox(clock, start_threshold_w=1400)
    expensive = ChargeContext(price_ct=32.0, cheap_hour=False, house_limit_a=63)

    # Kein Überschuss → kein Laden
    d = wb.decide(0.0, plugged(), expensive)
    assert not d.enable
    assert "warte auf Überschuss" in d.reason

    # Mit Überschuss → normales Überschussladen, nach der Start-Verzögerung.
    # Zwei Aufrufe, weil der Timer die Bedingung erst anlaufen lässt und dann
    # prüft – dieselbe Hysterese wie im Modus pv_only.
    wb.decide(5000.0, plugged(), expensive)
    clock.advance(120)
    d = wb.decide(5000.0, plugged(), expensive)
    assert d.enable
    assert "PV-Überschuss" in d.reason


def test_wallbox_stops_grid_charging_when_window_closes():
    clock = FakeClock()
    wb = wallbox(clock)
    cheap = ChargeContext(price_ct=8.0, cheap_hour=True, house_limit_a=63)
    assert wb.decide(0.0, plugged(), cheap).enable

    # Preis steigt, kein Überschuss vorhanden. Die Stopp-Verzögerung greift
    # wie beim Überschussladen: erst anlaufen lassen, dann abwarten.
    expensive = ChargeContext(price_ct=30.0, cheap_hour=False, house_limit_a=63)
    wb.decide(0.0, plugged(200), expensive)
    clock.advance(600)
    d = wb.decide(0.0, plugged(200), expensive)
    assert not d.enable
    assert "Überschuss zu gering" in d.reason


# ------------------------------------------------------------ Warmwasser


def test_heater_runs_full_power_in_cheap_window():
    wh = heater()
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=40), ChargeContext(price_ct=8.0, cheap_hour=True, price_limit_ct=15.0))
    assert d.power_w == 3000
    # Dieselbe Formulierung wie bei der Wallbox (price_reason in
    # regulation.py) – nennt Preis und Grenze, nicht nur "günstig".
    assert "8.0 ct" in d.reason and "15.0 ct" in d.reason


def test_heater_refuses_grid_heat_when_battery_would_cover_it():
    wh = heater()
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=40),
                  ChargeContext(price_ct=8.0, cheap_hour=True, battery_drain_unblockable=True))
    assert d.power_w == 0
    assert "lässt sich nicht sperren" in d.reason


def test_heater_stops_at_target_temp_even_in_cheap_window():
    """Die Zieltemperatur schlägt den Preis: Ein voller Speicher wird nicht
    weiter geheizt, nur weil der Strom gerade billig ist."""
    wh = heater()
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=62), ChargeContext(price_ct=5.0, cheap_hour=True))
    assert d.power_w == 0
    assert "Zieltemperatur" in d.reason


def test_heater_falls_back_to_surplus_when_price_rises():
    wh = heater(start_threshold_w=200, start_delay_s=0)
    expensive = ChargeContext(price_ct=30.0, cheap_hour=False)
    d = wh.decide(2000.0, WaterHeaterData(power=0, temperature_c=40), expensive)
    assert d.power_w == 2000
    assert "PV-Überschuss" in d.reason


def test_heater_off_beats_cheap_price():
    wh = heater(mode="off")
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=40), ChargeContext(price_ct=5.0, cheap_hour=True))
    assert d.power_w == 0
    assert d.reason == "deaktiviert"


def test_heater_headroom_available_in_price_mode_outside_window():
    """Außerhalb des Fensters ist 'price" eine ganz normale Überschusslast und
    darf am Lückenfüller teilnehmen – sonst ginge der Rest ins Netz."""
    wh = heater(start_threshold_w=200, start_delay_s=0)
    data = WaterHeaterData(power=0, temperature_c=40)
    decision = wh.decide(1000.0, data, ChargeContext(price_ct=30.0, cheap_hour=False))
    assert wh.headroom(decision, data) == 2000
