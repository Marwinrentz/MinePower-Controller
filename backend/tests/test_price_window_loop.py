"""Tests der Speichersperre im Control-Loop (Netzladefenster).

Ergänzt tests/test_price_mode.py: Dort geht es um die Entscheidung eines
einzelnen Reglers, hier um das Zusammenspiel — wer erkennt das Fenster, was
wird an den Speicher geschickt, und was passiert mit Wechselrichtern, die
sich gar nicht steuern lassen.
"""
import pytest

from app.core.loop import ControlLoop
from app.drivers.base import BatteryData, BatteryDriver, BatteryMode

# Die Fälschungen sind echte BatteryDriver-Unterklassen, und „kann nicht"
# heißt hier „überschreibt die Methode nicht" – genau wie in der Wirklichkeit.
# Eine Fälschung, die stattdessen NotImplementedError *wirft*, würde am
# eigentlichen Mechanismus vorbeitesten: `_send` fängt jede Ausnahme selbst
# ab, ein geworfener Fehler käme nie bei der Fähigkeitsprüfung an.


class _RecordingBattery(BatteryDriver):
    meta = None  # wird im Test nie über die Registry erzeugt

    def __init__(self, *, controllable=True):
        super().__init__({"allow_active_control": controllable})
        self.mode_calls: list[BatteryMode] = []
        self.reserve_calls: list[int] = []

    async def read_data(self) -> BatteryData:
        return BatteryData(soc=50.0, power=-500.0)


class FullBattery(_RecordingBattery):
    """Kann Modus und Reserve (Sungrow SH & Co.)."""

    async def set_mode(self, mode: BatteryMode, power_w=None) -> None:
        self.mode_calls.append(mode)

    async def set_reserve_soc(self, soc: int) -> None:
        self.reserve_calls.append(soc)


class ReserveOnlyBattery(_RecordingBattery):
    """Kein Modus-Register, aber eine Entladegrenze."""

    async def set_reserve_soc(self, soc: int) -> None:
        self.reserve_calls.append(soc)


class MonitoringBattery(_RecordingBattery):
    """Reines Monitoring – nichts zu stellen."""


class FakeDevice:
    def __init__(self, name, driver, settings=None):
        self.id = id(self)
        self.name = name
        self.driver = driver
        self.settings = settings or {}
        self.online = True
        self.enabled = True
        self.sent = {}
        self.sent_at = {}
        self.send_fail_count = {}
        self.send_backoff = {}
        self.controller = None
        self.command_error = None


class FakeSettings:
    def __init__(self, mode, price_limit_ct=15.0):
        self.mode = mode
        self.price_limit_ct = price_limit_ct


class FakeController:
    def __init__(self, mode, price_limit_ct=15.0):
        self.s = FakeSettings(mode, price_limit_ct)


@pytest.fixture
def loop():
    return ControlLoop()


# ------------------------------------------------- Fenster-Erkennung


def test_no_window_without_price(loop):
    dev = FakeDevice("Wallbox", None)
    dev.controller = FakeController("price")
    loop.devices = {dev.id: dev}

    assert loop._price_window_devices(None, None) == []


def test_window_open_when_price_below_device_limit(loop):
    dev = FakeDevice("Wallbox", None)
    dev.controller = FakeController("price", price_limit_ct=15.0)
    loop.devices = {dev.id: dev}

    assert loop._price_window_devices(9.0, False) == ["Wallbox"]


def test_window_closed_above_device_limit(loop):
    dev = FakeDevice("Wallbox", None)
    dev.controller = FakeController("price", price_limit_ct=15.0)
    loop.devices = {dev.id: dev}

    assert loop._price_window_devices(25.0, False) == []


def test_pv_only_device_never_opens_window(loop):
    """Das Fenster darf nur aufgehen, wenn jemand es auch nutzt. Sonst würde
    ein billiger Nachttarif den Speicher sperren, ohne dass irgendein Gerät
    lädt – das Haus zöge die Nacht über alles aus dem Netz."""
    dev = FakeDevice("Wallbox", None)
    dev.controller = FakeController("pv_only")
    loop.devices = {dev.id: dev}

    assert loop._price_window_devices(2.0, True) == []


def test_disabled_device_never_opens_window(loop):
    dev = FakeDevice("Wallbox", None)
    dev.controller = FakeController("price")
    dev.enabled = False
    loop.devices = {dev.id: dev}

    assert loop._price_window_devices(5.0, False) == []


def test_window_lists_every_participating_device(loop):
    wb = FakeDevice("Wallbox", None)
    wb.controller = FakeController("price")
    wh = FakeDevice("Heizstab", None)
    wh.controller = FakeController("price")
    loop.devices = {wb.id: wb, wh.id: wh}

    assert sorted(loop._price_window_devices(5.0, False)) == ["Heizstab", "Wallbox"]


# ------------------------------------------------- Speicher sperren


async def test_lock_uses_hold_mode_by_default(loop):
    driver = FullBattery()
    dev = FakeDevice("Speicher", driver)

    locked = await loop._lock_battery(dev, window_open=True)

    assert locked is True
    assert driver.mode_calls == [BatteryMode.HOLD]


async def test_lock_uses_force_charge_when_configured(loop):
    """„nur volladen": Der Speicher darf den günstigen Netzstrom mitnehmen –
    entladen darf er trotzdem nicht."""
    driver = FullBattery()
    dev = FakeDevice("Speicher", driver, {"price_window_mode": "charge"})

    await loop._lock_battery(dev, window_open=True)

    assert driver.mode_calls == [BatteryMode.FORCE_CHARGE]


async def test_unlock_returns_to_auto(loop):
    driver = FullBattery()
    dev = FakeDevice("Speicher", driver)

    await loop._lock_battery(dev, window_open=True)
    await loop._lock_battery(dev, window_open=False)

    assert driver.mode_calls == [BatteryMode.HOLD, BatteryMode.AUTO]


async def test_lock_falls_back_to_reserve_soc(loop):
    """Wechselrichter ohne Modus-Befehl, aber mit Reserve-Register: Reserve
    auf 100 % heißt ebenfalls „entlädt nicht"."""
    driver = ReserveOnlyBattery()
    dev = FakeDevice("Speicher", driver)

    locked = await loop._lock_battery(dev, window_open=True)

    assert locked is True
    assert driver.reserve_calls == [100]


async def test_reserve_fallback_restores_configured_value(loop):
    driver = ReserveOnlyBattery()
    dev = FakeDevice("Speicher", driver, {"reserve_soc": 25})

    await loop._lock_battery(dev, window_open=True)
    await loop._lock_battery(dev, window_open=False)

    assert driver.reserve_calls == [100, 25]


async def test_monitoring_only_battery_cannot_be_locked(loop):
    """Der Fall, der den Regler zum Abbruch bringt: reines Monitoring."""
    driver = MonitoringBattery(controllable=False)
    dev = FakeDevice("Speicher", driver)

    assert await loop._lock_battery(dev, window_open=True) is False
    assert driver.mode_calls == []
    assert driver.reserve_calls == []


async def test_offline_battery_cannot_be_locked(loop):
    driver = FullBattery()
    dev = FakeDevice("Speicher", driver)
    dev.online = False

    assert await loop._lock_battery(dev, window_open=True) is False


async def test_driver_without_any_control_reports_unlocked(loop):
    driver = MonitoringBattery()
    dev = FakeDevice("Speicher", driver)

    assert await loop._lock_battery(dev, window_open=True) is False


async def test_apply_price_window_requires_every_battery_locked(loop):
    """Zwei Speicher, einer nicht sperrbar → das Fenster gilt als unsicher.
    Ein einziger entladender Speicher reicht, um die Ersparnis aufzufressen."""
    good = FakeDevice("Sungrow", FullBattery())
    bad = FakeDevice("Fremdspeicher", MonitoringBattery(controllable=False))

    assert await loop._apply_price_window([good, bad], window_open=True) is False


async def test_apply_price_window_all_locked(loop):
    a = FakeDevice("Speicher A", FullBattery())
    b = FakeDevice("Speicher B", FullBattery())

    assert await loop._apply_price_window([a, b], window_open=True) is True


async def test_apply_price_window_without_battery_is_locked(loop):
    """Keine Batterie = nichts, was dazwischenfunken kann. Netzladen ist dann
    unbedenklich und darf nicht an einer fehlenden Sperre scheitern."""
    assert await loop._apply_price_window([], window_open=True) is True
