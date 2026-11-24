"""Temperaturmodell des simulierten Warmwasserspeichers (180 l)."""
from app.drivers import simulation


def _world_after(seconds: float, *, program: bool, set_power: float = 0.0):
    w = simulation.SimulationWorld()
    w.wh_temp = 44.0
    w.wh_device_program = program
    w.wh_set_power = set_power
    clock = [w._last_tick]
    orig = simulation.time.monotonic
    simulation.time.monotonic = lambda: clock[0]
    try:
        t = 0.0
        while t < seconds:
            clock[0] += 3.0
            t += 3.0
            w.tick()
    finally:
        simulation.time.monotonic = orig
    return w


def test_boost_heats_realistically_per_tick():
    w = _world_after(3.0, program=True)
    # 2,98 kW * 3 s = 2,48 Wh -> ~0,012 K (minus Standverlust)
    assert 43.9 < w.wh_temp < 44.1


def test_boost_one_hour_about_14_kelvin():
    w = _world_after(3600.0, program=True)
    assert 56.0 < w.wh_temp < 59.0


def test_never_far_above_cutoff():
    w = _world_after(6 * 3600.0, program=True)
    assert w.wh_temp <= 75.5
